"""Team Members — invite-based flow (Phase 1b).

Admin creates an invite (email + assigned brand). The invite sends an email
with a single-use link. The invitee clicks through to /accept-invite,
sets their password + name, and is auto-logged-in.

Seat cap (team_seats quota) counts both accepted members AND currently-
pending invites so a rush of invites can't oversubscribe the plan.
"""
from datetime import datetime, timedelta
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from typing import Optional

from core.auth import get_password_hash, create_access_token
from core.database import get_db
from core.config import FRONTEND_URL, FRONTEND_URLS
from models import User, TeamInvite
from routers.auth import get_current_user
from services.email_service import email_service
from services.plan_service import (
    check_feature, check_quota, get_team_seat_count, get_user_plan_config,
)
from services.team_service import is_team_member

router = APIRouter()

INVITE_TTL_DAYS = 7


# ----------------------------- Schemas -----------------------------

class InviteCreate(BaseModel):
    email: EmailStr
    # Invite type: 'team' (member inherits Master's plan; default) or
    # 'franchise' (invitee will pick own plan + own Stripe subscription
    # on accept). Franchise invites gate on the Master being on Agency+
    # and against a separate franchise_seats quota.
    invite_type: Optional[str] = "team"
    # Role to grant the invitee — validated server-side against the inviter's
    # grantable set (Master Admin → admin|user, Admin → user only). Defaults to
    # the least-privileged role.
    role: Optional[str] = "user"
    # Admin-provided label so they can identify pending rows in the Team
    # list (e.g. "Alice — NY branch lead"). Optional; suggested as the new
    # member's full_name on acceptance but overridable.
    invited_name: Optional[str] = None
    # Accept either a single id (legacy client) or a list (new multi-brand).
    # At least one is required; the server stores both (list authoritative).
    assigned_dna_product_id: Optional[str] = None
    assigned_dna_product_ids: Optional[list[str]] = None
    connection_ids: Optional[list[int]] = None  # SocialAccount.id to pre-assign
    # Optional profile — captured at invite time, copied to User on accept.
    # Enables analytics filtering by external company + region.
    member_company_name: Optional[str] = None
    country: Optional[str] = None          # ISO-2
    country_name: Optional[str] = None     # Display snapshot
    state: Optional[str] = None            # ISO-2 subdivision
    state_name: Optional[str] = None       # Display snapshot
    city: Optional[str] = None
    pin_code: Optional[str] = None


class InviteAccept(BaseModel):
    password: str
    full_name: Optional[str] = None


class MemberUpdate(BaseModel):
    # Identity / access
    full_name: Optional[str] = None
    role: Optional[str] = None  # change a member's role — Master Admin only
    assigned_dna_product_id: Optional[str] = None
    assigned_dna_product_ids: Optional[list[str]] = None
    disabled: Optional[bool] = None
    # Profile (all optional — admin can edit any subset)
    member_company_name: Optional[str] = None
    country: Optional[str] = None
    country_name: Optional[str] = None
    state: Optional[str] = None
    state_name: Optional[str] = None
    city: Optional[str] = None
    pin_code: Optional[str] = None


# ----------------------------- Helpers -----------------------------

def _require_admin(current_user: User) -> None:
    """Gate for team-management endpoints. Master Admin and Admin may manage
    members; a read-only User may not. (3-role RBAC: anyone with a non-empty
    grantable set can manage someone below them.)"""
    from core.roles import grantable_roles
    if not grantable_roles(getattr(current_user, "role", None)):
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to manage team members.",
        )


def _root_owner_id(current_user: User) -> int:
    """The Master Admin (workspace owner) whose roster this user manages. The
    hierarchy is flat — every member chains directly to the Master — so an
    Admin manages the SAME roster as the Master, not a personal sub-team."""
    return getattr(current_user, "team_owner_id", None) or current_user.id


def _authorize_target(current_user: User, member: User) -> None:
    """A manager may only act on members BELOW their role: Master Admin → any
    Admin/User; Admin → Users only. Blocks an Admin from disabling/removing/
    editing another Admin (or themselves) or the Master.

    Franchisee exception: franchisees have role='master_admin' (they own
    their own workspace), so the role-grantability check rejects them
    even for their own brand owner. But the Master of the franchise
    network legitimately needs to edit their assigned brands, boot them,
    etc. Allow when the target's franchise_of_id points at the caller."""
    from core.roles import grantable_roles, normalize_role
    caller_id = getattr(current_user, "id", None)
    if (
        getattr(member, "franchise_of_id", None)
        and caller_id
        and int(member.franchise_of_id) == int(caller_id)
    ):
        return
    if normalize_role(getattr(member, "role", None)) not in grantable_roles(
        getattr(current_user, "role", None)
    ):
        raise HTTPException(
            status_code=403,
            detail="You can only manage members below your role (Admins manage Users only).",
        )


def _require_can_grant(current_user: User, requested_role: str) -> str:
    """Authorize an INVITE by the 3-role hierarchy: Master Admin may grant
    admin|user; Admin may grant user only; User may grant nothing. Returns the
    normalized role to store on the invite (default `user`)."""
    from core.roles import grantable_roles, normalize_role, USER
    grantable = grantable_roles(current_user.role)
    if not grantable:
        raise HTTPException(
            status_code=403, detail="Your role cannot add members."
        )
    want = normalize_role(requested_role or USER)
    if want not in grantable:
        raise HTTPException(
            status_code=403,
            detail=f"You can only add: {', '.join(sorted(grantable))}.",
        )
    return want


