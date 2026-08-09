"""Per-workspace email suppression list."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.database import get_db
from models import User

from nexus.deps import get_nexus_user, require_current_workspace
from nexus.models_phase4 import NexusSuppressionList
from nexus.schemas_phase4 import SuppressionAdd, SuppressionOut

router = APIRouter(prefix="/nexus/suppression", tags=["Nexus — Suppression"])


@router.get("", response_model=List[SuppressionOut])
def list_suppression(
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    return (
        db.query(NexusSuppressionList)
        .filter(NexusSuppressionList.workspace_id == workspace.id)
        .order_by(NexusSuppressionList.id.desc())
        .all()
    )


@router.post("", response_model=SuppressionOut, status_code=status.HTTP_201_CREATED)
def add_suppression(
    payload: SuppressionAdd,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    email_lower = payload.email.lower()
    existing = (
        db.query(NexusSuppressionList)
        .filter(
            NexusSuppressionList.workspace_id == workspace.id,
            NexusSuppressionList.email_lower == email_lower,
        )
        .first()
    )
    if existing:
        # Idempotent — return existing row.
        return existing

    row = NexusSuppressionList(
        workspace_id=workspace.id,
        email_lower=email_lower,
        reason=payload.reason,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Race: another concurrent insert won — fetch and return.
        existing = (
            db.query(NexusSuppressionList)
            .filter(
                NexusSuppressionList.workspace_id == workspace.id,
                NexusSuppressionList.email_lower == email_lower,
            )
            .first()
        )
        if existing:
            return existing
        raise HTTPException(status_code=409, detail="Suppression conflict")
    db.refresh(row)
    return row


@router.delete("/{suppression_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_suppression_by_id(
    suppression_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    row = (
        db.query(NexusSuppressionList)
        .filter(
            NexusSuppressionList.id == suppression_id,
            NexusSuppressionList.workspace_id == workspace.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(row)
    db.commit()
    return None


@router.delete("/by-email/{email}", status_code=status.HTTP_204_NO_CONTENT)
def delete_suppression_by_email(
    email: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    row = (
        db.query(NexusSuppressionList)
        .filter(
            NexusSuppressionList.workspace_id == workspace.id,
            NexusSuppressionList.email_lower == email.lower(),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(row)
    db.commit()
    return None
