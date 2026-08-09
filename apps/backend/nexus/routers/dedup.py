"""Phase 6 dedup router — bulk lead deduplication stats.

Endpoints:
  GET  /nexus/dedup/stats — counts of duplicates / multi-pitched leads
  POST /nexus/dedup/run   — collapse duplicate leads by lowercased email (idempotent)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_common import extract_workspace_id, require_current_workspace
from nexus.schemas_phase6 import DedupStatsOut

logger = logging.getLogger("pipelyt.nexus.dedup")

router = APIRouter(prefix="/nexus/dedup", tags=["nexus-dedup"])


@router.get("/stats", response_model=DedupStatsOut)
def dedup_stats(
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    workspace_id = extract_workspace_id(workspace) or 0

    total_global_leads = 0
    total_outreach = 0
    multi_pitched = 0

    try:
        total_global_leads = db.execute(
            text(
                """SELECT COUNT(*) FROM nexus_global_leads
                   WHERE workspace_id = :ws"""
            ),
            {"ws": workspace_id},
        ).scalar() or 0
    except Exception:
        db.rollback()

    try:
        total_outreach = db.execute(
            text(
                """SELECT COUNT(*) FROM nexus_outreach
                   WHERE workspace_id = :ws AND status <> 'pending'"""
            ),
            {"ws": workspace_id},
        ).scalar() or 0
    except Exception:
        db.rollback()

    try:
        multi_pitched = db.execute(
            text(
                """SELECT COUNT(*) FROM nexus_global_leads
                   WHERE workspace_id = :ws
                     AND jsonb_array_length(COALESCE(pitched_products, '[]'::jsonb)) > 1"""
            ),
            {"ws": workspace_id},
        ).scalar() or 0
    except Exception:
        db.rollback()

    return DedupStatsOut(
        total_global_leads=int(total_global_leads),
        total_outreach=int(total_outreach),
        multi_pitched_leads=int(multi_pitched),
    )


@router.post("/run")
def dedup_run(
    dry_run: bool = True,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Collapse duplicate GlobalLead rows that share lowercased email.

    Keeps the earliest row, deletes later duplicates. When dry_run is True (default),
    just returns the count of duplicates that would be removed.
    """
    workspace_id = extract_workspace_id(workspace) or 0
    try:
        dup_count_row = db.execute(
            text(
                """SELECT COUNT(*) FROM (
                       SELECT LOWER(email) AS em, COUNT(*) AS c
                       FROM nexus_global_leads
                       WHERE workspace_id = :ws AND email IS NOT NULL AND email <> ''
                       GROUP BY LOWER(email) HAVING COUNT(*) > 1
                   ) d"""
            ),
            {"ws": workspace_id},
        ).scalar() or 0
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=503, detail=f"dedup query failed: {e}") from e

    if dry_run:
        return {"ok": True, "dry_run": True, "duplicate_email_groups": int(dup_count_row)}

    try:
        deleted = db.execute(
            text(
                """WITH ranked AS (
                       SELECT id,
                              ROW_NUMBER() OVER (PARTITION BY LOWER(email) ORDER BY id ASC) AS rn
                       FROM nexus_global_leads
                       WHERE workspace_id = :ws AND email IS NOT NULL AND email <> ''
                   )
                   DELETE FROM nexus_global_leads
                   WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
                   RETURNING id"""
            ),
            {"ws": workspace_id},
        ).fetchall()
        db.commit()
        return {"ok": True, "dry_run": False, "deleted_rows": len(deleted)}
    except Exception as e:  # noqa: BLE001
        db.rollback()
        raise HTTPException(status_code=503, detail=f"dedup delete failed: {e}") from e
