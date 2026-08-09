"""Campaign funnel aggregation.

Legacy: apps/nexus-legacy/server/services/campaignStats.js
Exports (legacy): statsForCampaign, statsForCampaigns, blank,
                  invalidateCampaignStatsCache

STUB: empty-shape returns so any caller depending on the keys still works.
"""
from __future__ import annotations
from typing import Any, Dict, Iterable, List


def blank() -> Dict[str, Any]:
    """Empty stats object shaped like legacy blank()."""
    return {
        "total_leads": 0,
        "leads_contacted": 0,
        "emails_sent": 0,
        "opens": 0,
        "clicks": 0,
        "replies": 0,
        "demos": 0,
        "bounces": 0,
        "unsubscribes": 0,
        "reply_rate": 0.0,
        "open_rate": 0.0,
        "click_rate": 0.0,
    }


def invalidate_campaign_stats_cache(campaign_id: int | str | None = None) -> None:
    """Clear cached stats. Legacy: invalidateCampaignStatsCache(id?)."""
    return None


async def stats_for_campaign(campaign: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy: statsForCampaign(campaign). Currently returns blank()."""
    return blank()


async def stats_for_campaigns(campaigns: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Legacy: statsForCampaigns([campaign,...])."""
    return [blank() for _ in campaigns]
