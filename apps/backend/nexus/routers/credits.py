"""Phase 6 credits router.

Endpoint:
  GET /nexus/credits/usage — last 30 days credit consumption for the workspace
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_common import extract_workspace_id, require_current_workspace
from nexus.models_phase6 import CreditLog
from nexus.schemas_phase6 import CreditBalanceOut, CreditUsageBreakdown, CreditUsageOut
from nexus.services import credits_service

logger = logging.getLogger("pipelyt.nexus.credits")

router = APIRouter(prefix="/nexus/credits", tags=["nexus-credits"])


@router.get("/usage", response_model=CreditUsageOut)
def credit_usage(
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    workspace_id = extract_workspace_id(workspace) or 0
    since = datetime.utcnow() - timedelta(days=30)

    rows = (
        db.query(CreditLog)
        .filter(CreditLog.workspace_id == workspace_id, CreditLog.created_at >= since)
        .all()
    )

    breakdown = CreditUsageBreakdown()
    total_credits = 0
    for r in rows:
        units = int(r.successful_matches or r.credits_used or 0)
        total_credits += units
        if r.action_type == "people_match":
            breakdown.people_enrichment += units
        elif r.action_type in ("bulk_people_match", "email_reveal"):
            breakdown.bulk_enrichment += units
        elif r.action_type == "voice_call":
            breakdown.voice_call += units
        elif r.action_type == "ai_email":
            breakdown.ai_email += units

    return CreditUsageOut(
        period="last_30_days",
        total_credits_consumed=total_credits,
        total_api_calls=len(rows),
        breakdown=breakdown,
        leads_with_email=total_credits,
        workspace_id=str(workspace_id),
    )


@router.get("/balance", response_model=CreditBalanceOut)
def credit_balance(
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Live credit balance for the current workspace's funding user (plan
    owner). Drives the credits badge, the Buy-Credits / out-of-credits states,
    and the low-credit login popup."""
    workspace_id = extract_workspace_id(workspace) or 0
    user_id = credits_service.resolve_credit_user_id(db, workspace_id)
    if not user_id:
        return CreditBalanceOut()
    return CreditBalanceOut(**credits_service.get_balance(db, user_id))


@router.get("/campaign/{campaign_id}")
def credit_campaign_usage(
    campaign_id: int,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
):
    """Credits consumed by one campaign, surfaced by its PER-PRODUCT number
    (#1/#2/#3) — the internal global id is never shown to users. Reads the
    running total off the campaign row (kept in lockstep by consume())."""
    workspace_id = extract_workspace_id(workspace) or 0
    row = db.execute(
        text(
            "SELECT credits_consumed, product_campaign_number "
            "FROM nexus_campaigns WHERE id = :c AND workspace_id = :w"
        ),
        {"c": campaign_id, "w": workspace_id},
    ).mappings().first()
    if not row:
        return {"campaign_number": None, "credits_consumed": 0}
    return {
        "campaign_number": row["product_campaign_number"],  # per-product #, what the UI shows
        "credits_consumed": int(row["credits_consumed"] or 0),
    }
