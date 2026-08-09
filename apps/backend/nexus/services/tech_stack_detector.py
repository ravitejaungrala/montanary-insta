"""Tech-stack detector — Wappalyzer-style fingerprinting.

Legacy: apps/nexus-legacy/server/services/techStackDetector.js
Exports (legacy): detectTechStack, batchDetectTechStacks
"""
from __future__ import annotations
from typing import Any, Dict, List


async def detect_tech_stack(domain: str) -> Dict[str, Any]:
    """Legacy: detectTechStack(domain) -> { technologies: [], categories: [] }."""
    return {"domain": domain, "technologies": [], "categories": [], "stub": True}


async def batch_detect_tech_stacks(domains: List[str]) -> Dict[str, Dict[str, Any]]:
    """Legacy: batchDetectTechStacks(domains)."""
    return {d: await detect_tech_stack(d) for d in domains}
