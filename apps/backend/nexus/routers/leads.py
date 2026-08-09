"""NEXUS Phase 3 — Lead discovery + CRUD endpoints.

Mounts under ``/nexus/leads``:

    POST /nexus/leads/autonomous   SSE stream of multi-source discovery
    POST /nexus/leads/accurate     synchronous single-source discovery
    POST /nexus/leads              bulk upsert (classic path)
    GET  /nexus/leads              list (workspace + campaign filter)
    GET  /nexus/leads/{id}         fetch one
    PATCH /nexus/leads/{id}        update
    DELETE /nexus/leads/{id}       delete

All endpoints require a Pipelyt JWT via :func:`nexus.deps.get_nexus_user` and
a current workspace via :func:`nexus.deps.require_current_workspace`.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from core.database import get_db
from models import User
from nexus.deps import (
    check_limit,
    get_nexus_user,
    increment_usage,
    require_current_workspace,
    trial_guard,
)
from nexus.models_phase3 import (
    NexusCampaign,
    NexusGlobalLead,
    NexusLead,
)
from nexus.schemas_phase3 import (
    AccurateDiscoveryIn,
    AutonomousDiscoveryIn,
    LeadBulkCreate,
    LeadCreate,
    LeadOut,
    LeadUpdate,
)
from nexus.services.lead_discovery import accurate_discover, autonomous_discover

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nexus/leads", tags=["Nexus — Leads"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _assert_campaign(
    db: Session, campaign_id: Optional[int], workspace_id: int
) -> None:
    if campaign_id is None:
        return
    row = (
        db.query(NexusCampaign)
        .filter(
            NexusCampaign.id == campaign_id,
            NexusCampaign.workspace_id == workspace_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found in workspace",
        )


def _upsert_global_from_payload(db: Session, payload) -> NexusGlobalLead:
    email = payload.email.lower()
    row = (
        db.query(NexusGlobalLead).filter(NexusGlobalLead.email == email).first()
    )
    if row is None:
        row = NexusGlobalLead(
            email=email,
            first_name=payload.first_name,
            last_name=payload.last_name,
            role=payload.role,
            company_domain=(payload.company_domain or "").lower() or None,
            company_name=payload.company_name,
            source=payload.source,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    else:
        for col in (
            "first_name",
            "last_name",
            "role",
            "company_domain",
            "company_name",
            "source",
        ):
            if not getattr(row, col) and getattr(payload, col, None):
                setattr(row, col, getattr(payload, col))
        db.commit()
        db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Discovery — autonomous (SSE)
# ---------------------------------------------------------------------------
@router.post(
    "/autonomous",
    dependencies=[
        Depends(trial_guard),
        Depends(check_limit("leads_per_month")),
    ],
)
async def autonomous(
    payload: AutonomousDiscoveryIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace = Depends(require_current_workspace),
):
    """Stream Server-Sent-Events as multi-source discovery progresses."""
    _assert_campaign(db, payload.campaign_id, workspace.id)

    async def _stream():
        try:
            async for chunk in autonomous_discover(
                db,
                workspace_id=workspace.id,
                icp=payload.icp.model_dump(exclude_none=True),
                campaign_id=payload.campaign_id,
                product_id=payload.product_id,
                target_domains=payload.target_domains,
                sources=payload.sources,
                max_leads=payload.max_leads,
                min_icp_score=payload.min_icp_score,
            ):
                yield chunk
        except Exception as exc:  # noqa: BLE001
            logger.exception("autonomous discovery crashed: %s", exc)
            yield f'data: {{"type":"error","message":"{exc}"}}\n\n'

    # Best-effort usage increment (the stream may emit zero leads — we still
    # charge the discovery attempt against the workspace quota).
    try:
        increment_usage(db, workspace, "leads_per_month", amount=1)
    except Exception:  # noqa: BLE001
        pass

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Discovery — accurate (synchronous)
# ---------------------------------------------------------------------------
@router.post(
    "/accurate",
    dependencies=[
        Depends(trial_guard),
        Depends(check_limit("leads_per_month")),
    ],
)
async def accurate(
    payload: AccurateDiscoveryIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace = Depends(require_current_workspace),
):
    _assert_campaign(db, payload.campaign_id, workspace.id)
    results = await accurate_discover(
        db,
        workspace_id=workspace.id,
        source=payload.source,
        icp=payload.icp.model_dump(exclude_none=True),
        campaign_id=payload.campaign_id,
        product_id=payload.product_id,
        target_domain=payload.target_domain,
        max_leads=payload.max_leads,
        min_icp_score=payload.min_icp_score,
    )
    try:
        increment_usage(db, workspace, "leads_per_month", amount=len(results))
    except Exception:  # noqa: BLE001
        pass
    return {"source": payload.source, "matched": len(results), "results": results}


# ---------------------------------------------------------------------------
# Classic CRUD — bulk upsert + list + detail + update + delete
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=List[LeadOut],
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(trial_guard),
        Depends(check_limit("leads_per_month")),
    ],
)
def bulk_upsert(
    payload: LeadBulkCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace = Depends(require_current_workspace),
):
    """Bulk upsert workspace-scoped leads. Existing rows are merged on
    ``(workspace_id, global_lead_id)`` — a lead can only be attached to
    one campaign at a time within a workspace, and re-enrolling moves
    the existing row to the new campaign."""
    _assert_campaign(db, payload.campaign_id, workspace.id)

    out: List[NexusLead] = []
    for item in payload.leads:
        # Resolve / create the GlobalLead.
        if item.global_lead_id:
            global_row = (
                db.query(NexusGlobalLead)
                .filter(NexusGlobalLead.id == item.global_lead_id)
                .first()
            )
            if not global_row:
                raise HTTPException(
                    status_code=404,
                    detail=f"Global lead {item.global_lead_id} not found",
                )
        else:
            global_row = _upsert_global_from_payload(db, item.global_lead)

        campaign_id = item.campaign_id or payload.campaign_id
        product_id = item.product_id or payload.product_id
        _assert_campaign(db, campaign_id, workspace.id)

        # One attachment per (workspace, global_lead) — latest wins.
        # An existing attachment under a different campaign is MOVED to
        # the new campaign so the lead can't end up enrolled in two
        # campaigns at once. See _attach_workspace_lead in
        # services/lead_discovery.py for the matching rule on the
        # discovery path, and scripts/dedupe_nexus_leads.py for the
        # cleanup of historical multi-campaign rows.
        existing = (
            db.query(NexusLead)
            .filter(
                NexusLead.workspace_id == workspace.id,
                NexusLead.global_lead_id == global_row.id,
            )
            .order_by(NexusLead.id.desc())
            .first()
        )
        if existing:
            if campaign_id is not None and existing.campaign_id != campaign_id:
                existing.campaign_id = campaign_id
            if product_id is not None and existing.product_id != product_id:
                existing.product_id = product_id
            existing.icp_score = max(existing.icp_score or 0, item.icp_score or 0)
            if item.signals:
                merged = dict(existing.signals or {})
                merged.update(item.signals)
                existing.signals = merged
            out.append(existing)
        else:
            row = NexusLead(
                workspace_id=workspace.id,
                campaign_id=campaign_id,
                product_id=product_id,
                global_lead_id=global_row.id,
                icp_score=item.icp_score,
                signals=item.signals or {},
            )
            db.add(row)
            out.append(row)

    db.commit()
    for r in out:
        db.refresh(r)

    try:
        increment_usage(db, workspace, "leads_per_month", amount=len(out))
    except Exception:  # noqa: BLE001
        pass
    return out


@router.get("", response_model=List[LeadOut])
def list_leads(
    campaign_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    min_score: Optional[int] = Query(None, ge=0, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace = Depends(require_current_workspace),
):
    q = db.query(NexusLead).filter(NexusLead.workspace_id == workspace.id)
    if campaign_id is not None:
        q = q.filter(NexusLead.campaign_id == campaign_id)
    if min_score is not None:
        q = q.filter(NexusLead.icp_score >= min_score)
    return (
        q.order_by(NexusLead.icp_score.desc(), NexusLead.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.get("/{lead_id}", response_model=LeadOut)
def get_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace = Depends(require_current_workspace),
):
    row = (
        db.query(NexusLead)
        .filter(
            NexusLead.id == lead_id, NexusLead.workspace_id == workspace.id
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")
    return row


@router.patch("/{lead_id}", response_model=LeadOut)
def update_lead(
    lead_id: int,
    payload: LeadUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace = Depends(require_current_workspace),
):
    row = (
        db.query(NexusLead)
        .filter(
            NexusLead.id == lead_id, NexusLead.workspace_id == workspace.id
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")

    if payload.campaign_id is not None:
        _assert_campaign(db, payload.campaign_id, workspace.id)
        row.campaign_id = payload.campaign_id
    if payload.icp_score is not None:
        row.icp_score = payload.icp_score
    if payload.signals is not None:
        row.signals = payload.signals
    if payload.status is not None:
        # Status lives on the GlobalLead — propagate.
        if row.global_lead:
            row.global_lead.status = payload.status
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace = Depends(require_current_workspace),
):
    row = (
        db.query(NexusLead)
        .filter(
            NexusLead.id == lead_id, NexusLead.workspace_id == workspace.id
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")
    db.delete(row)
    db.commit()
    return None


_ALLOWED_PRIORITY = {"active", "low_priority", "hidden"}


@router.patch("/{lead_id}/priority")
def set_lead_priority(
    lead_id: int,
    priority: str = Query(..., description="active | low_priority | hidden"),
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace = Depends(require_current_workspace),
):
    """Update the GTM-Journey priority flag for a lead. Workspace-scoped:
    the caller can only flip priority on a global lead they actually own
    via a NexusLead attachment."""
    if priority not in _ALLOWED_PRIORITY:
        raise HTTPException(
            status_code=400,
            detail=f"priority must be one of {sorted(_ALLOWED_PRIORITY)}",
        )

    row = (
        db.query(NexusLead)
        .filter(
            NexusLead.id == lead_id, NexusLead.workspace_id == workspace.id
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")
    if row.global_lead:
        row.global_lead.priority = priority
    db.commit()
    return {"ok": True, "lead_id": lead_id, "priority": priority}


__all__ = ["router"]
