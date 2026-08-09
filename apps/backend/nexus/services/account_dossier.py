"""Account dossier — talking-points generator for replied leads.

Legacy: apps/nexus-legacy/server/services/accountDossier.js
Exports (legacy): generateAndEmail, renderHtml, generateTalkingPoints

STUB: signatures match legacy 1:1 so devs can drop in the operational
body without touching call-sites. All functions return safe empty
defaults so callers don't crash.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional


async def generate_talking_points(
    lead: Dict[str, Any],
    campaign: Dict[str, Any],
    enrichment: Optional[Dict[str, Any]] = None,
    reply_text: str = "",
) -> List[str]:
    """Generate talking-point bullets for a replied lead via the NIM LLM.
    Legacy: generateTalkingPoints({ lead, campaign, enrichment, replyText })"""
    return []


def render_html(
    lead: Dict[str, Any],
    campaign: Dict[str, Any],
    enrichment: Optional[Dict[str, Any]] = None,
    reply_text: str = "",
    intent: str = "",
    talking_points: Optional[List[str]] = None,
    product_name: str = "",
) -> str:
    """Build the dossier-email HTML body.
    Legacy: renderHtml({ lead, campaign, enrichment, replyText, intent,
                         talkingPoints, productName })"""
    return ""


async def generate_and_email(
    lead: Dict[str, Any],
    campaign: Dict[str, Any],
    intent: str,
    reply_text: str,
    recipient_email: str,
) -> Dict[str, Any]:
    """End-to-end: generate dossier + send to rep's inbox.
    Legacy: generateAndEmail({ lead, campaign, intent, replyText, recipientEmail })"""
    return {"ok": False, "stub": True, "reason": "account_dossier stub"}
