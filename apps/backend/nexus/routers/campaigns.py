"""NEXUS Phase 3 — Campaign CRUD endpoints.

Mounts under ``/nexus/campaigns``. Campaigns are workspace-scoped; the JWT
identifies the user and ``require_current_workspace`` resolves the active
workspace from the request.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from models import User
from nexus.deps import (
    check_limit,
    get_nexus_user,
    require_current_workspace,
    require_nexus_role,
    trial_guard,
)
from nexus.models_phase3 import NexusCampaign
from nexus.schemas_phase3 import CampaignCreate, CampaignOut, CampaignUpdate

router = APIRouter(prefix="/nexus/campaigns", tags=["Nexus — Campaigns"])


# ─── Apollo-queued / sent email visibility ────────────────────────────────────


@router.get("/{campaign_id}/outbound-emails")
def list_campaign_outbound_emails(
    campaign_id: int,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Return the Gemini-generated outbound email content per lead for this
    campaign — both Apollo-routed (`channel='email_apollo'`) AND Resend-routed
    (`channel='email'`). Lets the operator see "what was queued / sent" with
    full subject + body for each lead, as proof of work after launching a
    campaign.

    Status interpretation:
      - `sent` (channel email_apollo): pushed to Apollo, Apollo will send
        according to its schedule. From the operator's POV: "queued"
        because Apollo controls when it actually leaves the mailbox.
      - `sent` (channel email): Resend has sent. Already delivered.
      - `failed`: enrollment / send failed. `error` field tells you why.

    Read-only — doesn't write or modify any data structure. Pure JOIN view
    over `nexus_touchpoints + nexus_global_leads + nexus_products`.
    """
    # Verify the campaign belongs to the active workspace before exposing
    # any touchpoint data.
    own = (
        db.query(NexusCampaign)
        .filter(
            NexusCampaign.id == campaign_id,
            NexusCampaign.workspace_id == workspace.id,
        )
        .first()
    )
    if not own:
        raise HTTPException(status_code=404, detail="campaign not found")

    # Resolve the run fence server-side from nexus_campaign_launches.
    # /analyze inserts one row per launch (see routers/analyze.py); the
    # MAX(launched_at) for this campaign is the start instant of THIS
    # run. Falling back to NULL when no row exists keeps the very-first-
    # run case working (the c.created_at fence below covers it) and
    # also degrades gracefully on stale envs where the migration hasn't
    # applied yet — same as pre-fix behavior.
    launched_at_row = db.execute(
        text(
            "SELECT MAX(launched_at) FROM nexus_campaign_launches "
            "WHERE campaign_id = :cid"
        ),
        {"cid": campaign_id},
    ).first()
    launched_at = launched_at_row[0] if launched_at_row else None

    rows = db.execute(
        text(
            """SELECT
                  t.id AS touchpoint_id,
                  t.lead_id,
                  t.step,
                  t.channel,
                  t.subject,
                  t.body,
                  t.status,
                  t.error,
                  t.sent_at,
                  gl.email AS lead_email,
                  COALESCE(NULLIF(gl.first_name, ''), '') AS first_name,
                  COALESCE(NULLIF(gl.last_name, ''), '') AS last_name,
                  COALESCE(NULLIF(gl.company_name, ''), '') AS company_name,
                  p.name AS product_name,
                  CASE
                    WHEN t.channel = 'email_apollo' AND t.status = 'queued' THEN 'pregenerated'
                    WHEN t.channel = 'email_apollo' AND t.status = 'sent' THEN 'queued_to_apollo'
                    WHEN t.channel = 'email_apollo' AND t.status = 'failed' THEN 'failed_to_queue'
                    WHEN t.channel = 'email' AND t.status = 'sent' THEN 'sent_via_resend'
                    WHEN t.channel = 'email' AND t.status = 'failed' THEN 'failed_send'
                    ELSE t.status
                  END AS display_status
                FROM nexus_touchpoints t
                LEFT JOIN nexus_global_leads gl ON gl.id = t.lead_id
                LEFT JOIN nexus_campaigns c ON c.id = t.campaign_id
                LEFT JOIN nexus_products p ON p.id = c.product_id
               WHERE t.campaign_id = :cid
                 AND t.channel IN ('email', 'email_apollo')
                 -- TIME FENCE: only return touchpoints created since the
                 -- LAUNCH that produced them. Two layers:
                 --   1. c.created_at — first-run / fallback fence (used
                 --      when no launch row exists yet for this campaign).
                 --   2. :launched_at — MAX(launched_at) from
                 --      nexus_campaign_launches, set by /analyze on
                 --      every call. Required because /analyze REUSES
                 --      the campaign row for a product (so
                 --      campaign.created_at is pinned to the first run
                 --      ever — days/weeks old) and without this fence
                 --      the preview leaks every touchpoint generated by
                 --      any prior run of the same campaign.
                 AND t.created_at >= c.created_at
                 AND (:launched_at IS NULL OR t.created_at >= :launched_at)
               ORDER BY t.sent_at DESC NULLS LAST, t.id DESC
               LIMIT :n
            """
        ),
        {"cid": campaign_id, "n": limit, "launched_at": launched_at},
    ).mappings().fetchall()

    return {
        "campaign_id": campaign_id,
        "count": len(rows),
        "emails": [dict(r) for r in rows],
    }


