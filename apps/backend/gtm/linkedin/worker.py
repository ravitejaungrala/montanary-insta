"""GTM LinkedIn Agent — SQS worker: executes ONE job per invocation.

Flow per job (Section 2 of the plan):
  1. idempotency guard (gtm_linkedin_jobs.is_executed)  → skip if already done
  2. claim: status=running, locked_at=now
  3. for non-login jobs: account must be healthy; validate the live session
     (invalid → mark_session_expired, no retry)
  4. dispatch to the browser action, with human pacing + per-type daily caps
  5. record gtm_linkedin_events + update gtm_linkedin_lead_state
  6. mark job completed (or failed → SQS retry → DLQ)

Playwright is reached only via `browser.py` (lazy import), so this module
imports fine in the main backend; only the worker container actually runs it.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from gtm.linkedin import auth, browser, content_generator, crypto, safety, scheduler, sequence_engine
from gtm.linkedin.events import record_event
from gtm.linkedin.logs import setup as _setup_logs
from gtm.linkedin.models import (
    GtmLinkedInAccount,
    GtmLinkedInAccountSettings,
    GtmLinkedInBrowserProfile,
    GtmLinkedInJob,
    GtmLinkedInLeadState,
)

_setup_logs()
logger = logging.getLogger("pipelyt.gtm_linkedin.worker")

# job_type → (settings counter attr, settings limit attr)
_CAP_FIELDS = {
    "send_connection_request": ("daily_connection_count", "daily_connection_limit"),
    # Combined first touch is capped on the connection cap (its primary action).
    # The handler bumps daily_connection_count explicitly, so no double-count here.
    "send_connect_and_inmail": ("daily_connection_count", "daily_connection_limit"),
    "send_message": ("daily_message_count", "daily_message_limit"),
    "send_inmail": ("daily_inmail_count", "daily_inmail_limit"),
}


def _utcnow() -> datetime:
    return datetime.utcnow()


def _settings(db: Session, account_id: int) -> Optional[GtmLinkedInAccountSettings]:
    return db.query(GtmLinkedInAccountSettings).filter_by(linkedin_account_id=account_id).first()


def _profile(db: Session, account_id: int) -> Optional[GtmLinkedInBrowserProfile]:
    return db.query(GtmLinkedInBrowserProfile).filter_by(linkedin_account_id=account_id).first()


def _lazy_reset_counts(db: Session, s: GtmLinkedInAccountSettings) -> None:
    """Zero the per-type daily counters on a new local-timezone day. Delegates to
    the shared helper so the worker (send-path) and scheduler (dispatch-path) use
    one identical reset rule and day boundary."""
    safety.reset_daily_counts_if_new_day(db, s)


def _under_cap(db: Session, acct: GtmLinkedInAccount, s: GtmLinkedInAccountSettings, job_type: str) -> bool:
    fields = _CAP_FIELDS.get(job_type)
    if not fields:
        return True
    _lazy_reset_counts(db, s)
    count_attr, _ = fields
    # Warm-up-ramped cap (new accounts send a fraction of the configured limit).
    limit = safety.effective_daily_limit(db, acct, s, job_type)
    return int(getattr(s, count_attr, 0) or 0) < limit


def _bump_count(db: Session, s: GtmLinkedInAccountSettings, job_type: str) -> None:
    fields = _CAP_FIELDS.get(job_type)
    if not fields:
        return
    count_attr, _ = fields
    setattr(s, count_attr, int(getattr(s, count_attr, 0) or 0) + 1)
    db.commit()


def _apply_identity(st: "GtmLinkedInLeadState", identity: Optional[Dict[str, Any]]) -> None:
    """Self-heal the lead's stored URL/name from what we just saw on the profile.
    If LinkedIn redirected to a new canonical URL, update the primary URL + slug too."""
    if not identity:
        return
    url = identity.get("url")
    name = identity.get("name")
    # Only treat it as a profile read when we're actually on a /in/ page (the
    # reply check opens a messaging thread, which must NOT overwrite the URL).
    if url and "/in/" in url:
        st.last_seen_profile_url = url
        if url != (st.linkedin_profile_url or ""):
            st.linkedin_profile_url = url
            from gtm.linkedin.scheduler import public_identifier
            slug = public_identifier(url)
            if slug:
                st.linkedin_public_identifier = slug
        if name:
            st.last_seen_name = name
        st.last_verified_at = _utcnow()


def _state_for(db: Session, job: GtmLinkedInJob) -> Optional[GtmLinkedInLeadState]:
    if not job.lead_id or not job.campaign_id:
        return None
    return (
        db.query(GtmLinkedInLeadState)
        .filter_by(lead_id=job.lead_id, campaign_id=job.campaign_id)
        .first()
    )


def _defer(db: Session, job: GtmLinkedInJob, reason: str, *,
           hours: Optional[float] = None, count_attempt: bool = False) -> Dict[str, Any]:
    """Re-arm this job's lead so the scheduler picks it up again.

    THE INVARIANT: no code path may end with a lead `active` and
    `next_action_at IS NULL`. `dispatch_due` clears next_action_at when it
    enqueues, and since b9c5cbb a failed job's SQS message is deleted rather than
    redelivered — so a bare `return` on any non-terminal outcome permanently
    strands the lead while it still reads as 'active'.

    Account-level reasons (cap, cooldown, expired session, lock contention) pass
    count_attempt=False: they say nothing about this lead and must not consume
    its retry budget. Best-effort — never raises on the failure path.
    """
    try:
        st = _state_for(db, job)
        if st is None:
            return {}
        if st.linkedin_sequence_status != "active":
            return {}  # already stopped/completed — nothing to re-arm
        return sequence_engine.defer_lead(
            db, st, reason=reason, delay_hours=hours, count_attempt=count_attempt,
        )
    except Exception:  # noqa: BLE001
        logger.warning("worker: defer failed for job %s (%s)", getattr(job, "id", "?"), reason,
                       exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return {}


def _reply_gate(db: Session, st: GtmLinkedInLeadState, s: Any, page: Any,
                acct: GtmLinkedInAccount, job: GtmLinkedInJob) -> Optional[Dict[str, Any]]:
    """Read the thread before a gated send. Returns a result dict to ABORT the
    send, or None to proceed.

    Only runs for touches carrying `check_reply_first` — the opener never has it
    (there is nothing to reply to yet), every follow-up does.

    An unreadable thread RAISES, which defers the job. That is deliberate: the
    alternative is sending a follow-up to someone who may already have replied,
    and a delayed touch is cheaper than a rude one.
    """
    touch = scheduler.current_touch(st)
    if not touch or not touch.get("check_reply_first"):
        return None

    read = browser.read_reply_thread(page, st.linkedin_profile_url or "", s, self_name=(acct.linkedin_display_name or None))
    acct.last_successful_action_at = _utcnow()
    db.commit()

    if not read.get("replied"):
        # No reply — but is the newest OUTBOUND message actually ours? If a rep
        # answered manually, the thread's last bubble is a `self` bubble and this
        # would otherwise read as "no reply yet" and send a follow-up on top of a
        # live human conversation.
        if sequence_engine.is_human_takeover(db, st, read):
            return {"action": "human_takeover", "aborted": job.job_type,
                    **sequence_engine.on_human_takeover(db, st)}
        logger.info("reply-gate: no reply for lead %s — proceeding with %s",
                    job.lead_id, job.job_type)
        return None

    logger.info("reply-gate: lead %s ALREADY REPLIED — aborting %s",
                job.lead_id, job.job_type)
    return {"action": "reply_found_before_send", "aborted": job.job_type,
            **sequence_engine.on_reply_read(db, st, read, source="send_gate")}


def _norm_name(s: Optional[str]) -> str:
    return " ".join((s or "").split()).strip().lower()


# A sweep opens at most this many threads per run. The list read is one page
# load; each match costs another. Capping bounds a bad day (or a bad matcher)
# rather than letting one sweep hammer the account.
_SWEEP_MAX_OPENS = 8


def _do_inbox_sweep(db: Session, acct: GtmLinkedInAccount, s: Any, page: Any) -> Dict[str, Any]:
    """Read the messaging list, then open ONLY the threads belonging to leads we
    are actively sequencing.

    This is what makes a reply prompt rather than eventual. The send-time gate
    only looks when we were about to message anyway — up to 4 days after they
    wrote — which is fine for suppressing a follow-up but far too slow to call a
    reply "a reply".

    Matching is by display name against this account's active leads, using the
    name LinkedIn showed us on the profile (`last_seen_name`). An ambiguous name
    is SKIPPED, not guessed: acting on the wrong lead would answer one prospect
    with another's context.
    """
    convos = browser.sweep_inbox(page, s)
    acct.last_successful_action_at = _utcnow()
    db.commit()
    if not convos:
        return {"action": "inbox_swept", "conversations": 0, "checked": 0}

    states = (
        db.query(GtmLinkedInLeadState)
        .filter(
            GtmLinkedInLeadState.linkedin_account_id == acct.id,
            GtmLinkedInLeadState.linkedin_sequence_status == "active",
        )
        .all()
    )
    by_name: Dict[str, list] = {}
    for st in states:
        key = _norm_name(st.last_seen_name)
        if key:
            by_name.setdefault(key, []).append(st)

    checked = replies_found = ambiguous = 0
    for c in convos:
        if checked >= _SWEEP_MAX_OPENS:
            logger.info("inbox-sweep: hit the %d-thread open cap; remaining threads wait for "
                        "the next sweep or their send-time gate", _SWEEP_MAX_OPENS)
            break
        matches = by_name.get(_norm_name(c.get("name")))
        if not matches:
            continue
        if len(matches) > 1:
            # Two active leads share a display name. Guessing would answer the
            # wrong person; the send-time gate will still catch both correctly.
            ambiguous += 1
            logger.info("inbox-sweep: %r matches %d active leads — skipping (ambiguous)",
                        c.get("name"), len(matches))
            continue
        st = matches[0]
        try:
            read = browser.read_reply_thread(page, st.linkedin_profile_url or "", s, self_name=(acct.linkedin_display_name or None))
            checked += 1
            if read.get("replied"):
                replies_found += 1
                sequence_engine.on_reply_read(db, st, read, source="inbox_sweep")
            elif sequence_engine.is_human_takeover(db, st, read):
                sequence_engine.on_human_takeover(db, st)
        except Exception:  # noqa: BLE001 — one unreadable thread must not abort the sweep
            logger.warning("inbox-sweep: could not read thread for lead %s", st.lead_id,
                           exc_info=True)

    logger.info("inbox-sweep: account %s — %d conversations, %d opened, %d replies, %d ambiguous",
                acct.id, len(convos), checked, replies_found, ambiguous)
    return {"action": "inbox_swept", "conversations": len(convos), "checked": checked,
            "replies": replies_found, "ambiguous": ambiguous}


# ── Per-account execution lock (one account = one worker) ─────────────────────
_LOCK_TTL_MINUTES = 10  # auto-expires so a crashed worker can't hold it forever


def _acquire_account_lock(db: Session, account_id: int, worker_id: str) -> bool:
    """Atomically claim the account if it's free (or its lock has expired).
    Returns True on success. The single UPDATE…WHERE is the race-safe gate —
    only one worker's update can match the 'free' condition at a time."""
    n = db.execute(
        text(
            """
            UPDATE gtm_linkedin_accounts
               SET execution_locked_until = NOW() + (:ttl || ' minutes')::interval,
                   execution_locked_by = :wid
             WHERE id = :aid
               AND (execution_locked_until IS NULL OR execution_locked_until < NOW())
            """
        ),
        {"ttl": str(_LOCK_TTL_MINUTES), "wid": worker_id, "aid": account_id},
    ).rowcount
    db.commit()
    return int(n or 0) == 1


