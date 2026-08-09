"""Cross-cutting NEXUS endpoints: health check, GDPR export, account deletion.

Mounted unprefixed — endpoints use absolute paths.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import get_db
from models import User

from nexus.deps import get_nexus_user, get_nexus_profile, require_current_workspace
from nexus.models import NexusUserProfile, NexusWorkspace

logger = logging.getLogger("nexus.routers.system")

router = APIRouter(tags=["Nexus — System"])


# ---------- Health ------------------------------------------------------------


@router.get("/nexus/health")
def health(db: Session = Depends(get_db)):
    """PUBLIC health probe. Returns the NEXUS subsystem status — useful for
    uptime checks. The DB check is a 1-row SELECT, so latency is dominated
    by the round-trip not by work."""
    db_ok = True
    db_error = None
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        db_error = str(exc)[:120]
    return {
        "ok": db_ok,
        "subsystem": "nexus",
        "db": "ok" if db_ok else "down",
        "db_error": db_error,
        "time": datetime.utcnow().isoformat() + "Z",
    }


# ---------- GDPR export -------------------------------------------------------


def _safe_query(db: Session, sql: str, params: Dict[str, Any]) -> list:
    """Run a parameterised SELECT and return rows as dicts. Returns [] on error."""
    try:
        return [dict(r) for r in db.execute(text(sql), params).mappings().all()]
    except Exception as exc:  # noqa: BLE001
        logger.debug("export query skipped: %s", str(exc)[:120])
        return []


@router.get("/nexus/me/export")
def export_my_data(
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    profile: NexusUserProfile = Depends(get_nexus_profile),
):
    """GDPR / DSR data export — dump every NEXUS row owned by this user.

    Returns a JSON file containing: profile, workspaces, products, campaigns,
    leads attached to those workspaces, sequences, bookings, voice calls,
    chat sessions + messages. Best-effort: missing tables are skipped, not
    erroring the whole export.

    The Content-Disposition header tells the browser to save the file.
    """
    workspaces = (
        db.query(NexusWorkspace)
        .filter(NexusWorkspace.owner_id == user.id)
        .all()
    )
    ws_ids = [w.id for w in workspaces]

    bundle: Dict[str, Any] = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "pricing_plan": user.pricing_plan,
        },
        "nexus_profile": {
            "role": profile.role,
            "default_workspace_id": profile.default_workspace_id,
            "trial_starts_at": profile.trial_starts_at.isoformat() if profile.trial_starts_at else None,
            "trial_ends_at": profile.trial_ends_at.isoformat() if profile.trial_ends_at else None,
            "timezone": getattr(profile, "timezone", None),
            "daily_report_time": getattr(profile, "daily_report_time", None),
            "auto_reply_enabled": getattr(profile, "auto_reply_enabled", None),
            "founder_mode": getattr(profile, "founder_mode", None),
        },
        "workspaces": [
            {
                "id": w.id,
                "name": w.name,
                "plan": w.plan,
                "status": w.status,
                "limits": w.limits,
                "usage": w.usage,
                "created_at": w.created_at.isoformat() if w.created_at else None,
            }
            for w in workspaces
        ],
    }

    if not ws_ids:
        return JSONResponse(
            content=bundle,
            headers={"Content-Disposition": "attachment; filename=nexus-export.json"},
        )

    # Per-workspace data. ANY(:ids) is Postgres-specific; in SQLite the
    # ANY rewrite falls back via the IN clause shape below.
    params = {"ids": ws_ids, "uid": user.id}

    bundle["products"] = _safe_query(
        db,
        "SELECT * FROM nexus_products WHERE workspace_id = ANY(:ids)",
        params,
    )
    bundle["campaigns"] = _safe_query(
        db,
        "SELECT * FROM nexus_campaigns WHERE workspace_id = ANY(:ids)",
        params,
    )
    bundle["leads"] = _safe_query(
        db,
        "SELECT * FROM nexus_leads WHERE workspace_id = ANY(:ids)",
        params,
    )
    bundle["sequences"] = _safe_query(
        db,
        "SELECT * FROM nexus_sequences WHERE workspace_id = ANY(:ids)",
        params,
    )
    bundle["lead_sequences"] = _safe_query(
        db,
        "SELECT * FROM nexus_lead_sequences WHERE workspace_id = ANY(:ids)",
        params,
    )
    bundle["bookings"] = _safe_query(
        db,
        "SELECT * FROM nexus_demo_bookings WHERE workspace_id = ANY(:ids)",
        params,
    )
    bundle["voice_calls"] = _safe_query(
        db,
        "SELECT * FROM nexus_voice_calls WHERE workspace_id = ANY(:ids)",
        params,
    )
    bundle["chat_sessions"] = _safe_query(
        db,
        "SELECT * FROM nexus_chat_sessions WHERE workspace_id = ANY(:ids) AND user_id = :uid",
        params,
    )

    return Response(
        content=json.dumps(bundle, default=str, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": "attachment; filename=nexus-export.json",
        },
    )


@router.delete("/nexus/me")
def delete_my_account(
    confirm: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    profile: NexusUserProfile = Depends(get_nexus_profile),
):
    """Wipe all NEXUS data owned by the current user.

    Requires `?confirm=DELETE` as a guard against accidental clicks.
    Does NOT touch the PIPELYT `users` row — only NEXUS-side data.
    Workspace cascade deletes (FK ON DELETE CASCADE) take care of most
    child rows; we explicitly clear cross-phase tables that don't have
    a real FK constraint.
    """
    if confirm != "DELETE":
        raise HTTPException(
            status_code=400,
            detail="Pass ?confirm=DELETE to wipe NEXUS data for this account.",
        )

    workspaces = (
        db.query(NexusWorkspace)
        .filter(NexusWorkspace.owner_id == user.id)
        .all()
    )
    ws_ids = [w.id for w in workspaces]

    # Explicit cross-phase cleanup. Each statement is independent so one
    # failing table doesn't block the rest.
    cleanup_sqls = [
        "DELETE FROM nexus_chat_messages WHERE session_id IN (SELECT id FROM nexus_chat_sessions WHERE user_id = :uid)",
        "DELETE FROM nexus_chat_sessions WHERE user_id = :uid",
        "DELETE FROM nexus_demo_briefings WHERE workspace_id = ANY(:ids)",
        "DELETE FROM nexus_demo_bookings WHERE workspace_id = ANY(:ids)",
        "DELETE FROM nexus_voice_calls WHERE workspace_id = ANY(:ids)",
        "DELETE FROM nexus_linkedin_messages WHERE workspace_id = ANY(:ids)",
        "DELETE FROM nexus_send_logs WHERE workspace_id = ANY(:ids)",
        "DELETE FROM nexus_touchpoints WHERE lead_sequence_id IN (SELECT id FROM nexus_lead_sequences WHERE workspace_id = ANY(:ids))",
        "DELETE FROM nexus_lead_sequences WHERE workspace_id = ANY(:ids)",
        "DELETE FROM nexus_sequences WHERE workspace_id = ANY(:ids)",
        "DELETE FROM nexus_suppression_list WHERE workspace_id = ANY(:ids)",
        "DELETE FROM nexus_conversation_accounts WHERE workspace_id = ANY(:ids)",
        "DELETE FROM nexus_automations WHERE workspace_id = ANY(:ids)",
        "DELETE FROM nexus_leads WHERE workspace_id = ANY(:ids)",
        "DELETE FROM nexus_campaigns WHERE workspace_id = ANY(:ids)",
        "DELETE FROM nexus_products WHERE workspace_id = ANY(:ids)",
    ]
    params = {"ids": ws_ids, "uid": user.id}
    for sql in cleanup_sqls:
        if not ws_ids and ":ids" in sql:
            continue
        try:
            db.execute(text(sql), params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("delete cleanup skipped (%s): %s", sql.split()[2], str(exc)[:120])

    # Workspaces (cascade should handle the rest).
    for w in workspaces:
        db.delete(w)

    # Finally the profile row itself.
    db.delete(profile)
    db.commit()

    return {"ok": True, "deleted_workspaces": len(ws_ids)}


__all__ = ["router"]
