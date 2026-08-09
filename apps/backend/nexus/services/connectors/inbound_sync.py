"""Reply agent for CONNECTED mailboxes (Outlook today).

Same job as `ms_mail_sync.sync_ms_inbound_mail` — read replies, match to a
lead, classify intent, stop follow-ups, draft a grounded reply, and auto-send
it — but the transport is each workspace's OWN connected mailbox (delegated
OAuth token on nexus_conversation_accounts), not a single app-only mailbox.

All the heavy lifting (dedup, lead resolution, intent classification, RAG
draft, suppression/loop guards, outbound persistence) is reused from
`ms_mail_sync` and `routers/inbound`. Only the read/send/mark-read calls are
swapped to the connector's delegated functions.

Driven by the scheduler tick (same cadence as the email sequencer).
Kill-switch: NEXUS_AUTO_REPLY_ENABLED=false stages drafts without sending.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from nexus.models_phase4 import NexusConversationAccount
from nexus.services.connectors import outlook
from nexus.services.reply_cta import ensure_signature
from nexus.services.ms_mail_sync import (
    _brand_for_sequence,
    _PLACEHOLDER_REPLY_PREFIX,
    _already_ingested,
    _body_text_from,
    _clean_reply_subject,
    _flatten_recipients,
    _calendar_response_type,
    _is_calendar_system_message,
    _is_suppressed,
    _lead_status_blocks_autoreply,
    _recent_outbound_exists,
    _resolve_lead,
    _resolve_lead_sequence_for_lead,
    _resolve_sequence_for_reply,
    _upsert_inbound_lead,
)

logger = logging.getLogger("nexus.services.connectors.inbound_sync")


def _connected_accounts(db: Session):
    """Active OAuth mailboxes with a usable refresh token (Outlook for now)."""
    return (
        db.query(NexusConversationAccount)
        .filter(
            NexusConversationAccount.provider == "outlook",
            NexusConversationAccount.status == "active",
            NexusConversationAccount.refresh_token.isnot(None),
        )
        .all()
    )


def sync_connected_inbound(db: Session) -> Dict[str, Any]:
    """Poll every connected mailbox, ingest lead replies, auto-reply when the
    guards pass. Returns a summary dict for logging."""
    accounts = _connected_accounts(db)
    summary: Dict[str, Any] = {
        "mailboxes": len(accounts),
        "messages_seen": 0,
        "matched_leads": 0,
        "ingested": 0,
        "auto_replied": 0,
        "auto_reply_skipped": 0,
        "marked_read": 0,
        "skipped_no_match": 0,
        "skipped_duplicate": 0,
        "skipped_meeting_response": 0,
        "errors": 0,
    }
    if not accounts:
        return summary

    from nexus.routers.inbound import _ingest_inbound_message

    autoreply_globally_on = (
        os.getenv("NEXUS_AUTO_REPLY_ENABLED", "true").strip().lower() == "true"
    )

    for acct in accounts:
        workspace_id = acct.workspace_id
        mailbox = acct.email_address
        token = outlook.get_valid_access_token(db, acct)
        if not token:
            # Consent lost — needs reconnect. Skip; don't error-spam.
            continue

        try:
            # Sweep by RECENT TIME WINDOW, not just isRead=false. A mail
            # client (or a "mark all as read" rule, or even just opening the
            # message in a preview pane) can flip isRead before this poller
            # ever runs, which made isRead-only filtering silently and
            # permanently drop real replies. Safe to widen: ingestion already
            # dedupes by internetMessageId (_already_ingested), so re-seeing
            # an already-processed message here is a no-op, not a double-send.
            since = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
            messages = outlook.list_messages(token, only_unread=False, since_iso=since, top=25)
        except Exception as e:  # noqa: BLE001
            logger.warning("connector list_messages failed ws=%s mbx=%s: %s", workspace_id, mailbox, e)
            summary["errors"] += 1
            continue

        for msg in messages or []:
            summary["messages_seen"] += 1
            try:
                graph_id = msg.get("id") or ""
                internet_id = msg.get("internetMessageId") or ""
                from_addr = (
                    ((msg.get("from") or {}).get("emailAddress") or {}).get("address") or ""
                ).strip()
                if not internet_id or not from_addr:
                    continue
                # /me/messages can surface the mailbox's OWN sent copies (Sent
                # Items or a self-CC), not just genuine inbound replies. Under
                # only_unread=True this was masked (our own sent copies are
                # already read); the only_unread=False time-window sweep
                # exposes it — without this guard, our own outbound mail gets
                # misclassified as a fake "reply from ourselves." Skip
                # anything from the mailbox itself; mark it read so it stops
                # resurfacing every sweep.
                if from_addr.lower() == mailbox.lower():
                    if graph_id:
                        outlook.mark_read(token, graph_id)
                    continue

                # A calendar RSVP ("Accepted: <demo>") auto-sent by an attendee's
                # client — not a real reply, so it's dropped (never ingested /
                # auto-replied). But if it's the prospect's own response, record
                # the OUTCOME on their booking so the journey shows the signal.
                if _is_calendar_system_message(msg):
                    rtype = _calendar_response_type(msg)
                    if rtype:
                        try:
                            from nexus.services.ms_bookings_sync import record_calendar_response

                            record_calendar_response(
                                db, workspace_id=workspace_id, attendee_email=from_addr, response=rtype
                            )
                        except Exception:  # noqa: BLE001
                            logger.warning("calendar RSVP record failed", exc_info=True)
                    summary["skipped_meeting_response"] = (
                        summary.get("skipped_meeting_response", 0) + 1
                    )
                    if graph_id and outlook.mark_read(token, graph_id):
                        summary["marked_read"] += 1
                    continue

                if _already_ingested(db, internet_id):
                    summary["skipped_duplicate"] += 1
                    if graph_id and outlook.mark_read(token, graph_id):
                        summary["marked_read"] += 1
                    continue

                global_lead_id = _resolve_lead(db, from_addr)
                if global_lead_id is None:
                    summary["skipped_no_match"] += 1
                    if graph_id and outlook.mark_read(token, graph_id):
                        summary["marked_read"] += 1
                    continue

                summary["matched_leads"] += 1
                inbound_lead_id = _upsert_inbound_lead(db, workspace_id, from_addr)
                # Subject-match the reply to the exact sequence (→ product) so
                # per-product reply counts are accurate; falls back internally.
                lead_sequence_id = _resolve_sequence_for_reply(
                    db, global_lead_id, msg.get("subject"), workspace_id=workspace_id
                )

                body_obj = msg.get("body") or {}
                body_ct = (body_obj.get("contentType") or "").lower()
                incoming_subject = msg.get("subject") or ""

                # Ingest = thread + classify intent + RAG draft + STOP
                # remaining follow-ups (sets lead/sequence status='replied').
                ingest_result = _ingest_inbound_message(
                    db,
                    workspace_id=workspace_id,
                    from_email=from_addr,
                    to_email=_flatten_recipients(msg.get("toRecipients")),
                    subject=incoming_subject,
                    body_text=_body_text_from(msg),
                    body_html=body_obj.get("content") if body_ct == "html" else None,
                    message_id_header=internet_id,
                    in_reply_to_header=None,
                    references_header=None,
                    gmail_thread_id=None,
                    resend_thread_id=msg.get("conversationId"),
                    raw_payload=None,
                    inbound_lead_id=inbound_lead_id,
                    lead_sequence_id=lead_sequence_id,
                )
                summary["ingested"] += 1

                inbound_msg_id = ingest_result.get("message_id")
                thread_id = ingest_result.get("thread_id")
                inbound_intent = (ingest_result.get("intent") or "").upper()

                # ── Auto-reply guards (cheap checks first) ───────────────
                # We reply to EVERY intent with its own tone (incl. OUT_OF_OFFICE
                # + UNSUBSCRIBE). Those two are "ack" intents — a single reply
                # (OOO: "we'll follow up when you're back"; unsubscribe: a removal
                # confirmation) that must send ONCE even though the message just
                # triggered its own suppression / status change, so they bypass the
                # suppression + lead-status guards. The 1-hour loop cap ALWAYS
                # applies, so an auto-responder can never cause a reply ping-pong.
                _ack_intent = inbound_intent in ("UNSUBSCRIBE", "OUT_OF_OFFICE")
                skip_reason: Optional[str] = None
                if not autoreply_globally_on:
                    skip_reason = "env_disabled"
                elif not inbound_msg_id:
                    skip_reason = "no_ingest"
                elif not _ack_intent and _is_suppressed(db, workspace_id, from_addr):
                    skip_reason = "suppressed"
                elif not _ack_intent and _lead_status_blocks_autoreply(db, global_lead_id):
                    skip_reason = "lead_blocked"
                elif _recent_outbound_exists(db, mailbox, from_addr):
                    skip_reason = "loop_cap_1h"

                if skip_reason is not None:
                    summary["auto_reply_skipped"] += 1
                    logger.info("auto-reply skipped ws=%s to=%s reason=%s", workspace_id, from_addr, skip_reason)
                else:
                    suggested: Optional[str] = None
                    reply_subject = _clean_reply_subject(incoming_subject)
                    try:
                        row = db.execute(
                            text("SELECT suggested_reply, subject FROM nexus_inbound_messages WHERE id = :id"),
                            {"id": int(inbound_msg_id)},
                        ).first()
                        if row:
                            suggested = row[0]
                            db_subject = row[1] or incoming_subject
                            reply_subject = _clean_reply_subject(db_subject)
                    except Exception:
                        db.rollback()

                    if not suggested or suggested.startswith(_PLACEHOLDER_REPLY_PREFIX):
                        summary["auto_reply_skipped"] += 1
                        logger.info("auto-reply skipped ws=%s to=%s reason=empty_or_placeholder", workspace_id, from_addr)
                    else:
                        # Sign the reply with the brand/product name; no booking CTA.
                        send_body = ensure_signature(
                            suggested, _brand_for_sequence(db, lead_sequence_id)
                        )
                        # Send the reply FROM the connected mailbox.
                        res = outlook.send_for_account(
                            db,
                            acct,
                            to_email=from_addr,
                            subject=reply_subject,
                            body_text=send_body,
                            in_reply_to=internet_id,
                        )
                        if res.get("ok"):
                            summary["auto_replied"] += 1
                            try:
                                outbound_received = datetime.utcnow() + timedelta(milliseconds=1)
                                db.execute(
                                    text(
                                        "INSERT INTO nexus_inbound_messages "
                                        "(thread_id, direction, from_email, to_email, subject, "
                                        " body_text, message_id_header, in_reply_to_header, "
                                        " received_at, intent, is_read) "
                                        "VALUES (:tid, 'outbound', :fr, :to, :sub, :body, :mid, "
                                        " :inrt, :rcv, 'OUTBOUND', TRUE)"
                                    ),
                                    {
                                        "tid": thread_id,
                                        "fr": mailbox,
                                        "to": from_addr,
                                        "sub": reply_subject,
                                        "body": send_body,
                                        "mid": f"connector-auto-{uuid4()}",
                                        "inrt": internet_id,
                                        "rcv": outbound_received,
                                    },
                                )
                                if thread_id is not None:
                                    db.execute(
                                        text(
                                            "UPDATE nexus_inbound_threads SET "
                                            " message_count = COALESCE(message_count,0) + 1, "
                                            " last_message_at = :rcv WHERE id = :tid"
                                        ),
                                        {"tid": thread_id, "rcv": outbound_received},
                                    )
                                db.commit()
                            except Exception as exc:  # noqa: BLE001
                                db.rollback()
                                logger.warning("connector outbound persist failed ws=%s: %s", workspace_id, exc)

                if graph_id and outlook.mark_read(token, graph_id):
                    summary["marked_read"] += 1

            except Exception as e:  # noqa: BLE001
                logger.warning("connector inbound: failed msg ws=%s id=%s: %s", workspace_id, msg.get("id"), e)
                db.rollback()
                summary["errors"] += 1
                continue

    return summary
