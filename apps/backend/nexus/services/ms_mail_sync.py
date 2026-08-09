"""MS Outlook (Microsoft Graph) inbound-mail polling sync.

Mirrors `ms_bookings_sync.py` exactly — same auth (MSAL app-only via
`ms_graph_client.get_access_token`), same per-workspace targeting via
`nexus_settings`, same scheduler-driven cadence.

Flow per polled message
-----------------------
  1. List unread messages in each `(workspace_id, mailbox)` target.
  2. Dedup on `internetMessageId` against existing
     `nexus_inbound_messages.message_id_header`.
  3. Resolve `from.emailAddress.address` → existing `nexus_global_leads` row.
     - No match  → mark read on Graph, skip. No DB writes for non-leads.
     - Match     → UPSERT a `nexus_inbound_leads` row (so the GTM-journey
                   timeline JOIN can find this message), then call
                   `routers/inbound._ingest_inbound_message(...)` which
                   threads, classifies intent, runs the RAG reply agent,
                   and persists the inbound row with `suggested_reply`.
  4. Auto-reply: if every guard clause passes, send the suggested reply
     via Graph and persist a sibling `nexus_inbound_messages` row with
     `direction='outbound'` so the journey timeline shows our reply.
     Guard list (any FAIL → skip send, message stays ingested):
       a. env: NEXUS_AUTO_REPLY_ENABLED must be `'true'` (default true).
       b. intent NOT IN {UNSUBSCRIBE, OUT_OF_OFFICE}.
       c. sender NOT in `nexus_suppression_list` for this workspace.
       d. `nexus_global_leads.status` NOT IN {unsubscribed, bounced}.
       e. inbound subject does not match the OOO subject regex.
       f. no outbound row to this sender in the last hour (1h loop cap).
       g. suggested_reply is non-empty AND not the rag_reply placeholder
          sentinel string ("Auto-draft placeholder...").
  5. Mark the Graph message read only AFTER ingestion (and optional send)
     completes. A failure leaves it unread for the next tick to retry.

Multi-mailbox-aware
-------------------
Per-workspace config lives in `nexus_settings.settings` JSON:
    {
      "ms_mailbox_address": "sales@yourdomain.com"   // required (string for now)
    }

To add more mailboxes per workspace in the future, change the type of
`ms_mailbox_address` to a list of strings — `_enumerate_targets` already
yields one row per address, so the upstream poll loop is ready.

Operator-wide fallback: `MS_MAILBOX_NEXUS` env var binds to the same
default workspace `ms_bookings_sync` uses (`MS_BOOKING_DEFAULT_WORKSPACE_ID`
or seed user's workspace).

Emergency kill-switch: `NEXUS_AUTO_REPLY_ENABLED=false` halts auto-send
across every workspace without a code deploy. Inbound messages still
ingest and stage with `suggested_reply` set — the agent just doesn't send.

Idempotent: dedup on `(workspace_id, internetMessageId)`.
"""

from __future__ import annotations

import html as _html_lib
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from nexus.services import ms_graph_client
from nexus.services.reply_cta import ensure_signature


logger = logging.getLogger("nexus.services.ms_mail_sync")


# ─── Auto-reply guard constants ──────────────────────────────────────────────


# rag_reply.generate falls back to this prefix when Gemini fails — we
# must NOT send that string to a real lead.
_PLACEHOLDER_REPLY_PREFIX = "Auto-draft placeholder"


# ─── Target enumeration ──────────────────────────────────────────────────────


