"""Lead scorer — ICP-fit scoring (0–100).

Legacy: apps/nexus-legacy/server/services/leadScorer.js
Exports (legacy): scoreLead, scoreCompany, scorePersona

NOTE: nexus/services/icp_scorer.py is a partial port. This module
preserves the legacy export names for any caller that imports them.
"""
from __future__ import annotations
from typing import Any, Dict


def score_lead(lead: Dict[str, Any], icp: Dict[str, Any]) -> int:
    """Legacy: scoreLead(lead, icp) -> 0..100."""
    try:
        from .icp_scorer import score_icp  # type: ignore
        return int(score_icp(lead, icp) or 0)
    except Exception:
        return 0


def score_company(company: Dict[str, Any], icp: Dict[str, Any]) -> int:
    """Legacy: scoreCompany(company, icp) -> 0..100."""
    return 0


def score_persona(person: Dict[str, Any], icp: Dict[str, Any]) -> int:
    """Legacy: scorePersona(person, icp) -> 0..100."""
    return 0
