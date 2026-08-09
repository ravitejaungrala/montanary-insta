"""Role-based access control — the single source of truth for the 3-role
hierarchy (Master Admin > Admin > User).

- **Master Admin** = workspace owner (a signup). Everything, incl. billing +
  adding Admins.
- **Admin** = operator. All the work (campaigns, posts, connectors) + add Users;
  no billing, can't add Admins.
- **User** = strictly view-only.

`users.role` holds one of these three values. Legacy values
(`admin`/`member`/`viewer`) map forward via LEGACY_TO_NEW — used by the rename
migration and by `require_nexus_role`'s legacy-arg translation so the ~30
existing nexus routers keep working unchanged.
"""
from __future__ import annotations

from typing import Iterable, Set

# ── role values ──────────────────────────────────────────────────────────────
MASTER_ADMIN = "master_admin"
ADMIN = "admin"
USER = "user"

ALL_ROLES = (MASTER_ADMIN, ADMIN, USER)
WRITE_ROLES: Set[str] = {MASTER_ADMIN, ADMIN}   # can perform work actions
BILLING_ROLES: Set[str] = {MASTER_ADMIN}        # billing / plan / buy credits

# Higher rank = more authority (for "can remove anyone below me" checks).
ROLE_RANK = {MASTER_ADMIN: 3, ADMIN: 2, USER: 1}

# ── permission matrix: action -> roles allowed ───────────────────────────────
PERMISSIONS = {
    # people management
    "add_admin": {MASTER_ADMIN},
    "add_user": WRITE_ROLES,
    "change_role": {MASTER_ADMIN},
    "remove_member": WRITE_ROLES,          # WHO they may remove → can_remove()
    # billing / plan / credits — Master Admin only
    "manage_billing": BILLING_ROLES,
    "buy_credits": BILLING_ROLES,
    "change_plan": BILLING_ROLES,
    # work actions — Master Admin + Admin
    "create_campaign": WRITE_ROLES,
    "run_campaign": WRITE_ROLES,
    "upload_leads": WRITE_ROLES,
    "connect_connector": WRITE_ROLES,
    "edit_sequence": WRITE_ROLES,
    "create_post": WRITE_ROLES,
    "edit_post": WRITE_ROLES,
    "schedule_post": WRITE_ROLES,
    "publish_post": WRITE_ROLES,
    "connect_social": WRITE_ROLES,
    "workspace_settings": WRITE_ROLES,
    "integrations": WRITE_ROLES,
}

# ── legacy → new mapping ─────────────────────────────────────────────────────
# Used by the rename migration AND by require_nexus_role's legacy translation.
LEGACY_TO_NEW = {"admin": MASTER_ADMIN, "member": ADMIN, "viewer": USER}

# UI labels (also mirrored on the frontend).
ROLE_LABELS = {MASTER_ADMIN: "Master Admin", ADMIN: "Admin", USER: "User"}


# ── helpers ──────────────────────────────────────────────────────────────────
def normalize_role(role: str | None) -> str:
    """Return a canonical new-vocab role. New values pass through. Only the
    UNAMBIGUOUS legacy values are mapped (`member`→admin, `viewer`→user); note
    legacy `admin` is NOT remapped here — it's identical to the new Admin value,
    and the boot migration is what promotes legacy owners (`admin` +
    team_owner_id IS NULL) to `master_admin` using context this function lacks.
    Unknown/empty → USER (safest, read-only)."""
    if role in ALL_ROLES:
        return role
    if role == "member":
        return ADMIN
    if role == "viewer":
        return USER
    return USER


def can(role: str | None, action: str) -> bool:
    """True if `role` may perform `action`."""
    return normalize_role(role) in PERMISSIONS.get(action, set())


def grantable_roles(adder_role: str | None) -> Set[str]:
    """Roles `adder_role` is allowed to grant when adding/inviting a member."""
    r = normalize_role(adder_role)
    if r == MASTER_ADMIN:
        return {ADMIN, USER}
    if r == ADMIN:
        return {USER}
    return set()


def can_remove(remover_role: str | None, target_role: str | None) -> bool:
    """Master Admin removes anyone below; Admin removes only Users; User none."""
    r, t = normalize_role(remover_role), normalize_role(target_role)
    if r == MASTER_ADMIN:
        return ROLE_RANK.get(t, 0) < ROLE_RANK[MASTER_ADMIN]
    if r == ADMIN:
        return t == USER
    return False


def expand_legacy_required(required: Iterable[str]) -> Set[str]:
    """Translate the role args passed to the legacy `require_nexus_role(...)`
    into the set of NEW roles that satisfy them — so existing callsites keep
    working after the rename:
      - legacy 'admin'  (write access)  -> {master_admin, admin}
      - legacy 'member'/'viewer' (any)  -> all roles
      - a new-vocab value passes through as itself.
    """
    out: Set[str] = set()
    for r in required:
        if r == "admin":
            out |= WRITE_ROLES
        elif r in ("member", "viewer"):
            out |= set(ALL_ROLES)
        elif r in ALL_ROLES:
            out.add(r)
    return out
