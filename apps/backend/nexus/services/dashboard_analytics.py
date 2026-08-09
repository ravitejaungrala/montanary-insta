"""GTM Dashboard analytics — the SINGLE backend source of truth for the
GTM dashboard's KPIs and charts.

Every number shown on the dashboard is computed HERE (server-side). The
`/nexus/analytics/dashboard` endpoint returns a fully-rendered payload so the
frontend does ZERO math — it only displays what this module produces.

Design rules (per product spec):
  • All "· 30D" metrics use a window computed DYNAMICALLY from `utcnow()` on
    every call (no cached/fixed dates) — see `analytics_aggregator.parse_period`
    + the `since` math inside the reused `compute_*` helpers.
  • Filters: `entity_type` ∈ {product, service, gcc, all} and an optional
    `product_id`. Default (no filter) = whole workspace.
  • Each KPI / chart has its own function below; `build_gtm_dashboard` only
    orchestrates them and assembles the response.

This module REUSES `analytics_aggregator` (compute_summary / compute_funnel /
compute_timeseries) and the per-product / top-roles queries so the dashboard,
the consolidated endpoint, and the report export can never disagree.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from nexus.services import analytics_aggregator as agg


# ── helpers ──────────────────────────────────────────────────────────────────
def _rate(num: int, den: int) -> float:
    """Percentage, 1 dp. 0 when the denominator is 0 (never divide-by-zero)."""
    return round((num / den) * 100.0, 1) if den else 0.0


def _norm_entity(entity_type: Optional[str]) -> Optional[str]:
    """Normalise the entity filter to product|service|gcc, else None (= all)."""
    et = (entity_type or "").strip().lower()
    return et if et in ("product", "service", "gcc") else None


def _series_points(points: Any) -> Dict[str, int]:
    """Coerce a compute_timeseries result (list of {date,count} dicts or
    objects) into a {date: count} map."""
    out: Dict[str, int] = {}
    for p in (points or []):
        d = p.get("date") if isinstance(p, dict) else getattr(p, "date", None)
        c = p.get("count") if isinstance(p, dict) else getattr(p, "count", None)
        if d is not None:
            out[str(d)] = int(c or 0)
    return out


# ── KPIs ──────────────────────────────────────────────────────────────────────
def kpi_block(
    summary: Dict[str, Any],
    *,
    total_leads: int,
    active_leads: int,
    archived_leads: int,
) -> Dict[str, Any]:
    """The four KPI tiles. emails/replies/demos are the 30D summary figures;
    total leads is lifetime (active/archived split). Rates are computed here so
    the frontend just prints them."""
    sent = int(summary.get("sent", 0) or 0)
    replies = int(summary.get("replies", 0) or 0)
    demos = int(summary.get("demo_bookings", 0) or 0)
    opens = int(summary.get("opens_unique") or summary.get("opens") or 0)
    return {
        "total_leads": int(total_leads),
        "active_leads": int(active_leads),
        "archived_leads": int(archived_leads),
        "emails_sent": sent,
        "replies": replies,
        "demos_booked": demos,
        "opens": opens,
        "open_rate": _rate(opens, sent),
        "reply_rate": _rate(replies, sent),
        "demo_rate": _rate(demos, sent),
    }


# ── Charts ─────────────────────────────────────────────────────────────────────
def chart_outreach_activity(sent_series: Any, replies_series: Any) -> List[Dict[str, Any]]:
    """Sent vs replies per day, merged into one ordered series (the merge that
    used to happen in the frontend)."""
    sent_map = _series_points(sent_series)
    rep_map = _series_points(replies_series)
    return [
        {"date": d, "sent": sent_map.get(d, 0), "replies": rep_map.get(d, 0)}
        for d in sorted(set(sent_map) | set(rep_map))
    ]


def chart_intent_breakdown(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Reply-intent classification for the donut. Sourced ONLY from the
    backend's `replies_breakdown_by_intent` (no client fallback chain). Demos
    are surfaced as their own slice so the donut isn't empty when replies are
    classified purely as booked demos."""
    breakdown = dict(summary.get("replies_breakdown_by_intent") or {})
    # QUESTION_PRICE was retired — a price question is just a QUESTION now. Fold
    # any historical rows still tagged QUESTION_PRICE into QUESTION.
    qp = breakdown.pop("QUESTION_PRICE", 0)
    if qp:
        breakdown["QUESTION"] = int(breakdown.get("QUESTION", 0) or 0) + int(qp)
    # Demo: show the CONCRETE outcome (actual bookings), not the transient
    # DEMO_SCHEDULED intent — a scheduled demo becomes a booked one, so counting
    # both double-counts the same funnel step.
    breakdown.pop("DEMO_SCHEDULED", None)
    demos = int(summary.get("demo_bookings", 0) or 0)
    if demos:
        breakdown["demo_booked"] = demos
    items = [
        {"label": str(k), "count": int(v)}
        for k, v in breakdown.items()
        if int(v or 0) > 0
    ]
    items.sort(key=lambda x: x["count"], reverse=True)
    return items


