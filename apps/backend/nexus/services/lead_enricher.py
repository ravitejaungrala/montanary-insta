"""Lead enrichment service for NEXUS Phase 3.

Pipeline for a given ``company_domain``:
    1. Fetch homepage HTML, parse title/meta/headings/body snippet.
    2. Infer tech stack from HTTP response headers + meta generators.
    3. Pull top-3 Google News RSS headlines for the company.
    4. Persist into ``nexus_lead_enrichment`` with a 30-day TTL.

All network calls use ``httpx.AsyncClient``. The service degrades gracefully:
any individual step failing returns a partial record rather than raising.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy.orm import Session

from nexus.models_phase3 import NexusLeadEnrichment

logger = logging.getLogger(__name__)

CACHE_TTL = timedelta(days=30)
DEFAULT_TIMEOUT = 12.0
USER_AGENT = "PipelytNexusBot/1.0 (+https://pipelyt.ai)"


# ---------------------------------------------------------------------------
# Tech-stack fingerprints — header + meta-generator signals only.
# ---------------------------------------------------------------------------
_HEADER_SIGNATURES: Dict[str, List[str]] = {
    "server": ["nginx", "apache", "iis", "envoy", "caddy", "cloudfront"],
    "x-powered-by": ["express", "php", "asp.net", "next.js"],
    "cf-ray": ["cloudflare"],
    "x-vercel-id": ["vercel"],
    "x-amz-cf-id": ["cloudfront"],
    "x-served-by": ["fastly"],
    "x-shopify-stage": ["shopify"],
    "x-github-request-id": ["github-pages"],
}

_GENERATOR_HINTS: Dict[str, str] = {
    "wordpress": "wordpress",
    "wix": "wix",
    "shopify": "shopify",
    "webflow": "webflow",
    "hubspot": "hubspot",
    "drupal": "drupal",
    "squarespace": "squarespace",
    "ghost": "ghost",
}


# ---------------------------------------------------------------------------
# Very small HTML extractor — avoids pulling bs4 just for a snippet.
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>", re.S)
_WS_RE = re.compile(r"\s+")


def _strip_tags(html: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html or "")).strip()


def _first(pattern: str, html: str, flags: int = re.I | re.S) -> Optional[str]:
    m = re.search(pattern, html, flags)
    return m.group(1).strip() if m else None


def _all(pattern: str, html: str, flags: int = re.I | re.S) -> List[str]:
    return [m.strip() for m in re.findall(pattern, html, flags)]


def _parse_html(html: str) -> Dict[str, Any]:
    title = _first(r"<title[^>]*>(.*?)</title>", html)
    meta_desc = _first(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html
    )
    h1 = [_strip_tags(t) for t in _all(r"<h1[^>]*>(.*?)</h1>", html)][:6]
    h2 = [_strip_tags(t) for t in _all(r"<h2[^>]*>(.*?)</h2>", html)][:10]
    # Body snippet — drop scripts/styles and clip to ~600 chars.
    body_html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    body_html = re.sub(r"<style[\s\S]*?</style>", " ", body_html, flags=re.I)
    body_snippet = _strip_tags(body_html)[:600]
    generator = _first(
        r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)["\']', html
    )
    return {
        "page_title": title,
        "meta_description": meta_desc,
        "headings": {"h1": h1, "h2": h2},
        "body_snippet": body_snippet,
        "generator": generator,
    }


def _infer_tech_stack(headers: Dict[str, str], generator: Optional[str]) -> List[str]:
    found: List[str] = []
    lower_headers = {k.lower(): str(v) for k, v in headers.items()}
    for header_name, candidates in _HEADER_SIGNATURES.items():
        val = lower_headers.get(header_name)
        if not val:
            continue
        val_lc = val.lower()
        for c in candidates:
            if c in val_lc and c not in found:
                found.append(c)
    if generator:
        g = generator.lower()
        for needle, label in _GENERATOR_HINTS.items():
            if needle in g and label not in found:
                found.append(label)
    return found


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------
async def _fetch_homepage(domain: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    bare = domain.strip()
    for prefix in ("https://", "http://"):
        if bare.lower().startswith(prefix):
            bare = bare[len(prefix):]
            break
    bare = bare.strip("/")
    url = f"https://{bare}"
    try:
        r = await client.get(
            url,
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        html = r.text or ""
        parsed = _parse_html(html)
        parsed["tech_stack"] = _infer_tech_stack(dict(r.headers), parsed.pop("generator", None))
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning("homepage fetch failed for %s: %s", domain, exc)
        return {"tech_stack": []}


async def _fetch_news(domain: str, client: httpx.AsyncClient) -> List[Dict[str, str]]:
    company_name = domain.split(".")[0].replace("-", " ")
    rss = (
        "https://news.google.com/rss/search?q="
        + quote_plus(company_name)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        r = await client.get(rss, timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT})
        root = ET.fromstring(r.text)
        items = root.findall(".//item")[:3]
        out: List[Dict[str, str]] = []
        for it in items:
            out.append(
                {
                    "title": (it.findtext("title") or "").strip(),
                    "link": (it.findtext("link") or "").strip(),
                    "pubDate": (it.findtext("pubDate") or "").strip(),
                }
            )
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("news fetch failed for %s: %s", domain, exc)
        return []


def _apollo_key() -> Optional[str]:
    import os
    try:
        from nexus import config as _cfg
        v = getattr(_cfg, "APOLLO_API_KEY", None)
        if v:
            return v
    except Exception:
        pass
    return os.getenv("APOLLO_API_KEY")


def _clean_list(raw: Any, cap: int) -> List[str]:
    """Normalise an Apollo string-list field, de-duped and capped."""
    out: List[str] = []
    if isinstance(raw, list):
        for v in raw:
            s = str(v).strip()
            if s and s.lower() not in {x.lower() for x in out}:
                out.append(s)
            if len(out) >= cap:
                break
    return out


async def _fetch_apollo_org(domain: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """Apollo organization-enrich → a structured profile of the LEAD'S company.

    This is one request we were already making, but we previously kept only
    ``short_description`` and dropped the rest of the payload — so downstream
    agents knew a prospect's company name and industry and nothing else. The
    same response also carries headcount, revenue, founding year, HQ, what they
    do (keywords) and their tech stack, at no extra credit cost.

    Returns {} on missing key / non-200 / parse error. Every field is optional —
    absent stays absent, never guessed.
    """
    key = _apollo_key()
    if not key:
        return {}
    try:
        r = await client.get(
            "https://api.apollo.io/v1/organizations/enrich",
            params={"domain": domain},
            headers={"X-Api-Key": key, "Cache-Control": "no-cache"},
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code >= 400:
            logger.info("apollo org-enrich HTTP %s for %s", r.status_code, domain)
            return {}
        org = (r.json() or {}).get("organization") or {}
    except Exception as exc:  # noqa: BLE001
        logger.info("apollo org-enrich fetch failed for %s: %s", domain, exc)
        return {}

    if not isinstance(org, dict) or not org:
        return {}

    hq = ", ".join(
        str(x).strip() for x in (org.get("city"), org.get("state"), org.get("country"))
        if str(x or "").strip()
    )
    profile: Dict[str, Any] = {
        "name": (org.get("name") or "").strip() or None,
        "description": (org.get("short_description") or org.get("seo_description") or "").strip() or None,
        "industry": (org.get("industry") or "").strip() or None,
        "headcount": org.get("estimated_num_employees"),
        "revenue": (
            str(org.get("annual_revenue_printed") or org.get("organization_revenue_printed") or "").strip()
            or None
        ),
        "founded_year": org.get("founded_year"),
        "hq": hq or None,
        # What they actually do / sell, in their own taxonomy.
        "keywords": _clean_list(org.get("keywords"), 12),
        # Their stack — the strongest signal for "does our product fit here".
        "technologies": _clean_list(
            org.get("technology_names") or org.get("current_technologies"), 15
        ),
        "linkedin_url": (org.get("linkedin_url") or "").strip() or None,
        "website": (org.get("website_url") or "").strip() or None,
    }
    return {k: v for k, v in profile.items() if v not in (None, "", [])}


def get_cached_enrichment(
    db: Session, company_domain: str
) -> Optional[NexusLeadEnrichment]:
    """Return a non-stale cached enrichment row, or ``None``."""
    if not company_domain:
        return None
    row = (
        db.query(NexusLeadEnrichment)
        .filter(NexusLeadEnrichment.company_domain == company_domain.lower())
        .first()
    )
    if not row:
        return None
    if datetime.utcnow() - row.fetched_at > CACHE_TTL:
        return None
    return row


async def enrich_domain(
    db: Session, company_domain: str, force: bool = False
) -> Optional[NexusLeadEnrichment]:
    """Enrich a single domain, returning the persisted row.

    Honours the 30-day TTL unless ``force=True``.
    """
    if not company_domain:
        return None
    domain = company_domain.lower().strip()

    if not force:
        cached = get_cached_enrichment(db, domain)
        if cached:
            return cached

    async with httpx.AsyncClient() as client:
        homepage_task = asyncio.create_task(_fetch_homepage(domain, client))
        news_task = asyncio.create_task(_fetch_news(domain, client))
        apollo_task = asyncio.create_task(_fetch_apollo_org(domain, client))
        homepage, news, apollo_org = await asyncio.gather(
            homepage_task, news_task, apollo_task
        )
    apollo_overview = (apollo_org or {}).get("description")

    row = (
        db.query(NexusLeadEnrichment)
        .filter(NexusLeadEnrichment.company_domain == domain)
        .first()
    )
    if row is None:
        row = NexusLeadEnrichment(company_domain=domain)
        db.add(row)

    row.page_title = (homepage.get("page_title") or "")[:512] or None
    # Prefer Apollo's rich company "About" overview as the summary; fall back
    # to the homepage <meta description>. The sequencer reads meta_description
    # as `company_summary` for the email opener.
    row.meta_description = apollo_overview or homepage.get("meta_description")
    row.headings = homepage.get("headings")
    row.body_snippet = homepage.get("body_snippet")
    row.tech_stack = homepage.get("tech_stack") or []
    row.news_headlines = news
    # Structured company profile (headcount, revenue, HQ, keywords, stack…).
    # Only overwrite when this run actually got one, so a transient Apollo
    # failure never wipes a good profile off an existing row.
    if apollo_org:
        row.company_profile = apollo_org
    row.fetched_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


async def enrich_many(
    db: Session, company_domains: List[str], force: bool = False
) -> Dict[str, Optional[NexusLeadEnrichment]]:
    """Enrich a batch of domains sequentially (DB session is single-threaded)."""
    out: Dict[str, Optional[NexusLeadEnrichment]] = {}
    for d in set(d for d in company_domains if d):
        out[d] = await enrich_domain(db, d, force=force)
    return out


__all__ = [
    "CACHE_TTL",
    "enrich_domain",
    "enrich_many",
    "get_cached_enrichment",
]