def _resolve_default_workspace(db: Session) -> Optional[int]:
    """Same resolution logic as ms_bookings_sync — keep behaviour identical so
    operator's single Microsoft tenant maps to one workspace consistently."""
    default_ws = (os.getenv("MS_BOOKING_DEFAULT_WORKSPACE_ID") or "").strip()
    if default_ws.isdigit():
        return int(default_ws)
    seed_email = (os.getenv("SEED_USER_EMAIL") or "").strip().lower()
    if seed_email:
        try:
            row = db.execute(
                text(
                    "SELECT w.id FROM nexus_workspaces w "
                    "JOIN users u ON u.id = w.owner_id "
                    "WHERE LOWER(u.email) = :em "
                    "ORDER BY w.id LIMIT 1"
                ),
                {"em": seed_email},
            ).first()
            if row:
                return int(row[0])
        except Exception:
            db.rollback()
    try:
        row = db.execute(text("SELECT id FROM nexus_workspaces ORDER BY id LIMIT 1")).first()
        if row:
            return int(row[0])
    except Exception:
        db.rollback()
    return None


def _enumerate_targets(db: Session) -> List[Tuple[int, str]]:
    """Return [(workspace_id, mailbox_address), ...].

    Per-workspace `nexus_settings.settings.ms_mailbox_address` wins; falls
    back to `MS_MAILBOX_NEXUS` env applied to the default workspace.
    """
    configured: Dict[int, str] = {}
    try:
        rows = db.execute(
            text(
                "SELECT workspace_id, COALESCE(settings->>'ms_mailbox_address', '') "
                "FROM nexus_settings"
            )
        ).fetchall()
    except Exception as e:
        logger.debug("nexus_settings query failed: %s", e)
        db.rollback()
        rows = []

    for ws_id, mbx in rows:
        mbx = (mbx or "").strip()
        if mbx:
            configured[int(ws_id)] = mbx

    targets: List[Tuple[int, str]] = list(configured.items())

    env_default = (os.getenv("MS_MAILBOX_NEXUS") or "").strip()
    if env_default:
        ws_id = _resolve_default_workspace(db)
        if ws_id is not None and ws_id not in configured:
            targets.append((ws_id, env_default))

    return targets


# ─── Lead resolution (same convention as ms_bookings_sync) ───────────────────


def _resolve_lead(db: Session, email: str) -> Optional[int]:
    """Resolve a sender email to a `nexus_global_leads.id`. Returns None when
    no match — the caller should mark-read-and-skip in that case."""
    if not email:
        return None
    try:
        row = db.execute(
            text(
                "SELECT id FROM nexus_global_leads "
                "WHERE LOWER(email) = :em LIMIT 1"
            ),
            {"em": email.lower()},
        ).first()
    except Exception as e:
        logger.debug("_resolve_lead failed: %s", e)
        db.rollback()
        return None
    return int(row[0]) if row else None


def _resolve_lead_sequence_for_lead(
    db: Session, global_lead_id: int, workspace_id: Optional[int] = None
) -> Optional[int]:
    """Find the most-recent `nexus_lead_sequences.id` for this lead.

    Used so the inbound reply lands on a thread whose `lead_sequence_id`
    is populated — that's the only knob the downstream RAG agent uses
    to attribute the inbound to a specific product
    (`nexus_lead_sequences.campaign_id → nexus_campaigns.product_id`).

    Falls back to None when the lead isn't enrolled in any sequence; the
    RAG agent then uses its own workspace-most-recent-campaign heuristic.
    """
    if global_lead_id is None:
        return None
    # ISOLATION: scope to the reply's workspace so a shared lead's sequence in
    # ANOTHER tenant can never be picked (which would attribute the reply — and
    # the auto-generated RAG reply's product grounding — to the wrong workspace).
    ws_clause = "AND workspace_id = :ws " if workspace_id is not None else ""
    params = {"gid": int(global_lead_id)}
    if workspace_id is not None:
        params["ws"] = int(workspace_id)
    try:
        row = db.execute(
            text(
                "SELECT id FROM nexus_lead_sequences "
                "WHERE lead_id = :gid "
                + ws_clause
                + "ORDER BY "
                # 'active' rows win over 'completed' / 'halted'; within a
                # status bucket, most-recent enrollment wins.
                "  CASE WHEN status = 'active' THEN 0 ELSE 1 END, "
                "  enrolled_at DESC, id DESC "
                "LIMIT 1"
            ),
            params,
        ).first()
        if row:
            return int(row[0])
    except Exception as e:
        logger.debug("_resolve_lead_sequence_for_lead failed: %s", e)
        db.rollback()
    return None