def linkedin_block(db: Session, workspace_id: int, *, since: Any, until: Any = None) -> Dict[str, Any]:
    """LinkedIn KPIs + funnel (GTM LinkedIn Agent, Phase 4), shaped exactly like
    the email blocks so the frontend renders it the same way. Sourced from the
    immutable `gtm_linkedin_events` log via `linkedin_funnel`, which returns
    zeros when the feature isn't migrated — so this block is always safe to
    include. `enabled` lets the UI hide the section when there's no activity."""
    from gtm.linkedin.events import linkedin_funnel

    f = linkedin_funnel(db, workspace_id=workspace_id, since=since, until=until)
    sent = f["connection_sent"]
    accepted = f["connection_accepted"]
    messaged = f["messages_sent"]
    replies = f["replies"]
    return {
        "enabled": bool(sent or accepted or messaged or replies),
        "kpis": {
            "connection_requests": sent,
            "accepted": accepted,
            "acceptance_rate": _rate(accepted, sent),
            "messages_sent": messaged,
            "replies": replies,
            "reply_rate": _rate(replies, messaged),
        },
        "funnel": [
            {"label": "Requests Sent", "count": sent, "pct": _rate(sent, sent), "conv_from_prev": None},
            {"label": "Accepted", "count": accepted, "pct": _rate(accepted, sent), "conv_from_prev": _rate(accepted, sent)},
            {"label": "Messaged", "count": messaged, "pct": _rate(messaged, sent), "conv_from_prev": _rate(messaged, accepted)},
            {"label": "Replied", "count": replies, "pct": _rate(replies, sent), "conv_from_prev": _rate(replies, messaged)},
        ],
    }


def chart_funnel(funnel_stages: Any) -> List[Dict[str, Any]]:
    """Conversion funnel with the stage % (relative to the first stage) computed
    here. `funnel_stages` is the list from agg.compute_funnel (already clamped
    monotonic + 30D-windowed)."""
    stages = list(funnel_stages or [])
    base = 0
    if stages:
        first = stages[0]
        base = int(first.get("count", 0) if isinstance(first, dict) else getattr(first, "count", 0) or 0)
    out: List[Dict[str, Any]] = []
    for s in stages:
        label = s.get("label") if isinstance(s, dict) else getattr(s, "label", "")
        count = int(s.get("count", 0) if isinstance(s, dict) else getattr(s, "count", 0) or 0)
        conv = s.get("conv_from_prev") if isinstance(s, dict) else getattr(s, "conv_from_prev", None)
        out.append({
            "label": label,
            "count": count,
            "pct": _rate(count, base),
            "conv_from_prev": conv,
        })
    return out


def chart_top_products(products: List[Dict[str, Any]], *, product_id: Optional[int], limit: int = 50) -> List[Dict[str, Any]]:
    """Top products by 30D send volume. Applies the product_id slice + the
    sort + the top-N cut that used to live in the frontend."""
    rows = list(products or [])
    if product_id is not None:
        rows = [p for p in rows if p.get("product_id") == product_id]
    rows.sort(key=lambda p: int(p.get("sent", 0) or 0), reverse=True)
    return rows[:limit]


