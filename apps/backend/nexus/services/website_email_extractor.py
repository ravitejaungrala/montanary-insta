"""Website email extractor — scrape mailto/text emails from a domain.

Legacy: apps/nexus-legacy/server/services/websiteEmailExtractor.js
Exports (legacy): extractWebsiteEmails, batchExtractEmails
"""
from __future__ import annotations
from typing import Any, Dict, List


async def extract_website_emails(domain: str) -> List[str]:
    """Legacy: extractWebsiteEmails(domain). Crawls /contact, /about, etc."""
    return []


async def batch_extract_emails(domains: List[str]) -> Dict[str, List[str]]:
    """Legacy: batchExtractEmails(domains)."""
    return {d: [] for d in domains}
