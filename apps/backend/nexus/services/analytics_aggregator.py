"""Campaign-level metric aggregator.

Pure SQL aggregation across Phase 4 (Touchpoint), Phase 5 (InboundMessage),
Phase 6 (Outreach + DemoBooking) tables. Sibling tables are referenced by raw
table name via SQL because the model classes belong to other agents and may
not yet exist as Python classes at import time.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("pipelyt.nexus.analytics_agg")


def _safe_scalar(db: Session, sql: str, params: Dict[str, Any]) -> int:
    """Run a COUNT() SQL safely — return 0 if the underlying table doesn't exist yet."""
    try:
        result = db.execute(text(sql), params).scalar()
        return int(result or 0)
    except Exception as e:  # noqa: BLE001
        logger.debug("analytics scalar query skipped: %s", str(e)[:150])
        try:
            db.rollback()
        except Exception:
            pass
        return 0


def _safe_rows(db: Session, sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Run a SELECT safely — return [] if the table doesn't exist yet."""
    try:
        rows = db.execute(text(sql), params).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.debug("analytics rows query skipped: %s", str(e)[:150])
        try:
            db.rollback()
        except Exception:
            pass
        return []


def _product_group_key_sql(alias: str) -> str:
    """The per-product `group_key` expression for a `nexus_products` row aliased
    `alias` — IDENTICAL to the one in analytics_per_product
    (`LOWER(TRIM(name)) || '||' || entity_type`, with a per-id fallback for
    empty names). Used to expand a single selected product_id to every same-name
    sibling so a filtered KPI matches the merged per-product row."""
    return (
        f"COALESCE(NULLIF(LOWER(TRIM({alias}.name)), '') || '||' || "
        f"COALESCE(NULLIF({alias}.icp->>'entity_type', ''), 'product'), "
        f"'__id__::' || {alias}.id::text)"
    )


def product_group_in_sql(pid_param: str) -> str:
    """SQL predicate `c.product_id IN (... same name+entity group as :pid ...)`.

    A selected product_id can be the *canonical* (highest-id) row of a same-name
    group while the actual campaigns hang off a different sibling row (duplicate
    products with the same name are created by the product-create path). The
    per-product table merges those siblings; this predicate makes the filter do
    the same, so the filtered KPIs/charts reconcile with the per-product row.
    Assumes `nexus_campaigns c` is in scope."""
    gk2 = _product_group_key_sql("p2")
    gk0 = _product_group_key_sql("p0")
    return (
        "c.product_id IN (SELECT p2.id FROM nexus_products p2, nexus_products p0 "
        f"WHERE p0.id = :{pid_param} AND p2.workspace_id = p0.workspace_id "
        "AND COALESCE(p2.status, '') <> 'archived' "
        f"AND {gk2} = {gk0})"
    )


def _slice_campaign_ids_sql(
    params: Dict[str, Any],
    *,
    product_id: Optional[int],
    entity_type: Optional[str],
) -> Optional[str]:
    """Build a `SELECT c.id FROM nexus_campaigns c ... WHERE ...` subquery
    that yields the campaign ids matching the dashboard's product / entity
    filter. Returns None when no filter is requested — callers then skip
    splicing it in, so the unfiltered SQL path is unchanged.

    Mutates `params` to add `_slice_pid` and/or `_slice_et` keys (prefixed
    with `_slice_` to avoid colliding with any caller-supplied key such as
    `:cid`, `:ws`, `:since`).
    """
    conds: List[str] = []
    # 'gcc' is a first-class entity type (icp.entity_type='gcc'), filtered by
    # exact match like 'service'. 'product' keeps the COALESCE default below.
    needs_products = entity_type in ("product", "service", "gcc")
    if product_id is not None:
        params["_slice_pid"] = int(product_id)
        conds.append(product_group_in_sql("_slice_pid"))
    if needs_products:
        params["_slice_et"] = entity_type
        if entity_type == "product":
            # Default for missing icp.entity_type is 'product' — same
            # convention as /per-product, so a filter "product" includes
            # rows where the icp blob simply hasn't set entity_type yet.
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


def parse_period(period: Optional[str]) -> int:
    """Parse '30d', '7d', '90d' → days (default 30, max 365)."""
    if not period:
        return 30
    p = period.strip().lower()
    try:
        if p.endswith("d"):
            n = int(p[:-1])
        elif p.endswith("w"):
            n = int(p[:-1]) * 7
        elif p.endswith("m"):
            n = int(p[:-1]) * 30
        else:
            n = int(p)
    except Exception:  # noqa: BLE001
        n = 30
    return max(1, min(n, 365))


def compute_summary(
    db: Session,
    *,
    workspace_id: int,
    campaign_id: Optional[int] = None,
    product_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    period_days: int = 30,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Roll up campaign-level metrics from ONE source of truth.

    Previously this mixed `nexus_outreach` (legacy Phase 6 table) with
    inconsistent timestamp predicates per stage — opens could exceed
    sent, summary disagreed with per-product (which reads touchpoints).
    Rewritten to query `nexus_touchpoints` directly with a single
    `sent_at >= :since` filter so every stage measures the same cohort
    of sends. Per-product uses the same SQL pattern → numbers reconcile.

    nexus_outreach is left intact for the (separate) timeseries chart;
    we just don't double-count it here.
    """
    # `since` may be passed explicitly (custom FROM/TO range); otherwise it
    # defaults to the trailing `period_days` window (back-compat). `until` is
    # an optional upper bound for a closed date range.
    if since is None:
        since = datetime.utcnow() - timedelta(days=period_days)
    params: Dict[str, Any] = {"ws": workspace_id, "since": since}
    # Per-column upper-bound clauses (each metric uses a different timestamp).
    _until_t = _until_m = _until_created = _until_obs = _until_db = ""
    if until is not None:
        params["until"] = until
        _until_t = " AND t.sent_at <= :until"
        _until_m = " AND m.received_at <= :until"
        _until_created = " AND created_at <= :until"
        _until_obs = " AND isg.observed_at <= :until"
        _until_db = " AND db.created_at <= :until"
    where_cid = ""
    if campaign_id:
        params["cid"] = campaign_id
        where_cid = "AND t.campaign_id = :cid"

    # Optional dashboard slice filter (product / entity_type). When both
    # are None, slice_select is None and every spliced `slice_*` fragment
    # below is "" — the SQL executed is byte-for-byte identical to the
    # pre-filter version, so existing callers (daily_report, NEXUS
    # Analytics tab, etc.) see no change.
    slice_select = _slice_campaign_ids_sql(
        params, product_id=product_id, entity_type=entity_type
    )
    slice_t = (
        f" AND t.campaign_id IN ({slice_select})" if slice_select else ""
    )
    slice_ls = (
        f" AND ls.campaign_id IN ({slice_select})" if slice_select else ""
    )
    slice_nl = (
        f" AND nl.campaign_id IN ({slice_select})" if slice_select else ""
    )
    slice_db = (
        f" AND campaign_id IN ({slice_select})" if slice_select else ""
    )

    # Single base predicate — every counter restricts to the SAME cohort:
    # touchpoints in this workspace, with a successful send_at inside the
    # period window. Opens / clicks / replies are then derived from this
    # cohort, so monotonicity is guaranteed by construction.
    #
    # `lead_sequence_id IS NOT NULL` requirement matches per-product's
    # JOIN through lead_sequences. Orphan touchpoints (direct sends from
    # test scripts, webhook-only entries, etc.) won't appear under any
    # product breakdown, so excluding them here keeps summary == sum of
    # per-product rows.
    #
    # JOIN nexus_campaigns + filter archived: touchpoints attached to
    # soft-deleted duplicate campaigns must not contribute to the
    # workspace-level totals. Without this filter, the headline KPI
    # would show "314 sent" while per-product (which correctly excludes
    # archived) shows ~54 — same data, two different filters, looks
    # broken to operators.
    base = (
        f"FROM nexus_touchpoints t "
        f"LEFT JOIN nexus_campaigns c ON c.id = t.campaign_id "
        f"WHERE t.workspace_id = :ws {where_cid} "
        f"  AND t.status = 'sent' "
        f"  AND t.sent_at >= :since "
        f"  AND t.lead_sequence_id IS NOT NULL "
        f"  AND (c.id IS NULL OR COALESCE(c.status, '') <> 'archived')"
        f"  {slice_t}{_until_t}"
    )

    sent = _safe_scalar(db, f"SELECT COUNT(*) {base}", params)
    opens = _safe_scalar(db, f"SELECT COUNT(*) {base} AND COALESCE(t.opens_count,0) > 0", params)
    opens_unique = _safe_scalar(
        db, f"SELECT COUNT(DISTINCT t.lead_id) {base} AND COALESCE(t.opens_count,0) > 0", params
    )
    clicks = _safe_scalar(
        db, f"SELECT COUNT(*) {base} AND COALESCE(t.clicks_count,0) > 0", params
    )

    # Replies = COUNT of inbound messages this workspace received in
    # the window. Source: nexus_inbound_messages (direction='inbound').
    #
    # Previously this counted lead_sequence rows whose status had been
    # flipped to 'replied' by the inbox poller. That undercounted —
    # most inbound messages don't get matched back to a specific
    # lead_sequence (sender's email doesn't resolve, thread routing
    # misses, etc.), so the inbox poller often classifies the intent
    # without flipping any lead status. Result: 18 inbound messages on
    # the intent breakdown chart but `replies = 1` on the headline KPI.
    #
    # Per-product user request: every inbound message counts as a
    # reply, regardless of whether the matcher resolved it back to a
    # lead_sequence. The intent breakdown and the Replies KPI now
    # reconcile (same underlying COUNT).
    replies_cid_clause = ""
    if campaign_id:
        replies_cid_clause = (
            "AND EXISTS (SELECT 1 FROM nexus_lead_sequences ls "
            "  WHERE ls.id = th.lead_sequence_id AND ls.campaign_id = :cid)"
        )
    # Slice filter for replies: restrict inbound messages to those whose
    # resolved lead_sequence belongs to a campaign in the slice. Empty
    # string when no filter is set → unfiltered SQL unchanged.
    replies_slice_clause = (
        " AND EXISTS (SELECT 1 FROM nexus_lead_sequences ls "
        "  WHERE ls.id = th.lead_sequence_id" + slice_ls + ")"
    ) if slice_select else ""
    replies = _safe_scalar(
        db,
        f"""
        SELECT COUNT(*)
          FROM nexus_inbound_messages m
          JOIN nexus_inbound_threads th ON th.id = m.thread_id
         WHERE th.workspace_id = :ws
           AND m.direction = 'inbound'
           AND m.received_at >= :since
           {replies_cid_clause}
           {replies_slice_clause}
           {_until_m}
        """,
        params,
    )

    bounces = _safe_scalar(
        db,
        f"""SELECT COUNT(*) FROM nexus_touchpoints t
             LEFT JOIN nexus_campaigns c ON c.id = t.campaign_id
             WHERE t.workspace_id = :ws {where_cid}
               AND t.status = 'bounced'
               AND t.sent_at >= :since
               AND (c.id IS NULL OR COALESCE(c.status, '') <> 'archived')
               {_until_t}
        """,
        params,
    )

    # Unsubscribes — separate table. Optional campaign filter via lead.
    unsubscribes = _safe_scalar(
        db,
        f"""SELECT COUNT(*) FROM nexus_suppression_list
           WHERE workspace_id = :ws AND created_at >= :since
           {_until_created}
        """,
        params,
    )

    # Demo bookings — scoped by the CAMPAIGN's workspace (authoritative), NOT
    # the booking's own workspace_id. A booking's workspace_id can be wrong
    # (set to a stale/other workspace), and bookings on deleted/NULL campaigns
    # must not count. This JOIN matches the per-product demos rollup exactly,
    # so the headline DEMOS KPI == sum of the per-product demos column.
    # COUNT DISTINCT booking_id_external dedupes the SAME MS appointment when it
    # was ingested into more than one workspace (the sync keys on
    # (workspace_id, booking_id_external), so a shared booking business fans the
    # same appointment out to every workspace). Falls back to the row id when
    # there's no external id (manual bookings) so those still count once each.
    where_demo = "" if not campaign_id else "AND db.campaign_id = :cid"
    demo_bookings = _safe_scalar(
        db,
        f"""SELECT COUNT(DISTINCT COALESCE(db.booking_id_external, db.id::text))
             FROM nexus_demo_bookings db
             JOIN nexus_campaigns c ON c.id = db.campaign_id
            WHERE c.workspace_id = :ws
              AND COALESCE(c.status, '') <> 'archived'
              AND db.created_at >= :since
              {where_demo}
              {slice_db}
              {_until_db}
        """,
        params,
    )

    # Intent signals — table has lead_id but no workspace_id. Scope by
    # joining through nexus_leads (the workspace junction). Without this
    # join the count leaked across tenants — every workspace saw every
    # other workspace's intent signals.
    intent_signals = _safe_scalar(
        db,
        f"""SELECT COUNT(*)
             FROM nexus_intent_signals isg
             JOIN nexus_leads nl ON nl.global_lead_id = isg.lead_id
            WHERE nl.workspace_id = :ws
              AND isg.observed_at >= :since
              {slice_nl}
              {_until_obs}
        """,
        params,
    )

    # Replies breakdown by intent — `nexus_inbound_messages` has neither
    # workspace_id nor created_at. Real columns: thread_id + received_at,
    # and workspace lives on nexus_inbound_threads. Join through threads
    # to scope properly. campaign_id filter routes through the
    # thread→lead_sequence→campaign chain.
    intent_cid_clause = ""
    if campaign_id:
        intent_cid_clause = (
            "AND EXISTS (SELECT 1 FROM nexus_lead_sequences ls "
            " WHERE ls.id = th.lead_sequence_id AND ls.campaign_id = :cid)"
        )
    # Same slice predicate as `replies` so the breakdown sums to the
    # filtered Replies KPI.
    intent_slice_clause = (
        " AND EXISTS (SELECT 1 FROM nexus_lead_sequences ls "
        " WHERE ls.id = th.lead_sequence_id" + slice_ls + ")"
    ) if slice_select else ""
    intent_rows = _safe_rows(
        db,
        f"""SELECT m.intent, COUNT(*) AS n
              FROM nexus_inbound_messages m
              JOIN nexus_inbound_threads th ON th.id = m.thread_id
             WHERE th.workspace_id = :ws
               AND m.direction = 'inbound'
               AND m.received_at >= :since
               {intent_cid_clause}
               {intent_slice_clause}
               {_until_m}
             GROUP BY m.intent
        """,
        params,
    )
    replies_breakdown = {
        str(r.get("intent") or "unknown"): int(r.get("n") or 0) for r in intent_rows
    }

    intent_signal_rate = (intent_signals / sent * 100.0) if sent else 0.0
    demo_book_rate = (demo_bookings / sent * 100.0) if sent else 0.0
    bounce_rate = (bounces / sent * 100.0) if sent else 0.0
    unsubscribe_rate = (unsubscribes / sent * 100.0) if sent else 0.0
    open_rate = (opens / sent * 100.0) if sent else 0.0
    click_rate = (clicks / sent * 100.0) if sent else 0.0
    reply_rate = (replies / sent * 100.0) if sent else 0.0

    return {
        "sent": sent,
        "opens": opens,
        "opens_unique": opens_unique,
        "clicks": clicks,
        "replies": replies,
        "replies_breakdown_by_intent": replies_breakdown,
        "demo_bookings": demo_bookings,
        "open_rate": round(open_rate, 2),
        "click_rate": round(click_rate, 2),
        "reply_rate": round(reply_rate, 2),
        "intent_signal_rate": round(intent_signal_rate, 2),
        "demo_book_rate": round(demo_book_rate, 2),
        "bounce_rate": round(bounce_rate, 2),
        "unsubscribe_rate": round(unsubscribe_rate, 2),
        "unsubscribes": unsubscribes,
    }


def compute_funnel(
    db: Session,
    *,
    workspace_id: int,
    campaign_id: Optional[int] = None,
    product_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    period_days: int = 30,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Sent → Opened → Clicked → Replied → Booked.

    Each stage is capped at **Sent** (the funnel's real ceiling) rather than
    at the immediately-preceding stage. This keeps every stage consistent with
    its headline KPI: Replied == the Replies KPI and Booked == the Demos KPI.

    Why not clamp to the previous stage? Opens/clicks rely on email tracking
    pixels that are often not configured, so Opened/Clicked come back as 0 even
    when there ARE real replies. Chaining the clamp (Replied ≤ Clicked) would
    then zero out Replied — making the funnel disagree with the Replies KPI.
    Capping to Sent prevents any stage exceeding total sends while letting the
    real reply/booking counts show through. Raw counts are also on /summary.
    """
    s = compute_summary(
        db,
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        product_id=product_id,
        entity_type=entity_type,
        period_days=period_days,
        since=since,
        until=until,
    )
    sent_c = int(s["sent"])

    def _cap(v: Any) -> int:
        # No stage can exceed total sends; otherwise show the true count so
        # the stage reconciles with its KPI tile.
        return min(int(v or 0), sent_c)

    opens_c = _cap(s["opens"])
    clicks_c = _cap(s["clicks"])
    replies_c = _cap(s["replies"])
    booked_c = _cap(s["demo_bookings"])

    def conv(num: int, den: int) -> Optional[float]:
        return round(num / den * 100.0, 2) if den else None

    return [
        {"label": "Sent",    "count": sent_c,    "conv_from_prev": None},
        {"label": "Opened",  "count": opens_c,   "conv_from_prev": conv(opens_c, sent_c)},
        {"label": "Clicked", "count": clicks_c,  "conv_from_prev": conv(clicks_c, opens_c)},
        {"label": "Replied", "count": replies_c, "conv_from_prev": conv(replies_c, clicks_c)},
        {"label": "Booked",  "count": booked_c,  "conv_from_prev": conv(booked_c, replies_c)},
    ]


def compute_timeseries(
    db: Session,
    *,
    workspace_id: int,
    campaign_id: Optional[int] = None,
    product_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    period_days: int = 30,
    column: str = "sent_at",
    status_in: tuple = ("sent", "opened", "clicked", "replied"),
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Daily count of outreach events. Returns a complete date series with zeros.

    Reads from `nexus_touchpoints` (the canonical source of truth for
    sends) — same table that `compute_summary` queries, so the dashboard
    chart and the headline KPIs reconcile. Previously this read from
    the legacy `nexus_outreach` table, which only contained a small
    subset of historical sends (~5 of 318 in the test workspace) → the
    chart showed days as zero even though hundreds of emails went out.

    Default column changed `email_sent_at` → `sent_at` to match the
    touchpoint schema. For opens/clicks we approximate with
    `last_opened_at` / `last_clicked_at` since touchpoints stores
    counters, not per-event timestamps. Reply timeseries falls back to
    the touchpoint's `sent_at` filtered to lead_sequences in 'replied'
    status — close enough for the daily-bucket chart.
    """
    if since is None:
        since = datetime.utcnow() - timedelta(days=period_days)
    params: Dict[str, Any] = {"ws": workspace_id, "since": since}
    _until_t = _until_m = ""
    if until is not None:
        params["until"] = until
        _until_t = f" AND t.{column} <= :until"
        _until_m = " AND m.received_at <= :until"
    where_cid = ""
    if campaign_id:
        params["cid"] = campaign_id
        where_cid = "AND t.campaign_id = :cid"

    # Same slice predicate used by compute_summary so the chart and KPI
    # tiles reflect the same cohort. When product_id / entity_type are
    # both None, slice_select is None → every spliced clause below is ""
    # → SQL identical to the pre-filter implementation.
    slice_select = _slice_campaign_ids_sql(
        params, product_id=product_id, entity_type=entity_type
    )
    slice_t = (
        f" AND t.campaign_id IN ({slice_select})" if slice_select else ""
    )
    slice_ls = (
        f" AND ls.campaign_id IN ({slice_select})" if slice_select else ""
    )

    status_list = ",".join(f"'{s}'" for s in status_in)

    # Count from nexus_inbound_messages so the chart matches the KPI card,
    # which also counts inbound messages directly.
    if status_in == ("replied",):
        replies_cid_clause = ""
        if campaign_id:
            replies_cid_clause = (
                "AND EXISTS (SELECT 1 FROM nexus_lead_sequences ls "
                "  WHERE ls.id = th.lead_sequence_id AND ls.campaign_id = :cid)"
            )
        replies_slice_clause = (
            " AND EXISTS (SELECT 1 FROM nexus_lead_sequences ls "
            " WHERE ls.id = th.lead_sequence_id" + slice_ls + ")"
        ) if slice_select else ""
        rows = _safe_rows(
            db,
            f"""SELECT DATE(m.received_at) AS day, COUNT(*) AS n
                  FROM nexus_inbound_messages m
                  JOIN nexus_inbound_threads th ON th.id = m.thread_id
                 WHERE th.workspace_id = :ws
                   AND m.direction = 'inbound'
                   AND m.received_at >= :since
                   {replies_cid_clause}
                   {replies_slice_clause}
                   {_until_m}
                 GROUP BY DATE(m.received_at)
                 ORDER BY day ASC
            """,
            params,
        )
    else:
        rows = _safe_rows(
            db,
            f"""SELECT DATE(t.{column}) AS day, COUNT(*) AS n
                  FROM nexus_touchpoints t
                  LEFT JOIN nexus_campaigns c ON c.id = t.campaign_id
                 WHERE t.workspace_id = :ws {where_cid}
                   AND t.status IN ({status_list})
                   AND t.{column} >= :since
                   -- Filter touchpoints attached to archived (soft-
                   -- deleted) campaigns so the chart reconciles with
                   -- the headline KPIs and per-product table.
                   AND (c.id IS NULL OR COALESCE(c.status, '') <> 'archived')
                   {slice_t}
                   {_until_t}
                 GROUP BY DATE(t.{column})
                 ORDER BY day ASC
            """,
            params,
        )

    # For the replies branch, rows are per-lead_sequence; collapse to
    # per-day counts. For all other branches, rows are already per-day.
    by_day: Dict[str, int] = {}
    for r in rows:
        key = str(r["day"]) if r["day"] is not None else None
        if key is None:
            continue
        by_day[key] = by_day.get(key, 0) + int(r["n"])

    series: List[Dict[str, Any]] = []
    today = datetime.utcnow().date()
    for i in range(period_days - 1, -1, -1):
        d = today - timedelta(days=i)
        key = d.isoformat()
        series.append({"date": key, "count": by_day.get(key, 0)})
    return series
