"""Detailed workspace report — CSV + PDF.

GET /nexus/reports/export?format=csv|pdf

Returns a per-lead detailed dump joined across products, campaigns,
touchpoints, demo bookings. ONE row per lead. Operator gets:
  - workspace totals summary line at the top (PDF only)
  - per-product roll-ups (PDF only)
  - per-lead rows: email, name, company, role, product, campaign, status,
    last_touch_at, channel, current_step, demos_booked, briefing flag

Read-only — does NOT mutate any data structure or write to the DB.
Uses pure-Python `reportlab` for PDF (lazy import — module load is cheap
even when reportlab isn't installed).
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from models import User
from nexus.deps import get_nexus_user, require_current_workspace

logger = logging.getLogger("pipelyt.nexus.reports")

router = APIRouter(prefix="/nexus/reports", tags=["Nexus — Reports"])


# ─── Data builder ────────────────────────────────────────────────────────────


def _slice_campaign_ids_sql(
    params: Dict[str, Any],
    *,
    product_id: Optional[int],
    entity_type: Optional[str],
) -> Optional[str]:
    """Mirror of analytics_aggregator._slice_campaign_ids_sql — kept local
    so the report endpoint doesn't reach across into the services layer.

    Returns a SELECT subquery yielding the campaign IDs that belong to the
    requested slice. Returns None when no filter is active — every caller
    then splices in an empty string and the SQL is byte-for-byte identical
    to the pre-filter version, so existing reports keep behaving exactly
    as before for the workspace-wide export.

    Uses `_slice_pid` / `_slice_et` parameter names so it does not collide
    with the existing `:w` key used everywhere in this module.
    """
    from nexus.services import analytics_aggregator as agg

    conds: List[str] = []
    needs_products = entity_type in ("product", "service", "gcc")
    if product_id is not None:
        params["_slice_pid"] = int(product_id)
        # Expand to the whole same-name+entity product group (mirrors the
        # dashboard) so a canonical product_id whose siblings hold the
        # campaigns still matches — keeps the export's leads + numbers in
        # lockstep with what's on screen.
        conds.append(agg.product_group_in_sql("_slice_pid"))
    if needs_products:
        params["_slice_et"] = entity_type
        if entity_type == "product":
            conds.append(
                "COALESCE(NULLIF(p.icp->>'entity_type', ''), 'product') = :_slice_et"
            )
        else:
            conds.append("p.icp->>'entity_type' = :_slice_et")
    if not conds:
        return None
    join_p = " JOIN nexus_products p ON p.id = c.product_id" if needs_products else ""
    return (
        f"SELECT c.id FROM nexus_campaigns c{join_p} "
        f"WHERE " + " AND ".join(conds)
    )


def _build_workspace_report(
    db: Session,
    workspace: Any,
    workspace_id: int,
    *,
    product_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the join graph and return a structured dict ready for either
    csv-writer or pdf-renderer to format.

    Shape:
        {
          "generated_at": iso,
          "workspace_id": int,
          "totals": {
              "products": int, "campaigns": int, "leads": int,
              "emails_sent": int, "demos_booked": int, "replied": int,
          },
          "per_product": [ {name, leads, sent, replied, demos}, ... ],
          "leads": [ {row dict per the SELECT below}, ... ]
        }
    """
    # Per-lead detail row. Joins are LEFT so a lead with no touchpoint /
    # no booking still appears.
    #
    # Slice filter (optional product_id / entity_type) restricts the
    # leads to those attached to campaigns in the slice. When no filter
    # is set, slice_clause is "" → SQL identical to pre-filter version.
    # The leads table must list the SAME distinct leads the dashboard's
    # "Total Leads" KPI counts — i.e. exactly the GTM Journey lead set:
    #   • only the workspace's OWNED campaigns (non-archived, has-leads) —
    #     see journey._owned_campaign_ids,
    #   • one row PER DISTINCT lead (not per campaign-enrollment),
    #   • 'rejected' (not-picked) leads excluded,
    #   • the same FROM/TO window the dashboard applies.
    # The previous query joined raw nexus_leads (one row per enrollment,
    # archived campaigns included, rejected included, no dedupe) which is
    # why the table showed 145 while the KPI showed 100.
    from nexus.routers.journey import _touch_counts_per_campaign, _owned_campaign_ids
    from nexus.services.dashboard_analytics import _parse_date as _pd, _ALL_TIME_SINCE as _ats

    owned_cids = _owned_campaign_ids(db, workspace_id)
    # Intersect with the product/entity slice when one is active.
    if owned_cids and (product_id is not None or entity_type in ("product", "service", "gcc")):
        _slice_params: Dict[str, Any] = {}
        _slice_sel = _slice_campaign_ids_sql(
            _slice_params, product_id=product_id, entity_type=entity_type
        )
        if _slice_sel:
            slice_ids = {
                int(r[0])
                for r in db.execute(text(_slice_sel), _slice_params).fetchall()
            }
            owned_cids = [c for c in owned_cids if c in slice_ids]

    # Same date window the dashboard/journey use (default = all-time).
    _since = _pd(start_date) or _ats
    _until = _pd(end_date, end_of_day=True)

    lead_rows: List[Dict[str, Any]] = []
    if owned_cids:
        lp: Dict[str, Any] = {"cids": owned_cids}
        date_clause = ""
        if _since is not None:
            lp["since"] = _since
            date_clause += " AND nl.created_at >= :since"
        if _until is not None:
            lp["until"] = _until
            date_clause += " AND nl.created_at <= :until"
        # ONE row per (lead × campaign) so a person worked across multiple
        # PRODUCTS appears once per product (matches the per-product GTM Journey).
        raw = db.execute(
            text(
                f"""SELECT gl.id AS global_lead_id,
                      nl.campaign_id AS campaign_id,
                      gl.email,
                      COALESCE(NULLIF(gl.first_name, ''), '') AS first_name,
                      COALESCE(NULLIF(gl.last_name, ''), '') AS last_name,
                      COALESCE(NULLIF(gl.company_name, ''), '') AS company_name,
                      COALESCE(NULLIF(gl.role, ''), NULLIF(gl.job_title, ''), '') AS role,
                      p.name AS product_name,
                      COALESCE(NULLIF(p.icp->>'entity_type', ''), 'product') AS entity_type,
                      CASE WHEN c.product_campaign_number IS NOT NULL
                           THEN p.name || ' #' || c.product_campaign_number
                           ELSE ''
                      END AS campaign_name
                    FROM nexus_leads nl
                    JOIN nexus_global_leads gl ON gl.id = nl.global_lead_id
                    LEFT JOIN nexus_campaigns c ON c.id = nl.campaign_id
                    LEFT JOIN nexus_products p ON p.id = c.product_id
                   WHERE nl.campaign_id = ANY(:cids)
                     AND COALESCE(nl.signals -> 'intent' ->> 'status', '') <> 'rejected'
                     {date_clause}
                   ORDER BY gl.id, nl.created_at DESC
                """
            ),
            lp,
        ).mappings().fetchall()
        lead_rows = [dict(r) for r in raw]

    # Per-(lead × campaign) Sent Emails + Last Contacted + demos so each
    # product row shows ITS OWN counts (matches the per-product GTM Journey).
    lead_id_list = list({int(r["global_lead_id"]) for r in lead_rows})
    attempt_stats: Dict[tuple, Dict[str, Any]] = (
        _touch_counts_per_campaign(db, owned_cids, lead_id_list, workspace_id)
        if owned_cids and lead_id_list else {}
    )
    demos_by_pair: Dict[tuple, int] = {}
    if lead_id_list:
        for r in db.execute(
            text(
                """SELECT lead_id, campaign_id,
                          COUNT(DISTINCT COALESCE(booking_id_external, id::text)) AS n
                     FROM nexus_demo_bookings
                    WHERE workspace_id = :w AND lead_id = ANY(:lids)
                      AND campaign_id = ANY(:cids)
                    GROUP BY lead_id, campaign_id"""
            ),
            {"w": workspace_id, "lids": lead_id_list, "cids": owned_cids},
        ).fetchall():
            demos_by_pair[(int(r[0]), int(r[1]))] = int(r[2] or 0)

    for r in lead_rows:
        key = (int(r.get("global_lead_id") or 0), int(r.get("campaign_id") or 0))
        st = attempt_stats.get(key) or {}
        r["email_n"] = int(st.get("email", 0) or 0)
        r["last_touch_at"] = st.get("last_at")
        r["demos_booked"] = demos_by_pair.get(key, 0)

    # Stable display order: product, then name.
    lead_rows.sort(key=lambda r: (
        (r.get("product_name") or "").lower(),
        (r.get("first_name") or "").lower(),
        (r.get("last_name") or "").lower(),
    ))

    # ── KPIs + per-product + lead buckets — computed from the SAME functions
    # the DASHBOARD calls (analytics_summary / analytics_per_product /
    # journey_summary), so the report figures ALWAYS reconcile with the UI.
    # Period is fixed at 30d to mirror the dashboard's "· 30D" tiles.
    # (The previous all-time SQL here diverged from the 30d dashboard, which
    # is exactly the mismatch this replaces.)
    from nexus.routers.analytics import (
        analytics_summary, analytics_per_product, analytics_top_roles,
    )
    from nexus.routers.journey import journey_summary
    from nexus.services import analytics_aggregator as agg
    from nexus.services.dashboard_analytics import _parse_date, _ALL_TIME_SINCE

    et_summary = entity_type if entity_type in ("product", "service", "gcc") else None
    et_pp = entity_type if entity_type in ("product", "service", "gcc") else "all"

    # Same FROM/TO → since/until window the dashboard uses, so a filtered
    # export reconciles with what's on screen (default = all-time).
    since = _parse_date(start_date) or _ALL_TIME_SINCE
    until = _parse_date(end_date, end_of_day=True)

    resp = analytics_summary(
        period="30d", campaign_id=None, product_id=product_id,
        entity_type=et_summary, since=since, until=until, db=db, workspace=workspace,
    )
    s = resp.summary
    pp = (analytics_per_product(
        entity_type=et_pp, period="30d", since=since, until=until,
        db=db, workspace=workspace,
    ) or {}).get("products", [])
    # Mirror the dashboard's client-side slice: analytics_per_product already
    # applied the entity_type filter; narrow to a specific product when one is
    # selected so the per-product table + slice lead counts in the export match
    # the UI's `filteredProducts` exactly.
    if product_id is not None:
        pp = [p for p in pp if p.get("product_id") == product_id]
    js = (journey_summary(
        since=since, until=until, db=db, workspace=workspace,
    ) or {}).get("summary", {})

    # Conversion funnel + Top roles — same functions/filters the dashboard
    # uses, so the CSV's chart sections reconcile with the on-screen charts.
    funnel_stages = agg.compute_funnel(
        db, workspace_id=workspace_id, campaign_id=None,
        product_id=product_id, entity_type=et_summary, since=since, until=until,
    ) or []
    funnel = [
        {
            "label": (st.get("label") if isinstance(st, dict) else getattr(st, "label", "")),
            "count": int(st.get("count", 0) if isinstance(st, dict) else getattr(st, "count", 0) or 0),
        }
        for st in funnel_stages
    ]
    top_roles = (analytics_top_roles(
        limit=10, product_id=product_id, entity_type=et_summary,
        since=since, until=until, db=db, workspace=workspace,
    ) or {}).get("roles", [])

    def _rate(num: int, den: int) -> float:
        return round((num / den) * 100.0, 1) if den else 0.0

    sent = int(s.sent or 0)
    replies = int(s.replies or 0)
    demos = int(s.demo_bookings or 0)
    opens = int(getattr(s, "opens_unique", 0) or s.opens or 0)

    # Lead buckets mirror the dashboard EXACTLY: workspace-wide → journey
    # summary; a product/entity slice → distinct PEOPLE in the slice (same
    # count_slice_distinct_leads helper the dashboard tile uses), so the
    # report KPI == dashboard tile == leads-table row count everywhere.
    is_filtered = product_id is not None or et_summary is not None
    if is_filtered:
        total_leads = active_leads = len(lead_rows)
        archived_leads = 0
    else:
        total_leads = int(js.get("total", 0))
        active_leads = int(js.get("active", 0))
        archived_leads = int(js.get("hidden", 0))

    kpis = {
        "period": "30d",
        "total_leads": total_leads,
        "active_leads": active_leads,
        "archived_leads": archived_leads,
        "emails_sent": sent,
        "replies": replies,
        "demos_booked": demos,
        "opens": opens,
        "open_rate": _rate(opens, sent),
        "reply_rate": _rate(replies, sent),
        "demo_rate": _rate(demos, sent),
    }

    # Intent breakdown (the dashboard's "Intent Breakdown" donut). Mirrors
    # chart_intent_breakdown: fold retired QUESTION_PRICE into QUESTION, and show
    # the concrete demo_booked outcome instead of the transient DEMO_SCHEDULED.
    intent = {
        str(k): int(v)
        for k, v in (s.replies_breakdown_by_intent or {}).items() if v
    }
    qp = intent.pop("QUESTION_PRICE", 0)
    if qp:
        intent["QUESTION"] = intent.get("QUESTION", 0) + qp
    intent.pop("DEMO_SCHEDULED", None)
    if demos:
        intent["demo_booked"] = demos

    # Outreach activity series (sent vs replies per day) — same series the
    # dashboard chart plots.
    def _series_map(points):
        out: Dict[str, int] = {}
        for p in (points or []):
            d = getattr(p, "date", None)
            c = getattr(p, "count", None)
            if d is None and isinstance(p, dict):
                d, c = p.get("date"), p.get("count")
            if d is not None:
                out[str(d)] = int(c or 0)
        return out

    sent_map = _series_map(resp.timeseries)
    rep_map = _series_map(resp.daily_replies)
    timeseries = [
        {"date": d, "sent": sent_map.get(d, 0), "replies": rep_map.get(d, 0)}
        for d in sorted(set(sent_map) | set(rep_map))
    ]

    # Per-product rows in the dashboard's shape (same source → same numbers).
    per_product = [
        {
            "name": p.get("name") or "—",
            "entity_type": p.get("entity_type") or "product",
            "leads": int(p.get("enrollments", 0)),
            "sent": int(p.get("sent", 0)),
            "opened": int(p.get("opened", 0)),
            "clicked": int(p.get("clicked", 0)),
            "replied": int(p.get("replied", 0)),
            "demos": int(p.get("demos", 0)),
            "open_rate": float(p.get("open_rate", 0.0)),
            "reply_rate": float(p.get("reply_rate", 0.0)),
        }
        for p in pp
    ]

    # Window label for the report header (replaces the old fixed "30d").
    if start_date or end_date:
        window_label = f"{start_date or 'beginning'} to {end_date or 'now'}"
    else:
        window_label = "All time"

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "workspace_id": workspace_id,
        "window_label": window_label,
        "kpis": kpis,
        "intent": intent,
        "timeseries": timeseries,
        "funnel": funnel,
        "top_roles": [
            {"role": r.get("role", ""), "lead_count": int(r.get("lead_count", 0) or 0)}
            for r in top_roles
        ],
        "per_product": per_product,
        "leads": lead_rows,
    }


