"""Pydantic request/response shapes for NEXUS routers.

Phase 1 — workspace + user. Subsequent phases append here.
"""

from datetime import datetime
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field


# ── Workspace ────────────────────────────────────────────────────────────────
class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    plan: Optional[str] = None  # falls back to user's PIPELYT pricing_plan


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    status: Optional[str] = None  # active | trialing | past_due | canceled | suspended
    plan: Optional[str] = None


class WorkspaceLimits(BaseModel):
    campaigns: int = 0
    leads_per_month: int = 0
    emails_per_month: int = 0
    tokens_per_month: int = 0
    team_members: int = 0
    mailboxes: int = 0


class WorkspaceUsage(BaseModel):
    campaigns_active: int = 0
    leads_this_month: int = 0
    emails_this_month: int = 0
    tokens_this_month: int = 0


class WorkspaceOut(BaseModel):
    id: int
    name: str
    owner_id: int
    plan: str
    status: str
    is_trial: bool
    trial_starts_at: Optional[datetime] = None
    trial_ends_at: Optional[datetime] = None
    limits: Dict[str, Any] = Field(default_factory=dict)
    usage: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    class Config:
        from_attributes = True  # Pydantic v2 ORM mode


class WorkspaceUsageOut(BaseModel):
    """Returned by GET /nexus/workspaces/{id}/usage."""
    workspace_id: int
    plan: str
    limits: Dict[str, Any]
    usage: Dict[str, Any]
    remaining: Dict[str, int]
    resets_in_days: int


# ── User ─────────────────────────────────────────────────────────────────────
class NexusUserOut(BaseModel):
    """Returned by GET /nexus/me — merged PIPELYT + NEXUS profile view."""
    # PIPELYT fields
    id: int
    email: str
    full_name: Optional[str] = None
    pricing_plan: str
    # NEXUS profile fields
    nexus_role: str = "admin"
    default_workspace_id: Optional[int] = None
    trial_starts_at: Optional[datetime] = None
    trial_ends_at: Optional[datetime] = None
    # Preferences
    timezone: str = "UTC"
    daily_report_time: str = "08:00"
    daily_pipeline_time: str = "07:00"
    auto_reply_enabled: bool = False
    founder_mode: bool = False
    # Derived
    trial_active: bool = True
    workspaces: List[WorkspaceOut] = Field(default_factory=list)


class NexusUserSettingsUpdate(BaseModel):
    """PATCH /nexus/me/settings — only NEXUS-side preferences. PIPELYT
    profile fields (full_name, etc.) are edited via the PIPELYT /profile
    endpoint, not here."""
    nexus_role: Optional[str] = None  # admin only (self-demote disallowed by router)
    default_workspace_id: Optional[int] = None
    timezone: Optional[str] = None
    daily_report_time: Optional[str] = None
    daily_pipeline_time: Optional[str] = None
    auto_reply_enabled: Optional[bool] = None
    founder_mode: Optional[bool] = None