def _release_account_lock(db: Session, account_id: int, worker_id: str) -> None:
    """Release only if WE still own the lock (a TTL-expired re-claim by another
    worker must not be clobbered)."""
    try:
        db.execute(
            text(
                "UPDATE gtm_linkedin_accounts SET execution_locked_until = NULL, execution_locked_by = NULL "
                "WHERE id = :aid AND execution_locked_by = :wid"
            ),
            {"aid": account_id, "wid": worker_id},
        )
        db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("gtm_linkedin: lock release failed for account %s", account_id, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass


# ── Entry ─────────────────────────────────────────────────────────────────────
def handle_job(db: Session, job_id: int) -> Dict[str, Any]:
    """Process one job. Returns a small result dict; raises on transient errors
    so SQS redelivers (→ DLQ after maxReceiveCount)."""
    job = db.query(GtmLinkedInJob).get(job_id)
    if job is None:
        logger.warning("worker: job %s not found in DB — nothing to do", job_id)
        return {"skipped": "job_not_found", "job_id": job_id}

    logger.info("worker: received job %s type=%s account=%s status=%s",
                job_id, job.job_type, job.linkedin_account_id, job.status)

    # 1) Idempotency — never re-run an executed job (SQS at-least-once delivery).
    if job.is_executed or job.status in ("completed", "cancelled", "dead", "failed"):
        logger.info("worker: job %s already %s — skipping (idempotent)", job_id, job.status)
        return {"skipped": "already_executed", "job_id": job_id}

    # 2) Per-account lock — one LinkedIn account = one active worker. If another
    # worker holds it, leave this job 'pending' to be redelivered/re-dispatched.
    acct_id = job.linkedin_account_id
    worker_id = uuid.uuid4().hex
    if acct_id and not _acquire_account_lock(db, acct_id, worker_id):
        logger.info("worker: job %s waiting — account %s is busy with another job (will retry)",
                    job_id, acct_id)
        # "Will retry" was only true while SQS redelivered. It no longer does, so
        # re-arm the lead's due-key explicitly — a short delay, since the other
        # worker's session usually finishes within minutes.
        return {"skipped": "account_locked", "job_id": job_id, "account_id": acct_id,
                **_defer(db, job, "account_locked", hours=0.5)}

    try:
        # 3) Claim.
        job.status = "running"
        job.started_at = _utcnow()
        job.locked_at = _utcnow()
        db.commit()

        acct = db.query(GtmLinkedInAccount).get(acct_id) if acct_id else None

        try:
            if job.job_type == "linkedin_login":
                result = _do_login(db, job, acct)
            elif job.job_type == "linkedin_login_challenge":
                result = _do_challenge(db, job, acct)
            else:
                result = _do_action(db, job, acct)
            _complete(db, job)
            logger.info("worker: job %s (%s) DONE — %s", job_id, job.job_type, result or {})
            return {"ok": True, "job_id": job_id, "job_type": job.job_type, **(result or {})}
        except Exception as e:  # noqa: BLE001
            db.rollback()
            job = db.query(GtmLinkedInJob).get(job_id)
            jtype = getattr(job, "job_type", "?")
            retries = 0
            if job:
                job.status = "failed"
                job.retry_count = int(job.retry_count or 0) + 1
                retries = job.retry_count
                job.error_message = str(e)[:500]
                db.commit()
            # Retry is now OURS to own. Since b9c5cbb the Fargate worker deletes
            # the SQS message on failure (a poisoned job was blocking the whole
            # per-account FIFO group), so nothing redelivers this. Without an
            # explicit defer the lead keeps next_action_at = NULL from dispatch
            # and is never picked up again — while still reading as 'active'.
            #
            # This covers the deliberate "raise rather than guess" guards too:
            # ambiguous profile-state reads and unreadable reply threads raise on
            # purpose so the action is retried instead of branching on a bad
            # read. That safety valve only works if something actually retries.
            deferred = _defer(db, job, f"job_failed:{jtype}", count_attempt=True) if job else {}
            logger.error("worker: job %s (%s) FAILED (attempt %s): %s — lead deferred: %s",
                         job_id, jtype, retries, e, deferred or "n/a")
            logger.exception("worker: job %s traceback", job_id)
            raise  # still surfaces to the caller / DLQ path for observability
    finally:
        if acct_id:
            _release_account_lock(db, acct_id, worker_id)


def _complete(db: Session, job: GtmLinkedInJob) -> None:
    job.status = "completed"
    job.is_executed = True
    job.completed_at = _utcnow()
    db.commit()


# ── Login / challenge ─────────────────────────────────────────────────────────
def _do_login(db: Session, job: GtmLinkedInJob, acct: GtmLinkedInAccount) -> Dict[str, Any]:
    enc = (job.payload_json or {}).get("enc_password")
    if not acct or not enc:
        raise ValueError("login job missing account or encrypted password")
    password = crypto.decrypt(enc)
    prof = _profile(db, acct.id)
    with browser.browser_session(None, prof) as page:
        res = browser.do_login(page, acct.linkedin_email, password)
    return _apply_login_result(db, acct, res)


def _do_challenge(db: Session, job: GtmLinkedInJob, acct: GtmLinkedInAccount) -> Dict[str, Any]:
    code = (job.payload_json or {}).get("code") or ""
    prof = _profile(db, acct.id)
    # The challenge page must already be in the session's cookies from the login attempt.
    cookies = auth.load_cookies(db, acct)
    with browser.browser_session(cookies, prof) as page:  # headless via GTM_LINKEDIN_HEADLESS
        res = browser.submit_challenge(page, code)
    return _apply_login_result(db, acct, res)


def _apply_login_result(db: Session, acct: GtmLinkedInAccount, res: Dict[str, Any]) -> Dict[str, Any]:
    status = res.get("status")
    email = getattr(acct, "linkedin_email", "?")
    if status == "active" and res.get("cookies"):
        auth.store_session(db, account_id=acct.id, cookies_json=res["cookies"])
        logger.info("worker: LOGIN SUCCESS for %s (account %s) — session captured, status=active", email, acct.id)
        return {"login": "active"}
    if status == "needs_code":
        acct.linkedin_session_status = "challenge"
        db.commit()
        record_event(db, workspace_id=acct.workspace_id, linkedin_account_id=acct.id, event_type="login_needs_code")
        logger.info("worker: LOGIN needs a 2FA/verification code for %s (account %s) — status=challenge", email, acct.id)
        return {"login": "needs_code"}
    # failed
    acct.linkedin_session_status = "expired"
    acct.linkedin_reauth_required = True
    db.commit()
    record_event(db, workspace_id=acct.workspace_id, linkedin_account_id=acct.id, event_type="login_failed", metadata={"detail": res.get("detail")})
    logger.warning("worker: LOGIN FAILED for %s (account %s) — reason: %s (status=expired, reconnect needed)",
                   email, acct.id, res.get("detail") or "unknown")
    return {"login": "failed"}


# ── Send actions (connect / message / inmail) + validate_session ──────────────
def _do_action(db: Session, job: GtmLinkedInJob, acct: GtmLinkedInAccount) -> Dict[str, Any]:
    if acct is None:
        raise ValueError("action job missing account")
    s = _settings(db, acct.id)
    prof = _profile(db, acct.id)

    # Health gate (defense in depth; scheduler already filters these).
    # Each of these is an ACCOUNT-level condition, not a statement about this
    # lead — so we re-arm the lead's due-key (dispatch_due nulled it at enqueue)
    # WITHOUT burning its retry budget. Returning bare, as this did before, left
    # the lead active with next_action_at NULL: never dispatched again.
    if acct.is_paused or acct.linkedin_session_status != "active":
        logger.info("action: job %s skipped — account %s not active (paused=%s status=%s)",
                    job.id, acct.id, acct.is_paused, acct.linkedin_session_status)
        return {"skipped": "account_not_active", **_defer(db, job, "account_not_active", hours=6)}
    if acct.cooldown_until and acct.cooldown_until > _utcnow():
        logger.info("action: job %s skipped — account %s in cooldown until %s",
                    job.id, acct.id, acct.cooldown_until)
        # Retry just after the cooldown expires rather than on a fixed backoff.
        _cd = max(0.25, (acct.cooldown_until - _utcnow()).total_seconds() / 3600.0)
        return {"skipped": "cooldown", **_defer(db, job, "cooldown", hours=_cd)}
    if not _under_cap(db, acct, s, job.job_type):
        logger.info("action: job %s skipped — daily cap reached for %s (account %s)",
                    job.id, job.job_type, acct.id)
        # Caps reset daily (IST-anchored, see scheduler._lazy_daily_reset), so
        # come back tomorrow rather than hammering the cap every hour.
        return {"skipped": "daily_cap_reached", **_defer(db, job, "daily_cap", hours=14)}

    logger.info("action: job %s opening browser session for account %s (%s)",
                job.id, acct.id, acct.linkedin_email)
    # Reuse the account's persistent S3 browser profile (already logged in) when
    # one exists; otherwise fall back to the legacy cookie-seed path so existing
    # accounts keep working (backward-compatible during the Phase 1→2 rollout).
    if getattr(acct, "profile_s3_key", None):
        _session_cm = browser.browser_session_persistent(acct.id, prof)
    else:
        cookies = auth.load_cookies(db, acct)
        _session_cm = browser.browser_session(cookies, prof)
    with _session_cm as page:
        # Session validation before any action (Section 9).
        if not browser.session_is_valid(page):
            logger.warning("action: job %s — session INVALID for account %s (cookie expired). "
                           "Marking expired; reconnect needed.", job.id, acct.id)
            auth.mark_session_expired(db, account_id=acct.id)
            # Account-level: the operator must reconnect. Re-arm the lead so it
            # resumes once they do, instead of dying with the session.
            return {"skipped": "session_expired", **_defer(db, job, "session_expired", hours=12)}
        acct.last_successful_check = _utcnow()
        db.commit()

        if job.job_type == "validate_session":
            return {"session": "valid"}

        if job.job_type == "sweep_inbox":
            return _do_inbox_sweep(db, acct, s, page)

        st = _state_for(db, job)
        if st is None:
            raise ValueError("action job missing lead_state")
        payload = job.payload_json or {}

        if job.job_type == "send_connection_request":
            note = payload.get("note") or ""
            # do_connect now reads state first (one open), only sends if NOT_CONNECTED.
            res = browser.do_connect(page, st.linkedin_profile_url or "", note, s)
            if res.get("member_id"):
                st.linkedin_member_id = res["member_id"]
            _apply_identity(st, res.get("identity"))  # self-heal URL/name
            if res.get("confidence") is not None:
                st.linkedin_state_confidence = res["confidence"]
            state = res.get("state")
            if not res.get("ok"):
                if res.get("not_connectable"):
                    # Follow-only / restricted profile (no Connect offered). Not a
                    # transient error — don't retry/DLQ. Route to the InMail branch
                    # (Premium reaches them via InMail; a free account's InMail step
                    # simply skips on no-credit and the sequence completes).
                    db.commit()
                    record_event(db, workspace_id=acct.workspace_id, lead_id=job.lead_id, campaign_id=job.campaign_id, linkedin_account_id=acct.id, event_type="connection_not_available")
                    logger.info("connect: lead %s not connectable — routing to InMail branch", job.lead_id)
                    return {"action": "not_connectable", **sequence_engine.on_acceptance_result(db, st, "declined")}
                db.commit()
                if state == "UNKNOWN":
                    raise RuntimeError("state ambiguous before connect — will retry")
                raise RuntimeError(f"connect failed: {res.get('detail')}")
            # Already pending → just advance to the acceptance check.
            if state == "PENDING":
                st.linkedin_connection_status = "pending"
                st.updated_at = _utcnow()
                db.commit()
                return {"action": "already_pending", **sequence_engine.after_send(db, st, "connection_request")}
            # Already a connection → skip straight to messaging.
            # Require high confidence — the "Message-only, no degree badge"
            # heuristic (confidence=60) produces false positives on profiles
            # where the 1st-degree indicator simply didn't load.
            if state == "CONNECTED" and res.get("confidence", 0) >= 80:
                db.commit()
                return {"action": "already_connected", **sequence_engine.on_acceptance_result(db, st, "accepted")}
            if state == "CONNECTED":
                raise RuntimeError(f"state CONNECTED but low confidence ({res.get('confidence')}) — will retry")
            # NOT_CONNECTED → request was sent.
            st.linkedin_connection_status = "pending"
            st.linkedin_connection_sent_at = _utcnow()
            st.linkedin_current_step = "connection_request"
            st.updated_at = _utcnow()
            db.commit()
            _bump_count(db, s, job.job_type)
            record_event(db, workspace_id=acct.workspace_id, lead_id=job.lead_id, campaign_id=job.campaign_id, linkedin_account_id=acct.id, event_type="connection_sent", metadata={"verified": bool(res.get("verified"))})
            scheduler.mark_content_sent(db, workspace_id=acct.workspace_id, lead_id=job.lead_id, variant="dm", note_attached=bool(res.get("with_note")))
            adv = sequence_engine.after_send(db, st, "connection_request")
            return {"action": "connection_sent", "verified": bool(res.get("verified")), **adv}

        if job.job_type == "send_connect_and_inmail":
            # Combined FIRST touch: send a connection request (with note) AND a
            # first InMail, in one browser session. The acceptance check then
            # branches to messages (accepted) or InMail follow-up (not accepted).
            note = payload.get("note") or ""
            subject = payload.get("subject") or ""
            body = payload.get("body") or ""
            # 1) Connection request — reads state first, sends only if NOT_CONNECTED.
            res = browser.do_connect(page, st.linkedin_profile_url or "", note, s)
            if res.get("member_id"):
                st.linkedin_member_id = res["member_id"]
            _apply_identity(st, res.get("identity"))
            if res.get("confidence") is not None:
                st.linkedin_state_confidence = res["confidence"]
            state = res.get("state")
            not_connectable = bool(res.get("not_connectable"))
            if not res.get("ok") and not not_connectable:
                db.commit()
                if state == "UNKNOWN":
                    raise RuntimeError("state ambiguous before connect — will retry")
                raise RuntimeError(f"connect failed: {res.get('detail')}")
            # Already a 1st-degree connection → no InMail; go straight to messaging.
            if state == "CONNECTED" and res.get("confidence", 0) >= 80:
                db.commit()
                return {"action": "already_connected", **sequence_engine.on_acceptance_result(db, st, "accepted")}
            if state == "CONNECTED":
                raise RuntimeError(f"state CONNECTED but low confidence ({res.get('confidence')}) — will retry")
            if not_connectable:
                # No Connect offered (Follow-only/restricted). Can't send an invite,
                # but a PREMIUM account can still reach them via InMail — so don't
                # stop yet; fall through to the InMail attempt below.
                record_event(db, workspace_id=acct.workspace_id, lead_id=job.lead_id, campaign_id=job.campaign_id, linkedin_account_id=acct.id, event_type="connection_not_available")
                logger.info("connect: lead %s not connectable — trying InMail instead (works on Premium)", job.lead_id)
            elif state == "NOT_CONNECTED":
                # NOT_CONNECTED (request just sent). PENDING (invite already out) falls through.
                st.linkedin_connection_sent_at = _utcnow()
                _bump_count(db, s, "send_connection_request")
                record_event(db, workspace_id=acct.workspace_id, lead_id=job.lead_id, campaign_id=job.campaign_id, linkedin_account_id=acct.id, event_type="connection_sent", metadata={"verified": bool(res.get("verified")), "combined": True})
                scheduler.mark_content_sent(db, workspace_id=acct.workspace_id, lead_id=job.lead_id, variant="dm", note_attached=bool(res.get("with_note")))
            st.linkedin_connection_status = "not_connectable" if not_connectable else "pending"
            db.commit()
            # 2) First InMail — only if there's copy, we're under the InMail cap, and
            # there's credit. An InMail failure must NOT fail the job (connection
            # already went out); we just record it and move on to the acceptance check.
            inmail_sent = False
            if (subject or body) and _under_cap(db, acct, s, "send_inmail"):
                ires = browser.do_send_inmail(page, st.linkedin_profile_url or "", subject, body, s)
                if ires.get("no_credit"):
                    st.linkedin_inmail_status = "skipped_no_credit"
                    record_event(db, workspace_id=acct.workspace_id, lead_id=job.lead_id, campaign_id=job.campaign_id, linkedin_account_id=acct.id, event_type="inmail_skipped_no_credit")
                elif ires.get("ok"):
                    verified = browser.verify_inmail_success(page)
                    st.linkedin_inmail_status = "sent"
                    st.linkedin_inmail_sent_at = _utcnow()
                    _bump_count(db, s, "send_inmail")
                    record_event(db, workspace_id=acct.workspace_id, lead_id=job.lead_id, campaign_id=job.campaign_id, linkedin_account_id=acct.id, event_type="inmail_sent", metadata={"verified": verified, "combined": True})
                    scheduler.mark_content_sent(db, workspace_id=acct.workspace_id, lead_id=job.lead_id, variant="inmail", step=0)
                    inmail_sent = True
                else:
                    record_event(db, workspace_id=acct.workspace_id, lead_id=job.lead_id, campaign_id=job.campaign_id, linkedin_account_id=acct.id, event_type="inmail_failed", metadata={"detail": ires.get("detail")})
            st.linkedin_current_step = "connect_and_inmail"
            st.updated_at = _utcnow()
            db.commit()
            # If we could neither connect NOR InMail (e.g. free account + Follow-only
            # profile), the lead is genuinely unreachable → stop it (no endless checks).
            if not_connectable and not inmail_sent:
                logger.info("connect: lead %s unreachable (no Connect, no InMail credit) — stopping", job.lead_id)
                return {"action": "unreachable", **sequence_engine.stop_lead(db, st, reason="unreachable_no_connect_no_inmail")}
            adv = sequence_engine.after_send(db, st, "connect_and_inmail")
            return {"action": "connect_and_inmail_sent", "connection_state": state,
                    "inmail_sent": inmail_sent, "not_connectable": not_connectable, **adv}

        # ── Reply gate: read the thread in THIS session, immediately before a
        # gated send. Replaces the standalone `check_reply_*` steps, which ran as
        # separate jobs on separate days — two page loads instead of one, and the
        # send then fired against a check that was already days stale. Here the
        # check is fresh at the moment of decision, which is what makes "no reply
        # for 4 days" a true statement rather than an approximation.
        if job.job_type in ("send_message", "send_inmail"):
            gate = _reply_gate(db, st, s, page, acct, job)
            if gate is not None:
                return gate

        if job.job_type == "send_message":
            body = payload.get("body") or ""
            _touch = scheduler.current_touch(st)
            _is_reply = bool(_touch and _touch.get("is_reply"))

            # An in-thread reply with no body means the generator refused to
            # answer (ungroundable question) or its output was unsafe. Sending
            # nothing and flagging a human is the CORRECT outcome here — a
            # fabricated answer to a real question, under the customer's name,
            # is the worst thing this engine could produce.
            if _is_reply and not body.strip():
                logger.info("reply: lead %s — generator declined to answer; handing to a human",
                            job.lead_id)
                return {"action": "reply_declined",
                        **sequence_engine.on_reply_undeliverable(db, st, reason="ungroundable")}
            if not body.strip():
                raise RuntimeError("send_message: empty body")

            res = browser.do_send_message(page, st.linkedin_profile_url or "", body, s)
            if not res.get("ok"):
                raise RuntimeError(f"message failed: {res.get('detail')}")
            _apply_identity(st, browser.capture_profile_identity(page))  # self-heal
            verified = browser.verify_message_in_thread(page, body)  # confirm it landed
            st.linkedin_message_sent_at = _utcnow()
            st.last_message_at = _utcnow()
            st.updated_at = _utcnow()
            db.commit()
            _bump_count(db, s, job.job_type)
            record_event(db, workspace_id=acct.workspace_id, lead_id=job.lead_id, campaign_id=job.campaign_id, linkedin_account_id=acct.id,
                         event_type="reply_sent" if _is_reply else "message_sent",
                         metadata={"verified": verified})
            if _is_reply:
                # Record it as OUR outbound so the human-takeover check (which
                # compares thread bubbles against messages we know we sent)
                # doesn't later mistake our own reply for a colleague's.
                sequence_engine.record_outbound_reply(db, st, body)
            else:
                scheduler.mark_content_sent(db, workspace_id=acct.workspace_id, lead_id=job.lead_id, variant="inmail",
                                            step=(_touch or {}).get("content_step")
                                            if (_touch or {}).get("content_step") is not None
                                            else scheduler._INMAIL_STEP_IDX.get(st.linkedin_current_step or "message_1", 1))
            adv = sequence_engine.after_send(db, st, st.linkedin_current_step or "message_1")
            return {"action": "reply_sent" if _is_reply else "message_sent",
                    "verified": verified, **adv}

        if job.job_type == "send_inmail":
            res = browser.do_send_inmail(page, st.linkedin_profile_url or "", payload.get("subject") or "", payload.get("body") or "", s)
            if res.get("no_credit"):
                st.linkedin_inmail_status = "skipped_no_credit"
                # Flip the ACCOUNT-level credit flag so the UI can show a
                # "credits exhausted" banner. Same signal for every future
                # InMail attempt until either an admin tops up + we detect
                # a successful send (which resets to 'ok' below).
                if acct.linkedin_inmail_credits_status != "exhausted":
                    acct.linkedin_inmail_credits_status = "exhausted"
                    acct.linkedin_inmail_credits_flagged_at = _utcnow()
                    logger.warning(
                        "inmail: account %s credits EXHAUSTED — flagged for UI banner (email=%s)",
                        acct.id, acct.linkedin_email,
                    )
                db.commit()
                record_event(db, workspace_id=acct.workspace_id, lead_id=job.lead_id, campaign_id=job.campaign_id, linkedin_account_id=acct.id, event_type="inmail_skipped_no_credit")
                # We're on the InMail branch (not connected) with no InMail
                # credit: there is no way to reach this person, and every later
                # step on this branch would hit the same wall. Stop with a
                # reason rather than returning bare — a bare return left the
                # lead active with next_action_at NULL, i.e. stranded forever.
                logger.info("inmail: lead %s unreachable (no InMail credit) — stopping", job.lead_id)
                return {"action": "inmail_skipped_no_credit",
                        **sequence_engine.stop_lead(db, st, reason="no_inmail_credit")}
            if not res.get("ok"):
                raise RuntimeError(f"inmail failed: {res.get('detail')}")
            _apply_identity(st, browser.capture_profile_identity(page))  # self-heal
            verified = browser.verify_inmail_success(page)  # confirm it sent
            st.linkedin_inmail_status = "sent"
            st.linkedin_inmail_sent_at = _utcnow()
            st.updated_at = _utcnow()
            # Success means credits ARE available now — clear any stale
            # "exhausted" flag so the UI banner disappears.
            if acct.linkedin_inmail_credits_status == "exhausted":
                acct.linkedin_inmail_credits_status = "ok"
                acct.linkedin_inmail_credits_flagged_at = None
                logger.info(
                    "inmail: account %s InMail succeeded — clearing exhausted flag",
                    acct.id,
                )
            db.commit()
            _bump_count(db, s, job.job_type)
            record_event(db, workspace_id=acct.workspace_id, lead_id=job.lead_id, campaign_id=job.campaign_id, linkedin_account_id=acct.id, event_type="inmail_sent", metadata={"verified": verified})
            scheduler.mark_content_sent(db, workspace_id=acct.workspace_id, lead_id=job.lead_id, variant="inmail",
                                        step=scheduler._INMAIL_STEP_IDX.get(st.linkedin_current_step or "inmail_1", 0))
            adv = sequence_engine.after_send(db, st, st.linkedin_current_step or "inmail_1")
            return {"action": "inmail_sent", "verified": verified, **adv}

        # ── Phase-3 detection + branching ───────────────────────────────────
        if job.job_type == "check_connection_status":
            ps = browser.read_profile_state(page, st.linkedin_profile_url or "", s)
            _apply_identity(st, ps.get("identity"))  # self-heal
            st.linkedin_state_confidence = ps.get("confidence", 0)
            acct.last_successful_action_at = _utcnow()
            db.commit()
            # Don't flip status on an ambiguous read — throw so it retries later.
            if ps["confidence"] < 50:
                raise RuntimeError(f"acceptance read ambiguous (conf={ps['confidence']}) — will retry")
            # CONNECTED → accepted ; PENDING → still pending ; NOT_CONNECTED → the
            # invite is gone (declined/withdrawn/expired), NOT pending.
            result = {"CONNECTED": "accepted", "PENDING": "pending", "NOT_CONNECTED": "declined"}.get(ps["state"], "pending")
            return {"action": "acceptance_checked", "state": ps["state"], "confidence": ps["confidence"],
                    **sequence_engine.on_acceptance_result(db, st, result)}

        if job.job_type == "check_reply_status":
            # Use read_reply_thread (post-merge API) to also capture the reply
            # body — we archive it in nexus_linkedin_messages so the
            # conversation view + future follow-up generator can read the
            # prospect's words.
            # Pass self_name so authorship is decided by "who is us" rather
            # than a positional heuristic that fails when the prospect
            # messaged us BEFORE our outreach.
            reply = browser.read_reply_thread(
                page, st.linkedin_profile_url or "", s,
                self_name=(acct.linkedin_display_name or None),
            )
            acct.last_successful_action_at = _utcnow()
            db.commit()
            return {
                "action": "reply_checked",
                **sequence_engine.on_reply_result(
                    db, st, reply["replied"],
                    reply_body=reply.get("last_body"),
                    sender_name=None,  # read_reply_thread doesn't return sender name
                ),
            }

        raise ValueError(f"unhandled job_type: {job.job_type}")


# ── SQS event-source entry ────────────────────────────────────────────────────
def lambda_handler(event, context=None):
    """SQS-triggered. Each record body = {"job_id": int}. Uses SQS partial-batch
    responses: a record whose job raises is reported in `batchItemFailures` so
    ONLY that message is redelivered (→ DLQ after maxReceiveCount), not the whole
    batch. (Enable 'Report batch item failures' on the event-source mapping.)"""
    from core.database import SessionLocal

    records = (event or {}).get("Records", [])
    logger.info("worker: SQS batch received — %d record(s)", len(records))
    failures = []
    processed = 0
    for rec in records:
        msg_id = rec.get("messageId")
        try:
            body = json.loads(rec.get("body") or "{}")
            job_id = body.get("job_id")
            if job_id is None:
                logger.warning("worker: SQS record %s had no job_id in body — skipping", msg_id)
                continue
            db = SessionLocal()
            try:
                handle_job(db, int(job_id))
                processed += 1
            finally:
                db.close()
        except Exception:  # noqa: BLE001 — failed job → redeliver just this message
            logger.exception("worker: SQS record %s failed — will be redelivered", msg_id)
            if msg_id:
                failures.append({"itemIdentifier": msg_id})
    logger.info("worker: SQS batch done — %d processed, %d failed (redelivering)", processed, len(failures))
    return {"batchItemFailures": failures, "processed": processed}
