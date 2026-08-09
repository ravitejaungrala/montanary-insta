"""
Conversations browse endpoints — workspace-scoped thread index, search, and
unmatched-reply triage.

  GET  /nexus/conversations                          list all threads (any state)
  GET  /nexus/conversations/unmatched                triage queue
  POST /nexus/conversations/unmatched/{id}/discard   mark unmatched processed
  POST /nexus/conversations/{thread_id}/close        close thread
  POST /nexus/conversations/{thread_id}/reopen       reopen thread
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from core.database import get_db
from nexus.deps import (  # type: ignore  # provided by Phase 1
    get_nexus_user,
    require_current_workspace,
    trial_guard,
)
from nexus.models_phase5 import InboundThread, UnmatchedReply
from nexus.schemas_phase5 import UnmatchedReplyOut

logger = logging.getLogger("nexus.routers.conversations")

router = APIRouter(
    prefix="/nexus/conversations",
    tags=["nexus-conversations"],
    dependencies=[Depends(get_nexus_user), Depends(trial_guard)],
)


def _preview(s: Optional[str], n: int = 160) -> Optional[str]:
    if not s:
        return None
    cleaned = " ".join(s.split())
    return cleaned[:n] + ("..." if len(cleaned) > n else "")


@router.get("")
def list_conversations(
    db: Session = Depends(get_db),
    workspace=Depends(require_current_workspace),
    status: Optional[str] = Query(None, pattern="^(open|closed)$"),
    q: Optional[str] = Query(None, description="Search subject"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    ws_id = getattr(workspace, "id", None) or workspace
    base = db.query(InboundThread).filter(InboundThread.workspace_id == ws_id)
    if status:
        base = base.filter(InboundThread.status == status)
    if q:
        like = f"%{q}%"
        base = base.filter(or_(InboundThread.subject.ilike(like)))

    total = base.count()
    rows = (
        base.order_by(desc(InboundThread.last_message_at))
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": t.id,
                "subject": t.subject,
                "status": t.status,
                "message_count": t.message_count,
                "last_message_at": t.last_message_at,
                "lead_id": t.lead_id,
                "lead_sequence_id": t.lead_sequence_id,
                "gmail_thread_id": t.gmail_thread_id,
                "resend_thread_id": t.resend_thread_id,
            }
            for t in rows
        ],
    }


@router.get("/unmatched", response_model=List[UnmatchedReplyOut])
def list_unmatched(
    db: Session = Depends(get_db),
    workspace=Depends(require_current_workspace),
    include_processed: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
):
    ws_id = getattr(workspace, "id", None) or workspace
    # SECURITY: only surface THIS workspace's unmatched replies. Previously
    # NULL-workspace (unattributable) replies were shared to EVERY tenant,
    # leaking their from_email/subject/body across workspaces.
    q = db.query(UnmatchedReply).filter(UnmatchedReply.workspace_id == ws_id)
    if not include_processed:
        q = q.filter(UnmatchedReply.processed.is_(False))
    rows = q.order_by(desc(UnmatchedReply.received_at)).limit(limit).all()
    return [
        UnmatchedReplyOut(
            id=r.id,
            workspace_id=r.workspace_id,
            from_email=r.from_email,
            subject=r.subject,
            body_preview=_preview(r.body_text),
            received_at=r.received_at,
            processed=bool(r.processed),
        )
        for r in rows
    ]


@router.post("/unmatched/{unmatched_id}/discard")
def discard_unmatched(
    unmatched_id: int,
    db: Session = Depends(get_db),
    workspace=Depends(require_current_workspace),
):
    ws_id = getattr(workspace, "id", None) or workspace
    row = db.query(UnmatchedReply).filter(UnmatchedReply.id == unmatched_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Unmatched reply not found")
    # SECURITY: only the owning workspace may discard its unmatched reply
    # (NULL-workspace orphans are no longer surfaced to any tenant).
    if row.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="Unmatched reply not found")
    row.processed = True
    db.commit()
    return {"ok": True, "id": unmatched_id, "processed": True}


def _set_thread_status(
    thread_id: int,
    new_status: str,
    db: Session,
    workspace,
):
    ws_id = getattr(workspace, "id", None) or workspace
    thread = db.query(InboundThread).filter(InboundThread.id == thread_id).first()
    if not thread or thread.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="Thread not found")
    thread.status = new_status
    db.commit()
    return {"ok": True, "id": thread_id, "status": new_status}


@router.post("/{thread_id}/close")
def close_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    workspace=Depends(require_current_workspace),
):
    return _set_thread_status(thread_id, "closed", db, workspace)


@router.post("/{thread_id}/reopen")
def reopen_thread(
    thread_id: int,
    db: Session = Depends(get_db),
    workspace=Depends(require_current_workspace),
):
    return _set_thread_status(thread_id, "open", db, workspace)


__all__ = ["router"]
