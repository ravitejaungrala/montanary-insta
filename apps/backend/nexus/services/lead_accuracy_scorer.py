"""Lead accuracy scorer — confidence (0–1) on each enriched lead.

Legacy: apps/nexus-legacy/server/services/leadAccuracyScorer.js
Exports (legacy): scoreLeadAccuracy, assignTier,
                  assembleBestPersonalizationSignal
"""
from __future__ import annotations
from typing import Any, Dict


def score_lead_accuracy(lead: Dict[str, Any]) -> float:
    """Legacy: scoreLeadAccuracy(lead) -> float 0..1."""
    return 0.0


def assign_tier(score: float) -> str:
    """Legacy: assignTier(score). Returns 'A' / 'B' / 'C' / 'D'."""
    if score >= 0.85:
        return "A"
    if score >= 0.65:
        return "B"
    if score >= 0.4:
        return "C"
    return "D"


def assemble_best_personalization_signal(lead: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy: assembleBestPersonalizationSignal(lead).
    Picks the strongest available signal for the cold email opener."""
    return {"source": "", "text": "", "confidence": 0.0}
