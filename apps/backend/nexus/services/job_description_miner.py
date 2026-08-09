"""Job-description miner — extract pains / tools / team size from JD text.

Legacy: apps/nexus-legacy/server/services/jobDescriptionMiner.js
Exports (legacy): mineJobDescription
"""
from __future__ import annotations
from typing import Any, Dict, List


async def mine_job_description(
    jd_text: str,
    product_pain_points: List[str] | None = None,
    competitor_tools: List[str] | None = None,
) -> Dict[str, Any]:
    """Legacy: mineJobDescription(jdText, productPainPoints, competitorTools)."""
    return {"pains": [], "tools": [], "team_size": 0, "seniority": ""}
