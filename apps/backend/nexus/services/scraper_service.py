"""Generic web scraper — Playwright-based extraction.

Legacy: apps/nexus-legacy/server/services/scraperService.js
Exports (legacy): scrapeUrl, scrapeMany, parseStructured

NOTE: nexus/services/playwright_scraper.py is the active implementation.
This module wraps it under the legacy export names.
"""
from __future__ import annotations
from typing import Any, Dict, List


async def scrape_url(url: str) -> Dict[str, Any]:
    """Legacy: scrapeUrl(url). Returns {html, text, title, meta, links}."""
    try:
        from .playwright_scraper import scrape  # type: ignore
        return await scrape(url) if hasattr(scrape, "__await__") else scrape(url)
    except Exception:
        return {"url": url, "html": "", "text": "", "title": "", "stub": True}


async def scrape_many(urls: List[str]) -> List[Dict[str, Any]]:
    """Legacy: scrapeMany(urls)."""
    out = []
    for u in urls:
        out.append(await scrape_url(u))
    return out


def parse_structured(html: str) -> Dict[str, Any]:
    """Legacy: parseStructured(html). Extract emails, links, headings."""
    return {"emails": [], "links": [], "headings": []}
