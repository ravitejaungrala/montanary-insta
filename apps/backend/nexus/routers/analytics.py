"""Phase 6 analytics router — campaign metrics + funnel + timeseries.

Endpoints:
  GET /nexus/analytics/summary  — full summary + funnel + timeseries
  GET /nexus/analytics/funnel   — funnel only
  GET /nexus/analytics/timeseries — timeseries only
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_common import extract_workspace_id, require_current_workspace
from nexus.schemas_phase6 import AnalyticsResponse, AnalyticsSummary
from nexus.services import analytics_aggregator as agg

logger = logging.getLogger("pipelyt.nexus.analytics")

router = APIRouter(prefix="/nexus/analytics", tags=["nexus-analytics"])


# ── Role-title normalization (for the "Top Targeted Roles" chart) ──────────────
# Apollo returns the same job title in many spellings — "VP of IT",
# "Vice President, Information Technology", "Vice President of Information
# Technology" are all one role. We collapse them to a canonical key so the chart
# shows one bar per real role. Seniority words (senior/executive/etc.) are
# PRESERVED so "Senior Vice President" never merges into "Vice President".
_ROLE_FILLER = {"of", "the", "a", "an", "for", "to", "and"}
# Whole-token abbreviation expansions (per-token map). C-suite titles are
# expanded so "CTO" and "Chief Technology Officer" group together; ambiguous
# ones (e.g. "CPO" = product vs people) are deliberately left as-is.
_ROLE_ABBREV = {
    "svp": ["senior", "vice", "president"],
    "evp": ["executive", "vice", "president"],
    "avp": ["assistant", "vice", "president"],
    "vp": ["vice", "president"],
    "sr": ["senior"],
    "jr": ["junior"],
    "asst": ["assistant"],
    "it": ["information", "technology"],
    "hr": ["human", "resources"],
    "mktg": ["marketing"],
    "ops": ["operations"],
    "eng": ["engineering"],
    "mgr": ["manager"],
    "dir": ["director"],
    "exec": ["executive"],
    "ceo": ["chief", "executive", "officer"],
    "cfo": ["chief", "financial", "officer"],
    "cto": ["chief", "technology", "officer"],
    "coo": ["chief", "operating", "officer"],
    "cmo": ["chief", "marketing", "officer"],
    "cio": ["chief", "information", "officer"],
    "ciso": ["chief", "information", "security", "officer"],
    "cro": ["chief", "revenue", "officer"],
    "chro": ["chief", "human", "resources", "officer"],
}


def _canonical_role_key(raw: str) -> str:
    """Lower-case, strip punctuation, drop filler words, expand common
    abbreviations to whole words → a stable grouping key. Returns '' for
    empty input (caller skips those)."""
    tokens = re.split(r"[^a-z0-9]+", (raw or "").lower())
    out: List[str] = []
    for tok in tokens:
        if not tok or tok in _ROLE_FILLER:
            continue
        out.extend(_ROLE_ABBREV.get(tok, [tok]))
    # Sort tokens so reordered compound titles ("CTO and VP Engineering" vs
    # "VP Engineering and CTO") share one key. Seniority words stay in the
    # multiset, so "Senior VP …" never collapses into "VP …".
    return " ".join(sorted(out))


@router.get("/dashboard")
def analytics_dashboard(
    period: str = Query("30d"),
    entity_type: Optional[str] = Query(None),
    product_id: Optional[int] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Consolidated GTM dashboard payload — ALL KPIs + charts, fully computed
    server-side. The frontend fetches this ONCE and renders it with zero math.

    - `start_date` / `end_date` ('YYYY-MM-DD') — custom FROM/TO window that
      governs EVERY KPI + chart. When both omitted → ALL-TIME (default).
    - `entity_type` ∈ product | service | gcc | all (default: all).
    - `product_id` — narrow to a single product.

    Returns: { kpis, outreach_activity, intent_breakdown, funnel, top_roles,
    top_products } — see nexus/services/dashboard_analytics.py.
    """
    from nexus.services.dashboard_analytics import build_gtm_dashboard
    return build_gtm_dashboard(
        db, workspace, period=period, entity_type=entity_type,
        product_id=product_id, start_date=start_date, end_date=end_date,
    )


