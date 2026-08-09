"""Public microsite router — public lead-capture form per workspace.

Legacy: apps/nexus-legacy/server/routes/microsite.js
Routes (legacy):
  GET  /microsite/:slug              — public landing page metadata
  POST /microsite/:slug/leads        — public form submit (no auth)
  GET  /api/microsite/config         — workspace's microsite config
  PATCH /api/microsite/config        — update config

Public endpoints (no auth) are intentionally separate from authenticated
ones; both live under /nexus/microsite/* for routing consistency.
"""
from __future__ import annotations
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_common import (
    extract_workspace_id,
    require_current_workspace,
)

router = APIRouter(prefix="/nexus/microsite", tags=["nexus-microsite"])


@router.get("/public/{slug}")
def get_microsite_public(slug: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Legacy: GET /microsite/:slug. Public, no auth."""
    return {
        "slug": slug,
        "workspace_name": "",
        "hero": "",
        "subhero": "",
        "form_fields": [
            {"name": "email", "label": "Email", "required": True},
            {"name": "name", "label": "Full Name", "required": True},
            {"name": "company", "label": "Company", "required": False},
        ],
        "stub": True,
    }


@router.post("/public/{slug}/leads")
async def submit_microsite_lead(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Legacy: POST /microsite/:slug/leads. Public, no auth.
    Creates a nexus_inbound_leads row + first nexus_inbound_threads row."""
    body = await request.json()
    return {"ok": False, "stub": True, "slug": slug, "received": body}


@router.get("/config")
def get_microsite_config(
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Legacy: GET /api/microsite/config — authenticated workspace settings."""
    return {
        "workspace_id": extract_workspace_id(workspace),
        "slug": "",
        "enabled": False,
        "hero": "",
        "subhero": "",
        "form_fields": [],
        "stub": True,
    }


@router.patch("/config")
def update_microsite_config(
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    return {"ok": False, "stub": True}
