"""FastAPI dependencies that mirror NEXUS's Express middleware.

Mappings from legacy NEXUS middleware (in `apps/nexus-legacy/server/middleware/`)
to the dependencies exported here:

| NEXUS (Express)                       | PIPELYT (FastAPI)              |
|---------------------------------------|--------------------------------|
| `protect` (auth.js)                   | `get_nexus_user`               |
| `trialGuard` (trialGuard.js)          | `trial_guard`                  |
| `requireRole('admin')` (requireRole.js)| `require_nexus_role('admin')` |
| `loadWorkspace` (planEnforcement.js)  | `get_current_workspace`        |
| `checkCampaignLimit` etc.             | `check_limit('campaigns')` etc.|

PIPELYT's existing JWT is reused — NEXUS does NOT issue its own. The
`get_current_user` from `routers.auth` resolves the user from the
`Authorization: Bearer ...` header; we wrap it so we can additionally
ensure a `NexusUserProfile` row exists and load the user's default
workspace.
"""

from datetime import datetime, timedelta
from typing import Optional, Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.database import get_db
from models import User
from routers.auth import block_user_writes, get_current_user  # noqa: F401 (re-export)

from nexus.models import (
    NexusWorkspace,
    NexusUserProfile,
    PLAN_LIMITS,
    default_limits_for,
)


# ── Auth: PIPELYT JWT + lazy NexusUserProfile bootstrap ──────────────────────
def get_nexus_user(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> User:
    """Authenticated PIPELYT user, with a NEXUS profile row auto-created
    on first hit. Returns the PIPELYT User; the caller can read NEXUS-side
    fields by joining to `NexusUserProfile` themselves or by depending on
    `get_nexus_profile` below.
    """
    _ensure_profile(db, user.id)
    return user


def get_nexus_profile(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NexusUserProfile:
    """Returns the user's NexusUserProfile row, creating it lazily."""
    return _ensure_profile(db, user.id)


def _ensure_profile(db: Session, user_id: int) -> NexusUserProfile:
    profile = (
        db.query(NexusUserProfile)
        .filter(NexusUserProfile.user_id == user_id)
        .first()
    )
    if profile is None:
        now = datetime.utcnow()
        # Mirror the canonical users.role into the profile (it's a mirror — the
        # guards read users.role). Falls back to the role helper's default.
        from sqlalchemy import text as _text
        from core.roles import normalize_role
        _urole = db.execute(
            _text("SELECT role FROM users WHERE id = :u"), {"u": user_id}
        ).scalar()
        profile = NexusUserProfile(
            user_id=user_id,
            role=normalize_role(_urole),
            trial_starts_at=now,
            trial_ends_at=now + timedelta(days=14),
        )
        db.add(profile)
        try:
            db.commit()
            db.refresh(profile)
        except IntegrityError:
            # Concurrent request created the profile first — re-fetch it.
            db.rollback()
            profile = (
                db.query(NexusUserProfile)
                .filter(NexusUserProfile.user_id == user_id)
                .first()
            )
    return profile


# ── Workspace resolution ─────────────────────────────────────────────────────
def get_current_workspace(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    profile: NexusUserProfile = Depends(get_nexus_profile),
) -> Optional[NexusWorkspace]:
    """Loads the workspace whose data this user should see. Returns None only
    if no workspace can be resolved at all (the router 404/422s in that case).

    Data isolation rule (3-role RBAC): a **team member** (Admin or User, i.e.
    `team_owner_id` is set) always shares their **Master Admin's** workspace —
    never their own. This is what gives Admins/Users the team's leads, products,
    and campaigns instead of an empty personal workspace. A Master Admin / owner
    resolves their selected default, falling back to the workspace they own.
    """
    owner_id = getattr(user, "team_owner_id", None)
    if owner_id:
        owner_ws = (
            db.query(NexusWorkspace)
            .filter(NexusWorkspace.owner_id == owner_id)
            .order_by(NexusWorkspace.id.asc())
            .first()
        )
        if owner_ws is not None:
            return owner_ws

    # Owner / standalone account — prefer the explicitly-selected workspace,
    # else fall back to one they own (covers a brand-new owner who never set
    # a default, avoiding a spurious 422 on first load).
    if profile.default_workspace_id is not None:
        ws = (
            db.query(NexusWorkspace)
            .filter(NexusWorkspace.id == profile.default_workspace_id)
            .first()
        )
        if ws is not None:
            return ws
    return (
        db.query(NexusWorkspace)
        .filter(NexusWorkspace.owner_id == user.id)
        .order_by(NexusWorkspace.id.asc())
        .first()
    )


def require_current_workspace(
    workspace: Optional[NexusWorkspace] = Depends(get_current_workspace),
) -> NexusWorkspace:
    """Stricter variant — 422 if the user has no workspace selected yet."""
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "No default workspace selected. Create one via "
                "POST /nexus/workspaces or select an existing one via "
                "PATCH /nexus/me/settings."
            ),
        )
    return workspace


