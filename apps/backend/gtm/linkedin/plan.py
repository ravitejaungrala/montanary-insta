"""GTM LinkedIn Agent — the per-lead cadence, as data.

Replaces the module-level `_ACCEPTED_PIPELINE` / `_INMAIL_PIPELINE` constants in
`sequence_engine.py`. Those were shared by every lead in every workspace and
advanced by list position (`pipeline.index(current_step)`), which made three
things impossible:

  - inserting a touch that isn't already a literal in the list (a reply);
  - giving one lead a different cadence from another (a re-plan);
  - failing loudly on an unknown step — the positional lookup fell through to
    `_complete()`, silently ending the sequence.

Here the cadence is a JSONB list on `gtm_linkedin_lead_state.plan`: an ordered
set of remaining touches that the engine walks, and that `replan()` rewrites in
response to what the prospect said.

TOUCH SHAPE
-----------
    {
      "id":                "a2",          # stable within the plan
      "step":              "message_2",   # LEGACY name — see below
      "kind":              "message",     # → worker job type (KIND_JOB)
      "role":              "followup",    # opener|followup|close|answer|request
      "content_step":      2,             # index into nexus_linkedin_messages.step
      "delay_days":        2,             # gap BEFORE this touch fires
      "check_reply_first": false,         # read the thread in the same session
      "status":            "pending",     # pending|sent|skipped|cancelled
    }

`step` carries the legacy step name deliberately. `scheduler._STEP_JOB`, the
`/linkedin/lead/{id}/flow` endpoint and the Flow tab all key off
`linkedin_current_step`, so keeping the same names means the plan can take over
scheduling without changing a single downstream consumer. The plan is the source
of truth; `linkedin_current_step` becomes a projection of it.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("pipelyt.gtm_linkedin.plan")

Touch = Dict[str, Any]
Plan = List[Touch]

# Touch kind → worker job type. Four kinds replace 15 `_STEP_JOB` entries; the
# legacy table stays as a fallback for plan-less (pre-migration) leads.
KIND_JOB = {
    "connection": "send_connection_request",
    "connect_and_inmail": "send_connect_and_inmail",
    "message": "send_message",
    "inmail": "send_inmail",
    "check_acceptance": "check_connection_status",
    "check_reply": "check_reply_status",
}

_SEND_KINDS = {"connection", "connect_and_inmail", "message", "inmail"}

# ── Cadence constants ────────────────────────────────────────────────────────
# Phase 1 mirrors the previous hardcoded pipelines EXACTLY so the refactor is
# provably behaviour-neutral; Phase 2 retimes them to the target spec. Keeping
# them here means retiming is a constant change, not a code change.
ACCEPTANCE_CHECK_DAYS = (3, 2)   # first check at T+3, second 2 days later (T+5)
MESSAGE_GAP_DAYS = 4             # silence before a DM follow-up
INMAIL_GAP_DAYS = 4              # silence before an InMail follow-up


def _t(tid: str, step: str, kind: str, role: str, *, content_step: Optional[int] = None,
       delay_days: int = 0, check_reply_first: bool = False) -> Touch:
    return {
        "id": tid, "step": step, "kind": kind, "role": role,
        "content_step": content_step, "delay_days": delay_days,
        "check_reply_first": check_reply_first, "status": "pending",
    }


# ── Templates ────────────────────────────────────────────────────────────────
def _connection_phase(workflow_mode: str) -> Plan:
    """Connection request, then the acceptance checks that fork the sequence.

    `combined` sends the first InMail alongside the request, so the InMail branch
    must NOT re-send it (see `inmail_branch`). `hybrid` has no connection phase
    at all — it is InMail-only.
    """
    if workflow_mode == "hybrid":
        return []
    first = "connect_and_inmail" if workflow_mode == "combined" else "connection"
    step = "connect_and_inmail" if workflow_mode == "combined" else "connection_request"
    out: Plan = [_t("c1", step, first, "request", content_step=0, delay_days=0)]
    for i, gap in enumerate(ACCEPTANCE_CHECK_DAYS, start=1):
        out.append(_t(f"c{i + 1}", "check_acceptance", "check_acceptance", "check", delay_days=gap))
    return out


def accepted_branch() -> Plan:
    """Post-acceptance DMs. Content indices mirror the previous
    `_INMAIL_STEP_IDX` mapping (message_1→1, message_2→2, close→3): after
    acceptance we deliver the stored InMail bodies as free DMs."""
    return [
        _t("a1", "message_1", "message", "opener", content_step=1, delay_days=0),
        _t("a2", "message_2", "message", "followup", content_step=2,
           delay_days=MESSAGE_GAP_DAYS, check_reply_first=True),
        _t("a3", "close_message", "message", "close", content_step=3,
           delay_days=MESSAGE_GAP_DAYS, check_reply_first=True),
    ]


def inmail_branch(workflow_mode: str = "standard") -> Plan:
    """Not-accepted fallback: an initial InMail plus ONE follow-up.

    In `combined` mode the initial InMail already went out with the connection
    request, so the branch starts at the follow-up — never re-sending it.
    """
    opener = _t("i1", "inmail_1", "inmail", "opener", content_step=0, delay_days=0)
    followup = _t("i2", "inmail_followup", "inmail", "followup", content_step=1,
                  delay_days=INMAIL_GAP_DAYS, check_reply_first=True)
    return [followup] if workflow_mode == "combined" else [opener, followup]


def build_plan(workflow_mode: str = "standard") -> Plan:
    """The full enrollment plan. The acceptance result later swaps everything
    after the connection phase for one branch or the other (`switch_branch`)."""
    mode = (workflow_mode or "standard").lower()
    if mode == "hybrid":
        return inmail_branch("standard")
    return _connection_phase(mode)


# ── Reads ────────────────────────────────────────────────────────────────────
def next_pending(plan: Optional[Plan]) -> Tuple[Optional[int], Optional[Touch]]:
    """First touch still to run. `(None, None)` means the sequence is done —
    the ONLY completion condition, unlike the old positional fallthrough which
    also completed on any unrecognised step."""
    for i, t in enumerate(plan or []):
        if t.get("status") == "pending":
            return i, t
    return None, None


def find(plan: Optional[Plan], touch_id: str) -> Optional[Touch]:
    for t in plan or []:
        if t.get("id") == touch_id:
            return t
    return None


def find_by_step(plan: Optional[Plan], step: str) -> Optional[Touch]:
    """Resolve a legacy `linkedin_current_step` back to its touch. Prefers a
    pending match so a repeated step name (e.g. two acceptance checks) resolves
    to the one actually in flight."""
    if not plan or not step:
        return None
    for t in plan:
        if t.get("step") == step and t.get("status") == "pending":
            return t
    for t in plan:
        if t.get("step") == step:
            return t
    return None


def job_type_for(touch: Touch) -> Optional[str]:
    return KIND_JOB.get(touch.get("kind") or "")


def is_send(touch: Touch) -> bool:
    return (touch.get("kind") or "") in _SEND_KINDS


def remaining(plan: Optional[Plan]) -> Plan:
    return [t for t in (plan or []) if t.get("status") == "pending"]


def summary(plan: Optional[Plan]) -> str:
    """Compact one-line rendering for logs: `message_1:sent > message_2:pending`."""
    return " > ".join(f"{t.get('step')}:{t.get('status')}" for t in (plan or [])) or "(empty)"


# ── Mutations (the five re-plan primitives) ──────────────────────────────────
def mark(plan: Plan, touch_id: str, status: str) -> Plan:
    for t in plan or []:
        if t.get("id") == touch_id:
            t["status"] = status
    return plan


def truncate(plan: Optional[Plan]) -> Plan:
    """Cancel every remaining touch. The terminal op for any stop, and the
    landing point for every failure path in the engine."""
    for t in plan or []:
        if t.get("status") == "pending":
            t["status"] = "cancelled"
    return plan or []


def retime(plan: Optional[Plan], extra_days: int, *, only_next: bool = True) -> Plan:
    """Push pending touches out. `only_next` shifts just the next one (an OOO
    return date moves the next send, it doesn't ripple the whole cadence)."""
    for t in plan or []:
        if t.get("status") != "pending":
            continue
        t["delay_days"] = max(0, int(t.get("delay_days") or 0) + int(extra_days))
        if only_next:
            break
    return plan or []


def insert_after(plan: Plan, touch: Touch, *, after_id: Optional[str] = None) -> Plan:
    """Insert a touch — the operation the old list literally could not express.
    With `after_id=None` it goes before the next pending touch, i.e. next."""
    if after_id:
        for i, t in enumerate(plan):
            if t.get("id") == after_id:
                plan.insert(i + 1, touch)
                return plan
    idx, _ = next_pending(plan)
    plan.insert(len(plan) if idx is None else idx, touch)
    return plan


def switch_branch(plan: Optional[Plan], new_tail: Plan) -> Plan:
    """Replace everything still pending with a different branch. Used for the
    acceptance fork and for a late acceptance while on the InMail branch — which
    was previously bespoke `_cancel_pending_jobs` + `_inmail_branch_entry` logic."""
    kept = [t for t in (plan or []) if t.get("status") != "pending"]
    return kept + new_tail


def mark_stale(plan: Optional[Plan], *, from_message_id: Optional[int] = None) -> Plan:
    """Flag pending touches for content regeneration. The send path regenerates
    lazily, so a generation failure can never block or corrupt a send."""
    for t in plan or []:
        if t.get("status") == "pending":
            t["content_stale"] = True
            if from_message_id is not None:
                t["stale_from_message_id"] = from_message_id
    return plan or []


# ── Backfill for leads enrolled before plans existed ─────────────────────────
# Maps a legacy `linkedin_current_step` to the touch id that should fire NEXT.
# `linkedin_current_step` names the step ABOUT to run, so a lead sitting on a
# `check_reply_*` step resolves to the touch that check was gating — the check
# itself is now folded into that touch (`check_reply_first`).
#
# `None` means "past the end of the new cadence": the not-accepted branch drops
# from four InMail touches to two, so a lead already on follow-up 2 or the close
# has had MORE touches than the new plan allows. Completing them is correct — the
# alternative would be re-sending copy they have already received.
_LEGACY_NEXT = {
    "accepted": {
        "message_1": "a1", "check_reply_1": "a2", "message_2": "a2",
        "check_reply_2": "a3", "close_message": "a3",
    },
    "inmail": {
        "inmail_1": "i1", "check_reply_i1": "i2", "inmail_followup": "i2",
        "check_reply_i2": None, "inmail_followup_2": None,
        "check_reply_i3": None, "close_inmail": None,
    },
    "pending_acceptance": {
        "connection_request": "c1", "connect_and_inmail": "c1",
        "check_acceptance": "c2",
    },
}


def backfill_plan(workflow_mode: str, branch: Optional[str],
                  current_step: Optional[str]) -> Tuple[Plan, bool]:
    """Reconstruct a plan for a lead enrolled before `plan` existed.

    Returns `(plan, resolved)`. `resolved=False` means the legacy step could not
    be placed — the caller must NOT invent a position, because guessing forward
    re-sends and guessing backward skips. It stops the lead for review instead.

    Everything before the resolved touch is marked `sent`, so the lead resumes
    exactly where it was rather than restarting the branch.
    """
    mode = (workflow_mode or "standard").lower()
    br = (branch or "pending_acceptance").lower()
    if br == "accepted":
        p = accepted_branch()
    elif br == "inmail":
        p = inmail_branch(mode)
    elif mode == "hybrid":
        p, br = inmail_branch("standard"), "inmail"
    else:
        p = _connection_phase(mode)

    table = _LEGACY_NEXT.get(br) or {}
    if not current_step:
        return p, True                      # never dispatched — the fresh plan is right
    if current_step not in table:
        return p, False                     # unknown → caller decides, never guess
    target = table[current_step]
    if target is None:
        return truncate(p), True            # past the end of the new cadence
    if not find(p, target):
        # `combined` mode's InMail branch has no i1 (it went out with the
        # connection request), so a lead resolving to i1 is already past it.
        if target == "i1" and mode == "combined":
            return p, True
        return p, False
    for t in p:
        if t.get("id") == target:
            break
        t["status"] = "sent"
    return p, True


def next_touch_id(plan: Optional[Plan], prefix: str = "x") -> str:
    """Collision-free id for an inserted touch."""
    n = 1
    existing = {t.get("id") for t in (plan or [])}
    while f"{prefix}{n}" in existing:
        n += 1
    return f"{prefix}{n}"
