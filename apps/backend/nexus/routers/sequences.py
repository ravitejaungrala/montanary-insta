"""CRUD + enrollment for Nexus sequences."""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.database import get_db
from models import User

from nexus.deps import (
    block_user_writes,
    get_nexus_user,
    require_current_workspace,
    trial_guard,
)
from nexus.models_phase4 import (
    NexusLeadSequence,
    NexusSequence,
    NexusSuppressionList,
)
from nexus.services.sequencer import compute_step_schedule
from nexus.schemas_phase4 import (
    EnrollRequest,
    EnrollResult,
    LeadSequenceOut,
    SequenceCreate,
    SequenceOut,
    SequenceUpdate,
)

router = APIRouter(
    prefix="/nexus/sequences",
    tags=["Nexus — Sequences"],
    dependencies=[Depends(block_user_writes)],  # read-only Users blocked from writes
)


@router.get("", response_model=List[SequenceOut])
def list_sequences(
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    return (
        db.query(NexusSequence)
        .filter(NexusSequence.workspace_id == workspace.id)
        .order_by(NexusSequence.id.desc())
        .all()
    )


@router.get("/{sequence_id}", response_model=SequenceOut)
def get_sequence(
    sequence_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    row = (
        db.query(NexusSequence)
        .filter(
            NexusSequence.id == sequence_id,
            NexusSequence.workspace_id == workspace.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Sequence not found")
    return row


@router.post("", response_model=SequenceOut, status_code=status.HTTP_201_CREATED)
def create_sequence(
    payload: SequenceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
    _trial=Depends(trial_guard),
):
    row = NexusSequence(
        workspace_id=workspace.id,
        campaign_id=payload.campaign_id,
        name=payload.name,
        steps=[s.model_dump() for s in payload.steps],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{sequence_id}", response_model=SequenceOut)
def update_sequence(
    sequence_id: int,
    payload: SequenceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    row = (
        db.query(NexusSequence)
        .filter(
            NexusSequence.id == sequence_id,
            NexusSequence.workspace_id == workspace.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Sequence not found")
    if payload.name is not None:
        row.name = payload.name
    if payload.campaign_id is not None:
        row.campaign_id = payload.campaign_id
    if payload.steps is not None:
        row.steps = [s.model_dump() for s in payload.steps]
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{sequence_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sequence(
    sequence_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    row = (
        db.query(NexusSequence)
        .filter(
            NexusSequence.id == sequence_id,
            NexusSequence.workspace_id == workspace.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Sequence not found")
    db.delete(row)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Enrollment / pause / resume
# ---------------------------------------------------------------------------


def _own_sequence_or_404(db: Session, sequence_id: int, workspace_id: int) -> NexusSequence:
    seq = (
        db.query(NexusSequence)
        .filter(
            NexusSequence.id == sequence_id,
            NexusSequence.workspace_id == workspace_id,
        )
        .first()
    )
    if not seq:
        raise HTTPException(status_code=404, detail="Sequence not found")
    return seq


def _lead_email(db: Session, lead_id: int) -> str | None:
    """Best-effort recipient lookup. Tolerates missing Phase 3 table."""
    try:
        row = db.execute(
            text("SELECT email FROM nexus_global_leads WHERE id = :id LIMIT 1"),
            {"id": int(lead_id)},
        ).first()
        return (row[0] if row and row[0] else None)
    except Exception:
        return None


@router.post(
    "/{sequence_id}/enroll",
    response_model=EnrollResult,
    status_code=status.HTTP_201_CREATED,
)
def enroll_leads(
    sequence_id: int,
    payload: EnrollRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
    _trial=Depends(trial_guard),
):
    """Enroll one or many leads into a sequence.

    - Accepts `lead_id` (single) or `lead_ids` (bulk).
    - Skips leads with an existing active enrollment in this sequence.
    - Skips leads whose email is in the workspace suppression list.
    - Returns counts so the UI can show "enrolled 23, skipped 4".
    """
    seq = _own_sequence_or_404(db, sequence_id, workspace.id)

    ids: List[int] = []
    if payload.lead_id is not None:
        ids.append(int(payload.lead_id))
    if payload.lead_ids:
        ids.extend(int(x) for x in payload.lead_ids)
    ids = list(dict.fromkeys(ids))  # dedup, preserve order
    if not ids:
        raise HTTPException(status_code=400, detail="lead_id or lead_ids required")

    # SECURITY: only enroll leads that are attached to the caller's workspace
    # (nexus_leads is the workspace junction; the global lead ids are shared).
    # Without this, a caller could enroll arbitrary leads from other tenants
    # into their own sequence and use it as a suppression/existence oracle.
    owned = {
        r[0]
        for r in db.execute(
            text(
                "SELECT global_lead_id FROM nexus_leads "
                "WHERE workspace_id = :ws AND global_lead_id = ANY(:ids)"
            ),
            {"ws": workspace.id, "ids": ids},
        ).fetchall()
    }
    ids = [i for i in ids if i in owned]
    if not ids:
        raise HTTPException(status_code=400, detail="No leads in this workspace to enroll")

    # Suppression set for this workspace
    suppressed = {
        s.email_lower
        for s in db.query(NexusSuppressionList)
        .filter(NexusSuppressionList.workspace_id == workspace.id)
        .all()
    }

    # Existing active enrollments for these leads in this sequence
    active_existing = {
        r[0]
        for r in db.query(NexusLeadSequence.lead_id)
        .filter(
            NexusLeadSequence.sequence_id == sequence_id,
            NexusLeadSequence.workspace_id == workspace.id,
            NexusLeadSequence.lead_id.in_(ids),
            NexusLeadSequence.status.in_(["active", "paused"]),
        )
        .all()
    }

    start_at = payload.start_at or datetime.utcnow()
    result = EnrollResult()

    for lid in ids:
        if lid in active_existing:
            result.skipped_already_active += 1
            continue
        email = _lead_email(db, lid)
        if email and email.lower() in suppressed:
            result.skipped_suppressed += 1
            continue
        ls = NexusLeadSequence(
            workspace_id=workspace.id,
            campaign_id=payload.campaign_id or seq.campaign_id,
            lead_id=lid,
            sequence_id=seq.id,
            current_step=0,
            next_action_at=start_at,
            status="active",
            # Fixed cadence decided once, at enrollment (email_flow_plan_3.md).
            step_schedule=compute_step_schedule(start_at, seq.steps or []),
        )
        # Per-lead SAVEPOINT so a uq_lead_seq conflict (lead already enrolled /
        # concurrent enroll race) skips just this lead instead of aborting the
        # whole batch.
        try:
            with db.begin_nested():
                db.add(ls)
                db.flush()
        except IntegrityError:
            result.skipped_already_active += 1
            continue
        result.lead_sequence_ids.append(ls.id)
        result.enrolled += 1

    db.commit()
    return result


@router.post("/lead-sequences/{lead_sequence_id}/pause", response_model=LeadSequenceOut)
def pause_lead_sequence(
    lead_sequence_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    ls = (
        db.query(NexusLeadSequence)
        .filter(
            NexusLeadSequence.id == lead_sequence_id,
            NexusLeadSequence.workspace_id == workspace.id,
        )
        .first()
    )
    if not ls:
        raise HTTPException(status_code=404, detail="LeadSequence not found")
    if ls.status not in ("active", "paused"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot pause from status='{ls.status}'",
        )
    ls.status = "paused"
    ls.locked_until = None
    db.commit()
    db.refresh(ls)
    return ls


@router.post("/lead-sequences/{lead_sequence_id}/resume", response_model=LeadSequenceOut)
def resume_lead_sequence(
    lead_sequence_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    ls = (
        db.query(NexusLeadSequence)
        .filter(
            NexusLeadSequence.id == lead_sequence_id,
            NexusLeadSequence.workspace_id == workspace.id,
        )
        .first()
    )
    if not ls:
        raise HTTPException(status_code=404, detail="LeadSequence not found")
    if ls.status != "paused":
        raise HTTPException(
            status_code=400,
            detail=f"Can only resume from paused (was '{ls.status}')",
        )
    ls.status = "active"
    if ls.next_action_at is None:
        ls.next_action_at = datetime.utcnow()
    db.commit()
    db.refresh(ls)
    return ls


@router.post("/defaults", response_model=SequenceOut, status_code=status.HTTP_201_CREATED)
def install_default_sequence(
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Idempotent: install the default 4-step sequence for the current workspace.

    Useful for workspaces created before the auto-install landed, or for
    operators who deleted it and want it back.
    """
    from nexus.routers.workspace import DEFAULT_SEQUENCE_NAME, DEFAULT_SEQUENCE_STEPS

    existing = (
        db.query(NexusSequence)
        .filter(
            NexusSequence.workspace_id == workspace.id,
            NexusSequence.name == DEFAULT_SEQUENCE_NAME,
        )
        .first()
    )
    if existing is not None:
        return existing

    row = NexusSequence(
        workspace_id=workspace.id,
        campaign_id=None,
        name=DEFAULT_SEQUENCE_NAME,
        steps=DEFAULT_SEQUENCE_STEPS,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
