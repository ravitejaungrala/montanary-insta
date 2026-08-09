from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional

from core.database import get_db
from routers.auth import get_current_user
from models import User, PublishedPost, PublishedPostPlatform, SocialAccount, AutoReplyLog
from services.ai_service import generate_replies_for_comments, _build_user_context
from services.team_service import get_team_scope_user_ids
from services.social_service import (
    reply_linkedin_org_comment,
    reply_facebook_comment,
    reply_instagram_comment,
    reply_twitter_comment
)
from core.config import TWITTER_API_KEY, TWITTER_API_SECRET

router = APIRouter(prefix="/reputation", tags=["reputation"])

@router.get("/posts")
async def get_reputation_posts(
    platform: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Fetch published posts for reputation management with filtering."""
    visible_ids = get_team_scope_user_ids(db, user)
    query = db.query(PublishedPost).filter(PublishedPost.user_id.in_(visible_ids))
    
    if platform and platform != 'all':
        # More robust join-based filtering by joining with the platforms table
        query = query.join(PublishedPostPlatform).filter(PublishedPostPlatform.platform == platform)
    
    if start_date:
        query = query.filter(PublishedPost.created_at >= start_date)
    if end_date:
        query = query.filter(PublishedPost.created_at <= end_date)
        
    posts = query.order_by(PublishedPost.created_at.desc()).all()

    # Bulk-load account rows for name/type lookup so the UI can show
    # "Salman Shaik (Personal)" vs "NEUZEN AI (Page)" chips instead of
    # a bare "LINKEDIN" label per row.
    pairs = {(pp.platform, pp.account_id)
             for p in posts for pp in p.platform_posts if pp.account_id}
    account_map = {}
    if pairs:
        rows = db.query(SocialAccount).filter(
            SocialAccount.platform.in_({pl for pl, _ in pairs}),
            SocialAccount.account_id.in_({aid for _, aid in pairs}),
        ).all()
        for a in rows:
            account_map[(a.platform, a.account_id)] = {
                "name": a.name or "",
                "type": a.type or "",
                "is_personal": (a.platform == "linkedin"
                                and (a.account_id or "").startswith("urn:li:person:")),
            }

    result = []
    for p in posts:
        for pp in p.platform_posts:
            # If a specific platform is filtered, skip others
            if platform and platform != 'all' and pp.platform != platform:
                continue

            # LinkedIn personal-profile posts are hidden from Reputation
            # entirely: LinkedIn's API doesn't return personal-post comment
            # text to third-party apps, so a row here would only ever show
            # a "not supported" empty state that clutters the queue.
            if pp.platform == "linkedin" and (pp.account_id or "").startswith("urn:li:person:"):
                continue

            plat_key = pp.platform.lower() if pp.platform else "all"
            info = account_map.get((pp.platform, pp.account_id)) or {}
            result.append({
                "id": p.id,
                "instance_id": pp.id,
                "content": p.content,
                "image_url": p.image_url,
                "media_type": p.media_type,
                "created_at": p.created_at,
                "platform": plat_key,
                "account_id": pp.account_id,
                # Enriched account fields — the frontend renders these as a
                # chip next to the platform label. `account_name` is what
                # the user sees; `is_personal` picks the badge colour.
                "account_name": info.get("name") or "",
                "account_type": info.get("type") or "",
                "is_personal": bool(info.get("is_personal")),
                "native_id": pp.native_post_id,
                "metrics": pp.metrics_json
            })

    return result

@router.post("/generate-replies")
async def generate_ai_replies(
    payload: dict,
    user: User = Depends(get_current_user)
):
    """Generate context-aware AI replies for a list of comments."""
    comments = payload.get("comments", [])
    post_context = payload.get("post_context", "")
    
    if not comments:
        return {"replies": []}
        
    # Build user context from DNA
    dna_context, _, _ = _build_user_context(user)
    
    replies = generate_replies_for_comments(
        comments=comments,
        post_context=post_context,
        business_dna=dna_context
    )
    
    return replies

@router.post("/confirm-replies")
async def confirm_bulk_replies(
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Bulk send confirmed replies to the social platforms."""
    replies_to_send = payload.get("replies", []) # List of {platform, native_post_id, comment_id, message, account_id}
    
    results = []
    for reply in replies_to_send:
        platform = reply.get("platform")
        comment_id = reply.get("comment_id")
        message = reply.get("message")
        account_id = reply.get("account_id")
        native_post_id = reply.get("native_post_id")
        
        # Get social account token
        account = db.query(SocialAccount).filter(
            SocialAccount.platform == platform,
            SocialAccount.account_id == account_id,
            SocialAccount.user_id == user.id
        ).first()
        
        if not account:
            results.append({"comment_id": comment_id, "success": False, "error": "Account not found"})
            continue
            
        try:
            if platform == 'linkedin':
                comment_urn, err = reply_linkedin_org_comment(account.token, native_post_id, account.account_id, message, comment_id)
                success = comment_urn is not None
            elif platform == 'facebook':
                comment_urn, err = reply_facebook_comment(account.token, comment_id, message)
                success = comment_urn is not None
            elif platform == 'instagram':
                comment_urn, err = reply_instagram_comment(account.token, comment_id, message)
                success = comment_urn is not None
            elif platform == 'twitter':
                comment_urn, err = reply_twitter_comment(TWITTER_API_KEY, TWITTER_API_SECRET, account.token, account.token_secret, comment_id, message)
                success = comment_urn is not None
            else:
                success, err = False, f"Unsupported platform: {platform}"
                
            results.append({
                "comment_id": comment_id,
                "success": success,
                "error": err
            })
        except Exception as e:
            results.append({
                "comment_id": comment_id,
                "success": False,
                "error": str(e)
            })

    return {"results": results}


# ─────────────────────────────────────────────────────────────────────
# Auto-Reply: page-wide toggle. When ON, the background worker in
# services/auto_reply_worker.py pulls new comments on every connected
# account's recent posts every ~5 minutes and auto-posts an AI reply.
# ─────────────────────────────────────────────────────────────────────

class AutoReplyToggleBody(BaseModel):
    enabled: bool


@router.get("/auto-reply")
async def get_auto_reply_setting(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return the user's current auto-reply toggle state + a small
    stat block the UI can show as reassurance."""
    total_logged = (
        db.query(AutoReplyLog).filter(AutoReplyLog.user_id == user.id).count()
    )
    last_row = (
        db.query(AutoReplyLog)
          .filter(AutoReplyLog.user_id == user.id, AutoReplyLog.status == "posted")
          .order_by(AutoReplyLog.replied_at.desc())
          .first()
    )
    return {
        "enabled": bool(user.auto_reply_enabled),
        "total_auto_replies": total_logged,
        "last_reply_at": last_row.replied_at.isoformat() + "Z" if last_row else None,
    }


@router.put("/auto-reply")
async def set_auto_reply_setting(
    body: AutoReplyToggleBody,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Flip the user's auto-reply toggle. Takes effect on the next
    worker tick (worst case ~5 minutes). No historical backfill is
    triggered here — the worker itself scans all unreplied comments on
    the next tick, so old comments will also get replies once ON."""
    user.auto_reply_enabled = bool(body.enabled)
    db.commit()
    return {"enabled": user.auto_reply_enabled}
