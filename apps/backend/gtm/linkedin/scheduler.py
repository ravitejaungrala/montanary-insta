"""GTM LinkedIn Agent — scheduler: randomized slot generation + dispatch + enroll.

The anti-detection control point (Section 3): the scheduler decides *when*
(jittered slot inside working hours), the worker only adds per-step jitter.

- `enroll_campaign` — create gtm_linkedin_lead_state for a campaign's leads on a
  chosen account, with the first step scheduled to a randomized slot.
- `schedule_step` — set a lead's next step + randomized `next_action_at`
  (reused by the Phase-3 sequence engine when advancing).
- `dispatch_due` — claim due + healthy lead-state, build the job (content via
  content_generator), enqueue it; null `next_action_at` so it isn't re-dispatched
  until the next step is scheduled. Send-type steps are dispatched in Phase 2;
  check_* steps are introduced by the Phase-3 sequence engine.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from gtm.linkedin import auth, browser, content_generator, safety
from gtm.linkedin import plan as _plan
from gtm.linkedin.logs import setup as _setup_logs
from gtm.linkedin.models import (
    GtmLinkedInAccount,
    GtmLinkedInAccountBrand,
    GtmLinkedInAccountSettings,
    GtmLinkedInLeadState,
)

_setup_logs()
logger = logging.getLogger("pipelyt.gtm_linkedin.scheduler")

# step → worker job_type. Send-type steps run the browser send action; check_*
# steps run the Phase-3 detectors (acceptance / reply) then branch. The reply
# checks are uniquely named per pipeline position so the engine can resolve the
# next touch from the step name alone.
_STEP_JOB = {
    "connection_request": "send_connection_request",
    "connect_and_inmail": "send_connect_and_inmail",  # combined first touch
    "check_acceptance": "check_connection_status",
    "message_1": "send_message",
    "check_reply_1": "check_reply_status",
    "message_2": "send_message",
    "check_reply_2": "check_reply_status",
    "close_message": "send_message",
    "inmail_1": "send_inmail",
    "check_reply_i1": "check_reply_status",
    "inmail_followup": "send_inmail",
    "check_reply_i2": "check_reply_status",
    "inmail_followup_2": "send_inmail",
    "check_reply_i3": "check_reply_status",
    "close_inmail": "send_inmail",
}
_SEND_JOBS = {"send_connection_request", "send_connect_and_inmail", "send_message", "send_inmail"}
_CHECK_JOBS = {"check_connection_status", "check_reply_status"}
_DEFAULT_DISPATCH_CAP = 25


def _utcnow() -> datetime:
    return datetime.utcnow()


def public_identifier(url: Optional[str]) -> Optional[str]:
    """Extract LinkedIn's public-identifier slug from a profile URL once at
    import: 'https://linkedin.com/in/john-smith-tech/' → 'john-smith-tech'.
    More stable to match/dedup on than the full URL."""
    if not url:
        return None
    m = re.search(r"/in/([^/?#]+)", url)
    return m.group(1) if m else None


def _settings(db: Session, account_id: int) -> Optional[GtmLinkedInAccountSettings]:
    return db.query(GtmLinkedInAccountSettings).filter_by(linkedin_account_id=account_id).first()


# ── Randomized slot ───────────────────────────────────────────────────────────
def random_slot(
    settings: Any, *, base: Optional[datetime] = None,
    min_minutes: float = 2.0, max_minutes: float = 20.0, delay_days: int = 0,
    delay_minutes: int = 0,
) -> datetime:
    """A jittered future time that falls inside the account's working window.
    `delay_days` shifts the base forward first (cadence spacing)."""
    import random

    slot = (base or _utcnow()) + timedelta(days=int(delay_days or 0),
                                           minutes=int(delay_minutes or 0))
    slot = slot + timedelta(minutes=random.uniform(min_minutes, max_minutes))
    if settings is None:
        return slot
    # Roll forward (hour steps, capped) until inside the working window.
    for _ in range(48):
        if browser.in_working_hours(settings, now_utc=slot):
            return slot
        slot = slot + timedelta(hours=1)
    return slot


def schedule_step(
    db: Session, state: GtmLinkedInLeadState, *, current_step: str,
    branch: Optional[str] = None, delay_days: int = 0, delay_minutes: int = 0,
    jitter_minutes: Optional[tuple] = None,
) -> None:
    """Point a lead at its next step and a fresh randomized slot.

    `delay_minutes` exists for conversation replies: answering days later defeats
    the point of replying at all.

    `jitter_minutes` overrides the default 2-20 min spread. That default is sized
    for day-scale cadence gaps, where it's noise; on a 5-minute reply window it
    would dominate — a "5-10 min" reply would really land 7-30 min out. Callers
    working at minute scale pass their own, tighter, spread.
    """
    s = _settings(db, state.linkedin_account_id) if state.linkedin_account_id else None
    state.linkedin_current_step = current_step
    if branch is not None:
        state.linkedin_current_branch = branch
    lo, hi = jitter_minutes if jitter_minutes else (2.0, 20.0)
    state.next_action_at = random_slot(
        s, delay_days=delay_days, delay_minutes=delay_minutes,
        min_minutes=lo, max_minutes=hi,
    )
    # Advancing means the previous step genuinely succeeded, so the retry budget
    # resets. Without this, a lead that hit a few transient failures early would
    # be stopped by one unrelated failure much later in a healthy sequence.
    # (defer_lead sets next_action_at directly and never routes through here, so
    # this can't clobber an in-progress backoff.)
    state.deferral_count = 0
    state.defer_reason = None
    state.updated_at = _utcnow()
    db.commit()


# ── Enrollment ────────────────────────────────────────────────────────────────
def _first_step_for_mode(workflow_mode: str):
    """First (step, branch, plan) per workflow mode:
       standard → connection request, then the acceptance checks
       combined → connection request + InMail together, then the checks
       hybrid   → InMail only (no connection phase)

    The plan is materialized HERE, at enrollment, so the lead's whole cadence is
    inspectable data from the moment it exists — and rewritable later.
    """
    p = _plan.build_plan(workflow_mode)
    _i, first = _plan.next_pending(p)
    branch = "inmail" if (workflow_mode or "").lower() == "hybrid" else "pending_acceptance"
    step = first["step"] if first else "connection_request"
    return step, branch, p


def auto_enroll_lead(
    db: Session, *, workspace_id: int, campaign_id: Optional[int], lead_id: int,
    workflow_mode: str = "standard",
) -> bool:
    """Best-effort: enroll ONE lead into the LinkedIn sequence — the per-lead
    mirror of the email auto-enroll. Called from the email intent_sweep accept
    path so a lead starts on LinkedIn at the SAME moment it starts on email,
    WHEN the campaign's brand has a connected active LinkedIn account and the lead
    has a LinkedIn URL. Idempotent on (lead_id, campaign_id). Never raises (a
    failure here must not break the email enroll)."""
    try:
        if not campaign_id:
            return False
        exists = (
            db.query(GtmLinkedInLeadState)
            .filter_by(lead_id=lead_id, campaign_id=campaign_id)
            .first()
        )
        if exists:
            return False
        from nexus.services.sequencer import _brand_id_for_campaign
        brand_id = _brand_id_for_campaign(db, campaign_id)
        if brand_id is None:
            return False
        accts = auth.accounts_for_brand(db, workspace_id, brand_id)  # active + non-paused only
        if not accts:
            return False
        acct = accts[0]
        from sqlalchemy import text as _t
        row = db.execute(
            _t("SELECT linkedin_url FROM nexus_global_leads WHERE id = :id"), {"id": lead_id}
        ).first()
        url = (row[0] or "").strip() if row and row[0] else ""
        if not url:
            return False
        s = _settings(db, acct.id)
        first_step, first_branch, first_plan = _first_step_for_mode(workflow_mode)
        with db.begin_nested():  # isolate from the caller's (email) transaction
            db.add(GtmLinkedInLeadState(
                workspace_id=workspace_id,
                lead_id=lead_id,
                campaign_id=campaign_id,
                linkedin_account_id=acct.id,
                linkedin_profile_url=url,
                linkedin_public_identifier=public_identifier(url),
                linkedin_sequence_status="active",
                linkedin_current_step=first_step,
                linkedin_current_branch=first_branch,
                plan=first_plan,
                workflow_mode=workflow_mode,
                next_action_at=random_slot(s, min_minutes=0.5, max_minutes=3.0),
            ))
        logger.info("auto-enroll: lead %s campaign %s → LinkedIn account %s (%s), mode=%s",
                    lead_id, campaign_id, acct.id, acct.linkedin_email, workflow_mode)
        return True
    except Exception:  # noqa: BLE001
        logger.warning("auto-enroll: LinkedIn enroll skipped for lead %s campaign %s (error)",
                       lead_id, campaign_id, exc_info=True)
        return False


def enroll_campaign(
    db: Session, *, workspace_id: int, campaign_id: int, account_id: int,
    workflow_mode: str = "standard",
) -> Dict[str, Any]:
    """Create gtm_linkedin_lead_state rows for the campaign's leads that have a
    LinkedIn URL, bound to `account_id`, first step scheduled. Idempotent on
    (lead_id, campaign_id)."""
    from sqlalchemy import text as _t

    # Tenant + brand ownership (defense-in-depth; enforced for ALL callers).
    # The campaign must belong to THIS workspace, and the chosen account must be
    # mapped to the campaign's SENDER BRAND — resolved with the SAME key the email
    # engine uses (product → normalized URL+entity → nexus_sender_brands), so
    # email + LinkedIn pick senders identically. A caller can't enroll another
    # workspace's campaign, nor drive one from an unmapped account.
    camp = db.execute(
        _t("SELECT 1 FROM nexus_campaigns WHERE id = :c AND workspace_id = :w"),
        {"c": campaign_id, "w": workspace_id},
    ).first()
    if camp is None:
        raise ValueError("campaign not found in this workspace")
    from nexus.services.sequencer import _brand_id_for_campaign
    _brand_id = _brand_id_for_campaign(db, campaign_id)
    if _brand_id is not None:
        mapped = (
            db.query(GtmLinkedInAccountBrand)
            .filter_by(linkedin_account_id=account_id, brand_id=_brand_id)
            .first()
        )
        if mapped is None:
            raise ValueError("account is not mapped to this campaign's brand")

    rows = db.execute(
        _t(
            """
            SELECT DISTINCT gl.id AS lead_id, gl.linkedin_url
              FROM nexus_leads nl
              JOIN nexus_global_leads gl ON gl.id = nl.global_lead_id
             WHERE nl.campaign_id = :c
               AND COALESCE(nl.signals -> 'intent' ->> 'status', '') <> 'rejected'
               AND COALESCE(NULLIF(gl.linkedin_url, ''), '') <> ''
            """
        ),
        {"c": campaign_id},
    ).fetchall()

    s = _settings(db, account_id)
    first_step, first_branch, first_plan = _first_step_for_mode(workflow_mode)
    created = 0
    for r in rows:
        lead_id = int(r[0])
        exists = (
            db.query(GtmLinkedInLeadState)
            .filter_by(lead_id=lead_id, campaign_id=campaign_id)
            .first()
        )
        if exists:
            continue
        st = GtmLinkedInLeadState(
            workspace_id=workspace_id,
            lead_id=lead_id,
            campaign_id=campaign_id,
            linkedin_account_id=account_id,
            linkedin_profile_url=r[1],
            linkedin_public_identifier=public_identifier(r[1]),
            linkedin_sequence_status="active",
            linkedin_current_step=first_step,
            linkedin_current_branch=first_branch,
            # Each lead gets its OWN copy — the plan is mutated per lead, so a
            # shared list would let one lead's re-plan rewrite everyone else's.
            plan=[dict(t) for t in first_plan],
            workflow_mode=workflow_mode,
            # Near-immediate first slot (still jittered + inside working hours) so
            # the next scheduler tick auto-picks newly enrolled leads right away.
            next_action_at=random_slot(s, min_minutes=0.5, max_minutes=3.0),
        )
        db.add(st)
        created += 1
    db.commit()
    logger.info("gtm_linkedin: enrolled %d lead(s) for campaign %s on account %s", created, campaign_id, account_id)
    return {"enrolled": created, "candidates": len(rows)}


# ── Dispatch ──────────────────────────────────────────────────────────────────
# Pre-generated cadence lives in nexus_linkedin_messages (written when the
# campaign runs): variant='dm' (1 row = the connection-request note) and
# variant='inmail' steps 0-3 (initial / follow-up 1 / follow-up 2 / closing),
# keyed by global lead_id + workspace_id. We prefer that stored copy and only
# generate fresh when a row is missing.
from sqlalchemy import text as _sql_text

# InMail cadence step index per GTM step. The combined first touch (and the
# standard inmail_1) send the INITIAL InMail (step 0); the post-acceptance
# messages reuse the later InMail bodies as free DMs (step 0 already went out).
_INMAIL_STEP_IDX = {
    "inmail_1": 0, "inmail_followup": 1, "inmail_followup_2": 2, "close_inmail": 3,
    "message_1": 1, "message_2": 2, "close_message": 3,
}


def current_touch(state: GtmLinkedInLeadState) -> Optional[Dict[str, Any]]:
    """The plan touch this lead's `linkedin_current_step` refers to.

    `linkedin_current_step` is a projection of the plan (kept for the Flow tab
    and the /flow endpoint, which key off it). Returns None for a lead with no
    plan yet — callers fall back to the legacy name-based lookups.
    """
    if not state.plan:
        return None
    return _plan.find_by_step(list(state.plan), state.linkedin_current_step or "")


def _job_type_for_state(state: GtmLinkedInLeadState) -> Optional[str]:
    """Worker job type for whatever this lead is due to do.

    Prefers the plan's `kind` (4 values); falls back to the legacy step-name
    table for leads enrolled before plans existed, so dispatch keeps working
    through the migration window.
    """
    t = current_touch(state)
    if t is not None:
        jt = _plan.job_type_for(t)
        if jt:
            return jt
        logger.warning("dispatch: lead %s touch %s has unknown kind %r",
                       state.lead_id, t.get("id"), t.get("kind"))
        return None
    return _STEP_JOB.get(state.linkedin_current_step or "")


def _latest_inbound_body(db: Session, workspace_id: int, lead_id: int) -> str:
    """The prospect's most recent message — what a conversation reply answers."""
    row = db.execute(
        _sql_text(
            "SELECT body FROM nexus_linkedin_messages "
            " WHERE workspace_id = :w AND lead_id = :l AND direction = 'inbound' "
            "   AND COALESCE(body, '') <> '' "
            " ORDER BY id DESC LIMIT 1"
        ),
        {"w": workspace_id, "l": lead_id},
    ).first()
    return (row[0] or "").strip() if row else ""


