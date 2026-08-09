"""Outlook (Microsoft Graph) mailbox connector — DELEGATED, MULTI-TENANT.

Distinct from `ms_graph_client.py` (app-only, single-tenant, for MS Bookings).
That flow cannot send mail as an arbitrary external user.

Here we use the OAuth 2.0 authorization-code flow against the `/common`
endpoint, which supports **both** any organizational (work/school) tenant
**and** personal Microsoft accounts. A customer signs in once, consents,
and the app sends email FROM their mailbox via Graph `/me/sendMail`.

Public API
----------
  build_auth_url(state)            -> consent URL (redirect the browser here)
  exchange_code(code)              -> tokens + connected identity
  get_valid_access_token(db, acct) -> cached token, auto-refreshed when stale
  send_for_account(db, acct, ...)  -> send + auto-refresh-and-retry on a
                                      rejected token (the high-level entry the
                                      sequencer uses)
  is_configured()                  -> are server credentials present?

Entra app requirements
----------------------
  - Supported account types: "Accounts in any organizational directory
    (multitenant) AND personal Microsoft accounts".
  - Delegated Microsoft Graph permissions: Mail.Send, offline_access,
    User.Read (+ openid, profile, email).
  - Redirect URI (env AZURE_OUTLOOK_REDIRECT_URI) registered on the app.
Uses the standard MS_CLIENT_ID / MS_CLIENT_SECRET (fallback AZURE_*) app.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("pipelyt.nexus.connectors.outlook")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# /common = any org tenant + personal Microsoft accounts.
AUTHORITY = "https://login.microsoftonline.com/common"

# Delegated scopes. MSAL AUTOMATICALLY adds the reserved scopes
# (openid, profile, offline_access) — we must NOT list them here or MSAL
# raises "You cannot use any scope value that is reserved". offline_access
# is still requested by MSAL, so we still get a refresh token.
#   Mail.Send      → send outreach from the user's mailbox
#   Mail.ReadWrite → read incoming replies (auto-stop follow-ups, inbox) and
#                    mark them read; includes read, so no separate Mail.Read
#   User.Read      → resolve the connected mailbox's email/identity
# Granting read now means users consent ONCE (adding it later forces a
# re-consent for everyone already connected).
SCOPES: List[str] = [
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/User.Read",
]

# Refresh the access token this many seconds BEFORE it actually expires so a
# send never races a just-expired token.
_EXPIRY_BUFFER_SECONDS = 300


# ── Credentials / config ──────────────────────────────────────────────────────


def _resolve_credentials() -> tuple[str, str]:
    """(client_id, client_secret) — the standard Microsoft/Azure app
    (MS_CLIENT_ID / MS_CLIENT_SECRET, falling back to AZURE_*), used here
    against /common for the delegated multi-tenant flow."""
    client_id = os.getenv("MS_CLIENT_ID") or os.getenv("AZURE_CLIENT_ID") or ""
    client_secret = (
        os.getenv("MS_CLIENT_SECRET") or os.getenv("AZURE_CLIENT_SECRET") or ""
    )
    return client_id, client_secret


def get_redirect_uri() -> str:
    """Callback URL Microsoft redirects to after consent. MUST match a
    redirect URI registered on the Entra app exactly."""
    return (
        os.getenv("AZURE_OUTLOOK_REDIRECT_URI")
        or "http://localhost:8000/nexus/connectors/outlook/callback"
    ).strip()


def is_configured() -> bool:
    client_id, client_secret = _resolve_credentials()
    return bool(client_id and client_secret)


def _msal_app():
    try:
        import msal  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"msal package not installed: {e}") from e

    client_id, client_secret = _resolve_credentials()
    if not (client_id and client_secret):
        raise RuntimeError(
            "Outlook connector not configured "
            "(set MS_CLIENT_ID + MS_CLIENT_SECRET, or AZURE_* equivalents)"
        )
    return msal.ConfidentialClientApplication(
        client_id,
        authority=AUTHORITY,
        client_credential=client_secret,
    )


def _expiry_from(expires_in: Optional[int]) -> datetime:
    seconds = int(expires_in or 3600)
    return datetime.utcnow() + timedelta(seconds=max(0, seconds - _EXPIRY_BUFFER_SECONDS))


# ── OAuth: step 1 — build consent URL ────────────────────────────────────────


def build_auth_url(state: str) -> str:
    app = _msal_app()
    return app.get_authorization_request_url(
        SCOPES,
        state=state,
        redirect_uri=get_redirect_uri(),
        prompt="select_account",
    )


# ── OAuth: step 2 — exchange code for tokens + identity ──────────────────────


def exchange_code(code: str) -> Dict[str, Any]:
    """Exchange the authorization code for tokens + the connected identity.

    Returns {access_token, refresh_token, expires_at, email, display_name,
    tenant_id}. Raises RuntimeError on failure.
    """
    app = _msal_app()
    result = app.acquire_token_by_authorization_code(
        code, scopes=SCOPES, redirect_uri=get_redirect_uri()
    )
    if "access_token" not in result:
        raise RuntimeError(
            "Outlook token exchange failed: "
            f"{result.get('error_description') or result.get('error') or result}"
        )

    claims = result.get("id_token_claims") or {}
    email = (
        claims.get("preferred_username")
        or claims.get("email")
        or claims.get("upn")
        or ""
    ).strip().lower()
    return {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token"),
        "expires_at": _expiry_from(result.get("expires_in")),
        "email": email,
        "display_name": (claims.get("name") or "").strip(),
        "tenant_id": (claims.get("tid") or "").strip(),
    }


# ── OAuth: step 3 — refresh ──────────────────────────────────────────────────


def _refresh_tokens(refresh_token: str) -> Dict[str, Any]:
    """Mint a fresh access token from a refresh token. Microsoft rotates
    refresh tokens, so the returned refresh_token MUST be persisted.
    Raises RuntimeError on failure (e.g. consent revoked)."""
    app = _msal_app()
    result = app.acquire_token_by_refresh_token(refresh_token, scopes=SCOPES)
    if "access_token" not in result:
        raise RuntimeError(
            "Outlook token refresh failed: "
            f"{result.get('error_description') or result.get('error') or result}"
        )
    return {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token") or refresh_token,
        "expires_at": _expiry_from(result.get("expires_in")),
    }


def _refresh_and_store(db, account) -> Optional[str]:
    """Force a refresh from the stored refresh_token and persist the new
    tokens on the account. Returns the new access token, or None if the
    account can't be refreshed (no refresh token / consent revoked)."""
    refresh_token = getattr(account, "refresh_token", None)
    if not refresh_token:
        logger.warning(
            "outlook account id=%s has no refresh_token — needs reconnect",
            getattr(account, "id", "?"),
        )
        return None
    try:
        fresh = _refresh_tokens(refresh_token)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "outlook refresh failed for account id=%s: %s",
            getattr(account, "id", "?"), exc,
        )
        return None

    account.access_token = fresh["access_token"]
    account.refresh_token = fresh["refresh_token"]
    account.token_expires_at = fresh["expires_at"]
    try:
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
    return fresh["access_token"]


