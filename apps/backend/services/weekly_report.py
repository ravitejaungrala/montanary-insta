"""Weekly dashboard-stats digest — one email per active user, triggered by
the `/scheduler/weekly-report` endpoint on a Monday-morning EventBridge tick
(see lambda_pinger.py).

Reuses the exact same scoping + queries the dashboard endpoints use
(GET /scheduled, /posts/published, /drafts, /connections, /analytics/summary)
so the numbers in the email never drift from what a user would see logging in.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from models import User, ScheduledPost, PublishedPost, Draft, SocialAccount
from services.team_service import get_team_scope_user_ids, is_team_member, get_plan_owner
from services.email_service import email_service

logger = logging.getLogger("pipelyt.weekly_report")

# Skip re-sending to a user who already got one within this window — guards
# against the EventBridge heartbeat firing the trigger endpoint more than
# once inside its ~10-minute Monday-morning window.
_MIN_DAYS_BETWEEN_REPORTS = 6


async def aggregate_user_stats(db: Session, user: User) -> dict:
    """Compute the same numbers the Dashboard shows, scoped to `user`."""
    visible_ids = get_team_scope_user_ids(db, user)

    scheduled_count = db.query(ScheduledPost).filter(
        ScheduledPost.user_id.in_(visible_ids),
        ScheduledPost.status.in_(["pending"]),
    ).count()

    published_count = db.query(PublishedPost).filter(
        PublishedPost.user_id.in_(visible_ids)
    ).count()

    drafts_count = db.query(Draft).filter(
        Draft.user_id.in_(visible_ids)
    ).count()

    # Connections — asymmetric with the other three: an unfiltered admin
    # view is just their OWN accounts (matches GET /connections), not the
    # whole team's.
    if is_team_member(user):
        admin = get_plan_owner(db, user)
        connected_count = db.query(SocialAccount).filter(
            or_(
                SocialAccount.user_id == user.id,
                (SocialAccount.user_id == admin.id) & (SocialAccount.assigned_to_user_id == user.id),
            )
        ).count()
    else:
        connected_count = db.query(SocialAccount).filter(
            SocialAccount.user_id == user.id
        ).count()

    # Followers / engagement — call the real analytics endpoint function
    # directly instead of re-deriving its baseline/snapshot logic. Best
    # effort: some plans don't include the "analytics" feature, in which
    # case check_feature() raises 403 — degrade gracefully rather than
    # dropping the whole report for that user.
    followers = 0
    engagement_rate = "—"
    try:
        from routers.analytics import get_analytics_summary
        summary_resp = await get_analytics_summary(current_user=user, db=db)
        summary = summary_resp.get("summary", {})
        followers = summary.get("total_followers", 0)
        engagement_rate = summary.get("engagement_rate", "0%")
    except HTTPException as e:
        logger.info(f"Weekly report: analytics unavailable for user {user.id} ({e.detail})")
    except Exception:
        logger.exception(f"Weekly report: analytics fetch failed for user {user.id}")

    return {
        "scheduled_count": scheduled_count,
        "published_count": published_count,
        "drafts_count": drafts_count,
        "connected_count": connected_count,
        "followers": followers,
        "engagement_rate": engagement_rate,
        "upcoming_count": scheduled_count,
    }


async def send_weekly_report_for_user(db: Session, user: User) -> bool:
    stats = await aggregate_user_stats(db, user)
    return email_service.send_weekly_report_email(user.email, user.full_name, stats)


async def run_weekly_reports(db: Session, dry_run: bool = False) -> dict:
    """Send the weekly report to every eligible user.

    Eligible: not disabled, has an email, uses the Pipelyt dashboard
    (product_selection in 'pipelyt'/'both'), and hasn't received a report
    in the last _MIN_DAYS_BETWEEN_REPORTS days.

    NOTE: `User.last_weekly_report_sent_at` must exist on the model (added
    in the models.py + main.py migration step) before this will run.
    """
    cutoff = datetime.utcnow() - timedelta(days=_MIN_DAYS_BETWEEN_REPORTS)
    users = db.query(User).filter(
        User.disabled.is_(False),
        User.email.isnot(None),
        User.product_selection.in_(["pipelyt", "both"]),
        or_(
            User.last_weekly_report_sent_at.is_(None),
            User.last_weekly_report_sent_at < cutoff,
        ),
    ).all()

    sent, failed = 0, 0
    results = []
    for user in users:
        try:
            stats = await aggregate_user_stats(db, user)
            if dry_run:
                results.append({"user_id": user.id, "email": user.email, "stats": stats})
                continue
            ok = email_service.send_weekly_report_email(user.email, user.full_name, stats)
            if ok:
                user.last_weekly_report_sent_at = datetime.utcnow()
                db.commit()
                sent += 1
            else:
                failed += 1
        except Exception:
            db.rollback()
            logger.exception(f"Weekly report failed for user {user.id}")
            failed += 1

    logger.info(f"Weekly report run complete — sent={sent} failed={failed} eligible={len(users)}")
    response = {"sent": sent, "failed": failed, "eligible": len(users)}
    if dry_run:
        response["results"] = results
    return response
