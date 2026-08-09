"""Team-members feature helpers (Phase 1).

Three helpers the rest of the app depends on:

    get_plan_owner(db, user)        — resolves to the admin whose plan
                                      gates this user. Members → admin;
                                      standalone users / admins → self.

    get_team_scope_user_ids(db, user) — list of user_ids a query should
                                        return rows for. Members see only
                                        themselves; admins see self + all
                                        their team members.

    get_member_effective_dna(db, user) — resolves a team member's assigned
                                         DNA by looking up the admin's
                                         business_dna["products"][id]. Raises
                                         if member has no assignment or the
                                         admin has removed the product.

Design notes:
  * Zero-overhead for standalone users: helpers short-circuit when
    team_owner_id is None.
  * We intentionally do NOT cache on the request because member <-> admin
    relationship can change mid-session (e.g. reassignment); a fresh read
    is cheap and correct.
  * Disabled team members still appear in the admin's team_scope so
    admin analytics keep showing their historical content. Login gating
    is handled separately in auth.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from models import User


def get_plan_owner(db: Session, user: User) -> User:
    """Return the admin whose plan governs this user (or the user themselves)."""
    if not getattr(user, "team_owner_id", None):
        return user
    admin = db.query(User).filter(User.id == user.team_owner_id).first()
    # Defensive: fall back to self if the team_owner was deleted. Should
    # never happen normally (we keep admins around), but don't 500 on it.
    return admin or user


def get_team_scope_user_ids(db: Session, user: User) -> list[int]:
    """IDs a query should include when acting as this user.

    Team members + franchisees see only themselves. Masters see themselves +
    every team member AND every franchisee under them (both types roll up
    into the Master's analytics / dashboards / roster)."""
    if getattr(user, "team_owner_id", None) or getattr(user, "franchise_of_id", None):
        return [int(user.id)]
    member_ids = [
        int(m.id)
        for m in db.query(User.id).filter(User.team_owner_id == user.id).all()
    ]
    franchisee_ids = [
        int(f.id)
        for f in db.query(User.id).filter(User.franchise_of_id == user.id).all()
    ]
    return [int(user.id), *member_ids, *franchisee_ids]


def get_member_effective_dna(db: Session, user: User) -> dict | None:
    """For a team member, return the first (primary) DNA dict assigned.

    Kept for back-compat with single-brand call sites. Use
    `get_member_assigned_dnas` when you need the full list.
    """
    dnas = get_member_assigned_dnas(db, user)
    return dnas[0] if dnas else None


def get_member_assigned_dna_ids(user: User) -> list[str]:
    """Authoritative list of DNA product ids this user may post under.
    Falls back to the legacy single column if the list column is empty."""
    ids = list(getattr(user, "assigned_dna_product_ids", None) or [])
    if ids:
        return [x for x in ids if x]
    single = getattr(user, "assigned_dna_product_id", None)
    return [single] if single else []


def get_member_assigned_dnas(db: Session, user: User) -> list[dict]:
    """Resolve each assigned DNA id to the admin's product dict."""
    if not getattr(user, "team_owner_id", None):
        return []
    ids = get_member_assigned_dna_ids(user)
    if not ids:
        return []
    admin = get_plan_owner(db, user)
    products = (admin.business_dna or {}).get("products") or {}
    return [products[pid] for pid in ids if pid in products]


def is_team_member(user: User) -> bool:
    return bool(getattr(user, "team_owner_id", None))


def is_franchisee(user: User) -> bool:
    """True if the user is a franchisee of some Master (own plan + own
    subscription, but brand-affiliated). Mutually exclusive with
    is_team_member."""
    return bool(getattr(user, "franchise_of_id", None))


def get_franchise_of(db: Session, user: User) -> User | None:
    """Return the Master a franchisee reports to, or None if not a
    franchisee. Unlike get_plan_owner, this does NOT return the user
    themselves for standalone users."""
    fid = getattr(user, "franchise_of_id", None)
    if not fid:
        return None
    return db.query(User).filter(User.id == fid).first()


def get_analytics_scope(db: Session, master: User) -> list[int]:
    """Master-side rollup scope: master + all team members + all
    franchisees. Used for aggregated analytics that span the whole
    brand network.

    Franchisees have their own plan/quota but they post under this
    Master's brand, so their content rolls up into Master's aggregated
    view.
    """
    team_ids = [
        int(r[0])
        for r in db.query(User.id).filter(User.team_owner_id == master.id).all()
    ]
    franchisee_ids = [
        int(r[0])
        for r in db.query(User.id).filter(User.franchise_of_id == master.id).all()
    ]
    return [int(master.id), *team_ids, *franchisee_ids]


def resolve_filtered_visible_ids(
    db: Session,
    user: User,
    *,
    member_user_ids: str | None = None,
    dna_product_ids: str | None = None,
    filter_company: str | None = None,
    filter_country: str | None = None,
    filter_state: str | None = None,
    filter_city: str | None = None,
    filter_pin_code: str | None = None,
    user_type: str | None = None,  # 'team' | 'franchise' | None (both)
) -> list[int]:
    """Shared filter resolver used by /scheduled, /posts/published, /drafts,
    /connections, and /analytics/* so every endpoint narrows by the same
    rules given the same query params.

    Admin with all filters empty → full team scope (self + all members).
    Admin with ANY filter → admin excluded, just the filtered member set.
    Team member → always just themselves (filters ignored).

    Returns the final list of user_ids rows should come from.
    """
    if is_team_member(user) or is_franchisee(user):
        return [int(user.id)]

    from sqlalchemy import func as _sqlfunc

    # Start with full team scope (Master + team members + franchisees)
    visible_ids = set(get_team_scope_user_ids(db, user))

    # User-type narrowing — 'team' → only team members, 'franchise' → only
    # franchisees. Master themselves is included in both slices so their
    # own posts still show up in the roll-up.
    if user_type == "team":
        team_ids = {int(u.id) for u in db.query(User.id).filter(User.team_owner_id == user.id).all()}
        visible_ids = visible_ids & (team_ids | {int(user.id)})
    elif user_type == "franchise":
        franch_ids = {int(u.id) for u in db.query(User.id).filter(User.franchise_of_id == user.id).all()}
        visible_ids = visible_ids & (franch_ids | {int(user.id)})

    # Profile filters (any of company/country/state/city/pin)
    profile_active = any([filter_company, filter_country, filter_state, filter_city, filter_pin_code])
    if profile_active:
        # Profile filter applies to the union of team members + franchisees
        # (both live under the Master).
        q = db.query(User.id).filter(
            (User.team_owner_id == user.id) | (User.franchise_of_id == user.id)
        )
        if filter_company:
            q = q.filter(_sqlfunc.lower(User.member_company_name) == filter_company.strip().lower())
        if filter_country:
            q = q.filter(User.country == filter_country.strip())
        if filter_state:
            q = q.filter(User.state == filter_state.strip())
        if filter_city:
            q = q.filter(_sqlfunc.lower(User.city) == filter_city.strip().lower())
        if filter_pin_code:
            q = q.filter(User.pin_code == filter_pin_code.strip())
        visible_ids = {int(r[0]) for r in q.all()}

    # Brand multi — narrow to admin + members assigned any of the brands.
    brand_ids: list[str] = []
    if dna_product_ids:
        brand_ids = [x.strip() for x in dna_product_ids.split(",") if x.strip()]
    if brand_ids:
        brand_members = db.query(User).filter(
            (User.team_owner_id == user.id) | (User.franchise_of_id == user.id)
        ).all()
        sel_set = set(brand_ids)
        brand_keep = set()
        for m in brand_members:
            mids = set(
                (m.assigned_dna_product_ids or [])
                or ([m.assigned_dna_product_id] if m.assigned_dna_product_id else [])
            )
            if mids & sel_set:
                brand_keep.add(int(m.id))
        # The admin themselves authors posts under the brands in their own
        # business_dna.products map — if any selected brand is one the admin
        # owns, include them in brand_keep. Without this a solo admin who
        # filters by their own brand would see zero rows (the previous
        # behaviour assumed only team-member-assignees author brand content,
        # which is wrong for solo admins and for admins who post under
        # multiple of their own brands).
        admin_products = ((user.business_dna or {}).get("products") or {})
        admin_brand_ids = set(admin_products.keys())
        # Also accept the Company-DNA sentinel and the No-DNA sentinel so the
        # filter UI can later add explicit "Company" / "None" entries.
        admin_brand_ids.add("__company__")
        if admin_brand_ids & sel_set:
            brand_keep.add(int(user.id))
        if profile_active:
            visible_ids = visible_ids & brand_keep
        else:
            visible_ids = brand_keep

    # Explicit member csv — final narrowing.
    if member_user_ids:
        try:
            requested = {int(x) for x in member_user_ids.split(",") if x.strip()}
        except Exception:
            requested = set()
        if requested:
            # Validate requested ids — admin can address themselves OR any
            # of their team members. Admin-only sentinel "<admin_id>" used
            # by the frontend when filter is cleared maps to admin-only.
            valid = {
                int(r[0]) for r in db.query(User.id).filter(
                    (User.id == user.id)
                    | (User.team_owner_id == user.id)
                    | (User.franchise_of_id == user.id),
                    User.id.in_(requested),
                ).all()
            }
            if valid:
                if profile_active or brand_ids:
                    visible_ids = visible_ids & valid
                else:
                    visible_ids = valid

    return sorted(visible_ids)


def resolve_member_publish_dna(user: User, requested_id: str | None) -> str | None:
    """Decide which dna_product_id a member's publish action is allowed to
    use. Rules:
      - Member must have at least one assigned brand; otherwise return None
        (the caller can still accept None — post lands untagged).
      - If `requested_id` is in their assigned list, honour it (they picked
        from the composer dropdown).
      - Otherwise fall back to their primary (first in list). This prevents
        tampered clients from posting under a brand they aren't assigned.
    """
    ids = get_member_assigned_dna_ids(user)
    if not ids:
        return None
    if requested_id and requested_id in ids:
        return requested_id
    return ids[0]