def get_valid_access_token(db, account) -> Optional[str]:
    """Return a usable access token, refreshing + persisting when the cached
    one is missing or within the expiry buffer. None ⇒ needs reconnect."""
    if account is None:
        return None
    cached = getattr(account, "access_token", None)
    expires_at = getattr(account, "token_expires_at", None)
    if cached and expires_at and expires_at > datetime.utcnow():
        return cached
    return _refresh_and_store(db, account)


# ── Send ─────────────────────────────────────────────────────────────────────


def _post_send_mail(access_token: str, message: Dict[str, Any]) -> int:
    """Low-level POST to Graph /me/sendMail. Returns the HTTP status code
    (0 on transport error)."""
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.post(
                f"{GRAPH_BASE}/me/sendMail",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"message": message, "saveToSentItems": True},
            )
        return r.status_code
    except Exception as exc:  # noqa: BLE001
        logger.warning("outlook send transport error: %s", exc)
        return 0


def _build_message(
    to_email: str,
    subject: str,
    body_html: Optional[str],
    body_text: Optional[str],
    in_reply_to: Optional[str],
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    message: Dict[str, Any] = {
        "subject": subject or "",
        "body": {
            "contentType": "HTML" if body_html else "Text",
            "content": body_html or body_text or "",
        },
        "toRecipients": [{"emailAddress": {"address": to_email}}],
    }
    if attachments:
        message["attachments"] = attachments
    # NOTE: Microsoft Graph REJECTS standard internet headers set via
    # `internetMessageHeaders` — only names starting with 'x-'/'X-' are allowed,
    # so "In-Reply-To"/"References" return HTTP 400 (InvalidInternetMessageHeader)
    # and the ENTIRE send fails. That silently broke every threaded auto-reply
    # (outreach was unaffected since new emails pass no in_reply_to). We therefore
    # do NOT inject those headers; recipient clients thread on the "Re: <subject>"
    # convention. `in_reply_to` stays in the signature for callers but is not
    # written as a header.
    return message


def send_mail(
    access_token: str,
    *,
    to_email: str,
    subject: str,
    body_html: Optional[str] = None,
    body_text: Optional[str] = None,
    in_reply_to: Optional[str] = None,
) -> bool:
    """Send a single mail with an already-valid access token. True on 202."""
    if not (access_token and to_email):
        return False
    message = _build_message(to_email, subject, body_html, body_text, in_reply_to)
    status = _post_send_mail(access_token, message)
    if status not in (200, 202):
        logger.warning("outlook send_mail to=%s status=%s", to_email, status)
        return False
    return True


# ── Read (delegated /me/messages) — for the reply agent ──────────────────────


def list_messages(
    access_token: str,
    *,
    only_unread: bool = True,
    top: int = 25,
    since_iso: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List messages in the CONNECTED user's mailbox via delegated token.
    Returns Graph message dicts (same shape ms_mail_sync already parses).
    Empty list on any error."""
    if not access_token:
        return []
    filters: List[str] = []
    if only_unread:
        filters.append("isRead eq false")
    if since_iso:
        filters.append(f"receivedDateTime ge {since_iso}")
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
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(
                f"{GRAPH_BASE}/me/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                params=params,
            )
        if r.status_code >= 400:
            logger.warning("outlook list_messages status=%s body=%s", r.status_code, r.text[:300])
            return []
        return r.json().get("value", [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("outlook list_messages transport error: %s", exc)
        return []


def mark_read(access_token: str, message_id: str) -> bool:
    """Mark a message read in the connected mailbox (delegated)."""
    if not (access_token and message_id):
        return False
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.patch(
                f"{GRAPH_BASE}/me/messages/{message_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"isRead": True},
            )
        return r.status_code < 400
    except Exception as exc:  # noqa: BLE001
        logger.warning("outlook mark_read transport error: %s", exc)
        return False


def send_for_account(
    db,
    account,
    *,
    to_email: str,
    subject: str,
    body_html: Optional[str] = None,
    body_text: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """High-level send used by the sequencer.

    Handles token lifecycle end-to-end:
      1. Get a valid (auto-refreshed) access token.
      2. Send.
      3. If the token is rejected (401), force ONE refresh + retry — covers
         the case where Microsoft invalidated the token before our clock
         said it expired (password change, conditional-access, rotation).

    `attachments` (optional) are Graph fileAttachment dicts — used to send a
    calendar `.ics` meeting request from the mailbox with only the Mail.Send
    scope (no Calendars permission needed).

    Returns {"ok": bool, "error": str|None}. "needs_reconnect" means the
    mailbox lost consent and the user must re-link it.
    """
    if not to_email:
        return {"ok": False, "error": "lead has no email"}

    token = get_valid_access_token(db, account)
    if not token:
        return {"ok": False, "error": "needs_reconnect"}

    message = _build_message(to_email, subject, body_html, body_text, in_reply_to, attachments)
    status = _post_send_mail(token, message)
    if status in (200, 202):
        return {"ok": True, "error": None}

    # 401 (and 403 invalid-token variants) → token rejected. Force a refresh
    # and retry exactly once before giving up.
    if status in (401, 403):
        logger.info("outlook token rejected (status=%s) — forcing refresh + retry", status)
        token = _refresh_and_store(db, account)
        if not token:
            return {"ok": False, "error": "needs_reconnect"}
        status = _post_send_mail(token, message)
        if status in (200, 202):
            return {"ok": True, "error": None}

    logger.warning("outlook send_for_account to=%s final_status=%s", to_email, status)
    return {"ok": False, "error": f"send_failed_status_{status}"}
