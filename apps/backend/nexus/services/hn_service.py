"""Hacker News + DDG search service — public web signals.

Legacy: apps/nexus-legacy/server/services/hnService.js
Exports (legacy): generateSearchQueries, searchDDG, getCompanyNews,
                  getLinkedInContext, getJobSignal, getLeadCompanyContext,
                  buildFallbackQueries
"""
from __future__ import annotations
from typing import Any, Dict, List


async def generate_search_queries(
    icp: Dict[str, Any], product_summary: Dict[str, Any]
) -> List[str]:
    """Legacy: generateSearchQueries(icp, productSummary)."""
    return []


async def search_ddg(query: str) -> List[Dict[str, Any]]:
    """Legacy: searchDDG(query)."""
    return []


async def get_company_news(company_name: str) -> List[Dict[str, Any]]:
    """Legacy: getCompanyNews(companyName)."""
    return []


async def get_linkedin_context(
    linkedin_url: str, name: str, company: str
) -> Dict[str, Any]:
    """Legacy: getLinkedInContext(linkedinUrl, name, company)."""
    return {}


async def get_job_signal(company_name: str, icp_roles: List[str]) -> Dict[str, Any]:
    """Legacy: getJobSignal(companyName, icpRoles)."""
    return {}


async def get_lead_company_context(domain_or_url: str) -> Dict[str, Any]:
    """Legacy: getLeadCompanyContext(domainOrUrl)."""
    return {}


def build_fallback_queries(
    icp: Dict[str, Any], product_summary: Dict[str, Any]
) -> List[str]:
    """Legacy: buildFallbackQueries(icp, productSummary)."""
    return []