def _validate_dna_product(admin: User, product_id: str) -> dict:
    products = (admin.business_dna or {}).get("products") or {}
    product = products.get(product_id)
    if not product:
        raise HTTPException(
            status_code=400,
            detail=f"DNA product '{product_id}' not found on your account.",
        )
    return product


def _coerce_dna_id_list(single: Optional[str], many: Optional[list[str]]) -> list[str]:
    """Normalise incoming single-id + list-id inputs into a deduped list.

    Both fields accepted for back-compat. If both are given, the list wins.
    Empty list raises — a member must be assigned at least one brand."""
    if many:
        ids = [x for x in many if x]
    elif single:
        ids = [single]
    else:
        ids = []
    # Dedupe preserving order
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _validate_dna_product_ids(admin: User, ids: list[str]) -> None:
    products = (admin.business_dna or {}).get("products") or {}
    missing = [i for i in ids if i not in products]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"DNA product(s) not found on your account: {', '.join(missing)}",
        )


def _pending_invites_for_admin(db: Session, admin_id: int):
    """Active invites: not used, not expired."""
    now = datetime.utcnow()
    return (
        db.query(TeamInvite)
        .filter(
            TeamInvite.invited_by == admin_id,
            TeamInvite.used_at.is_(None),
            (TeamInvite.expires_at.is_(None)) | (TeamInvite.expires_at > now),
        )
        .order_by(TeamInvite.created_at.desc())
        .all()
    )


def _seat_usage(db: Session, admin_id: int) -> tuple[int, int]:
    """Return (used_seats, cap) including pending invites."""
    members = get_team_seat_count(db, admin_id)
    pending = len(_pending_invites_for_admin(db, admin_id))
    return members + pending, get_user_plan_config(
        db.query(User).filter(User.id == admin_id).first()
    )["quotas"].get("team_seats", 0)


def _assert_seat_available(db: Session, admin: User) -> None:
    used, cap = _seat_usage(db, admin.id)
    if used >= cap:
        raise HTTPException(
            status_code=403,
            detail=f"Plan seat limit reached ({used}/{cap}). Cancel a pending invite or upgrade.",
        )


def _franchise_seat_usage(db: Session, master_id: int) -> tuple[int, int]:
    """Return (used_franchise_seats, cap). Counts accepted franchisees +
    pending franchise invites."""
    accepted = db.query(User).filter(
        User.franchise_of_id == master_id
    ).count()
    now = datetime.utcnow()
    pending = db.query(TeamInvite).filter(
        TeamInvite.invited_by == master_id,
        TeamInvite.invite_type == "franchise",
        TeamInvite.used_at.is_(None),
        TeamInvite.expires_at > now,
    ).count()
    cap = get_user_plan_config(
        db.query(User).filter(User.id == master_id).first()
    )["quotas"].get("franchise_seats", 0)
    return accepted + pending, cap


def _assert_franchise_seat_available(db: Session, master: User) -> None:
    used, cap = _franchise_seat_usage(db, master.id)
    if used >= cap:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Franchise seat limit reached ({used}/{cap}). "
                f"Revoke a pending invite or upgrade to Enterprise."
            ),
        )


def _frontend_base() -> str:
    """Pick the first configured frontend origin for the invite link.

    FRONTEND_URL is a single string; FRONTEND_URLS is a list. Prefer the list
    if present (prod may have multiple). Fall back to localhost for dev.
    """
    if FRONTEND_URLS and len(FRONTEND_URLS) > 0:
        return FRONTEND_URLS[0].rstrip("/")
    if FRONTEND_URL:
        return FRONTEND_URL.rstrip("/")
    return "http://localhost:5173"


def _brand_summary(ids: list[str], products: dict) -> list[dict]:
    """Turn a list of product_ids into [{id, name}] tuples for the UI."""
    out = []
    for pid in ids or []:
        p = products.get(pid) or {}
        out.append({
            "id": pid,
            "name": p.get("product_name") or p.get("company_name") or pid,
        })
    return out


def _serialize_member(m: User, products: dict) -> dict:
    from services.team_service import get_member_assigned_dna_ids
    ids = get_member_assigned_dna_ids(m)
    brands = _brand_summary(ids, products)
    primary = brands[0]["name"] if brands else None
    # A user with franchise_of_id set is a franchisee — same row shape,
    # just tagged as 'franchise' so the UI can badge them and any
    # user_type filter can distinguish them from team members.
    is_franch = bool(getattr(m, "franchise_of_id", None))
    return {
        "id": m.id,
        "kind": "franchise" if is_franch else "member",
        "user_type": "franchise" if is_franch else "team",
        "email": m.email,
        "full_name": m.full_name,
        "role": getattr(m, "role", None) or "user",
        "pricing_plan": getattr(m, "pricing_plan", None),
        "assigned_dna_product_id": m.assigned_dna_product_id,
        "assigned_dna_product_ids": ids,
        "assigned_brands": brands,
        "assigned_brand_name": primary,   # legacy single-brand display
        "disabled": bool(m.disabled),
        "status": "disabled" if m.disabled else "active",
        # Profile fields (present on team-member rows; may be NULL)
        "member_company_name": m.member_company_name,
        "country": m.country,
        "country_name": m.country_name,
        "state": m.state,
        "state_name": m.state_name,
        "city": m.city,
        "pin_code": m.pin_code,
        "created_at": m.created_at.isoformat() + "Z" if getattr(m, "created_at", None) else None,
    }


