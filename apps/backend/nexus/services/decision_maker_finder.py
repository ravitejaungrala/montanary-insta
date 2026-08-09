"""Decision-maker finder — title scoring + cross-source identity check.

Legacy: apps/nexus-legacy/server/services/decisionMakerFinder.js
Exports (legacy): verifyDecisionMaker, getSeniorityScore
"""
from __future__ import annotations
from typing import Any, Dict


def get_seniority_score(title: str) -> int:
    """Legacy: getSeniorityScore(title). Returns 0–100."""
    return 0


async def verify_decision_maker(
    person: Dict[str, Any],
    company: Dict[str, Any],
    icp: Dict[str, Any],
    product_category: str = "",
) -> Dict[str, Any]:
    """Legacy: verifyDecisionMaker(person, company, icp, productCategory)."""
    return {"is_decision_maker": False, "confidence": 0.0, "reasoning": "stub"}