# ── Trial / status guard (mirror of trialGuard.js) ───────────────────────────
def trial_guard(
    workspace: NexusWorkspace = Depends(require_current_workspace),
) -> NexusWorkspace:
    """DISABLED — payment gating is turned off for all of Nexus.

    Previously this raised HTTP 402 when the workspace was canceled,
    suspended, or its trial had expired. All the original signatures
    (and the existing `Depends(trial_guard)` wiring across every
    Nexus router) are preserved — the function now just returns the
    workspace unconditionally so every endpoint stays accessible.

    To re-enable later, restore the suspended/canceled and trial-
    expired checks below.
    """
    return workspace


# ── Role guard (mirror of requireRole.js) ────────────────────────────────────
def require_nexus_role(*roles: str) -> Callable:
    """Dependency factory: allow only users whose canonical **`users.role`**
    satisfies the requirement. `users.role` is the single source of truth
    (3-role hierarchy); `nexus_user_profiles.role` is just a mirror.

    Legacy args still work unchanged — `require_nexus_role("admin")` (the ~30
    existing callsites) is translated via `roles.expand_legacy_required` so the
    new `master_admin`+`admin` both satisfy a legacy `"admin"` requirement, and
    `"member"/"viewer"` requirements accept any authenticated role.
    """
    required = set(roles)

    def _guard(
        user: User = Depends(get_current_user),
        profile: NexusUserProfile = Depends(get_nexus_profile),
    ) -> NexusUserProfile:
        from core.roles import expand_legacy_required, normalize_role
        allowed = expand_legacy_required(required)
        current = normalize_role(getattr(user, "role", None))
        if current not in allowed:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "Forbidden: insufficient role",
                    "required": sorted(allowed),
                    "current": current,
                },
            )
        return profile

    return _guard


# `block_user_writes` is imported above from routers.auth (canonical home —
# no nexus deps) and re-exported so nexus routers can keep importing it from
# the familiar nexus.deps location.


# ── Plan enforcement (mirror of planEnforcement.js) ──────────────────────────
LIMIT_KEYS = (
    "campaigns",
    "leads_per_month",
    "emails_per_month",
    "tokens_per_month",
    "team_members",
    "mailboxes",
)


def _days_until_reset(workspace: NexusWorkspace) -> int:
    """Days until next billing-cycle reset. We anchor on
    workspace.created_at since billing_cycle_start was dropped from the
    schema (billing is PIPELYT's responsibility)."""
    if not workspace.created_at:
        return 30
    now = datetime.utcnow()
    start = workspace.created_at
    next_reset = start
    # Advance month-by-month until we land in the future.
    while next_reset <= now:
        # Naive month addition — good enough for a UI hint.
        year = next_reset.year + (next_reset.month // 12)
        month = (next_reset.month % 12) + 1
        next_reset = next_reset.replace(year=year, month=month)
    return max(1, (next_reset - now).days)


def _limit_for(workspace: NexusWorkspace, key: str) -> int:
    """Resolve a quota — prefer the workspace's stored limit (so admins
    can override), else fall back to PLAN_LIMITS keyed off plan."""
    limits = workspace.limits or default_limits_for(workspace.plan or "starter")
    value = limits.get(key)
    if value is None:
        value = default_limits_for(workspace.plan or "starter").get(key, 0)
    return int(value)


def check_limit(key: str) -> Callable:
    """Dependency factory for usage-quota checks. Use as:

        @router.post("/some-leady-thing", dependencies=[Depends(check_limit("leads_per_month"))])

    Raises 402 with the standard NEXUS limit payload if the workspace
    has hit its monthly cap. `-1` in PLAN_LIMITS means unlimited.
    """
    if key not in LIMIT_KEYS:
        raise ValueError(f"check_limit({key!r}): unknown limit key")

    usage_field_map = {
        "campaigns": "campaigns_active",
        "leads_per_month": "leads_this_month",
        "emails_per_month": "emails_this_month",
        "tokens_per_month": "tokens_this_month",
    }

    def _guard(
        workspace: NexusWorkspace = Depends(trial_guard),
    ) -> NexusWorkspace:
        # DISABLED — plan-quota gating is turned off for all of Nexus.
        # Every limit check now passes through. Signatures preserved so
        # existing `Depends(check_limit("..."))` wiring across routers
        # stays valid. To re-enable, restore the limit comparison +
        # 402 raise below.
        return workspace

    return _guard


def increment_usage(
    db: Session,
    workspace_id: int,
    field: str,
    delta: int = 1,
) -> None:
    """Atomic-ish counter bump on workspace.usage. Mirrors
    `incrementUsage` in planEnforcement.js. Uses Postgres `jsonb_set`
    so concurrent calls don't trample each other's JSON updates.

    Caller is responsible for picking a valid usage field
    (one of: campaigns_active, leads_this_month, emails_this_month,
    tokens_this_month).
    """
    from sqlalchemy import text as _text

    db.execute(
        _text(
            """
            UPDATE nexus_workspaces
            SET usage = jsonb_set(
                COALESCE(usage, '{}'::jsonb),
                ARRAY[:field],
                to_jsonb(
                    COALESCE((usage ->> :field)::int, 0) + :delta
                ),
                true
            ),
            updated_at = CURRENT_TIMESTAMP
            WHERE id = :wid
            """
        ),
        {"field": field, "delta": delta, "wid": workspace_id},
    )
    db.commit()