def _normalize_subject(s: Optional[str]) -> str:
    """Lowercase + strip leading Re:/Fwd:/Fw: prefixes (repeatedly) so a reply
    subject matches the original outbound subject it answers."""
    s = (s or "").strip()
    low = s.lower()
    changed = True
    while changed:
        changed = False
        for pre in ("re:", "fwd:", "fw:", "re :", "fwd :", "fw :"):
            if low.startswith(pre):
                s = s[len(pre):].lstrip()
                low = s.lower()
                changed = True
    return low.strip()


def _resolve_sequence_for_reply(
    db: Session, global_lead_id: Optional[int], subject: Optional[str],
    workspace_id: Optional[int] = None,
) -> Optional[int]:
    """Attribute a reply to the EXACT sequence (→ campaign → product) the
    prospect replied to, using the STORED outbound touchpoint subject — replies
    carry "Re: <original subject>". This drives accurate per-product reply
    counts in analytics. Falls back to the most-recent-active sequence only when
    no subject match exists (e.g. the prospect changed the subject line).
    """
    if global_lead_id is None:
        return None
    norm = _normalize_subject(subject)
    if norm:
        # ISOLATION: only consider outbound touchpoints from THIS workspace, so
        # a reply is never attributed to another tenant's sequence/campaign.
        ws_clause = "AND workspace_id = :ws " if workspace_id is not None else ""
        params = {"gid": int(global_lead_id)}
        if workspace_id is not None:
            params["ws"] = int(workspace_id)
        try:
            rows = db.execute(
                text(
                    "SELECT lead_sequence_id, subject FROM nexus_touchpoints "
                    "WHERE lead_id = :gid AND lead_sequence_id IS NOT NULL "
                    "  AND channel LIKE 'email%' "
                    + ws_clause
                    + "ORDER BY sent_at DESC NULLS LAST, id DESC"
                ),
                params,
            ).fetchall()
            for r in rows:
                if _normalize_subject(r[1]) == norm:
                    return int(r[0])  # exact email the reply answers
        except Exception as e:
            logger.debug("_resolve_sequence_for_reply subject-match failed: %s", e)
            db.rollback()
    # No subject match → previous behaviour (most-recent active sequence).
    return _resolve_lead_sequence_for_lead(db, global_lead_id, workspace_id)


def _upsert_inbound_lead(
    db: Session, workspace_id: int, email: str
) -> Optional[int]:
    """UPSERT into nexus_inbound_leads (workspace_id, email). Returns
    the row id. This is the surrogate the journey timeline JOIN keys on
    (`nexus_inbound_threads.lead_id = nexus_inbound_leads.id`)."""
    if not email or workspace_id is None:
        return None
    try:
        row = db.execute(
            text(
                "INSERT INTO nexus_inbound_leads (workspace_id, email, source) "
                "VALUES (:ws, :em, 'ms_graph') "
                "ON CONFLICT (workspace_id, email) "
                "DO UPDATE SET email = EXCLUDED.email "
                "RETURNING id"
            ),
            {"ws": int(workspace_id), "em": email.lower()},
        ).first()
        if row:
            db.commit()
            return int(row[0])
    except Exception as e:
        logger.debug("inbound_lead upsert failed: %s", e)
        db.rollback()
    return None


def _is_suppressed(db: Session, workspace_id: int, email: str) -> bool:
    """Workspace-scoped suppression check. Safe-fails to False."""
    if not email or workspace_id is None:
        return False
    try:
        row = db.execute(
            text(
                "SELECT 1 FROM nexus_suppression_list "
                "WHERE workspace_id = :ws AND LOWER(email) = :em LIMIT 1"
            ),
            {"ws": int(workspace_id), "em": email.lower()},
        ).first()
        return row is not None
    except Exception:
        db.rollback()
        return False