# ─── Leads table (the 9 columns the dashboard export uses) ───────────────────


_LEADS_HEADER = [
    "S.No", "Name", "Role", "Email", "Company", "Product / Entity", "Campaign",
    "Sent Emails", "Last Contacted", "Demos Booked",
]


def _lead_table_row(r: Dict[str, Any], idx: int) -> List[Any]:
    """Map one raw lead row → the dashboard leads-table column order.

    `idx` is the 1-based serial number (S.No). Sent Emails + Last Contacted
    come from the GTM-Journey-scoped stats patched onto the row upstream."""
    name = " ".join(
        x for x in (r.get("first_name") or "", r.get("last_name") or "") if x
    ).strip() or "—"
    last_touch = r.get("last_touch_at")
    last_contacted = (
        last_touch.strftime("%Y-%m-%d") if hasattr(last_touch, "strftime")
        else (last_touch or "")
    )
    return [
        idx,
        name,
        r.get("role") or "",
        r.get("email") or "",
        r.get("company_name") or "",
        r.get("product_name") or "",
        r.get("campaign_name") or "",
        int(r.get("email_n") or 0),
        last_contacted,
        int(r.get("demos_booked") or 0),
    ]


# ─── CSV renderer ────────────────────────────────────────────────────────────


def _render_csv(data: Dict[str, Any]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)

    kpis = data.get("kpis") or {}
    # ── Section 1: Dashboard KPIs (same figures the UI shows) ────────────────
    w.writerow(["NEXUS Workspace Report"])
    w.writerow(["Generated", data.get("generated_at", "")])
    w.writerow(["Date range", data.get("window_label", "All time")])
    w.writerow([])
    w.writerow(["KPI", "Value"])
    w.writerow(["Total leads", kpis.get("total_leads", 0)])
    w.writerow(["Active leads", kpis.get("active_leads", 0)])
    w.writerow(["Archived leads", kpis.get("archived_leads", 0)])
    w.writerow(["Emails sent", kpis.get("emails_sent", 0)])
    w.writerow(["Replies", kpis.get("replies", 0)])
    w.writerow(["Demos booked", kpis.get("demos_booked", 0)])
    w.writerow([])

    # ── Section 2: Intent breakdown ──────────────────────────────────────────
    intent = data.get("intent") or {}
    w.writerow(["Intent Breakdown", "Count"])
    if intent:
        for k, v in intent.items():
            w.writerow([str(k).replace("_", " ").title(), v])
    else:
        w.writerow(["(no replies classified)", 0])
    w.writerow([])

    # ── Section 3: Per-product roll-up ───────────────────────────────────────
    w.writerow([
        "Per-Product", "Type", "Leads", "Sent", "Opened", "Clicked",
        "Replied", "Demos", "Open rate (%)", "Reply rate (%)",
    ])
    for p in (data.get("per_product") or []):
        w.writerow([
            p.get("name", ""), p.get("entity_type", ""),
            p.get("leads", 0), p.get("sent", 0), p.get("opened", 0),
            p.get("clicked", 0), p.get("replied", 0), p.get("demos", 0),
            p.get("open_rate", 0), p.get("reply_rate", 0),
        ])
    w.writerow([])

    # ── Section 4: Outreach activity (sent vs replies per day) ───────────────
    w.writerow(["Outreach Activity"])
    w.writerow(["Date", "Emails sent", "Replies"])
    for pt in (data.get("timeseries") or []):
        w.writerow([pt.get("date", ""), pt.get("sent", 0), pt.get("replies", 0)])
    w.writerow([])

    # ── Section 5: Conversion funnel ─────────────────────────────────────────
    w.writerow(["Conversion Funnel"])
    w.writerow(["Stage", "People"])
    for st in (data.get("funnel") or []):
        w.writerow([st.get("label", ""), st.get("count", 0)])
    w.writerow([])

    # ── Section 6: Top targeted roles ────────────────────────────────────────
    w.writerow(["Top Targeted Roles"])
    w.writerow(["Role", "Leads"])
    for role in (data.get("top_roles") or []):
        w.writerow([role.get("role", ""), role.get("lead_count", 0)])
    w.writerow([])

    # ── Section 7: Leads detail ──────────────────────────────────────────────
    w.writerow([f"Leads ({len(data.get('leads', []))})"])
    w.writerow(_LEADS_HEADER)
    for i, r in enumerate(data.get("leads", []), start=1):
        w.writerow(_lead_table_row(r, i))
    return buf.getvalue().encode("utf-8")


