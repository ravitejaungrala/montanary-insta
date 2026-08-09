"""Microsoft Graph helper — workspace access tokens + Bookings appointments.

Uses MSAL client-credentials flow (app-only). Same Azure tenant + client ID as
PIPELYT OTP email sender (`AZURE_*` env vars), but the Graph scope here is
`https://graph.microsoft.com/.default` so the app needs `Bookings.Read.All` +
`Bookings.ReadWrite.All` permissions granted.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("pipelyt.nexus.ms_graph")


GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _resolve_credentials() -> tuple[str, str, str]:
    """Resolve (client_id, tenant_id, client_secret) from the standard
    Microsoft/Azure app: MS_* first, then AZURE_* (legacy alias used by OTP
    email) so existing deploys keep working.

    Read at call time, not import time — supports flipping creds without restart.
    """
    client_id = os.getenv("MS_CLIENT_ID") or os.getenv("AZURE_CLIENT_ID") or ""
    tenant_id = os.getenv("MS_TENANT_ID") or os.getenv("AZURE_TENANT_ID") or ""
    client_secret = (
        os.getenv("MS_CLIENT_SECRET") or os.getenv("AZURE_CLIENT_SECRET") or ""
    )
    return client_id, tenant_id, client_secret


def _msal_app():
    try:
        import msal  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"msal package not installed: {e}") from e

    client_id, tenant_id, client_secret = _resolve_credentials()
    if not (client_id and tenant_id and client_secret):
        raise RuntimeError(
            "MS Graph credentials not configured "
            "(set MS_*_NEXUS, MS_*, or AZURE_* env vars)"
        )

    return msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential=client_secret,
    )


def resolve_business_id() -> str:
    """Default business ID for the workspace-less polling path. Per-workspace
    Settings.ms_booking_business_id wins if present; this is the operator-wide
    fallback for local dev or single-tenant setups."""
    return (
        os.getenv("AZURE_BOOKING_BUSINESS_ID_GTM")
        or os.getenv("MS_BOOKING_BUSINESS_ID")
        or ""
    ).strip()


async def get_access_token() -> str:
    """Fetch an app-only access token for Microsoft Graph."""
    app = _msal_app()
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"MSAL token error: {result.get('error_description', result)}")
    return result["access_token"]


async def get_appointment(business_id: str, appointment_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single MS Bookings appointment by ID."""
    token = await get_access_token()
    url = (
        f"{GRAPH_BASE}/solutions/bookingBusinesses/{business_id}"
        f"/appointments/{appointment_id}"
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            logger.warning("Graph getAppointment %s: %s", r.status_code, r.text[:200])
            return None
        return r.json()


async def list_appointments(business_id: str) -> List[Dict[str, Any]]:
    """List appointments for an MS Bookings business.

    On 4xx/5xx returns an empty list — Graph errors are most often
    auth-config issues (missing `Bookings.Read.All` consent, wrong
    business_id, expired client secret). The body is logged at WARNING
    so production failures show up in CloudWatch instead of silently
    presenting as "appointments_seen=0" with no diagnostic clue.
    """
    token = await get_access_token()
    url = f"{GRAPH_BASE}/solutions/bookingBusinesses/{business_id}/appointments"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        if r.status_code >= 400:
            logger.warning(
                "Graph listAppointments biz=%s status=%s body=%s",
                business_id,
                r.status_code,
                r.text[:300],
            )
            return []
        return r.json().get("value", [])


# ─── Mail (Outlook) ────────────────────────────────────────────────────────────
#
# Designed to be safe when no MS account is linked yet — every helper either
# returns an empty result (list_messages) or raises a RuntimeError that callers
# catch and log (send_mail / mark_message_read). No global state.

async def list_messages(
    mailbox: str,
    *,
    only_unread: bool = True,
    top: int = 50,
    since_iso: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List messages in `mailbox` (a user principal name / email address).

    `since_iso` is an ISO-8601 UTC timestamp, e.g. '2026-05-25T10:00:00Z'.
    Pass it to bound the query window when the mailbox is large.

    On any 4xx/5xx returns []; the body is logged at WARNING. Matches the
    list_appointments convention so the polling caller can treat empty as
    "nothing new" rather than having to discriminate errors from no-mail.
    """
    if not mailbox:
        return []
    token = await get_access_token()
    filters: List[str] = []
    if only_unread:
        filters.append("isRead eq false")
    if since_iso:
        filters.append(f"receivedDateTime ge {since_iso}")

    # NOTE: do NOT add `inReplyTo` to $select — it isn't a property on the
    # Graph `message` resource and Graph returns 400 for unknown fields,
    # which silently zeros out the polling. Threading on the recipient
    # side relies on Outlook's `conversationId` (which is included below).
    params: Dict[str, str] = {
        "$top": str(top),
        "$orderby": "receivedDateTime desc",
        "$select": (
            "id,internetMessageId,subject,from,toRecipients,receivedDateTime,"
            "bodyPreview,body,conversationId,isRead"
        ),
    }
    if filters:
        params["$filter"] = " and ".join(filters)

    url = f"{GRAPH_BASE}/users/{mailbox}/messages"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        if r.status_code >= 400:
            logger.warning(
                "Graph list_messages mailbox=%s status=%s body=%s",
                mailbox,
                r.status_code,
                r.text[:300],
            )
            return []
        return r.json().get("value", [])


async def send_mail(
    mailbox: str,
    *,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
    in_reply_to: Optional[str] = None,
) -> Optional[str]:
    """Send a mail as `mailbox` via Graph `/users/{mailbox}/sendMail`.

    Returns the sentinel `"ok"` on success (Graph's sendMail returns 202
    with no body, so we have no real Internet-Message-ID to surface).
    Returns None on any failure. Callers should treat anything other than
    None as a successful send but must NOT persist the sentinel as a
    real provider message ID — Outlook recipient threading happens via
    `conversationId`, not via the In-Reply-To header (which Exchange
    transport may strip when it's set via `internetMessageHeaders`).
    """
    if not (mailbox and to_email):
        return None
    try:
        token = await get_access_token()
    except Exception as exc:  # noqa: BLE001
        logger.warning("send_mail token error: %s", exc)
        return None

    message: Dict[str, Any] = {
        "subject": subject or "",
        "body": {
            "contentType": "HTML" if body_html else "Text",
            "content": body_html or body_text or "",
        },
        "toRecipients": [{"emailAddress": {"address": to_email}}],
    }
    if in_reply_to:
        message["internetMessageHeaders"] = [
            {"name": "In-Reply-To", "value": in_reply_to},
            {"name": "References", "value": in_reply_to},
        ]

    payload = {"message": message, "saveToSentItems": True}
    url = f"{GRAPH_BASE}/users/{mailbox}/sendMail"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if r.status_code not in (200, 202):
            logger.warning(
                "Graph send_mail mailbox=%s to=%s status=%s body=%s",
                mailbox,
                to_email,
                r.status_code,
                r.text[:300],
            )
            return None
        # 202 Accepted is the typical success — Graph returns no body and
        # no message-id. We return the status sentinel "ok" so callers can
        # distinguish success-without-id from failure; downstream code that
        # stores provider IDs should treat anything other than None as sent
        # but should NOT use this string as a unique identifier.
        return "ok"


async def mark_message_read(mailbox: str, message_id: str) -> bool:
    """PATCH a Graph message to `isRead=true`. Best-effort; logs+returns
    False on any failure so the poller can decide whether to retry."""
    if not (mailbox and message_id):
        return False
    try:
        token = await get_access_token()
    except Exception as exc:  # noqa: BLE001
        logger.warning("mark_message_read token error: %s", exc)
        return False

    url = f"{GRAPH_BASE}/users/{mailbox}/messages/{message_id}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.patch(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"isRead": True},
        )
        if r.status_code >= 400:
            logger.warning(
                "Graph mark_read mailbox=%s id=%s status=%s body=%s",
                mailbox,
                message_id,
                r.status_code,
                r.text[:200],
            )
            return False
        return True


async def create_calendar_event(
    mailbox: str,
    *,
    subject: str,
    start_iso: str,
    end_iso: str,
    time_zone: str,
    attendee_email: str,
    attendee_name: Optional[str] = None,
    body_html: Optional[str] = None,
    online_meeting: bool = False,
) -> Optional[Dict[str, Any]]:
    """Create a real calendar event on `mailbox`'s Outlook calendar and invite
    `attendee_email` to it, via app-only Graph `POST /users/{mailbox}/events`.

    This is what turns a WORDED confirmation ("Tuesday at 2pm works") into the
    same real-world outcome as clicking the booking link: the event lands on
    the rep's calendar AND Outlook mails the attendee a proper invite they can
    accept/decline.

    `online_meeting` (a Teams join link) is OFF by default and opt-in: creating
    a Teams meeting app-only additionally requires OnlineMeetings.ReadWrite.All
    + a Graph application access policy scoped to the mailbox. Without that,
    `isOnlineMeeting: true` makes the request HANG (observed: a plain event
    returns in ~2s, the Teams variant times out past 20s). A plain calendar
    invite — the actual requirement — creates reliably; enable the Teams link
    only on a tenant configured for app-only online meetings.

    `start_iso`/`end_iso` are LOCAL wall-clock ISO strings (no offset), paired
    with `time_zone` (an IANA name like "Asia/Kolkata") — Graph interprets the
    wall-clock time in that zone, so we never have to do offset math ourselves.

    Returns the created event dict (has `id`, `webLink`, and, for online
    meetings, `onlineMeeting.joinUrl`) on success, or None on any failure. The
    caller treats None as "couldn't place it on a calendar" and falls back to
    the existing mark-booked behaviour — the send/ingest path is never broken
    by a calendar failure.

    Requires the app to hold the `Calendars.ReadWrite` application permission.
    A 403 here means that grant is missing; the warning names it so it's a
    one-step Azure fix, not a mystery.
    """
    if not (mailbox and attendee_email and start_iso and end_iso):
        return None
    try:
        token = await get_access_token()
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_calendar_event token error: %s", exc)
        return None

    event: Dict[str, Any] = {
        "subject": subject or "Demo",
        "start": {"dateTime": start_iso, "timeZone": time_zone},
        "end": {"dateTime": end_iso, "timeZone": time_zone},
        "attendees": [
            {
                "type": "required",
                "emailAddress": {
                    "address": attendee_email,
                    "name": attendee_name or attendee_email,
                },
            }
        ],
    }
    if body_html:
        event["body"] = {"contentType": "HTML", "content": body_html}
    if online_meeting:
        event["isOnlineMeeting"] = True
        event["onlineMeetingProvider"] = "teamsForBusiness"

    url = f"{GRAPH_BASE}/users/{mailbox}/events"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=event,
        )
        if r.status_code not in (200, 201):
            logger.warning(
                "Graph create_event mailbox=%s attendee=%s status=%s body=%s "
                "(a 403 means the app lacks the Calendars.ReadWrite permission)",
                mailbox,
                attendee_email,
                r.status_code,
                r.text[:300],
            )
            return None
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return {"id": None}


# ─── Bookings: change subscription (existing) ─────────────────────────────────


async def create_change_subscription(
    business_id: str,
    notification_url: str,
    *,
    client_state: str = "nexus-bookings",
    expiration_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a Graph change-notification subscription for MS Bookings appointments."""
    token = await get_access_token()
    body = {
        "changeType": "created,updated",
        "notificationUrl": notification_url,
        "resource": f"solutions/bookingBusinesses/{business_id}/appointments",
        "expirationDateTime": expiration_iso,
        "clientState": client_state,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{GRAPH_BASE}/subscriptions",
            json={k: v for k, v in body.items() if v is not None},
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Graph subscription create failed: {r.status_code} {r.text[:200]}")
        return r.json()
