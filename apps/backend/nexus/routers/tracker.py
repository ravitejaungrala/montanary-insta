"""Phase 6 tracker router — campaign tracker dashboard.

Endpoint:
  GET /nexus/tracker — campaign-level rolling progress for the workspace
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_common import extract_workspace_id, require_current_workspace
from nexus.schemas_phase6 import TrackerOut, TrackerRow

logger = logging.getLogger("pipelyt.nexus.tracker")

router = APIRouter(prefix="/nexus/tracker", tags=["nexus-tracker"])


@router.get("", response_model=TrackerOut)
def tracker(
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    workspace_id = extract_workspace_id(workspace) or 0

    campaigns: List[Dict[str, Any]] = []
    try:
        rows = db.execute(
            text(
                """SELECT c.id AS cid,
                          COALESCE(p.name, 'Unnamed') AS product_name
                   FROM nexus_campaigns c
                   LEFT JOIN nexus_products p ON p.id = c.product_id
                   WHERE c.workspace_id = :ws
                     AND EXISTS (
                         SELECT 1 FROM nexus_leads nl
                          WHERE nl.campaign_id = c.id
                     )"""
            ),
            {"ws": workspace_id},
        ).fetchall()
        for r in rows:
            campaigns.append({"campaign_id": r[0], "product_name": r[1]})
    except Exception:
        db.rollback()

    out: List[TrackerRow] = []
    for c in campaigns:
        cid = c["campaign_id"]
        try:
            sent = db.execute(
                text(
                    """SELECT COUNT(*) FROM nexus_outreach
                       WHERE campaign_id = :cid
                         AND status IN ('sent','opened','clicked','replied','demo_scheduled')"""
                ),
                {"cid": cid},
            ).scalar() or 0
            opened = db.execute(
                text(
                    """SELECT COUNT(*) FROM nexus_outreach
                       WHERE campaign_id = :cid
                         AND status IN ('opened','clicked','replied')"""
                ),
                {"cid": cid},
            ).scalar() or 0
            replied = db.execute(
                text(
                    """SELECT COUNT(*) FROM nexus_outreach
                       WHERE campaign_id = :cid AND status='replied'"""
                ),
                {"cid": cid},
            ).scalar() or 0
            booked = db.execute(
                text("SELECT COUNT(*) FROM nexus_demo_bookings WHERE campaign_id = :cid"),
                {"cid": cid},
            ).scalar() or 0
            last_activity = db.execute(
                text(
                    """SELECT MAX(GREATEST(
                          COALESCE(email_sent_at, '1970-01-01'::timestamp),
                          COALESCE(opened_at, '1970-01-01'::timestamp),
                          COALESCE(clicked_at, '1970-01-01'::timestamp),
                          COALESCE(updated_at, '1970-01-01'::timestamp)
                       )) FROM nexus_outreach WHERE campaign_id = :cid"""
                ),
                {"cid": cid},
            ).scalar()
        except Exception:
            db.rollback()
            sent = opened = replied = booked = 0
            last_activity = None

        out.append(
            TrackerRow(
                campaign_id=cid,
                product_name=c["product_name"],
                sent=int(sent),
                opened=int(opened),
                replied=int(replied),
                booked=int(booked),
                last_activity=last_activity,
            )
        )

    return TrackerOut(campaigns=out)
