"""Content-signal finder — conference speakers, blog authors, job changes.

Legacy: apps/nexus-legacy/server/services/contentSignalFinder.js
Exports (legacy): scrapeConferenceSpeakers, scrapeCompanyBlogAuthors,
                  findRecentJobChanges
"""
from __future__ import annotations
from typing import Any, Dict, List


async def scrape_conference_speakers(icp: Dict[str, Any]) -> List[Dict[str, Any]]:
    return []


async def scrape_company_blog_authors(
    domain: str, product_topics: List[str] | None = None
) -> List[Dict[str, Any]]:
    return []


async def find_recent_job_changes(icp: Dict[str, Any]) -> List[Dict[str, Any]]:
    return []
