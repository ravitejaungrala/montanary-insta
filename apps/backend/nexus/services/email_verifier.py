"""Multi-provider email verification (Hunter + Abstract API + Reoon).

``verify_email`` runs the providers in order, short-circuiting on the first
high-confidence result. Each provider returns a tuple ``(verified: bool, score: int 0..100)``.

Score blending:
    - Hunter ``score`` (0-100) is used directly when status in {valid, accept_all}.
    - Abstract returns deliverability + quality_score (0-1) — mapped to 0-100.
    - Reoon returns status in {safe, valid, invalid, unknown}.

When all providers fail (missing keys or errors) we return (False, 0).
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


def _env(name: str) -> Optional[str]:
    try:
        # Prefer nexus.config attributes when Phase 1 ships them.
        from nexus import config as _cfg  # type: ignore
        val = getattr(_cfg, name, None)
        if val:
            return val
    except Exception:  # noqa: BLE001
        pass
    return os.getenv(name)


async def _hunter(email: str, client: httpx.AsyncClient) -> Optional[Tuple[bool, int]]:
    key = _env("HUNTER_API_KEY")
    if not key:
        return None
    try:
        r = await client.get(
            "https://api.hunter.io/v2/email-verifier",
            params={"email": email, "api_key": key},
            timeout=12.0,
        )
        data = (r.json() or {}).get("data", {}) if r.status_code == 200 else {}
        status = (data.get("status") or "").lower()
        score = int(data.get("score") or 0)
        verified = status in {"valid", "accept_all", "webmail"} and score >= 50
        return verified, score
    except Exception as exc:  # noqa: BLE001
        logger.warning("hunter verify failed: %s", exc)
        return None


async def _abstract(email: str, client: httpx.AsyncClient) -> Optional[Tuple[bool, int]]:
    key = _env("ABSTRACT_API_KEY")
    if not key:
        return None
    try:
        r = await client.get(
            "https://emailvalidation.abstractapi.com/v1/",
            params={"api_key": key, "email": email},
            timeout=12.0,
        )
        if r.status_code != 200:
            return None
        data = r.json() or {}
        deliver = (data.get("deliverability") or "").upper()
        try:
            quality = float(data.get("quality_score") or 0.0)
        except (TypeError, ValueError):
            quality = 0.0
        score = int(round(quality * 100))
        verified = deliver == "DELIVERABLE" and score >= 60
        return verified, score
    except Exception as exc:  # noqa: BLE001
        logger.warning("abstract verify failed: %s", exc)
        return None


async def _reoon(email: str, client: httpx.AsyncClient) -> Optional[Tuple[bool, int]]:
    key = _env("REOON_API_KEY")
    if not key:
        return None
    try:
        r = await client.get(
            "https://emailverifier.reoon.com/api/v1/verify",
            params={"email": email, "key": key, "mode": "power"},
            timeout=15.0,
        )
        if r.status_code != 200:
            return None
        data = r.json() or {}
        status = (data.get("status") or "").lower()
        verified = status in {"safe", "valid"}
        score = 95 if verified else (60 if status == "unknown" else 0)
        return verified, score
    except Exception as exc:  # noqa: BLE001
        logger.warning("reoon verify failed: %s", exc)
        return None


async def verify_email(email: str) -> Tuple[bool, int]:
    """Best-effort multi-provider verify. Returns ``(verified, score 0..100)``."""
    if not email or "@" not in email:
        return False, 0
    email = email.strip().lower()

    best: Optional[Tuple[bool, int]] = None
    async with httpx.AsyncClient() as client:
        for fn in (_hunter, _abstract, _reoon):
            result = await fn(email, client)
            if result is None:
                continue
            verified, score = result
            # Short-circuit on high-confidence success.
            if verified and score >= 80:
                return verified, score
            # Otherwise keep the best score seen so far.
            if best is None or score > best[1]:
                best = result
    return best if best is not None else (False, 0)


__all__ = ["verify_email"]