# ─── PDF renderer (lazy reportlab import) ────────────────────────────────────


def _render_pdf(data: Dict[str, Any]) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import LETTER, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
        )
    except ImportError as e:
        raise HTTPException(
            status_code=500,
            detail=(
                "reportlab not installed in this environment. "
                "Add to apps/backend/requirements.txt and rebuild."
            ),
        ) from e

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(LETTER),
        title="NEXUS workspace report",
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )
    styles = getSampleStyleSheet()
    style_h1 = ParagraphStyle(
        "h1", parent=styles["Heading1"], textColor=colors.black, spaceAfter=12,
    )
    style_h2 = ParagraphStyle(
        "h2", parent=styles["Heading2"], textColor=colors.HexColor("#ff4500"),
        spaceBefore=14, spaceAfter=6,
    )
    style_normal = styles["Normal"]
    elements = []

    elements.append(Paragraph("NEXUS workspace report", style_h1))
    # Palette-safe muted: ~50% black on white ≈ #808080. Reportlab solid
    # color (no PDF alpha support across all viewers), but matches the
    # text-black/50 utility the UI uses elsewhere.
    elements.append(
        Paragraph(
            f"Generated {data.get('generated_at','')}",
            ParagraphStyle("muted", parent=style_normal, textColor=colors.HexColor("#808080")),
        )
    )
    elements.append(Spacer(1, 12))

    # ── Dashboard KPIs — identical figures + 30d window as the UI tiles ──────
    kpis = data.get("kpis") or {}
    per = kpis.get("period", "30d")
    elements.append(Paragraph("Dashboard overview", style_h2))
    kpi_data = [
        ["Total leads", f"Emails sent · {per}", f"Replies · {per}", f"Demos booked · {per}"],
        [
            str(kpis.get("total_leads", 0)),
            str(kpis.get("emails_sent", 0)),
            str(kpis.get("replies", 0)),
            str(kpis.get("demos_booked", 0)),
        ],
        [
            f"{kpis.get('active_leads', 0)} active · {kpis.get('archived_leads', 0)} archived",
            f"{kpis.get('open_rate', 0)}% open rate",
            f"{kpis.get('reply_rate', 0)}% reply rate",
            f"{kpis.get('demo_rate', 0)}% demo rate",
        ],
    ]
    t = Table(kpi_data, hAlign="LEFT", colWidths=[2.3 * inch] * 4)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ff4500")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, 1), 16),
        ("FONTSIZE", (0, 2), (-1, 2), 8),
        ("TEXTCOLOR", (0, 2), (-1, 2), colors.HexColor("#808080")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e6e6e6")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 8))

    # ── Intent breakdown ─────────────────────────────────────────────────────
    intent = data.get("intent") or {}
    if intent:
        elements.append(Paragraph("Intent breakdown", style_h2))
        irows = [["Intent", "Count"]] + [
            [str(k).replace("_", " ").title(), str(v)] for k, v in intent.items()
        ]
        it = Table(irows, hAlign="LEFT")
        it.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.black),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e6e6e6")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
        ]))
        elements.append(it)
        elements.append(Spacer(1, 8))

    # ── Per-product roll-up (same columns + numbers as the dashboard) ────────
    per_product = data.get("per_product") or []
    if per_product:
        elements.append(Paragraph("Per-product roll-up", style_h2))
        rows = [["Product", "Type", "Leads", "Sent", "Opened", "Clicked", "Replied", "Demos", "Open %", "Reply %"]]
        for p in per_product:
            rows.append([
                p.get("name") or "—",
                (p.get("entity_type") or "product").title(),
                str(p.get("leads", 0)),
                str(p.get("sent", 0)),
                str(p.get("opened", 0)),
                str(p.get("clicked", 0)),
                str(p.get("replied", 0)),
                str(p.get("demos", 0)),
                str(p.get("open_rate", 0)),
                str(p.get("reply_rate", 0)),
            ])
        pt = Table(rows, hAlign="LEFT")
        pt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.black),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e6e6e6")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
        ]))
        elements.append(pt)

    elements.append(PageBreak())

    # Per-lead table
    leads = data.get("leads") or []
    elements.append(Paragraph(f"Leads ({len(leads)})", style_h2))
    if not leads:
        elements.append(Paragraph("No leads recorded yet.", style_normal))
    else:
        header = [
            "Email", "Name", "Company", "Role",
            "Product", "Campaign", "Status", "Step",
            "Emails", "Last touch", "Demos",
        ]
        rows = [header]
        for r in leads:
            name = " ".join(
                x for x in (r.get("first_name") or "", r.get("last_name") or "") if x
            ).strip() or "—"
            last_touch = r.get("last_touch_at")
            last_touch_s = (
                last_touch.strftime("%Y-%m-%d") if hasattr(last_touch, "strftime")
                else (last_touch or "")
            )
            rows.append([
                (r.get("email") or "—")[:40],
                name[:32],
                (r.get("company_name") or "—")[:28],
                (r.get("role") or "—")[:24],
                (r.get("product_name") or "—")[:18],
                (r.get("campaign_name") or "—")[:18],
                (r.get("lead_status") or "—")[:16],
                str(r.get("current_step") if r.get("current_step") is not None else "—"),
                str(r.get("email_n") or 0),
                last_touch_s[:16],
                str(r.get("demos_booked") or 0),
            ])
        lt = Table(rows, hAlign="LEFT", repeatRows=1)
        lt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ff4500")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("GRID", (0, 0), (-1, -1), 0.2, colors.HexColor("#e6e6e6")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elements.append(lt)

    doc.build(elements)
    return buf.getvalue()