def _lead_status_blocks_autoreply(db: Session, global_lead_id: int) -> bool:
    """True when the global lead's status is one that should suppress
    auto-replies (already unsubscribed or bounced)."""
    if global_lead_id is None:
        return False
    try:
        row = db.execute(
            text("SELECT status FROM nexus_global_leads WHERE id = :id"),
            {"id": int(global_lead_id)},
        ).first()
        if not row:
            return False
        return (row[0] or "").lower() in {"unsubscribed", "bounced"}
    except Exception:
        db.rollback()
        return False


def _recent_outbound_exists(
    db: Session, mailbox: str, to_email: str
) -> bool:
    """1h reply-loop cap: True if we've already sent an outbound reply
    from `mailbox` to `to_email` within the last hour."""
    if not to_email or not mailbox:
        return False
    try:
        row = db.execute(
            text(
                "SELECT 1 FROM nexus_inbound_messages "
                "WHERE direction = 'outbound' "
                "  AND LOWER(to_email) = :to_em "
                "  AND LOWER(from_email) = :mbx "
                "  AND received_at > NOW() - INTERVAL '1 hour' "
                "LIMIT 1"
            ),
            {"to_em": to_email.lower(), "mbx": mailbox.lower()},
        ).first()
        return row is not None
    except Exception:
        db.rollback()
        return False


# ─── Dedup ───────────────────────────────────────────────────────────────────


def _already_ingested(db: Session, internet_message_id: str) -> bool:
    """Returns True if a `nexus_inbound_messages` row already exists with
    this `message_id_header`. Safe-fails to False so a transient DB error
    doesn't permanently skip a message."""
    if not internet_message_id:
        return False
    try:
        row = db.execute(
            text(
                "SELECT 1 FROM nexus_inbound_messages "
                "WHERE message_id_header = :mid LIMIT 1"
            ),
            {"mid": internet_message_id},
        ).first()
    except Exception:
        db.rollback()
        return False
    return row is not None


# ─── Graph message → ingest call shape ───────────────────────────────────────


def _flatten_recipients(recips: Optional[List[Dict[str, Any]]]) -> str:
    if not recips:
        return ""
    parts: List[str] = []
    for r in recips:
        addr = ((r or {}).get("emailAddress") or {}).get("address") or ""
        if addr:
            parts.append(addr)
    return ", ".join(parts)


def _html_to_text(html: str) -> str:
    """Minimal HTML → plaintext for stored inbound bodies. Drops script/style,
    turns <br>/<p> into line breaks, strips remaining tags, decodes the common
    entities. Keeps the whole message (no length cap)."""
    if not html:
        return ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</p\s*>", "\n\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html_lib.unescape(s)  # decode all named/numeric entities (&mdash;, &rsquo;, …)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# Markers that begin the quoted ORIGINAL message a mail client appends below
# a reply. We cut everything from the first one onward so only the new text
# the sender actually typed is stored/classified/shown.
_ORIG_MSG_RE = re.compile(r"(?i)^-{2,}\s*Original Message\s*-{2,}\s*$")
_ON_WROTE_RE = re.compile(r"(?i)^On\s.+\bwrote:\s*$")
_FROM_HDR_RE = re.compile(r"(?im)^From:\s?\S")
_SENT_HDR_RE = re.compile(r"(?im)^\s*(Sent|Date):\s?\S")
_SUBJ_HDR_RE = re.compile(r"(?im)^\s*Subject:\s?\S")
_UNDERSCORE_RE = re.compile(r"^_{5,}\s*$")


