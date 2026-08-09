"""Gmail (Google) mailbox connector — OAuth 2.0 authorization-code flow.

Mirrors the public API of `connectors.outlook` so the router + sequencer can
treat both providers identically:

  build_auth_url(state)            -> Google consent URL
  exchange_code(code)              -> tokens + connected identity
  get_valid_access_token(db, acct) -> cached token, auto-refreshed when stale
  send_for_account(db, acct, ...)  -> send via Gmail API + refresh-and-retry
  is_configured()                  -> are server credentials present?

A customer connects their own Gmail / Google Workspace mailbox once; the app
then sends FROM it via the Gmail API (users/me/messages/send).

App requirements (Google Cloud Console → APIs & Services)
---------------------------------------------------------
  - OAuth client (Web application) with client id + secret.
  - Authorized redirect URI = env GMAIL_REDIRECT_URI (registered on the app).
  - Enable the Gmail API.
  - OAuth consent screen + Google verification for the SENSITIVE/RESTRICTED
    `gmail.modify` scope (read + send + mark-read) before non-test external
    users can connect.

Env: GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET (+ GMAIL_REDIRECT_URI).

Note vs Outlook: Google issues a refresh token ONLY on the first consent
(access_type=offline & prompt=consent) and does NOT rotate it on refresh, so
we keep the stored refresh token across refreshes.
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("pipelyt.nexus.connectors.gmail")

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"
GMAIL_SEND_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

# gmail.modify is the single read+write workhorse: it covers SENDING outreach
# (the messages.send endpoint accepts gmail.modify), READING incoming replies
# (auto-stop follow-ups), and MARKING them read. So a separate gmail.send scope
# is redundant and was dropped — one fewer scope on the consent screen.
# gmail.modify is a RESTRICTED scope → Google app verification (+ CASA) is
# required before public (external) users can connect; test users work
# immediately in "Testing" publishing status.
SCOPES: List[str] = [
    "https://www.googleapis.com/auth/gmail.modify",
    "openid",
    "email",
    "profile",
]

_EXPIRY_BUFFER_SECONDS = 300


# ── Credentials / config ──────────────────────────────────────────────────────


def _resolve_credentials() -> tuple[str, str]:
    client_id = os.getenv("GOOGLE_CLIENT_ID") or ""
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET") or ""
    return client_id, client_secret


def get_redirect_uri() -> str:
    return (
        os.getenv("GMAIL_REDIRECT_URI")
        or "http://localhost:8000/nexus/connectors/gmail/callback"
    ).strip()


def is_configured() -> bool:
    client_id, client_secret = _resolve_credentials()
    return bool(client_id and client_secret)


def _expiry_from(expires_in: Optional[int]) -> datetime:
    seconds = int(expires_in or 3600)
    return datetime.utcnow() + timedelta(seconds=max(0, seconds - _EXPIRY_BUFFER_SECONDS))


# ── OAuth: step 1 — build consent URL ────────────────────────────────────────


def build_auth_url(state: str) -> str:
    client_id, _ = _resolve_credentials()
    if not client_id:
        raise RuntimeError("Gmail connector not configured (set GOOGLE_CLIENT_ID).")
    from urllib.parse import urlencode

    params = {
        "client_id": client_id,
        "redirect_uri": get_redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        # offline + consent guarantees a refresh token is returned.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


# ── OAuth: step 2 — exchange code for tokens + identity ──────────────────────


def exchange_code(code: str) -> Dict[str, Any]:
    """Exchange the authorization code for tokens + the connected identity.

    Returns {access_token, refresh_token, expires_at, email, display_name,
    tenant_id}. Raises RuntimeError on failure.
    """
    client_id, client_secret = _resolve_credentials()
    if not (client_id and client_secret):
        raise RuntimeError("Gmail connector not configured.")

    with httpx.Client(timeout=20.0) as client:
        r = client.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": get_redirect_uri(),
                "grant_type": "authorization_code",
            },
        )
    if r.status_code != 200:
        raise RuntimeError(f"Gmail token exchange failed: {r.status_code} {r.text[:300]}")
    tok = r.json()
    access_token = tok.get("access_token")
    if not access_token:
        raise RuntimeError(f"Gmail token exchange returned no access_token: {tok}")

    email, display_name = _fetch_identity(access_token)
    return {
        "access_token": access_token,
        "refresh_token": tok.get("refresh_token"),
        "expires_at": _expiry_from(tok.get("expires_in")),
        "email": email,
        "display_name": display_name,
        "tenant_id": "",  # not applicable to Gmail
    }


def _fetch_identity(access_token: str) -> tuple[str, str]:
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.get(
                USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if r.status_code == 200:
            info = r.json()
            return (
                (info.get("email") or "").strip().lower(),
                (info.get("name") or "").strip(),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("gmail userinfo fetch failed: %s", exc)
    return "", ""


# ── OAuth: step 3 — refresh ──────────────────────────────────────────────────


def _refresh_tokens(refresh_token: str) -> Dict[str, Any]:
    """Mint a fresh access token. Google does NOT return a new refresh token
    on refresh, so the caller keeps the existing one."""
    client_id, client_secret = _resolve_credentials()
    with httpx.Client(timeout=20.0) as client:
        r = client.post(
            TOKEN_ENDPOINT,
            data={
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
            },
        )
    if r.status_code != 200:
        raise RuntimeError(f"Gmail token refresh failed: {r.status_code} {r.text[:300]}")
    tok = r.json()
    if "access_token" not in tok:
        raise RuntimeError(f"Gmail token refresh returned no access_token: {tok}")
    return {
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token") or refresh_token,
        "expires_at": _expiry_from(tok.get("expires_in")),
    }


def _refresh_and_store(db, account) -> Optional[str]:
    refresh_token = getattr(account, "refresh_token", None)
    if not refresh_token:
        logger.warning(
            "gmail account id=%s has no refresh_token — needs reconnect",
            getattr(account, "id", "?"),
        )
        return None
    try:
        fresh = _refresh_tokens(refresh_token)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "gmail refresh failed for account id=%s: %s",
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
    if account is None:
        return None
    cached = getattr(account, "access_token", None)
    expires_at = getattr(account, "token_expires_at", None)
    if cached and expires_at and expires_at > datetime.utcnow():
        return cached
    return _refresh_and_store(db, account)


# ── Send ─────────────────────────────────────────────────────────────────────


def _build_raw(
    to_email: str,
    subject: str,
    body_html: Optional[str],
    body_text: Optional[str],
    from_email: Optional[str],
    in_reply_to: Optional[str],
) -> str:
    if body_html:
        msg = MIMEMultipart("alternative")
        if body_text:
            msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))
    else:
        msg = MIMEText(body_text or "", "plain", "utf-8")
    msg["To"] = to_email
    msg["Subject"] = subject or ""
    if from_email:
        msg["From"] = from_email
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _post_send(access_token: str, raw: str) -> int:
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.post(
                GMAIL_SEND_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"raw": raw},
            )
        return r.status_code
    except Exception as exc:  # noqa: BLE001
        logger.warning("gmail send transport error: %s", exc)
        return 0


def send_mail(
    access_token: str,
    *,
    to_email: str,
    subject: str,
    body_html: Optional[str] = None,
    body_text: Optional[str] = None,
    from_email: Optional[str] = None,
    in_reply_to: Optional[str] = None,
) -> bool:
    if not (access_token and to_email):
        return False
    raw = _build_raw(to_email, subject, body_html, body_text, from_email, in_reply_to)
    status = _post_send(access_token, raw)
    if status != 200:
        logger.warning("gmail send_mail to=%s status=%s", to_email, status)
        return False
    return True


def send_for_account(
    db,
    account,
    *,
    to_email: str,
    subject: str,
    body_html: Optional[str] = None,
    body_text: Optional[str] = None,
    in_reply_to: Optional[str] = None,
) -> Dict[str, Any]:
    """High-level send: valid token → send → refresh-and-retry once on 401."""
    if not to_email:
        return {"ok": False, "error": "lead has no email"}

    from_email = getattr(account, "email_address", None)
    token = get_valid_access_token(db, account)
    if not token:
        return {"ok": False, "error": "needs_reconnect"}

    raw = _build_raw(to_email, subject, body_html, body_text, from_email, in_reply_to)
    status = _post_send(token, raw)
    if status == 200:
        return {"ok": True, "error": None}

    if status in (401, 403):
        logger.info("gmail token rejected (status=%s) — forcing refresh + retry", status)
        token = _refresh_and_store(db, account)
        if not token:
            return {"ok": False, "error": "needs_reconnect"}
        status = _post_send(token, raw)
        if status == 200:
            return {"ok": True, "error": None}

    logger.warning("gmail send_for_account to=%s final_status=%s", to_email, status)
    return {"ok": False, "error": f"send_failed_status_{status}"}