def chart_top_roles(roles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Top targeted roles — already computed (role, lead_count, share_pct) by
    the top-roles query; passed through unchanged so the frontend renders bars
    directly off share_pct."""
    return list(roles or [])


# ── Assembler ──────────────────────────────────────────────────────────────────
def _parse_date(s: Optional[str], *, end_of_day: bool = False) -> Optional[datetime]:
    """Parse a 'YYYY-MM-DD' (or ISO) string to a datetime. Returns None when
    empty/unparseable. `end_of_day` pushes a TO date to 23:59:59 so the whole
    day is included in a closed range."""
    if not s:
        return None
    try:
        d = datetime.strptime(str(s)[:10], "%Y-%m-%d")
        return d.replace(hour=23, minute=59, second=59) if end_of_day else d
    except Exception:
        return None


# Far-past lower bound used when no FROM date is picked → "all-time".
_ALL_TIME_SINCE = datetime(1970, 1, 1)


def build_gtm_dashboard(
    db: Session,
    workspace: Any,
    *,
    period: str = "30d",
    entity_type: Optional[str] = None,
    product_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    top_roles_limit: int = 10,
) -> Dict[str, Any]:
    """Assemble the full GTM dashboard payload — every KPI + chart, fully
    computed. The frontend fetches this once and renders it verbatim.

    Date window: an explicit FROM/TO (`start_date`/`end_date`, 'YYYY-MM-DD')
    governs EVERY KPI + chart. When neither is set the window is ALL-TIME
    (since the epoch, no upper bound) — the dashboard's default.

    Lazy imports of the analytics/journey endpoint functions avoid a circular
    import (analytics.py imports this module for the endpoint)."""
    from nexus._phase6_common import extract_workspace_id
    from nexus.routers.analytics import analytics_per_product, analytics_top_roles
    from nexus.routers.journey import journey_summary

    workspace_id = extract_workspace_id(workspace) or 0
    days = agg.parse_period(period)
    et = _norm_entity(entity_type)
    et_pp = et or "all"

    # FROM/TO → since/until. No FROM ⇒ all-time lower bound; no TO ⇒ up to now.
    since = _parse_date(start_date) or _ALL_TIME_SINCE
    until = _parse_date(end_date, end_of_day=True)

    # ── Core summary (sent / replies / demos / opens / intent) over the window ──
    summary = agg.compute_summary(
        db, workspace_id=workspace_id, campaign_id=None,
        product_id=product_id, entity_type=et, since=since, until=until,
    )

    # ── Funnel (monotonic-clamped) ──
    funnel_stages = agg.compute_funnel(
        db, workspace_id=workspace_id, campaign_id=None,
        product_id=product_id, entity_type=et, since=since, until=until,
    )

    # ── Outreach activity series (sent + replies per day) ──
    sent_series = agg.compute_timeseries(
        db, workspace_id=workspace_id, campaign_id=None,
        product_id=product_id, entity_type=et, since=since, until=until,
    )
    replies_series = agg.compute_timeseries(
        db, workspace_id=workspace_id, campaign_id=None,
        product_id=product_id, entity_type=et, since=since, until=until,
        status_in=("replied",),
    )

    # ── Per-product → drives Top Products + the filtered lead count ──
    per_product = (analytics_per_product(
        entity_type=et_pp, period=period, since=since, until=until,
        db=db, workspace=workspace,
    ) or {}).get("products", [])

    # ── Top roles (filter + window aware) ──
    roles = (analytics_top_roles(
        limit=top_roles_limit, product_id=product_id, entity_type=et,
        since=since, until=until, db=db, workspace=workspace,
    ) or {}).get("roles", [])

    # ── Lead buckets: workspace-wide → journey summary (windowed); a slice →
    # distinct PEOPLE in the slice (same set the leads table lists). ──
    is_filtered = product_id is not None or et is not None
    if is_filtered:
        # Count distinct leads, NOT the sum of per-product enrollments — a
        # person enrolled in two products would be double-counted otherwise,
        # making the tile disagree with the leads table / GTM Journey.
        from nexus.routers.journey import count_slice_distinct_leads
        slice_leads = count_slice_distinct_leads(
            db, workspace_id, product_id=product_id, entity_type=et,
            since=since, until=until,
        )
        total_leads = active_leads = slice_leads
        archived_leads = 0
    else:
        js = (journey_summary(
            since=since, until=until, db=db, workspace=workspace,
        ) or {}).get("summary", {})
        total_leads = int(js.get("total", 0) or 0)
        active_leads = int(js.get("active", 0) or 0)
        archived_leads = int(js.get("hidden", 0) or 0)

    return {
        "period": period,
        "period_days": days,
        "start_date": start_date or None,
        "end_date": end_date or None,
        "entity_type": et or "all",
        "product_id": product_id,
        "kpis": kpi_block(
            summary,
            total_leads=total_leads,
            active_leads=active_leads,
            archived_leads=archived_leads,
        ),
        "outreach_activity": chart_outreach_activity(sent_series, replies_series),
        "intent_breakdown": chart_intent_breakdown(summary),
        "funnel": chart_funnel(funnel_stages),
        "top_roles": chart_top_roles(roles),
        "top_products": chart_top_products(per_product, product_id=product_id),
        # LinkedIn (GTM LinkedIn Agent) — workspace-level funnel/KPIs; safe zeros
        # until the feature is migrated/enabled. Per-product LinkedIn columns are
        # a follow-up (would join events → campaign → product).
        "linkedin": linkedin_block(db, workspace_id, since=since, until=until),
    }
