"""GTM LinkedIn Agent — Phase 3 sequence engine: state machine + branching.

Owns every transition AFTER a job's browser action completes:
- after a SEND (connection / message / inmail) → schedule the next step,
- after a CHECK (acceptance / reply) → branch / advance / stop,
- global stop signals (email reply, demo booked, unsubscribe) → stop the lead.

DOM detection of acceptance/reply lives in `browser.py` (`detect_acceptance`,
`detect_reply`); this module only INTERPRETS those results — emitting immutable
`gtm_linkedin_events`, updating `gtm_linkedin_lead_state`, and rescheduling the
next step via `scheduler.schedule_step`. The worker calls into here.

The cadence itself lives in `plan.py` as per-lead DATA
(`gtm_linkedin_lead_state.plan`), not as module constants here. This module
walks and rewrites that plan; it no longer knows what the steps are.

    connection request ──check_acceptance(T+3, T+5)──┬─accepted────► DM opener → follow-up → close
                                                     └─not accepted► InMail opener → follow-up
    Any reply           → replan (continue / re-time / stop / converse)
    Late acceptance     → switch_branch off the InMail tail
    Any failure         → defer with backoff, or stop once the budget is spent

Every outcome ends in exactly one of: advance, defer, or stop. A lead may never
be left `active` with `next_action_at IS NULL` — nothing would ever dispatch it
again (see `defer_lead`).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import text as _sql_text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from gtm.linkedin import plan, scheduler
from gtm.linkedin.events import record_event
from gtm.linkedin.models import GtmLinkedInJob, GtmLinkedInLeadState

logger = logging.getLogger("pipelyt.gtm_linkedin.sequence")

# Cadence timing now lives in plan.py (plan.ACCEPTANCE_CHECK_DAYS,
# plan.MESSAGE_GAP_DAYS, plan.INMAIL_GAP_DAYS) so that retiming the sequence is a
# data change. These aliases are kept because other modules and tests import
# them; they are read-only views onto the plan module's values.
ACCEPTANCE_WAIT_DAYS = plan.ACCEPTANCE_CHECK_DAYS[0]
MESSAGE_GAP_DAYS = plan.MESSAGE_GAP_DAYS
INMAIL_GAP_DAYS = plan.INMAIL_GAP_DAYS
MAX_ACCEPTANCE_CHECKS = len(plan.ACCEPTANCE_CHECK_DAYS)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _save_plan(db: Session, state: GtmLinkedInLeadState, p: plan.Plan) -> None:
    """Persist a plan. JSONB columns are only re-serialized when the attribute is
    REASSIGNED — mutating the list in place leaves SQLAlchemy unaware of the
    change and the write is silently lost."""
    state.plan = list(p)
    try:
        flag_modified(state, "plan")
    except Exception:  # noqa: BLE001 — not an instrumented instance (tests)
        pass
    state.updated_at = _utcnow()


def _ensure_plan(db: Session, state: GtmLinkedInLeadState) -> plan.Plan:
    """The lead's plan, backfilling one for leads enrolled before plans existed.

    A lead whose legacy step can't be placed is STOPPED for review rather than
    guessed at: guessing forward re-sends copy the prospect already got, and
    guessing backward skips a touch. This is the one place the old engine failed
    open (`_advance_pipeline` silently `_complete()`d on an unknown step) and it
    is deliberately the opposite here.
    """
    if state.plan:
        return list(state.plan)
    p, resolved = plan.backfill_plan(
        state.workflow_mode or "standard",
        state.linkedin_current_branch,
        state.linkedin_current_step,
    )
    if not resolved:
        logger.error(
            "gtm_linkedin: lead %s has an unplaceable legacy step %r on branch %r — "
            "stopping for review rather than guessing its position",
            state.lead_id, state.linkedin_current_step, state.linkedin_current_branch,
        )
        _save_plan(db, state, plan.truncate(p))
        stop_lead(db, state, reason="backfill_unresolved")
        return list(state.plan or [])
    _save_plan(db, state, p)
    db.commit()
    logger.info("gtm_linkedin: backfilled plan for lead %s — %s",
                state.lead_id, plan.summary(p))
    return p


def _cancel_pending_jobs(db: Session, lead_id: int, campaign_id: int) -> int:
    """Cancel not-yet-executed queued jobs for a lead (branch-switch / stop)."""
    n = (
        db.query(GtmLinkedInJob)
        .filter(
            GtmLinkedInJob.lead_id == lead_id,
            GtmLinkedInJob.campaign_id == campaign_id,
            GtmLinkedInJob.status.in_(["pending", "running"]),
            GtmLinkedInJob.is_executed.is_(False),
        )
        .update({"status": "cancelled"}, synchronize_session=False)
    )
    db.commit()
    return int(n or 0)


def _advance(db: Session, state: GtmLinkedInLeadState, *,
             completed_step: Optional[str] = None) -> Dict[str, Any]:
    """Mark the touch that just ran as sent, then schedule the next pending one.

    Replaces the positional `_advance_pipeline`. An empty remainder is now the
    ONLY way to complete — the old version also completed whenever the current
    step wasn't a member of the hardcoded list, which silently ended sequences.
    """
    p = _ensure_plan(db, state)
    if state.linkedin_sequence_status != "active":
        return {"sequence": state.linkedin_sequence_status}

    if completed_step:
        done = plan.find_by_step(p, completed_step)
        if done is not None:
            plan.mark(p, done["id"], "sent")
        else:
            logger.warning(
                "gtm_linkedin: lead %s completed step %r not found in its plan (%s) — "
                "advancing without marking",
                state.lead_id, completed_step, plan.summary(p),
            )

    _idx, nxt = plan.next_pending(p)
    if nxt is None:
        _save_plan(db, state, p)
        return _complete(db, state)

    _save_plan(db, state, p)
    # A reply is scheduled at MINUTE scale, so the default 2-20 min slot jitter
    # would swamp it (a "5-10 min" reply landing 7-30 min out). Keep the spread
    # proportional to the delay it's perturbing.
    _jitter = (0.0, 1.0) if nxt.get("is_reply") else None
    scheduler.schedule_step(
        db, state, current_step=nxt["step"],
        delay_days=int(nxt.get("delay_days") or 0),
        delay_minutes=int(nxt.get("delay_minutes") or 0),
        jitter_minutes=_jitter,
    )
    logger.info("gtm_linkedin: lead %s -> %s (in %sd %smin) | %s",
                state.lead_id, nxt["step"], nxt.get("delay_days") or 0,
                nxt.get("delay_minutes") or 0, plan.summary(p))
    return {"next": nxt["step"], "touch": nxt["id"]}


def _complete(db: Session, state: GtmLinkedInLeadState) -> Dict[str, Any]:
    state.linkedin_sequence_status = "completed"
    state.next_action_at = None
    state.updated_at = _utcnow()
    db.commit()
    record_event(
        db, workspace_id=state.workspace_id, lead_id=state.lead_id,
        campaign_id=state.campaign_id, linkedin_account_id=state.linkedin_account_id,
        event_type="sequence_completed",
    )
    return {"sequence": "completed"}


# ── Deferral (retry) ──────────────────────────────────────────────────────────
# Since b9c5cbb the Fargate worker DELETES the SQS message on failure (a poisoned
# job was blocking the whole per-account FIFO group). That removed the only retry
# mechanism: `dispatch_due` nulls next_action_at when it enqueues, so a job that
# fails or skips without rescheduling leaves the lead active + next_action_at
# NULL — unreachable by the due query, and invisible because it still reads as
# 'active'. Deferral replaces SQS redelivery with a scheduler-owned retry: it is
# durable, respects working hours and jitter, and never blocks the FIFO group.
MAX_DEFERRALS = 5
_BACKOFF_HOURS = (1, 4, 12, 24, 48)


def defer_lead(
    db: Session, state: GtmLinkedInLeadState, *, reason: str,
    delay_hours: Optional[float] = None, count_attempt: bool = True,
) -> Dict[str, Any]:
    """Reschedule the CURRENT step instead of leaving the lead stranded.

    `count_attempt=False` for account-level conditions (daily cap reached,
    cooldown, session expired, another worker holds the lock) — those say nothing
    about this lead, so they must not burn its retry budget or it would be
    stopped for someone else's rate limit.

    Lead-level failures DO count, and once MAX_DEFERRALS is exhausted the lead is
    stopped with a reason rather than retried forever. Never raises: this runs on
    the failure path, where a second exception would re-strand the lead.
    """
    try:
        # A dead lead must never be re-armed. Deferral's whole job is to set a
        # due-key, so calling it on a stopped/completed lead would resurrect it
        # into the dispatch queue — resuming outreach to someone we explicitly
        # stopped messaging (unsubscribed, handed to a human, out of credit).
        # Guarded HERE rather than only in the caller: this function owns
        # "re-arm a lead", so it owns "don't re-arm a dead one".
        if state.linkedin_sequence_status != "active":
            logger.info("gtm_linkedin: skipping defer for lead %s — sequence is %s, not active",
                        state.lead_id, state.linkedin_sequence_status)
            return {"deferred": None, "skipped": state.linkedin_sequence_status}

        attempts = int(state.deferral_count or 0)
        if count_attempt:
            attempts += 1
            state.deferral_count = attempts
            if attempts > MAX_DEFERRALS:
                logger.warning(
                    "gtm_linkedin: lead %s exhausted %d deferrals on step %s (%s) — stopping",
                    state.lead_id, MAX_DEFERRALS, state.linkedin_current_step, reason,
                )
                return stop_lead(db, state, reason=f"max_deferrals:{reason}"[:64])
            hours = _BACKOFF_HOURS[min(attempts - 1, len(_BACKOFF_HOURS) - 1)]
        else:
            hours = 1.0
        if delay_hours is not None:
            hours = delay_hours

        s = scheduler._settings(db, state.linkedin_account_id) if state.linkedin_account_id else None
        state.next_action_at = scheduler.random_slot(
            s, base=_utcnow() + timedelta(hours=hours), min_minutes=1.0, max_minutes=15.0,
        )
        state.last_deferred_at = _utcnow()
        state.defer_reason = (reason or "")[:64]
        state.updated_at = _utcnow()
        db.commit()
        record_event(
            db, workspace_id=state.workspace_id, lead_id=state.lead_id,
            campaign_id=state.campaign_id, linkedin_account_id=state.linkedin_account_id,
            event_type="step_deferred",
            metadata={"reason": reason, "attempt": attempts if count_attempt else None,
                      "step": state.linkedin_current_step, "retry_at": state.next_action_at.isoformat()},
        )
        logger.info("gtm_linkedin: deferred lead %s step %s for %sh (%s, attempt %s)",
                    state.lead_id, state.linkedin_current_step, hours, reason, attempts)
        return {"deferred": reason, "retry_at": state.next_action_at, "attempt": attempts}
    except Exception:  # noqa: BLE001 — must never re-strand the lead
        logger.warning("gtm_linkedin: defer_lead failed for lead %s", state.lead_id, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return {"deferred": reason, "error": True}


def clear_deferrals(state: GtmLinkedInLeadState) -> None:
    """Reset the retry budget after a step genuinely succeeds — otherwise a lead
    that hit 4 transient failures early would be stopped by one failure much
    later in an otherwise healthy sequence."""
    state.deferral_count = 0
    state.defer_reason = None


def stop_lead(db: Session, state: GtmLinkedInLeadState, *, reason: str) -> Dict[str, Any]:
    """Stop an active LinkedIn sequence, cancel its pending jobs, and empty its
    plan so nothing can resume it.

    Truncates the plan IN PLACE rather than via `_ensure_plan` — `_ensure_plan`
    calls back into `stop_lead` when a legacy step can't be placed, and routing
    through it here would recurse. A lead with no plan simply has nothing to
    truncate.
    """
    existing = getattr(state, "plan", None)
    if existing:
        state.plan = plan.truncate(list(existing))
        try:
            flag_modified(state, "plan")
        except Exception:  # noqa: BLE001 — not an instrumented instance
            pass
    state.linkedin_sequence_status = "stopped"
    state.next_action_at = None
    state.updated_at = _utcnow()
    db.commit()
    _cancel_pending_jobs(db, state.lead_id, state.campaign_id)
    record_event(
        db, workspace_id=state.workspace_id, lead_id=state.lead_id,
        campaign_id=state.campaign_id, linkedin_account_id=state.linkedin_account_id,
        event_type="sequence_stopped", metadata={"reason": reason},
    )
    return {"sequence": "stopped", "reason": reason}


# ── After a SEND completes ────────────────────────────────────────────────────
def after_send(db: Session, state: GtmLinkedInLeadState, completed_step: str) -> Dict[str, Any]:
    """Advance the lead after a successful send action (called by the worker).

    The connection request is no longer special-cased: the acceptance checks are
    ordinary touches in the plan, so this is a plain advance for every send.
    """
    return _advance(db, state, completed_step=completed_step)


# ── After CHECK ACCEPTANCE ────────────────────────────────────────────────────
def _enter_branch(db: Session, state: GtmLinkedInLeadState, branch: str) -> Dict[str, Any]:
    """Swap the pending tail for a branch template, cancel jobs queued for the
    tail we're abandoning, and schedule the branch's first touch.

    This replaces three pieces of bespoke logic: `_inmail_branch_entry` (with its
    `combined`-mode carve-out to avoid re-sending the first InMail — now handled
    by `plan.inmail_branch`), the manual `_cancel_pending_jobs` branch-cancel,
    and the positional entry-step lookup.
    """
    mode = state.workflow_mode or "standard"
    tail = plan.accepted_branch() if branch == "accepted" else plan.inmail_branch(mode)
    p = plan.switch_branch(_ensure_plan(db, state), tail)
    _save_plan(db, state, p)
    db.commit()
    # Any job already queued for the abandoned tail must not fire.
    _cancel_pending_jobs(db, state.lead_id, state.campaign_id)

    _idx, nxt = plan.next_pending(p)
    if nxt is None:
        return _complete(db, state)
    scheduler.schedule_step(
        db, state, current_step=nxt["step"], branch=branch,
        delay_days=int(nxt.get("delay_days") or 0),
    )
    logger.info("gtm_linkedin: lead %s entered %s branch -> %s | %s",
                state.lead_id, branch, nxt["step"], plan.summary(p))
    return {"next": nxt["step"], "touch": nxt["id"], "branch": branch}


def on_acceptance_result(db: Session, state: GtmLinkedInLeadState, result: str) -> Dict[str, Any]:
    """Interpret a profile acceptance read. `result` ∈ {'accepted','pending',
    'declined'} — 'unknown' is raised by the worker (and now deferred) so we
    never branch on a bad read.

    accepted ⇒ DM branch, dropping any InMail tail (a late acceptance while on
    the InMail branch is an expected path, not an edge case: the InMail goes out
    at T+5 and its follow-up at T+9, so acceptances land in between).
    pending  ⇒ re-check while checks remain, then fall through to InMail.
    declined ⇒ the invite is gone (declined/withdrawn/expired); it is NOT still
    pending, so go to InMail immediately rather than burning the remaining checks.
    """
    if result == "declined":
        state.linkedin_connection_status = "declined"
        state.updated_at = _utcnow()
        db.commit()
        record_event(
            db, workspace_id=state.workspace_id, lead_id=state.lead_id,
            campaign_id=state.campaign_id, linkedin_account_id=state.linkedin_account_id,
            event_type="connection_declined", metadata={"source": "profile_check"},
        )
        return {"acceptance": "declined", **_enter_branch(db, state, "inmail")}

    if result == "accepted":
        state.linkedin_connection_status = "accepted"
        state.linkedin_connection_accepted_at = _utcnow()
        state.updated_at = _utcnow()
        db.commit()
        record_event(
            db, workspace_id=state.workspace_id, lead_id=state.lead_id,
            campaign_id=state.campaign_id, linkedin_account_id=state.linkedin_account_id,
            event_type="connection_accepted", metadata={"source": "profile_check"},
        )
        return {"acceptance": "accepted", **_enter_branch(db, state, "accepted")}

    # Still pending. Mark this check done; if the plan holds another one, it
    # becomes the next touch. Counting is now positional in the plan rather than
    # a COUNT over completed jobs — which double-counted re-run checks.
    p = _ensure_plan(db, state)
    done = plan.find_by_step(p, "check_acceptance")
    if done is not None:
        plan.mark(p, done["id"], "sent")
    _save_plan(db, state, p)
    db.commit()
    remaining_checks = sum(
        1 for t in plan.remaining(p) if t.get("kind") == "check_acceptance"
    )
    record_event(
        db, workspace_id=state.workspace_id, lead_id=state.lead_id,
        campaign_id=state.campaign_id, linkedin_account_id=state.linkedin_account_id,
        event_type="acceptance_checked",
        metadata={"result": "pending", "checks_left": remaining_checks},
    )
    if remaining_checks > 0:
        return {"acceptance": "pending", **_advance(db, state)}
    return {"acceptance": "pending_timeout", **_enter_branch(db, state, "inmail")}


# ── After CHECK REPLY ─────────────────────────────────────────────────────────
def on_reply_result(db: Session, state: GtmLinkedInLeadState, replied: bool) -> Dict[str, Any]:
    """Boolean reply outcome — no reply text available.

    Kept for the legacy `check_reply_status` job and as the degradation path when
    a thread is readable but its TEXT is not: without knowing what they said, the
    only safe response is the old one — stop. `on_reply_classified` (Phase 6) is
    the reply-aware entry point.
    """
    if replied:
        return record_reply(db, state, body=None, source="thread_check")
    return _advance(db, state, completed_step=state.linkedin_current_step or "")


MAX_CONVERSATION_TURNS = 2


def _flag(db: Session, state: GtmLinkedInLeadState, reason: str) -> None:
    """Mark the lead for a human. Not a stop on its own — the caller decides."""
    state.flagged_reason = (reason or "")[:64]
    state.flagged_at = _utcnow()
    state.updated_at = _utcnow()
    db.commit()
    record_event(
        db, workspace_id=state.workspace_id, lead_id=state.lead_id,
        campaign_id=state.campaign_id, linkedin_account_id=state.linkedin_account_id,
        event_type="handoff_flagged", metadata={"reason": reason},
    )


def is_human_takeover(db: Session, state: GtmLinkedInLeadState, read: Dict[str, Any]) -> bool:
    """True if the newest OUTBOUND message in the thread isn't one we sent.

    Scenario 6 from the plan: a rep answers the prospect manually. The reply gate
    sees a `self` bubble, reads it as OUR message, concludes "no reply yet", and
    fires the scheduled follow-up on top of a live human conversation. That is
    the most visibly broken thing this engine can do, and it happens the first
    time anyone touches the inbox.

    LinkedIn won't tell us who typed it, so we compare against the bodies we
    recorded sending. An outbound message we have no record of means a human is
    driving.

    Conservative by construction: any doubt (no text read, nothing on record,
    a query failure) returns False and the sequence proceeds as before. A false
    positive would silently halt healthy automation, so we only claim takeover
    when we can positively show the message isn't ours.
    """
    ours = (read or {}).get("last_self_body")
    if not ours or not ours.strip():
        return False
    try:
        from gtm.linkedin.replies import _body_hash
        rows = db.execute(
            _sql_text(
                "SELECT body FROM nexus_linkedin_messages "
                " WHERE workspace_id = :w AND lead_id = :l AND direction = 'outbound' "
                "   AND COALESCE(body, '') <> '' "
                " ORDER BY id DESC LIMIT 25"
            ),
            {"w": state.workspace_id, "l": state.lead_id},
        ).fetchall()
        known = {_body_hash(r[0]) for r in rows if r and r[0]}
        if not known:
            return False  # nothing on record to compare against — don't guess
        if _body_hash(ours) in known:
            return False
        # Bodies get truncated/reflowed in the DOM, so also accept a solid
        # prefix match before declaring a stranger wrote it.
        head = " ".join(ours.split())[:60].lower()
        for r in rows:
            if head and head in " ".join((r[0] or "").split()).lower():
                return False
        return True
    except Exception:  # noqa: BLE001
        logger.debug("gtm_linkedin: human-takeover check failed for lead %s", state.lead_id,
                     exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return False


def on_human_takeover(db: Session, state: GtmLinkedInLeadState) -> Dict[str, Any]:
    """A human is handling this thread — stand down."""
    logger.info("gtm_linkedin: lead %s has an outbound message we didn't send — "
                "a human is driving this thread; stopping automation", state.lead_id)
    _flag(db, state, "human_takeover")
    p = plan.truncate(_ensure_plan(db, state))
    _save_plan(db, state, p)
    db.commit()
    record_event(
        db, workspace_id=state.workspace_id, lead_id=state.lead_id,
        campaign_id=state.campaign_id, linkedin_account_id=state.linkedin_account_id,
        event_type="human_takeover_detected",
    )
    return stop_lead(db, state, reason="human_takeover")


def on_reply_read(db: Session, state: GtmLinkedInLeadState, read: Dict[str, Any],
                  *, source: str) -> Dict[str, Any]:
    """Act on a structured thread read: persist, classify, re-plan.

    The single funnel for every reply, whatever found it (the send-time gate or
    the inbox sweep), so all three of persistence, classification and branching
    happen identically.
    """
    from gtm.linkedin import replies

    body = (read or {}).get("last_body")
    read_ok = bool((read or {}).get("read_ok"))

    state.linkedin_message_reply_status = "replied"
    state.last_reply_at = _utcnow()
    state.updated_at = _utcnow()
    db.commit()

    # Text unreadable ⇒ we know they replied but not what they said. Degrade to
    # the pre-existing behaviour (stop) rather than guessing an intent.
    if not read_ok or not body:
        record_event(
            db, workspace_id=state.workspace_id, lead_id=state.lead_id,
            campaign_id=state.campaign_id, linkedin_account_id=state.linkedin_account_id,
            event_type="reply_received", metadata={"source": source, "read_ok": read_ok, "chars": 0},
        )
        _flag(db, state, "reply_unreadable")
        p = plan.truncate(_ensure_plan(db, state))
        _save_plan(db, state, p)
        db.commit()
        return {"reply": "unreadable", **stop_lead(db, state, reason="replied")}

    cls = replies.classify(body)
    variant = "inmail" if (state.linkedin_current_branch == "inmail") else "dm"
    msg_id = replies.persist_inbound(
        db, workspace_id=state.workspace_id, lead_id=state.lead_id, body=body,
        variant=variant, intent=cls.get("intent"), confidence=cls.get("confidence"),
    )
    record_event(
        db, workspace_id=state.workspace_id, lead_id=state.lead_id,
        campaign_id=state.campaign_id, linkedin_account_id=state.linkedin_account_id,
        event_type="reply_received",
        metadata={"source": source, "chars": len(body), "read_ok": True,
                  "intent": cls.get("intent"), "confidence": cls.get("confidence")},
    )

    decision = replies.decide(cls, body=body, received_at=_utcnow())
    logger.info("gtm_linkedin: lead %s replied (%s, conf=%.2f) -> %s [%s]",
                state.lead_id, cls.get("intent"), cls.get("confidence") or 0.0,
                decision["action"], decision["reason"])
    return _apply_decision(db, state, decision, message_id=msg_id, intent=cls.get("intent"))


def _apply_decision(db: Session, state: GtmLinkedInLeadState, decision: Dict[str, Any],
                    *, message_id: Optional[int], intent: Optional[str]) -> Dict[str, Any]:
    """Execute a `replies.decide()` result against the plan."""
    p = _ensure_plan(db, state)
    action = decision.get("action")

    if decision.get("suppress"):
        _suppress(db, state)
    if decision.get("handoff"):
        _flag(db, state, decision.get("reason") or "handoff")

    for op in decision.get("ops") or []:
        name, arg = (op if isinstance(op, tuple) else (op, None))
        if name == "truncate":
            p = plan.truncate(p)
        elif name == "retime":
            p = plan.retime(p, int(arg or 0), only_next=True)
        elif name == "mark_stale":
            p = plan.mark_stale(p, from_message_id=message_id)
        elif name == "insert_reply":
            p = _insert_reply_touch(p)
        else:
            logger.warning("gtm_linkedin: unknown plan op %r for lead %s", name, state.lead_id)

    _save_plan(db, state, p)
    db.commit()

    if action == "stop":
        return {"reply": decision["reason"], "intent": intent,
                **stop_lead(db, state, reason=decision["reason"])}

    if action == "converse":
        turns = int(state.conversation_turns or 0)
        if turns >= MAX_CONVERSATION_TURNS:
            # An unbounded automated conversation is the most dangerous thing
            # this engine can do, so the cap stays. At the cap we simply stop —
            # no human flag: the thread is visible in the LinkedIn inbox like any
            # other, and a rep picks it up from there.
            logger.info("gtm_linkedin: lead %s hit the conversation cap (%d turns) — stopping",
                        state.lead_id, turns)
            p = plan.truncate(p)
            _save_plan(db, state, p)
            db.commit()
            return {"reply": decision["reason"], "intent": intent,
                    **stop_lead(db, state, reason="conversation_cap")}
        return {"reply": decision["reason"], "intent": intent, **_advance(db, state)}

    # continue — the cadence lives on with re-timed / regrounded touches.
    return {"reply": decision["reason"], "intent": intent, **_advance(db, state)}


# A reply lands in this window after we notice it — the TOTAL wall-clock delay,
# not a base the slot jitter then adds to (see the tightened jitter in _advance).
# Tight on purpose: a prompt answer is the whole point. Still randomized rather
# than fixed, so replies don't land on a machine-looking interval.
REPLY_DELAY_MINUTES = (5, 9)
# If we reply and they go quiet, one nudge, then done. NOT the full original
# cadence: they already engaged, so re-running a cold sequence at them reads
# worse than silence.
POST_CONVERSATION_NUDGE_DAYS = 4


def _insert_reply_touch(p: plan.Plan) -> plan.Plan:
    """Queue an in-thread reply, plus a single nudge if they then go quiet.

    Scheduled rather than sent inline: the delay keeps the response
    human-plausible, and generating on the send path means a generation failure
    can't block the thread read that found the reply in the first place.

    The nudge is reply-gated, so if they answer our answer, the gate catches it
    and we converse again (up to MAX_CONVERSATION_TURNS) instead of nudging
    someone mid-conversation.
    """
    import random
    tid = plan.next_touch_id(p, "r")
    reply = {
        "id": tid, "step": "reply", "kind": "message", "role": "answer",
        "content_step": None, "delay_days": 0,
        "delay_minutes": random.randint(*REPLY_DELAY_MINUTES),
        "check_reply_first": False, "status": "pending",
        "content_stale": True, "is_reply": True,
    }
    p = plan.insert_after(p, reply)
    nudge = {
        "id": plan.next_touch_id(p, "n"), "step": "close_message", "kind": "message",
        "role": "close", "content_step": 3, "delay_days": POST_CONVERSATION_NUDGE_DAYS,
        "check_reply_first": True, "status": "pending", "content_stale": True,
    }
    return plan.insert_after(p, nudge, after_id=tid)


def _suppress(db: Session, state: GtmLinkedInLeadState) -> None:
    """Add the lead to this workspace's suppression list. Best-effort."""
    try:
        from sqlalchemy import text as _t
        db.execute(
            _t(
                "INSERT INTO nexus_suppression_list (workspace_id, email_lower, reason, added_at) "
                "SELECT :ws, lower(gl.email), 'linkedin_stop_request', NOW() "
                "  FROM nexus_global_leads gl "
                " WHERE gl.id = :lid AND COALESCE(gl.email, '') <> '' "
                "ON CONFLICT DO NOTHING"
            ),
            {"ws": state.workspace_id, "lid": state.lead_id},
        )
        db.commit()
        logger.info("gtm_linkedin: lead %s suppressed on an explicit LinkedIn stop request",
                    state.lead_id)
    except Exception:  # noqa: BLE001
        logger.warning("gtm_linkedin: suppression insert failed for lead %s", state.lead_id,
                       exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass


def on_reply_undeliverable(db: Session, state: GtmLinkedInLeadState, *,
                           reason: str) -> Dict[str, Any]:
    """We chose not to send a generated reply. Hand the thread to a human.

    Reached when the generator declines an ungroundable question (§5.2 of the
    plan). NOT an error: a prospect asked something real and the honest answer is
    "a person should take this", not a fabricated one. The sequence stops so no
    scheduled follow-up lands on top of an unanswered question.
    """
    logger.info("gtm_linkedin: lead %s reply undeliverable (%s) — handing to a human",
                state.lead_id, reason)
    _flag(db, state, f"reply_{reason}"[:64])
    record_event(
        db, workspace_id=state.workspace_id, lead_id=state.lead_id,
        campaign_id=state.campaign_id, linkedin_account_id=state.linkedin_account_id,
        event_type="reply_declined", metadata={"reason": reason},
    )
    p = plan.truncate(_ensure_plan(db, state))
    _save_plan(db, state, p)
    db.commit()
    return stop_lead(db, state, reason=f"handoff_{reason}"[:64])


def record_outbound_reply(db: Session, state: GtmLinkedInLeadState, body: str) -> None:
    """Store a reply we sent, and count the conversation turn.

    Two consumers: the turn cap (§5.1) reads `conversation_turns`, and the
    human-takeover check compares thread bubbles against our recorded outbound
    bodies — without this row, our OWN reply would look like a stranger's message
    and halt the sequence on the next gate.
    """
    try:
        db.execute(
            _sql_text(
                "INSERT INTO nexus_linkedin_messages "
                "  (workspace_id, lead_id, direction, body, variant, step, intent, sent_at, "
                "   linkedin_message_urn) "
                "VALUES (:w, :l, 'outbound', :b, 'reply', 0, 'conversation', NOW(), :urn)"
            ),
            {"w": state.workspace_id, "l": state.lead_id, "b": body,
             "urn": f"gtm-li-reply:{state.lead_id}:{int(state.conversation_turns or 0) + 1}"},
        )
        state.conversation_turns = int(state.conversation_turns or 0) + 1
        state.updated_at = _utcnow()
        db.commit()
        logger.info("gtm_linkedin: lead %s conversation turn %d sent",
                    state.lead_id, state.conversation_turns)
    except Exception:  # noqa: BLE001 — the message already went out; bookkeeping must not fail the job
        logger.warning("gtm_linkedin: failed to record outbound reply for lead %s",
                       state.lead_id, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass


def record_reply(db: Session, state: GtmLinkedInLeadState, *,
                 body: Optional[str], source: str) -> Dict[str, Any]:
    """Mark that the prospect replied, then stop (the un-classified default).

    Split out from `on_reply_result` so the sweep, the send-time check and the
    classifier path all record a reply identically.
    """
    state.linkedin_message_reply_status = "replied"
    state.last_reply_at = _utcnow()
    state.updated_at = _utcnow()
    db.commit()
    record_event(
        db, workspace_id=state.workspace_id, lead_id=state.lead_id,
        campaign_id=state.campaign_id, linkedin_account_id=state.linkedin_account_id,
        event_type="reply_received",
        metadata={"source": source, "chars": len(body or "") if body else 0},
    )
    p = plan.truncate(_ensure_plan(db, state))
    _save_plan(db, state, p)
    db.commit()
    return stop_lead(db, state, reason="replied")


# ── Global stop signal (called from the email reply / demo / unsubscribe path) ─
def stop_lead_states_for_lead(db: Session, *, lead_id: int, reason: str, workspace_id: int) -> int:
    """Stop every ACTIVE LinkedIn sequence for a global lead WITHIN ONE WORKSPACE
    — used when the lead replies by email, books a demo, or unsubscribes (one
    human signal stops all channels). Scoped to `workspace_id` so a reply in one
    workspace never stops another workspace's outreach to the same global lead
    (a `nexus_global_leads` row can be enrolled by multiple workspaces). Returns
    the number of sequences stopped. Best-effort: never raises."""
    try:
        states = (
            db.query(GtmLinkedInLeadState)
            .filter(
                GtmLinkedInLeadState.lead_id == lead_id,
                GtmLinkedInLeadState.workspace_id == workspace_id,
                GtmLinkedInLeadState.linkedin_sequence_status == "active",
            )
            .all()
        )
        for st in states:
            stop_lead(db, st, reason=reason)
        return len(states)
    except Exception:  # noqa: BLE001
        logger.warning("gtm_linkedin: stop_lead_states_for_lead failed for lead %s", lead_id, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return 0
