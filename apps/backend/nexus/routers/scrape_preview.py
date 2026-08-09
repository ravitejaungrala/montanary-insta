"""Scrape-preview router — the "show me what you'll write about" step
of the NewCampaign wizard.

Legacy: apps/nexus-legacy/server/routes/scrapePreview.js

Request:
    POST /nexus/scrape-preview
    { "product_url": "https://example.com" }

Response:
    {
      "product_name":  "Example",
      "summary_text":  "Human-readable, multi-line summary…",
      "favicon_url":   "https://icons.duckduckgo.com/ip3/example.com.ico",
      "brand_colors":  [],
      "raw_analysis":  { name, category, value_proposition, icp: {...} }
    }

Does NOT create any Product/Campaign rows — this is a preview only. The
final commit happens when the user clicks Launch and the frontend POSTs
to /nexus/analyze.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_common import require_current_workspace
from nexus.services import gemini, playwright_scraper

logger = logging.getLogger("pipelyt.nexus.scrape_preview")

router = APIRouter(prefix="/nexus/scrape-preview", tags=["nexus-scrape-preview"])


class _ContentResult:
    """Scrape-result stand-in for the no-URL (paste/upload) path. Carries the
    user's content as .text/.combined_text so analyze_product + the summary
    builder work unchanged. No truncation."""

    def __init__(self, text: str):
        self.text = text or ""
        self.combined_text = self.text
        self.char_count = len(self.text)
        self.title = ""
        self.meta_description = ""
        self.h1s: List[str] = []
        self.h2s: List[str] = []


def _normalise_url(raw: str) -> Optional[str]:
    u = (raw or "").strip()
    if not u:
        return None
    if not re.match(r"^https?://", u, re.IGNORECASE):
        u = f"https://{u}"
    try:
        parsed = urlparse(u)
        if not parsed.hostname or "." not in parsed.hostname:
            return None
    except Exception:
        return None
    return u


def _favicon_for(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        host = host.lstrip("www.")
        return f"https://icons.duckduckgo.com/ip3/{host}.ico"
    except Exception:
        return ""


_TAGLINE_STARTERS = re.compile(
    r"^(turn|grow|build|scale|boost|power|drive|make|get|help|create|discover|unleash)\b",
    re.IGNORECASE,
)


def _looks_like_tagline(s: str) -> bool:
    if not s:
        return False
    if len(s) > 60:
        return True
    if _TAGLINE_STARTERS.search(s.strip()):
        return True
    # Multiple sentences signal — period followed by capital letter.
    if re.search(r"[.!?][^a-z0-9]*[A-Z]", s):
        return True
    return False


def _extract_product_name(
    analysis: Dict[str, Any], page_title: str, domain: str
) -> str:
    """Prefer the cleanest short brand-like fragment over the AI's full name."""
    ai_name = (analysis.get("name") or "").strip()
    title = (page_title or "").strip()

    if title:
        parts = [p.strip() for p in re.split(r"[|–—\-·•]", title) if p.strip()]
        if parts:
            candidate = sorted(parts, key=len)[0]
            if 2 <= len(candidate) <= 40 and not _looks_like_tagline(candidate):
                return candidate
        if len(title) <= 50 and not _looks_like_tagline(title):
            return title

    if ai_name and not _looks_like_tagline(ai_name):
        return ai_name

    return domain


