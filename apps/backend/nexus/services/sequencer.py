"""Sequence tick processor.

Called by `/nexus/scheduler/sequencer/tick` (EventBridge -> pinger ->
this endpoint). Defensive against missing Phase 3 tables: if the
cross-phase tables aren't there yet the worker silently skips work and
returns zero counts so the same scheduler can boot pre-merge without
errors.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

log = logging.getLogger("nexus.sequencer")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def compute_step_schedule(anchor: datetime, steps: List[Dict[str, Any]]) -> Dict[str, str]:
    """Fixed, cumulative send dates for every step, decided once at
    enrollment (email_flow_plan_3.md §3a) — `anchor` is step 0's own
    intended send time (whatever the caller is about to set
    next_action_at to), not necessarily "now": manual enrollment can
    schedule a future start_at, so this must anchor on that, not
    datetime.utcnow().

    Each step's delay_days is relative to the PRECEDING step (matches
    existing usage throughout this file, e.g. `now + timedelta(days=
    steps[current_step]["delay_days"])` after a send) — so a step's
    offset from the anchor is the running sum of every delay up to and
    including it: [0, 3, 3, 3] -> day 0, day 3, day 6, day 9.

    Returns {"0": iso_str, "1": iso_str, ...} — string keys because this
    is stored as JSONB and JSON object keys are always strings; callers
    index it with str(step_order).
    """
    schedule: Dict[str, str] = {}
    running = anchor
    for i, step in enumerate(steps or []):
        if i > 0:
            running = running + timedelta(days=int(step.get("delay_days", 1) or 0))
        schedule[str(step.get("order", i))] = running.isoformat()
    return schedule


def _scheduled_next_action_at(
    ls, step_order: int, steps: List[Dict[str, Any]], now: datetime
) -> datetime:
    """The fixed date for `step_order` from ls.step_schedule (set once at
    enrollment) — falls back to the old rolling `now + delay_days`
    computation only for rows enrolled before step_schedule existed
    (email_flow_plan_3.md §3a). Sending late never pushes this later steps'
    dates around; it only affects how late THIS step's own send was.
    """
    schedule = getattr(ls, "step_schedule", None) or {}
    raw = schedule.get(str(step_order))
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    delay = int(steps[step_order].get("delay_days", 1) or 0) if step_order < len(steps) else 1
    return now + timedelta(days=delay)


def _table_exists(db: Session, name: str) -> bool:
    try:
        row = db.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = :n LIMIT 1"
            ),
            {"n": name},
        ).first()
        return row is not None
    except Exception:
        return False


def _is_suppressed(db: Session, workspace_id: int, email: str) -> bool:
    if not email:
        return False
    try:
        from nexus.models_phase4 import NexusSuppressionList

        row = (
            db.query(NexusSuppressionList)
            .filter(
                NexusSuppressionList.workspace_id == workspace_id,
                NexusSuppressionList.email_lower == email.lower(),
            )
            .first()
        )
        return row is not None
    except Exception:
        log.exception("nexus.sequencer: suppression check failed")
        return False


def _pick_conversation_account(db: Session, workspace_id: int):
    """Return an active sending account with daily headroom, or None.

    Lazy-bootstrap: if a workspace has NO conversation account configured
    and `NEXUS_DEFAULT_FROM_EMAIL` is set in env, create one on the fly
    using Resend as the provider. This unblocks first-time launches —
    the user no longer has to manually seed a row before any email can
    fire. Users can later override via /nexus/conversation-accounts/setup
    or directly in the DB.
    """
    import os

    try:
        from nexus.models_phase4 import NexusConversationAccount

        rows = (
            db.query(NexusConversationAccount)
            .filter(
                NexusConversationAccount.workspace_id == workspace_id,
                NexusConversationAccount.status == "active",
            )
            .all()
        )
        # Prefer a CONNECTED OAuth mailbox (Outlook/Gmail with a stored
        # refresh token) over a plain Resend default, so outreach goes out
        # from the user's OWN mailbox whenever they've connected one. Falls
        # back to any other active account with daily headroom.
        def _rank(r):
            return 0 if (r.provider in ("outlook", "gmail") and r.refresh_token) else 1

        for r in sorted(rows, key=_rank):
            if (r.daily_send_count or 0) < (r.daily_send_limit or 0):
                return r

        # No active accounts (or all over daily cap). If the user has set
        # NEXUS_DEFAULT_FROM_EMAIL, lazy-create a default account so the
        # first launch can actually fire emails. Without that env var we
        # return None and the caller will punt the row 1h.
        if rows:
            return None  # accounts exist but all at-cap; daily reset job will handle
        default_email = os.getenv("NEXUS_DEFAULT_FROM_EMAIL", "").strip()
        if not default_email:
            return None
        try:
            daily_limit = int(os.getenv("NEXUS_DEFAULT_DAILY_LIMIT", "50"))
        except ValueError:
            daily_limit = 50
        new_account = NexusConversationAccount(
            workspace_id=workspace_id,
            email_address=default_email,
            daily_send_limit=daily_limit,
            daily_send_count=0,
            provider="resend",
            status="active",
        )
        db.add(new_account)
        try:
            db.commit()
            db.refresh(new_account)
            log.info(
                "nexus.sequencer: lazy-created conversation account ws=%s email=%s",
                workspace_id,
                default_email,
            )
            return new_account
        except Exception:
            # Most likely cause: another worker raced us and the unique
            # constraint on email_address fires. Re-query and return.
            db.rollback()
            existing = (
                db.query(NexusConversationAccount)
                .filter(NexusConversationAccount.email_address == default_email)
                .first()
            )
            return existing
    except Exception:
        log.exception("nexus.sequencer: pick conversation account failed")
        return None


def _brand_id_for_campaign(db: Session, campaign_id) -> Optional[int]:
    """Resolve the sender brand (card) a campaign belongs to: campaign →
    product → normalized URL + entity_type → nexus_sender_brands.id.

    Matching by the NORMALIZED URL (same key New Run uses to dedup products)
    means all duplicate product rows for one website collapse to one card.
    """
    if not campaign_id:
        return None
    try:
        row = db.execute(
            text(
                """
                SELECT p.source_url,
                       COALESCE(NULLIF(p.icp->>'entity_type',''),'product'),
                       c.workspace_id
                  FROM nexus_campaigns c
                  JOIN nexus_products p ON p.id = c.product_id
                 WHERE c.id = :c
                """
            ),
            {"c": int(campaign_id)},
        ).first()
        if not row:
            return None
        from nexus.services.url_norm import brand_key as _norm
        url_norm = _norm(row[0] or "")
        entity_type = row[1] or "product"
        ws_id = row[2]
        brow = db.execute(
            text(
                "SELECT id FROM nexus_sender_brands "
                "WHERE workspace_id = :w AND url_norm = :u AND entity_type = :e LIMIT 1"
            ),
            {"w": ws_id, "u": url_norm, "e": entity_type},
        ).first()
        return int(brow[0]) if brow else None
    except Exception:
        db.rollback()
        return None


def _connected_oauth_accounts(db: Session, workspace_id: int, brand_id=None):
    """Active OAuth mailboxes (Outlook/Gmail) for a workspace, optionally
    filtered to those attached to a specific sender brand (card)."""
    from nexus.models_phase4 import NexusConversationAccount

    q = db.query(NexusConversationAccount).filter(
        NexusConversationAccount.workspace_id == workspace_id,
        NexusConversationAccount.status == "active",
        NexusConversationAccount.provider.in_(("outlook", "gmail")),
        NexusConversationAccount.refresh_token.isnot(None),
    )
    if brand_id is not None:
        q = q.filter(NexusConversationAccount.brand_id == brand_id)
    return q.all()


# Day boundary for the per-day send cap. IST (UTC+5:30) → the counter resets
# at midnight India time. (Timestamps are stored as UTC; we shift to IST only
# to decide which calendar day a send belongs to.)
_DAY_TZ_OFFSET = timedelta(hours=5, minutes=30)


def _next_daily_reset_utc(now: datetime) -> datetime:
    """Next UTC instant the per-mailbox daily send cap resets (IST midnight).

    A lead held because every mapped mailbox is at its cap must wake up AT
    this instant, not at an arbitrary `now + 1h`. The day's whole quota goes
    out in one short burst right after the reset; a +1h retry drifts to a
    different minute each day and can land after that burst has already
    drained the cap, holding the lead again — indefinitely, never by design,
    purely by bad luck of what minute it first got held on.
    """
    ist_now = now + _DAY_TZ_OFFSET
    ist_next_midnight = datetime(ist_now.year, ist_now.month, ist_now.day) + timedelta(days=1)
    return ist_next_midnight - _DAY_TZ_OFFSET


def _lazy_daily_reset(db: Session, account) -> None:
    """Per-CALENDAR-DAY cap (Way 1) that resets ITSELF — no external nightly
    job. If this mailbox's counter is from an earlier IST day, zero it on the
    first use of the new day. So Mon can send 50 and Tue (a new IST day) gets
    a fresh 50, even a few hours later."""
    if account is None:
        return
    today_ist = (datetime.utcnow() + _DAY_TZ_OFFSET).date()
    lr = getattr(account, "last_reset_at", None)
    lr_ist_date = (
        (lr + _DAY_TZ_OFFSET).date()
        if (lr is not None and hasattr(lr, "date"))
        else None
    )
    if lr_ist_date is None or lr_ist_date < today_ist:
        account.daily_send_count = 0
        account.last_reset_at = datetime.utcnow()  # stored as UTC
        try:
            db.commit()
        except Exception:
            db.rollback()


def _pick_rotation_account(db: Session, workspace_id: int, brand_id):
    """Least-busy mailbox attached to this brand that's under its per-day cap.
    None when none have headroom (or the brand has no mailbox)."""
    # No resolved sender brand for the campaign → HOLD. We must NEVER fall back
    # to "any workspace mailbox" (that would send a product's outreach from an
    # unrelated brand's mailbox). A None brand_id would skip the brand filter in
    # _connected_oauth_accounts, so guard it explicitly here.
    if brand_id is None:
        return None
    mapped = _connected_oauth_accounts(db, workspace_id, brand_id)
    if not mapped:
        return None
    for m in mapped:
        _lazy_daily_reset(db, m)  # fresh count if it's a new calendar day
    available = [
        m for m in mapped
        if (m.daily_send_count or 0) < (m.daily_send_limit or 20)
    ]
    if not available:
        return None
    # Fewest sent today first → even spread; id as a stable tie-break.
    available.sort(key=lambda m: ((m.daily_send_count or 0), m.id))
    return available[0]


def _assign_or_get_account(db: Session, ls, brand_id):
    """Return the sending mailbox for THIS lead, keeping it sticky.

    - Assigned already → reuse it. If that mailbox is DISCONNECTED or at its
      daily cap → return None (HOLD the lead). We never switch a lead to a
      different address mid-thread; it waits and resumes from the SAME mailbox
      once it reconnects / its cap resets.
    - First send → rotate across the mailboxes attached to the campaign's
      sender brand (card), pin the chosen one to the lead.
    - If other cards have mailboxes but this brand has none/all-capped → None
      (defer; don't borrow another brand's mailbox).
    - If the brand has NO mailbox connected → HOLD the lead (it waits and
      resumes once a mailbox is connected). We never send from a shared
      address (onboarding@resend.dev). The old Resend fallback is opt-in only
      (NEXUS_ALLOW_RESEND_FALLBACK="true") for local dev / single-sender setups.
    """
    from nexus.models_phase4 import NexusConversationAccount

    assigned_id = getattr(ls, "conversation_account_id", None)
    if assigned_id:
        acct = (
            db.query(NexusConversationAccount)
            .filter(NexusConversationAccount.id == assigned_id)
            .first()
        )
        if acct is not None:
            # Keep the lead on its ONE assigned mailbox for the whole thread.
            # Per-calendar-day cap with self-reset (fresh count on a new day).
            _lazy_daily_reset(db, acct)
            if (
                (acct.status or "") == "active"
                and (acct.daily_send_count or 0) < (acct.daily_send_limit or 20)
            ):
                return acct
            # Mailbox is DISCONNECTED or at its daily cap → HOLD this lead
            # (wait). We never switch a lead to a different address mid-thread;
            # it resumes from the SAME mailbox once it reconnects / its cap
            # resets. (The disconnect endpoint keeps the row, so the same
            # mailbox can come back and the lead continues cleanly.)
            return None
        # Row is GONE (truly deleted, not just disconnected — rare, since
        # disconnect keeps the row). It can never reconnect, so fall through
        # and re-assign rather than stranding the lead forever.

    acct = _pick_rotation_account(db, ls.workspace_id, brand_id)
    if acct is None:
        # No mailbox for this brand → HOLD the lead. We do NOT fall back to a
        # shared sender (onboarding@resend.dev): that hurts deliverability and
        # isn't the customer's own mailbox. The lead waits until a mailbox is
        # connected to this brand, then resumes automatically.
        #
        # The legacy Resend fallback is opt-in only (NEXUS_ALLOW_RESEND_FALLBACK
        # = "true") and only when ZERO OAuth mailboxes exist anywhere — kept
        # for local dev / older single-sender setups.
        import os as _os
        allow_resend = (
            _os.getenv("NEXUS_ALLOW_RESEND_FALLBACK", "false").strip().lower() == "true"
        )
        if allow_resend and not _connected_oauth_accounts(db, ls.workspace_id, None):
            acct = _pick_conversation_account(db, ls.workspace_id)
        else:
            return None

    if acct is not None and getattr(ls, "conversation_account_id", None) != acct.id:
        ls.conversation_account_id = acct.id
        try:
            db.commit()
        except Exception:
            db.rollback()
    return acct


def _hold_reason_and_retry(db: Session, ls, brand_id, now: datetime):
    """When `_assign_or_get_account` returns None, work out WHY so the retry
    cadence matches the actual blocker instead of a one-size-fits-all +1h:

      - assigned mailbox DISCONNECTED → hourly recheck; nothing about the
        send cap changes that, an admin has to reconnect it.
      - assigned mailbox (or every mailbox on rotation) is at TODAY'S CAP →
        wait for the exact reset instant (see `_next_daily_reset_utc`), not
        a same-hour retry that can drift past the day's brief send burst.
      - brand has NO mailbox connected at all → hourly recheck.

    Mirrors the branches in `_assign_or_get_account` exactly — this is a
    read-only re-derivation of the same decision, not a new policy.
    Returns (last_error: str, next_action_at: datetime).
    """
    from nexus.models_phase4 import NexusConversationAccount

    assigned_id = getattr(ls, "conversation_account_id", None)
    if assigned_id:
        acct = (
            db.query(NexusConversationAccount)
            .filter(NexusConversationAccount.id == assigned_id)
            .first()
        )
        if acct is not None:
            if (acct.status or "") != "active":
                return (
                    "assigned mailbox disconnected — holding (content ready)",
                    now + timedelta(hours=1),
                )
            return (
                "assigned mailbox at daily send cap — holding until reset (content ready)",
                _next_daily_reset_utc(now),
            )

    if _connected_oauth_accounts(db, ls.workspace_id, brand_id):
        return (
            "all mapped mailboxes at daily send cap — holding until reset (content ready)",
            _next_daily_reset_utc(now),
        )
    return (
        "no mapped mailbox for this product — holding (content ready)",
        now + timedelta(hours=1),
    )


_STEP_KIND = {0: "initial", 1: "followup_1", 2: "followup_2", 3: "closing"}


def _ensure_lead_emails(db, ls, lead_dict, sender_ctx, enrichment):
    """Generate the lead's 4-email set ONCE and persist one row per cadence
    step in nexus_lead_emails. Returns {step: NexusLeadEmail}.

    Idempotent: if rows already exist (this lead was processed on an earlier
    tick) they're returned as-is — no re-generation. Safe under concurrent
    ticks via the unique (lead_sequence_id, step) constraint.
    """
    from nexus.models_phase4 import NexusLeadEmail

    existing = (
        db.query(NexusLeadEmail)
        .filter(NexusLeadEmail.lead_sequence_id == ls.id)
        .all()
    )
    if existing:
        return {r.step: r for r in existing}

    from nexus.services.outreach_template import generate_template_content

    c = generate_template_content(lead_dict, sender_ctx, enrichment)
    opener_text = c.get("personalized_opener") or ""
    plan = [
        (0, c.get("subject"), c.get("intro_body"), c.get("real_result")),
        (1, c.get("followup_subject"), c.get("followup_body"), None),
        (2, c.get("followup2_subject"), c.get("followup2_body"), None),
        (3, c.get("closing_subject"), c.get("closing_body"), None),
    ]
    rows = [
        NexusLeadEmail(
            workspace_id=ls.workspace_id,
            campaign_id=ls.campaign_id,
            lead_id=ls.lead_id,
            lead_sequence_id=ls.id,
            step=step,
            kind=_STEP_KIND.get(step),
            subject=(subj or ""),
            body=(body or ""),
            real_result=rr,
            # Per-lead personalized opener — only on the initial email.
            opener=(opener_text if step == 0 else None),
            status="pending",
        )
        for step, subj, body, rr in plan
    ]
    db.add_all(rows)
    try:
        db.commit()
        return {r.step: r for r in rows}
    except Exception:
        # Most likely a concurrent tick already inserted them — re-read.
        db.rollback()
        again = (
            db.query(NexusLeadEmail)
            .filter(NexusLeadEmail.lead_sequence_id == ls.id)
            .all()
        )
        return {r.step: r for r in again}


def _latest_inbound_message_for_sequence(db, lead_sequence_id):
    """Most recent INBOUND (lead-authored, not our own outbound ack) message
    on this lead's thread. Used by the send-time freshness guard in
    process_due_sequences. Returns None if Phase 5 (inbox) tables are absent
    or no reply exists yet — never raises."""
    if not _table_exists(db, "nexus_inbound_threads") or not _table_exists(db, "nexus_inbound_messages"):
        return None
    try:
        from nexus.models_phase5 import InboundThread, InboundMessage

        return (
            db.query(InboundMessage)
            .join(InboundThread, InboundThread.id == InboundMessage.thread_id)
            .filter(
                InboundThread.lead_sequence_id == lead_sequence_id,
                InboundMessage.direction == "inbound",
            )
            .order_by(InboundMessage.received_at.desc())
            .first()
        )
    except Exception:
        log.exception("nexus.sequencer: latest inbound message lookup failed (non-fatal)")
        return None


def regenerate_pending_followups(db, ls, inbound_message, steps=None):
    """Reply-aware regeneration of a lead's pending (not-yet-sent) follow-up
    steps, grounded in an inbound reply.

    Called from two places:
      - eagerly, right after a reply is ingested (routers/inbound.py),
        targeting ALL currently-pending steps so the Content tab reflects
        the conversation immediately.
      - as a send-time freshness guard (process_due_sequences, below),
        targeting just the ONE step about to fire, so a later reply in the
        same thread is picked up even if it arrived after the eager pass.

    Idempotent per-(step, message): a step already regenerated FROM this
    exact inbound message is skipped, but one regenerated from an OLDER
    message (or never regenerated) is eligible — see
    NexusLeadEmail.regenerated_from_message_id. This is deliberately NOT a
    one-time-ever cap, so content stays fresh across multiple replies in
    the same thread instead of going stale after the first one.

    No-ops (returns None) if there's nothing pending, or if generation
    fails — a failed regeneration must never touch existing content or
    block the caller (mirrors the enrollment-time generation's
    fail-soft posture, just without a fallback: leaving the existing
    content alone IS the safe fallback here).
    """
    from nexus.models_phase4 import NexusLeadEmail

    message_id = getattr(inbound_message, "id", None)
    if message_id is None:
        return None

    # `ls.current_step` is the NEXT step to send (process_due_sequences reads
    # lead_emails.get(ls.current_step) when a row comes due) — so "pending"
    # steps are step >= current_step, not step > current_step. Step 0 is
    # naturally excluded once a reply exists, since current_step is always
    # >= 1 by the time anything could have been replied to.
    q = db.query(NexusLeadEmail).filter(
        NexusLeadEmail.lead_sequence_id == ls.id,
        NexusLeadEmail.step >= (ls.current_step or 0),
    )
    if steps is not None:
        q = q.filter(NexusLeadEmail.step.in_(list(steps)))
    targets = [r for r in q.all() if r.regenerated_from_message_id != message_id]
    if not targets:
        return None

    lead_dict = _load_lead(db, ls.lead_id)
    if not lead_dict:
        return None
    product = _load_product(db, ls.workspace_id, campaign_id=ls.campaign_id) or {}
    sender_ctx = _build_sender_ctx_for_product(db, product)
    enrichment = _li_company_background(db, lead_dict.get("company_domain"))

    intent = (getattr(inbound_message, "intent", None) or "").upper()
    return_date = None
    if intent == "OUT_OF_OFFICE":
        from nexus.services.intent_classifier import (
            extract_ooo_return_date,
            extract_ooo_return_delay_days,
        )
        _ooo_body = getattr(inbound_message, "body_text", "") or ""
        _ooo_received = getattr(inbound_message, "received_at", None)
        # An explicit calendar date is exact, so it wins. Only when none is
        # stated do we fall back to a RELATIVE duration ("back in a week", "out
        # for 10 days"), anchoring the estimated return on when the reply
        # arrived so the next send still lands after they're likely back.
        return_date = extract_ooo_return_date(_ooo_body, _ooo_received)
        if return_date is None:
            _delay_days = extract_ooo_return_delay_days(_ooo_body)
            if _delay_days:
                _anchor = _ooo_received or _utcnow()
                return_date = _anchor.date() + timedelta(days=_delay_days)
    # The auto-reply guard chain (ms_mail_sync.py / inbound_sync.py) sends
    # InboundMessage.suggested_reply for essentially every non-suppressed
    # reply, not just OUT_OF_OFFICE/UNSUBSCRIBE — those two just bypass the
    # suppression/lead-status checks that other intents are still subject
    # to. So suggested_reply is a reasonable "what we already told them"
    # signal for any continuing intent, not only OOO.
    our_prior_reply = getattr(inbound_message, "suggested_reply", None)

    # Scenario 7 gap 2: the lead wants a demo but named NO specific time (a
    # stated time would have classified DEMO_SCHEDULED and been handled as a
    # real booking instead). In that case the next follow-up should proactively
    # offer the booking link so they can pick a slot. As of the 7b fix the
    # booking link is delivered in the INSTANT reply (routers/inbound.py), not
    # the follow-up — so we no longer force it into the rewrite here. The
    # branded follow-up shell still carries the standard "Book a quick call"
    # CTA button, which is enough as a passive reminder.

    # Scenario 5 (phase 1 — reground): the person has LEFT the company on file, so
    # the remaining follow-ups must stop pitching that company as if they still
    # worked there. We hand the rewrite the former company name so it can
    # acknowledge the move and NOT sell to a place the person has left. (The
    # ask-new-company / pitch-new-company branches are a later phase; this phase
    # only stops the wrong-company pitch.)
    departed = intent == "LEFT_COMPANY"
    former_company = (lead_dict.get("company_name") or "").strip() if departed else None

    reply_context = {
        "intent": intent or None,
        "body_text": getattr(inbound_message, "body_text", "") or "",
        "received_at": getattr(inbound_message, "received_at", None),
        "our_prior_reply": our_prior_reply,
        "return_date": return_date,
        "departed": departed,
        "former_company": former_company,
    }

    from nexus.services.outreach_template import generate_reply_aware_followups
    result = generate_reply_aware_followups(lead_dict, sender_ctx, enrichment, reply_context)
    if not result:
        return None

    now = _utcnow()
    field_map = {
        1: ("followup_subject", "followup_body"),
        2: ("followup2_subject", "followup2_body"),
        3: ("closing_subject", "closing_body"),
    }
    updated = 0
    for row in targets:
        subj_key, body_key = field_map.get(row.step, (None, None))
        if not subj_key:
            continue
        new_subj, new_body = result.get(subj_key), result.get(body_key)
        if not new_subj or not new_body:
            continue
        row.subject = new_subj
        row.body = new_body
        row.regenerated_from_message_id = message_id
        row.regenerated_at = now
        updated += 1

    if updated == 0:
        db.rollback()
        return None

    # Timing (email_flow_plan_3.md §3b): the cadence is fixed at enrollment
    # and a reply does NOT move it — a QUESTION or INTERESTED reply gets
    # content regenerated above, but still sends on its originally
    # scheduled date. The one exception is a stated OUT_OF_OFFICE return
    # date, which pushes ONLY the next step (send the morning after they're
    # back) — later steps keep their original fixed dates, they don't
    # ripple forward with it.
    next_at = ls.next_action_at
    if return_date:
        next_at = datetime.combine(return_date, datetime.min.time()) + timedelta(days=1)
        ls.next_action_at = next_at
        orig_schedule = dict(ls.step_schedule or {})
        new_schedule = dict(orig_schedule)
        new_schedule[str(ls.current_step)] = next_at.isoformat()

        # A far-out return can push the next step PAST a later step's original
        # date (e.g. return 31 Jul -> next step 1 Aug, but Closing was 30 Jul).
        # The sequencer still sends in step order, so that later step wouldn't
        # fire early — but it would fire IMMEDIATELY after this one instead of
        # keeping its gap. So shift only the later steps that now fall at/before
        # the running date, preserving each one's ORIGINAL spacing; steps that
        # are already comfortably after the return keep their own date (no
        # unnecessary ripple, per email_flow_plan_3.md).
        def _parse(v):
            try:
                return datetime.fromisoformat(v) if v else None
            except (TypeError, ValueError):
                return None

        later = sorted(
            int(k) for k in orig_schedule
            if str(k).isdigit() and int(k) > ls.current_step
        )
        running = next_at
        prev_orig = _parse(orig_schedule.get(str(ls.current_step)))
        for step in later:
            orig = _parse(orig_schedule.get(str(step)))
            if orig is None:
                continue
            gap = (orig - prev_orig) if (prev_orig and orig > prev_orig) else timedelta(days=3)
            prev_orig = orig
            if orig <= running:
                running = running + gap
                new_schedule[str(step)] = running.isoformat()
            else:
                running = orig
        ls.step_schedule = new_schedule

    try:
        db.commit()
    except Exception:
        log.exception("nexus.sequencer: regenerate_pending_followups commit failed")
        db.rollback()
        return None

    log.info(
        "nexus.sequencer: regenerated %d pending step(s) for lead_sequence=%s from message=%s, next_action_at=%s",
        updated, ls.id, message_id, next_at,
    )
    return {
        "updated_steps": updated,
        "next_action_at": next_at,
        "referral_contacts": result.get("referral_contacts") or [],
    }


def handle_referral_mentions(db, lead_sequence_id, inbound_message):
    """Referral handling for intents that DON'T already get it via
    regenerate_pending_followups — i.e. halting intents (NOT_INTERESTED,
    UNSUBSCRIBE, DEMO_SCHEDULED). A reply naming an alternate contact is
    real signal regardless of whether the SENDER's own sequence is
    stopping, so this runs independently of _is_continuing (see
    routers/inbound.py::_apply_intent_side_effects).

    Gated by the cheap looks_like_referral() heuristic pre-filter so a
    plain "not interested" with no one else named never spends a Gemini
    call. Best-effort — never raises, never blocks the caller.
    """
    try:
        from nexus.services.reply_post_processor import looks_like_referral
        body_text = getattr(inbound_message, "body_text", "") or ""
        if not looks_like_referral(body_text):
            return
        from nexus.services.outreach_template import extract_referral_mentions
        contacts = extract_referral_mentions(body_text)
        if not contacts:
            return
        from nexus.models_phase4 import NexusLeadSequence
        ls = db.query(NexusLeadSequence).filter(NexusLeadSequence.id == lead_sequence_id).first()
        if ls is None:
            return
        create_referral_leads(db, ls, contacts, inbound_message.id)
    except Exception:
        log.exception("nexus.sequencer: handle_referral_mentions failed (non-fatal)")
        try:
            db.rollback()
        except Exception:
            pass


def create_referral_leads(db, source_ls, referral_contacts, source_message_id):
    """Create a new lead + enroll it in the normal active 4-step cadence for
    each referral contact named in a reply (e.g. "please contact
    mitch@maap.cc for Commercial & Ecomm").

    Per contact:
      - Enrolled exactly like any other lead — active, full cadence, no
        approval step.
      - Only the FIRST email is special: honestly grounded in the actual
        referral ("X mentioned you handle Y") rather than the standard
        cold-open framing. Follow-ups 1-3 use the normal generated cadence.
      - Reuses the SAME company-level enrichment as the source lead (same
        company_domain) rather than re-scraping — see _li_company_background.

    Best-effort per contact — one failure must not affect the others or
    bubble up into the reply-ingestion pipeline that called this.
    """
    for referral in referral_contacts or []:
        try:
            _create_referral_lead_if_new(db, source_ls, referral, source_message_id)
        except Exception:
            log.exception(
                "nexus.sequencer: referral lead creation failed (non-fatal) for %s",
                referral.get("email") if isinstance(referral, dict) else referral,
            )
            try:
                db.rollback()
            except Exception:
                pass


def _create_referral_lead_if_new(db, source_ls, referral, source_message_id):
    from nexus.models_phase3 import NexusGlobalLead
    from nexus.models_phase4 import NexusLeadSequence, NexusLeadEmail, NexusSequence

    email = (referral.get("email") or "").strip().lower()
    name = (referral.get("name") or "").strip()
    if not email:
        # No email to actually contact — nothing actionable to create.
        return None

    # Dedupe by email — nexus_global_leads.email is unique per workspace
    # already; don't create a duplicate lead for someone we already have.
    # Real signal though — flag it on THEIR existing sequence rather than
    # silently dropping it (this was always the plan; it just wasn't wired
    # until a live test caught the gap — see Test_mail.md scenario 15).
    existing = db.query(NexusGlobalLead).filter(NexusGlobalLead.email == email).first()
    if existing is not None:
        log.info(
            "nexus.sequencer: referral %s already exists as lead %s — flagging, not duplicating",
            email, existing.id,
        )
        try:
            existing_ls = (
                db.query(NexusLeadSequence)
                .filter(NexusLeadSequence.lead_id == existing.id)
                .order_by(NexusLeadSequence.id.desc())
                .first()
            )
            if existing_ls is not None:
                source_lead = _load_lead(db, source_ls.lead_id)
                who = (
                    f"{source_lead.get('first_name') or ''} {source_lead.get('last_name') or ''}".strip()
                    or "another lead"
                )
                company = source_lead.get("company_name") or "their company"
                existing_ls.needs_review = True
                existing_ls.needs_review_reason = (
                    f"Mentioned by {who} ({company}) in a reply on "
                    f"{_utcnow().date().isoformat()}"
                    + (f" — {referral.get('role_hint')}" if referral.get("role_hint") else "")
                )
                db.commit()
        except Exception:
            log.exception("nexus.sequencer: failed to flag existing lead %s from referral mention", existing.id)
            db.rollback()
        return None

    source_lead = _load_lead(db, source_ls.lead_id)
    product = _load_product(db, source_ls.workspace_id, campaign_id=source_ls.campaign_id) or {}
    first_name, _, last_name = name.partition(" ")

    # Title: prefer whatever the reply actually stated/implied about THIS
    # person (role_hint). If the reply instead frames them as taking over
    # the SENDER's own role/work (same_role_handoff — "he's handling my
    # work now"), inherit the sender's real title instead of a vague
    # "handling X's work" phrase — it's a more accurate, more useful title
    # than a paraphrase of the handoff itself. Never fabricate a title
    # when neither signal is present.
    if referral.get("same_role_handoff") and source_lead.get("title"):
        role = source_lead.get("title")
    else:
        role = referral.get("role_hint")

    new_lead = NexusGlobalLead(
        email=email,
        first_name=first_name or None,
        last_name=last_name or None,
        role=role,
        company_domain=source_lead.get("company_domain"),
        company_name=source_lead.get("company_name"),
        status="new",
        source="referral",
        source_lead_id=source_ls.lead_id,
        source_message_id=source_message_id,
    )
    db.add(new_lead)
    try:
        db.commit()
    except Exception:
        db.rollback()
        log.warning("nexus.sequencer: referral lead insert failed for %s (likely dup race)", email)
        return None

    # nexus_leads is the campaign-enrollment join table the GTM Journey lead
    # LIST is actually driven by (routers/journey.py::journey_leads() reads
    # "SELECT DISTINCT global_lead_id FROM nexus_leads WHERE campaign_id = ...").
    # Without this row the referral lead is invisible in the UI even though
    # everything else works.
    #
    # icp_score is inherited from the SOURCE lead's own score for this
    # campaign, not computed fresh — we have no independent ICP signal on a
    # referral lead (no title/seniority data of our own), but they were
    # vouched for by someone who already cleared the bar, so showing a hard
    # 0 (reads as "unqualified") would be misleading. Falls back to 0 only
    # if the source lead's own score can't be found.
    try:
        source_icp_score = db.execute(
            text(
                "SELECT icp_score FROM nexus_leads "
                "WHERE global_lead_id = :lid AND campaign_id = :camp "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"lid": source_ls.lead_id, "camp": source_ls.campaign_id},
        ).scalar() or 0
        db.execute(
            text(
                "INSERT INTO nexus_leads (workspace_id, campaign_id, product_id, global_lead_id, icp_score, created_at, updated_at) "
                "VALUES (:ws, :camp, :prod, :lid, :score, NOW(), NOW())"
            ),
            {
                "ws": source_ls.workspace_id,
                "camp": source_ls.campaign_id,
                "prod": product.get("id"),
                "lid": new_lead.id,
                "score": int(source_icp_score),
            },
        )
        db.commit()
    except Exception:
        log.exception("nexus.sequencer: nexus_leads enrollment insert failed for referral lead %s", new_lead.id)
        db.rollback()

    # Enrolled exactly like any other lead — active, full 4-step cadence,
    # no approval step. Only the FIRST email is special: it's grounded in
    # the actual referral (honest — "X mentioned you handle Y") rather than
    # the standard cold-open framing, since that's the one email where
    # pretending independent research would be both false and worse than
    # just saying what actually happened. Follow-ups 1-3 use the normal
    # generated cadence, same as every other lead from this point on.
    _start_at = _utcnow()
    _seq_def = db.query(NexusSequence).filter(NexusSequence.id == source_ls.sequence_id).first()
    new_seq = NexusLeadSequence(
        workspace_id=source_ls.workspace_id,
        campaign_id=source_ls.campaign_id,
        lead_id=new_lead.id,
        sequence_id=source_ls.sequence_id,
        current_step=0,
        next_action_at=_start_at,
        status="active",
        # Fixed cadence decided once, at enrollment (email_flow_plan_3.md).
        step_schedule=compute_step_schedule(_start_at, (_seq_def.steps if _seq_def else None) or []),
    )
    db.add(new_seq)
    db.commit()

    lead_dict = {
        "first_name": first_name or name or None,
        "last_name": last_name or None,
        "title": role,
        "company_name": new_lead.company_name,
        "company_domain": new_lead.company_domain,
        "email": email,
    }
    sender_ctx = _build_sender_ctx_for_product(db, product)
    enrichment = _li_company_background(db, new_lead.company_domain)
    _ensure_lead_emails(db, new_seq, lead_dict, sender_ctx, enrichment)

    try:
        from nexus.services.outreach_template import generate_referral_intro
        draft = generate_referral_intro(
            referral={"name": name or None, "email": email, "role_hint": role},
            source_lead=source_lead,
            sender=sender_ctx,
            enrichment=enrichment,
        )
        if draft:
            step0 = (
                db.query(NexusLeadEmail)
                .filter(NexusLeadEmail.lead_sequence_id == new_seq.id, NexusLeadEmail.step == 0)
                .first()
            )
            if step0 is not None:
                step0.subject = draft.get("subject") or step0.subject
                step0.body = draft.get("body") or step0.body
                db.commit()
    except Exception:
        log.exception("nexus.sequencer: referral intro generation failed for %s — keeping standard step-0 copy", email)

    log.info(
        "nexus.sequencer: created referral lead=%s sequence=%s from source lead=%s message=%s (pending review)",
        new_lead.id, new_seq.id, source_ls.lead_id, source_message_id,
    )
    return new_lead.id


def _brand_booking_url(db, prod):
    """The per-brand demo booking link (Connectors page) for this product's
    brand, or None. Matched by normalized URL + entity_type within the
    product's workspace — the same key that populates nexus_sender_brands.
    Best-effort: any failure returns None so outreach never breaks."""
    try:
        from nexus.services.url_norm import brand_key
        from nexus.models_phase4 import NexusSenderBrand

        prod = prod or {}
        prod_id = prod.get("id")
        src_url = (prod.get("source_url") or "").strip()
        et = (prod.get("entity_type") or "product").strip().lower()
        if not (prod_id and src_url):
            return None
        ws_row = db.execute(
            text("SELECT workspace_id FROM nexus_products WHERE id = :pid"),
            {"pid": prod_id},
        ).first()
        ws_id = ws_row[0] if ws_row else None
        if not ws_id:
            return None
        brand = (
            db.query(NexusSenderBrand)
            .filter(
                NexusSenderBrand.workspace_id == ws_id,
                NexusSenderBrand.url_norm == brand_key(src_url),
                NexusSenderBrand.entity_type == et,
            )
            .first()
        )
        val = (brand.booking_url or "").strip() if brand is not None else ""
        return val or None
    except Exception:
        log.exception("nexus.sequencer: per-brand booking_url lookup failed (non-fatal)")
        return None


def _build_sender_ctx_for_product(db, product):
    """Assemble the 3-layer sender context for a product.

    Mirrors the inline block in process_due_sequences (env defaults →
    product overrides → icp.brand overrides + Business-DNA colors), factored
    out so drafts can be generated AT QUALIFICATION TIME — not lazily at send
    time. The send loop keeps its own copy for rendering; this is only used to
    GENERATE the stored content, which the send loop then reuses verbatim.
    """
    from nexus.services.outreach_template import (
        _env_or_default, get_default_sender,
    )

    prod = product or {}
    prod_name = (prod.get("name") or "").strip()
    prod_url = (prod.get("source_url") or "").strip()
    clean_url = prod_url.replace("https://", "").replace("http://", "").rstrip("/")

    ctx = dict(get_default_sender())

    if prod_name:
        ctx["company_name"] = prod_name
    if clean_url:
        ctx["company_url"] = clean_url

    # Brand overrides, weakest first: product, then the campaign. The campaign
    # is what the user typed a representative for, so it must win — the same
    # brand exists as several product rows and a product-level rep would miss
    # every campaign attached to a different one.
    for brand_overrides in (prod.get("icp_brand") or {}, prod.get("campaign_brand") or {}):
        if not isinstance(brand_overrides, dict):
            continue
        for k in (
            "company_name", "company_url", "rep_name", "rep_title",
            "rep_email", "rep_phone", "cta_url", "cta_label",
        ):
            v = brand_overrides.get(k)
            if v:
                ctx[k] = str(v)

    # Per-brand demo booking link (set on the Connectors page) wins over the
    # env default and icp.brand.cta_url for the email CTA.
    _booking = _brand_booking_url(db, prod)
    if _booking:
        ctx["cta_url"] = _booking

    ctx["value_prop"] = prod.get("value_prop") or ""
    ctx["icp_pain"] = prod.get("icp_pain") or ""
    ctx["business_type"] = prod.get("entity_type") or "product"
    ctx["product_description"] = prod.get("product_description") or {}
    # Drives the f1 template's capability cards. Harmless to the incumbent
    # renderer, which ignores unknown sender keys.
    # Website-derived capabilities win over the stored key_benefits list: they
    # are generated from what the site actually says, so they read as selling
    # points rather than category labels. Falls back when absent.
    _f1s = prod.get("f1_sections") or {}
    _caps = _f1s.get("capabilities") if isinstance(_f1s, dict) else None
    ctx["key_benefits"] = _caps or prod.get("key_benefits") or []

    # Brand colours: Business DNA first, then colours previously extracted from
    # the product's own website (phase18 `nexus_products.brand_colors`).
    #
    # allow_extraction=False is deliberate — this function runs inside the send
    # loop, and a live extraction is a Microlink round-trip plus a Gemini vision
    # call. That belongs on the warm path (scripts/warm_brand_colors.py or
    # product onboarding), never in a tick.
    try:
        from nexus.services.brand_assets import resolve_brand_colors
        dna = resolve_brand_colors(db, prod, allow_extraction=False) or {}
        if dna.get("brand_color"):
            ctx["brand_color"] = dna["brand_color"]
        if dna.get("accent_color"):
            ctx["accent_color"] = dna["accent_color"]
        if dna.get("background_color"):
            ctx["background_color"] = dna["background_color"]
    except Exception:
        log.exception("nexus.sequencer: brand color fetch failed (non-fatal)")

    # Per-product email images (logo / hero), read from cache only — the
    # extraction path is scripts/warm_brand_images.py, never a send tick.
    try:
        from nexus.services.brand_assets import resolve_product_assets
        ctx["images"] = resolve_product_assets(db, prod) or {}
    except Exception:
        log.exception("nexus.sequencer: brand image fetch failed (non-fatal)")
        ctx["images"] = {}

    # Per-product f1 section copy (headings for the benefits / editorial / CTA
    # blocks). Cached on the product so it stays identical across a campaign.
    ctx["f1_sections"] = prod.get("f1_sections") or {}

    # Masthead "POWERED BY" credit. Taken from what the PRODUCT'S OWN SITE
    # displays, so Z-Ninth does not go out credited to NeuZenAI. An empty
    # string means the site shows no credit and the row is omitted; only a
    # product that was never analysed falls through to the env default.
    _attr = (prod.get("f1_sections") or {}).get("attribution")
    for _src in (prod.get("icp_brand") or {}, prod.get("campaign_brand") or {}):
        if isinstance(_src, dict) and _src.get("attribution") is not None:
            _attr = str(_src["attribution"])          # operator override wins
    if _attr is not None:
        ctx["attribution"] = _attr

    # Postal address for the CAN-SPAM footer line. Brand override first, then
    # the env default. Never a per-company table in code.
    _addr = ""
    for _src in (prod.get("campaign_brand") or {}, prod.get("icp_brand") or {}):
        if isinstance(_src, dict) and _src.get("postal_address"):
            _addr = str(_src["postal_address"])
    ctx["postal_address"] = _addr or _env_or_default("POSTAL_ADDRESS", "")
    return ctx


def prime_drafts_for_lead(db, ls):
    """Generate + store the 4 cadence email drafts for an enrolled lead NOW.

    Called at qualification/enrollment time so the GTM Content tab shows the
    drafts immediately — BEFORE the sequencer sends them. Sending still happens
    on the sequencer tick, paced by mailbox availability + the daily cap.

    Idempotent: delegates to _ensure_lead_emails, which no-ops if the rows
    already exist. Best-effort — never raises (a draft-gen failure must not
    break enrollment). Returns the number of stored draft rows.
    """
    try:
        if ls is None or not getattr(ls, "lead_id", None):
            return 0
        lead_dict = _load_lead(db, ls.lead_id)
        if not lead_dict:
            return 0
        product = _load_product(db, ls.workspace_id, campaign_id=ls.campaign_id) or {}
        # Auto-enrich the lead's company so the draft is grounded in REAL facts
        # (Apollo "About" overview + Google News). Cached with a 30-day TTL, so
        # it's ~1 Apollo credit per NEW company and a no-op for already-enriched
        # ones. Best-effort — never blocks draft generation.
        _domain = lead_dict.get("company_domain")
        if _domain:
            try:
                import asyncio as _aio
                from nexus.services.lead_enricher import enrich_domain
                _loop_running = True
                try:
                    _aio.get_running_loop()
                except RuntimeError:
                    _loop_running = False
                # Primary path is the background draft-priming thread (no loop):
                # run it inline. In a live event loop (rare inline call) skip —
                # the cache/send-time path still fills it later.
                if not _loop_running:
                    _aio.run(enrich_domain(db, _domain))
            except Exception:
                log.info("prime_drafts: auto-enrich skipped (non-fatal) for %s", _domain)
                try:
                    db.rollback()
                except Exception:
                    pass
        enrichment = _li_company_background(db, lead_dict.get("company_domain"))
        sender_ctx = _build_sender_ctx_for_product(db, product)
        rows = _ensure_lead_emails(db, ls, lead_dict, sender_ctx, enrichment)
        return len(rows or {})
    except Exception:
        log.exception(
            "nexus.sequencer: prime_drafts_for_lead failed (non-fatal) ls=%s",
            getattr(ls, "id", "?"),
        )
        try:
            db.rollback()
        except Exception:
            pass
        return 0


def prime_drafts_for_campaign(db, campaign_id, max_leads=1000):
    """Generate cadence drafts for EVERY enrolled lead in a campaign that lacks
    them — a SINGLE batched pass.

    Deliberately a separate step (not inside the per-lead enroll loop) so it
    runs ONCE, AFTER all qualified leads have been identified + enrolled — it
    never executes mid-scoring, so the qualification loop is never slowed by
    Gemini calls. Idempotent (the NOT EXISTS guard + _ensure_lead_emails skip
    leads that already have drafts). Best-effort per lead. Returns the number
    of leads freshly drafted.
    """
    from types import SimpleNamespace

    rows = db.execute(
        text(
            """
            SELECT ls.id, ls.workspace_id, ls.campaign_id, ls.lead_id
              FROM nexus_lead_sequences ls
             WHERE ls.campaign_id = :c
               AND ls.status = 'active'
               AND ls.lead_id IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM nexus_lead_emails le
                    WHERE le.lead_sequence_id = ls.id
               )
             ORDER BY ls.id
             LIMIT :cap
            """
        ),
        {"c": campaign_id, "cap": int(max_leads)},
    ).mappings().all()

    generated = 0
    for r in rows:
        ls = SimpleNamespace(
            id=r["id"], workspace_id=r["workspace_id"],
            campaign_id=r["campaign_id"], lead_id=r["lead_id"],
        )
        if prime_drafts_for_lead(db, ls):
            generated += 1
    log.info(
        "nexus.sequencer: primed drafts for %d/%d enrolled lead(s) in campaign %s",
        generated, len(rows), campaign_id,
    )
    return generated


def prime_drafts_for_campaign_bg(campaign_id):
    """BackgroundTask wrapper — fresh DB session, so an HTTP handler can fire
    draft generation without blocking its response on N Gemini calls."""
    from core.database import SessionLocal

    db = SessionLocal()
    try:
        generated = prime_drafts_for_campaign(db, campaign_id)
        # Signal completion so the UI can toast "emails generated". Merge a small
        # patch into the campaign's icp JSONB (drafts_ready / count / timestamp).
        import json as _json
        db.execute(
            text(
                "UPDATE nexus_campaigns "
                "SET icp = COALESCE(icp, '{}'::jsonb) || :patch::jsonb "
                "WHERE id = :c"
            ),
            {
                "c": campaign_id,
                "patch": _json.dumps({
                    "drafts_ready": True,
                    "drafts_count": int(generated or 0),
                    "drafts_ready_at": datetime.utcnow().isoformat() + "Z",
                }),
            },
        )
        db.commit()
    except Exception:
        log.exception("prime_drafts_for_campaign_bg failed for campaign %s", campaign_id)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def _step_content(by_step, step):
    """Build the render_email `gemini` dict for one step from a stored row.
    Returns None if the row is missing (render_email regenerates as fallback)."""
    from nexus.services.outreach_template import _STEP_FIELDS

    row = (by_step or {}).get(step)
    if row is None:
        return None
    subj_key, body_key = _STEP_FIELDS.get(step, _STEP_FIELDS[0])
    content = {subj_key: row.subject or "", body_key: row.body or ""}
    if row.real_result:
        content["real_result"] = row.real_result
    return content


def _build_brand_kit(prod, sender_ctx):
    """Assemble the brand kit for the rich renderer: product name as wordmark,
    colors from Business DNA (already on sender_ctx), CTA, and the signature.

    The signature carries the REPRESENTATIVE — the real person captured on the
    New Campaign wizard and stored in product.icp['brand'] — which
    `_build_sender_ctx_for_product` has already resolved onto `sender_ctx`.
    `email_blocks.signature()` falls back to "<Company> Team" only when there
    is genuinely no name, so an unconfigured product still renders sensibly.
    """
    prod = prod or {}
    company_name = sender_ctx.get("company_name") or prod.get("name") or ""
    bk = {
        "wordmark": company_name,            # name, NOT a DNA wordmark
        "company_name": company_name,
        "header_style": "dark",
        "site_url": prod.get("source_url") or sender_ctx.get("company_url") or "",
        # CTA = the BOOKING url (from .env NEXUS_DEFAULT_CTA_URL via sender_ctx,
        # or a per-product brand override). NEVER the company/product site.
        # TODO: source this per-user dynamically once booking config is added.
        "cta_url": sender_ctx.get("cta_url") or "",
        "cta_label": sender_ctx.get("cta_label") or "Book a quick call",
        "signature": {
            # rep_name was previously never passed and rep_title was pinned to
            # "", so every branded email signed "<Company> Team" even when the
            # product had a representative configured. Both now come through
            # from sender_ctx; absent values still hit the Team fallback.
            "rep_name": sender_ctx.get("rep_name") or "",
            "rep_title": sender_ctx.get("rep_title") or "",
            "rep_email": sender_ctx.get("rep_email") or "",
            "company_url": sender_ctx.get("company_url") or "",
        },
    }
    if sender_ctx.get("brand_color"):
        bk["brand_color"] = sender_ctx["brand_color"]
    if sender_ctx.get("accent_color"):
        bk["accent_color"] = sender_ctx["accent_color"]
    return bk


def _f1_enabled() -> bool:
    """Which outreach template the sequencer renders.

    DEFAULT: f1 (the branded template with per-product logo/hero/palette).

    Was opt-in via NEXUS_EMAIL_TEMPLATE=f1, which meant a deploy that hadn't
    set the var silently kept sending the old template — the exact failure we
    hit on 2026-08-05, where the var was added to .env but the already-running
    process never saw it. Defaulting ON removes that trap: the only way to get
    the legacy renderer now is to ask for it explicitly.

    Escape hatch: NEXUS_EMAIL_TEMPLATE=legacy (or 'classic'/'old') falls back
    to the incumbent branded renderer.
    """
    choice = (os.getenv("NEXUS_EMAIL_TEMPLATE") or "").strip().lower()
    return choice not in ("legacy", "classic", "old", "rich")


def _render_f1_step(prod, lead_dict, sender_ctx, email_row, step):
    """Render one cadence step with the f1 template.

    Returns {subject, html, text} or None to fall through to the incumbent
    renderer — a failure here must never break a send.
    """
    try:
        from nexus.services.outreach_template import _STEP_FIELDS
        from nexus.services.outreach_template_f1 import render_email_f1

        subj_key, body_key = _STEP_FIELDS.get(step, _STEP_FIELDS[0])
        subject = (getattr(email_row, "subject", "") or "").strip()
        body = (getattr(email_row, "body", "") or "").strip()

        # On the initial email the per-lead opener leads the body; later steps
        # have no opener. Mirrors _render_branded_step's composition.
        if step == 0:
            opener = (getattr(email_row, "opener", "") or "").strip()
            if opener:
                body = (opener + "\n\n" + body).strip() if body else opener

        gemini = {subj_key: subject, body_key: body}
        # The display headline is the strongest line we have for the initial
        # email; the subject already carries the personalised hook.
        if step == 0 and subject:
            gemini["headline"] = subject
        rr = (getattr(email_row, "real_result", "") or "").strip()
        if rr:
            gemini["real_result"] = rr

        artefact = render_email_f1(lead_dict, sender_ctx, gemini, step=step)
        if artefact and artefact.get("html"):
            return artefact
    except Exception:
        log.exception("nexus.sequencer: f1 render failed (falling back)")
    return None


def _render_branded_step(db, prod, lead_dict, sender_ctx, email_row, step):
    """Render ANY cadence step with the SAME branded shell (header, brand
    colors, signature, footer) so follow-ups + closing look as professional as
    the initial — just lighter content.

      * step 0  : full role variant (centerpiece) + personalized opener.
      * step 1-3: branded but light — the one personalized follow-up paragraph,
                  CTA on 1-2, no CTA on the closing (step 3).

    Returns {subject, html, text}, or None to fall back to the minimal
    renderer (so the send path can never break).
    """
    try:
        prod = prod or {}
        from nexus.services.email_blocks import render_rich_email
        brand_kit = _build_brand_kit(prod, sender_ctx)
        subject = (getattr(email_row, "subject", "") or "").strip()

        if step == 0:
            variant = None
            variant_key = None
            if prod.get("id"):
                from nexus.services.email_kit_generator import ensure_variant_for_lead
                variant, variant_key = ensure_variant_for_lead(
                    db,
                    prod.get("id"),
                    lead_dict.get("title"),
                    product_description=prod.get("product_description"),
                    entity_type=prod.get("entity_type") or "product",
                )
            opener = (getattr(email_row, "opener", "") or "").strip()
            # Render the per-lead intro_body too (between the opener and the
            # box) so the personalized body is never dropped from the initial.
            _intro = (getattr(email_row, "body", "") or "").strip()
            opener_full = (opener + "\n\n" + _intro).strip() if _intro else opener
            if variant:
                v = dict(variant)
                if subject:
                    v["subject"] = subject
                res = render_rich_email(brand_kit, v, lead_dict, step=0, opener=opener_full)
                if res:
                    if email_row is not None and variant_key:
                        try:
                            email_row.variant_key = variant_key
                        except Exception:
                            pass
                    return res
            # No role variant (e.g. product has no description) — still render
            # the BRANDED shell (neutral colors when DNA is absent) with the
            # opener + intro body, rather than dropping to the plain template.
            body = (getattr(email_row, "body", "") or "").strip()
            combined = (opener + "\n\n" + body).strip() if opener else body
            if not combined:
                return None
            lite = {
                "subject": subject,
                "blocks": [],
                "cta_label": brand_kit.get("cta_label") or "Book a quick call",
            }
            return render_rich_email(brand_kit, lite, lead_dict, step=0, opener=combined)

        # Follow-ups / closing: branded shell + the single personalized
        # paragraph (no centerpiece). render_rich_email hides the CTA on step 3.
        body = (getattr(email_row, "body", "") or "").strip()
        if not body:
            return None
        variant = {
            "subject": subject,
            "blocks": [],
            "cta_label": brand_kit.get("cta_label") or "Book a quick call",
        }
        return render_rich_email(brand_kit, variant, lead_dict, step=step, opener=body)
    except Exception:
        log.exception("nexus.sequencer: branded render failed — falling back")
        return None


def _load_lead(db: Session, lead_id: int) -> Dict[str, Any]:
    """Best-effort Phase 3 lead load.

    Returns a dict with the personalization fields we need. If the
    Phase 3 table isn't present yet, returns an empty-ish dict so the
    fallback template still produces something.
    """
    if not _table_exists(db, "nexus_global_leads"):
        return {}
    try:
        # Real columns on this table are `job_title` (added in phase7) and
        # `role` (original phase3). Neither is named `title`. Earlier
        # versions of this query SELECT-ed a non-existent `title` column
        # and silently caught the UndefinedColumn error, so the sequencer
        # always saw an empty lead. There is also no `enrichment` column
        # on this table — enrichment is computed per-tick now (cheap
        # Gemini call) and held only in memory.
        # Core columns (all exist since phase3/phase7). person_city/state/
        # country + company_domain drive location + company-background lookup.
        base = (
            "first_name, last_name, "
            "COALESCE(job_title, role) AS title, "
            "company_name, email, linkedin_url, company_domain, "
            "person_city, person_state, person_country"
        )
        # linkedin_headline is newer (2026-06-05) — select it but tolerate a
        # DB that hasn't run the ALTER yet (rollback + retry without it so the
        # core lead still loads and the send never breaks).
        try:
            row = db.execute(
                text(f"SELECT {base}, linkedin_headline FROM nexus_global_leads WHERE id = :id LIMIT 1"),
                {"id": lead_id},
            ).mappings().first()
        except Exception:
            db.rollback()
            row = db.execute(
                text(f"SELECT {base} FROM nexus_global_leads WHERE id = :id LIMIT 1"),
                {"id": lead_id},
            ).mappings().first()
        if not row:
            return {}
        return dict(row)
    except Exception:
        log.exception("nexus.sequencer: load lead failed")
        return {}


def _build_product_description(
    *,
    name: str,
    category: str,
    value_proposition: str,
    key_benefits: Any,
    industry_relevance: str,
    icp_obj: Any,
) -> Dict[str, Any]:
    """3-section grounded product description (what it is / what we do +
    capabilities / who we serve + industries), built fresh from the product's
    stored fields. Shared by the email path (_load_product) and the LinkedIn
    sweep so both channels prompt from the SAME knowledge base."""
    def _icp_values(key: str):
        v = icp_obj.get(key) if isinstance(icp_obj, dict) else None
        if isinstance(v, dict):
            v = v.get("values")
        return [str(x) for x in v if x] if isinstance(v, list) else []

    kb = key_benefits
    if isinstance(kb, str):
        try:
            import json as _json
            kb = _json.loads(kb)
        except Exception:
            kb = []
    if not isinstance(kb, list):
        kb = []
    _name = name or ""
    _cat = category or ""
    _vp = value_proposition or ""
    _ind_rel = industry_relevance or ""
    _inds = _icp_values("organization_industries") or ([_ind_rel] if _ind_rel else [])
    pd: Dict[str, Any] = {}
    _identity = (f"{_name} — {_cat}".strip(" —") if (_name or _cat) else "") or _vp
    if _identity:
        pd["what_the_company_is"] = _identity
    if _vp:
        pd["what_they_do"] = _vp
    if kb:
        pd["key_capabilities"] = [str(x) for x in kb if x][:12]
    if _ind_rel:
        pd["who_they_serve"] = _ind_rel
    if _inds:
        pd["target_industries"] = _inds[:12]
    return pd


# Prose fields where Gemini's stored description beats anything we can
# synthesize — it writes full sentences grounded in the actual scrape, while
# the fallback can only restate the 80-char value proposition.
_PD_PROSE_FIELDS = (
    "what_the_company_is", "what_they_do", "who_they_serve", "customer_profile",
)


def _merge_product_description(
    *, stored: Any, synthesized: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge the persisted Gemini description (nexus_products.product_description,
    added 2026-07-31) over the synthesized fallback.

    Prose fields: stored wins.

    key_capabilities: stored entries are used ONLY to fill an empty list, never
    to extend key_benefits. On spenzo.ai Gemini returned internal module names
    it had picked up from /terms ("Pulse", "Engine & Drivers") rather than the
    homepage capability blocks — appending those put four names a prospect has
    never heard into a block the email prompt labels "use ONLY this for product
    claims". key_benefits is the operator-visible, editable list and stays
    authoritative whenever it has anything in it.

    Returns the synthesized dict untouched when nothing is stored, which keeps
    legacy rows and the pasted-content path on their current behaviour.
    """
    if isinstance(stored, str):
        try:
            import json as _json

            stored = _json.loads(stored)
        except Exception:  # noqa: BLE001
            stored = None
    if not isinstance(stored, dict) or not any(stored.values()):
        return synthesized

    out = dict(synthesized)
    for f in _PD_PROSE_FIELDS:
        if stored.get(f):
            out[f] = stored[f]

    if not out.get("key_capabilities") and stored.get("key_capabilities"):
        out["key_capabilities"] = [
            c for c in stored["key_capabilities"] if str(c).strip()
        ][:12]

    for f in ("target_industries", "target_geographies"):
        if stored.get(f) and not out.get(f):
            out[f] = stored[f]
    return out


