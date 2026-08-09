"""User + workspace settings router.

Legacy: apps/nexus-legacy/server/routes/settingsRoute.js
Routes (legacy):
  GET  /api/settings/me              — current user prefs
  PATCH /api/settings/me             — update prefs
  GET  /api/settings/workspace       — current workspace config
  PATCH /api/settings/workspace      — update workspace config
  GET  /api/settings/notifications   — notification prefs
  PATCH /api/settings/notifications  — update notification prefs

STUB: read/write nexus_user_profiles + nexus_settings rows; current
shape returns defaults so the frontend renders without errors.
"""
from __future__ import annotations
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from core.database import get_db
from nexus._phase6_common import (
    extract_user_id,
    extract_workspace_id,
    get_nexus_user,
    require_current_workspace,
)

router = APIRouter(prefix="/nexus/settings", tags=["nexus-settings"])


@router.get("/me")
def get_my_settings(
    db: Session = Depends(get_db),
    user: Any = Depends(get_nexus_user),
) -> Dict[str, Any]:
    """Current user's NEXUS preferences (timezone, daily-report time, etc.)."""
    return {
        "user_id": extract_user_id(user),
        "timezone": "UTC",
        "daily_report_time": "08:00",
        "daily_pipeline_time": "07:00",
        "auto_reply_enabled": False,
        "founder_mode": False,
        "stub": True,
    }


@router.patch("/me")
def update_my_settings(
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    user: Any = Depends(get_nexus_user),
) -> Dict[str, Any]:
    return {"ok": False, "stub": True, "updated_fields": list(body.keys())}


@router.get("/workspace")
def get_workspace_settings(
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    return {
        "workspace_id": extract_workspace_id(workspace),
        "settings": {},
        "stub": True,
    }


@router.patch("/workspace")
def update_workspace_settings(
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    workspace: Any = Depends(require_current_workspace),
) -> Dict[str, Any]:
    return {"ok": False, "stub": True}


@router.get("/notifications")
def get_notification_settings(
    db: Session = Depends(get_db),
    user: Any = Depends(get_nexus_user),
) -> Dict[str, Any]:
    return {"email": True, "in_app": True, "slack": False, "stub": True}


@router.patch("/notifications")
def update_notification_settings(
    body: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    user: Any = Depends(get_nexus_user),
) -> Dict[str, Any]:
    return {"ok": False, "stub": True}
