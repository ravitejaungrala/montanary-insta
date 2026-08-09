"""Pulls REAL outreach data into the same `OutreachRow` shape simulator.py
produces (see /implementation.md §5.1). Mirrors the safe-query style of
nexus/services/analytics_aggregator.py — a sibling table missing on a given
deploy degrades to an empty result instead of a 500.

Segment fields (industry/role/location) prefer the LEAD's own ground-truth
data (nexus_global_leads) over the campaign's target filter, since a
targeting query often returns leads slightly outside the exact filter — see
/implementation.md §1. Revenue band and technologies have no reliable
per-lead ground truth in this schema, so they're always derived from the
campaign's `icp` JSONB (`revenue_range`, `buyer_technologies`).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from .dimensions import OutreachRow, revenue_band_for

logger = logging.getLogger("pipelyt.nexus.performance_agent.aggregator")

_QUERY = """
SELECT
    t.campaign_id,
    t.lead_id,
    t.channel,
    le.variant_key,
    le.kind AS cadence_step,
    t.step AS raw_step,
    t.sent_at,
    EXISTS (
        SELECT 1 FROM nexus_inbound_threads th
        JOIN nexus_inbound_messages im ON im.thread_id = th.id
        WHERE th.lead_sequence_id = t.lead_sequence_id AND im.direction = 'inbound'
    ) AS replied,
    (
        SELECT im2.intent FROM nexus_inbound_threads th2
        JOIN nexus_inbound_messages im2 ON im2.thread_id = th2.id
        WHERE th2.lead_sequence_id = t.lead_sequence_id
          AND im2.direction = 'inbound'
          AND im2.intent IN ('INTERESTED', 'DEMO_SCHEDULED')
        ORDER BY im2.received_at DESC
        LIMIT 1
    ) AS positive_intent,
    EXISTS (
        SELECT 1 FROM nexus_demo_bookings db
        WHERE db.lead_id = t.lead_id AND db.campaign_id = t.campaign_id
    ) AS meeting_booked,
    c.icp AS campaign_icp,
    gl.organization_industry,
    gl.person_country,
    COALESCE(gl.job_title, gl.role) AS lead_role
FROM nexus_touchpoints t
LEFT JOIN nexus_lead_emails le
       ON le.lead_sequence_id = t.lead_sequence_id AND le.step = t.step
LEFT JOIN nexus_campaigns c ON c.id = t.campaign_id
LEFT JOIN nexus_global_leads gl ON gl.id = t.lead_id
WHERE t.workspace_id = :ws
  AND t.status = 'sent'
  AND t.channel IS NOT NULL
  {campaign_clause}
  {since_clause}