def _serialize_invite(inv: TeamInvite, products: dict) -> dict:
    ids = list(getattr(inv, "assigned_dna_product_ids", None) or (
        [inv.assigned_dna_product_id] if inv.assigned_dna_product_id else []
    ))
    brands = _brand_summary(ids, products)
    primary = brands[0]["name"] if brands else None
    return {
        "id": inv.id,
        "kind": "invite",
        "email": inv.email,
        "full_name": inv.invited_name,     # admin-provided identifier
        "invited_name": inv.invited_name,
        "role": getattr(inv, "role", None) or "user",
        "assigned_dna_product_id": inv.assigned_dna_product_id,
        "assigned_dna_product_ids": ids,
        "assigned_brands": brands,
        "assigned_brand_name": primary,
        "disabled": False,
        "status": "pending",
        # Profile snapshot set by admin at invite time (may be NULL)
        "member_company_name": getattr(inv, "member_company_name", None),
        "country": getattr(inv, "country", None),
        "country_name": getattr(inv, "country_name", None),
        "state": getattr(inv, "state", None),
        "state_name": getattr(inv, "state_name", None),
        "city": getattr(inv, "city", None),
        "pin_code": getattr(inv, "pin_code", None),
        "expires_at": inv.expires_at.isoformat() + "Z" if inv.expires_at else None,
        "created_at": inv.created_at.isoformat() + "Z" if inv.created_at else None,
    }


# ----------------------------- Endpoints -----------------------------