@router.post(
    "",
    response_model=CampaignOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_nexus_role("admin")),  # Master Admin + Admin (not User)
        Depends(trial_guard),
        Depends(check_limit("campaigns")),
    ],
)
def create_campaign(
    payload: CampaignCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace = Depends(require_current_workspace),
):
    row = NexusCampaign(
        workspace_id=workspace.id,
        product_id=payload.product_id,
        name=payload.name,
        icp=(payload.icp.model_dump(exclude_none=True) if payload.icp else None),
        status=payload.status,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("", response_model=List[CampaignOut])
def list_campaigns(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace = Depends(require_current_workspace),
):
    # Global rule: hide campaigns with zero enrolled leads. They are
    # transient / orphan rows from re-runs and must not surface in any
    # listing, count, or analytics scope. EXISTS subquery keeps the
    # filter cheap (one index seek per row, no GROUP BY).
    # Also skip archived (soft-deleted duplicate campaigns).
    q = (
        db.query(NexusCampaign)
        .filter(NexusCampaign.workspace_id == workspace.id)
        .filter(NexusCampaign.status != "archived")
        .filter(
            text(
                "EXISTS (SELECT 1 FROM nexus_leads nl "
                "WHERE nl.campaign_id = nexus_campaigns.id)"
            )
        )
    )
    if status_filter:
        q = q.filter(NexusCampaign.status == status_filter)
    return (
        q.order_by(NexusCampaign.created_at.desc()).offset(offset).limit(limit).all()
    )


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace = Depends(require_current_workspace),
):
    row = (
        db.query(NexusCampaign)
        .filter(
            NexusCampaign.id == campaign_id,
            NexusCampaign.workspace_id == workspace.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return row


@router.patch(
    "/{campaign_id}",
    response_model=CampaignOut,
    dependencies=[Depends(require_nexus_role("admin"))],  # not User
)
def update_campaign(
    campaign_id: int,
    payload: CampaignUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace = Depends(require_current_workspace),
):
    row = (
        db.query(NexusCampaign)
        .filter(
            NexusCampaign.id == campaign_id,
            NexusCampaign.workspace_id == workspace.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if payload.name is not None:
        row.name = payload.name
    if payload.product_id is not None:
        row.product_id = payload.product_id
    if payload.icp is not None:
        row.icp = payload.icp.model_dump(exclude_none=True)
    if payload.status is not None:
        row.status = payload.status
    db.commit()
    db.refresh(row)
    return row


@router.delete(
    "/{campaign_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_nexus_role("admin"))],  # not User
)
def delete_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace = Depends(require_current_workspace),
):
    row = (
        db.query(NexusCampaign)
        .filter(
            NexusCampaign.id == campaign_id,
            NexusCampaign.workspace_id == workspace.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Campaign not found")
    db.delete(row)
    db.commit()
    return None


@router.get("/{campaign_id}/metrics")
def campaign_metrics(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace = Depends(require_current_workspace),
):
    """Aggregate per-campaign performance.

    Returns: leads_enrolled, sent, opened, clicked, replied, demos_booked,
    plus the derived open/click/reply rates. Each lookup is best-effort —
    a missing cross-phase table (e.g. demos in dev) zeroes the metric
    rather than failing the request.
    """
    row = (
        db.query(NexusCampaign)
        .filter(
            NexusCampaign.id == campaign_id,
            NexusCampaign.workspace_id == workspace.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Campaign not found")

    def _scalar(sql: str, params: dict) -> int:
        try:
            r = db.execute(text(sql), params).first()
            return int(r[0] or 0) if r else 0
        except Exception:
            return 0

    leads_enrolled = _scalar(
        "SELECT COUNT(DISTINCT lead_id) FROM nexus_lead_sequences "
        "WHERE campaign_id = :c AND lead_id IS NOT NULL",
        {"c": campaign_id},
    )
    sent = _scalar(
        "SELECT COUNT(1) FROM nexus_touchpoints t "
        "JOIN nexus_lead_sequences ls ON ls.id = t.lead_sequence_id "
        "WHERE ls.campaign_id = :c AND t.status IN "
        "      ('sent','opened','clicked','replied','bounced')",
        {"c": campaign_id},
    )
    opened = _scalar(
        "SELECT COUNT(1) FROM nexus_touchpoints t "
        "JOIN nexus_lead_sequences ls ON ls.id = t.lead_sequence_id "
        "WHERE ls.campaign_id = :c AND COALESCE(t.opens_count, 0) > 0",
        {"c": campaign_id},
    )
    clicked = _scalar(
        "SELECT COUNT(1) FROM nexus_touchpoints t "
        "JOIN nexus_lead_sequences ls ON ls.id = t.lead_sequence_id "
        "WHERE ls.campaign_id = :c AND COALESCE(t.clicks_count, 0) > 0",
        {"c": campaign_id},
    )
    replied = _scalar(
        "SELECT COUNT(DISTINCT lead_id) FROM nexus_lead_sequences "
        "WHERE campaign_id = :c AND status = 'replied'",
        {"c": campaign_id},
    )
    bounced = _scalar(
        "SELECT COUNT(DISTINCT lead_id) FROM nexus_lead_sequences "
        "WHERE campaign_id = :c AND halt_reason = 'bounce'",
        {"c": campaign_id},
    )
    demos = _scalar(
        "SELECT COUNT(1) FROM nexus_demo_bookings db "
        "JOIN nexus_global_leads gl ON LOWER(gl.email) = LOWER(db.attendee_email) "
        "JOIN nexus_lead_sequences ls ON ls.lead_id = gl.id "
        "WHERE ls.campaign_id = :c",
        {"c": campaign_id},
    )

    def _rate(num: int, denom: int) -> float:
        return round((num / denom) * 100.0, 2) if denom else 0.0

    return {
        "campaign_id": campaign_id,
        "status": row.status,
        "leads_enrolled": leads_enrolled,
        "sent": sent,
        "opened": opened,
        "clicked": clicked,
        "replied": replied,
        "bounced": bounced,
        "demos_booked": demos,
        "open_rate": _rate(opened, sent),
        "click_rate": _rate(clicked, sent),
        "reply_rate": _rate(replied, sent),
        "bounce_rate": _rate(bounced, sent),
    }


__all__ = ["router"]
