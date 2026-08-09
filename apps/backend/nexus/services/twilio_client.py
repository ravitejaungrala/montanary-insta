"""Thin Twilio wrapper for outbound voice + TwiML generation.

Auth: TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN env vars. The actual `twilio`
Python package may not be installed in every Lambda image — the module imports
it lazily so importing nexus.services.twilio_client succeeds without it.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("pipelyt.nexus.twilio")


TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
TWILIO_VOICE_WEBHOOK_BASE = os.getenv("TWILIO_VOICE_WEBHOOK_BASE", "")


def _get_client():
    """Lazy import — keeps the rest of the backend importable when twilio isn't installed."""
    try:
        from twilio.rest import Client  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"twilio package not installed: {e}") from e

    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        raise RuntimeError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not configured")

    return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def initiate_call(
    to_number: str,
    *,
    from_number: Optional[str] = None,
    webhook_url: Optional[str] = None,
    status_callback: Optional[str] = None,
    record: bool = True,
) -> Dict[str, Any]:
    """Start an outbound Twilio call. Returns {sid, status} or raises on hard failure.

    `webhook_url` is the TwiML endpoint Twilio fetches when the call is answered.
    `status_callback` receives status updates (ringing, in-progress, completed, etc.).
    """
    client = _get_client()
    from_n = from_number or TWILIO_FROM_NUMBER
    if not from_n:
        raise RuntimeError("TWILIO_FROM_NUMBER not configured")

    call = client.calls.create(
        to=to_number,
        from_=from_n,
        url=webhook_url or f"{TWILIO_VOICE_WEBHOOK_BASE}/nexus/voice/twiml-stream",
        status_callback=status_callback,
        status_callback_event=["initiated", "ringing", "answered", "completed"],
        record=record,
    )

    return {
        "sid": call.sid,
        "status": call.status,
        "to": call.to,
        "from": call.from_,
    }


def build_twiml_say_and_stream(message: str, stream_url: Optional[str] = None) -> str:
    """Generate a TwiML response that speaks `message` and optionally streams audio.

    Stream URL should be a wss:// endpoint accepting Twilio Media Streams. When
    omitted, a simple <Say> + <Pause> pattern keeps the line open for transcription.
    """
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<Response>"]
    safe_msg = (message or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    parts.append(f"<Say voice=\"alice\">{safe_msg}</Say>")

    if stream_url:
        parts.append(f'<Start><Stream url="{stream_url}" /></Start>')

    parts.append('<Pause length="30" />')
    parts.append("</Response>")
    return "".join(parts)


def build_twiml_gather(prompt: str, action_url: str, timeout: int = 5) -> str:
    """Generate a <Gather> TwiML for capturing speech input from the lead."""
    safe = (prompt or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Gather input="speech" action="{action_url}" speechTimeout="auto" timeout="{timeout}">'
        f'<Say voice="alice">{safe}</Say>'
        "</Gather>"
        "</Response>"
    )
