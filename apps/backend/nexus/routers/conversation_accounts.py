"""POST /nexus/conversation-accounts/setup
GET  /nexus/conversation-accounts/

Minimal wizard for the sender-email account that powers outbound emails.

Why it exists: the sequencer in services/sequencer.py refuses to send if
no NexusConversationAccount row is configured for the workspace. There
was no API path to create one — so first-time Nexus users would launch
a campaign, leads would enroll, and the sequencer would silently punt
every tick with "no sending account with headroom". This router lets
the user (or an admin script) configure it.

A separate lazy-bootstrap path in `_pick_conversation_account` covers
the env-var case (`NEXUS_DEFAULT_FROM_EMAIL`). This endpoint covers the
"user wants to choose their own from-address per workspace" case.

No schema change — uses the existing nexus_conversation_accounts table.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from models import User
from nexus.deps import (  # type: ignore[import-not-found]
    get_nexus_user,
    require_current_workspace,
)
from nexus.models_phase4 import NexusConversationAccount

logger = logging.getLogger("pipelyt.nexus.conversation_accounts")

router = APIRouter(
    prefix="/nexus/conversation-accounts",
    tags=["Nexus — Conversation Accounts"],
)


def _serialize(row: NexusConversationAccount) -> Dict[str, Any]:
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "email_address": row.email_address,
        "daily_send_limit": row.daily_send_limit,
        "daily_send_count": row.daily_send_count,
        "provider": row.provider,
        "status": row.status,
    }


@router.get("")
def list_accounts(
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
) -> Dict[str, Any]:
    rows: List[NexusConversationAccount] = (
        db.query(NexusConversationAccount)
        .filter(NexusConversationAccount.workspace_id == workspace.id)
        .order_by(NexusConversationAccount.id.asc())
        .all()
    )
    return {"accounts": [_serialize(r) for r in rows]}


@router.post("/setup")
def setup_account(
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Create or update the workspace's sending account.

    Body:
      email_address     str (required) — verified Resend sender, e.g. "hello@acme.com"
      daily_send_limit  int (optional, default 20)
      provider          str (optional, default "resend")
      status            str (optional, default "active")

    Idempotent: a second call with the same email_address bumps the
    limit / re-activates the row. The unique constraint on email_address
    is workspace-agnostic, so the same address can't power two
    workspaces (acceptable for v1 — Resend domains are per-sender).
    """
    email = str(body.get("email_address") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email_address is required.")

    try:
        daily_limit = int(body.get("daily_send_limit") or 20)
    except (TypeError, ValueError):
        daily_limit = 50
    daily_limit = max(1, min(daily_limit, 2000))  # sanity cap

    provider = str(body.get("provider") or "resend").strip().lower() or "resend"
    status = str(body.get("status") or "active").strip().lower() or "active"

    existing: Optional[NexusConversationAccount] = (
        db.query(NexusConversationAccount)
        .filter(NexusConversationAccount.email_address == email)
        .first()
    )

    if existing is not None:
        # Refuse to silently steal an account from another workspace.
        if existing.workspace_id and existing.workspace_id != workspace.id:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Email {email} is already configured for another workspace."
                ),
            )
        existing.workspace_id = workspace.id
        existing.daily_send_limit = daily_limit
        existing.provider = provider
        existing.status = status
        db.commit()
        db.refresh(existing)
        logger.info(
            "conversation_accounts: updated existing ws=%s email=%s limit=%s",
            workspace.id,
            email,
            daily_limit,
        )
        return {"account": _serialize(existing), "created": False}

    row = NexusConversationAccount(
        workspace_id=workspace.id,
        email_address=email,
        daily_send_limit=daily_limit,
        daily_send_count=0,
        provider=provider,
        status=status,
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        logger.exception("conversation_accounts: insert failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Insert failed: {exc}") from exc

    logger.info(
        "conversation_accounts: created ws=%s email=%s limit=%s",
        workspace.id,
        email,
        daily_limit,
    )
    return {"account": _serialize(row), "created": True}
