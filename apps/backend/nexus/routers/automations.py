"""CRUD for Nexus automations (recurring discovery jobs)."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.database import get_db
from models import User

from nexus.deps import (
    get_nexus_user,
    require_current_workspace,
    trial_guard,
)
from nexus.models_phase4 import NexusAutomation
from nexus.schemas_phase4 import (
    AutomationCreate,
    AutomationOut,
    AutomationUpdate,
)
from nexus.services.automation_runner import compute_next_run_at

router = APIRouter(prefix="/nexus/automations", tags=["Nexus — Automations"])


@router.get("", response_model=List[AutomationOut])
def list_automations(
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    return (
        db.query(NexusAutomation)
        .filter(NexusAutomation.workspace_id == workspace.id)
        .order_by(NexusAutomation.id.desc())
        .all()
    )


@router.get("/{automation_id}", response_model=AutomationOut)
def get_automation(
    automation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    row = (
        db.query(NexusAutomation)
        .filter(
            NexusAutomation.id == automation_id,
            NexusAutomation.workspace_id == workspace.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Automation not found")
    return row


@router.post("", response_model=AutomationOut, status_code=status.HTTP_201_CREATED)
def create_automation(
    payload: AutomationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
    _trial=Depends(trial_guard),
):
    next_run_at = payload.next_run_at
    if next_run_at is None:
        # Default daily automations to fire ~1 minute from now so the
        # next scheduler tick picks them up.
        next_run_at = compute_next_run_at(
            last_run=None,
            schedule_type=payload.schedule_type,
            tz=payload.tz,
        )
    row = NexusAutomation(
        workspace_id=workspace.id,
        campaign_id=payload.campaign_id,
        name=payload.name,
        schedule_type=payload.schedule_type,
        tz=payload.tz,
        target_leads=payload.target_leads,
        next_run_at=next_run_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{automation_id}", response_model=AutomationOut)
def update_automation(
    automation_id: int,
    payload: AutomationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    row = (
        db.query(NexusAutomation)
        .filter(
            NexusAutomation.id == automation_id,
            NexusAutomation.workspace_id == workspace.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Automation not found")
    for field in (
        "name",
        "campaign_id",
        "schedule_type",
        "tz",
        "target_leads",
        "next_run_at",
        "status",
    ):
        val = getattr(payload, field)
        if val is not None:
            setattr(row, field, val)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_automation(
    automation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    row = (
        db.query(NexusAutomation)
        .filter(
            NexusAutomation.id == automation_id,
            NexusAutomation.workspace_id == workspace.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Automation not found")
    db.delete(row)
    db.commit()
    return None


@router.post("/{automation_id}/run-now", response_model=AutomationOut)
def run_now(
    automation_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Force the automation to fire on the next sequencer tick.

    Pushes next_run_at to "now", flips status to active if paused. The actual
    discovery work happens on the scheduler tick — this endpoint just nudges
    it, so it's cheap and Lambda-safe.
    """
    row = (
        db.query(NexusAutomation)
        .filter(
            NexusAutomation.id == automation_id,
            NexusAutomation.workspace_id == workspace.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Automation not found")
    from datetime import datetime, timezone
    row.status = "active"
    row.next_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(row)
    return row