def _strip_quoted_history(body: str) -> str:
    """Drop the quoted original message clients append below a reply.

    Handles the common shapes: the Outlook "From: … Sent: … Subject: …"
    forwarded-header block (only when all three appear together, so a bare
    "From:" in real prose isn't a false positive), Gmail's "On … wrote:",
    "----- Original Message -----", and Outlook's underscore divider.

    Conservative on purpose: a "From:" alone won't trigger it, and if a marker
    is found but there's nothing above it (the whole message IS the quote),
    the original is returned unchanged rather than blanking the body.
    """
    if not body:
        return body
    lines = body.split("\n")
    cut = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s:
            continue
        if _ON_WROTE_RE.match(s) or _ORIG_MSG_RE.match(s):
            cut = i
            break
        if _FROM_HDR_RE.match(s):
            window = "\n".join(lines[i:i + 6])
            if _SENT_HDR_RE.search(window) and _SUBJ_HDR_RE.search(window):
                cut = i
                break
        if _UNDERSCORE_RE.match(s):
            window = "\n".join(lines[i:i + 8])
            if _FROM_HDR_RE.search(window) or _SUBJ_HDR_RE.search(window):
                cut = i
                break
    if cut is None:
        # Mobile clients glue the quoted header onto the signature line, e.g.
        # "Get Outlook for AndroidFrom: X <..>\nSent: ..\nSubject: ..", so the
        # From: isn't at line start. Scan for the whole header cluster anywhere.
        m = re.search(
            r"(?is)From:\s?\S.{0,300}?(?:Sent|Date):\s?\S.{0,300}?Subject:\s?\S",
            body,
        )
        if m:
            head = body[:m.start()].rstrip()
            head = re.sub(
                r"(?i)(?:Get|Sent from)\s+Outlook(?:\s+for\s+\w+)?\s*$", "", head
            ).strip()
            if head:
                return head
        return body
    head = "\n".join(lines[:cut]).strip()
    return head if head else body


def _clean_reply_subject(subject: str) -> str:
    """Build a tidy reply subject — exactly one "Re:" + the core subject.

    Inbound auto-replies stack their own prefixes on top of the original, e.g.
    "Out Of Office Re: <orig>" or "Departure Notification & Thank You Re:
    <orig>". Naively prepending "Re:" produced the messy
    "Re: <prefix> Re: <orig>". We take the text after the LAST "Re:" token as
    the real subject and prepend a single "Re:"."""
    s = (subject or "").strip()
    if not s:
        return "Re: your message"
    parts = re.split(r"(?i)\bre\s*:\s*", s)
    core = (parts[-1] if parts else s).strip() or s
    return f"Re: {core}"


def _brand_for_sequence(db: Session, lead_sequence_id: Optional[int]) -> str:
    """Brand/product name to sign the reply with, resolved from the reply's
    sequence → campaign → product. Best-effort — returns '' if unresolved."""
    if not lead_sequence_id:
        return ""
    try:
        row = db.execute(
            text(
                """SELECT p.name
                     FROM nexus_lead_sequences ls
                     JOIN nexus_campaigns c ON c.id = ls.campaign_id
                     JOIN nexus_products p ON p.id = c.product_id
                    WHERE ls.id = :id"""
            ),
            {"id": lead_sequence_id},
        ).first()
        return (row[0] or "").strip() if row else ""
    except Exception:  # noqa: BLE001
        db.rollback()
        return ""


def _body_text_from(msg: Dict[str, Any]) -> str:
    """Full message text. Prefer the COMPLETE body.content (Graph returns the
    whole message) over bodyPreview — the latter is capped by Graph at ~255
    chars and truncates real replies. body.content is usually HTML, so strip it
    to plaintext; fall back to bodyPreview only when there is no body content."""
    body = msg.get("body") or {}
    content = (body.get("content") or "").strip()
    if content:
        is_html = (body.get("contentType") or "").lower() == "html" or "<" in content
        plain = _html_to_text(content) if is_html else content
        # Keep only what the sender just typed, not the quoted email they
        # replied to (which otherwise gets stored, classified, and shown on
        # the Flow tab underneath their actual reply).
        return _strip_quoted_history(plain)
    return (msg.get("bodyPreview") or "").strip()


