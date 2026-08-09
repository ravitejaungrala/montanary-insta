"""JWT helpers for the long-lived tokens embedded in outbound emails.

Tracking pixel JWT  → {touchpoint_id, lead_id, workspace_id, exp}
Unsubscribe JWT     → {lead_id, workspace_id, exp}

Both use HS256 + SECRET_KEY with a 1-year expiry. Emails referenced months
later still verify because the token is essentially a stateless capability
limited to the (workspace, lead) pair.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from jose import JWTError, jwt

from core.config import SECRET_KEY, ALGORITHM

logger = logging.getLogger("pipelyt.nexus.phase6.tokens")


_ONE_YEAR_SECONDS = 365 * 24 * 60 * 60


def encode_pixel_token(*, touchpoint_id: int, lead_id: int, workspace_id: int) -> str:
    payload = {
        "typ": "nexus_pixel",
        "tp": int(touchpoint_id),
        "ld": int(lead_id),
        "ws": int(workspace_id),
        "exp": datetime.utcnow() + timedelta(seconds=_ONE_YEAR_SECONDS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_pixel_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if data.get("typ") != "nexus_pixel":
            return None
        return {
            "touchpoint_id": int(data["tp"]),
            "lead_id": int(data["ld"]),
            "workspace_id": int(data["ws"]),
        }
    except (JWTError, KeyError, ValueError, TypeError) as e:
        logger.debug("pixel token decode failed: %s", e)
        return None


def encode_unsub_token(*, lead_id: int, workspace_id: int) -> str:
    payload = {
        "typ": "nexus_unsub",
        "ld": int(lead_id),
        "ws": int(workspace_id),
        "exp": datetime.utcnow() + timedelta(seconds=_ONE_YEAR_SECONDS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_unsub_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if data.get("typ") != "nexus_unsub":
            return None
        return {
            "lead_id": int(data["ld"]),
            "workspace_id": int(data["ws"]),
        }
    except (JWTError, KeyError, ValueError, TypeError) as e:
        logger.debug("unsub token decode failed: %s", e)
        return None


def encode_click_token(
    *, touchpoint_id: int, lead_id: int, workspace_id: int, url: str
) -> str:
    """Embed the original destination URL inside a signed click token. The
    redirect handler verifies, logs the click, and 302s to `url`."""
    payload = {
        "typ": "nexus_click",
        "tp": int(touchpoint_id),
        "ld": int(lead_id),
        "ws": int(workspace_id),
        "u": str(url),
        "exp": datetime.utcnow() + timedelta(seconds=_ONE_YEAR_SECONDS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_click_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        data = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if data.get("typ") != "nexus_click":
            return None
        return {
            "touchpoint_id": int(data["tp"]),
            "lead_id": int(data["ld"]),
            "workspace_id": int(data["ws"]),
            "url": str(data["u"]),
        }
    except (JWTError, KeyError, ValueError, TypeError) as e:
        logger.debug("click token decode failed: %s", e)
        return None