@router.get("/team/members")
async def list_members_and_invites(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Combined list of accepted members + pending invites. Admin only.
    Returns rows with a `kind` discriminator so the UI can style them."""
    _require_admin(current_user)
    check_feature(current_user, "teams", db=db)

    # Flat hierarchy: an Admin manages the MASTER's roster, not a personal one.
    # Resolve the owner so the roster, pending invites, brand list and seat cap
    # all come from the Master Admin's account (the Admin's own business_dna is
    # typically empty).
    owner_id = _root_owner_id(current_user)
    owner = db.query(User).filter(User.id == owner_id).first() or current_user

    members = (
        db.query(User)
        .filter(User.team_owner_id == owner_id)
        .order_by(User.id.asc())
        .all()
    )
    # Franchisees are shown in the same roster with a distinguishing
    # `kind='franchise'` field so the UI can badge them. They differ
    # from team members only in: (1) they pay their own subscription,
    # (2) they connect their own social accounts. Everything else
    # (Master's brand DNA, appearing in analytics rollups, being
    # manageable from the Team page) is the same.
    franchisees = (
        db.query(User)
        .filter(User.franchise_of_id == owner_id)
        .order_by(User.id.asc())
        .all()
    )
    # Team-wide pending invites: created by the Master OR any member (an Admin
    # may have invited a User), not just the current viewer.
    _now = datetime.utcnow()
    _team_ids = [owner_id] + [m.id for m in members] + [f.id for f in franchisees]
    invites = (
        db.query(TeamInvite)
        .filter(
            TeamInvite.invited_by.in_(_team_ids),
            TeamInvite.used_at.is_(None),
            (TeamInvite.expires_at.is_(None)) | (TeamInvite.expires_at > _now),
        )
        .order_by(TeamInvite.created_at.desc())
        .all()
    )

    products = (owner.business_dna or {}).get("products") or {}
    cap = get_user_plan_config(owner)["quotas"].get("team_seats", 0)
    used_seats = len([m for m in members if not m.disabled]) + len(invites)

    return {
        "members": [
            *[_serialize_invite(i, products) for i in invites],
            *[_serialize_member(m, products) for m in members],
            # Franchisees appear alongside team members with kind='franchise'
            # so the UI can render a badge. Their subscription is separate
            # from Master's, but they share the brand roster.
            *[_serialize_member(f, products) for f in franchisees],
        ],
        "seats": {"used": used_seats, "cap": cap},
        "available_dna_products": [
            {"product_id": pid, "product_name": p.get("product_name") or p.get("company_name")}
            for pid, p in products.items()
        ],
    }


@router.post("/team/invites")
async def create_invite(
    body: InviteCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Normalize + validate invite_type early so branches below are clean.
    invite_type = (body.invite_type or "team").strip().lower()
    if invite_type not in ("team", "franchise"):
        raise HTTPException(
            status_code=400,
            detail="invite_type must be 'team' or 'franchise'.",
        )
    is_franchise = invite_type == "franchise"

    # Flat hierarchy: brands, seats and connections all live on the Master's
    # account, so resolve the owner and validate against it (an Admin's own
    # business_dna is empty).
    owner_id = _root_owner_id(current_user)
    owner = db.query(User).filter(User.id == owner_id).first() or current_user

    if is_franchise:
        # Franchise-specific validation:
        #   1. Master must be on a plan with the franchise feature enabled.
        #   2. Only the Master themselves can send franchise invites, not
        #      an Admin under them (franchisees affiliate directly to the
        #      brand-owning Master).
        #   3. Master cannot themselves be a franchisee (single-level only).
        #   4. Role must be 'master_admin' — franchisee is master of own space.
        #   5. Separate franchise_seats quota (not team_seats).
        check_feature(current_user, "franchise", db=db)
        if int(current_user.id) != int(owner_id):
            raise HTTPException(
                status_code=403,
                detail="Only the workspace owner can invite franchisees.",
            )
        if getattr(current_user, "franchise_of_id", None):
            raise HTTPException(
                status_code=403,
                detail="Franchisees cannot invite other franchisees.",
            )
        granted_role = "master_admin"
        _assert_franchise_seat_available(db, owner)
    else:
        granted_role = _require_can_grant(current_user, body.role)
        check_feature(current_user, "teams", db=db)
        _assert_seat_available(db, owner)

    # Accept single-id OR list; require at least one; validate all against
    # the OWNER's current business_dna.products.
    dna_ids = _coerce_dna_id_list(body.assigned_dna_product_id, body.assigned_dna_product_ids)
    if not dna_ids:
        raise HTTPException(status_code=400, detail="At least one brand must be assigned.")
    _validate_dna_product_ids(owner, dna_ids)

    # Block if email already belongs to ANY user (admin, member of someone else,
    # or a standalone account). Avoid creating a conflicting second identity.
    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="That email is already registered.")

    # Block if there's already an active pending invite for the same email
    # from this admin — force them to resend/revoke the old one instead.
    now = datetime.utcnow()
    existing_invite = (
        db.query(TeamInvite)
        .filter(
            TeamInvite.email == body.email,
            TeamInvite.invited_by == current_user.id,
            TeamInvite.used_at.is_(None),
            TeamInvite.expires_at > now,
        )
        .first()
    )
    if existing_invite:
        raise HTTPException(
            status_code=400,
            detail="An active invite already exists for that email. Resend or revoke it first.",
        )

    # Validate connection ids (if provided) — must be admin-owned accounts.
    conn_ids = body.connection_ids or []
    if conn_ids:
        from models import SocialAccount
        owned = {
            int(r[0]) for r in db.query(SocialAccount.id).filter(
                SocialAccount.id.in_(conn_ids),
                SocialAccount.user_id == owner_id,
            ).all()
        }
        missing = [i for i in set(conn_ids) if i not in owned]
        if missing:
            # Spell out which ids failed so the frontend can tell the admin
            # exactly what went wrong instead of the generic "not yours".
            raise HTTPException(
                status_code=400,
                detail=f"Connection id(s) not yours or missing: {missing}. "
                       f"Refresh the invite modal and re-pick connections.",
            )

    def _opt_str(val):
        """Treat empty / whitespace-only strings as NULL."""
        if val is None:
            return None
        s = str(val).strip()
        return s or None

    token = secrets.token_urlsafe(32)
    invite = TeamInvite(
        token=token,
        email=body.email,
        invite_type=invite_type,
        role=granted_role,  # validated against the inviter's grantable set
        invited_name=(body.invited_name or "").strip() or None,
        invited_by=current_user.id,
        # Mirror primary id into single-value column; authoritative list alongside.
        assigned_dna_product_id=dna_ids[0],
        assigned_dna_product_ids=dna_ids,
        expires_at=now + timedelta(days=INVITE_TTL_DAYS),
        assigned_connection_ids=list(set(conn_ids)) if conn_ids else None,
        # Profile snapshot (all optional). Whitespace-only strings normalised
        # to NULL so analytics filters aren't polluted with blanks.
        member_company_name=_opt_str(body.member_company_name),
        country=_opt_str(body.country),
        country_name=_opt_str(body.country_name),
        state=_opt_str(body.state),
        state_name=_opt_str(body.state_name),
        city=_opt_str(body.city),
        pin_code=_opt_str(body.pin_code),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)

    # Send email. Non-blocking in the sense that we commit the invite first —
    # the admin can always resend from the UI if delivery fails.
    products = (owner.business_dna or {}).get("products") or {}
    brand = products.get(body.assigned_dna_product_id) or {}
    accept_url = f"{_frontend_base()}/accept-invite?token={token}"
    email_ok = email_service.send_team_invite_email(
        recipient_email=body.email,
        inviter_name=current_user.full_name or current_user.email,
        company_name=owner.company_name
            or (owner.business_dna or {}).get("company_name"),
        brand_name=brand.get("product_name") or brand.get("company_name"),
        accept_url=accept_url,
    )

    return {
        "ok": True,
        "invite": _serialize_invite(invite, products),
        "email_sent": email_ok,
        "accept_url": accept_url,  # returned so admin can copy if email fails
    }


@router.post("/team/invites/{invite_id}/resend")
async def resend_invite(
    invite_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    check_feature(current_user, "teams", db=db)

    owner_id = _root_owner_id(current_user)
    owner = db.query(User).filter(User.id == owner_id).first() or current_user
    _team_ids = [owner_id] + [r[0] for r in db.query(User.id).filter(User.team_owner_id == owner_id).all()]
    invite = (
        db.query(TeamInvite)
        .filter(TeamInvite.id == invite_id, TeamInvite.invited_by.in_(_team_ids))
        .first()
    )
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.used_at is not None:
        raise HTTPException(status_code=400, detail="Invite has already been accepted.")

    # Reset expiry + rotate token so old link stops working.
    invite.token = secrets.token_urlsafe(32)
    invite.expires_at = datetime.utcnow() + timedelta(days=INVITE_TTL_DAYS)
    db.commit()

    products = (owner.business_dna or {}).get("products") or {}
    brand = products.get(invite.assigned_dna_product_id) or {}
    accept_url = f"{_frontend_base()}/accept-invite?token={invite.token}"
    email_ok = email_service.send_team_invite_email(
        recipient_email=invite.email,
        inviter_name=current_user.full_name or current_user.email,
        company_name=owner.company_name
            or (owner.business_dna or {}).get("company_name"),
        brand_name=brand.get("product_name") or brand.get("company_name"),
        accept_url=accept_url,
    )
    return {"ok": True, "email_sent": email_ok, "accept_url": accept_url}


@router.delete("/team/invites/{invite_id}")
async def revoke_invite(
    invite_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    _owner_id = _root_owner_id(current_user)
    _team_ids = [_owner_id] + [r[0] for r in db.query(User.id).filter(User.team_owner_id == _owner_id).all()]
    invite = (
        db.query(TeamInvite)
        .filter(TeamInvite.id == invite_id, TeamInvite.invited_by.in_(_team_ids))
        .first()
    )
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.used_at is not None:
        raise HTTPException(status_code=400, detail="Invite already accepted; disable the member instead.")
    db.delete(invite)
    db.commit()
    return {"ok": True}


@router.put("/team/members/{member_id}")
async def update_member(
    member_id: int,
    body: MemberUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    check_feature(current_user, "teams", db=db)

    _owner = _root_owner_id(current_user)
    # Match either a team member (team_owner_id) OR a franchisee
    # (franchise_of_id) under this Master. The roster is one unified
    # list on the UI, so lookup must accept both.
    member = db.query(User).filter(
        User.id == member_id,
        (User.team_owner_id == _owner) | (User.franchise_of_id == _owner),
    ).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    _authorize_target(current_user, member)  # Admin → Users only; Master → any below

    # Brands + seats belong to the Master — validate against the owner so an
    # Admin (empty business_dna) can still reassign brands / re-enable seats.
    owner = db.query(User).filter(User.id == _root_owner_id(current_user)).first() or current_user

    # Brand reassign — accepts single, list, or both.
    if body.assigned_dna_product_id is not None or body.assigned_dna_product_ids is not None:
        new_ids = _coerce_dna_id_list(body.assigned_dna_product_id, body.assigned_dna_product_ids)
        if not new_ids:
            raise HTTPException(status_code=400, detail="A member must be assigned at least one brand.")
        _validate_dna_product_ids(owner, new_ids)
        member.assigned_dna_product_id = new_ids[0]
        member.assigned_dna_product_ids = new_ids
    if body.disabled is not None:
        if member.disabled and not body.disabled:
            _assert_seat_available(db, owner)
        member.disabled = bool(body.disabled)

    # Role change — Master Admin only, and only to a role they may grant
    # (admin | user). Never changes the owner/Master Admin's own role.
    if body.role is not None:
        from core.roles import MASTER_ADMIN, grantable_roles, normalize_role
        if normalize_role(current_user.role) != MASTER_ADMIN:
            raise HTTPException(status_code=403, detail="Only the Master Admin can change a member's role.")
        new_role = normalize_role(body.role)
        if new_role not in grantable_roles(current_user.role):
            raise HTTPException(status_code=400, detail="Invalid role.")
        member.role = new_role

    # Profile edits — any field provided (even empty string) is persisted.
    # Empty/whitespace strings are normalised to NULL so filters stay clean.
    def _opt_str(val):
        if val is None:
            return "__UNCHANGED__"  # sentinel: field wasn't sent
        s = str(val).strip()
        return s or None

    for _field in (
        "full_name", "member_company_name",
        "country", "country_name",
        "state", "state_name",
        "city", "pin_code",
    ):
        new_val = _opt_str(getattr(body, _field))
        if new_val != "__UNCHANGED__":
            setattr(member, _field, new_val)

    db.commit()
    db.refresh(member)
    products = (owner.business_dna or {}).get("products") or {}
    return _serialize_member(member, products)


@router.delete("/team/members/{member_id}")
async def disable_member(
    member_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Soft-remove — flips `disabled=True` so the account can no longer log
    in but their past content (posts, drafts, scheduled) stays intact for
    historical analytics. Applies uniformly to team members AND franchisees;
    for franchisees this blocks login (and therefore blocks reading the
    Master's brand DNA) but keeps the franchise link intact so an Enable
    later restores full access. Use POST /team/members/{id}/remove to
    fully boot a franchisee from the network (unlink).
    """
    _require_admin(current_user)
    check_feature(current_user, "teams", db=db)

    _owner = _root_owner_id(current_user)
    member = db.query(User).filter(
        User.id == member_id,
        (User.team_owner_id == _owner) | (User.franchise_of_id == _owner),
    ).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    _authorize_target(current_user, member)  # Admin → Users only; Master → any below
    member.disabled = True
    db.commit()
    return {"ok": True, "id": member.id, "disabled": True}


@router.get("/team/filter-options")
async def team_filter_options(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rich filter data for the Analytics page cascading filter.

    Returns:
      - `members` — full roster of active team members with their company,
        brands, country/state/city/pin. The frontend uses this to cascade:
        picking a company narrows the available brands, picking a brand
        narrows the available members, etc. — all client-side, no extra
        round-trips.
      - `brands` — every brand defined on the admin's business_dna.products.
      - `companies / countries / states / cities / pin_codes` — distinct
        values across members (sorted, deduped, NULL-filtered) kept for
        back-compat with anything still reading the flat shape.
    """
    _require_admin(current_user)

    members_q = db.query(User).filter(
        User.team_owner_id == _root_owner_id(current_user),
        User.disabled == False,  # noqa: E712 — SQL literal FALSE
    ).all()

    def _brands_of(m: User) -> list[str]:
        ids = list(m.assigned_dna_product_ids or [])
        if not ids and m.assigned_dna_product_id:
            ids = [m.assigned_dna_product_id]
        return ids

    members = [{
        "id": m.id,
        "email": m.email,
        "full_name": m.full_name,
        "company": m.member_company_name,
        "brands": _brands_of(m),
        "country": m.country,
        "country_name": m.country_name,
        "state": m.state,
        "state_name": m.state_name,
        "city": m.city,
        "pin_code": m.pin_code,
    } for m in members_q]

    _owner = db.query(User).filter(User.id == _root_owner_id(current_user)).first() or current_user
    products = (_owner.business_dna or {}).get("products") or {}
    brands = sorted(
        [
            {
                "id": pid,
                "name": p.get("product_name") or p.get("company_name") or pid,
            }
            for pid, p in products.items()
        ],
        key=lambda x: (x["name"] or "").lower(),
    )

    def _uniq(values):
        seen, out = set(), []
        for v in values:
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return sorted(out, key=lambda s: s.lower())

    def _uniq_pairs(pairs):
        seen, out = set(), []
        for iso, name in pairs:
            if not iso or iso in seen:
                continue
            seen.add(iso)
            out.append({"code": iso, "name": name or iso})
        return sorted(out, key=lambda x: (x["name"] or "").lower())

    # Include the admin's OWN company / location alongside the team members'.
    # Without this the "Companies" filter only listed team members' companies
    # and an admin couldn't see (or filter to) their own company in the list,
    # which felt like the admin was missing from their own dashboard.
    admin_company = (current_user.company_name or "").strip() or None
    admin_country = current_user.country
    admin_country_name = current_user.country_name
    admin_state = current_user.state
    admin_state_name = current_user.state_name
    admin_city = current_user.city
    admin_pin = current_user.pin_code

    return {
        "members": members,
        "brands": brands,
        "companies": _uniq([admin_company] + [m["company"] for m in members]),
        "countries": _uniq_pairs(
            [(admin_country, admin_country_name)] +
            [(m["country"], m["country_name"]) for m in members]
        ),
        "states":    _uniq_pairs(
            [(admin_state, admin_state_name)] +
            [(m["state"], m["state_name"]) for m in members]
        ),
        "cities":    _uniq([admin_city] + [m["city"] for m in members]),
        "pin_codes": _uniq([admin_pin] + [m["pin_code"] for m in members]),
    }


@router.post("/team/members/{member_id}/remove")
async def hard_remove_member(
    member_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Hard delete — permanently removes the team-member account and
    detaches their published/scheduled/drafts from them (content rows are
    kept for the admin's historical record, just orphaned). Irreversible;
    the admin must confirm in the UI before invoking this.

    Unlike the soft disable, any social accounts that were delegated to
    this member get their `assigned_to_user_id` cleared (back to the
    admin pool) so the seat freed up here is immediately usable.
    """
    _require_admin(current_user)
    check_feature(current_user, "teams", db=db)

    _owner = _root_owner_id(current_user)
    # Match either a team member (team_owner_id) OR a franchisee
    # (franchise_of_id) under this Master. The roster is one unified
    # list on the UI, so lookup must accept both.
    member = db.query(User).filter(
        User.id == member_id,
        (User.team_owner_id == _owner) | (User.franchise_of_id == _owner),
    ).first()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")
    _authorize_target(current_user, member)  # Admin → Users only; Master → any below

    # Franchisees own their account + subscription — hard-delete is not
    # available for them. "Remove" for a franchisee = unlink from the
    # franchise network (they become a standalone user, keep everything).
    if getattr(member, "franchise_of_id", None):
        member.franchise_of_id = None
        member.assigned_dna_product_id = None
        member.assigned_dna_product_ids = None
        db.commit()
        return {"ok": True, "id": member.id, "unlinked": True}

    # ----- FK cleanup (Postgres has no ON DELETE CASCADE on these refs) -----
    # Every table that has a `user_id` / `*_by` / `*_user_id` -> users.id FK
    # must be handled before db.delete(member) — otherwise Postgres throws
    # ForeignKeyViolation and the whole transaction rolls back. The intent
    # (per the docstring) is "content rows are kept for the admin's
    # historical record, just orphaned" — so we REASSIGN the member's data
    # to the admin (current_user.id) wherever it makes sense, NULL where
    # the column is nullable, and delete throw-away records.
    from models import (
        SocialAccount, PublishedPost, Draft, ScheduledPost,
        UserAnalyticsSnapshot, ReconnectRequest,
        TeamInvite, GeneratedCampaign, UserTemplate, GeneratedVideo,
    )

    admin_id = current_user.id

    # Social accounts — uniquely owned per member; both the ownership and
    # the assignment routes should fall back to the admin.
    db.query(SocialAccount).filter(
        SocialAccount.assigned_to_user_id == member.id
    ).update({SocialAccount.assigned_to_user_id: None}, synchronize_session=False)
    db.query(SocialAccount).filter(
        SocialAccount.user_id == member.id
    ).update({SocialAccount.user_id: admin_id}, synchronize_session=False)

    # Content rows — reassign to admin so they remain in the workspace.
    db.query(PublishedPost).filter(PublishedPost.user_id == member.id) \
        .update({PublishedPost.user_id: admin_id}, synchronize_session=False)
    db.query(Draft).filter(Draft.user_id == member.id) \
        .update({Draft.user_id: admin_id}, synchronize_session=False)
    db.query(ScheduledPost).filter(ScheduledPost.user_id == member.id) \
        .update({ScheduledPost.user_id: admin_id}, synchronize_session=False)
    db.query(ScheduledPost).filter(ScheduledPost.approved_by == member.id) \
        .update({ScheduledPost.approved_by: None}, synchronize_session=False)

    # Generated assets — reassign so admin can still see them.
    db.query(GeneratedCampaign).filter(GeneratedCampaign.user_id == member.id) \
        .update({GeneratedCampaign.user_id: admin_id}, synchronize_session=False)
    db.query(UserTemplate).filter(UserTemplate.user_id == member.id) \
        .update({UserTemplate.user_id: admin_id}, synchronize_session=False)
    db.query(GeneratedVideo).filter(GeneratedVideo.user_id == member.id) \
        .update({GeneratedVideo.user_id: admin_id}, synchronize_session=False)

    # Per-user metric history — reassign so workspace KPIs don't lose data.
    db.query(UserAnalyticsSnapshot).filter(UserAnalyticsSnapshot.user_id == member.id) \
        .update({UserAnalyticsSnapshot.user_id: admin_id}, synchronize_session=False)

    # Member-only ephemeral / request rows — safe to drop. (PendingOAuthSync
    # has no user_id column — its rows are keyed by state_or_token, so they
    # die naturally as their state token expires.)
    db.query(ReconnectRequest).filter(ReconnectRequest.requester_user_id == member.id).delete(synchronize_session=False)

    # Team invites the member ever sent (members usually can't, but guard anyway).
    db.query(TeamInvite).filter(TeamInvite.invited_by == member.id) \
        .update({TeamInvite.invited_by: admin_id}, synchronize_session=False)

    # NEXUS products created by this member are WORKSPACE assets, not personal
    # ones. nexus_products.user_id is ON DELETE CASCADE, so without this the
    # member's delete would cascade-delete their products → the campaigns that
    # reference those products get orphaned and vanish from the GTM Journey
    # (which INNER JOINs nexus_products). Reassign them to the ROOT account
    # owner (Master Admin) so the products — and all their campaigns/leads —
    # survive the member's removal. (nexus_user_profiles cascade-deletes by
    # design — that per-user row SHOULD go with the member.)
    from sqlalchemy import text as _text
    root_owner_id = getattr(current_user, "team_owner_id", None) or current_user.id
    db.execute(
        _text("UPDATE nexus_products SET user_id = :owner WHERE user_id = :member"),
        {"owner": root_owner_id, "member": member.id},
    )

    member_id_snapshot = member.id
    member_email = member.email
    db.delete(member)
    db.commit()
    return {"ok": True, "id": member_id_snapshot, "email": member_email, "removed": True}


# ---------- Public (unauthenticated) endpoints for the invitee ----------

@router.get("/team/invites/{token}")
async def read_invite(token: str, db: Session = Depends(get_db)):
    """Fetch invite context for the accept-invite landing page. No auth."""
    invite = db.query(TeamInvite).filter(TeamInvite.token == token).first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    now = datetime.utcnow()
    if invite.used_at is not None:
        raise HTTPException(status_code=410, detail="This invite has already been used.")
    if invite.expires_at and invite.expires_at < now:
        raise HTTPException(status_code=410, detail="This invite has expired. Ask your admin to resend.")

    admin = db.query(User).filter(User.id == invite.invited_by).first()
    products = (admin.business_dna or {}).get("products") or {} if admin else {}
    brand = products.get(invite.assigned_dna_product_id) or {}

    return {
        "email": invite.email,
        "invite_type": (getattr(invite, "invite_type", None) or "team"),
        "company_name": (admin.company_name if admin else None)
            or ((admin.business_dna or {}).get("company_name") if admin else None),
        "brand_name": brand.get("product_name") or brand.get("company_name"),
        "inviter_name": (admin.full_name if admin else None) or (admin.email if admin else None),
    }


@router.post("/team/invites/{token}/accept")
async def accept_invite(token: str, body: InviteAccept, db: Session = Depends(get_db)):
    """Complete signup. Creates the member user, marks invite used, returns JWT."""
    if not body.password or len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    invite = db.query(TeamInvite).filter(TeamInvite.token == token).first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite not found")
    now = datetime.utcnow()
    if invite.used_at is not None:
        raise HTTPException(status_code=410, detail="This invite has already been used.")
    if invite.expires_at and invite.expires_at < now:
        raise HTTPException(status_code=410, detail="This invite has expired.")

    # Race-check email uniqueness at accept-time (someone could have taken
    # the email between invite and acceptance).
    existing = db.query(User).filter(User.email == invite.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="That email was registered in the meantime.")

    admin = db.query(User).filter(User.id == invite.invited_by).first()
    if not admin:
        raise HTTPException(status_code=410, detail="Inviting admin account no longer exists.")

    # Flatten the hierarchy to the ROOT account owner (Master Admin). When an
    # Admin (who is themselves a team member) sends the invite, the new member
    # must still chain directly to the Master — single-hop plan/credit/workspace
    # resolution (get_plan_owner, get_current_workspace) assumes a flat tree.
    from services.team_service import get_plan_owner
    root_owner = get_plan_owner(db, admin)

    invite_dna_ids = list(
        getattr(invite, "assigned_dna_product_ids", None)
        or ([invite.assigned_dna_product_id] if invite.assigned_dna_product_id else [])
    )
    # If the invitee didn't supply a name, inherit the admin-provided
    # invited_name so the row isn't blank in the Team list.
    effective_full_name = (body.full_name or invite.invited_name or "").strip() or None

    invite_type = (getattr(invite, "invite_type", None) or "team").lower()
    is_franchise = invite_type == "franchise"

    if is_franchise:
        # Franchisee: independent operator affiliated to the Master's brand
        # but with their OWN plan + Stripe subscription. NOT set on User:
        #   - team_owner_id (that's for members who inherit Master's plan)
        #   - pricing_plan (must be picked at onboarding step 4)
        #   - onboarded (False → frontend redirects to plan picker)
        # Instead, franchise_of_id anchors them to the Master's brand so
        # DNA / analytics rollups still work.
        member = User(
            email=invite.email,
            hashed_password=get_password_hash(body.password),
            full_name=effective_full_name,
            role="master_admin",  # master of own workspace
            team_owner_id=None,
            franchise_of_id=root_owner.id,
            assigned_dna_product_id=invite_dna_ids[0] if invite_dna_ids else None,
            assigned_dna_product_ids=invite_dna_ids or None,
            pricing_plan="free",  # placeholder until they complete checkout
            onboarded=False,
            disabled=False,
            member_company_name=getattr(invite, "member_company_name", None),
            country=getattr(invite, "country", None),
            country_name=getattr(invite, "country_name", None),
            state=getattr(invite, "state", None),
            state_name=getattr(invite, "state_name", None),
            city=getattr(invite, "city", None),
            pin_code=getattr(invite, "pin_code", None),
        )
    else:
        member = User(
            email=invite.email,
            hashed_password=get_password_hash(body.password),
            full_name=effective_full_name,
            # Role granted at invite time (validated against the inviter). Older
            # invites without a role fall back to the least-privileged User.
            role=getattr(invite, "role", None) or "user",
            team_owner_id=root_owner.id,
            assigned_dna_product_id=invite_dna_ids[0] if invite_dna_ids else None,
            assigned_dna_product_ids=invite_dna_ids or None,
            pricing_plan=root_owner.pricing_plan,
            onboarded=True,
            disabled=False,
            # Propagate the profile snapshot the admin filled in at invite time
            # onto the new user. The invitee can still edit these later via
            # their own profile page; the admin can edit via PUT /team/members/{id}.
            member_company_name=getattr(invite, "member_company_name", None),
            country=getattr(invite, "country", None),
            country_name=getattr(invite, "country_name", None),
            state=getattr(invite, "state", None),
            state_name=getattr(invite, "state_name", None),
            city=getattr(invite, "city", None),
            pin_code=getattr(invite, "pin_code", None),
        )
    db.add(member)
    db.flush()  # need member.id for connection assignment below

    # Apply any pre-assigned connections from the invite row (team members
    # only — franchisees connect their OWN socials, not the Master's).
    if not is_franchise and invite.assigned_connection_ids:
        from models import SocialAccount
        accounts = db.query(SocialAccount).filter(
            SocialAccount.id.in_(invite.assigned_connection_ids),
            SocialAccount.user_id == admin.id,
        ).all()
        for acc in accounts:
            acc.assigned_to_user_id = member.id

    invite.used_at = now
    db.commit()
    db.refresh(member)

    access_token = create_access_token(data={"sub": member.email})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "is_new_user": is_franchise,  # franchisee still needs onboarding
        "needs_plan_selection": is_franchise,
        "invite_type": invite_type,
        "user_email": member.email,
    }
