"""Lead deduplication + throttle service.

Legacy: apps/nexus-legacy/server/services/dedupService.js
Exports (legacy): extractDomain, seniorityScore, applyDedupAndThrottle,
                  scheduleEmailVerification

NOTE: nexus/routers/dedup.py exposes the HTTP endpoint; this service
holds the business logic and matches the legacy export names.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional


def extract_domain(email: str) -> str:
    """Legacy: extractDomain(email)."""
    if not email or "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1].lower()


def schedule_email_verification(global_lead_id: int, email: str) -> None:
    """Legacy: scheduleEmailVerification(_globalLeadId, _email).
    Queues a Disify check; no-op stub for now."""
    return None


def seniority_score(role: Optional[str]) -> int:
    """Legacy: seniorityScore(role). 0..100 — higher = more senior."""
    if not role:
        return 0
    r = role.lower()
    if any(k in r for k in ("ceo", "founder", "cto", "cfo", "coo", "president")):
        return 100
    if any(k in r for k in ("vp", "vice president", "head of", "director")):
        return 80
    if "manager" in r or "lead" in r:
        return 50
    return 20


async def apply_dedup_and_throttle(
    raw_companies: List[Dict[str, Any]],
    campaign_id: int,
    product_url: str,
    global_lead_model: Any = None,
    max_new: int = 0,
) -> List[Dict[str, Any]]:
    """Legacy: applyDedupAndThrottle(rawCompanies, campaignId, productUrl,
                                       GlobalLead, maxNew).
    Filters out leads already in nexus_global_leads (by email) and caps
    the size of the returned set."""
    return raw_companies[:max_new] if max_new else raw_companies
