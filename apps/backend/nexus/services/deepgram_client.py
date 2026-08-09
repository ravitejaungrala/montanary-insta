"""Deepgram async transcription helper.

Used to transcribe a Twilio recording URL after a call completes. Polling/web-
hook callbacks happen via the `transcript` field in VoiceCall.

NEXUS legacy used Deepgram's prerecorded API with a `callback_url` so this
module also returns the callback URL setup so the route can wire it back to
`/nexus/voice/transcript`.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("pipelyt.nexus.deepgram")


DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
DEEPGRAM_BASE = "https://api.deepgram.com/v1"


async def transcribe_url(
    audio_url: str,
    *,
    callback_url: Optional[str] = None,
    model: str = "nova-2",
    language: str = "en",
) -> Dict[str, Any]:
    """Submit a prerecorded audio URL to Deepgram.

    If `callback_url` is set, returns immediately with `{request_id}`. Otherwise
    blocks until transcription finishes and returns the full response.
    """
    if not DEEPGRAM_API_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY not configured")

    params: Dict[str, Any] = {
        "model": model,
        "language": language,
        "punctuate": "true",
        "smart_format": "true",
    }
    if callback_url:
        params["callback"] = callback_url

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{DEEPGRAM_BASE}/listen",
            params=params,
            headers={
                "Authorization": f"Token {DEEPGRAM_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"url": audio_url},
        )
        if r.status_code >= 400:
            logger.warning("Deepgram error %s: %s", r.status_code, r.text[:200])
            r.raise_for_status()
        return r.json()


def extract_transcript_text(deepgram_payload: Dict[str, Any]) -> str:
    """Pull the joined transcript text from a Deepgram response."""
    try:
        channels = deepgram_payload.get("results", {}).get("channels", [])
        if not channels:
            return ""
        alts = channels[0].get("alternatives", [])
        if not alts:
            return ""
        return (alts[0].get("transcript") or "").strip()
    except Exception:  # noqa: BLE001
        return ""