@router.get("/summary", response_model=AnalyticsResponse)
def analytics_summary(
    period: str = Query("30d"),
    campaign_id: Optional[int] = Query(None),
    product_id: Optional[int] = Query(None),
    entity_type: Optional[str] = Query(None),
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    workspace_id = extract_workspace_id(workspace) or 0
    days = agg.parse_period(period)

    # Normalise entity_type — accept "all"/"" as "no filter" so the
    # frontend can pass through whatever its filter pill is set to.
    et = (entity_type or "").strip().lower()
    if et not in ("product", "service", "gcc"):
        et = None  # treat anything else as no filter

    summary = agg.compute_summary(
        db, workspace_id=workspace_id, campaign_id=campaign_id,
        product_id=product_id, entity_type=et, period_days=days,
        since=since, until=until,
    )
    funnel = agg.compute_funnel(
        db, workspace_id=workspace_id, campaign_id=campaign_id,
        product_id=product_id, entity_type=et, period_days=days,
        since=since, until=until,
    )
    sent_series = agg.compute_timeseries(
        db, workspace_id=workspace_id, campaign_id=campaign_id,
        product_id=product_id, entity_type=et, period_days=days,
        since=since, until=until,
    )
    # Column names updated to match nexus_touchpoints schema
    # (last_opened_at / last_clicked_at, not opened_at / clicked_at).
    opens_series = agg.compute_timeseries(
        db,
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        product_id=product_id,
        entity_type=et,
        period_days=days,
        column="last_opened_at",
        status_in=("sent", "opened", "clicked", "replied"),
        since=since, until=until,
    )
    clicks_series = agg.compute_timeseries(
        db,
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        product_id=product_id,
        entity_type=et,
        period_days=days,
        column="last_clicked_at",
        status_in=("sent", "opened", "clicked", "replied"),
        since=since, until=until,
    )
    # Replies branch: compute_timeseries detects status_in=("replied",) and
    # switches to counting lead_sequences in 'replied' status bucketed by
    # the touchpoint sent_at. `column` is ignored in that branch.
    replies_series = agg.compute_timeseries(
        db,
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        product_id=product_id,
        entity_type=et,
        period_days=days,
        status_in=("replied",),
        since=since, until=until,
    )

    return AnalyticsResponse(
        summary=AnalyticsSummary(**summary),
        funnel=funnel,
        timeseries=sent_series,
        daily_opens=opens_series,
        daily_clicks=clicks_series,
        daily_replies=replies_series,
    )


@router.get("/funnel")
def analytics_funnel(
    period: str = Query("30d"),
    campaign_id: Optional[int] = Query(None),
    product_id: Optional[int] = Query(None),
    entity_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    workspace_id = extract_workspace_id(workspace) or 0
    days = agg.parse_period(period)
    et = (entity_type or "").strip().lower()
    if et not in ("product", "service", "gcc"):
        et = None
    return {
        "stages": agg.compute_funnel(
            db,
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            product_id=product_id,
            entity_type=et,
            period_days=days,
        )
    }


@router.get("/timeseries")
def analytics_timeseries(
    period: str = Query("30d"),
    campaign_id: Optional[int] = Query(None),
    product_id: Optional[int] = Query(None),
    entity_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    workspace_id = extract_workspace_id(workspace) or 0
    days = agg.parse_period(period)
    et = (entity_type or "").strip().lower()
    if et not in ("product", "service", "gcc"):
        et = None
    return {
        "series": agg.compute_timeseries(
            db,
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            product_id=product_id,
            entity_type=et,
            period_days=days,
        )
    }


@router.get("/top-roles")
def analytics_top_roles(
    limit: int = Query(10, ge=1, le=50),
    product_id: Optional[int] = Query(None),
    entity_type: Optional[str] = Query(None),
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Top-K targeted roles by enrolled-lead count for this workspace.

    Returns roles in descending order with a per-role lead count and
    its share of the total. Powers the "Top Targeted Roles" chart on
    the dashboard so the operator can see at a glance whose desks
    we're landing on.

    Filters out leads with empty/whitespace role strings. Trims
    extremely-long roles (>120 chars) — Apollo sometimes returns the
    person's *bio* in the title field; those would dominate the chart
    visually without contributing useful signal.

    Optional `product_id` narrows the rollup to one product's leads.
    Useful for "show me what roles we hit for Spenzo AI specifically."

    Optional `entity_type` ('product' | 'service') narrows to all
    products of that type. When neither filter is set, the JOIN and
    WHERE fragments below are empty strings → SQL identical to the
    pre-filter version, so existing callers see no change.
    """
    workspace_id = extract_workspace_id(workspace) or 0
    et = (entity_type or "").strip().lower()
    if et not in ("product", "service", "gcc"):
        et = None
    params: Dict[str, Any] = {"w": workspace_id, "n": limit}
    where_product = ""
    if product_id is not None:
        params["pid"] = product_id
        # Expand to the whole same-name+entity product group (mirrors
        # analytics_per_product) so a canonical product_id whose siblings hold
        # the campaigns still matches — keeps roles consistent with the KPIs.
        where_product = "AND " + agg.product_group_in_sql("pid")
    # Entity-type filter joins nexus_products and predicates on
    # icp.entity_type. Same convention as compute_summary: a missing
    # icp.entity_type defaults to 'product' so legacy rows without the
    # JSONB key still match the 'product' filter.
    extra_join = ""
    extra_where = ""
    if et is not None:
        params["et"] = et
        extra_join = " JOIN nexus_products p ON p.id = c.product_id"
        if et == "product":
            extra_where = (
                " AND COALESCE(NULLIF(p.icp->>'entity_type', ''), 'product') = :et"
            )
        else:
            extra_where = " AND p.icp->>'entity_type' = :et"

    # Optional FROM/TO window on when the lead was added to the campaign
    # (nexus_leads.created_at). Empty when no dates → lifetime counts.
    _date_clause = ""
    if since is not None:
        params["since"] = since
        _date_clause += " AND nl.created_at >= :since"
    if until is not None:
        params["until"] = until
        _date_clause += " AND nl.created_at <= :until"

    # Fetch raw per-spelling counts (no LIMIT/share here) — the normalization +
    # merge + top-N + share are done in Python below so similar spellings of one
    # role collapse into a single bar.
    sql = text(
        f"""SELECT TRIM(gl.role) AS role,
                   COUNT(DISTINCT gl.id) AS lead_count
              FROM nexus_global_leads gl
              JOIN nexus_leads nl ON nl.global_lead_id = gl.id
              JOIN nexus_campaigns c ON c.id = nl.campaign_id
              {extra_join}
             WHERE nl.workspace_id = :w
               {where_product}
               {extra_where}
               AND gl.role IS NOT NULL
               AND TRIM(gl.role) <> ''
               AND LENGTH(TRIM(gl.role)) <= 120
               {_date_clause}
             GROUP BY TRIM(gl.role)
        """
    )
    try:
        rows = db.execute(sql, params).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("top-roles failed: %s", exc)
        return {"roles": [], "total_with_role": 0}

    # Merge spelling variants by canonical key. Per group: sum the lead counts
    # and remember the spelling with the highest single count as the display
    # label (the most "natural" form the operator actually sees in their data).
    grouped: Dict[str, Dict[str, Any]] = {}
    for raw, cnt in rows:
        key = _canonical_role_key(raw)
        if not key:
            continue
        cnt = int(cnt or 0)
        g = grouped.get(key)
        if g is None:
            grouped[key] = {"label": raw, "label_count": cnt, "lead_count": cnt}
        else:
            g["lead_count"] += cnt
            if cnt > g["label_count"]:
                g["label"], g["label_count"] = raw, cnt

    total = sum(g["lead_count"] for g in grouped.values())
    merged = sorted(
        grouped.values(),
        key=lambda g: (-g["lead_count"], g["label"].lower()),
    )[:limit]

    return {
        "roles": [
            {
                "role": g["label"],
                "lead_count": g["lead_count"],
                "share_pct": round(g["lead_count"] * 100.0 / total, 1) if total else 0.0,
            }
            for g in merged
        ],
        "total_with_role": total,
    }


@router.get("/per-product")
def analytics_per_product(
    entity_type: str = "all",
    period: str = "30d",
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Roll up sends / replies / demos grouped by product.

    A campaign belongs to at most one product; we left-join leads → sequences
    → touchpoints to count sends and replies, and demo_bookings via the
    campaign id for booked-demo counts. Returns one row per product in the
    workspace (including products with zero activity).

    `period` (7d / 30d / 90d) restricts the touchpoint window so per-product
    numbers reconcile with the time-windowed `/summary` endpoint.

    `entity_type` filter is read from `nexus_products.icp.entity_type`
    (a JSONB key the NewCampaign wizard sets). Accepts:
      - "product"  → only product-type rows
      - "service"  → only service-type rows
      - "all" / "" → both (default; preserves prior behaviour)
    """
    workspace_id = extract_workspace_id(workspace) or 0
    et = (entity_type or "all").strip().lower()
    if et not in ("product", "service", "gcc", "all"):
        et = "all"
    # Empty entity_type in JSONB falls back to 'product' to mirror the
    # frontend wizard default. The filter is applied against the
    # canonical row chosen per source_url group (see canonical CTE).
    entity_clause = ""
    if et == "product":
        entity_clause = (
            "AND COALESCE(NULLIF(cr.icp->>'entity_type', ''), 'product') = 'product'"
        )
    elif et == "service":
        entity_clause = "AND cr.icp->>'entity_type' = 'service'"
    elif et == "gcc":
        entity_clause = "AND cr.icp->>'entity_type' = 'gcc'"

    # Same period semantics as compute_summary: trailing N days of touchpoint
    # send activity. Demo bookings respect the same window so the demos
    # column matches summary.demo_bookings.
    period_days = agg.parse_period(period)
    if since is None:
        since = datetime.utcnow() - timedelta(days=period_days)
    # Optional upper bound (closed FROM/TO range). Per date-column clauses
    # spliced below; empty when no end date is set.
    _until_t = _until_m = _until_db = ""
    if until is not None:
        _until_t = " AND t.sent_at <= :until"
        _until_m = " AND m.received_at <= :until"
        _until_db = " AND db.created_at <= :until"

    # 2026-05-29 — Collapse duplicate products that share the SAME NAME
    # + ENTITY TYPE (case-insensitive, trimmed) per user request.
    # Previously the rollup grouped by source_url only — but two
    # campaigns named "Spenzo AI" with slightly different URLs
    # (e.g. https://spenzo.ai vs https://www.spenzo.ai/) would render
    # as TWO rows. Now they collapse into ONE row: same display name,
    # summed metrics. Different entity types (a "Spenzo AI" product
    # vs a hypothetical "Spenzo AI" service) stay separate — that's
    # intentional, since they're meaningfully different campaigns.
    # Products with empty name fall back to a unique per-id key.
    sql = text(
        f"""
        WITH product_groups AS (
            -- Group key = LOWER(TRIM(name)) || '||' || entity_type.
            -- Archived products are excluded so they never contribute
            -- to per-product analytics rows.
            SELECT
                p.id          AS product_id,
                p.name        AS name,
                p.category    AS category,
                p.icp         AS icp,
                p.source_url  AS source_url,
                p.created_at  AS created_at,
                COALESCE(
                    NULLIF(LOWER(TRIM(p.name)), '')
                      || '||'
                      || COALESCE(NULLIF(p.icp->>'entity_type', ''), 'product'),
                    '__id__::' || p.id::text
                ) AS group_key
              FROM nexus_products p
             WHERE p.workspace_id = :w
               AND COALESCE(p.status, '') <> 'archived'
        ),
        canonical_row AS (
            -- One row per URL group — the row with the highest id
            -- (most recently created) is the canonical name/category/
            -- icp source for that URL. Other siblings still contribute
            -- to summed metrics via product_groups → counts.
            SELECT DISTINCT ON (group_key)
                   group_key, product_id, name, category, icp, source_url
              FROM product_groups
             ORDER BY group_key, product_id DESC
        ),
        active_campaigns AS (
            -- Global zero-lead rule: exclude campaigns that have no
            -- enrolled leads. Mapping is per-product, then joined to
            -- product_groups so we can roll up by group_key.
            -- Archived campaigns (soft-deleted duplicates) are also
            -- excluded.
            SELECT c.id AS campaign_id, c.product_id, pg.group_key
              FROM nexus_campaigns c
              JOIN product_groups pg ON pg.product_id = c.product_id
             WHERE c.workspace_id = :w
               AND COALESCE(c.status, '') <> 'archived'
               AND EXISTS (
                   SELECT 1 FROM nexus_leads nl
                    WHERE nl.campaign_id = c.id
               )
        ),
        group_sequences AS (
            SELECT ac.group_key, ls.id AS lead_seq_id, ls.status
              FROM active_campaigns ac
              LEFT JOIN nexus_lead_sequences ls ON ls.campaign_id = ac.campaign_id
        ),
        counts AS (
            SELECT
                gs.group_key,
                COUNT(DISTINCT gs.lead_seq_id)                                       AS enrollments,
                COUNT(t.id) FILTER (WHERE t.status = 'sent')                         AS sent,
                COUNT(t.id) FILTER (WHERE t.status = 'sent' AND COALESCE(t.opens_count, 0) > 0)  AS opened,
                COUNT(t.id) FILTER (WHERE t.status = 'sent' AND COALESCE(t.clicks_count, 0) > 0) AS clicked
              FROM group_sequences gs
              LEFT JOIN nexus_touchpoints t
                     ON t.lead_sequence_id = gs.lead_seq_id
                    AND t.sent_at >= :since{_until_t}
             GROUP BY gs.group_key
        ),
        -- Replies = inbound messages attributable to a lead enrolled
        -- under this product, in the window. Two-step resolution:
        --   1. nexus_inbound_threads.lead_sequence_id → ls.campaign_id
        --      (preferred — exact thread→sequence match by the inbox
        --      matcher; rare in practice because most inbound flows
        --      come back to the test redirect, not the actual sender)
        --   2. Fallback: nexus_inbound_leads.email →
        --      nexus_global_leads.email → nexus_leads → campaign →
        --      product. Catches the common case where the matcher
        --      didn't write lead_sequence_id but the sender's email
        --      still resolves to a known lead.
        -- The COUNT(DISTINCT m.id) prevents one message resolving via
        -- both paths from being double-counted.
        replies_per_group AS (
            -- Each inbound reply is attributed to EXACTLY ONE product via its
            -- thread's sequence → campaign → product. The previous email-based
            -- fallback (th.lead_id = inbound_lead-by-email) fanned a reply from
            -- a lead worked across MULTIPLE products into every one of them,
            -- double-counting (Top Products replied > the Replies KPI).
            SELECT pg.group_key, COUNT(DISTINCT m.id) AS replied
              FROM nexus_inbound_messages m
              JOIN nexus_inbound_threads th ON th.id = m.thread_id
              JOIN nexus_lead_sequences ls ON ls.id = th.lead_sequence_id
              JOIN nexus_campaigns c ON c.id = ls.campaign_id
              JOIN product_groups pg ON pg.product_id = c.product_id
             WHERE th.workspace_id = :w
               AND m.direction = 'inbound'
               AND m.received_at >= :since{_until_m}
             GROUP BY pg.group_key
        ),
        demos AS (
            -- COUNT DISTINCT booking_id_external dedupes the same MS
            -- appointment if it was ingested into multiple workspaces.
            SELECT ac.group_key,
                   COUNT(DISTINCT COALESCE(db.booking_id_external, db.id::text)) AS demo_count
              FROM nexus_demo_bookings db
              JOIN active_campaigns ac ON ac.campaign_id = db.campaign_id
             WHERE db.created_at >= :since{_until_db}
             GROUP BY ac.group_key
        ),
        group_leads AS (
            -- LEADS column = ALL non-rejected leads (per lead × campaign) in the
            -- group's non-archived campaigns — matches the Total Leads KPI and
            -- the GTM Journey, not just the sequence-enrolled subset.
            SELECT ac.group_key,
                   COUNT(DISTINCT nl.id) AS leads
              FROM active_campaigns ac
              JOIN nexus_leads nl ON nl.campaign_id = ac.campaign_id
             WHERE COALESCE(nl.signals -> 'intent' ->> 'status', '') <> 'rejected'
             GROUP BY ac.group_key
        )
        SELECT cr.product_id,
               cr.name,
               cr.category,
               COALESCE(NULLIF(cr.icp->>'entity_type', ''), 'product') AS entity_type,
               COALESCE(gl.leads, 0)       AS enrollments,
               COALESCE(co.sent, 0)        AS sent,
               COALESCE(co.opened, 0)      AS opened,
               COALESCE(co.clicked, 0)     AS clicked,
               COALESCE(rep.replied, 0)    AS replied,
               COALESCE(d.demo_count, 0)   AS demos
          FROM canonical_row cr
          LEFT JOIN counts co ON co.group_key = cr.group_key
          LEFT JOIN group_leads gl ON gl.group_key = cr.group_key
          LEFT JOIN replies_per_group rep ON rep.group_key = cr.group_key
          LEFT JOIN demos  d  ON d.group_key  = cr.group_key
         WHERE TRUE
           {entity_clause}
           -- Hide URL groups whose every sibling campaign is zero-lead.
           AND EXISTS (
               SELECT 1 FROM active_campaigns ac
                WHERE ac.group_key = cr.group_key
           )
         ORDER BY sent DESC, cr.name ASC
        """
    )
    try:
        _pp = {"w": workspace_id, "since": since}
        if until is not None:
            _pp["until"] = until
        rows = db.execute(sql, _pp).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning("per-product analytics failed: %s", exc)
        return {"products": [], "error": "rollup_unavailable"}

    def _rate(num: int, denom: int) -> float:
        return round((num / denom) * 100.0, 2) if denom else 0.0

    out = []
    for r in rows:
        sent = int(r[5] or 0)
        out.append(
            {
                "product_id": int(r[0]),
                "name": r[1] or "",
                "category": r[2] or "",
                "entity_type": (r[3] or "product").lower(),
                "enrollments": int(r[4] or 0),
                "sent": sent,
                "opened": int(r[6] or 0),
                "clicked": int(r[7] or 0),
                "replied": int(r[8] or 0),
                "demos": int(r[9] or 0),
                "open_rate": _rate(int(r[6] or 0), sent),
                "click_rate": _rate(int(r[7] or 0), sent),
                "reply_rate": _rate(int(r[8] or 0), sent),
            }
        )
    return {"products": out, "entity_type": et}