# ─── Endpoint ────────────────────────────────────────────────────────────────


@router.get("")
def list_reports(
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Stub for the frontend's "past reports" list.

    The Reports page polls `GET /nexus/reports` on mount to populate a
    "Past reports" panel of previously generated daily digests. Today we
    don't persist past reports anywhere (the daily_report.py job emits
    them but doesn't archive — they're sent to the operator via email).
    Returning an empty list keeps the page clean without the 404 noise
    in the backend log. When archival is wired, swap this for a real
    listing query.
    """
    return {"reports": []}


@router.get("/leads")
def report_leads(
    product_id: Optional[int] = Query(None),
    entity_type: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Leads detail for the current dashboard slice, as JSON — powers the
    leads table appended to the client-side (html2canvas + jsPDF) PDF export.

    Returns the SAME 9 columns the CSV uses, in the same order, so the PDF
    and CSV leads tables always match: Name, Role, Email, Company,
    Product / Entity, Step, Sent Emails, Last Contacted, Demos Booked.
    """
    ws_id = getattr(workspace, "id", None)
    et = (entity_type or "").strip().lower()
    if et not in ("product", "service", "gcc"):
        et = None
    if ws_id is None:
        raise HTTPException(status_code=500, detail="workspace has no .id")
    data = _build_workspace_report(
        db, workspace, workspace_id=ws_id, product_id=product_id,
        entity_type=et, start_date=start_date, end_date=end_date,
    )
    columns = _LEADS_HEADER
    rows = [_lead_table_row(r, i) for i, r in enumerate(data.get("leads", []), start=1)]
    return {"columns": columns, "rows": rows, "count": len(rows)}