def _stored_note(db: Session, workspace_id: int, lead_id: int) -> Optional[str]:
    row = db.execute(
        _sql_text(
            "SELECT body FROM nexus_linkedin_messages WHERE workspace_id = :w AND lead_id = :l "
            "AND direction = 'outbound' AND variant = 'dm' ORDER BY id LIMIT 1"
        ),
        {"w": workspace_id, "l": lead_id},
    ).first()
    return row[0].strip() if row and row[0] and row[0].strip() else None


def _stored_inmail(db: Session, workspace_id: int, lead_id: int, step_idx: int) -> Optional[Dict[str, str]]:
    row = db.execute(
        _sql_text(
            "SELECT subject, body FROM nexus_linkedin_messages WHERE workspace_id = :w AND lead_id = :l "
            "AND direction = 'outbound' AND variant = 'inmail' AND step = :s ORDER BY id LIMIT 1"
        ),
        {"w": workspace_id, "l": lead_id, "s": step_idx},
    ).first()
    if row and row[1] and row[1].strip():
        return {"subject": (row[0] or "").strip(), "body": row[1].strip()}
    return None


def mark_content_sent(db: Session, *, workspace_id: int, lead_id: int, variant: str,
                      step: Optional[int] = None, note_attached: bool = True) -> None:
    """Mark the campaign-generated nexus_linkedin_messages row as SENT so the GTM
    Journey UI reflects the LinkedIn send the SAME way it does email — that UI
    treats a row whose `linkedin_message_urn` is set as 'sent' (touch counts,
    content status, and the Flow tab all read from it). Browser automation has no
    real message URN, so we stamp a synthetic marker. The 'dm' note has no step
    (single row) → pass step=None to match it. Best-effort; never raises.

    `note_attached` distinguishes a connection sent WITH the personalized note
    (note text delivered) from a bare connection request (no note — e.g. out of
    free notes). Both are a real touch, but only the former actually delivered
    the message, so we stamp a different marker the UI can label honestly."""
    urn_prefix = "gtm-li" if note_attached else "gtm-li-nonote"
    params: Dict[str, Any] = {
        "w": workspace_id, "l": lead_id, "v": variant,
        "urn": f"{urn_prefix}:{lead_id}:{variant}:{step if step is not None else 0}",
    }
    step_clause = ""
    if step is not None:
        step_clause = "AND step = :s "
        params["s"] = int(step)
    try:
        # Mark EXACTLY ONE row (the lowest-id unsent match) so each send maps to a
        # single touch — even if duplicate draft rows exist for this variant/step,
        # the touch count (COUNT of urn-set rows) can't be inflated.
        db.execute(
            _sql_text(
                "UPDATE nexus_linkedin_messages "
                "SET linkedin_message_urn = :urn, sent_at = NOW() "
                "WHERE id = ("
                "  SELECT id FROM nexus_linkedin_messages "
                "  WHERE workspace_id = :w AND lead_id = :l AND direction = 'outbound' "
                f"  AND variant = :v {step_clause}"
                "  AND (linkedin_message_urn IS NULL OR linkedin_message_urn = '') "
                "  ORDER BY step, id LIMIT 1"
                ")"
            ),
            params,
        )
        db.commit()
        logger.info("content: marked %s step %s SENT for lead %s (UI now reflects it)", variant, step, lead_id)
    except Exception:  # noqa: BLE001
        logger.warning("content: mark_content_sent failed for lead %s %s/%s", lead_id, variant, step, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass


def _content_payload(db: Session, state: GtmLinkedInLeadState, job_type: str) -> Dict[str, Any]:
    """Copy for this step. Prefers the campaign's PRE-GENERATED cadence stored in
    nexus_linkedin_messages; falls back to on-the-fly generation (lead+product via
    the email engine's loaders) only when a stored row is missing."""
    ws, lead_id = state.workspace_id, state.lead_id
    step = state.linkedin_current_step or ""

    # The plan carries an explicit content_step per touch. Prefer it over the
    # legacy name→index table, which defaulted an unrecognised step to index 1 —
    # silently re-sending follow-up 1's body.
    _touch = current_touch(state)

    def _cstep(default: int) -> int:
        if _touch is not None and _touch.get("content_step") is not None:
            return int(_touch["content_step"])
        return _INMAIL_STEP_IDX.get(step, default)

    def _lp():
        from nexus.services.sequencer import _load_lead, _load_product
        lead = _load_lead(db, lead_id) or {}
        product = _load_product(db, ws, campaign_id=state.campaign_id) or {}
        return lead, product, (lead.get("enrichment") or {})

    if job_type == "send_connection_request":
        note = _stored_note(db, ws, lead_id)
        if not note:
            note = content_generator.generate_connection_note(*_lp())
        return {"note": note}

    if job_type == "send_connect_and_inmail":
        # First touch: stored connection NOTE + stored INITIAL InMail (step 0).
        note = _stored_note(db, ws, lead_id)
        im = _stored_inmail(db, ws, lead_id, 0)
        if not note or not im:
            lead, product, enr = _lp()
            if not note:
                note = content_generator.generate_connection_note(lead, product, enr)
            if not im:
                im = content_generator.generate_linkedin_inmail(lead, product, enr)
        return {"note": note, **im}

    if job_type == "send_inmail":
        im = _stored_inmail(db, ws, lead_id, _cstep(0))
        if not im:
            im = content_generator.generate_linkedin_inmail(*_lp())
        return im

    if job_type == "send_message":
        # A conversation reply is generated per turn against what they actually
        # said — it has no pre-generated row, and must NOT fall through to the
        # stored-InMail lookup below (whose `_INMAIL_STEP_IDX.get(step, 1)`
        # default would send follow-up 1's body as if it were an answer).
        if _touch is not None and _touch.get("is_reply"):
            lead, product, enr = _lp()
            body = content_generator.generate_conversation_reply(
                lead, product, _latest_inbound_body(db, ws, lead_id), enr,
            )
            # None ⇒ ungroundable or unsafe. Empty payload; the worker turns that
            # into a hand-off rather than sending anything.
            return {"body": body} if body else {"body": "", "reply_declined": True}

        # InMail-as-msg: after acceptance deliver the stored InMail bodies as free DMs.
        im = _stored_inmail(db, ws, lead_id, _cstep(1))
        if im:
            return {"body": im["body"]}
        lead, product, enr = _lp()
        # Prefer the touch's semantic role; fall back to the legacy step name.
        role = (_touch or {}).get("role") or ""
        if role == "close" or step == "close_message":
            body = content_generator.generate_close_message(lead, product, enr)
        elif role == "opener" or step == "message_1":
            body = content_generator.generate_linkedin_message(lead, product, enr)
        else:
            body = content_generator.generate_followup_message(lead, product, enr, step=2)
        return {"body": body}

    return {}


def dispatch_due(db: Session, *, max_rows: int = _DEFAULT_DISPATCH_CAP) -> Dict[str, Any]:
    """Claim due + healthy lead-state rows and enqueue their step's job."""
    now = _utcnow()
    due = (
        db.query(GtmLinkedInLeadState)
        .join(GtmLinkedInAccount, GtmLinkedInAccount.id == GtmLinkedInLeadState.linkedin_account_id)
        .filter(
            GtmLinkedInLeadState.linkedin_sequence_status == "active",
            GtmLinkedInLeadState.next_action_at.isnot(None),
            GtmLinkedInLeadState.next_action_at <= now,
            GtmLinkedInAccount.linkedin_session_status == "active",
            GtmLinkedInAccount.is_paused.is_(False),
            (GtmLinkedInAccount.cooldown_until.is_(None)) | (GtmLinkedInAccount.cooldown_until <= now),
            (GtmLinkedInAccount.execution_locked_until.is_(None)) | (GtmLinkedInAccount.execution_locked_until <= now),
        )
        .order_by(GtmLinkedInLeadState.next_action_at.asc())
        .limit(max_rows)
        .all()
    )
    enqueued = 0
    skipped = 0
    capped = 0
    off_hours = 0
    # Per-tick send budget keyed by (account_id, cap-bucket). Without this, every
    # due lead reads daily_connection_count BEFORE the worker bumps it, so a whole
    # batch passes `remaining > 0` in one tick and the ramp cap never bites. We
    # reserve a slot as we enqueue so only `remaining` sends go out per tick; the
    # rest keep next_action_at and retry next tick (→ tomorrow once the cap resets).
    budget: Dict[Any, int] = {}
    for st in due:
        job_type = _job_type_for_state(st)
        if job_type in _SEND_JOBS:
            # Warm-up ramp: don't enqueue a send the worker would only skip once the
            # ramped daily cap is hit. Leave next_action_at so it retries next tick.
            acct = db.query(GtmLinkedInAccount).get(st.linkedin_account_id)
            s = _settings(db, st.linkedin_account_id)
            # Working-hours guard: a send whose slot is now outside the account's
            # window (slot was in-window but got backlogged, or the clock rolled past
            # the window) must NOT fire. Leave next_action_at so it dispatches as
            # soon as the window reopens. Read-only CHECK jobs are exempt below.
            if not browser.in_working_hours(s):
                off_hours += 1
                continue
            # The combined first touch is gated on the connection cap (its primary
            # action); the InMail half is cap-checked per-action in the worker.
            gate_job = "send_connection_request" if job_type == "send_connect_and_inmail" else job_type
            bkey = (st.linkedin_account_id, gate_job)
            if bkey not in budget:
                budget[bkey] = safety.remaining_daily(db, acct, s, gate_job) if acct else 0
            if acct is None or budget[bkey] <= 0:
                capped += 1
                continue
            payload = _content_payload(db, st, job_type)
            budget[bkey] -= 1  # reserve this tick's slot so we don't over-dispatch
        elif job_type in _CHECK_JOBS:
            payload = {}  # detectors need no content
        else:
            # Unresolvable step: no plan touch and no legacy mapping. `continue`
            # alone would leave next_action_at in the past, so this lead would be
            # re-picked on EVERY tick forever, crowding out real work. Clear the
            # due-key and stop it — a lead we can't act on is not a lead we
            # should keep looking at.
            logger.error("dispatch: lead %s has unresolvable step %r — stopping",
                         st.lead_id, st.linkedin_current_step)
            st.next_action_at = None
            st.linkedin_sequence_status = "stopped"
            st.paused_reason = "unresolvable_step"
            st.updated_at = _utcnow()
            db.commit()
            skipped += 1
            continue
        auth.enqueue_job(
            db, workspace_id=st.workspace_id, account_id=st.linkedin_account_id,
            job_type=job_type, payload=payload, lead_id=st.lead_id, campaign_id=st.campaign_id,
        )
        # Claim: clear the due-key so the next tick won't re-dispatch. The
        # Phase-3 engine reschedules the next step after the action completes.
        st.next_action_at = None
        st.updated_at = _utcnow()
        db.commit()
        enqueued += 1
    return {"enqueued": enqueued, "skipped": skipped, "capped": capped,
            "off_hours": off_hours, "due": len(due)}


def _autopause_sweep(db: Session) -> int:
    """Auto-pause any active, unpaused account whose recent jobs are a failure streak
    (session break / UI change). Cheap; runs each tick. Returns how many were paused."""
    accts = (
        db.query(GtmLinkedInAccount)
        .filter(
            GtmLinkedInAccount.linkedin_session_status == "active",
            GtmLinkedInAccount.is_paused.is_(False),
        )
        .all()
    )
    paused = 0
    for a in accts:
        try:
            if safety.evaluate_and_autopause(db, a):
                paused += 1
        except Exception:  # noqa: BLE001
            logger.warning("gtm_linkedin: autopause sweep failed for account %s", a.id, exc_info=True)
            try:
                db.rollback()
            except Exception:
                pass
    return paused


def tick(db: Session) -> Dict[str, Any]:
    """Scheduler heartbeat entry (EventBridge / lambda_pinger)."""
    auto_paused = _autopause_sweep(db)
    result = dispatch_due(db)
    result["auto_paused"] = auto_paused
    # Reply detection runs on the same heartbeat. Its own interval gate means
    # most ticks queue nothing; a tick that skips it is the normal case.
    try:
        result["sweeps"] = enqueue_inbox_sweeps(db).get("queued", 0)
    except Exception:  # noqa: BLE001 — detection must never break dispatch
        logger.warning("scheduler tick: inbox sweep enqueue failed", exc_info=True)
        result["sweeps"] = 0
    logger.info(
        "scheduler tick: due=%s enqueued=%s capped(daily-limit)=%s skipped=%s "
        "auto_paused=%s sweeps=%s",
        result.get("due", 0), result.get("enqueued", 0), result.get("capped", 0),
        result.get("skipped", 0), auto_paused, result.get("sweeps", 0),
    )
    return result


# How often each account's inbox is swept. Every 2-4h inside working hours is
# FEWER page loads than a real person makes — the messaging page is the most
# visited surface for an active LinkedIn account — while capping reply latency at
# a few hours. Daily would cap it at 24h, which is too slow to call a reply
# prompt; that gap is the difference between "they replied to me" and "a bot got
# back to me the next day".
INBOX_SWEEP_INTERVAL_HOURS = float(os.getenv("GTM_LINKEDIN_SWEEP_HOURS", "4"))


def enqueue_inbox_sweeps(db: Session) -> Dict[str, Any]:
    """Queue one inbox sweep per healthy account that is due for one.

    Per ACCOUNT, not per lead: the sweep reads the conversation list in a single
    page load and only the matched threads get opened.

    Skipped for accounts with no active leads — sweeping an idle account is pure
    detection risk for no signal.
    """
    now = _utcnow()
    cutoff = now - timedelta(hours=INBOX_SWEEP_INTERVAL_HOURS)
    queued = 0
    skipped = 0
    try:
        accounts = (
            db.query(GtmLinkedInAccount)
            .filter(
                GtmLinkedInAccount.linkedin_session_status == "active",
                GtmLinkedInAccount.is_paused.is_(False),
                (GtmLinkedInAccount.cooldown_until.is_(None)) | (GtmLinkedInAccount.cooldown_until <= now),
            )
            .all()
        )
    except Exception:  # noqa: BLE001
        logger.warning("inbox-sweep: account query failed", exc_info=True)
        db.rollback()
        return {"queued": 0, "skipped": 0}

    for a in accounts:
        s = _settings(db, a.id)
        if not browser.in_working_hours(s):
            skipped += 1
            continue
        # Only sweep where a reply could plausibly arrive.
        active_leads = (
            db.query(GtmLinkedInLeadState)
            .filter(
                GtmLinkedInLeadState.linkedin_account_id == a.id,
                GtmLinkedInLeadState.linkedin_sequence_status == "active",
            )
            .count()
        )
        if not active_leads:
            skipped += 1
            continue
        # Don't re-queue while a recent sweep is still pending or fresh.
        recent = (
            db.query(GtmLinkedInJob)
            .filter(
                GtmLinkedInJob.linkedin_account_id == a.id,
                GtmLinkedInJob.job_type == "sweep_inbox",
                GtmLinkedInJob.created_at >= cutoff,
            )
            .first()
        )
        if recent is not None:
            skipped += 1
            continue
        auth.enqueue_job(db, workspace_id=a.workspace_id, account_id=a.id,
                         job_type="sweep_inbox", payload={})
        queued += 1
    logger.info("inbox-sweep: queued %d, skipped %d", queued, skipped)
    return {"queued": queued, "skipped": skipped}


def enqueue_session_validations(db: Session) -> Dict[str, Any]:
    """Phase-5 daily sweep: enqueue a `validate_session` job for every active,
    non-paused account so a stale/expired cookie is caught + flagged for reconnect
    BEFORE it breaks a real send. Triggered by a daily EventBridge rule (infra).
    The worker's `validate_session` path marks expired sessions and pauses their
    leads."""
    now = _utcnow()
    accts = (
        db.query(GtmLinkedInAccount)
        .filter(
            GtmLinkedInAccount.linkedin_session_status == "active",
            GtmLinkedInAccount.is_paused.is_(False),
            (GtmLinkedInAccount.cooldown_until.is_(None)) | (GtmLinkedInAccount.cooldown_until <= now),
        )
        .all()
    )
    n = 0
    for a in accts:
        auth.enqueue_job(db, workspace_id=a.workspace_id, account_id=a.id, job_type="validate_session", payload={})
        n += 1
    logger.info("gtm_linkedin: enqueued %d session validation(s)", n)
    return {"validations_enqueued": n}
