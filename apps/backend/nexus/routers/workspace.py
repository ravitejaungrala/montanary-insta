"""Workspace CRUD endpoints.

Mounted at `/nexus/workspaces` from main.py. Replaces (and trims) the
legacy NEXUS `/api/team` + `/api/settings` surface where they touched
workspace management.

Auth: all routes require a valid PIPELYT JWT (`get_nexus_user`).
"""

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.database import get_db
from models import User

from nexus import schemas
from nexus.deps import (
    get_nexus_user,
    get_nexus_profile,
    require_nexus_role,
    _days_until_reset,
)
import secrets

from pydantic import BaseModel, EmailStr

from nexus.models import (
    NexusWorkspace,
    NexusUserProfile,
    NexusWorkspaceInvite,
    PLAN_LIMITS,
    default_limits_for,
    default_trial_ends_at,
)

router = APIRouter(prefix="/nexus/workspaces", tags=["Nexus — Workspaces"])


# Default 4-step outbound sequence auto-installed for every new workspace.
# Step 0 fires on enrollment; subsequent steps are gated by delay_days from
# the previous send. Subject/body templates are simple {var} substitutions
# handled by the personalize fallback when the LLM path is unavailable.
DEFAULT_SEQUENCE_NAME = "Default — 4-step outbound"
DEFAULT_SEQUENCE_STEPS = [
    {
        "step": 0,
        "delay_days": 0,
        "subject_template": "Quick question, {first_name}",
        "body_template": (
            "Hi {first_name},\n\n"
            "I noticed {company_name} and thought {product_name} might be relevant — "
            "we help teams like yours with {value_prop}.\n\n"
            "Worth a 15-minute look next week?"
        ),
    },
    {
        "step": 1,
        "delay_days": 3,
        "subject_template": "Re: Quick question, {first_name}",
        "body_template": (
            "Just bumping this up in case it got buried.\n\n"
            "Most folks in your seat at {company_name} care about {icp_pain} — "
            "happy to share a short loom on how we'd approach that.\n\n"
            "Want me to send it over?"
        ),
    },
    {
        "step": 2,
        "delay_days": 3,
        "subject_template": "{first_name} — last try on this",
        "body_template": (
            "Last note on my side. If timing's off, no worries — "
            "would it be useful to ping you again next quarter?\n\n"
            "Either way, appreciate you reading this far."
        ),
    },
    {
        "step": 3,
        "delay_days": 3,
        "subject_template": "Wrapping up — should I close the loop?",
        "body_template": (
            "Hey {first_name},\n\n"
            "Closing the loop on my side. Two options:\n"
            "  1) Reply with 'later' and I'll re-surface next quarter.\n"
            "  2) Reply with 'no' and I won't bug you again.\n\n"
            "Thanks for considering."
        ),
    },
]


def _install_default_sequence(db: Session, workspace_id: int) -> None:
    """Insert the default sequence for a freshly-created workspace.

    Best-effort: if the Phase 4 table is not yet present (cold-boot pre-merge)
    we silently skip — the next workspace-create after migrations land will
    succeed, and existing workspaces can use the /defaults endpoint below.
    """
    try:
        from nexus.models_phase4 import NexusSequence
        existing = (
            db.query(NexusSequence)
            .filter(
                NexusSequence.workspace_id == workspace_id,
                NexusSequence.name == DEFAULT_SEQUENCE_NAME,
            )
            .first()
        )
        if existing is not None:
            return
        row = NexusSequence(
            workspace_id=workspace_id,
            campaign_id=None,
            name=DEFAULT_SEQUENCE_NAME,
            steps=DEFAULT_SEQUENCE_STEPS,
        )
        db.add(row)
        db.commit()
    except Exception:
        # Don't block workspace creation on this — the user can call
        # POST /nexus/sequences/defaults later.
        db.rollback()


def _to_out(ws: NexusWorkspace) -> schemas.WorkspaceOut:
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


