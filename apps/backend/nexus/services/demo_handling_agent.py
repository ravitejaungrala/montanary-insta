"""Demo-handling agent — booking confirmation + rep notifications.

Legacy: apps/nexus-legacy/server/services/demoHandlingAgent.js
Exports (legacy): handleBookingCreated, confirmBooking

Scenario 7 (email_senarios.md): `book_demo_from_reply` turns a WORDED
confirmation ("Tuesday at 2pm works") into the same real-world outcome as a
calendar-link booking — a real Outlook invite on the rep's calendar + a
`nexus_demo_bookings` row + the unified demo-scheduled status roll-up.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("nexus.services.demo_handling_agent")


def generate_confirm_token() -> str:
    """Legacy: generateConfirmToken() — URL-safe random token."""
    return secrets.token_urlsafe(24)


def format_date_time(date: datetime) -> str:
    """Legacy: formatDateTime(date)."""
    return date.isoformat() if date else ""


async def get_rep_settings() -> Dict[str, Any]:
    """Legacy: getRepSettings() — reads nexus_settings rep info."""
    return {}


def build_rep_notification_email(
    lead_name: str,
    lead_email: str,
    start_time: str,
    confirm_url: str,
    rep: Dict[str, Any],
) -> str:
    """Legacy: buildRepNotificationEmail({...})."""
    return ""


def build_lead_confirmation_email(
    lead_name: str, rep_name: str, start_time: str, booking_link: str
) -> str:
    """Legacy: buildLeadConfirmationEmail({...})."""
    return ""


# Meeting length + interpretation zone for a worded confirmation. The prospect
# says "2pm" but not "2pm in which zone" — we interpret it in the operator's
# configured zone (defaults to IST, matching the sequencer's day-boundary
# assumption) and hand the wall-clock time + zone to Graph, which does the
# offset math. Both overridable via env without a code change.
_DEMO_TIMEZONE = os.getenv("NEXUS_DEMO_TIMEZONE", "Asia/Kolkata")
try:
    _DEMO_DURATION_MIN = int(os.getenv("NEXUS_DEMO_DURATION_MIN", "30"))
except ValueError:
    _DEMO_DURATION_MIN = 30
# Attach a Teams online meeting to the created event? OFF by default: app-only
# Teams meeting creation needs OnlineMeetings.ReadWrite.All + an application
# access policy, without which the Graph event request HANGS. Turn on only on a
# tenant configured for it (a plain calendar invite is created either way).
_DEMO_TEAMS_MEETING = os.getenv("NEXUS_DEMO_TEAMS_MEETING", "false").strip().lower() == "true"


def _run_async(coro):
    """Run an async Graph call from this sync side-effect path, whether or not
    an event loop is already running (the inbound poller is async and calls the
    ingest pipeline synchronously inside it). A bare asyncio.run() would raise
    'cannot be called from a running event loop' in that case, so when a loop is
    live we complete the coroutine on a worker thread instead."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


def _local_to_utc(naive_local: datetime, tz_name: str) -> datetime:
    """Interpret a naive wall-clock time as being in `tz_name` and return the
    equivalent UTC datetime, for the .ics DTSTART/DTEND (which we emit in UTC/Z
    form). zoneinfo handles the real offset, incl. DST for zones that have it."""
    try:
        from zoneinfo import ZoneInfo

        return naive_local.replace(tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc)
    except Exception:  # noqa: BLE001
        # Fallback: assume the configured zone is IST (+5:30), the operator
        # default, if zoneinfo can't resolve the name for some reason.
        return (naive_local - timedelta(hours=5, minutes=30)).replace(tzinfo=timezone.utc)


