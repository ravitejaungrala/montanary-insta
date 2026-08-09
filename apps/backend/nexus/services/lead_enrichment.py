"""Lightweight Gemini-driven lead enrichment.

Given a lead (name, title, company, domain) and the user's outbound
product/service context, produce a small grounded enrichment dict the
sequencer can feed into personalize.generate_email. Lives between
"no enrichment at all" (today's floor) and "wire every intel service"
(weeks of work).

★ HARD RULE ★ Gemini has NO live web access here, so this function
must NOT invent specific facts about the lead's company — no funding
rounds, no headcount, no recent news, no customers, no revenue. It
sticks to role-and-industry-typical signals + bridges to the
outbound product. Empty fields are preferred over fabrications.

Used in two places:
  - sequencer.process_due_sequences (lazy enrich just before first send
    when the lead row's enrichment column is empty)
  - smoke test (direct call for verification)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Mapping

from nexus.services import gemini

log = logging.getLogger("nexus.lead_enrichment")

GEMINI_MODEL = gemini.CHAT_MODEL  # single source of truth (nexus/services/gemini.py)


_ENRICHMENT_SYSTEM = """You generate grounded enrichment hooks for B2B cold-outreach personalization. The cold-email writer will use your output to make the message specific to this lead.

Inputs you receive:
  - a lead (name, title, company, domain, linkedin_url)
  - the OUTBOUND product/service being pitched

What you produce: a small JSON object with role-and-industry-typical signals plus bridges to the outbound product.

═══════════════════════════════════════════════════════════════════════
ABSOLUTE NO-HALLUCINATION RULE
═══════════════════════════════════════════════════════════════════════
You have NO live web access. NEVER invent specific facts about the
lead's company:
  ❌ no funding rounds, no valuations, no headcount numbers
  ❌ no "recent news" (you don't know what's recent)
  ❌ no specific customer names of theirs
  ❌ no revenue figures, percentages, growth metrics
  ❌ no specific events ("they just launched X", "they hired Y")

What you ARE allowed to infer:
  ✅ the kind of pain a person in this ROLE typically faces
  ✅ pain that someone in this INDUSTRY (if implied by company name)
     typically faces
  ✅ how those pains might bridge to the outbound product's value
  ✅ widely-recognized platforms strongly implied by the role
     (a "Salesforce Admin" almost certainly uses Salesforce)

When uncertain, prefer an empty array or short string. Better empty
than fabricated. The writer can write a generic email — they cannot
unsay a hallucinated fact.

═══════════════════════════════════════════════════════════════════════
OUTPUT SCHEMA
═══════════════════════════════════════════════════════════════════════
Return ONLY a JSON object. No markdown, no preamble, no comments.

{
  "common_pain":       "one sentence describing the typical pain for this role at this kind of company. Generic but truthful.",
  "talking_points":    ["2 to 4 bridge points connecting their likely concerns to the outbound product. Each point should be a complete short sentence."],
  "likely_tech_stack": ["only platforms STRONGLY implied by role/industry; empty array if you have to guess"],
  "industry_hint":     "your best inference of the company's industry from its name/domain; empty string if unclear",
  "rationale":         "one sentence explaining the basis of your inferences — helps the email writer judge confidence"
}
"""


def _build_user_prompt(lead: Mapping[str, Any], product: Mapping[str, Any]) -> str:
    parts: List[str] = [
        "Lead:",
        f"  Name: {lead.get('first_name') or ''} {lead.get('last_name') or ''}".strip(),
        f"  Title: {lead.get('title') or '(unknown)'}",
        f"  Company: {lead.get('company_name') or '(unknown)'}",
        f"  Company domain: {lead.get('company_domain') or '(unknown)'}",
    ]
    if lead.get("linkedin_url"):
        parts.append(f"  LinkedIn: {lead['linkedin_url']}")
    parts.append("")
    parts.append("Outbound product/service:")
    parts.append(f"  Name: {product.get('name') or '(unnamed)'}")
    if product.get("value_prop"):
        parts.append(f"  What it does: {product['value_prop']}")
    if product.get("icp_pain"):
        parts.append(f"  ICP pain it solves: {product['icp_pain']}")
    return "\n".join(parts)


def enrich_lead(
    lead: Mapping[str, Any], product: Mapping[str, Any]
) -> Dict[str, Any]:
    """Returns a grounded enrichment dict, or {} on failure.

    The shape MUST be JSON-serialisable since the caller may persist it
    directly into `nexus_global_leads.enrichment` (JSONB column).
    """
    raw = ""
    try:
        raw = gemini.chat_completion(
            system=_ENRICHMENT_SYSTEM,
            user=_build_user_prompt(lead, product),
            temperature=0.3,
            # 1200 was too tight when the system prompt + talking_points
            # array got long — Gemini truncated mid-JSON and the parser
            # rejected the result, falling through to empty enrichment.
            max_tokens=2000,
            response_format_json=True,
        )
        log.info("lead_enrichment: Gemini raw len=%d", len(raw or ""))
        if not raw or not raw.strip():
            log.warning("lead_enrichment: Gemini returned empty body")
            return {}
    except Exception as exc:
        log.exception("nexus.lead_enrichment: Gemini call raised: %s", exc)
        return {}

    try:
        parsed = gemini.extract_json(raw)
    except Exception as exc:
        log.warning(
            "nexus.lead_enrichment: JSON parse failed (%s). First 300 chars: %r",
            exc,
            raw[:300],
        )
        return {}

    if not isinstance(parsed, dict):
        log.warning(
            "nexus.lead_enrichment: parsed is %s, expected dict. Value: %r",
            type(parsed).__name__,
            str(parsed)[:200],
        )
        return {}

    talking = [
        str(t).strip()
        for t in (parsed.get("talking_points") or [])
        if isinstance(t, (str, int, float)) and str(t).strip()
    ][:4]
    stack = [
        str(s).strip()
        for s in (parsed.get("likely_tech_stack") or [])
        if isinstance(s, (str, int, float)) and str(s).strip()
    ][:6]

    return {
        "common_pain": str(parsed.get("common_pain") or "").strip()[:400],
        "talking_points": talking,
        "likely_tech_stack": stack,
        "industry_hint": str(parsed.get("industry_hint") or "").strip()[:80],
        "rationale": str(parsed.get("rationale") or "").strip()[:400],
        "_source": "gemini_lightweight",
        "_model": GEMINI_MODEL,
    }
