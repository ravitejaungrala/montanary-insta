"""Phase 6 performance router — per-campaign rolling insights.

Endpoints:
  GET /nexus/performance/stats — per-campaign rollup + dead zones + top performers
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_common import extract_user_id, extract_workspace_id, require_current_workspace, get_nexus_user
from nexus.models_phase6 import ConversionSnapshot, PerformanceInsight, WinningExample
from nexus.schemas_phase6 import (
    ActionItemOut,
    DeleteInsightOut,
    GenerateInsightIn,
    InsightListOut,
    PerformanceInsightOut,
    PerformanceStatsOut,
    RecommendationsOut,
    WinningExampleListOut,
    WinningExampleOut,
)
from nexus.services.performance_agent.generator import generate_insight

logger = logging.getLogger("pipelyt.nexus.performance")

router = APIRouter(prefix="/nexus/performance", tags=["nexus-performance"])


def _insight_out(row: PerformanceInsight) -> PerformanceInsightOut:
    data_source = None
    if isinstance(row.metrics, dict):
        data_source = row.metrics.get("data_source")
    recommendations = None
    if isinstance(row.campaign_insights, dict) and row.campaign_insights:
        raw_actions = row.campaign_insights.get("actions") or []
        actions = [
            ActionItemOut(title=a.get("title") or "", detail=a.get("detail") or "")
            for a in raw_actions
            if isinstance(a, dict)
        ]
        recommendations = RecommendationsOut(
            headline=row.campaign_insights.get("headline") or "",
            summary=row.campaign_insights.get("summary") or "",
            quick_wins=[str(q) for q in (row.campaign_insights.get("quick_wins") or []) if str(q).strip()],
            actions=actions,
            caveat=row.campaign_insights.get("caveat") or "",
        )
    return PerformanceInsightOut(
        id=row.id,
        status=row.status or "running",
        period_start=row.period_start,
        period_end=row.period_end,
        summary=row.summary or "",
        recommendations=recommendations,
        data_source=data_source,
        outreach_insights=row.outreach_insights,
        error=row.error or "",
        generated_at=row.generated_at,
    )


@router.get("/stats", response_model=PerformanceStatsOut)
def performance_stats(
    db: Session = Depends(get_db),
    user: Any = Depends(get_nexus_user),
    workspace: Any = Depends(require_current_workspace),
):
    workspace_id = extract_workspace_id(workspace) or 0
    user_id = extract_user_id(user)

    rows: List[Dict[str, Any]] = []
    try:
        result = db.execute(
            text(
                """SELECT c.id AS campaign_id,
                          COALESCE(p.name, 'Unnamed') AS product_name,
                          COALESCE(c.total_sent, 0) AS total_sent,
                          COALESCE(c.total_replies, 0) AS total_replies
                   FROM nexus_campaigns c
                   LEFT JOIN nexus_products p ON p.id = c.product_id
                   WHERE c.workspace_id = :ws
                     AND EXISTS (
                         SELECT 1 FROM nexus_leads nl
                          WHERE nl.campaign_id = c.id
                     )
                   ORDER BY c.id DESC
                """
            ),
            {"ws": workspace_id},
        ).fetchall()
        for r in result:
            m = dict(r._mapping)
            rows.append(m)
    except Exception as e:  # noqa: BLE001
        logger.debug("performance fetch campaigns skipped: %s", e)
        db.rollback()

    campaign_rows = []
    for r in rows:
        sent = int(r.get("total_sent") or 0)
        replies = int(r.get("total_replies") or 0)
        reply_rate = round(replies / sent * 100, 1) if sent else 0.0

        # Per-campaign lead count from nexus_outreach
        try:
            demos = db.execute(
                text(
                    """SELECT COUNT(*) FROM nexus_outreach
                       WHERE campaign_id = :cid AND status='demo_scheduled'"""
                ),
                {"cid": r["campaign_id"]},
            ).scalar() or 0
            total_leads = db.execute(
                text("SELECT COUNT(DISTINCT lead_id) FROM nexus_outreach WHERE campaign_id = :cid"),
                {"cid": r["campaign_id"]},
            ).scalar() or 0
        except Exception:
            db.rollback()
            demos, total_leads = 0, 0

        demo_rate = round(demos / sent * 100, 1) if sent else 0.0
        is_dead = sent >= 30 and reply_rate < 2

        campaign_rows.append({
            "campaign_id": int(r["campaign_id"]),
            "product_name": r.get("product_name") or "Unnamed",
            "total_leads": int(total_leads),
            "total_sent": sent,
            "replies": replies,
            "reply_rate": reply_rate,
            "demos": int(demos),
            "demo_rate": demo_rate,
            "is_dead": is_dead,
            "leads_by_status": {},
        })

    total_sent = sum(r["total_sent"] for r in campaign_rows)
    total_replies = sum(r["replies"] for r in campaign_rows)
    total_demos = sum(r["demos"] for r in campaign_rows)
    total_leads = sum(r["total_leads"] for r in campaign_rows)
    summary = {
        "campaigns": len(campaign_rows),
        "total_leads": total_leads,
        "total_sent": total_sent,
        "total_replies": total_replies,
        "total_demos": total_demos,
        "overall_reply_rate": round(total_replies / total_sent * 100, 1) if total_sent else 0.0,
        "overall_demo_rate": round(total_demos / total_sent * 100, 1) if total_sent else 0.0,
    }

    dead_zones = sorted(
        [r for r in campaign_rows if r["is_dead"]], key=lambda x: -x["total_sent"]
    )
    top_campaigns = sorted(campaign_rows, key=lambda x: -x["reply_rate"])[:5]

    snapshots_count = db.query(ConversionSnapshot).filter(
        ConversionSnapshot.workspace_id == workspace_id
    ).count()

    return PerformanceStatsOut(
        campaigns=campaign_rows,
        summary=summary,
        dead_zones=dead_zones,
        top_campaigns=top_campaigns,
        snapshots_count=snapshots_count,
    )


# ─── Performance Agent ──────────────────────────────────────────────────────
# Ranked-pattern insights (channel/variant/segment/timing) — see
# /implementation.md. New surface: NexusPerformanceAgent.jsx (not the removed
# NexusPerformance.jsx).


@router.get("", response_model=InsightListOut)
def list_insights(
    db: Session = Depends(get_db),
    user: Any = Depends(get_nexus_user),
    workspace: Any = Depends(require_current_workspace),
):
    workspace_id = extract_workspace_id(workspace) or 0
    try:
        rows = (
            db.query(PerformanceInsight)
            .filter(PerformanceInsight.workspace_id == workspace_id)
            .order_by(PerformanceInsight.created_at.desc())
            .limit(20)
            .all()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("list_insights query failed: %s", e)
        db.rollback()
        rows = []
    return InsightListOut(insights=[_insight_out(r) for r in rows])


@router.post("/generate", response_model=PerformanceInsightOut)
def generate(
    payload: GenerateInsightIn = GenerateInsightIn(),
    db: Session = Depends(get_db),
    user: Any = Depends(get_nexus_user),
    workspace: Any = Depends(require_current_workspace),
):
    workspace_id = extract_workspace_id(workspace) or 0
    # SECURITY: a client-supplied campaign_id must belong to the caller's
    # workspace before we generate insights over its data.
    if payload.campaign_id is not None and not db.execute(
        text("SELECT 1 FROM nexus_campaigns WHERE id = :id AND workspace_id = :ws LIMIT 1"),
        {"id": payload.campaign_id, "ws": workspace_id},
    ).first():
        raise HTTPException(status_code=404, detail="campaign not found in this workspace")
    insight_id = generate_insight(
        db,
        workspace_id=workspace_id,
        campaign_id=payload.campaign_id,
        force_source=payload.force_source,
    )
    row = db.query(PerformanceInsight).filter(PerformanceInsight.id == insight_id).first()
    return _insight_out(row)


@router.get("/winning-examples", response_model=WinningExampleListOut)
def list_winning_examples(
    db: Session = Depends(get_db),
    user: Any = Depends(get_nexus_user),
    workspace: Any = Depends(require_current_workspace),
):
    workspace_id = extract_workspace_id(workspace) or 0
    try:
        rows = (
            db.query(WinningExample)
            .filter(WinningExample.workspace_id == workspace_id)
            .order_by(WinningExample.reply_rate.desc())
            .limit(20)
            .all()
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("list_winning_examples query failed: %s", e)
        db.rollback()
        rows = []
    return WinningExampleListOut(
        examples=[
            WinningExampleOut(
                example_type=r.example_type,
                lead_segment=r.lead_segment,
                reply_rate=r.reply_rate or 0.0,
                email_subject=r.email_subject or "",
                text=r.text or "",
            )
            for r in rows
        ]
    )


@router.delete("/{insight_id}", response_model=DeleteInsightOut)
def delete_insight(
    insight_id: int,
    db: Session = Depends(get_db),
    user: Any = Depends(get_nexus_user),
    workspace: Any = Depends(require_current_workspace),
):
    """Deletes one insight report. Scoped to the caller's workspace_id so a
    workspace can never delete (or discover, via a 404-vs-403 timing/behavior
    difference) another workspace's insight by guessing an id.

    Does NOT touch nexus_winning_examples — that table is a standing "current
    best patterns" cache shared across insight runs, not owned by any single
    insight (see /implementation-v2.md §1)."""
    workspace_id = extract_workspace_id(workspace) or 0
    row = (
        db.query(PerformanceInsight)
        .filter(PerformanceInsight.id == insight_id, PerformanceInsight.workspace_id == workspace_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Insight not found")
    db.delete(row)
    db.commit()
    return DeleteInsightOut(deleted=True)
