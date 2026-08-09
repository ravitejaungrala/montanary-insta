"""Hunter.io domain enumeration — find people emails for a given company domain."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

HUNTER_BASE = "https://api.hunter.io/v2"


def _api_key() -> Optional[str]:
    try:
        from nexus import config as _cfg  # type: ignore
        v = getattr(_cfg, "HUNTER_API_KEY", None)
        if v:
            return v
    except Exception:  # noqa: BLE001
        pass
    return os.getenv("HUNTER_API_KEY")


def _normalise(domain: str, p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    email = (p.get("value") or "").strip().lower()
    if not email:
        return None
    return {
        "email": email,
        "first_name": p.get("first_name"),
        "last_name": p.get("last_name"),
        "role": p.get("position"),
        "company_domain": domain.lower(),
        "company_name": None,
        "source": "hunter",
        "raw": {
            "confidence": p.get("confidence"),
            "linkedin": p.get("linkedin"),
            "department": p.get("department"),
            "seniority": p.get("seniority"),
        },
    }


async def enumerate_domain(
    domain: str,
    *,
    max_leads: int = 25,
    roles: Optional[List[str]] = None,
    seniority: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Return people emails for the supplied corporate domain."""
    key = _api_key()
    if not key or not domain:
        return []

    params: Dict[str, Any] = {
        "domain": domain,
        "api_key": key,
        "limit": min(max(max_leads, 1), 100),
    }
    if seniority:
        params["seniority"] = ",".join(seniority)
    if roles:
        # Hunter supports department only; we filter roles client-side below.
        pass

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{HUNTER_BASE}/domain-search", params=params)
        if r.status_code != 200:
            logger.warning("hunter HTTP %s: %s", r.status_code, r.text[:200])
            return []
        data = (r.json() or {}).get("data") or {}
        people = data.get("emails") or []
    except Exception as exc:  # noqa: BLE001
        logger.exception("hunter enumerate failed: %s", exc)
        return []

    out: List[Dict[str, Any]] = []
    role_set = {r.lower() for r in (roles or []) if r}
    for p in people:
        n = _normalise(domain, p)
        if not n:
            continue
        if role_set and n.get("role"):
            if not any(rs in n["role"].lower() for rs in role_set):
                continue
        out.append(n)
        if len(out) >= max_leads:
            break
    return out


__all__ = ["enumerate_domain"]
