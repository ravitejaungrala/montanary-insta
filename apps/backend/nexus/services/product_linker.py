"""Product linker — find-or-create product + attach to campaign.

Legacy: apps/nexus-legacy/server/services/productLinker.js
Exports (legacy): findOrCreateProduct, linkCampaignToProduct
"""
from __future__ import annotations
from typing import Any, Dict


async def find_or_create_product(p: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy: findOrCreateProduct(p)."""
    return {"id": None, "stub": True}


async def link_campaign_to_product(
    campaign: Dict[str, Any], opts: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    """Legacy: linkCampaignToProduct(campaign, opts)."""
    return {"ok": False, "stub": True}
