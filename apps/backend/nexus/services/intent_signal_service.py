"""Web-based intent-signal service — public web/social/GitHub mining.

Legacy: apps/nexus-legacy/server/services/intentSignalService.js
Exports (legacy): searchReddit, searchCrunchbase, searchGitHub

NOTE: nexus/services/intent_classifier.py handles INBOUND-REPLY intent
classification. This service is the LEGACY web-signal variant —
distinct purpose, distinct exports.
"""
from __future__ import annotations
from typing import Any, Dict, List


async def search_reddit(pain_points: List[str]) -> List[Dict[str, Any]]:
    """Legacy: searchReddit(painPoints) — find subreddits + posts."""
    return []


async def search_crunchbase(industries: List[str]) -> List[Dict[str, Any]]:
    """Legacy: searchCrunchbase(industries) — funding signals."""
    return []


async def search_github(icp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Legacy: searchGitHub(icp) — repo/issue activity signals."""
    return []
