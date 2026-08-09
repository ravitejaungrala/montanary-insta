"""User-side NEXUS endpoints — `/nexus/me` and related settings.

PIPELYT's own /profile remains canonical for name/email/avatar etc.
This router exposes only NEXUS-specific fields (role, default workspace,
trial window) and the merged view the NEXUS UI consumes.
"""

from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from models import User

from nexus import schemas
from nexus.deps import get_nexus_user, get_nexus_profile
from nexus.models import NexusUserProfile, NexusWorkspace

router = APIRouter(prefix="/nexus", tags=["Nexus — User"])


def _workspace_out(ws: NexusWorkspace) -> schemas.WorkspaceOut:
    return schemas.WorkspaceOut(
        id=ws.id,
        name=ws.name,
        owner_id=ws.owner_id,
        plan=ws.plan or "starter",
        status=ws.status or "active",
        is_trial=(ws.is_trial or "").lower() == "true",
        trial_starts_at=ws.trial_starts_at,
        trial_ends_at=ws.trial_ends_at,
        limits=ws.limits or {},
        usage=ws.usage or {},
        created_at=ws.created_at,
    )


@router.get("/me", response_model=schemas.NexusUserOut)
def get_me(
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    profile: NexusUserProfile = Depends(get_nexus_profile),
):
    """Merged PIPELYT user + NEXUS profile + owned workspaces.

    The NEXUS UI calls this on app boot to populate its AuthContext +
    BillingContext in one round trip.
    """
    workspaces = (
        db.query(NexusWorkspace)
        .filter(NexusWorkspace.owner_id == user.id)
        .order_by(NexusWorkspace.created_at.desc())
        .all()
    )

    trial_active = True
    if profile.trial_ends_at is not None:
        trial_active = datetime.utcnow() < profile.trial_ends_at

    return schemas.NexusUserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        pricing_plan=user.pricing_plan or "free",
        nexus_role=profile.role or "admin",
        default_workspace_id=profile.default_workspace_id,
        trial_starts_at=profile.trial_starts_at,
        trial_ends_at=profile.trial_ends_at,
        timezone=getattr(profile, "timezone", None) or "UTC",
        daily_report_time=getattr(profile, "daily_report_time", None) or "08:00",
        daily_pipeline_time=getattr(profile, "daily_pipeline_time", None) or "07:00",
        auto_reply_enabled=bool(getattr(profile, "auto_reply_enabled", False)),
        founder_mode=bool(getattr(profile, "founder_mode", False)),
        trial_active=trial_active,
        workspaces=[_workspace_out(w) for w in workspaces],
    )


@router.patch("/me/settings", response_model=schemas.NexusUserOut)
def patch_me_settings(
    payload: schemas.NexusUserSettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    profile: NexusUserProfile = Depends(get_nexus_profile),
):
    """Update NEXUS-specific user preferences. PIPELYT-side fields
    (full_name, etc.) are NOT settable here — use PIPELYT /profile."""

    if payload.default_workspace_id is not None:
        # Must own the workspace we're switching to.
        ws = (
            db.query(NexusWorkspace)
            .filter(
                NexusWorkspace.id == payload.default_workspace_id,
                NexusWorkspace.owner_id == user.id,
            )
            .first()
        )
        if ws is None:
            raise HTTPException(
                status_code=404,
                detail="Workspace not found or not owned by current user",
            )
        profile.default_workspace_id = payload.default_workspace_id

    if payload.nexus_role is not None:
        # Self-demote disallowed — the only admin must remain admin.
        # In Phase 1, no team feature exists yet, so role flips are
        # effectively no-ops; we accept the field for forward compat
        # but reject anything other than the existing role.
        if payload.nexus_role != (profile.role or "admin"):
            raise HTTPException(
                status_code=400,
                detail="Self role-change is not allowed; ask an admin to flip the role server-side.",
            )

    # Preferences. Each is independently optional; we only touch the row
    # for fields actually included in the payload, so partial PATCH works.
    import re
    _HHMM = re.compile(r"^\d{2}:\d{2}$")

    if payload.timezone is not None:
        # Light validation — accept anything ≤64 chars; pytz lookup happens
        # at scheduler dispatch time.
        if not (1 <= len(payload.timezone) <= 64):
            raise HTTPException(status_code=400, detail="timezone must be 1–64 chars")
        profile.timezone = payload.timezone
    if payload.daily_report_time is not None:
        if not _HHMM.match(payload.daily_report_time):
            raise HTTPException(status_code=400, detail="daily_report_time must be HH:MM")
        profile.daily_report_time = payload.daily_report_time
    if payload.daily_pipeline_time is not None:
        if not _HHMM.match(payload.daily_pipeline_time):
            raise HTTPException(status_code=400, detail="daily_pipeline_time must be HH:MM")
        profile.daily_pipeline_time = payload.daily_pipeline_time
    if payload.auto_reply_enabled is not None:
        profile.auto_reply_enabled = bool(payload.auto_reply_enabled)
    if payload.founder_mode is not None:
        profile.founder_mode = bool(payload.founder_mode)

    db.commit()
    db.refresh(profile)

    # Reuse the /me builder to return the fresh view.
    return get_me(db=db, user=user, profile=profile)