@router.get("/export")
def export_report(
    format: str = Query("csv", pattern=r"^(csv|pdf)$"),
    product_id: Optional[int] = Query(None),
    entity_type: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Download a detailed workspace report.

    - `format=csv` → flat per-lead CSV (16 columns).
    - `format=pdf` → multi-page PDF with totals + per-product roll-up +
       per-lead detail table.

    Optional slice filter:
    - `product_id` → restrict to one product's leads/campaigns.
    - `entity_type` ('product' | 'service') → restrict to that target type.

    When neither is set, the report is workspace-wide (unchanged from
    the original behaviour — the additive SQL splices evaluate to "").

    Returns a file-attachment response — frontend should fetch with
    `responseType: 'blob'` and use `URL.createObjectURL` + `<a download>`
    to trigger the browser download.
    """
    # Split the try/except so the log + 500 response point at the
    # ACTUAL failing stage (build vs render). The previous combined
    # try blamed every PDF crash on "report build failed" even when
    # the data was fine.
    ws_id = getattr(workspace, "id", None)
    et = (entity_type or "").strip().lower()
    if et not in ("product", "service", "gcc"):
        et = None
    logger.info(
        "export_report: workspace.id=%s format=%s product_id=%s entity_type=%s",
        ws_id, format, product_id, et,
    )
    if ws_id is None:
        raise HTTPException(
            status_code=500,
            detail="workspace dependency returned an object without `.id`",
        )

    try:
        data = _build_workspace_report(
            db,
            workspace,
            workspace_id=ws_id,
            product_id=product_id,
            entity_type=et,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("report build failed for workspace_id=%s", ws_id)
        raise HTTPException(
            status_code=500, detail=f"report build failed: {e}"
        ) from e

    stamp = datetime.utcnow().strftime("%Y-%m-%d")
    # Filename suffix surfaces the slice in the downloaded file's name
    # so users can tell exported slices apart at-a-glance in Downloads.
    slice_tag = ""
    if product_id is not None:
        slice_tag = f"-product-{product_id}"
    elif et:
        slice_tag = f"-{et}"

    if format == "pdf":
        try:
            body = _render_pdf(data)
        except Exception as e:  # noqa: BLE001
            logger.exception("PDF render failed for workspace_id=%s", ws_id)
            raise HTTPException(
                status_code=500, detail=f"PDF render failed: {e}"
            ) from e
        return Response(
            content=body,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="nexus-report{slice_tag}-{stamp}.pdf"'
                ),
            },
        )

    # csv (default)
    try:
        body = _render_csv(data)
    except Exception as e:  # noqa: BLE001
        logger.exception("CSV render failed for workspace_id=%s", ws_id)
        raise HTTPException(
            status_code=500, detail=f"CSV render failed: {e}"
        ) from e
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="nexus-report{slice_tag}-{stamp}.csv"'
            ),
        },
    )