def _build_ics(
    *,
    uid: str,
    organizer_email: str,
    organizer_name: Optional[str],
    attendee_email: str,
    attendee_name: Optional[str],
    subject: str,
    start_local: datetime,
    end_local: datetime,
    tz_name: str,
    description: str,
) -> str:
    """A minimal but valid iCalendar meeting REQUEST. Sent as a text/calendar
    attachment, Outlook/Gmail/Apple Mail render it as an invite with
    Accept/Decline and add it to the recipient's calendar — a real calendar
    invite using only the Mail.Send permission we already have."""
    def _z(dt: datetime) -> str:
        return _local_to_utc(dt, tz_name).strftime("%Y%m%dT%H%M%SZ")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    org_cn = organizer_name or organizer_email
    att_cn = attendee_name or attendee_email
    desc = (description or "").replace("\n", "\\n")
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "PRODID:-//Pipelyt//NEXUS Demo//EN",
            "VERSION:2.0",
            "CALSCALE:GREGORIAN",
            "METHOD:REQUEST",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{_z(start_local)}",
            f"DTEND:{_z(end_local)}",
            f"SUMMARY:{subject}",
            f"DESCRIPTION:{desc}",
            f"ORGANIZER;CN={org_cn}:mailto:{organizer_email}",
            (
                f"ATTENDEE;CN={att_cn};ROLE=REQ-PARTICIPANT;"
                f"PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{attendee_email}"
            ),
            "SEQUENCE:0",
            "STATUS:CONFIRMED",
            "TRANSP:OPAQUE",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )


def _send_ics_invite(
    db: Session,
    *,
    account_id: int,
    rep_mailbox: str,
    attendee_email: str,
    attendee_name: Optional[str],
    subject: str,
    start_local: datetime,
    end_local: datetime,
    description: str,
) -> bool:
    """Fallback invite path that needs only Mail.Send: email the lead a real
    .ics meeting request FROM the rep's connected mailbox. Returns True if the
    send succeeded."""
    from nexus.models_phase4 import NexusConversationAccount
    from nexus.services.connectors import outlook

    account = (
        db.query(NexusConversationAccount)
        .filter(NexusConversationAccount.id == account_id)
        .first()
    )
    if account is None:
        return False

    ics = _build_ics(
        uid="%s@pipelyt" % uuid.uuid4(),
        organizer_email=rep_mailbox,
        organizer_name=None,
        attendee_email=attendee_email,
        attendee_name=attendee_name,
        subject=subject,
        start_local=start_local,
        end_local=end_local,
        tz_name=_DEMO_TIMEZONE,
        description=description,
    )
    attachment = {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": "invite.ics",
        "contentType": "text/calendar; method=REQUEST",
        "contentBytes": base64.b64encode(ics.encode("utf-8")).decode("ascii"),
    }
    when_str = start_local.strftime("%A %d %b, %I:%M %p")
    body_html = (
        "<p>Great — confirming our time below. I've attached a calendar invite "
        "so it lands on your calendar.</p>"
        "<p><b>%s</b></p>" % when_str
    )
    res = outlook.send_for_account(
        db,
        account,
        to_email=attendee_email,
        subject=subject,
        body_html=body_html,
        attachments=[attachment],
    )
    return bool(res.get("ok"))


def build_invite_body_html(
    db: Session,
    *,
    booking_id: int,
    attendee_name: Optional[str] = None,
    brand: Optional[str] = None,
    fallback_desc: str = "",
    grounded: bool = True,
) -> str:
    """Phase 2: build the calendar-invite body from the stored prospect-facing
    meeting agenda, with a short intro line. Falls back to `fallback_desc` when
    no agenda exists OR when `grounded` is False (thin-KB product → keep the
    one-liner rather than send the prospect a generic agenda)."""
    if not grounded:
        return "<p>%s</p>" % (fallback_desc or "Looking forward to our session.")
    agenda_html = ""
    try:
        from nexus.models_phase6 import MeetingAgenda

        row = (
            db.query(MeetingAgenda)
            .filter(MeetingAgenda.demo_booking_id == booking_id)
            .first()
        )
        agenda_html = (row.agenda_html or "").strip() if row else ""
    except Exception as exc:  # noqa: BLE001
        log.debug("build_invite_body_html: agenda lookup failed: %s", exc)
    if not agenda_html:
        return "<p>%s</p>" % (fallback_desc or "Looking forward to our session.")
    first = (attendee_name or "").strip().split(" ")[0] if attendee_name else "there"
    intro = "Hi %s, looking forward to walking you through %s. Here's what we'll cover:" % (
        first, brand or "the product",
    )
    return "<div>%s\n%s</div>" % ("<p>%s</p>" % intro, agenda_html)