# Calendar RSVP subjects Outlook auto-generates when an attendee responds to an
# invite ("Accepted: <subject>", "Declined: …", "Tentative: …", cancellations).
# English-tenant fallback only — the primary signal is the Graph @odata.type.
_CALENDAR_SUBJECT_RE = re.compile(
    r"^\s*(accepted|declined|tentative(?:ly accepted)?|cancell?ed):",
    re.IGNORECASE,
)


def _is_calendar_system_message(msg: Dict[str, Any]) -> bool:
    """True for a meeting request/response an attendee's calendar mailed us —
    NOT a real reply.

    When the demo invite is accepted, the attendee's client auto-sends an
    "Accepted: <subject>" notification back to the rep's mailbox. Ingesting it
    stores a junk row, classifies it, and can fire a canned auto-reply. Two
    forms occur:
      - Outlook native meeting responses (often empty-bodied), and
      - Google Calendar acceptance emails (subject "Accepted: …", WITH a body).

    Signals:
      1. Graph surfaces native meeting messages as derived types, so
         @odata.type is 'eventMessage' / 'eventMessageRequest' /
         'eventMessageResponse'.
      2. An RSVP subject prefix ("Accepted:/Declined:/Tentative:/Canceled:").
         The regex is anchored at the start and requires the colon, so a genuine
         threaded reply ("Re: <subject>") and prose like "Accepted our proposal"
         never match — no body check needed, which is what lets a body-carrying
         Google Calendar acceptance be caught too.
    """
    otype = (msg.get("@odata.type") or "").lower()
    if "eventmessage" in otype:
        return True
    if _CALENDAR_SUBJECT_RE.match(msg.get("subject") or ""):
        return True
    return False


_CALENDAR_RESPONSE_RE = [
    ("accepted", re.compile(r"^\s*accepted:", re.I)),
    ("tentative", re.compile(r"^\s*tentative(?:ly accepted)?:", re.I)),
    ("declined", re.compile(r"^\s*declined:", re.I)),
    ("cancelled", re.compile(r"^\s*cancell?ed:", re.I)),
]


def _calendar_response_type(msg: Dict[str, Any]) -> Optional[str]:
    """The prospect's RSVP as 'accepted'|'declined'|'tentative'|'cancelled', or
    None if this calendar message isn't a response (e.g. a meeting request). Used
    to record the OUTCOME (the message itself is still dropped, never ingested)."""
    otype = (msg.get("@odata.type") or "").lower()
    if "meetingaccepted" in otype:
        return "accepted"
    if "meetingtentative" in otype:
        return "tentative"
    if "meetingdeclined" in otype:
        return "declined"
    subj = msg.get("subject") or ""
    for kind, rx in _CALENDAR_RESPONSE_RE:
        if rx.match(subj):
            return kind
    return None


# ─── Top-level sync ──────────────────────────────────────────────────────────


