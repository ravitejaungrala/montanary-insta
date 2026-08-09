"""
Inbound ingestion helper for NEXUS.

The only inbound channel is Microsoft Graph polling
(`services/ms_mail_sync.sync_ms_inbound_mail`). That poller imports
`_ingest_inbound_message` from this module to thread, classify, run the
RAG reply agent, and persist the message + inbox row.

No HTTP routes are exposed here; the router is kept as an empty
APIRouter so `main.py`'s NEXUS router registration loop still succeeds.

`_ingest_inbound_message`:
  1. Matches the reply to an existing thread (or creates one / logs unmatched).
  2. Persists an InboundMessage and Inbox row.
  3. Runs intent classification + RAG reply.
  4. Applies downstream side-effects per intent (suppression list,
     LeadSequence halt, GlobalLead status update).

The downstream side-effects target tables owned by Phases 3 and 4 — we
guard each update with a `try/except` + table-existence probe so this
module works in isolation and self-heals when those tables land.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi import Depends
from core.database import get_db
from nexus.deps import require_current_workspace

from nexus.models_phase5 import InboundMessage, Inbox
from nexus.services import intent_classifier, ms_graph_client, rag_reply, thread_matcher
from nexus.services.reply_cta import ensure_signature

logger = logging.getLogger("nexus.routers.inbound")

router = APIRouter(prefix="/nexus/inbound", tags=["nexus-inbound"])

# Intents that keep the automated cadence running instead of halting it.
# Rule: only an explicitly NEGATIVE signal stops outreach (NOT_INTERESTED,
# UNSUBSCRIBE) — every other reply, including a positive one, gets an
# instant reply (existing auto-reply guard chain in ms_mail_sync.py /
# inbound_sync.py) AND keeps its remaining follow-up steps, rewritten to
# build on the reply instead of firing stale pre-written content (see
# regenerate_pending_followups below).
#
# DEMO_SCHEDULED is the one exception to "positive keeps going": a meeting is
# already booked, so continuing to send cold-cadence follow-ups chasing a
# meeting that's already on the calendar would be counterproductive — it
# halts and hands off to the booking flow (see the DEMO_SCHEDULED block
# below), same as before.
#
# LEFT_COMPANY keeps going too (scenario 5): the person has moved on, but the
# thread is still worth continuing WITH THEM — to learn where they've gone and
# who to talk to instead. The follow-ups are regrounded so they stop pitching
# the company the person just left (see regenerate_pending_followups). A named
# replacement contact still spins up its own referral lead via the continuing
# regeneration path, exactly as a continuing reply already does.
CONTINUING_INTENTS = {"OUT_OF_OFFICE", "NOT_NOW", "QUESTION", "INTERESTED", "LEFT_COMPANY"}


# ---------- Downstream side-effects (cross-phase, best-effort) ---------------

def _table_exists(db: Session, table_name: str) -> bool:
    try:
        row = db.execute(
            text("SELECT 1 FROM information_schema.tables WHERE table_name = :t LIMIT 1"),
            {"t": table_name},
        ).first()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


def _apply_intent_side_effects(
    db: Session,
    *,
    workspace_id: Optional[int],
    from_email: Optional[str],
    intent: str,
    lead_id: Optional[int],
    lead_sequence_id: Optional[int],
    inbound_message: Optional[InboundMessage] = None,
) -> None:
    """Best-effort updates against Phase 3 (GlobalLead) and Phase 4
    (LeadSequence, SuppressionList) tables. Silent no-ops if tables aren't
    present yet."""

    # We still mark them 'replied' (a response DID arrive) regardless of
    # intent, but CONTINUING_INTENTS do NOT stop the sequence — their pending
    # follow-up steps get rewritten instead (see the regeneration block
    # below), rather than silently sending stale, reply-unaware content.
    # Every other intent is treated as requiring a human handoff, so it ALSO
    # stops outreach (below).
    _is_continuing = (intent or "").upper() in CONTINUING_INTENTS

    # `lead_id` passed in is nexus_inbound_leads.id (the timeline-join key), NOT
    # the global-lead id. Resolve the real global lead from the sender's email so
    # the status update + LinkedIn stop below hit the correct row.
    global_lead_id: Optional[int] = None
    if from_email and _table_exists(db, "nexus_global_leads"):
        try:
            _gl = db.execute(
                text("SELECT id FROM nexus_global_leads WHERE lower(email) = lower(:em) LIMIT 1"),
                {"em": from_email},
            ).first()
            if _gl:
                global_lead_id = int(_gl[0])
        except Exception as exc:  # noqa: BLE001
            logger.warning("global lead resolve failed: %s", exc)
            db.rollback()

    # ANY reply — including OOO — marks the lead 'replied' so the badge reflects
    # that the prospect responded.
    if global_lead_id is not None:
        try:
            db.execute(
                text(
                    "UPDATE nexus_global_leads SET status = :s, updated_at = NOW() "
                    "WHERE id = :id"
                ),
                {"s": "replied", "id": global_lead_id},
            )
            # Per-workspace mirror (authoritative for display) — scoped so this
            # tenant's 'replied' never appears on another tenant's view of a
            # shared lead.
            if workspace_id is not None:
                db.execute(
                    text(
                        "UPDATE nexus_leads SET status = :s, updated_at = NOW() "
                        "WHERE global_lead_id = :id AND workspace_id = :ws"
                    ),
                    {"s": "replied", "id": global_lead_id, "ws": int(workspace_id)},
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("global lead status update failed: %s", exc)
            db.rollback()

    if not _is_continuing and lead_sequence_id is not None and _table_exists(db, "nexus_lead_sequences"):
        try:
            db.execute(
                text(
                    "UPDATE nexus_lead_sequences SET status = :s, updated_at = NOW() "
                    "WHERE id = :id"
                ),
                {"s": "replied", "id": lead_sequence_id},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("lead sequence status update failed: %s", exc)
            db.rollback()

    # Self-heal: a continuing-intent reply should actively ENSURE the
    # sequence is active, not just skip halting it — a row previously
    # halted to 'replied' (e.g. an earlier misclassified reply) must not
    # stay stuck once a continuing-intent reply arrives. Scoped to the
    # 'replied' status specifically (the one this same halting branch
    # sets) — never touches a compliance/conversion halt_reason like
    # 'unsubscribed' or 'demo_scheduled'.
    if _is_continuing and lead_sequence_id is not None and _table_exists(db, "nexus_lead_sequences"):
        try:
            db.execute(
                text(
                    "UPDATE nexus_lead_sequences SET status = 'active', updated_at = NOW() "
                    "WHERE id = :id AND status = 'replied'"
                ),
                {"id": lead_sequence_id},
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("lead sequence reactivation failed: %s", exc)
            db.rollback()

    # CONTINUING_INTENTS: rewrite the pending follow-up steps so they
    # acknowledge/build on what the lead actually said, instead of firing
    # the stale enrollment-time draft (the bug this replaces). Best-effort —
    # a failure here must never block message ingestion.
    if (
        _is_continuing
        and lead_sequence_id is not None
        and inbound_message is not None
        and _table_exists(db, "nexus_lead_sequences")
        and _table_exists(db, "nexus_lead_emails")
    ):
        try:
            from nexus.models_phase4 import NexusLeadSequence
            from nexus.services.sequencer import regenerate_pending_followups, create_referral_leads

            ls = db.query(NexusLeadSequence).filter(NexusLeadSequence.id == lead_sequence_id).first()
            if ls is not None:
                regen_result = regenerate_pending_followups(db, ls, inbound_message)
                referral_contacts = (regen_result or {}).get("referral_contacts") or []
                if referral_contacts:
                    create_referral_leads(db, ls, referral_contacts, inbound_message.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("reply-aware follow-up regeneration failed (non-fatal): %s", exc)
            db.rollback()
    elif (
        lead_sequence_id is not None
        and inbound_message is not None
        and _table_exists(db, "nexus_lead_sequences")
    ):
        # Halting intents skip the block above entirely (no follow-ups to
        # rewrite — the sequence is stopping), but a named alternate
        # contact is still real signal even when the SENDER's own reply is
        # a "no". Standalone, cheaply-gated extraction path — see
        # sequencer.py::handle_referral_mentions.
        try:
            from nexus.services.sequencer import handle_referral_mentions
            handle_referral_mentions(db, lead_sequence_id, inbound_message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("referral-mention handling failed (non-fatal): %s", exc)
            db.rollback()

    # UNSUBSCRIBE -> suppression list + halt sequence + lead status = unsubscribed
    if intent == "UNSUBSCRIBE":
        if from_email and _table_exists(db, "nexus_suppression_list"):
            try:
                # Columns are workspace_id/email_lower/reason/added_at
                # (NexusSuppressionList, models_phase4.py) — NOT email/
                # created_at. A real column-name bug found via the 20-lead
                # simulation (test_mail_simulation.py, scenario 9): a
                # NOT_INTERESTED reply worded like "don't reach out again"
                # got classified UNSUBSCRIBE, hit this INSERT, and failed.
                db.execute(
                    text(
                        "INSERT INTO nexus_suppression_list (workspace_id, email_lower, reason, added_at) "
                        "VALUES (:ws, :em, 'inbound_unsubscribe', NOW()) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"ws": workspace_id, "em": from_email.lower()},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("suppression insert failed: %s", exc)
                db.rollback()

        if global_lead_id is not None:
            try:
                db.execute(
                    text(
                        "UPDATE nexus_global_leads SET status = 'unsubscribed', updated_at = NOW() "
                        "WHERE id = :id"
                    ),
                    {"id": global_lead_id},
                )
                # Per-workspace mirror (unsubscribe is scoped to the tenant the
                # prospect replied to — matches the workspace-scoped suppression
                # list above; another tenant's view is unaffected).
                if workspace_id is not None:
                    db.execute(
                        text(
                            "UPDATE nexus_leads SET status = 'unsubscribed', updated_at = NOW() "
                            "WHERE global_lead_id = :id AND workspace_id = :ws"
                        ),
                        {"id": global_lead_id, "ws": int(workspace_id)},
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("lead unsubscribed update failed: %s", exc)
                db.rollback()

        if lead_sequence_id is not None and _table_exists(db, "nexus_lead_sequences"):
            try:
                db.execute(
                    text(
                        "UPDATE nexus_lead_sequences SET status = 'halted', halt_reason = 'unsubscribed', "
                        "updated_at = NOW() WHERE id = :id"
                    ),
                    {"id": lead_sequence_id},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("sequence halt(unsub) failed: %s", exc)
                db.rollback()

    # DEMO_SCHEDULED — but "wants a demo" splits two ways (scenario 7), and the
    # classifier can't reliably tell them apart (an enthusiastic "happy to do a
    # demo whenever" classifies DEMO_SCHEDULED just like "Tuesday 2pm works").
    # The RELIABLE discriminator is whether a concrete time can be extracted:
    #   - a parseable time  -> real booking + invite, POSITIVE STOP (like §6).
    #   - no parseable time -> they want a demo but haven't picked a slot, so
    #     KEEP the cadence going and let the next follow-up carry the booking
    #     link (scenario 7b). book_demo_from_reply returns booked=False here.
    demo_kept_active = False
    if intent == "DEMO_SCHEDULED" and lead_sequence_id is not None and _table_exists(db, "nexus_lead_sequences"):
        book_result: Dict[str, Any] = {}
        if inbound_message is not None and _table_exists(db, "nexus_demo_bookings"):
            try:
                from nexus.services.demo_handling_agent import book_demo_from_reply

                book_result = book_demo_from_reply(
                    db,
                    lead_sequence_id=lead_sequence_id,
                    inbound_message=inbound_message,
                )
                logger.info("demo booking from reply: %s", book_result)
            except Exception as exc:  # noqa: BLE001
                logger.warning("book_demo_from_reply failed (non-fatal): %s", exc)
                db.rollback()

        if book_result.get("booked"):
            # A real time was confirmed. book_demo_from_reply already ran the
            # demo-scheduled roll-up (which halts the sequence); make the
            # positive-stop tag explicit here too so it's set even if the
            # roll-up was a partial no-op.
            try:
                db.execute(
                    text(
                        "UPDATE nexus_lead_sequences SET status = 'halted', "
                        "halt_reason = 'demo_scheduled', next_action_at = NULL, "
                        "updated_at = NOW() WHERE id = :id"
                    ),
                    {"id": lead_sequence_id},
                )
                db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("sequence halt(demo) failed: %s", exc)
                db.rollback()
        else:
            # Wants a demo, no time given -> DON'T halt. Reactivate (the
            # non-continuing branch above set 'replied') and regenerate the
            # pending follow-ups; the booking-link heuristic in
            # regenerate_pending_followups puts the link in the next one.
            demo_kept_active = True
            try:
                db.execute(
                    text(
                        "UPDATE nexus_lead_sequences SET status = 'active', "
                        "updated_at = NOW() WHERE id = :id AND status IN ('replied', 'paused')"
                    ),
                    {"id": lead_sequence_id},
                )
                db.commit()
                if inbound_message is not None and _table_exists(db, "nexus_lead_emails"):
                    from nexus.models_phase4 import NexusLeadSequence
                    from nexus.services.sequencer import regenerate_pending_followups

                    ls_row = (
                        db.query(NexusLeadSequence)
                        .filter(NexusLeadSequence.id == lead_sequence_id)
                        .first()
                    )
                    if ls_row is not None:
                        regenerate_pending_followups(db, ls_row, inbound_message)
            except Exception as exc:  # noqa: BLE001
                logger.warning("demo(no-time) keep-active + regen failed: %s", exc)
                db.rollback()

    # Mirror the stop onto the lead's LinkedIn sequences — one human signal
    # (reply / unsubscribe / demo) stops ALL channels (Phase-3 GTM LinkedIn
    # Agent). Flag-gated + best-effort so the email reply path never depends on
    # the LinkedIn agent being enabled or its tables existing.
    # CONTINUING_INTENTS are skipped here too — the automation keeps going on
    # every channel for those, not just email. A DEMO_SCHEDULED reply we kept
    # active (wants a demo, no time — scenario 7b) is also a keep-going case, so
    # it must NOT stop LinkedIn either.
    if not _is_continuing and not demo_kept_active and global_lead_id is not None and workspace_id is not None:
        try:
            from core.config import NEXUS_LINKEDIN_ENABLED
            if NEXUS_LINKEDIN_ENABLED and _table_exists(db, "gtm_linkedin_lead_state"):
                from gtm.linkedin.sequence_engine import stop_lead_states_for_lead
                _reason = {
                    "UNSUBSCRIBE": "unsubscribed",
                    "DEMO_SCHEDULED": "demo_booked",
                }.get(intent, "email_reply")
                # Scoped to THIS workspace — a reply here must not stop another
                # workspace's LinkedIn outreach to the same global lead.
                stop_lead_states_for_lead(db, lead_id=global_lead_id, reason=_reason, workspace_id=workspace_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("linkedin sequence stop failed: %s", exc)
            db.rollback()


def _resolve_booking_url(db: Session, product_id: Optional[int]) -> Optional[str]:
    """The booking link for a product's brand: the per-brand
    nexus_sender_brands.booking_url (same resolver the emails use), else the
    product's icp.brand.cta_url, else the NEXUS_DEFAULT_CTA_URL env. None when
    nothing is configured (caller then just doesn't offer a link)."""
    import os as _os

    env_default = (_os.getenv("NEXUS_DEFAULT_CTA_URL") or _os.getenv("CTA_URL") or "").strip() or None
    if not product_id:
        return env_default
    try:
        row = db.execute(
            text(
                "SELECT id, source_url, "
                "COALESCE(NULLIF(icp->>'entity_type',''),'product') AS et, icp "
                "FROM nexus_products WHERE id = :p"
            ),
            {"p": int(product_id)},
        ).mappings().first()
        if not row:
            return env_default
        from nexus.services.sequencer import _brand_booking_url

        brand_url = _brand_booking_url(
            db, {"id": row["id"], "source_url": row["source_url"], "entity_type": row["et"]}
        )
        if brand_url:
            return brand_url
        icp = row["icp"] or {}
        if isinstance(icp, str):
            import json as _json

            try:
                icp = _json.loads(icp)
            except Exception:  # noqa: BLE001
                icp = {}
        brand = (icp.get("brand") or {}) if isinstance(icp, dict) else {}
        return (str(brand.get("cta_url") or "").strip() or env_default)
    except Exception:  # noqa: BLE001
        db.rollback()
        return env_default


def _lead_country_iso(db: Session, email: str) -> Optional[str]:
    """The lead's country as an ISO-2 region (from nexus_global_leads.
    person_country), or None. Used to anchor bare-local phone numbers to the
    right country code. Best-effort — never raises."""
    try:
        row = db.execute(
            text("SELECT person_country FROM nexus_global_leads WHERE lower(email) = lower(:e)"),
            {"e": email},
        ).first()
        return intent_classifier.country_to_iso(row[0]) if row else None
    except Exception:  # noqa: BLE001
        return None


def _merge_lead_phones(
    db: Session, email: str, numbers: list, workspace_id: Optional[int] = None
) -> None:
    """Merge freshly-extracted phone numbers into the lead's list (scenario 9).

    Append-only and de-duplicated across replies: a second number in the same
    reply, or a new number in a later reply, JOINS the existing ones rather than
    replacing them or being dropped. The primary scalar `phone` is filled only
    when it's currently empty — never overwritten (a verified Apollo number
    outranks a regex-matched signature).

    Must be called inside a SAVEPOINT (see the call site) so any failure here
    rolls back ONLY the phone write, never the surrounding message ingestion.
    """
    import json as _json

    row = db.execute(
        text("SELECT phone, phones FROM nexus_global_leads WHERE lower(email) = lower(:e)"),
        {"e": email},
    ).first()
    if row is None:
        return
    primary = (row[0] or "").strip()
    existing = row[1] if isinstance(row[1], list) else []
    # Seed the list from the primary if it's not represented yet (a pre-backfill
    # row, or an Apollo phone captured before this list existed) so merging can
    # never silently drop the number already on file.
    if primary and not existing:
        existing = [primary]
    merged = intent_classifier.merge_phone_lists(existing, numbers)
    if merged == existing and primary:
        return  # nothing new to persist
    new_primary = primary or (merged[0] if merged else None)
    db.execute(
        text(
            "UPDATE nexus_global_leads "
            "SET phones = CAST(:phones AS jsonb), "
            "    phone = COALESCE(NULLIF(phone, ''), :primary) "
            "WHERE lower(email) = lower(:e)"
        ),
        {"phones": _json.dumps(merged), "primary": new_primary, "e": email},
    )
    # Per-workspace mirror (authoritative for display) — a phone captured from a
    # reply in THIS workspace must not surface on another tenant's view.
    if workspace_id is not None:
        db.execute(
            text(
                "UPDATE nexus_leads nl "
                "SET phones = CAST(:phones AS jsonb), "
                "    phone = COALESCE(NULLIF(nl.phone, ''), :primary), "
                "    updated_at = NOW() "
                "FROM nexus_global_leads gl "
                "WHERE gl.id = nl.global_lead_id "
                "  AND lower(gl.email) = lower(:e) "
                "  AND nl.workspace_id = :ws"
            ),
            {"phones": _json.dumps(merged), "primary": new_primary, "e": email, "ws": int(workspace_id)},
        )


# ---------- Core ingestion ----------------------------------------------------

def _ingest_inbound_message(
    db: Session,
    *,
    workspace_id: Optional[int],
    from_email: Optional[str],
    to_email: Optional[str],
    subject: Optional[str],
    body_text: Optional[str],
    body_html: Optional[str],
    message_id_header: Optional[str],
    in_reply_to_header: Optional[str],
    references_header: Optional[str] = None,
    gmail_thread_id: Optional[str] = None,
    resend_thread_id: Optional[str] = None,
    raw_payload: Optional[str] = None,
    received_at: Optional[datetime] = None,
    inbound_lead_id: Optional[int] = None,
    lead_sequence_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Returns {'message_id': int|None, 'thread_id': int|None, 'intent': str, 'unmatched': bool}.

    `inbound_lead_id` (when provided) is the `nexus_inbound_leads.id`
    that the GTM-journey timeline join keys on — callers should pass it
    when the sender has been resolved to a known lead.

    `lead_sequence_id` (when provided) is the `nexus_lead_sequences.id`
    that the inbound is most plausibly a reply to — used downstream to
    resolve `product_id` for RAG retrieval (lines 221-235 below). When
    null, the RAG agent falls back to the workspace's most-recent active
    campaign's product.
    """

    references_list = []
    if references_header:
        references_list = [r.strip() for r in references_header.replace(",", " ").split() if r.strip()]

    thread = thread_matcher.match(
        db,
        workspace_id=workspace_id,
        from_email=from_email,
        subject=subject,
        in_reply_to=in_reply_to_header,
        references=references_list,
        gmail_thread_id=gmail_thread_id,
        resend_thread_id=resend_thread_id,
    )

    # Back-fill an existing thread that pre-dates lead/sequence resolution
    # so the journey timeline JOIN (thread.lead_id = inbound_lead.id) and
    # the product-attribution chain (thread.lead_sequence_id → campaign →
    # product) both start hitting from this message onward. The existing
    # db.commit() below persists the in-memory assignments.
    if thread is not None and thread.lead_id is None and inbound_lead_id is not None:
        thread.lead_id = inbound_lead_id
    if thread is not None and thread.lead_sequence_id is None and lead_sequence_id is not None:
        thread.lead_sequence_id = lead_sequence_id

    # If no match AND we have a workspace, create a fresh thread so the
    # message still lands in the inbox.
    if thread is None and workspace_id is not None:
        thread = thread_matcher.create_thread(
            db,
            workspace_id=workspace_id,
            subject=subject,
            gmail_thread_id=gmail_thread_id,
            resend_thread_id=resend_thread_id,
            lead_id=inbound_lead_id,
            lead_sequence_id=lead_sequence_id,
        )

    # If still no thread (no workspace context AND no header match),
    # persist UnmatchedReply and bail.
    if thread is None:
        thread_matcher.log_unmatched(
            db,
            workspace_id=workspace_id,
            from_email=from_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            message_id_header=message_id_header,
            in_reply_to_header=in_reply_to_header,
            raw_payload=raw_payload,
        )
        db.commit()
        return {"message_id": None, "thread_id": None, "intent": "UNCLASSIFIED", "unmatched": True}

    # Classify intent
    classification = intent_classifier.classify(body_text or "")

    # Best-effort: a reply's signature block often carries real phone numbers
    # (e.g. an OOO auto-reply's "Brandi Meredith / VP of Marketing /
    # 402.873.2318", or an office AND a mobile) that we'd otherwise never
    # capture. Every distinct number is kept in the lead's `phones` list,
    # de-duplicated and append-only across replies (scenario 9); the primary
    # `phone` scalar is only filled when empty. Isolated in a SAVEPOINT so a
    # failure here can't discard the rest of this message's ingestion.
    if from_email:
        try:
            # Anchor bare local numbers on the lead's own country so they store
            # with the right +CC (country_code_number.md): a lead in India who
            # writes "098111 20034" gets "+91 98111 20034", not a US guess.
            _lead_region = _lead_country_iso(db, from_email)
            numbers = intent_classifier.extract_phone_numbers(
                body_text or "", default_region=_lead_region
            )
            if numbers:
                with db.begin_nested():
                    _merge_lead_phones(
                        db, from_email, numbers, workspace_id=thread.workspace_id
                    )
        except Exception:  # noqa: BLE001
            logger.warning("phone capture failed (non-fatal) for %s", from_email)

    # Resolve product_id via lead_sequence → campaign so RAG retrieval
    # can scope to the product's knowledge base (knowledge embeddings
    # are keyed by product_id, not workspace_id).
    product_id: Optional[int] = None
    if thread.lead_sequence_id:
        row = db.execute(
            text(
                """
                SELECT c.product_id
                  FROM nexus_lead_sequences ls
                  JOIN nexus_campaigns c ON c.id = ls.campaign_id
                 WHERE ls.id = :ls_id
                """
            ),
            {"ls_id": thread.lead_sequence_id},
        ).fetchone()
        if row and row[0]:
            product_id = int(row[0])

    # Scenario 7b: if the reply says they want a demo but names NO time, the
    # INSTANT reply should proactively hand over the booking link (so they can
    # pick a slot right away) rather than inventing times or making them wait
    # for a follow-up. Resolve the product/brand's booking link and pass it in.
    booking_url: Optional[str] = None
    try:
        from nexus.services.reply_post_processor import wants_demo_without_time
        if wants_demo_without_time(body_text or ""):
            booking_url = _resolve_booking_url(db, product_id)
    except Exception:  # noqa: BLE001
        logger.warning("booking-link resolve for instant reply failed (non-fatal)")

    # RAG-grounded suggested reply
    suggested = rag_reply.generate(
        inbound_text=body_text or "",
        intent=classification.intent,
        workspace_id=thread.workspace_id,
        db=db,
        product_id=product_id,
        booking_url=booking_url,
    )

    msg = InboundMessage(
        thread_id=thread.id,
        direction="inbound",
        from_email=from_email,
        to_email=to_email,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        message_id_header=message_id_header,
        in_reply_to_header=in_reply_to_header,
        received_at=received_at or datetime.utcnow(),
        intent=classification.intent,
        intent_confidence=classification.confidence,
        intent_reasoning=classification.reasoning,
        suggested_reply=suggested,
        is_read=False,
    )
    db.add(msg)
    try:
        db.flush()
    except IntegrityError:
        # Two concurrent poller runs can both pass the caller's
        # _already_ingested() pre-check for the same message_id_header
        # before either commits (classic check-then-insert race) — the
        # unique index on nexus_inbound_messages.message_id_header is what
        # actually closes it. Losing this race isn't an error: someone else
        # already ingested the identical message, so roll back our attempt
        # and hand back THEIR row instead of creating a duplicate thread +
        # message (which rendered as two identical "Reply from X" cards).
        db.rollback()
        existing = None
        if message_id_header:
            existing = (
                db.query(InboundMessage)
                .filter(InboundMessage.message_id_header == message_id_header)
                .first()
            )
        if existing is None:
            raise
        return {
            "message_id": existing.id,
            "thread_id": existing.thread_id,
            "intent": existing.intent,
            "unmatched": False,
        }

    # Thread bookkeeping
    thread.last_message_at = msg.received_at
    thread.message_count = (thread.message_count or 0) + 1
    if gmail_thread_id and not thread.gmail_thread_id:
        thread.gmail_thread_id = gmail_thread_id
    if resend_thread_id and not thread.resend_thread_id:
        thread.resend_thread_id = resend_thread_id

    # Inbox unread queue
    inbox_row = Inbox(
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        message_id=msg.id,
        is_unread=True,
    )
    db.add(inbox_row)

    # Downstream side-effects
    _apply_intent_side_effects(
        db,
        workspace_id=thread.workspace_id,
        from_email=from_email,
        intent=classification.intent,
        lead_id=thread.lead_id,
        lead_sequence_id=thread.lead_sequence_id,
        inbound_message=msg,
    )

    db.commit()
    return {
        "message_id": msg.id,
        "thread_id": thread.id,
        "intent": classification.intent,
        "unmatched": False,
    }


# ---------- Manual reply-agent endpoint --------------------------------------
#
# On-demand: POST a (workspace_id, lead_email, query) and the reply agent
# generates a grounded reply and (optionally) sends it via MS Graph from the
# configured mailbox. Does NOT persist inbound/outbound rows — this is a
# manual trigger that bypasses the poller's dedup + threading bookkeeping.

class ReplyToLeadRequest(BaseModel):
    workspace_id: int
    lead_email: EmailStr
    query: str
    mailbox: Optional[str] = None
    subject: Optional[str] = None
    send: bool = True


def _resolve_mailbox(db: Session, workspace_id: int) -> Optional[str]:
    try:
        row = db.execute(
            text(
                "SELECT settings->>'ms_mailbox_address' FROM nexus_settings "
                "WHERE workspace_id = :ws LIMIT 1"
            ),
            {"ws": int(workspace_id)},
        ).first()
        if row and row[0]:
            return str(row[0]).strip()
    except Exception:
        db.rollback()
    import os
    env_default = (os.getenv("MS_MAILBOX_NEXUS") or "").strip()
    return env_default or None


@router.post("/reply-to-lead")
async def reply_to_lead(
    payload: ReplyToLeadRequest,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    if not (payload.query or "").strip():
        raise HTTPException(status_code=400, detail="query is required")

    # SECURITY: the workspace is ALWAYS the authenticated caller's own — never
    # the client-supplied `payload.workspace_id` (which would let any caller
    # read another tenant's KB/product data and send from their mailbox).
    ws_id = int(workspace.id if hasattr(workspace, "id") else workspace)

    classification = intent_classifier.classify(payload.query)
    suggested = rag_reply.generate(
        inbound_text=payload.query,
        intent=classification.intent,
        workspace_id=ws_id,
        db=db,
    )

    result: Dict[str, Any] = {
        "intent": classification.intent,
        "confidence": classification.confidence,
        "suggested_reply": suggested,
        "sent": False,
        "mailbox": None,
        "subject": None,
    }

    if not payload.send:
        return result

    mailbox = (payload.mailbox or "").strip() or _resolve_mailbox(db, ws_id)
    if not mailbox:
        raise HTTPException(
            status_code=400,
            detail="no mailbox configured (pass `mailbox` or set nexus_settings.ms_mailbox_address / MS_MAILBOX_NEXUS)",
        )

    subject = (payload.subject or "").strip() or "Re: your message"
    # Sign the reply with the workspace's product/brand name (no booking CTA).
    _brand = ""
    try:
        _bp = db.execute(
            text("SELECT name FROM nexus_products WHERE workspace_id = :w ORDER BY id DESC LIMIT 1"),
            {"w": ws_id},
        ).first()
        _brand = (_bp[0] or "").strip() if _bp else ""
    except Exception:  # noqa: BLE001
        db.rollback()
    send_body = ensure_signature(suggested, _brand)
    sent_id = await ms_graph_client.send_mail(
        mailbox,
        to_email=payload.lead_email,
        subject=subject,
        body_text=send_body,
    )
    result["sent"] = bool(sent_id)
    result["mailbox"] = mailbox
    result["subject"] = subject
    if not sent_id:
        raise HTTPException(status_code=502, detail="Graph send_mail failed")
    return result


__all__ = ["router", "_ingest_inbound_message"]