def _build_summary_text(analysis: Dict[str, Any], scraped, product_name: str = "") -> str:
    """Compose a clean, multi-line summary from the structured Gemini output.

    Reads as natural prose — no machine labels like "One-liner:" or
    "Category:" because users see this directly in the editable
    description bubble. Wording adapts to entity_type (product vs.
    service) so a consultancy doesn't read like "What you get:" while
    a SaaS doesn't read like "What we deliver:".
    """
    icp = analysis.get("icp") or {}
    is_service = (icp.get("entity_type") or "product").lower() == "service"

    name = (product_name or analysis.get("name") or "").strip()
    category = (analysis.get("category") or "").strip()
    one_liner = (analysis.get("value_proposition") or "").strip()
    pricing = (analysis.get("pricing_tier") or "").strip().lower()
    industry_relevance = (analysis.get("industry_relevance") or "").strip()
    industries = [i for i in (icp.get("industries") or []) if i]
    roles = [r for r in (icp.get("roles") or []) if r]
    pains = [p for p in (icp.get("pain_points") or []) if p][:3]
    benefits = [b for b in (analysis.get("key_benefits") or []) if b][:4]
    tech_hints = [t for t in (icp.get("tech_stack_hints") or []) if t][:3]

    lines: List[str] = []

    if name and category:
        lines.append(f"{name} — {category}")
        lines.append("")
    elif name:
        lines.append(name)
        lines.append("")
    elif category:
        lines.append(category)
        lines.append("")

    if one_liner:
        sentence = one_liner if one_liner.endswith(".") else f"{one_liner}."
        lines.append(sentence)

    # Audience line — services say "We help X in Y", products say "Built for X in Y".
    audience_industry = industries[0] if industries else industry_relevance
    if is_service:
        if roles and audience_industry:
            lines.append(f"We help {roles[0]}s in {audience_industry}.")
        elif audience_industry:
            lines.append(f"We work with companies in {audience_industry}.")
        elif roles:
            lines.append(f"We work with {roles[0]}s.")
    else:
        if roles and audience_industry:
            lines.append(f"Built for {roles[0]} in {audience_industry}.")
        elif roles:
            lines.append(f"Built for {roles[0]}.")
        elif audience_industry:
            lines.append(f"Built for teams in {audience_industry}.")

    if pains:
        lines.append("")
        lines.append("Key challenges it solves:" if not is_service else "Problems we solve:")
        lines.extend(f"- {p}" for p in pains)

    if benefits:
        lines.append("")
        lines.append("What you get:" if not is_service else "What we deliver:")
        lines.extend(f"- {b}" for b in benefits)

    if tech_hints:
        lines.append("")
        if is_service:
            lines.append(f"Works across stacks like: {', '.join(tech_hints)}.")
        else:
            lines.append(f"Integrates with: {', '.join(tech_hints)}.")

    # Pricing — both products and services have natural phrasings here.
    pricing_phrases_product = {
        "free": "Free to use.",
        "freemium": "Free tier available, paid plans unlock more.",
        "paid": "Paid product.",
        "enterprise": "Enterprise pricing — contact sales for a quote.",
    }
    pricing_phrases_service = {
        "project": "Engagements priced per project.",
        "retainer": "Available on a monthly retainer.",
        "hourly": "Billed hourly.",
        "enterprise": "Enterprise engagements — contact for a scoped quote.",
    }
    phrases = pricing_phrases_service if is_service else pricing_phrases_product
    if pricing in phrases:
        lines.append("")
        lines.append(phrases[pricing])

    result = "\n".join(lines).strip()
    if result:
        return result

    # Fallback when Gemini gave us nothing usable (JS-heavy site, paywall, etc.)
    fallback: List[str] = []
    if scraped.meta_description and len(scraped.meta_description) > 20:
        fallback.append(scraped.meta_description.strip())
    if scraped.title and scraped.title not in fallback:
        fallback.append(scraped.title.strip())
    # Last resort: first ~600 chars of body text.
    if not fallback and scraped.text:
        fallback.append(scraped.text[:600].strip())
    return "\n\n".join(fallback).strip()


