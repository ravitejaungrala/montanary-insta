"""Competitor intelligence — scrape competitor names + reviewer profiles.

Legacy: apps/nexus-legacy/server/services/competitorIntelligence.js
Exports (legacy): getCompetitorNames, scrapeCompetitorReviews
"""
from __future__ import annotations
from typing import Any, Dict, List


async def get_competitor_names(product_name: str) -> List[str]:
    """Legacy: getCompetitorNames(productName) -> [name,...]."""
    return []


async def scrape_competitor_reviews(competitor_names: List[str]) -> List[Dict[str, Any]]:
    """Legacy: scrapeCompetitorReviews(names) -> [{reviewer, source, ...}]."""
    return []