def _load_product(
    db: Session,
    workspace_id: int,
    campaign_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Load the product that owns this campaign.

    BUG-FIX 2026-05-22: Previously this returned the workspace's *latest*
    product regardless of which campaign the lead belonged to. That meant
    z-ninth leads got emails sourced from Spenzo's product metadata (and
    vice-versa) — every email always pulled from `ORDER BY id DESC LIMIT 1`.
    Now if a campaign_id is provided, we resolve `campaign.product_id` and
    load THAT product. The legacy fallback (latest product in workspace)
    only kicks in when campaign_id is missing.
    """
    if not _table_exists(db, "nexus_products"):
        return {}
    try:
        row = None
        if campaign_id:
            row = db.execute(
                text(
                    """
                    SELECT p.name, p.value_proposition, p.icp, p.source_url, p.id, p.user_id,
                           p.f1_sections,
                           p.key_benefits, p.category, p.industry_relevance, p.workspace_id,
                           p.product_description,
                           c.icp AS campaign_icp
                      FROM nexus_products p
                      JOIN nexus_campaigns c ON c.product_id = p.id
                     WHERE c.id = :cid AND p.workspace_id = :w
                     LIMIT 1
                    """
                ),
                {"cid": campaign_id, "w": workspace_id},
            ).mappings().first()
        if row is None:
            # Fallback: legacy behaviour, latest product in workspace.
            # Only fires for orphan lead_sequences with no campaign_id.
            row = db.execute(
                text(
                    """
                    SELECT name, value_proposition, icp, source_url, id, user_id,
                           f1_sections,
                           key_benefits, category, industry_relevance, workspace_id,
                           product_description
                      FROM nexus_products
                     WHERE workspace_id = :w
                     ORDER BY id DESC LIMIT 1
                    """
                ),
                {"w": workspace_id},
            ).mappings().first()
        if not row:
            return {}
        icp_obj = row.get("icp") or {}
        if isinstance(icp_obj, str):
            try:
                import json as _json

                icp_obj = _json.loads(icp_obj) or {}
            except Exception:
                icp_obj = {}
        pains = icp_obj.get("pain_points") if isinstance(icp_obj, dict) else None
        pain_str = ""
        if isinstance(pains, list) and pains:
            pain_str = "; ".join(str(p) for p in pains if p)[:500]
        # Optional per-product brand overrides under icp.brand JSONB key.
        # Keys recognised by sender_ctx layer 3:
        #   company_name, company_url, rep_name, rep_title, rep_email,
        #   rep_phone, cta_url, cta_label
        brand = icp_obj.get("brand") if isinstance(icp_obj, dict) else None
        if not isinstance(brand, dict):
            brand = {}
        entity_type = (
            (icp_obj.get("entity_type") if isinstance(icp_obj, dict) else "")
            or "product"
        ).strip().lower()
        if entity_type not in ("product", "service", "gcc"):
            entity_type = "product"

        # 3-section grounded description. Gemini's persisted version (added
        # 2026-07-31) carries the real capability prose; the synthesized one
        # built from the stored columns is the fallback for rows analyzed
        # before that column existed. Both are current — product_description
        # is rewritten by the same /analyze upsert that writes the columns.
        pd = _merge_product_description(
            stored=row.get("product_description"),
            synthesized=_build_product_description(
                name=row.get("name") or "",
                category=row.get("category") or "",
                value_proposition=row.get("value_proposition") or "",
                key_benefits=row.get("key_benefits"),
                industry_relevance=row.get("industry_relevance") or "",
                icp_obj=icp_obj,
            ),
        )

        # Brand overrides set on the CAMPAIGN itself (currently the
        # representative). These win over the product's, because the same brand
        # often exists as several product rows — Spenzo alone is five, across
        # workspaces and both .io/.ai — so a product-level rep silently misses
        # every campaign hanging off a different row. The campaign is what the
        # user actually named a rep for.
        _c_icp = row.get("campaign_icp") or {}
        if isinstance(_c_icp, str):
            try:
                import json as _cjson

                _c_icp = _cjson.loads(_c_icp) or {}
            except Exception:  # noqa: BLE001
                _c_icp = {}
        campaign_brand = _c_icp.get("brand") if isinstance(_c_icp, dict) else None
        if not isinstance(campaign_brand, dict):
            campaign_brand = {}

        return {
            "id": row.get("id"),
            "user_id": row.get("user_id"),
            "workspace_id": row.get("workspace_id") or workspace_id,
            "campaign_brand": campaign_brand,
            "name": row.get("name") or "",
            "value_prop": row.get("value_proposition") or "",
            "icp_pain": pain_str,
            "source_url": row.get("source_url") or "",
            "icp_brand": brand,
            # Operator-visible benefit list. Already SELECTed above for
            # _build_product_description; also surfaced here because the f1
            # template renders it as the capability cards. Without it that
            # whole section silently disappears.
            "key_benefits": row.get("key_benefits") or [],
            # Per-product f1 section copy (phase19). Empty until warmed;
            # the renderer then uses brand-neutral fallbacks.
            "f1_sections": row.get("f1_sections") or {},
            # 3-pillar grounded description for the email knowledge base.
            "product_description": pd,
            # product | service | gcc — drives the email tone (see
            # outreach_template business_type handling).
            "entity_type": entity_type,
        }
    except Exception:
        log.exception("nexus.sequencer: load product failed")
        return {}


def _cache_get(db: Session, lead_id: int, sequence_id: int, step: int):
    from nexus.models_phase4 import NexusPersonalizationCache

    return (
        db.query(NexusPersonalizationCache)
        .filter(
            NexusPersonalizationCache.lead_id == lead_id,
            NexusPersonalizationCache.sequence_id == sequence_id,
            NexusPersonalizationCache.step == step,
        )
        .first()
    )


def _cache_put(
    db: Session,
    lead_id: int,
    sequence_id: int,
    step: int,
    subject: str,
    body: str,
    model_used: str,
):
    from nexus.models_phase4 import NexusPersonalizationCache

    entry = NexusPersonalizationCache(
        lead_id=lead_id,
        sequence_id=sequence_id,
        step=step,
        subject=subject,
        body=body,
        model_used=model_used,
    )
    db.add(entry)


DEFAULT_MAX_ROWS_PER_TICK = 25
IDEMPOTENCY_WINDOW_HOURS = 6


def process_due_sequences(
    db: Session, max_rows: int = DEFAULT_MAX_ROWS_PER_TICK
) -> Dict[str, Any]:
    """Process every LeadSequence row that is due.

    Returns: {count, sent, skipped, errors, details}

    OUTBOUND DELIVERY: per-step Gemini render + Resend SMTP (our own
    scheduler owns the cadence + delivery).

    Safety guardrails (added 2026-05-22 after a runaway re-send incident):
      1. `max_rows` cap — bounds the blast radius of any single tick.
         Default 25; local-loop callers pass smaller values (10).
      2. Idempotency check — before sending, query nexus_touchpoints for
         a recent successful send to the same (lead_sequence_id, step).
         If one exists within IDEMPOTENCY_WINDOW_HOURS, we skip the
         Resend call AND advance the sequence state so the row doesn't
         keep getting re-claimed. Prevents duplicate emails after a
         crashed-before-commit run.
      3. Per-row commit — each row commits independently. A failure on
         row N doesn't roll back rows 1..N-1. The previous code buffered
         every row's writes into a single end-of-batch commit; one
         transient error sent the whole batch back into the queue.
      4. workspace_id / lead_id / campaign_id explicitly written on
         touchpoints. Previously these were NULL, which made analytics
         and follow-up sequencing miss the rows entirely.
    """
    result: Dict[str, Any] = {
        "count": 0,
        "sent": 0,
        "skipped": 0,
        "errors": 0,
        "details": [],
    }

    # Defensive: if our own tables aren't there yet, bail cleanly.
    if not _table_exists(db, "nexus_lead_sequences"):
        log.info("nexus.sequencer: nexus_lead_sequences absent — nothing to do")
        return result

    # Imports deferred so module import doesn't require migrations.
    from nexus.models_phase4 import (
        NexusLeadSequence,
        NexusSequence,
        NexusTouchpoint,
        NexusSendLog,
    )
    from nexus.services.resend_client import send_email

    now = _utcnow()
    lock_until = now + timedelta(minutes=5)
    cap = max(1, int(max_rows or DEFAULT_MAX_ROWS_PER_TICK))

    # Claim a batch atomically. SELECT ... FOR UPDATE SKIP LOCKED on Postgres
    # guarantees that two concurrent ticks (e.g. EventBridge double-fire, or a
    # warm Lambda + cold Lambda colliding) never pick the same row. We also
    # respect any prior unexpired lock so a stuck-in-progress row is not
    # double-picked.
    try:
        claimed_ids = [
            r[0]
            for r in db.execute(
                text(
                    """
                    UPDATE nexus_lead_sequences
                       SET locked_until = :lock_until
                     WHERE id IN (
                         SELECT id FROM nexus_lead_sequences
                          WHERE status = 'active'
                            AND next_action_at IS NOT NULL
                            AND next_action_at <= :now
                            AND (locked_until IS NULL OR locked_until <= :now)
                          -- Never-contacted leads (current_step = 0) go first,
                          -- ahead of any follow-up/closing step — a mailbox at
                          -- its daily cap must not let a lead sit at zero
                          -- touches indefinitely while later steps for OTHER
                          -- leads keep cutting in line ahead of it. Within each
                          -- of those two priority classes, oldest-enrolled
                          -- first: when the daily cap spills work to the next
                          -- day, the existing BACKLOG drains before
                          -- newly-generated leads ever start (enrolled_at, not
                          -- the hold-bumped next_action_at, decides priority).
                          ORDER BY (current_step = 0) DESC, enrolled_at ASC, next_action_at ASC
                          LIMIT :cap
                          FOR UPDATE SKIP LOCKED
                     )
                     RETURNING id
                    """
                ),
                {"now": now, "lock_until": lock_until, "cap": cap},
            ).fetchall()
        ]
        db.commit()
    except Exception:
        # SQLite / older Postgres without SKIP LOCKED — fall back to a
        # best-effort lock-by-update. Race window is much smaller than no lock.
        log.exception("nexus.sequencer: skip-locked claim failed, using fallback")
        db.rollback()
        rows = (
            db.query(NexusLeadSequence.id)
            .filter(
                NexusLeadSequence.status == "active",
                NexusLeadSequence.next_action_at != None,  # noqa: E711
                NexusLeadSequence.next_action_at <= now,
                (NexusLeadSequence.locked_until == None) | (NexusLeadSequence.locked_until <= now),  # noqa: E711
            )
            .order_by(
                (NexusLeadSequence.current_step == 0).desc(),
                NexusLeadSequence.enrolled_at.asc(),
                NexusLeadSequence.next_action_at.asc(),
            )
            .limit(cap)
            .all()
        )
        claimed_ids = [r[0] for r in rows]
        if claimed_ids:
            db.query(NexusLeadSequence).filter(
                NexusLeadSequence.id.in_(claimed_ids)
            ).update({NexusLeadSequence.locked_until: lock_until}, synchronize_session=False)
            db.commit()

    if not claimed_ids:
        return result

    due: List[NexusLeadSequence] = (
        db.query(NexusLeadSequence)
        .filter(NexusLeadSequence.id.in_(claimed_ids))
        .all()
    )
    result["count"] = len(due)

    for ls in due:
        try:
            # Load sequence definition first — we need its step list and
            # the step's channel to scope the per-lead idempotency guard.
            seq = db.query(NexusSequence).filter(NexusSequence.id == ls.sequence_id).first()
            if not seq:
                ls.status = "dead"
                ls.last_error = "sequence missing"
                db.commit()
                result["skipped"] += 1
                continue

            steps = seq.steps or []
            if ls.current_step >= len(steps):
                ls.status = "completed"
                ls.completed_at = now
                ls.next_action_at = None
                db.commit()
                result["skipped"] += 1
                continue

            step_def = steps[ls.current_step]
            step_channel = (step_def.get("channel") or "email").lower()

            # ---- IDEMPOTENCY GUARD ----------------------------------------
            # PRIOR (broken 2026-05-22): keyed on (lead_sequence_id, step).
            # Caught within-sequence dupes (crash-then-replay, two ticks
            # racing for the same row) but MISSED cross-sequence dupes —
            # a global lead enrolled into two campaigns produced two
            # distinct lead_sequence_id values, so each row's tick passed
            # its own guard and fired step 0. User-visible symptom: lead
            # received two "Initial Email" messages an hour apart.
            #
            # NOW: keyed on (global_lead_id, step, channel). The first
            # sequence to fire writes a touchpoint with that lead_id;
            # the second sequence's tick sees it and skips. The second
            # row's bookkeeping still advances (current_step += 1,
            # next_action_at) so it leaves the due queue and follows the
            # normal cadence for step 1+ — no infinite retry, just no
            # duplicate send at the same step within the window.
            # SECURITY/ISOLATION: scope the dedup to THIS workspace. Without the
            # workspace_id filter, the same global lead targeted by two DIFFERENT
            # workspaces would have one tenant's send suppress the other's (and
            # leak that another tenant already contacted the person). Within a
            # workspace this still dedups a lead enrolled in two campaigns.
            recent = db.execute(
                text(
                    """
                    SELECT 1 FROM nexus_touchpoints
                     WHERE lead_id = :lead_id
                       AND workspace_id = :ws
                       AND step = :step
                       AND channel = :ch
                       AND status = 'sent'
                       AND sent_at >= NOW() - INTERVAL ':hrs hours'
                     LIMIT 1
                    """.replace(":hrs", str(int(IDEMPOTENCY_WINDOW_HOURS)))
                ),
                {"lead_id": ls.lead_id, "ws": ls.workspace_id, "step": ls.current_step, "ch": step_channel},
            ).first()
            if recent:
                log.info(
                    "sequencer: skipping duplicate send lead=%s ls=%s step=%s ch=%s "
                    "(already sent within %dh)",
                    ls.lead_id, ls.id, ls.current_step, step_channel,
                    IDEMPOTENCY_WINDOW_HOURS,
                )
                ls.current_step += 1
                if ls.current_step >= len(steps):
                    ls.status = "completed"
                    ls.completed_at = now
                    ls.next_action_at = None
                else:
                    ls.next_action_at = _scheduled_next_action_at(ls, ls.current_step, steps, now)
                ls.locked_until = None
                ls.last_error = None
                db.commit()
                result["skipped"] += 1
                result["details"].append(
                    {"lead_sequence_id": ls.id, "step": ls.current_step - 1, "status": "skipped_idempotent"}
                )
                continue

            # ---- suppression ----------------------------------------------
            lead_dict = _load_lead(db, ls.lead_id) if ls.lead_id else {}
            recipient = lead_dict.get("email")
            if recipient and _is_suppressed(db, ls.workspace_id, recipient):
                ls.status = "dead"
                ls.last_error = "suppressed"
                result["skipped"] += 1
                continue

            # NOTE: the sending mailbox is picked AFTER the email content is
            # generated + stored below — so a lead whose product has no mailbox
            # yet still gets its 4 emails written (it just waits to send).

            # ---- personalization + branded-template render ----------------
            # Loads product + the lead's company-background enrichment, then
            # renders the stored draft via the full branded template
            # (services/outreach_template.py). Content is generated ONCE at
            # qualification time and reused here — no per-send re-generation.
            #
            # The personalization cache (_cache_get / _cache_put) is bypassed
            # here because its body column stores plain text — the template
            # produces a richer (subject, intro_body, real_result) tuple
            # that doesn't fit cleanly. The cache helpers are kept for any
            # future caller that still wants per-step caching; revisit
            # serialising the template payload into the cache row later.
            subject: str = ""
            body: str = ""           # plain-text version (logged on Touchpoint)
            html_body: str = ""      # rendered HTML body sent to Resend
            model_used: str = "gemini-3.1-flash-lite"  # matches outreach_template.py's hardcoded model

            # Pass campaign_id so we load THE CAMPAIGN'S product (z-ninth's
            # product for a z-ninth lead, Spenzo's for a Spenzo lead).
            # Previously this loaded the workspace's latest product
            # unconditionally — which is why every email defaulted to
            # whichever product was created most recently in the workspace.
            product = _load_product(db, ls.workspace_id, campaign_id=ls.campaign_id)
            enrichment = lead_dict.get("enrichment") or {}
            if isinstance(enrichment, str):
                try:
                    import json as _json

                    enrichment = _json.loads(enrichment)
                except Exception:
                    enrichment = {}

            # (Removed 2026-06-19) The lazy per-lead Gemini enricher
            # (lead_enrichment.enrich_lead) used to run here, but its output
            # was never consumed: the 4 emails are generated ONCE at
            # qualification time (prime_drafts_for_lead) and reused on send,
            # so _ensure_lead_emails below no-ops for already-drafted leads.
            # It only burned a Gemini call per tick. The REAL enrichment —
            # the lead's company background — is loaded below.

            # Real COMPANY BACKGROUND for the lead's own company (already
            # scraped during discovery into nexus_lead_enrichment, but unused
            # at send time until now). Grounds the personalized opener with
            # real facts. Best-effort; never blocks the send.
            _cdomain = lead_dict.get("company_domain")
            if _cdomain and _table_exists(db, "nexus_lead_enrichment"):
                try:
                    bg = db.execute(
                        text(
                            "SELECT meta_description, body_snippet, news_headlines "
                            "FROM nexus_lead_enrichment WHERE company_domain = :d LIMIT 1"
                        ),
                        {"d": _cdomain},
                    ).mappings().first()
                    if bg:
                        summary = bg.get("meta_description") or bg.get("body_snippet")
                        if summary:
                            enrichment = {**enrichment, "company_summary": str(summary)[:600]}
                        news = bg.get("news_headlines")
                        titles = []
                        if isinstance(news, list):
                            for n in news[:2]:
                                if isinstance(n, dict) and n.get("title"):
                                    titles.append(str(n["title"]))
                                elif isinstance(n, str):
                                    titles.append(n)
                        if titles:
                            enrichment = {**enrichment, "company_news": titles}
                except Exception:
                    log.exception("nexus.sequencer: company background lookup failed (non-fatal)")

            # Build the branded email. outreach_template handles the Gemini
            # call internally (subject + intro_body + real_result) and
            # renders the full Spenzo HTML template. On any failure it
            # falls back to its own deterministic copy so the send still
            # goes out.
            email_row = None  # the nexus_lead_emails row for this step (set below)
            account = None    # sending mailbox, picked after content is stored
            try:
                from nexus.services.outreach_template import (
                    get_default_sender,
                    render_email,
                )
                # Sender context — ONE builder, shared with draft priming.
                #
                # This used to be a ~60-line inline copy of
                # _build_sender_ctx_for_product. The two drifted: the copy never
                # learned to read the CAMPAIGN's brand overrides, so a
                # representative entered on a campaign was silently dropped and
                # every delivered email fell back to "<Company> Team" — while the
                # factored-out function (and therefore every test written against
                # it) reported the rep correctly. Collapsed to remove that class
                # of bug entirely: whatever generates drafts now also sends them.
                prod = product or {}
                sender_ctx = _build_sender_ctx_for_product(db, prod)

                # GENERATE + STORE the full 4-email set ONCE — this runs even
                # when NO mailbox is connected, so a held lead has its content
                # ready. Gemini runs once; later steps reuse the stored rows.
                lead_emails = _ensure_lead_emails(
                    db, ls, lead_dict, sender_ctx, enrichment
                )

                # Now pick the sending mailbox (rotation by brand/card). The 4
                # emails are already stored above — so if the brand has no
                # mailbox yet we simply HOLD this lead; it resumes (and sends
                # the stored content) once a mailbox is connected/mapped.
                _brand_id = _brand_id_for_campaign(db, ls.campaign_id)
                account = _assign_or_get_account(db, ls, _brand_id)
                if account is None:
                    ls.last_error, ls.next_action_at = _hold_reason_and_retry(
                        db, ls, _brand_id, now
                    )
                    result["skipped"] += 1
                    continue

                # rep_email from the sending mailbox. Stays here rather than in
                # the shared builder because it needs `account`, which is only
                # chosen above. The mailbox DISPLAY NAME is still ignored on
                # purpose — it is the operator's personal name, not the sender
                # identity for a customer's product; that comes from the
                # representative on the campaign/product.
                # (rep_email is kept for reply-to use; it is not shown in the body.)
                if getattr(account, "provider", "") in ("outlook", "gmail"):
                    if not sender_ctx.get("rep_email") and getattr(account, "email_address", ""):
                        sender_ctx["rep_email"] = account.email_address

                # Send-time freshness guard: if a reply has landed on this
                # thread that the current step's content wasn't (re)generated
                # from — including "never regenerated but a reply exists",
                # e.g. a NOT_NOW sequence whose next scheduled step predates a
                # later reply — regenerate just this ONE step inline before it
                # goes out. This is the safety net for the eager regeneration
                # in routers/inbound.py: it guarantees what actually sends
                # reflects the LATEST state of the conversation even if the
                # eager pass was skipped or a second reply arrived since.
                # Cheap on the common path (one indexed lookup); an LLM call
                # only fires when content is actually stale.
                try:
                    latest_reply = _latest_inbound_message_for_sequence(db, ls.id)
                    if latest_reply is not None:
                        _current_row = lead_emails.get(ls.current_step)
                        _already_fresh = (
                            _current_row is not None
                            and getattr(_current_row, "regenerated_from_message_id", None)
                            == latest_reply.id
                        )
                        if not _already_fresh:
                            regenerate_pending_followups(
                                db, ls, latest_reply, steps=[ls.current_step]
                            )
                except Exception:
                    log.exception(
                        "nexus.sequencer: send-time freshness guard failed (non-fatal)"
                    )

                email_row = lead_emails.get(ls.current_step)
                step_gemini = _step_content(lead_emails, ls.current_step)

                # Branded render for EVERY step (initial + follow-ups + closing)
                # so the whole cadence looks consistent + professional. Initial
                # carries the role-version centerpiece; follow-ups are the same
                # branded shell with one personalized paragraph. Falls back to
                # the minimal renderer on any error so the send NEVER breaks.
                # Which renderer produced this email is ALWAYS logged. The f1
                # path falls back to the legacy renderer on any error, and that
                # fallback used to be silent — an operator seeing the old
                # template had no way to tell whether the flag was off, the
                # process was stale, or f1 had thrown. Now every send says so.
                artefact = None
                _renderer = "legacy"
                if _f1_enabled():
                    artefact = _render_f1_step(
                        prod, lead_dict, sender_ctx, email_row, ls.current_step
                    )
                    if artefact is not None:
                        _renderer = "f1"
                    else:
                        log.warning(
                            "nexus.sequencer: f1 ENABLED but render returned None "
                            "— falling back to legacy for ls=%s step=%s product=%s",
                            ls.id, ls.current_step, prod.get("id"),
                        )
                else:
                    log.info(
                        "nexus.sequencer: f1 disabled via NEXUS_EMAIL_TEMPLATE=%r",
                        os.getenv("NEXUS_EMAIL_TEMPLATE"),
                    )
                if artefact is None:
                    artefact = _render_branded_step(
                        db, prod, lead_dict, sender_ctx, email_row, ls.current_step
                    )
                    if artefact is not None and _renderer == "legacy":
                        _renderer = "legacy(rich)"
                log.info(
                    "nexus.sequencer: rendered ls=%s step=%s with renderer=%s",
                    ls.id, ls.current_step, _renderer,
                )

                if artefact is None:
                    artefact = render_email(
                        lead=lead_dict,
                        sender=sender_ctx,
                        # Stored content for THIS step. None → render_email
                        # regenerates as a safety fallback (row missing).
                        gemini=step_gemini,
                        enrichment=enrichment,
                        # Render the email for THIS cadence step (0=initial,
                        # 1=follow-up 1, 2=follow-up 2, 3=close-up).
                        step=ls.current_step,
                    )
                subject = artefact["subject"]
                body = artefact["text"]
                html_body = artefact["html"]
            except Exception:
                # Final fallback: deterministic plain-paragraph email so
                # the send still fires. Bare <p> wrap mimics the old
                # pre-template behaviour. Touchpoint still logs body.
                log.exception(
                    "nexus.sequencer: outreach_template render failed — using plain wrap"
                )
                first = (lead_dict.get("first_name") or "there").strip()
                subject = f"Quick thought, {first}"
                body = (
                    f"Hi {first},\n\n"
                    "Wanted to send a quick note — happy to share more if it's relevant.\n"
                )
                html_body = (
                    "<p>" + body.replace("\n\n", "</p><p>").replace("\n", "<br/>") + "</p>"
                )
                model_used = "fallback/static"

            # Safety: if an error above left no mailbox resolved, hold the lead
            # (don't crash the send block). The content is already stored.
            # `_brand_id` may not exist here — the exception that left
            # `account` unset could have fired before that line ever ran —
            # so re-derive it fresh rather than trust the try block's copy.
            if account is None:
                ls.last_error, ls.next_action_at = _hold_reason_and_retry(
                    db, ls, _brand_id_for_campaign(db, ls.campaign_id), now
                )
                result["skipped"] += 1
                continue

            # ---- send -----------------------------------------------------
            # Provider routing (2026-06-04):
            #   provider == 'outlook' → send FROM the workspace's own
            #     connected Outlook mailbox via delegated Graph OAuth. This
            #     is the per-user mailbox-connection flow.
            #   otherwise → Resend fallback. We still send from the fixed
            #     NEXUS_DEFAULT_FROM_EMAIL (not account.email_address)
            #     because an arbitrary Resend sender would fail domain
            #     verification; the account row is only used for daily-cap
            #     tracking on this path.
            import os as _os

            send_result: Dict[str, Any] = {"ok": False}
            if not recipient:
                send_result = {
                    "ok": False,
                    "error": "lead has no email",
                    "id": None,
                }
            elif getattr(account, "provider", "") in ("outlook", "gmail"):
                # Send FROM the workspace's connected mailbox (Outlook or
                # Gmail). The connector handles token refresh + a 401 retry
                # internally; a "needs_reconnect" error means consent was
                # lost and the user must re-link the mailbox in Connectors.
                from nexus.services.connectors import get_connector
                _conn = get_connector(account.provider)
                _res = _conn.send_for_account(
                    db,
                    account,
                    to_email=recipient,
                    subject=subject,
                    body_html=html_body,
                    body_text=body,
                )
                send_result = {
                    "ok": bool(_res.get("ok")),
                    "id": None,
                    "provider": account.provider,
                    "error": _res.get("error"),
                }
            else:
                fixed_from = (
                    (_os.environ.get("NEXUS_DEFAULT_FROM_EMAIL") or "onboarding@resend.dev").strip()
                    or "onboarding@resend.dev"
                )
                send_result = send_email(
                    to=recipient,
                    from_=fixed_from,
                    subject=subject,
                    html=html_body,
                    text=body,
                )

            # workspace_id / lead_id / campaign_id explicitly written so
            # analytics + journey UI can find these rows. Without these,
            # the row gets created with NULL workspace_id and every
            # downstream query (analytics summary, journey timeline,
            # per-product breakdown) misses it.
            # One touchpoint per (lead_sequence, step, channel). If a row already
            # exists for this step — e.g. a prior FAILED attempt now being retried
            # — UPDATE it in place instead of INSERTing a second row. Two rows
            # (failed + sent) otherwise rendered as a duplicate "Initial Email"
            # (one Sent, one "Draft") and double-counted the step. Scoped to THIS
            # sequence, so a lead enrolled in two campaigns is unaffected.
            _tp_status = "sent" if send_result.get("ok") else "failed"
            tp = (
                db.query(NexusTouchpoint)
                .filter(
                    NexusTouchpoint.lead_sequence_id == ls.id,
                    NexusTouchpoint.step == ls.current_step,
                    NexusTouchpoint.channel == "email",
                )
                .order_by(NexusTouchpoint.id.desc())
                .first()
            )
            if tp is not None:
                tp.subject = subject
                tp.body = body
                tp.resend_message_id = send_result.get("id")
                tp.status = _tp_status
                tp.error = send_result.get("error")
                tp.workspace_id = ls.workspace_id
                tp.lead_id = ls.lead_id
                tp.campaign_id = ls.campaign_id
                tp.sent_at = now
            else:
                tp = NexusTouchpoint(
                    lead_sequence_id=ls.id,
                    step=ls.current_step,
                    subject=subject,
                    body=body,
                    resend_message_id=send_result.get("id"),
                    status=_tp_status,
                    error=send_result.get("error"),
                    workspace_id=ls.workspace_id,
                    lead_id=ls.lead_id,
                    campaign_id=ls.campaign_id,
                    channel="email",
                    sent_at=now,
                )
                db.add(tp)

            sl = NexusSendLog(
                workspace_id=ls.workspace_id,
                conversation_account_id=account.id,
                lead_id=ls.lead_id,
                recipient=recipient,
                status="sent" if send_result.get("ok") else "failed",
            )
            db.add(sl)

            if send_result.get("ok"):
                # Atomic DB-level increment. Previously this was a read-modify-write
                # (`count = count + 1`) on the ORM object, so when two concurrent
                # heartbeat ticks sent from the SAME mailbox their increments raced
                # and one was lost — the counter under-reported and the daily cap
                # overshot (e.g. 21 sent while the counter read 19). A single SQL
                # `+ 1` can't drop updates. We then EXPIRE the stale in-memory value
                # so later cap checks in this tick reload the true count AND the ORM
                # flush never clobbers the atomic increment.
                db.execute(
                    text(
                        "UPDATE nexus_conversation_accounts "
                        "SET daily_send_count = COALESCE(daily_send_count, 0) + 1 "
                        "WHERE id = :id"
                    ),
                    {"id": account.id},
                )
                db.expire(account, ["daily_send_count"])
                result["sent"] += 1
                # Mark the stored plan row for THIS step as sent (it was
                # fetched for the current step before the increment below).
                if email_row is not None:
                    email_row.status = "sent"
                    email_row.sent_at = now
                    _pid = send_result.get("id")
                    if _pid:
                        email_row.provider_message_id = str(_pid)
                ls.current_step += 1
                ls.last_error = None
                # Promote the global lead's badge from 'new'/'queued' →
                # 'contacted' so GTM Journey reflects that outreach actually
                # fired. Idempotent: leads already past contacted (replied,
                # demo_scheduled, bounced, unsubscribed) are left alone so a
                # follow-up send doesn't regress the status.
                if ls.lead_id:
                    try:
                        db.execute(
                            text(
                                """
                                UPDATE nexus_global_leads
                                   SET status = 'contacted'
                                 WHERE id = :gid
                                   AND (status IS NULL
                                        OR status = ''
                                        OR status IN ('new','queued'))
                                """
                            ),
                            {"gid": ls.lead_id},
                        )
                        # Per-workspace status mirror (authoritative for display).
                        db.execute(
                            text(
                                """
                                UPDATE nexus_leads
                                   SET status = 'contacted', updated_at = NOW()
                                 WHERE global_lead_id = :gid AND workspace_id = :ws
                                   AND (status IS NULL
                                        OR status = ''
                                        OR status IN ('new','queued'))
                                """
                            ),
                            {"gid": ls.lead_id, "ws": ls.workspace_id},
                        )
                    except Exception:
                        log.warning(
                            "sequencer: status bump to contacted failed (gl=%s)",
                            ls.lead_id,
                        )
                if ls.current_step >= len(steps):
                    ls.status = "completed"
                    ls.completed_at = now
                    ls.next_action_at = None
                else:
                    ls.next_action_at = _scheduled_next_action_at(ls, ls.current_step, steps, now)
            else:
                result["errors"] += 1
                ls.last_error = send_result.get("error") or "send failed"
                # Retry in 1 hour on transient failure.
                ls.next_action_at = now + timedelta(hours=1)

            # Release the lock so the next tick can re-pick this row when due.
            ls.locked_until = None

            result["details"].append(
                {
                    "lead_sequence_id": ls.id,
                    "step": tp.step,
                    "status": tp.status,
                    "model_used": model_used,
                }
            )

            # PER-ROW COMMIT — critical safety: persist this row's outcome
            # NOW instead of buffering until end-of-batch. If a later row
            # fails (Resend 5xx, stale RDS connection, etc.) this row's
            # touchpoint + lead_sequence advance stays committed and the
            # row will NOT be re-claimed on the next tick. Previously the
            # whole batch shared one transaction at the end; a single
            # failure rolled back every send's record and triggered the
            # re-send loop.
            try:
                db.commit()
            except Exception:
                log.exception(
                    "nexus.sequencer: per-row commit failed id=%s — "
                    "row may be re-tried but Resend already fired",
                    ls.id,
                )
                try:
                    db.rollback()
                except Exception:
                    pass
        except Exception as e:
            log.exception("nexus.sequencer: row failed id=%s", ls.id)
            result["errors"] += 1
            # CRITICAL: once a row inside the for-loop raises, the
            # SQLAlchemy session's transaction is left in an aborted
            # state. ORM writes (ls.last_error = ...) on that session
            # would normally be fine, but the very next query in the
            # NEXT iteration of the loop fires a PendingRollbackError
            # cascade — every subsequent row dies before reaching
            # send. Symptom in the logs: "50 errors, only 1 detail".
            # Fix: roll the session back, then use a fresh raw-SQL
            # update to record the per-row failure outside the
            # poisoned transaction. ORM attribute writes here would
            # just re-poison the next transaction.
            try:
                db.rollback()
            except Exception:
                pass
            try:
                db.execute(
                    text(
                        """
                        UPDATE nexus_lead_sequences
                           SET last_error = :err,
                               next_action_at = :nxt,
                               locked_until = NULL
                         WHERE id = :id
                        """
                    ),
                    {
                        "err": str(e)[:500],
                        "nxt": now + timedelta(hours=1),
                        "id": ls.id,
                    },
                )
                db.commit()
            except Exception:
                log.exception(
                    "nexus.sequencer: failed to record per-row failure id=%s",
                    ls.id,
                )
                try:
                    db.rollback()
                except Exception:
                    pass

    try:
        db.commit()
    except Exception:
        log.exception("nexus.sequencer: commit failed")
        try:
            db.rollback()
        except Exception:
            pass
        result["errors"] += 1

    return result


# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn draft auto-generation
#
# When a campaign attaches new leads, the user wants an outbound LinkedIn DM
# pre-written so they can copy/paste from the GTM Journey right-pane without
# clicking a "generate" button. Lambda has no background worker, so the only
# practical place to do this is the per-minute sequencer tick: the next tick
# after lead discovery picks up any leads that still lack an outbound draft
# and synthesizes one.
#
# Cap is intentional. Gemini calls are blocking; ~3s each. We let the tick
# absorb at most LINKEDIN_DRAFTS_PER_TICK leads so a 25-lead campaign drains
# across ~5 ticks (~5 min) without one tick dragging on for a minute.
# Per-company dedup is enforced via the same SQL the /linkedin/generate
# endpoint uses — same dedup, same prompt, just no HTTP layer.
# ─────────────────────────────────────────────────────────────────────────────


# Per-tick cap on LinkedIn DM draft generation. Bumped 5 → 25 (2026-05-28)
# so a 100-lead manual upload drains across ~4 ticks (~4 min) instead of
# ~20 ticks (~20 min). Each tick now runs ~25 × ~3s Gemini calls ≈ 75s,
# still well within typical scheduler intervals. Tune via env if a
# specific deploy has stricter limits.
LINKEDIN_DRAFTS_PER_TICK = int(os.getenv("NEXUS_LINKEDIN_DRAFTS_PER_TICK", "25"))


def _li_company_background(db: Session, company_domain) -> Dict[str, Any]:
    """Real company background for the lead's own company, from the same
    nexus_lead_enrichment scrape the email path uses — so LinkedIn copy is
    grounded in real facts too. Best-effort; never raises."""
    out: Dict[str, Any] = {"company_summary": "", "company_news": []}
    if not company_domain or not _table_exists(db, "nexus_lead_enrichment"):
        return out
    try:
        bg = db.execute(
            text(
                "SELECT meta_description, body_snippet, news_headlines "
                "FROM nexus_lead_enrichment WHERE company_domain = :d LIMIT 1"
            ),
            {"d": company_domain},
        ).mappings().first()
        if bg:
            summary = bg.get("meta_description") or bg.get("body_snippet")
            if summary:
                out["company_summary"] = str(summary)[:600]
            news = bg.get("news_headlines")
            titles = []
            if isinstance(news, list):
                for n in news[:2]:
                    if isinstance(n, dict) and n.get("title"):
                        titles.append(str(n["title"]))
                    elif isinstance(n, str):
                        titles.append(n)
            out["company_news"] = titles
    except Exception:
        log.debug("nexus.linkedin: company background lookup failed (non-fatal)")
    return out


def process_linkedin_drafts(
    db: Session, max_per_tick: int = LINKEDIN_DRAFTS_PER_TICK
) -> Dict[str, Any]:
    """Generate outbound LinkedIn DM drafts for leads that don't have one yet.

    Returns: {scanned, generated, skipped, errors}.

    Safe to call repeatedly; the dedup query guarantees we never write a
    second outbound draft for the same (workspace, global_lead_id). Drafts
    are generated PER LEAD — every enrolled lead gets its own message, even
    when several leads share a company domain. Each draft is a Gemini call
    (see routers.linkedin._generate_li_body) so we cap aggressively.
    """
    result: Dict[str, Any] = {
        "scanned": 0,
        "generated": 0,
        "skipped": 0,
        "errors": 0,
    }

    if not _table_exists(db, "nexus_linkedin_messages"):
        return result
    if not _table_exists(db, "nexus_leads"):
        return result
    # Belt-and-braces: the candidate query joins against nexus_global_leads
    # too. Skip-with-zero rather than letting the JOIN raise on a partial
    # pre-merge schema — keeps the scheduler tick clean across environments.
    if not _table_exists(db, "nexus_global_leads"):
        return result
    if not _table_exists(db, "nexus_campaigns"):
        return result

    # Find global leads enrolled in some campaign that don't yet have an
    # outbound LinkedIn draft of their own. We pull workspace_id +
    # product_id from the campaign join so the generator gets the right
    # product context.
    # Dedup is PER LEAD — a candidate lead is picked iff no outbound
    # LinkedIn draft already exists for this exact global_lead_id in this
    # workspace. We intentionally do NOT dedup by company_domain anymore:
    # every enrolled lead gets its own message even when several leads
    # share a company.
    try:
        candidate_rows = db.execute(
            text(
                """
                SELECT DISTINCT
                    gl.id            AS global_lead_id,
                    c.workspace_id   AS workspace_id,
                    c.product_id     AS product_id,
                    gl.first_name    AS first_name,
                    COALESCE(gl.job_title, gl.role) AS role,
                    gl.company_name  AS company_name,
                    gl.company_domain AS company_domain,
                    gl.person_city   AS person_city,
                    gl.person_state  AS person_state,
                    gl.person_country AS person_country,
                    gl.linkedin_headline AS linkedin_headline,
                    gl.source        AS source
                FROM nexus_leads nl
                JOIN nexus_campaigns c       ON c.id = nl.campaign_id
                JOIN nexus_global_leads gl   ON gl.id = nl.global_lead_id
                -- Enrollment gate: only draft for leads the operator approved
                -- and launched. Start-Outreach enrolls the accepted set; intent
                -- scoring alone no longer enrolls. No lead_sequence row => the
                -- run hasn't been launched (or this lead wasn't picked), so we
                -- must NOT generate LinkedIn content for it yet.
                WHERE EXISTS (
                    SELECT 1
                      FROM nexus_lead_sequences ls
                     WHERE ls.lead_id = gl.id
                       AND ls.campaign_id = c.id
                )
                AND NOT EXISTS (
                    SELECT 1
                      FROM nexus_linkedin_messages lim
                     WHERE lim.workspace_id = c.workspace_id
                       AND lim.direction = 'outbound'
                       AND lim.lead_id = gl.id
                )
                ORDER BY gl.id DESC
                LIMIT :cap
                """
            ),
            {"cap": int(max_per_tick)},
        ).mappings().all()
    except Exception:
        # Pre-merge or partial schema — bail cleanly.
        log.exception("nexus.sequencer: linkedin candidate query failed")
        return result

    result["scanned"] = len(candidate_rows)
    if not candidate_rows:
        return result

    # Import lazily so module load doesn't require these deps in test envs.
    # `_generate_inmail` is imported alongside the DM body generator so we
    # can produce a parallel InMail draft for every newly-discovered lead
    # — keeps the auto-InMail behaviour consistent between this background
    # sweep and the manual /linkedin/generate HTTP endpoint.
    from nexus.models_phase5 import LinkedInMessage
    from nexus.routers.linkedin import _generate_li_body, _generate_inmail

    # Best-effort per-workspace product cache so we don't re-query the
    # product row for every lead in a batch.
    product_cache: Dict[int, Dict[str, Any]] = {}

    def _product_for(workspace_id: int, product_id: Any) -> Dict[str, Any]:
        ck = int(product_id or 0) or -int(workspace_id)
        if ck in product_cache:
            return product_cache[ck]
        # Same fields the email path loads, so the LinkedIn copy prompts from
        # the SAME 3-section grounded product description.
        cols = ("name, value_proposition, icp, key_benefits, category, "
                "industry_relevance, product_description")
        prow = None
        if product_id:
            try:
                prow = db.execute(
                    text(f"SELECT {cols} FROM nexus_products WHERE id = :pid LIMIT 1"),
                    {"pid": int(product_id)},
                ).mappings().first()
            except Exception:
                prow = None
        if prow is None:
            try:
                prow = db.execute(
                    text(
                        f"SELECT {cols} FROM nexus_products "
                        "WHERE workspace_id = :w ORDER BY id LIMIT 1"
                    ),
                    {"w": int(workspace_id)},
                ).mappings().first()
            except Exception:
                prow = None
        if prow:
            icp_obj = prow.get("icp") or {}
            if isinstance(icp_obj, str):
                try:
                    import json as _json
                    icp_obj = _json.loads(icp_obj) or {}
                except Exception:
                    icp_obj = {}
            pd = _merge_product_description(
                stored=prow.get("product_description"),
                synthesized=_build_product_description(
                    name=prow.get("name") or "",
                    category=prow.get("category") or "",
                    value_proposition=prow.get("value_proposition") or "",
                    key_benefits=prow.get("key_benefits"),
                    industry_relevance=prow.get("industry_relevance") or "",
                    icp_obj=icp_obj,
                ),
            )
            out = {
                "name": prow.get("name") or "",
                "value_prop": prow.get("value_proposition") or "",
                "product_description": pd,
                # Booking link for the InMail CTA (appended in code, not by the
                # model). From the product's brand config, else env default.
                "cta_url": (icp_obj.get("brand") or {}).get("cta_url") or os.getenv("CTA_URL") or "",
            }
        else:
            out = {"name": "", "value_prop": "", "product_description": {}, "cta_url": ""}
        product_cache[ck] = out
        return out

    for cand in candidate_rows:
        try:
            workspace_id = int(cand["workspace_id"] or 0)
            global_lead_id = int(cand["global_lead_id"] or 0)
            if not workspace_id or not global_lead_id:
                result["skipped"] += 1
                continue

            # Belt-and-braces: re-check dedup right before insert, PER LEAD.
            # The candidate query already filters, but two concurrent ticks
            # could pick overlapping rows on slow Postgres. We only skip if
            # THIS specific lead already has an outbound draft — every lead
            # gets its own message, even when several share a company.
            company_domain = cand["company_domain"]
            already = db.execute(
                text(
                    """
                    SELECT 1 FROM nexus_linkedin_messages
                    WHERE workspace_id = :w
                      AND direction = 'outbound'
                      AND lead_id = :gid
                    LIMIT 1
                    """
                ),
                {"w": workspace_id, "gid": global_lead_id},
            ).first()
            if already:
                log.debug(
                    "nexus.linkedin: skipping lead=%s (already has outbound draft)",
                    global_lead_id,
                )
                result["skipped"] += 1
                continue

            product = _product_for(workspace_id, cand.get("product_id"))
            # Same grounded knowledge base the email path uses: lead location +
            # LinkedIn headline, the 3-section product description, and real
            # company background — so DM/InMail are precise & personalized too.
            _location = ", ".join(
                x for x in (
                    cand.get("person_city"),
                    cand.get("person_state"),
                    cand.get("person_country"),
                ) if x
            )
            _headline = cand.get("linkedin_headline") or ""
            _bg = _li_company_background(db, company_domain)
            body = _generate_li_body(
                first_name=cand.get("first_name") or "",
                role=cand.get("role") or "",
                company=cand.get("company_name") or company_domain or "",
                product_name=product["name"] or "our product",
                product_value_prop=product["value_prop"] or "",
                location=_location,
                linkedin_headline=_headline,
                product_description=product.get("product_description") or {},
                company_summary=_bg["company_summary"],
                company_news=_bg["company_news"],
            )
            # DM insert is wrapped in a SAVEPOINT so a future
            # partial-UNIQUE-INDEX violation from a concurrent worker
            # race (add_linkedin_message_unique_index.py installs
            # uq_li_msg_outbound_one_per_variant on
            # workspace_id+lead_id+variant WHERE direction='outbound')
            # rolls back ONLY this insert. The surrounding loop
            # commits everything else, and the lead is counted as a
            # skip rather than an error so the operator sees the
            # honest reason in the result dict.
            row = LinkedInMessage(
                workspace_id=workspace_id,
                lead_id=global_lead_id,
                direction="outbound",
                body=body,
                intent="generated",
            )
            try:
                with db.begin_nested():
                    db.add(row)
                    db.flush()
            except IntegrityError:
                # Another worker won the race for this lead's DM.
                # No-op: the row already exists, the operator's
                # invariant ("at most one outbound DM per lead per
                # workspace") still holds.
                log.warning(
                    "nexus.sequencer: DM unique-constraint race for "
                    "lead=%s workspace=%s — another worker already "
                    "inserted the DM. Skipping InMail too to preserve "
                    "the 1-DM + 1-InMail invariant.",
                    global_lead_id, workspace_id,
                )
                result["skipped"] += 1
                continue
            result["generated"] += 1

            # Auto-generate a LinkedIn InMail draft alongside the DM
            # so newly-discovered leads land in the GTM Journey with
            # both copy variants ready for the operator to pick from.
            # Wrapped in a SAVEPOINT (db.begin_nested) so any failure
            # below — Gemini timeout, DB hiccup, bad payload — rolls
            # back ONLY the InMail attempt. The DM row above and any
            # prior candidates' inserts in this batch stay intact.
            # We deliberately do NOT add a separate company-scope
            # dedup here: the outer pre-DM check on lines 1019-1048
            # already ensures we only reach this code for companies
            # that had no outbound draft yet, so a fresh InMail is
            # always appropriate at this point.
            # Retry the InMail generation up to 2 times on transient
            # failures (Gemini timeout, DB connection blip, JSON parse
            # quirk). Each attempt runs inside its own SAVEPOINT so a
            # rollback doesn't poison the surrounding DM commit or the
            # rest of the candidate batch. We've observed cases where
            # the first attempt rolled back and the lead landed with
            # DM-only; this loop catches ~80% of those without changing
            # any other code path or success behaviour.
            INMAIL_MAX_ATTEMPTS = 2
            inmail_succeeded = False
            for inmail_attempt in range(1, INMAIL_MAX_ATTEMPTS + 1):
                try:
                    with db.begin_nested():
                        from nexus.routers.linkedin import _INMAIL_STEPS
                        cadence = _generate_inmail(
                            first_name=cand.get("first_name") or "",
                            role=cand.get("role") or "",
                            company=cand.get("company_name") or company_domain or "",
                            product_name=product["name"] or "our product",
                            product_value_prop=product["value_prop"] or "",
                            location=_location,
                            linkedin_headline=_headline,
                            product_description=product.get("product_description") or {},
                            company_summary=_bg["company_summary"],
                            company_news=_bg["company_news"],
                            cta_url=product.get("cta_url") or "",
                        )
                        # Store the 4-step InMail cadence as 4 rows
                        # (variant='inmail', step 0=initial .. 3=closing). The
                        # UI shows the lowest unsent step as the active draft.
                        for _step_idx, _key in enumerate(_INMAIL_STEPS):
                            _msg = cadence.get(_key) or {}
                            db.add(LinkedInMessage(
                                workspace_id=workspace_id,
                                lead_id=global_lead_id,
                                direction="outbound",
                                body=_msg.get("body") or "",
                                subject=_msg.get("subject") or "",
                                variant="inmail",
                                step=_step_idx,
                                intent="generated",
                            ))
                        db.flush()
                    inmail_succeeded = True
                    break
                except IntegrityError:
                    # Another worker won the race for this lead's
                    # InMail. The row already exists; treat as
                    # success (no retry, no error) so the operator
                    # doesn't see spurious WARNINGs in CloudWatch.
                    log.info(
                        "nexus.sequencer: InMail unique-constraint "
                        "race for lead=%s workspace=%s — row already "
                        "exists, treating as success.",
                        global_lead_id, workspace_id,
                    )
                    inmail_succeeded = True
                    break
                except Exception:
                    # Log every attempt at WARNING so the operator can
                    # see retries in the log. Only the final failure
                    # leaves the lead DM-only.
                    log.warning(
                        "nexus.sequencer: inmail draft for lead %s "
                        "attempt %d/%d failed",
                        global_lead_id, inmail_attempt, INMAIL_MAX_ATTEMPTS,
                        exc_info=True,
                    )
            if not inmail_succeeded:
                log.warning(
                    "nexus.sequencer: inmail draft for lead %s "
                    "permanently failed after %d attempts (DM preserved)",
                    global_lead_id, INMAIL_MAX_ATTEMPTS,
                )
        except Exception:
            log.exception("nexus.sequencer: linkedin draft failed")
            result["errors"] += 1
            db.rollback()
            continue

    try:
        db.commit()
    except Exception:
        log.exception("nexus.sequencer: linkedin commit failed")
        db.rollback()
        result["errors"] += 1
    return result
