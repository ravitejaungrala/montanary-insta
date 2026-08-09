"""SQLAlchemy tables for the ported NEXUS data model.

Phase 1 — Workspace + NEXUS-specific user profile (1:1 with PIPELYT users).
Subsequent phases append models here per `docs/nexus-migration/MODEL_MAP.md`.

Conventions:
- Every NEXUS-side table is prefixed `nexus_` to keep it visually distinct
  from PIPELYT's own tables.
- We do NOT modify PIPELYT's `users` table. NEXUS-specific user data lives
  in a separate `nexus_user_profiles` table with a 1:1 FK to `users.id`.
  This keeps PIPELYT's User ORM class — and every existing PIPELYT
  query — completely untouched, regardless of whether `NEXUS_ENABLED`
  is on or off in any given environment.
- Billing fields are intentionally absent — PIPELYT's `/payment` router
  handles billing. See docs/nexus-migration/PHASE_PLAN.md.
"""

from datetime import datetime, timedelta
from sqlalchemy import Boolean, Column, BigInteger, String, DateTime, ForeignKey, JSON, Index
from sqlalchemy.orm import relationship

from core.database import Base


# ── Plan limits (mirrors NEXUS Workspace.PLAN_LIMITS) ────────────────────────
# Single source of truth for per-plan quotas. `pricing_plan` on the PIPELYT
# User row keys into this dict; unknown plans fall back to `starter`.
PLAN_LIMITS = {
    "free": {
        "campaigns": 1,
        "leads_per_month": 100,
        "emails_per_month": 50,
        "tokens_per_month": 50_000,
        "team_members": 1,
        "mailboxes": 1,
    },
    "starter": {
        "campaigns": 5,
        "leads_per_month": 5_000,
        "emails_per_month": 1_000,
        "tokens_per_month": 500_000,
        "team_members": 1,
        "mailboxes": 1,
    },
    "growth": {  # PIPELYT alias for NEXUS "pro"
        "campaigns": 25,
        "leads_per_month": 50_000,
        "emails_per_month": 10_000,
        "tokens_per_month": 2_000_000,
        "team_members": 5,
        "mailboxes": 3,
    },
    "agency": {
        "campaigns": 100,
        "leads_per_month": 200_000,
        "emails_per_month": 50_000,
        "tokens_per_month": 10_000_000,
        "team_members": 25,
        "mailboxes": 10,
    },
    "enterprise": {
        "campaigns": -1,
        "leads_per_month": -1,
        "emails_per_month": -1,
        "tokens_per_month": -1,
        "team_members": -1,
        "mailboxes": -1,
    },
}


def default_limits_for(plan: str) -> dict:
    """Resolve PLAN_LIMITS for a plan, with safe fallback to 'starter'."""
    return dict(PLAN_LIMITS.get((plan or "").lower(), PLAN_LIMITS["starter"]))


# ── Credit allotment + per-operation cost ────────────────────────────────────
# Monthly credit grant per plan. `-1` == unlimited (Enterprise). These are
# CREDITS (lead-discovery budget), separate from the quotas in PLAN_LIMITS.
# Mirrors Apollo: credits renew each billing cycle and do NOT roll over.
CREDIT_ALLOTMENT = {
    "free": 0,        # no free users today; safe default
    "starter": 500,
    "growth": 1000,
    "agency": 2500,
    "enterprise": -1,  # unlimited (or a finite admin override per user)
}

# Credit cost per credit-bearing operation. Email reveal costs 1 credit/lead
# today; adding a new paid op (e.g. enrichment) is a single entry here plus a
# `consume(op="...")` call at that op's site — nothing else changes.
CREDIT_COSTS = {
    "email_reveal": 1,
}


def credit_allotment_for(plan: str) -> int:
    """Monthly credit grant for a plan; 0 for unknown plans, -1 = unlimited."""
    return CREDIT_ALLOTMENT.get((plan or "").lower(), 0)


def default_trial_ends_at() -> datetime:
    """Workspaces auto-issue a 14-day trial on creation."""
    return datetime.utcnow() + timedelta(days=14)


