"""LinkedIn service — message generation + (stubbed) send/connection inference.

Legacy: apps/nexus-legacy/server/services/linkedinService.js

`generate_message` is a real Gemini call. The send/fetch_connections
paths remain stubs because LinkedIn has no public DM API — messages
get queued for the user to send manually.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Mapping

from nexus.services import gemini

log = logging.getLogger("nexus.linkedin_service")

GEMINI_MODEL = gemini.CHAT_MODEL  # single source of truth (nexus/services/gemini.py)
MAX_CONNECT_NOTE = 280  # LinkedIn connection-request note hard cap (300 incl trailing)
MAX_DM_BODY = 700       # First DM should be short; longer reduces reply rate


_BANNED = re.compile(
    r"\b(leverage|synergy|game[- ]?changer|disruptive|innovative|"
    r"revolutionize|transform|cutting[- ]edge|best[- ]in[- ]class|"
    r"world[- ]class|seamless|holistic|touch base|circle back|"
    r"supercharge|empower|10x|unlock\b)",
    re.IGNORECASE,
)


def _fallback_template(
    lead: Mapping[str, Any], product: Mapping[str, Any], message_type: str
) -> str:
    """Deterministic fallback when Gemini call fails or returns garbage."""
    first = (lead.get("first_name") or "there").strip()
    company = (lead.get("company_name") or "your team").strip()
    product_name = (product.get("name") or "what we're building").strip()
    if message_type == "connect":
        return (
            f"Hi {first}, came across {company} and what you're working on. "
            f"Building {product_name} — would love to connect."
        )
    return (
        f"Hi {first}, saw your work at {company}. We're building "
        f"{product_name} — figured this might be relevant. Open to a quick chat?"
    )


def _system_prompt(message_type: str) -> str:
    if message_type == "connect":
        return (
            "You write short LinkedIn connection-request notes for B2B sales. "
            "Plain text only. Under 280 characters TOTAL. No subject line. "
            "No greeting fluff, no emojis, no hashtags. Reference one concrete "
            "thing about the recipient or their company. End with a soft "
            "reason-to-connect, not a pitch. NEVER use words like leverage, "
            "synergy, game-changer, disrupt, transform, innovative, cutting-edge, "
            "world-class, supercharge, 10x, unlock, empower."
        )
    return (
        "You write short LinkedIn direct messages for B2B sales. Plain text "
        "only. Under 600 characters. No subject. No greeting fluff, no emojis, "
        "no hashtags. Reference one specific thing about the recipient or "
        "their company, then a one-line value pitch tied to their role/work, "
        "then a soft single-sentence CTA. NEVER use leverage, synergy, "
        "game-changer, disrupt, transform, innovative, cutting-edge, "
        "world-class, supercharge, 10x, unlock, empower."
    )


def _user_prompt(
    lead: Mapping[str, Any],
    product: Mapping[str, Any],
    enrichment: Mapping[str, Any],
    step: int,
    message_type: str,
) -> str:
    parts: List[str] = []
    parts.append(f"Recipient: {lead.get('first_name', '')} {lead.get('last_name', '')}".strip())
    if lead.get("title"):
        parts.append(f"Role: {lead['title']}")
    if lead.get("company_name"):
        parts.append(f"Company: {lead['company_name']}")
    if lead.get("linkedin_url"):
        parts.append(f"LinkedIn: {lead['linkedin_url']}")
    if enrichment:
        snippets = []
        for k in ("recent_news", "tech_stack", "hiring_signals", "summary"):
            v = enrichment.get(k) if isinstance(enrichment, dict) else None
            if v:
                snippets.append(f"{k}: {v}")
        if snippets:
            parts.append("Researched signals:\n" + "\n".join(snippets))
    parts.append("")
    parts.append(f"Your product: {product.get('name', '(unnamed)')}")
    if product.get("value_prop"):
        parts.append(f"Value prop: {product['value_prop']}")
    if product.get("icp_pain"):
        parts.append(f"ICP pain: {product['icp_pain']}")
    parts.append("")
    if message_type == "connect":
        parts.append(
            "Write the connection-request note. Under 280 chars. Plain text. "
            "Return ONLY the note text — no quotes, no preamble, no labels."
        )
    else:
        parts.append(
            f"This is step {step} of an outbound sequence. Write the direct "
            "message. Under 600 chars. Plain text. Return ONLY the message "
            "body — no preamble, no labels, no salutation more than one line."
        )
    return "\n".join(parts)


def _generate_one(
    lead: Mapping[str, Any],
    product: Mapping[str, Any],
    enrichment: Mapping[str, Any],
    step: int,
    message_type: str,
) -> str:
    try:
        raw = gemini.chat_completion(
            system=_system_prompt(message_type),
            user=_user_prompt(lead, product, enrichment, step, message_type),
            temperature=0.5,
            max_tokens=400,
            response_format_json=False,
        )
        text = (raw or "").strip().strip('"').strip("'")
        # First-pass guard against banned phrases — one retry budget.
        if _BANNED.search(text):
            log.info("nexus.linkedin: retrying after banned phrase hit")
            try:
                raw2 = gemini.chat_completion(
                    system=_system_prompt(message_type)
                    + " You used a banned word last time. Rewrite without ANY of those words.",
                    user=_user_prompt(lead, product, enrichment, step, message_type),
                    temperature=0.5,
                    max_tokens=400,
                    response_format_json=False,
                )
                text2 = (raw2 or "").strip().strip('"').strip("'")
                if text2 and not _BANNED.search(text2):
                    text = text2
            except Exception:
                log.exception("nexus.linkedin: retry call failed")
        # Hard-cap final length
        cap = MAX_CONNECT_NOTE if message_type == "connect" else MAX_DM_BODY
        if len(text) > cap:
            text = text[:cap].rsplit(" ", 1)[0].rstrip(",.;:-") + "…"
        return text
    except Exception:
        log.exception("nexus.linkedin: Gemini call failed — using fallback")
        return _fallback_template(lead, product, message_type)


async def generate_message(
    lead: Dict[str, Any], campaign: Dict[str, Any], step: int = 0
) -> Dict[str, Any]:
    """Generate a personalized LinkedIn message for `lead`.

    `campaign` should carry a `product` dict with name/value_prop/icp_pain
    keys; if missing we fall back to the product fields nested inside
    campaign itself. `step=0` produces a connection-request note (≤280
    chars). Step 1+ produces direct-message bodies (≤600 chars).
    """
    product = dict(
        campaign.get("product")
        or {
            "name": campaign.get("name"),
            "value_prop": campaign.get("value_prop"),
            "icp_pain": campaign.get("icp_pain"),
        }
    )
    enrichment = dict(campaign.get("enrichment") or lead.get("enrichment") or {})
    message_type = "connect" if step == 0 else "dm"
    body = _generate_one(lead, product, enrichment, step, message_type)
    # LinkedIn messages have no subject — return empty string for parity
    # with email gen so downstream callers can unify shapes.
    return {
        "subject": "",
        "body": body,
        "model_used": GEMINI_MODEL,
        "message_type": message_type,
    }


async def send_message(lead_id: int | str, body: str) -> Dict[str, Any]:
    """LinkedIn has no public DM API — messages are queued for the user
    to send manually. Returns ok=False, stub=True so callers can branch."""
    return {"ok": False, "stub": True, "note": "LinkedIn has no public DM API"}


async def fetch_connections(user_id: int | str) -> List[Dict[str, Any]]:
    """Legacy: fetchConnections(userId). Not implemented — would need an
    OAuth flow against a partner LinkedIn API."""
    return []


def estimate_connection_degree(
    user: Dict[str, Any], lead: Dict[str, Any]
) -> int:
    """Legacy: estimateConnectionDegree(user, lead). Without graph access
    we default to 3rd degree."""
    return 3
