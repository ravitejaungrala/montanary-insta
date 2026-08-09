"""Context-research agent — pre-demo briefing orchestrator.

Legacy: apps/nexus-legacy/server/services/contextResearchAgent.js
Exports (legacy): runContextResearch, refineBriefing
"""
from __future__ import annotations
from typing import Any, Dict


async def fetch_product_context(booking: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy: fetchProductContext(booking) — gather product summary +
    ICP for the booking's lead."""
    return {}


async def synthesize_briefing(
    lead: Dict[str, Any],
    product: Dict[str, Any],
    campaign: Dict[str, Any],
    booking: Dict[str, Any],
) -> Dict[str, Any]:
    """Legacy: synthesizeBriefing({ lead, product, campaign, booking })."""
    return {"about_person": "", "about_company": "", "talking_points": [],
            "questions_to_ask": [], "likely_objections": []}


def build_briefing_email(
    briefing: Dict[str, Any],
    lead: Dict[str, Any],
    booking: Dict[str, Any],
    product: Dict[str, Any],
) -> str:
    """Legacy: buildBriefingEmail({ briefing, lead, booking, product })."""
    return ""


async def run_context_research(
    booking: Dict[str, Any], force: bool = False
) -> Dict[str, Any]:
    """Legacy: runContextResearch(booking, { force = false }).
    Entry point invoked by /journey/leads/:id/demo/regenerate."""
    return {"ok": False, "stub": True}


async def refine_briefing(booking_id: int | str, user_prompt: str) -> Dict[str, Any]:
    """Legacy: refineBriefing(bookingId, userPrompt)."""
    return {"ok": False, "stub": True}