@router.post("/rebuild")
def rebuild_summary(
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Re-render the Product Description from a cached raw_analysis with
    a different entity_type override. Cheap — does NOT re-scrape or
    re-call Gemini. The wizard uses this when the user toggles the
    product/service pill so the description text flips wording to match.
    """
    raw_analysis = body.get("raw_analysis") or {}
    product_name = (body.get("product_name") or "").strip()
    entity_type_override = (body.get("entity_type") or "").strip().lower()

    if not isinstance(raw_analysis, dict) or not raw_analysis:
        raise HTTPException(status_code=400, detail="raw_analysis is required")

    # Override the entity_type in the in-memory copy of the analysis so
    # _build_summary_text branches on the user's choice.
    icp = dict(raw_analysis.get("icp") or {})
    if entity_type_override in ("product", "service", "gcc"):
        icp["entity_type"] = entity_type_override
    patched = dict(raw_analysis)
    patched["icp"] = icp

    # _build_summary_text needs a `scraped` object with .meta_description /
    # .title / .text attributes for its fallback path. We weren't given
    # one (the caller is just re-rendering), so a tiny shim is enough.
    class _NoScraped:
        meta_description = ""
        title = ""
        text = ""

    summary = _build_summary_text(patched, _NoScraped(), product_name=product_name)
    return {
        "summary_text": summary,
        "entity_type": icp.get("entity_type") or "product",
    }


@router.post("")
async def scrape_preview(
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Scrape a URL (or use pasted/uploaded content), run it through Gemini,
    return a human-readable summary + ICP triple. When `content` is provided the
    user has no website — we skip scraping and feed the content straight in."""
    # Accept both `product_url` (legacy wizard) and `url` (port callers).
    raw = body.get("product_url") or body.get("url") or ""
    content_in = str(body.get("content") or "").strip()
    content_mode = bool(content_in)
    raw_name = str(body.get("product_name") or "").strip()
    url = None if content_mode else _normalise_url(str(raw))
    if not content_mode and not url:
        raise HTTPException(status_code=400, detail="A valid product_url or content is required")

    # Optional explicit override — the wizard now lets the user pick
    # Product vs Service at the URL step. When sent we lock the
    # entity_type to that value and skip Gemini's own classification.
    entity_type_override = (str(body.get("entity_type") or "")).strip().lower()
    # 2026-05-28 — accept "gcc" as a 3rd entity_type alongside
    # product/service. See `_ANALYZE_SYSTEM` in nexus/services/gemini.py
    # for the three-type classification rubric.
    if entity_type_override not in ("product", "service", "gcc"):
        entity_type_override = ""

    if content_mode:
        # No-URL path: use the pasted/uploaded content directly (no scrape,
        # no char limit). `_ContentResult` carries it as .text so analyze_product
        # + the summary builder work unchanged.
        scrape_result = _ContentResult(content_in)
        bundle_for_gemini = scrape_result
    else:
        try:
            # fetch_bundle gives Gemini homepage + signal-rich subpages. 120s
            # UX deadline (not a scrape ceiling); reuses the warm scrape cache.
            import time as _t
            cached_bundle = playwright_scraper.get_cached_bundle(url, db=db)
            if cached_bundle is not None:
                scrape_bundle = cached_bundle
                logger.info(
                    "[TIMING] Scrape cache HIT — skipped Jina entirely (instant) for %s",
                    url,
                )
            else:
                preview_deadline = _t.monotonic() + 120.0
                scrape_bundle = await playwright_scraper.fetch_bundle(
                    url, retries=2, deadline_ts=preview_deadline
                )
                # Store for the next Regenerate click + the /analyze launch.
                playwright_scraper.cache_bundle(url, scrape_bundle, db=db)
        except Exception as exc:  # noqa: BLE001
            logger.exception("scrape-preview scrape failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail=f"Could not reach the website: {exc}",
            ) from exc

        scrape_result = scrape_bundle.homepage
        if not scrape_result or not scrape_result.text:
            raise HTTPException(
                status_code=422,
                detail="The page returned no extractable text. Try a different URL.",
            )
        bundle_for_gemini = scrape_bundle

    try:
        # Pass the bundle (or content) so the type-specific Gemini prompt runs.
        analysis = gemini.analyze_product(
            url=url or "",
            scrape_result=bundle_for_gemini,
            description=None,
            entity_type=entity_type_override or None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("scrape-preview LLM call failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"AI summary generation failed: {exc}",
        ) from exc

    hostname = (urlparse(url).hostname or "") if url else ""
    domain = hostname[4:] if hostname.startswith("www.") else hostname

    # User's explicit choice wins over Gemini's auto-detection.
    if entity_type_override:
        icp_mut = dict(analysis.get("icp") or {})
        icp_mut["entity_type"] = entity_type_override
        icp_mut["confidence"] = "high"  # user said so
        icp_mut["rationale"] = "Set by user at the URL step."
        analysis = dict(analysis)
        analysis["icp"] = icp_mut

    # Name: explicit user-provided name (no-URL path) wins; else model/domain.
    product_name = raw_name or _extract_product_name(analysis, scrape_result.title, domain)
    summary_text = _build_summary_text(analysis, scrape_result, product_name=product_name)
    favicon_url = _favicon_for(url) if url else ""

    icp = analysis.get("icp") or {}

    return {
        "product_name": product_name,
        "summary_text": summary_text,
        "favicon_url": favicon_url,
        "brand_colors": [],  # TODO: extract from CSS in a future pass
        "raw_analysis": analysis,
        # 2026-06-01 — full scraped site text (homepage + subpages). The wizard
        # forwards this to /analyze/suggest-targeting so the grounded ICP
        # research reasons over the REAL page content (incl. customer/case-study
        # pages + footer/contact details that carry HQ locations), not just the
        # short summary. 2026-06-08 — bound sized to the MODEL'S capacity
        # (gemini.GEMINI_MAX_INPUT_CHARS, ~3M chars) instead of an arbitrary
        # small cap, so no realistic site loses data. The old 16K cap was
        # clipping the tail of multi-page sites → locations/industries came
        # back empty intermittently.
        "scraped_content": (
            content_in if content_mode else (bundle_for_gemini.combined_text or "")
        )[:gemini.GEMINI_MAX_INPUT_CHARS],
        # Top-level mirror of the dual-mode detection so the frontend
        # doesn't have to dig into raw_analysis.icp to render the pill.
        "entity_type": icp.get("entity_type") or "product",
        "entity_confidence": icp.get("confidence") or "medium",
        "entity_rationale": icp.get("rationale") or "",
    }
