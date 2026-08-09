"""Plan & quota enforcement.

Team-members support (Phase 1): check_feature / check_quota always resolve
the effective plan owner first (admin for team members, self otherwise),
and quota usage counts span the whole team — a member publishing a post
debits the admin's monthly quota, and the admin seeing their own usage
already includes everything the team has done.
"""
from datetime import datetime
from sqlalchemy.orm import Session
from fastapi import HTTPException
from core.pricing import PLAN_CONFIG
from models import User, PublishedPost, ScheduledPost, SocialAccount, GeneratedCampaign
from services.team_service import get_plan_owner, get_team_scope_user_ids


def get_user_plan_config(user: User):
    return PLAN_CONFIG["enterprise"]


def get_monthly_post_count(db: Session, user_ids: list[int]) -> int:
    """Count posts published OR scheduled this month across the given users.
    Passed as a list so the admin's count naturally includes team members."""
    if not user_ids:
        return 0
    start_of_month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    published = db.query(PublishedPost).filter(
        PublishedPost.user_id.in_(user_ids),
        PublishedPost.created_at >= start_of_month,
    ).count()
    scheduled = db.query(ScheduledPost).filter(
        ScheduledPost.user_id.in_(user_ids),
        ScheduledPost.created_at >= start_of_month,
        ScheduledPost.status == "pending",
    ).count()
    return published + scheduled


def get_monthly_image_count(db: Session, user_ids: list[int]) -> int:
    if not user_ids:
        return 0
    start_of_month = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    campaigns = db.query(GeneratedCampaign).filter(
        GeneratedCampaign.user_id.in_(user_ids),
        GeneratedCampaign.created_at >= start_of_month,
    ).all()
    total = 0
    for c in campaigns:
        if isinstance(c.visuals, list):
            total += len(c.visuals)
    return total


def get_brand_count(user: User) -> int:
    """Brands = 1 (main company) + number of products in the DNA."""
    dna = user.business_dna or {}
    products = dna.get("products", {})
    return 1 + len(products)


def get_channel_count(db: Session, user_ids: list[int]) -> int:
    if not user_ids:
        return 0
    return db.query(SocialAccount).filter(SocialAccount.user_id.in_(user_ids)).count()


def get_team_seat_count(db: Session, admin_id: int) -> int:
    """Current active (not disabled) team members under this admin."""
    return db.query(User).filter(
        User.team_owner_id == admin_id,
        User.disabled.is_(False),
    ).count()


def check_quota(db: Session, user: User, metric: str, additional: int = 1):
    """Check the plan-owner's quota for `metric`. Team members resolve to admin.

    Usage is counted across the full team scope, so one admin + 3 members
    posting share the admin's monthly limit.
    """
    owner = get_plan_owner(db, user)
    config = get_user_plan_config(owner)
    limit = config["quotas"].get(metric, 0)

    scope_ids = get_team_scope_user_ids(db, owner)
    used = 0
    if metric == "posts":
        used = get_monthly_post_count(db, scope_ids)
    elif metric == "images":
        used = get_monthly_image_count(db, scope_ids)
    elif metric == "brands":
        # Brands = admin's DNA — team members don't have their own.
        used = get_brand_count(owner)
    elif metric == "channels":
        used = get_channel_count(db, scope_ids)
    elif metric == "team_seats":
        used = get_team_seat_count(db, owner.id)

    if (used + additional) > limit:
        raise HTTPException(
            status_code=403,
            detail=f"Plan limit exceeded for {metric} ({used}/{limit}). Please upgrade your plan."
        )
    return True


def has_feature(user: User, feature: str, db: Session | None = None) -> bool:
    """Feature check against the plan owner. Accepts optional db so the
    old call-sites (has_feature(user, 'canva')) keep working for standalone
    users without a DB lookup."""
    if db is not None:
        owner = get_plan_owner(db, user)
    else:
        owner = user
    config = get_user_plan_config(owner)
    return config["features"].get(feature, False)


def check_feature(user: User, feature: str, db: Session | None = None):
    if not has_feature(user, feature, db=db):
        raise HTTPException(
            status_code=403,
            detail=f"Feature '{feature}' is not available on your current plan. Please upgrade."
        )
    return True
