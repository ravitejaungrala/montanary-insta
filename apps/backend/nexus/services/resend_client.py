"""Thin wrapper around the Resend Python SDK.

The `resend` package is imported lazily so that the absence of the
dependency does not break module import — production Lambdas will ship
it, but unit tests / local servers without RESEND_API_KEY can still
import this module.

Honors a `TEST_REDIRECT_EMAIL` environment variable: when set, *every*
outgoing email is redirected to that address regardless of the
requested recipient. Useful during QA so we never accidentally hit a
real prospect.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

log = logging.getLogger("nexus.resend")


def _get_redirect_target() -> Optional[str]:
    val = os.getenv("TEST_REDIRECT_EMAIL")
    if val and val.strip():
        return val.strip()
    return None


def send_email(
    to: str,
    from_: str,
    subject: str,
    html: str,
    text: Optional[str] = None,
) -> Dict[str, Any]:
    """Send an email via Resend.

    Returns a dict with at least:
        {"ok": bool, "id": str|None, "error": str|None,
         "redirected_to": str|None, "original_to": str}

    Never raises; failures are signalled via the `ok=False` shape so
    that callers (sequencer) can log a Touchpoint with `status='failed'`
    rather than aborting the whole tick.
    """
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        return {
            "ok": False,
            "id": None,
            "error": "RESEND_API_KEY not configured",
            "redirected_to": None,
            "original_to": to,
        }

    original_to = to
    redirect = _get_redirect_target()
    if redirect:
        log.info("nexus.resend: redirecting %s -> %s (TEST_REDIRECT_EMAIL)", to, redirect)
        to = redirect

    try:
        import resend  # type: ignore
    except Exception as e:  # pragma: no cover - exercised only when SDK absent
        return {
            "ok": False,
            "id": None,
            "error": f"resend SDK not installed: {e}",
            "redirected_to": redirect,
            "original_to": original_to,
        }

    try:
        resend.api_key = api_key
        payload: Dict[str, Any] = {
            "from": from_,
            "to": [to] if isinstance(to, str) else to,
            "subject": subject,
            "html": html,
        }
        if text:
            payload["text"] = text

        resp = resend.Emails.send(payload)
        # The SDK returns either a dict or an object with `id`.
        msg_id = None
        if isinstance(resp, dict):
            msg_id = resp.get("id")
        else:
            msg_id = getattr(resp, "id", None)

        return {
            "ok": True,
            "id": msg_id,
            "error": None,
            "redirected_to": redirect,
            "original_to": original_to,
        }
    except Exception as e:
        log.exception("nexus.resend: send failed")
        return {
            "ok": False,
            "id": None,
            "error": str(e),
            "redirected_to": redirect,
            "original_to": original_to,
        }
