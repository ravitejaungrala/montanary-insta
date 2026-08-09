"""Reddit pain-point discovery — surface intent signals from public threads.

Reddit's public JSON endpoint requires no auth for read-only search. We use
it to find posts mentioning ICP keywords or pain points and return
"intent-signal" lead candidates — these will not have emails, but the parent
service treats them as intent rows rather than full GlobalLead entries.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

REDDIT_BASE = "https://www.reddit.com"
USER_AGENT = "PipelytNexusBot/1.0 (by /u/pipelyt)"


def _build_query(icp: Dict[str, Any]) -> Optional[str]:
    parts: List[str] = []
    for k in ("pain_points", "keywords"):
        for v in icp.get(k) or []:
            v = str(v).strip()
            if v:
                parts.append(f'"{v}"')
    if not parts:
        return None
    return " OR ".join(parts[:6])


async def search_pain_points(
    icp: Dict[str, Any],
    *,
    max_results: int = 25,
    subreddits: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Return Reddit posts shaped as intent-signal candidates."""
    q = _build_query(icp)
    if not q:
        return []

    url = f"{REDDIT_BASE}/search.json"
    params = {"q": q, "limit": min(max_results, 100), "sort": "new", "t": "month"}
    if subreddits:
        params["restrict_sr"] = "on"
        url = f"{REDDIT_BASE}/r/{'+'.join(subreddits)}/search.json"

    try:
        async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": USER_AGENT}) as client:
            r = await client.get(url, params=params)
        if r.status_code != 200:
            logger.warning("reddit HTTP %s: %s", r.status_code, r.text[:200])
            return []
        data = r.json() or {}
        children = ((data.get("data") or {}).get("children")) or []
    except Exception as exc:  # noqa: BLE001
        logger.exception("reddit search failed: %s", exc)
        return []

    signals: List[Dict[str, Any]] = []
    for ch in children[:max_results]:
        d = ch.get("data") or {}
        signals.append(
            {
                "source": "reddit",
                "signal_type": "reddit_pain",
                "title": d.get("title"),
                "permalink": f"{REDDIT_BASE}{d.get('permalink', '')}",
                "subreddit": d.get("subreddit"),
                "author": d.get("author"),
                "score": d.get("score"),
                "created_utc": d.get("created_utc"),
                "selftext": (d.get("selftext") or "")[:600],
            }
        )
    return signals


__all__ = ["search_pain_points"]
