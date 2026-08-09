"""Billing router stub — NEXUS-side view onto PIPELYT billing.

Legacy: apps/nexus-legacy/server/routes/billing.js
Routes (legacy):
  GET  /api/billing/usage           — current plan + usage counters
  GET  /api/billing/plan            — plan details + limits
  POST /api/billing/upgrade         — initiate upgrade

NOTE: PIPELYT owns the real billing surface at /payment/*. This router
exists ONLY so legacy code paths that hit /nexus/billing/* don't 404.
Long-term, the frontend should call PIPELYT's billing endpoints directly.
"""
from __future__ import annotations
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_common import (
    extract_workspace_id,
    require_current_workspace,
)
from nexus.models import default_limits_for

router = APIRouter(prefix="/nexus/billing", tags=["nexus-billing"])


@router.get("/usage")
def get_usage(
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Legacy: GET /api/billing/usage."""
    wid = extract_workspace_id(workspace)
    plan = "starter"
    usage = {}
    try:
        plan = getattr(workspace, "plan", None) or "starter"
        usage = getattr(workspace, "usage", None) or {}
    except Exception:
        pass
    return {
        "workspace_id": wid,
        "plan": plan,
        "usage": usage,
        "limits": default_limits_for(plan),
        "stub": True,
    }


@router.get("/plan")
def get_plan(
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Legacy: GET /api/billing/plan."""
    plan = "starter"
    try:
        plan = getattr(workspace, "plan", None) or "starter"
    except Exception:
        pass
    return {"plan": plan, "limits": default_limits_for(plan)}


@router.post("/upgrade")
def initiate_upgrade(
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    """Legacy: POST /api/billing/upgrade. Redirects users to PIPELYT
    billing surface (/payment/*) — out of scope for NEXUS."""
    return {
        "ok": False,
        "stub": True,
        "redirect_to": "/payment",
        "reason": "PIPELYT handles billing",
    }