"""


def _safe_rows(db: Session, sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Same tolerate-a-missing-sibling-table pattern as analytics_aggregator._safe_rows."""
    try:
        rows = db.execute(text(sql), params).fetchall()
        return [dict(r._mapping) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("performance_agent.aggregator: query skipped: %s", str(exc)[:200])
        try:
            db.rollback()
        except Exception:
            pass
        return []


def _icp_list(icp: Optional[dict], key: str) -> List[str]:
    if not isinstance(icp, dict):
        return []
    val = icp.get(key)
    if isinstance(val, dict):
        val = val.get("values")
    if isinstance(val, list):
        return [str(v).strip() for v in val if isinstance(v, str) and v.strip()]
    return []


def _icp_single(icp: Optional[dict], key: str) -> Optional[str]:
    """Only usable when the campaign targeted exactly ONE value — a
    multi-value target list is ambiguous per touchpoint (see module docstring)."""
    values = _icp_list(icp, key)
    return values[0] if len(values) == 1 else None


def _icp_revenue_band(icp: Optional[dict]) -> Optional[str]:
    if not isinstance(icp, dict):
        return None
    rr = icp.get("revenue_range")
    # Apollo-style revenue_range can be a single {min,max} or a list of them
    # (see NexusNewCampaign.jsx revenueFromStored/revenueLabel) — use the
    # first window when it's a list; a campaign spanning several disjoint
    # windows is rare and the first is still a reasonable representative band.
    if isinstance(rr, list) and rr:
        rr = rr[0]
    if isinstance(rr, dict):
        return revenue_band_for(rr.get("min"), rr.get("max"))
    return None


# Mirrors sequencer.py's _STEP_KIND — fallback for touchpoints whose
# nexus_lead_emails.kind is NULL (e.g. rows predating that column, or a
# channel that doesn't route through nexus_lead_emails).
_STEP_KIND_FALLBACK = {0: "initial", 1: "followup_1", 2: "followup_2", 3: "closing"}


def _cadence_step(rec: Dict[str, Any]) -> Optional[str]:
    kind = rec.get("cadence_step")
    if kind:
        return str(kind)
    raw_step = rec.get("raw_step")
    if raw_step is None:
        return None
    return _STEP_KIND_FALLBACK.get(int(raw_step))


def _row_from_record(rec: Dict[str, Any]) -> Optional[OutreachRow]:
    sent_at = rec.get("sent_at")
    if not isinstance(sent_at, datetime):
        return None
    icp = rec.get("campaign_icp") or {}

    industry = rec.get("organization_industry") or _icp_single(icp, "organization_industries")
    role = rec.get("lead_role") or _icp_single(icp, "person_titles")
    location = rec.get("person_country") or _icp_single(icp, "person_locations")
    technologies = _icp_list(icp, "buyer_technologies")

    return OutreachRow(
        campaign_id=int(rec["campaign_id"]) if rec.get("campaign_id") is not None else 0,
        lead_id=int(rec["lead_id"]) if rec.get("lead_id") is not None else 0,
        channel=str(rec.get("channel") or "email"),
        variant_key=str(rec.get("variant_key") or "unknown"),
        sent_at=sent_at,
        replied=bool(rec.get("replied")),
        intent=rec.get("positive_intent"),
        meeting_booked=bool(rec.get("meeting_booked")),
        industry=industry,
        revenue_band=_icp_revenue_band(icp),
        role=role,
        cadence_step=_cadence_step(rec),
        technologies=technologies,
        location=location,
    )


def collect_outreach_rows(
    db: Session,
    workspace_id: int,
    campaign_id: Optional[int] = None,
    since: Optional[datetime] = None,
) -> List[OutreachRow]:
    """One OutreachRow per sent touchpoint in `workspace_id`, joined to its
    reply/intent (if any), meeting booking (if any), and segment facets from
    the campaign's ICP + the lead's own ground-truth data."""
    params: Dict[str, Any] = {"ws": workspace_id}
    campaign_clause = ""
    if campaign_id:
        params["cid"] = campaign_id
        campaign_clause = "AND t.campaign_id = :cid"
    since_clause = ""
    if since is not None:
        params["since"] = since
        since_clause = "AND t.sent_at >= :since"

    sql = _QUERY.format(campaign_clause=campaign_clause, since_clause=since_clause)
    records = _safe_rows(db, sql, params)

    rows: List[OutreachRow] = []
    for rec in records:
        row = _row_from_record(rec)
        if row is not None:
            rows.append(row)
    return rows


def count_real_touchpoints(db: Session, workspace_id: int) -> int:
    """Cheap count used by generator.py's cold-start switch (§6.1) — does
    NOT run the full join, just how much real send volume exists."""
    try:
        result = db.execute(
            text("SELECT COUNT(*) FROM nexus_touchpoints WHERE workspace_id = :ws AND status = 'sent'"),
            {"ws": workspace_id},
        ).scalar()
        return int(result or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("performance_agent.aggregator: count_real_touchpoints skipped: %s", str(exc)[:200])
        try:
            db.rollback()
        except Exception:
            pass
        return 0


def count_real_outcomes(db: Session, workspace_id: int) -> int:
    """Positive replies + meetings booked — proof the real data has actual
    OUTCOME signal to rank on, not just send volume. Paired with
    count_real_touchpoints() in generator._resolve_data_source(): sends
    alone can't tell you whether the data is informative (500 sends with 0
    replies still produces a flat, prior-dominated ranking on every slice),
    so the cold-start switch requires both a volume floor AND an outcome
    floor before it will show 'real' data instead of the clearly-labeled
    simulated fallback. See /implementation.md §6.1."""
    from .dimensions import POSITIVE_INTENTS

    try:
        intents_sql = ",".join(f"'{i}'" for i in POSITIVE_INTENTS)
        positive_replies = db.execute(
            text(
                f"""
                SELECT COUNT(*) FROM nexus_inbound_messages m
                JOIN nexus_inbound_threads th ON th.id = m.thread_id
                WHERE th.workspace_id = :ws
                  AND m.direction = 'inbound'
                  AND m.intent IN ({intents_sql})
                """
            ),
            {"ws": workspace_id},
        ).scalar() or 0
        meetings = db.execute(
            text("SELECT COUNT(*) FROM nexus_demo_bookings WHERE workspace_id = :ws"),
            {"ws": workspace_id},
        ).scalar() or 0
        return int(positive_replies) + int(meetings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("performance_agent.aggregator: count_real_outcomes skipped: %s", str(exc)[:200])
        try:
            db.rollback()
        except Exception:
            pass
        return 0


__all__ = ["collect_outreach_rows", "count_real_touchpoints", "count_real_outcomes"]