@router.post("", response_model=schemas.WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(
    payload: schemas.WorkspaceCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    profile: NexusUserProfile = Depends(get_nexus_profile),
):
    """Create a NEXUS workspace owned by the current user.

    First workspace becomes the user's default automatically. Subsequent
    workspaces can be promoted via PATCH /nexus/me/settings.

    Plan defaults to the user's PIPELYT pricing_plan (so quotas track
    the user's PIPELYT subscription out of the box). Caller can override
    with a stricter plan for testing.
    """
    plan = payload.plan or (user.pricing_plan or "starter")
    if plan not in PLAN_LIMITS:
        plan = "starter"

    now = datetime.utcnow()
    workspace = NexusWorkspace(
        name=payload.name.strip(),
        owner_id=user.id,
        plan=plan,
        status="trialing",
        is_trial="true",
        trial_starts_at=now,
        trial_ends_at=now + timedelta(days=14),
        limits=default_limits_for(plan),
        usage={
            "campaigns_active": 0,
            "leads_this_month": 0,
            "emails_this_month": 0,
            "tokens_this_month": 0,
        },
    )
    db.add(workspace)
    db.commit()
    db.refresh(workspace)

    # Auto-set as default if user has none.
    if profile.default_workspace_id is None:
        profile.default_workspace_id = workspace.id
        db.commit()

    # Auto-install the default 4-step sequence so the user can immediately
    # enroll leads without first authoring a sequence.
    _install_default_sequence(db, workspace.id)

    return _to_out(workspace)


@router.get("", response_model=List[schemas.WorkspaceOut])
def list_workspaces(
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
):
    """List workspaces owned by the current user."""
    rows = (
        db.query(NexusWorkspace)
        .filter(NexusWorkspace.owner_id == user.id)
        .order_by(NexusWorkspace.created_at.desc())
        .all()
    )
    return [_to_out(w) for w in rows]


def _load_owned_workspace(
    workspace_id: int, db: Session, user: User
) -> NexusWorkspace:
    ws = (
        db.query(NexusWorkspace)
        .filter(NexusWorkspace.id == workspace_id)
        .first()
    )
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if ws.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Not the workspace owner")
    return ws


@router.get("/{workspace_id}", response_model=schemas.WorkspaceOut)
def get_workspace(
    workspace_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
):
    ws = _load_owned_workspace(workspace_id, db, user)
    return _to_out(ws)


@router.patch("/{workspace_id}", response_model=schemas.WorkspaceOut)
def update_workspace(
    workspace_id: int,
    payload: schemas.WorkspaceUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    _: NexusUserProfile = Depends(require_nexus_role("admin")),
):
    ws = _load_owned_workspace(workspace_id, db, user)

    if payload.name is not None:
        ws.name = payload.name.strip()
    if payload.status is not None:
        if payload.status not in {
            "active", "trialing", "past_due", "canceled", "suspended"
        }:
            raise HTTPException(status_code=400, detail="Invalid status value")
        ws.status = payload.status
    if payload.plan is not None:
        if payload.plan not in PLAN_LIMITS:
            raise HTTPException(status_code=400, detail="Unknown plan")
        ws.plan = payload.plan
        # Re-apply default limits for the new plan (admins can override
        # afterwards by editing the limits JSONB directly — separate
        # endpoint, not yet exposed).
        ws.limits = default_limits_for(payload.plan)

    db.commit()
    db.refresh(ws)
    return _to_out(ws)


# ── Invites + team ───────────────────────────────────────────────────────────


class InviteCreate(BaseModel):
    email: EmailStr
    role: str = "member"  # admin | member | viewer


class InviteOut(BaseModel):
    id: int
    workspace_id: int
    email_lower: str
    role: str
    status: str
    token: str
    created_at: datetime
    accepted_at: Optional[datetime] = None

    class Config:
        from_attributes = True


_ALLOWED_INVITE_ROLES = {"admin", "member", "viewer"}


@router.post(
    "/{workspace_id}/invites",
    response_model=InviteOut,
    status_code=status.HTTP_201_CREATED,
)
def create_invite(
    workspace_id: int,
    payload: InviteCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
):
    """Create a workspace invitation. Owner-only."""
    ws = _load_owned_workspace(workspace_id, db, user)
    if payload.role not in _ALLOWED_INVITE_ROLES:
        raise HTTPException(
            status_code=400,
            detail=f"role must be one of {sorted(_ALLOWED_INVITE_ROLES)}",
        )
    email_lower = payload.email.lower().strip()
    existing = (
        db.query(NexusWorkspaceInvite)
        .filter(
            NexusWorkspaceInvite.workspace_id == ws.id,
            NexusWorkspaceInvite.email_lower == email_lower,
            NexusWorkspaceInvite.status == "pending",
        )
        .first()
    )
    if existing:
        return InviteOut.model_validate(existing)

    invite = NexusWorkspaceInvite(
        workspace_id=ws.id,
        invited_by_user_id=user.id,
        email_lower=email_lower,
        role=payload.role,
        token=secrets.token_urlsafe(32),
        status="pending",
        expires_at=datetime.utcnow() + timedelta(days=14),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return InviteOut.model_validate(invite)


@router.get("/{workspace_id}/invites", response_model=List[InviteOut])
def list_invites(
    workspace_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
):
    ws = _load_owned_workspace(workspace_id, db, user)
    rows = (
        db.query(NexusWorkspaceInvite)
        .filter(NexusWorkspaceInvite.workspace_id == ws.id)
        .order_by(NexusWorkspaceInvite.id.desc())
        .all()
    )
    return [InviteOut.model_validate(r) for r in rows]


@router.delete(
    "/{workspace_id}/invites/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_invite(
    workspace_id: int,
    invite_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
):
    ws = _load_owned_workspace(workspace_id, db, user)
    inv = (
        db.query(NexusWorkspaceInvite)
        .filter(
            NexusWorkspaceInvite.id == invite_id,
            NexusWorkspaceInvite.workspace_id == ws.id,
        )
        .first()
    )
    if not inv:
        raise HTTPException(status_code=404, detail="Invite not found")
    inv.status = "revoked"
    db.commit()
    return None


@router.post("/invites/{token}/accept")
def accept_invite(
    token: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    profile: NexusUserProfile = Depends(get_nexus_profile),
):
    """Accept a workspace invite. The caller must be signed in to PIPELYT
    with the email the invite was sent to (we match case-insensitively).
    """
    inv = (
        db.query(NexusWorkspaceInvite)
        .filter(NexusWorkspaceInvite.token == token)
        .first()
    )
    if not inv:
        raise HTTPException(status_code=404, detail="Invite not found")
    if inv.status != "pending":
        raise HTTPException(status_code=400, detail=f"Invite already {inv.status}")
    if inv.expires_at and inv.expires_at < datetime.utcnow():
        inv.status = "expired"
        db.commit()
        raise HTTPException(status_code=400, detail="Invite expired")
    if (user.email or "").lower().strip() != inv.email_lower:
        raise HTTPException(
            status_code=403,
            detail="This invite was sent to a different email.",
        )

    inv.status = "accepted"
    inv.accepted_at = datetime.utcnow()

    # Set role on the user's profile + default workspace.
    profile.role = inv.role
    profile.default_workspace_id = inv.workspace_id

    db.commit()
    return {
        "ok": True,
        "workspace_id": inv.workspace_id,
        "role": inv.role,
    }


@router.get("/{workspace_id}/usage", response_model=schemas.WorkspaceUsageOut)
def get_workspace_usage(
    workspace_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
):
    """Returns current usage + remaining quota for each tracked dimension."""
    ws = _load_owned_workspace(workspace_id, db, user)
    limits = ws.limits or default_limits_for(ws.plan or "starter")
    usage = ws.usage or {}

    usage_field_map = {
        "campaigns": ("campaigns_active", "campaigns"),
        "leads_per_month": ("leads_this_month", "leads_per_month"),
        "emails_per_month": ("emails_this_month", "emails_per_month"),
        "tokens_per_month": ("tokens_this_month", "tokens_per_month"),
    }

    remaining = {}
    for limit_key, (usage_key, _) in usage_field_map.items():
        lim = int(limits.get(limit_key, 0))
        if lim == -1:
            remaining[limit_key] = -1  # unlimited
        else:
            remaining[limit_key] = max(0, lim - int(usage.get(usage_key, 0)))

    return schemas.WorkspaceUsageOut(
        workspace_id=ws.id,
        plan=ws.plan or "starter",
        limits=limits,
        usage=usage,
        remaining=remaining,
        resets_in_days=_days_until_reset(ws),
    )
