"""Job-board scraping (Greenhouse, Lever, Ashby) for pain signals.

Legacy: apps/nexus-legacy/server/services/jobBoardService.js
Exports (legacy): searchDDGJobBoard, getGreenhouseJobs, getLeverJobs,
                  getAshbyJobs, extractPainSignal
"""
from __future__ import annotations
from typing import Any, Dict, List


async def search_ddg_job_board(query: str) -> List[Dict[str, Any]]:
    return []


async def get_greenhouse_jobs(slug: str) -> List[Dict[str, Any]]:
    return []


async def get_lever_jobs(slug: str) -> List[Dict[str, Any]]:
    return []


async def get_ashby_jobs(slug: str) -> List[Dict[str, Any]]:
    return []


async def extract_pain_signal(description: str) -> Dict[str, Any]:
    """Legacy: extractPainSignal(description). LLM-mines pain points."""
    return {"pains": [], "tools_used": [], "team_size": 0}
