"""The lead's OWN company — one loader, one prompt format, used everywhere.

Downstream agents used to know a prospect's company as two strings
(`company_name` + `organization_industry`), so anything they said about the
account was either vague or invented. The facts were mostly already collected —
`nexus_lead_enrichment` holds the company's site description, recent news and
tech signals, and Apollo's organization-enrich response (a call we already make)
carries headcount, revenue, HQ, what they do and their stack — they just were
never assembled or handed to a model.

This module is that assembly point. It reads the lead row + the per-domain
enrichment cache and returns ONE normalised company dict, plus a renderer that
formats it identically for every prompt. Agenda, briefing and outreach all call
this, so they can never disagree about the account.

Everything degrades: a missing field is absent, never guessed, and
`format_company_block` marks what it does not know so a model is told the gap
rather than left to fill it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("nexus.services.company_context")

# Refresh a company profile older than this when a caller asks for fresh data.
# Matches lead_enricher.CACHE_TTL — one Apollo credit per company per month.
_STALE_AFTER = timedelta(days=30)

_EMPTY: Dict[str, Any] = {
    "name": None, "domain": None, "industry": None, "description": None,
    "headcount": None, "revenue": None, "founded_year": None, "hq": None,
    "keywords": [], "technologies": [], "news": [], "linkedin_url": None,
    "has_profile": False,
}


def _table_exists(db: Session, table: str) -> bool:
    try:
        return bool(
            db.execute(
                text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}
            ).scalar()
        )
    except Exception:  # noqa: BLE001
        return False


def load_company_context(
    db: Session,
    *,
    lead_id: Optional[int] = None,
    company_domain: Optional[str] = None,
    company_name: Optional[str] = None,
    ensure_fresh: bool = False,
) -> Dict[str, Any]:
    """Assemble everything known about the lead's own company.

    `lead_id` is the normal entry point (it resolves the domain); pass
    `company_domain` directly when there is no lead row. With `ensure_fresh`,
    a missing or >30-day-old profile triggers a best-effort re-enrichment first
    — safe to use from background paths (booking, poller), which is where the
    freshness actually matters. Never raises.
    """
    ctx = dict(_EMPTY)
    ctx["keywords"], ctx["technologies"], ctx["news"] = [], [], []
    ctx["name"] = (company_name or "").strip() or None
    domain = (company_domain or "").strip().lower() or None

    # 1. The lead row — company name / domain / industry.
    if lead_id:
        try:
            r = db.execute(
                text(
                    "SELECT company_name, company_domain, organization_industry "
                    "FROM nexus_global_leads WHERE id = :l"
                ),
                {"l": int(lead_id)},
            ).mappings().first()
            if r:
                ctx["name"] = ctx["name"] or (r.get("company_name") or "").strip() or None
                domain = domain or (r.get("company_domain") or "").strip().lower() or None
                ctx["industry"] = (r.get("organization_industry") or "").strip() or None
        except Exception as exc:  # noqa: BLE001
            log.debug("company_context: lead lookup failed: %s", exc)
            _rollback(db)
    ctx["domain"] = domain
    if not domain or not _table_exists(db, "nexus_lead_enrichment"):
        return ctx

    # 2. Freshen the per-domain cache when asked (best-effort, never blocks).
    if ensure_fresh:
        _ensure_enriched(db, domain)

    # 3. The enrichment cache — structured profile + site summary + news.
    #
    # Read in two tiers. `company_profile` is newer than the rest of this table,
    # so on a database where its migration hasn't run yet a single SELECT naming
    # it fails outright — and would take the site description and news down with
    # it, blanking a company we actually know things about. The legacy columns
    # are therefore fetched separately, and the profile is best-effort on top.
    row = None
    try:
        row = db.execute(
            text(
                "SELECT meta_description, body_snippet, news_headlines, tech_stack "
                "FROM nexus_lead_enrichment WHERE company_domain = :d LIMIT 1"
            ),
            {"d": domain},
        ).mappings().first()
    except Exception as exc:  # noqa: BLE001
        log.debug("company_context: enrichment lookup failed: %s", exc)
        _rollback(db)
        return ctx
    if not row:
        return ctx

    profile = None
    try:
        profile = db.execute(
            text(
                "SELECT company_profile FROM nexus_lead_enrichment "
                "WHERE company_domain = :d LIMIT 1"
            ),
            {"d": domain},
        ).scalar()
    except Exception as exc:  # noqa: BLE001
        # Column not migrated yet — degrade to the site-derived fields below.
        log.debug("company_context: company_profile unavailable (%s)", exc)
        _rollback(db)

    if isinstance(profile, dict) and profile:
        ctx["has_profile"] = True
        ctx["name"] = ctx["name"] or profile.get("name")
        ctx["industry"] = ctx["industry"] or profile.get("industry")
        for key in ("description", "headcount", "revenue", "founded_year", "hq", "linkedin_url"):
            if profile.get(key):
                ctx[key] = profile[key]
        ctx["keywords"] = _strlist(profile.get("keywords"), 12)
        ctx["technologies"] = _strlist(profile.get("technologies"), 15)

    # Site description is the fallback when Apollo had no 'About' text.
    if not ctx["description"]:
        summary = row.get("meta_description") or row.get("body_snippet")
        if summary:
            ctx["description"] = str(summary)[:600]

    # Site-inferred stack only supplements Apollo's (which is far richer).
    if not ctx["technologies"]:
        ctx["technologies"] = _strlist(row.get("tech_stack"), 8)

    news = row.get("news_headlines")
    if isinstance(news, list):
        for n in news[:3]:
            if isinstance(n, dict) and n.get("title"):
                ctx["news"].append(str(n["title"]).strip())
            elif isinstance(n, str) and n.strip():
                ctx["news"].append(n.strip())
    return ctx


def format_company_block(ctx: Dict[str, Any], *, header: str = "THE PROSPECT'S COMPANY") -> str:
    """Render a company context for a prompt.

    Unknown fields render as '(nothing on file)' rather than being omitted —
    an absent line reads to a model as an invitation to fill the gap, an
    explicit unknown does not.
    """
    c = ctx or {}
    lines = [f"{header}:"]
    lines.append(f"  name: {c.get('name') or '(nothing on file)'}")
    lines.append(f"  industry: {c.get('industry') or '(nothing on file)'}")
    lines.append(f"  what they do: {c.get('description') or '(nothing on file)'}")
    size = c.get("headcount")
    lines.append(f"  headcount: {size if size else '(nothing on file)'}")
    lines.append(f"  revenue: {c.get('revenue') or '(nothing on file)'}")
    lines.append(f"  headquarters: {c.get('hq') or '(nothing on file)'}")
    founded = c.get("founded_year")
    lines.append(f"  founded: {founded if founded else '(nothing on file)'}")
    kw = c.get("keywords") or []
    lines.append("  focus areas: " + (", ".join(kw) if kw else "(nothing on file)"))
    tech = c.get("technologies") or []
    lines.append("  tech they use: " + (", ".join(tech) if tech else "(nothing on file)"))
    news = c.get("news") or []
    lines.append("  recent news: " + ("; ".join(news) if news else "(nothing on file)"))
    return "\n".join(lines)


def has_signal(ctx: Dict[str, Any]) -> bool:
    """True when we know something real about the company beyond its name —
    i.e. there is something worth grounding an account-specific claim on."""
    c = ctx or {}
    return bool(
        c.get("description") or c.get("headcount") or c.get("keywords")
        or c.get("technologies") or c.get("news") or c.get("revenue")
    )


# ─── internals ────────────────────────────────────────────────────────────────

def _strlist(raw: Any, cap: int) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for v in raw:
        s = str(v).strip()
        if s and s.lower() not in {x.lower() for x in out}:
            out.append(s)
        if len(out) >= cap:
            break
    return out


def _rollback(db: Session) -> None:
    try:
        db.rollback()
    except Exception:  # noqa: BLE001
        pass


def _ensure_enriched(db: Session, domain: str) -> None:
    """Re-run enrichment when this domain has no profile or a stale one.

    `enrich_domain` is async while every caller here is sync, so it runs via
    asyncio.run — only when no loop is already running (the same guard the
    sequencer uses). Inside a live loop we skip rather than risk blocking; the
    cached row, however old, is still returned. Never raises.
    """
    # Same two-tier read as above: age is on the original table, the profile may
    # not be migrated yet. A missing profile column must not disable refresh.
    try:
        fetched = db.execute(
            text("SELECT fetched_at FROM nexus_lead_enrichment "
                 "WHERE company_domain = :d LIMIT 1"),
            {"d": domain},
        ).scalar()
    except Exception:  # noqa: BLE001
        _rollback(db)
        return
    try:
        profile = db.execute(
            text("SELECT company_profile FROM nexus_lead_enrichment "
                 "WHERE company_domain = :d LIMIT 1"),
            {"d": domain},
        ).scalar()
    except Exception:  # noqa: BLE001
        _rollback(db)
        profile = None

    if fetched is not None:
        fresh = isinstance(fetched, datetime) and (datetime.utcnow() - fetched) < _STALE_AFTER
        if fresh and isinstance(profile, dict) and profile:
            return  # already have a current profile

    try:
        import asyncio

        try:
            asyncio.get_running_loop()
            return  # inside a live loop — don't block it
        except RuntimeError:
            pass
        from nexus.services.lead_enricher import enrich_domain

        asyncio.run(enrich_domain(db, domain, force=True))
    except Exception as exc:  # noqa: BLE001
        log.info("company_context: enrichment skipped for %s: %s", domain, exc)
        _rollback(db)


__all__ = ["load_company_context", "format_company_block", "has_signal"]