# ── Workspace ────────────────────────────────────────────────────────────────
class NexusWorkspace(Base):
    """Multi-tenant container for NEXUS data (campaigns, leads, sequences…).

    A PIPELYT user can own multiple workspaces. The user's
    `nexus_default_workspace_id` column (added in sync_db_schema) points
    to the workspace selected for the current session.

    Billing fields are deliberately omitted — `users.pricing_plan`
    drives plan-derived quotas via `PLAN_LIMITS` above.
    """

    __tablename__ = "nexus_workspaces"

    id = Column(BigInteger, primary_key=True, index=True)
    name = Column(String, nullable=False)
    owner_id = Column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Trial window — independent of PIPELYT subscription so workspace
    # creation grants its own 14-day eval period.
    is_trial = Column(String, default="true")  # 'true' | 'false' (stored as VARCHAR for Postgres BIT-avoidance)
    trial_starts_at = Column(DateTime, default=datetime.utcnow)
    trial_ends_at = Column(DateTime, default=default_trial_ends_at)

    # Plan + status snapshot (read-through to PIPELYT user). Cached here
    # so a workspace can be cloned across users without re-resolving.
    plan = Column(String, default="starter")  # mirrors PIPELYT pricing_plan
    status = Column(String, default="active")  # active | trialing | past_due | canceled | suspended

    # Limits + rolling-month usage counters, JSONB for cheap evolution.
    limits = Column(JSON, default=lambda: default_limits_for("starter"))
    usage = Column(
        JSON,
        default=lambda: {
            "campaigns_active": 0,
            "leads_this_month": 0,
            "emails_this_month": 0,
            "tokens_this_month": 0,
        },
    )

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User", foreign_keys=[owner_id])

    __table_args__ = (
        Index("ix_nexus_workspaces_owner_status", "owner_id", "status"),
    )


# ── User profile (1:1 with PIPELYT users) ───────────────────────────────────
class NexusUserProfile(Base):
    """NEXUS-specific user attributes, stored in a side table to keep
    PIPELYT's `users` row untouched.

    Created lazily — the first time a user hits a /nexus endpoint, the
    deps layer creates a row here with defaults (admin role, no default
    workspace, 14-day trial window). PIPELYT's existing `users` row is
    never modified.
    """

    __tablename__ = "nexus_user_profiles"

    user_id = Column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role = Column(String, default="admin")  # admin | member | viewer
    default_workspace_id = Column(
        BigInteger,
        ForeignKey("nexus_workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Trial window — 14 days from first /nexus hit.
    trial_starts_at = Column(DateTime, default=datetime.utcnow)
    trial_ends_at = Column(DateTime, default=default_trial_ends_at)

    # Personal preferences. Persisted at the user level so they survive
    # workspace switches. Migration ALTERs land via phase6.MIGRATIONS so
    # cold-start adds them on existing rows.
    timezone = Column(String(64), default="UTC")
    daily_report_time = Column(String(8), default="08:00")     # HH:MM, 24h
    daily_pipeline_time = Column(String(8), default="07:00")   # HH:MM, 24h
    auto_reply_enabled = Column(Boolean, default=False)
    founder_mode = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", foreign_keys=[user_id])
    default_workspace = relationship(
        "NexusWorkspace",
        foreign_keys=[default_workspace_id],
    )


# ── Workspace invitations ────────────────────────────────────────────────────
class NexusWorkspaceInvite(Base):
    """Outstanding email invitations to join a workspace.

    Created by the owner via POST /nexus/workspaces/{id}/invites. The token
    is a secrets.token_urlsafe() value; the invited user accepts via
    POST /nexus/workspaces/invites/{token}/accept after they've signed up
    on PIPELYT.
    """
    __tablename__ = "nexus_workspace_invites"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(
        BigInteger,
        ForeignKey("nexus_workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invited_by_user_id = Column(BigInteger, nullable=False)
    email_lower = Column(String, nullable=False, index=True)
    role = Column(String, default="member")  # admin | member | viewer
    token = Column(String, unique=True, nullable=False, index=True)
    status = Column(String, default="pending")  # pending | accepted | revoked | expired
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    accepted_at = Column(DateTime, nullable=True)
