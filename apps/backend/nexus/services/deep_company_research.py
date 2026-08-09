"""Deep company research — multi-layer firmographic qualification.

Legacy: apps/nexus-legacy/server/services/deepCompanyResearch.js
Exports (legacy): qualifyCompany
"""
from __future__ import annotations
from typing import Any, Dict


async def qualify_company(
    candidate: Dict[str, Any], icp: Dict[str, Any], campaign: Dict[str, Any]
) -> Dict[str, Any]:
    """Legacy: qualifyCompany(candidate, icp, campaign).
    Returns {qualified: bool, score: 0-100, signals: [...], reasoning: str}."""
    return {"qualified": False, "score": 0, "signals": [], "reasoning": "stub"}
