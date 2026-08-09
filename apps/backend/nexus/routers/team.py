"""Team management router — invites, roles, member CRUD.

Legacy: apps/nexus-legacy/server/routes/team.js
Routes (legacy):
  GET    /api/team/members         — list workspace members
  POST   /api/team/invite          — send invite (email)
  POST   /api/team/invite/:token/accept — accept invite
  POST   /api/team/invite/:id/revoke    — revoke pending invite
  PATCH  /api/team/members/:id     — change role
  DELETE /api/team/members/:id     — remove member

Backed by nexus_workspace_invites (existing) + nexus_user_profiles.role.
"""
from __future__ import annotations
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_common import (
    extract_user_id,
    extract_workspace_id,
    get_nexus_user,
    require_current_workspace,
)

router = APIRouter(prefix="/nexus/team", tags=["nexus-team"])


@router.get("/members")
def list_members(
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    wid = extract_workspace_id(workspace)
    # Stub: return owner only.
    rows = db.execute(
        text(
            """SELECT u.id, u.email, COALESCE(p.role, 'admin') AS role
               FROM users u
               LEFT JOIN nexus_user_profiles p ON p.user_id = u.id
               WHERE u.id IN (SELECT owner_id FROM nexus_workspaces WHERE id = :wid)"""
        ),
        {"wid": wid},
    ).fetchall()
    return {
        "members": [
            {"_id": r[0], "email": r[1], "role": r[2]} for r in rows
        ]
    }


@router.post("/invite")
def send_invite(
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
    user: Any = Depends(get_nexus_user),
) -> Dict[str, Any]:
    """Legacy: POST /api/team/invite — { email, role }."""
    email = (body.get("email") or "").lower().strip()
    role = body.get("role") or "member"
    if not email:
        raise HTTPException(status_code=400, detail="email required")
    # Stub — write to nexus_workspace_invites
    return {"ok": False, "stub": True, "email": email, "role": role}


@router.post("/invite/{token}/accept")
def accept_invite(
    token: str,
    db: Session = Depends(get_db),
    user: Any = Depends(get_nexus_user),
) -> Dict[str, Any]:
    return {"ok": False, "stub": True, "token": token}


@router.post("/invite/{invite_id}/revoke")
def revoke_invite(
    invite_id: int,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    return {"ok": False, "stub": True, "invite_id": invite_id}


@router.patch("/members/{member_id}")
def update_member_role(
    member_id: int,
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    return {"ok": False, "stub": True, "member_id": member_id, "role": body.get("role")}


@router.delete("/members/{member_id}")
def remove_member(
    member_id: int,
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    return {"ok": False, "stub": True, "member_id": member_id}
