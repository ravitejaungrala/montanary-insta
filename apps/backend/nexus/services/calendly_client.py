"""Calendly webhook signature verification.

Header `Calendly-Webhook-Signature` arrives in the form:
    t=<unix_ts>,v1=<hex_sig>

HMAC-SHA256 over `<t>.<rawBody>` with `CALENDLY_WEBHOOK_SIGNING_KEY`.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Optional

logger = logging.getLogger("pipelyt.nexus.calendly")


CALENDLY_WEBHOOK_SIGNING_KEY = os.getenv("CALENDLY_WEBHOOK_SIGNING_KEY", "")


def verify_signature(raw_body: bytes, header: Optional[str]) -> Optional[bool]:
    """Verify a Calendly webhook signature.

    Returns:
        True  — signature valid
        False — signature invalid (header was present and key configured)
        None  — verification skipped (no key configured)
    """
    if not CALENDLY_WEBHOOK_SIGNING_KEY:
        return None
    if not header or not isinstance(header, str):
        return False

    parts: dict = {}
    for token in header.split(","):
        if "=" in token:
            k, v = token.split("=", 1)
            parts[k.strip()] = v.strip()

    ts = parts.get("t")
    sig = parts.get("v1")
    if not ts or not sig:
        return False

    data = f"{ts}.{raw_body.decode('utf-8', errors='ignore') if isinstance(raw_body, bytes) else raw_body}"
    expected = hmac.new(
        CALENDLY_WEBHOOK_SIGNING_KEY.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    try:
        return hmac.compare_digest(sig, expected)
    except Exception:  # noqa: BLE001
        return False
