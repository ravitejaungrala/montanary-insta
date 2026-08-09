"""Franchise model endpoints (Master → Franchisee).

Franchisees are independent operators affiliated to a Master's brand.
Unlike team members, they bring their own plan + Stripe subscription
and post on their own connected social accounts. This router exposes:

  - Roster:              GET  /franchise/roster
  - Aggregated analytics:GET  /franchise/analytics/aggregated
  - Individual analytics:GET  /franchise/{id}/analytics
  - Content library:     GET  /franchise/{id}/posts
  - Remove franchisee:   DELETE /franchise/{id}    (Master boots)
  - Leave network:       POST /franchise/leave     (franchisee self-exit)

Booting or leaving clears franchise_of_id (and brand DNA access). The
franchisee keeps their own plan, own content, own social accounts —
they simply become a standalone user.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import get_db
from models import User, PublishedPost, Draft, ScheduledPost
from routers.auth import get_current_user
from services.plan_service import check_feature

router = APIRouter()


# ----------------------------- Helpers -----------------------------

def _require_master(user: User) -> None:
    """Franchise management is Master-only. Admins-under-master cannot
    invite/boot franchisees (this differs from team invites, which
    Admins can send)."""
    if getattr(user, "franchise_of_id", None):
        raise HTTPException(
            status_code=403,
            detail="Franchisees cannot manage other franchisees.",
        )
    if getattr(user, "team_owner_id", None):
        raise HTTPException(
            status_code=403,
            detail="Only workspace owners can manage franchisees.",
        )


def _get_franchisee(db: Session, master: User, franchisee_id: int) -> User:
    row = db.query(User).filter(
        User.id == franchisee_id,
        User.franchise_of_id == master.id,
    ).first()
    if not row:
        raise HTTPException(
            status_code=404,
            detail="Franchisee not found in your network.",
        )
    return row


def _serialize_franchisee(f: User, master: User) -> dict:
    products = (master.business_dna or {}).get("products") or {}
    ids = list(getattr(f, "assigned_dna_product_ids", None) or [])
    brands = [
        {"id": pid, "name": (products.get(pid) or {}).get("product_name") or pid}
        for pid in ids
    ]
    return {
        "id": f.id,
        "email": f.email,
        "full_name": f.full_name,
        "pricing_plan": f.pricing_plan,
        "onboarded": bool(getattr(f, "onboarded", False)),
        "disabled": bool(getattr(f, "disabled", False)),
        "brands": brands,
        "member_company_name": f.member_company_name,
        "country": f.country,
        "country_name": f.country_name,
        "state": f.state,
        "state_name": f.state_name,
        "city": f.city,
    }


# ----------------------------- Endpoints -----------------------------

@router.get("/franchise/roster")
async def get_franchise_roster(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all franchisees under the current Master + seat usage."""
    _require_master(current_user)
    check_feature(current_user, "franchise", db=db)

    from core.pricing import PLAN_CONFIG
    plan = PLAN_CONFIG.get(current_user.pricing_plan or "free") or PLAN_CONFIG["free"]
    cap = int(plan["quotas"].get("franchise_seats", 0))

    rows = (
        db.query(User)
        .filter(User.franchise_of_id == current_user.id)
        .order_by(User.id.asc())
        .all()
    )
    return {
        "franchisees": [_serialize_franchisee(f, current_user) for f in rows],
        "seat_usage": {"used": len(rows), "cap": cap},
    }


@router.get("/franchise/analytics/aggregated")
async def get_franchise_analytics_aggregated(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cross-franchisee rollup: totals across every franchisee's content."""
    _require_master(current_user)
    check_feature(current_user, "franchise", db=db)

    franchisee_ids = [
        int(r[0])
        for r in db.query(User.id).filter(User.franchise_of_id == current_user.id).all()
    ]
    if not franchisee_ids:
        return {
            "franchisee_count": 0,
            "total_published": 0,
            "total_drafts": 0,
            "total_scheduled": 0,
        }

    total_published = (
        db.query(func.count(PublishedPost.id))
        .filter(PublishedPost.user_id.in_(franchisee_ids))
        .scalar()
        or 0
    )
    total_drafts = (
        db.query(func.count(Draft.id))
        .filter(Draft.user_id.in_(franchisee_ids))
        .scalar()
        or 0
    )
    total_scheduled = (
        db.query(func.count(ScheduledPost.id))
        .filter(ScheduledPost.user_id.in_(franchisee_ids))
        .scalar()
        or 0
    )
    return {
        "franchisee_count": len(franchisee_ids),
        "total_published": int(total_published),
        "total_drafts": int(total_drafts),
        "total_scheduled": int(total_scheduled),
    }


@router.get("/franchise/{franchisee_id}/analytics")
async def get_franchise_analytics_individual(
    franchisee_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Individual franchisee's post counts by kind."""
    _require_master(current_user)
    f = _get_franchisee(db, current_user, franchisee_id)

    published = db.query(func.count(PublishedPost.id)).filter(
        PublishedPost.user_id == f.id
    ).scalar() or 0
    drafts = db.query(func.count(Draft.id)).filter(
        Draft.user_id == f.id
    ).scalar() or 0
    scheduled = db.query(func.count(ScheduledPost.id)).filter(
        ScheduledPost.user_id == f.id
    ).scalar() or 0

    return {
        "franchisee": _serialize_franchisee(f, current_user),
        "published_count": int(published),
        "drafts_count": int(drafts),
        "scheduled_count": int(scheduled),
    }


@router.get("/franchise/{franchisee_id}/posts")
async def get_franchise_posts(
    franchisee_id: int,
    limit: int = 20,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """View-only content library: recent published posts by this franchisee.
    Master can read but not edit or republish."""
    _require_master(current_user)
    f = _get_franchisee(db, current_user, franchisee_id)

    limit = max(1, min(int(limit or 20), 100))
    offset = max(0, int(offset or 0))
    rows = (
        db.query(PublishedPost)
        .filter(PublishedPost.user_id == f.id)
        .order_by(PublishedPost.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    posts = [
        {
            "id": p.id,
            "platform": getattr(p, "platform", None),
            "content": getattr(p, "content", None),
            "media_url": getattr(p, "media_url", None),
            "thumbnail_url": getattr(p, "thumbnail_url", None),
            "created_at": (p.created_at.isoformat() if getattr(p, "created_at", None) else None),
        }
        for p in rows
    ]
    return {
        "franchisee_id": f.id,
        "posts": posts,
        "limit": limit,
        "offset": offset,
    }


@router.delete("/franchise/{franchisee_id}")
async def remove_franchisee(
    franchisee_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Master boots a franchisee out of the brand network. Franchisee
    keeps their own plan, subscription, content, and socials — they just
    lose access to Master's brand DNA and stop appearing in Master's
    roster/analytics."""
    _require_master(current_user)
    f = _get_franchisee(db, current_user, franchisee_id)

    f.franchise_of_id = None
    # Revoke assigned brand ids (they belonged to Master's business_dna).
    f.assigned_dna_product_id = None
    f.assigned_dna_product_ids = None
    db.commit()
    return {"ok": True, "id": f.id, "removed": True}


@router.post("/franchise/leave")
async def leave_franchise_network(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Franchisee voluntarily exits the brand network. Same effect as
    being booted — clears affiliation but preserves the account."""
    if not getattr(current_user, "franchise_of_id", None):
        raise HTTPException(
            status_code=400,
            detail="You are not currently a franchisee.",
        )
    current_user.franchise_of_id = None
    current_user.assigned_dna_product_id = None
    current_user.assigned_dna_product_ids = None
    db.commit()
    return {"ok": True, "left": True}