def send_agenda_email(db: Session, *, booking_id: int) -> bool:
    """Phase 2b: for a SELF-SERVICE booking (Calendly / MS Bookings) — where the
    external tool sent the calendar invite, not us — email the prospect the
    agenda as a standalone follow-up. Gated on product-KB grounding, idempotent
    (won't re-send on webhook retries), and best-effort (never raises)."""
    try:
        from nexus.models_phase6 import DemoBooking
        from nexus.models_phase4 import NexusConversationAccount
        from nexus.services import meeting_agenda_agent

        b = db.query(DemoBooking).filter(DemoBooking.id == booking_id).first()
        if not b or not (b.attendee_email or "").strip():
            return False
        meta = dict(b.meta or {})
        if meta.get("agenda_emailed"):
            return True  # already sent — don't spam on a webhook retry
        if not meeting_agenda_agent.agenda_is_grounded(db, booking_id):
            # No real KB grounding behind THIS agenda → keep it rep-facing.
            return False

        brand = None
        if b.product_id:
            brand = db.execute(
                text("SELECT name FROM nexus_products WHERE id = :p"), {"p": b.product_id}
            ).scalar()
        body = build_invite_body_html(
            db, booking_id=booking_id, attendee_name=b.attendee_name,
            brand=brand, fallback_desc="", grounded=True,
        )
        if "<h3>" not in body:  # no real agenda rendered → nothing to send
            return False

        # Sending mailbox: the booking's rep account, else any connected account
        # in the workspace.
        acct = None
        if b.rep_email:
            acct = (
                db.query(NexusConversationAccount)
                .filter(NexusConversationAccount.email_address == b.rep_email)
                .first()
            )
        if acct is None:
            acct = (
                db.query(NexusConversationAccount)
                .filter(
                    NexusConversationAccount.workspace_id == b.workspace_id,
                    NexusConversationAccount.provider.in_(["outlook", "gmail"]),
                )
                .first()
            )
        if acct is None:
            return False

        subject = "Agenda — %s demo" % (brand or "your")
        provider = (getattr(acct, "provider", "") or "").lower()
        if provider == "gmail":
            from nexus.services.connectors import gmail

            res = gmail.send_for_account(
                db, acct, to_email=b.attendee_email, subject=subject, body_html=body
            )
        else:
            from nexus.services.connectors import outlook

            res = outlook.send_for_account(
                db, acct, to_email=b.attendee_email, subject=subject, body_html=body
            )
        if res.get("ok"):
            meta["agenda_emailed"] = True
            b.meta = meta
            db.commit()
            return True
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("send_agenda_email failed for booking %s: %s", booking_id, exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return False


def _resolve_rep_mailbox(db: Session, ls_row) -> Optional[str]:
    """The mailbox the invite is sent FROM — the one this lead is already
    pinned to for its outreach thread, so the invite comes from the same rep
    the prospect has been talking to. None if the lead has no assigned mailbox
    (then we can't place a real invite and fall back to mark-booked only)."""
    acct_id = ls_row._mapping.get("conversation_account_id")
    if not acct_id:
        return None
    row = db.execute(
        text("SELECT email_address FROM nexus_conversation_accounts WHERE id = :i"),
        {"i": acct_id},
    ).first()
    return (row[0].strip() if row and row[0] else None)


def book_demo_from_reply(
    db: Session,
    *,
    lead_sequence_id: int,
    inbound_message: Any,
) -> Dict[str, Any]:
    """Scenario 7 gap 1: a reply confirmed a specific demo time in words, so
    create a REAL calendar invite for it — the same real-world outcome as
    clicking the booking link — and record it as a booking.

    Steps (each best-effort; a failure never breaks reply ingestion):
      1. Parse the committed time from the reply. No confident time → return
         {"booked": False, "reason": "no_time"} and leave today's behaviour
         (already marked demo_scheduled/halted by the caller) untouched.
      2. Create the Outlook event on the rep's own mailbox with the lead as an
         attendee (Graph mails them a proper accept/decline invite).
      3. Insert a nexus_demo_bookings row (source='email_reply') and run the
         same mark_lead_demo_scheduled roll-up scenario 6 uses, so a worded
         confirmation and a calendar-link booking converge on identical state.

    Returns a small dict for logging/tests.
    """
    from nexus.services import intent_classifier

    result: Dict[str, Any] = {"booked": False, "reason": None, "event_id": None}
    try:
        ls_row = db.execute(
            text(
                "SELECT id, workspace_id, campaign_id, lead_id, conversation_account_id "
                "FROM nexus_lead_sequences WHERE id = :i"
            ),
            {"i": lead_sequence_id},
        ).first()
        if not ls_row:
            result["reason"] = "no_sequence"
            return result
        m = ls_row._mapping
        workspace_id, campaign_id, lead_id = m["workspace_id"], m["campaign_id"], m["lead_id"]

        body_text = getattr(inbound_message, "body_text", "") or ""
        received_at = getattr(inbound_message, "received_at", None)
        when = intent_classifier.extract_demo_datetime(body_text, received_at)
        if when is None:
            # Classified DEMO_SCHEDULED but no parseable clock time (e.g. "yes
            # let's set up a demo" with no day). Nothing to put on a calendar —
            # the caller's halt/positive-stop stands, no invite is invented.
            result["reason"] = "no_time"
            return result

        lead = db.execute(
            text("SELECT email, first_name, last_name FROM nexus_global_leads WHERE id = :i"),
            {"i": lead_id},
        ).first()
        if not lead or not (lead[0] or "").strip():
            result["reason"] = "no_lead_email"
            return result
        attendee_email = lead[0].strip()
        attendee_name = (" ".join(x for x in (lead[1], lead[2]) if x)).strip() or None

        rep_mailbox = _resolve_rep_mailbox(db, ls_row)
        product_id = db.execute(
            text("SELECT product_id FROM nexus_campaigns WHERE id = :c"),
            {"c": campaign_id},
        ).scalar()
        brand = db.execute(
            text("SELECT name FROM nexus_products WHERE id = :p"),
            {"p": product_id},
        ).scalar() if product_id else None

        start = when
        end = when + timedelta(minutes=_DEMO_DURATION_MIN)
        subject = "%s demo" % (brand or "Product")

        acct_id = m.get("conversation_account_id")
        desc = "Confirmed from your reply. Looking forward to walking you through %s." % (
            brand or "the product"
        )

        # Record the booking FIRST and COMMIT it. The lead committed to a time, so
        # the booking is the source of truth — and both the agenda and the invite
        # below key off booking.id. Committing before the best-effort agenda/invite
        # work means a failure there can never undo the booking.
        from nexus.models_phase6 import DemoBooking
        from nexus.services.ms_bookings_sync import mark_lead_demo_scheduled
        from nexus.services import meeting_agenda_agent

        booking = DemoBooking(
            workspace_id=workspace_id,
            lead_id=lead_id,
            campaign_id=campaign_id,
            product_id=product_id,
            source="email_reply",
            attendee_email=attendee_email,
            attendee_name=attendee_name,
            rep_email=rep_mailbox,
            scheduled_at=start,
            end_time=end,
            duration_min=_DEMO_DURATION_MIN,
            status="scheduled",
            meta={
                "created_via": "email_reply_worded_confirmation",
                "time_zone": _DEMO_TIMEZONE,
                "source_message_id": getattr(inbound_message, "id", None),
            },
        )
        db.add(booking)
        try:
            db.commit()
            db.refresh(booking)
        except Exception as exc:  # noqa: BLE001
            log.warning("book_demo_from_reply: DemoBooking insert failed: %s", exc)
            db.rollback()
            result["reason"] = "booking_insert_failed"
            return result
        booking_id = booking.id

        # Generate + store the prospect-facing agenda now (best-effort) so the
        # invite body can embed it.
        try:
            meeting_agenda_agent.generate_for_booking(db, booking_id=booking_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("book_demo_from_reply: agenda generation skipped: %s", exc)

        # Phase 2: embed the agenda in the invite the prospect receives — but ONLY
        # when THIS agenda actually retrieved KB chunks; otherwise keep the
        # one-liner, so ungrounded prose never reaches a prospect.
        try:
            grounded = meeting_agenda_agent.agenda_is_grounded(db, booking_id)
        except Exception:  # noqa: BLE001
            grounded = False
        invite_body_html = build_invite_body_html(
            db,
            booking_id=booking_id,
            attendee_name=attendee_name,
            brand=brand,
            fallback_desc=desc,
            grounded=grounded,
        )

        # Create a real invite. Two mechanisms, best-fidelity first:
        #   1. app-only Graph event on the rep's calendar — auto-invites the
        #      attendee AND books the rep. Needs the Calendars.ReadWrite APP
        #      permission; returns None (403) until an Azure admin grants it.
        #   2. .ics meeting request emailed from the rep's connected mailbox —
        #      works with only the Mail.Send scope we already hold.
        # Whichever lands first wins; both are best-effort.
        event = None
        invite_via = None
        if rep_mailbox:
            from nexus.services import ms_graph_client

            try:
                event = _run_async(
                    ms_graph_client.create_calendar_event(
                        rep_mailbox,
                        subject=subject,
                        start_iso=start.strftime("%Y-%m-%dT%H:%M:%S"),
                        end_iso=end.strftime("%Y-%m-%dT%H:%M:%S"),
                        time_zone=_DEMO_TIMEZONE,
                        attendee_email=attendee_email,
                        attendee_name=attendee_name,
                        body_html=invite_body_html,
                        online_meeting=_DEMO_TEAMS_MEETING,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("book_demo_from_reply: Graph event creation failed: %s", exc)
            if event:
                invite_via = "graph_event"
            elif acct_id:
                try:
                    if _send_ics_invite(
                        db,
                        account_id=acct_id,
                        rep_mailbox=rep_mailbox,
                        attendee_email=attendee_email,
                        attendee_name=attendee_name,
                        subject=subject,
                        start_local=start,
                        end_local=end,
                        description=desc,
                    ):
                        invite_via = "ics_email"
                except Exception as exc:  # noqa: BLE001
                    log.warning("book_demo_from_reply: .ics invite send failed: %s", exc)
        else:
            log.info("book_demo_from_reply: no rep mailbox for lead %s — recording booking without an invite", lead_id)

        # Record the invite outcome back onto the committed booking row + roll up
        # the demo-scheduled status.
        try:
            booking.booking_id_external = (event or {}).get("id") if isinstance(event, dict) else None
            _meta = dict(booking.meta or {})
            _meta.update(
                invite_created=bool(invite_via),
                invite_method=invite_via,
                web_link=(event or {}).get("webLink") if isinstance(event, dict) else None,
                agenda_in_invite=bool(grounded and invite_via),
            )
            booking.meta = _meta
            mark_lead_demo_scheduled(
                db, lead_id=lead_id, workspace_id=workspace_id, campaign_id=campaign_id
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("book_demo_from_reply: post-invite update failed: %s", exc)
            db.rollback()

        result.update(
            booked=True,
            reason=("invite_created" if invite_via else "booked_no_invite"),
            invite_method=invite_via,
            event_id=(event or {}).get("id") if isinstance(event, dict) else None,
            scheduled_at=start.isoformat(),
            rep_mailbox=rep_mailbox,
        )
        log.info(
            "book_demo_from_reply: lead=%s booked demo at %s (invite_via=%s)",
            lead_id, start.isoformat(), invite_via,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        log.exception("book_demo_from_reply failed (non-fatal): %s", exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        result["reason"] = "error"
        return result


async def handle_booking_created(appointment: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy: handleBookingCreated(appointment). Fires on
    Calendly / MS Bookings webhook; persists nexus_demo_bookings row
    + emails rep with confirmation token."""
    return {"ok": False, "stub": True}


async def confirm_booking(token: str) -> Dict[str, Any]:
    """Legacy: confirmBooking(token). One-click rep confirm URL."""
    return {"ok": False, "stub": True}