async def sync_ms_inbound_mail(db: Session) -> Dict[str, Any]:
    """Poll every configured `(workspace, mailbox)` target. For each new,
    lead-matched message: ingest, run the RAG reply agent, then auto-send
    the reply via Graph if every guard clause passes. Return a summary
    dict for logging."""
    targets = _enumerate_targets(db)
    summary: Dict[str, Any] = {
        "targets": len(targets),
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
    if not targets:
        return summary

    # Imported here (not at module top) to mirror ms_bookings_sync's pattern
    # and avoid circular import risk with routers/inbound.py.
    from nexus.routers.inbound import _ingest_inbound_message

    # Global kill-switch — flip via env without a code deploy. Default ON.
    autoreply_globally_on = (
        os.getenv("NEXUS_AUTO_REPLY_ENABLED", "true").strip().lower() == "true"
    )

    for workspace_id, mailbox in targets:
        try:
            # Sweep by RECENT TIME WINDOW, not just isRead=false — see
            # matching comment in connectors/inbound_sync.py. A message
            # marked read before this poller ever ran would otherwise be
            # silently dropped forever; ingestion's dedupe-by-
            # internetMessageId makes it safe to re-see already-processed
            # messages here.
            since = (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
            messages = await ms_graph_client.list_messages(
                mailbox, only_unread=False, since_iso=since, top=50
            )
        except Exception as e:
            logger.warning(
                "list_messages failed ws=%s mailbox=%s: %s",
                workspace_id, mailbox, e,
            )
            summary["errors"] += 1
            continue

        for msg in messages or []:
            summary["messages_seen"] += 1
            try:
                graph_id = msg.get("id") or ""
                internet_id = msg.get("internetMessageId") or ""
                from_addr = (
                    ((msg.get("from") or {}).get("emailAddress") or {}).get("address")
                    or ""
                ).strip()

                if not internet_id or not from_addr:
                    continue

                # /me/messages can surface the mailbox's OWN sent copies, not
                # just genuine inbound replies — see matching comment in
                # connectors/inbound_sync.py. Masked under only_unread=True;
                # exposed by the only_unread=False time-window sweep.
                if from_addr.lower() == mailbox.lower():
                    if graph_id:
                        await ms_graph_client.mark_message_read(mailbox, graph_id)
                    continue

                # A calendar RSVP ("Accepted: <demo>") an attendee's client
                # auto-sends — not a real reply, so it's dropped (never ingested,
                # classified, or auto-replied to). BUT if it's the prospect's own
                # response we record the OUTCOME on their booking (accepted →
                # confirmed; declined → cancelled + reopen follow-ups) so the
                # journey shows the signal.
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
                    summary["skipped_meeting_response"] += 1
                    if graph_id:
                        ok = await ms_graph_client.mark_message_read(mailbox, graph_id)
                        if ok:
                            summary["marked_read"] += 1
                    continue

                if _already_ingested(db, internet_id):
                    summary["skipped_duplicate"] += 1
                    # Still mark read — we've handled this one before.
                    if graph_id:
                        ok = await ms_graph_client.mark_message_read(mailbox, graph_id)
                        if ok:
                            summary["marked_read"] += 1
                    continue

                # Lead-DB gate: only senders we know about get any DB
                # writes or replies. Unmatched senders are dropped on
                # the floor (mark-read only — Microsoft-side state).
                global_lead_id = _resolve_lead(db, from_addr)
                if global_lead_id is None:
                    summary["skipped_no_match"] += 1
                    if graph_id:
                        ok = await ms_graph_client.mark_message_read(mailbox, graph_id)
                        if ok:
                            summary["marked_read"] += 1
                    continue

                summary["matched_leads"] += 1

                # UPSERT inbound_lead so the GTM-journey timeline JOIN
                # (thread.lead_id = inbound_lead.id) starts to fire on
                # this message. Separate id from global_lead_id.
                inbound_lead_id = _upsert_inbound_lead(
                    db, workspace_id, from_addr
                )

                # Attribute this reply to the EXACT sequence (→ campaign →
                # product) it answers, by matching the reply subject against the
                # stored outbound touchpoint subjects. Drives per-product reply
                # counts in analytics. Falls back to most-recent-active sequence.
                lead_sequence_id = _resolve_sequence_for_reply(
                    db, global_lead_id, msg.get("subject"), workspace_id=workspace_id
                )

                # Graph doesn't expose In-Reply-To natively in the message
                # body — only via $expand=internetMessageHeaders. Threading
                # works fine via conversationId, which the thread matcher
                # uses through the resend_thread_id slot.
                body_obj = msg.get("body") or {}
                body_ct = (body_obj.get("contentType") or "").lower()
                incoming_subject = msg.get("subject") or ""
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

                # ── Auto-reply guard list ───────────────────────────────
                # Any failing clause → skip send. Order is cheap-checks-first
                # so we short-circuit before hitting any DB query when we can.
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
                    logger.info(
                        "auto-reply skipped ws=%s to=%s reason=%s",
                        workspace_id, from_addr, skip_reason,
                    )
                else:
                    suggested: Optional[str] = None
                    reply_subject = _clean_reply_subject(incoming_subject)
                    try:
                        row = db.execute(
                            text(
                                "SELECT suggested_reply, subject "
                                "FROM nexus_inbound_messages WHERE id = :id"
                            ),
                            {"id": int(inbound_msg_id)},
                        ).first()
                        if row:
                            suggested = row[0]
                            db_subject = row[1] or incoming_subject
                            reply_subject = _clean_reply_subject(db_subject)
                    except Exception:
                        db.rollback()

                    # Final pre-send guard: empty suggestion or the
                    # rag_reply degraded-mode placeholder. Never send
                    # placeholder text to a real lead.
                    if not suggested or suggested.startswith(
                        _PLACEHOLDER_REPLY_PREFIX
                    ):
                        summary["auto_reply_skipped"] += 1
                        logger.info(
                            "auto-reply skipped ws=%s to=%s reason=empty_or_placeholder",
                            workspace_id, from_addr,
                        )
                    else:
                        # Sign the reply with the brand/product name (the model
                        # ends on "Best," and is told not to sign, so the sender
                        # name is otherwise inconsistent). No booking-CTA link.
                        send_body = ensure_signature(
                            suggested, _brand_for_sequence(db, lead_sequence_id)
                        )
                        sent_id = await ms_graph_client.send_mail(
                            mailbox,
                            to_email=from_addr,
                            subject=reply_subject,
                            body_text=send_body,
                            in_reply_to=internet_id,
                        )
                        if sent_id:
                            summary["auto_replied"] += 1
                            # Best-effort outbound persist + thread bump.
                            # Nested try so a DB failure here does NOT
                            # block mark_read — the email is already sent.
                            try:
                                outbound_received = (
                                    datetime.utcnow()
                                    + timedelta(milliseconds=1)
                                )
                                synthetic_mid = f"ms-auto-{uuid4()}"
                                db.execute(
                                    text(
                                        "INSERT INTO nexus_inbound_messages "
                                        "(thread_id, direction, from_email, "
                                        " to_email, subject, body_text, "
                                        " message_id_header, in_reply_to_header, "
                                        " received_at, intent, is_read) "
                                        "VALUES (:tid, 'outbound', :fr, :to, "
                                        " :sub, :body, :mid, :inrt, :rcv, "
                                        " 'OUTBOUND', TRUE)"
                                    ),
                                    {
                                        "tid": thread_id,
                                        "fr": mailbox,
                                        "to": from_addr,
                                        "sub": reply_subject,
                                        # Store the SIGNED body (no CTA link) so the
                                        # inbox/journey UI shows exactly what was sent.
                                        "body": send_body,
                                        "mid": synthetic_mid,
                                        "inrt": internet_id,
                                        "rcv": outbound_received,
                                    },
                                )
                                if thread_id is not None:
                                    db.execute(
                                        text(
                                            "UPDATE nexus_inbound_threads "
                                            "SET message_count = "
                                            "      COALESCE(message_count,0) + 1, "
                                            "    last_message_at = :rcv "
                                            "WHERE id = :tid"
                                        ),
                                        {
                                            "tid": thread_id,
                                            "rcv": outbound_received,
                                        },
                                    )
                                db.commit()
                            except Exception as exc:
                                db.rollback()
                                logger.warning(
                                    "outbound row persist failed ws=%s to=%s: %s",
                                    workspace_id, from_addr, exc,
                                )

                # Mark read last — only after ingestion (and optional send)
                # finished. Failure earlier leaves it unread for retry.
                if graph_id:
                    ok = await ms_graph_client.mark_message_read(mailbox, graph_id)
                    if ok:
                        summary["marked_read"] += 1

            except Exception as e:
                logger.warning(
                    "ms_mail_sync: failed processing message ws=%s id=%s: %s",
                    workspace_id, msg.get("id"), e,
                )
                db.rollback()
                summary["errors"] += 1
                continue

    return summary
