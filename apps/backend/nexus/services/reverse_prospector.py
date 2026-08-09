"""Reverse prospector — find lookalike posts/leads on Reddit + HN.

Legacy: apps/nexus-legacy/server/services/reverseProspector.js
Exports (legacy): searchReddit, searchHN, runProspecting, buildKeywords,
                  scorePost
"""
from __future__ import annotations
from typing import Any, Dict, List


def score_post(title: str, body: str, kw: str, intent_phrase: str) -> int:
    """Legacy: scorePost({ title, body, kw, intentPhrase })."""
    return 0


async def search_reddit(
    subreddit: str, query: str, time: str = "week", limit: int = 10
) -> List[Dict[str, Any]]:
    """Legacy: searchReddit({ subreddit, query, time, limit })."""
    return []


async def search_hn(
    query: str, hours: int = 168, limit: int = 20
) -> List[Dict[str, Any]]:
    """Legacy: searchHN({ query, hours, limit })."""
    return []


def build_keywords(campaign: Dict[str, Any]) -> List[str]:
    """Legacy: buildKeywords(campaign)."""
    return []


async def run_prospecting(
    campaign: Dict[str, Any],
    max_results_per_source: int = 20,
    sources: List[str] | None = None,
) -> Dict[str, Any]:
    """Legacy: runProspecting({...})."""
    return {"posts": [], "leads": [], "stub": True}
