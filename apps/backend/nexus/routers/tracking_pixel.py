"""Phase 6 email tracking pixel.

Endpoint:
  GET /nexus/track/{token}.gif  PUBLIC — 1x1 transparent GIF; logs the open.

The token is a long-lived JWT carrying (touchpoint_id, lead_id, workspace_id).
Verified via SECRET_KEY HS256. Pixel always returns 200 with the GIF so a bad
token doesn't break the email render.
"""

from __future__ import annotations

import logging
from datetime import datetime

from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_tokens import decode_click_token, decode_pixel_token

logger = logging.getLogger("pipelyt.nexus.pixel")

router = APIRouter(prefix="/nexus/track", tags=["nexus-tracking"])


# 1x1 transparent GIF
TRANSPARENT_GIF = (
    b"\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff"
    b"\x00\x00\x00\x21\xf9\x04\x01\x00\x00\x00\x00\x2c\x00\x00\x00\x00"
    b"\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b"
)


def _gif_response() -> Response:
    return Response(
        content=TRANSPARENT_GIF,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/{token}.gif")
def track_open(token: str, db: Session = Depends(get_db)):
    """PUBLIC tracking pixel — logs the open then returns a transparent GIF."""
    payload = decode_pixel_token(token)
    if not payload:
        return _gif_response()

    touchpoint_id = payload["touchpoint_id"]
    lead_id = payload["lead_id"]
    now = datetime.utcnow()

    # Update Touchpoint (Phase 4) — table may not exist yet, swallow errors.
    try:
        db.execute(
            text(
                """UPDATE nexus_touchpoints
                   SET opens_count = COALESCE(opens_count, 0) + 1,
                       last_opened_at = :now
                   WHERE id = :tp"""
            ),
            {"now": now, "tp": touchpoint_id},
        )
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.debug("Touchpoint open update skipped: %s", e)
        db.rollback()

    # Also update Outreach row if present (legacy path).
    try:
        db.execute(
            text(
                """UPDATE nexus_outreach
                   SET status = CASE WHEN status='sent' THEN 'opened' ELSE status END,
                       opened_at = COALESCE(opened_at, :now)
                   WHERE lead_id = :lid AND opened_at IS NULL"""
            ),
            {"now": now, "lid": lead_id},
        )
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.debug("Outreach open update skipped: %s", e)
        db.rollback()

    return _gif_response()


# Safe-URL guard for the click redirect. Refuse anything that isn't http(s):// —
# in particular, javascript:, data:, file:, ftp:.
_ALLOWED_SCHEMES = {"http", "https"}
_FALLBACK_REDIRECT = "https://pipelyt.com"


def _safe_redirect_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.scheme.lower() in _ALLOWED_SCHEMES and parsed.netloc:
            return url
    except Exception:
        pass
    return _FALLBACK_REDIRECT


@router.get("/click/{token}")
def track_click(token: str, db: Session = Depends(get_db)):
    """PUBLIC click redirect — logs the click, then 302s to the embedded URL.

    Token is a signed JWT carrying (touchpoint_id, lead_id, workspace_id, url).
    A bad/expired token redirects to a safe fallback instead of erroring, so
    the recipient never sees a broken link.
    """
    payload = decode_click_token(token)
    if not payload:
        return RedirectResponse(_FALLBACK_REDIRECT, status_code=302)

    touchpoint_id = payload["touchpoint_id"]
    lead_id = payload["lead_id"]
    destination = _safe_redirect_url(payload["url"])
    now = datetime.utcnow()

    # Touchpoint click counter
    try:
        db.execute(
            text(
                """UPDATE nexus_touchpoints
                   SET clicks_count = COALESCE(clicks_count, 0) + 1,
                       last_clicked_at = :now,
                       status = CASE
                                  WHEN status IN ('sent','opened') THEN 'clicked'
                                  ELSE status
                                END
                   WHERE id = :tp"""
            ),
            {"now": now, "tp": touchpoint_id},
        )
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.debug("Touchpoint click update skipped: %s", e)
        db.rollback()

    # Legacy Outreach row update
    try:
        db.execute(
            text(
                """UPDATE nexus_outreach
                   SET status = CASE WHEN status IN ('sent','opened') THEN 'clicked' ELSE status END,
                       clicked_at = COALESCE(clicked_at, :now)
                   WHERE lead_id = :lid AND clicked_at IS NULL"""
            ),
            {"now": now, "lid": lead_id},
        )
        db.commit()
    except Exception as e:  # noqa: BLE001
        logger.debug("Outreach click update skipped: %s", e)
        db.rollback()

    return RedirectResponse(destination, status_code=302)
