"""GitHub-based prospecting — surface technical decision makers by querying users.

We use the GitHub REST search-users endpoint, then enrich each hit with the
public profile to grab name, public email, company, and bio. People without
a public email are skipped (we never scrape commit emails).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def _token() -> Optional[str]:
    try:
        from nexus import config as _cfg  # type: ignore
        v = getattr(_cfg, "GITHUB_TOKEN", None)
        if v:
            return v
    except Exception:  # noqa: BLE001
        pass
    return os.getenv("GITHUB_TOKEN")


def _headers() -> Dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "PipelytNexusBot/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    tok = _token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _build_query(icp: Dict[str, Any]) -> str:
    parts: List[str] = []
    roles = icp.get("roles") or []
    if roles:
        parts.append(" OR ".join(f'"{r}"' for r in roles[:4]))
    keywords = icp.get("keywords") or []
    if keywords:
        parts.append(" OR ".join(f'"{k}"' for k in keywords[:4]))
    geographies = icp.get("geographies") or []
    if geographies:
        parts.append(" ".join(f"location:{g}" for g in geographies[:3]))
    if not parts:
        parts.append("hiring")
    return " ".join(parts).strip()


async def _fetch_profile(
    login: str, client: httpx.AsyncClient
) -> Optional[Dict[str, Any]]:
    try:
        r = await client.get(
            f"{GITHUB_API}/users/{login}", headers=_headers(), timeout=12.0
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("github profile fetch failed for %s: %s", login, exc)
        return None


def _company_to_domain(company: Optional[str]) -> Optional[str]:
    if not company:
        return None
    c = company.strip().lstrip("@").lower()
    if " " in c or not c:
        return None
    if "." in c:
        return c
    return None  # we don't fabricate domains from free-text companies


def _split_name(name: Optional[str]) -> Dict[str, Optional[str]]:
    if not name:
        return {"first_name": None, "last_name": None}
    parts = name.strip().split()
    if not parts:
        return {"first_name": None, "last_name": None}
    if len(parts) == 1:
        return {"first_name": parts[0], "last_name": None}
    return {"first_name": parts[0], "last_name": " ".join(parts[1:])}


async def search_users(
    icp: Dict[str, Any],
    *,
    max_leads: int = 25,
) -> List[Dict[str, Any]]:
    """Return GitHub-derived leads matching the ICP."""
    q = _build_query(icp)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{GITHUB_API}/search/users",
                headers=_headers(),
                params={"q": q, "per_page": min(max_leads, 50)},
            )
            if r.status_code != 200:
                logger.warning("github search HTTP %s: %s", r.status_code, r.text[:200])
                return []
            data = r.json() or {}
            logins = [item.get("login") for item in (data.get("items") or []) if item.get("login")]

            profiles = await asyncio.gather(
                *(_fetch_profile(login, client) for login in logins[:max_leads])
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("github search failed: %s", exc)
        return []

    out: List[Dict[str, Any]] = []
    for prof in profiles:
        if not prof:
            continue
        email = (prof.get("email") or "").strip().lower()
        if not email or "@" not in email:
            continue
        name_parts = _split_name(prof.get("name"))
        out.append(
            {
                "email": email,
                "first_name": name_parts["first_name"],
                "last_name": name_parts["last_name"],
                "role": prof.get("bio"),
                "company_name": prof.get("company"),
                "company_domain": _company_to_domain(prof.get("company")),
                "source": "github",
                "raw": {
                    "login": prof.get("login"),
                    "location": prof.get("location"),
                    "public_repos": prof.get("public_repos"),
                    "followers": prof.get("followers"),
                    "html_url": prof.get("html_url"),
                },
            }
        )
        if len(out) >= max_leads:
            break
    return out


__all__ = ["search_users"]
