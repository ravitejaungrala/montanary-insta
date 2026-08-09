"""LinkedIn discovery — DDG-search-based profile finder.

Legacy: apps/nexus-legacy/server/services/linkedinDiscovery.js
Exports (legacy): searchLinkedIn
"""
from __future__ import annotations
from typing import Any, Dict, List


async def search_ddg_linkedin(query: str) -> List[Dict[str, Any]]:
    """Legacy: searchDDGLinkedIn(query). Returns LinkedIn-style hits via DDG."""
    return []


def parse_linkedin_title(title: str) -> Dict[str, str]:
    """Legacy: parseLinkedInTitle(title). Splits 'Name — Title at Company'."""
    return {"name": "", "title": "", "company": ""}


async def search_linkedin(icp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Legacy: searchLinkedIn(icp). End-to-end discovery for ICP roles."""
    return []
