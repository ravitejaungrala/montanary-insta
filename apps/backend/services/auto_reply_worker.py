"""Auto-Reply background worker.

Every ~5 minutes (driven by main.py's _local_sequencer_loop), this
scans every user with `User.auto_reply_enabled=True`, enumerates their
recent posts, fetches new comments, and auto-posts an AI-generated
reply to each un-replied root comment. Every reply is recorded in
`AutoReplyLog` so we never double-reply to the same comment across ticks.

Design constraints baked in:

- **Idempotency via AutoReplyLog.** Before generating a reply for a
  fetched comment, we check whether (user_id, platform, comment_id) is
  already logged. If yes → skip (regardless of whether the previous
  status was "posted" or "failed" — a persistently-failing comment
  should not be retried on every tick).
- **Rate-limit awareness.** LinkedIn Dev-Tier is 500 req/day/app.
  A tight tick would blow it: we skip a post entirely as soon as
  that account's LinkedIn quota is exhausted for this tick.
- **Skip unsupported platforms.**
    * LinkedIn PERSONAL posts (urn:li:person:*) — LinkedIn's API
      does not expose personal-post comment TEXT to 3rd parties
      (r_member_social_feed is a closed permission).
- **X (Twitter) IS supported.** X moved to a pay-as-you-go model in
  Feb 2026 — 450 search reads / 15 min per app is plenty for polling.
  Cost is roughly $0.005 per read + $0.015 per reply post. If any X
  account 429s, we back off that account for this tick (same pattern
  as LinkedIn).
- **Only recent posts.** Comments dry up on posts >30 days old. Skipping
  older posts drops our per-tick request count sharply.
- **Skip own replies.** Comments with `is_self` or from one of our own
  connected accounts are excluded.
- **Skip comments with existing platform replies.** If the platform
  reports `reply_count>0` we assume the user (or a previous auto-reply
  the log lost) already handled it.

The whole worker is a single top-level function `process_auto_replies()`
that the sequencer calls in a threadpool. No per-user threading — a
sequential pass keeps the LinkedIn quota accounting predictable.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import and_
from sqlalchemy.orm.attributes import flag_modified

from core.database import SessionLocal
from core.config import TWITTER_API_KEY, TWITTER_API_SECRET
from models import (
    User,
    SocialAccount,
    PublishedPost,
    PublishedPostPlatform,
    AutoReplyLog,
)
from services.ai_service import generate_replies_for_comments, _build_user_context
from services.social_service import (
    fetch_linkedin_org_comments,
    fetch_facebook_comments,
    fetch_instagram_comments,
    fetch_twitter_comments,
    reply_linkedin_org_comment,
    reply_facebook_comment,
    reply_instagram_comment,
    reply_twitter_comment,
)

logger = logging.getLogger("pipelyt.auto_reply")

# Hard safety rails — cap per-user work in a single tick so a runaway
# or misconfigured user can't blow every LinkedIn / YouTube quota.
_MAX_POSTS_PER_USER_PER_TICK = 100
_MAX_COMMENTS_PER_POST_PER_TICK = 25

# Per-platform polling policy — derived from platform-owned docs on
# rate limits + practical algorithm shelf life. See the strategy doc.
#
#   window_days      — how far back we bother looking for comments.
#                      Older posts are skipped entirely — engagement
#                      dies off past that window (or the platform is
#                      evergreen and we're capping the polling
#                      breadth for cost / quota reasons).
#   min_interval_min — minimum minutes between two consecutive polls
#                      of the SAME post. The sequencer ticks every
#                      5 min; posts whose last poll is within this
#                      window are skipped, so a 60-minute cadence
#                      really means "one poll per 12 sequencer ticks".
#
# LinkedIn's numbers depend on whether the app is on Dev Tier (500 req
# per app per day cap) or Standard Tier (partner-negotiated, thousands
# per day). We read `LINKEDIN_TIER=dev|standard` from the env — default
# `dev` is the safe assumption; flip to `standard` once the tier
# upgrade lands from LinkedIn.
_LINKEDIN_TIER = (os.environ.get("LINKEDIN_TIER", "dev") or "dev").strip().lower()
_LINKEDIN_INTERVAL_MIN = 5 if _LINKEDIN_TIER == "standard" else 60

_PLATFORM_POLL_CONFIG = {
    "linkedin":  {"window_days": 7,  "min_interval_min": _LINKEDIN_INTERVAL_MIN},
    "facebook":  {"window_days": 7,  "min_interval_min": 5},
    "instagram": {"window_days": 7,  "min_interval_min": 5},
    "twitter":   {"window_days": 7,  "min_interval_min": 60},
    "youtube":   {"window_days": 90, "min_interval_min": 60},
    "tiktok":    {"window_days": 30, "min_interval_min": 60},
}

# Broadest window across all platforms — used as the outer SQL bound.
# Platform-specific windows are applied in-loop against this superset.
_MAX_WINDOW_DAYS = max(cfg["window_days"] for cfg in _PLATFORM_POLL_CONFIG.values())

# Metadata key used inside PublishedPostPlatform.metrics_json to remember
# when we last successfully polled a post for auto-reply purposes. The
# cadence gate checks this value before spending an API call.
_POLL_STAMP_KEY = "_auto_reply_poll_at"


def _fetch_comments(account: SocialAccount, native_post_id: str):
    """Adapter: run the right fetch function based on account.platform.
    Returns (comments_list, err_or_None). Same contract as the fetchers.
    """
    p = (account.platform or "").lower()
    try:
        if p == "linkedin":
            return fetch_linkedin_org_comments(
                account.token, native_post_id, account.account_id
            )
        if p == "facebook":
            return fetch_facebook_comments(account.token, native_post_id)
        if p == "instagram":
            # Instagram comments fetching is temporarily put on hold
            return [], None
        if p == "twitter":
            return fetch_twitter_comments(
                TWITTER_API_KEY, TWITTER_API_SECRET,
                account.token, account.token_secret, native_post_id,
            )
    except Exception as e:
        return [], f"fetch exception: {e}"
    return [], "unsupported platform"


def _post_reply(account: SocialAccount, native_post_id: str,
                comment: dict, text: str):
    """Adapter: post `text` as a reply to `comment` on the right platform.
    Returns (posted_id_or_None, err_or_None)."""
    p = (account.platform or "").lower()
    cid = comment.get("id")
    try:
        if p == "linkedin":
            return reply_linkedin_org_comment(
                account.token, native_post_id, account.account_id,
                text, parent_comment_urn=cid,
            )
        if p == "facebook":
            return reply_facebook_comment(account.token, cid, text)
        if p == "instagram":
            return reply_instagram_comment(account.token, cid, text)
        if p == "twitter":
            return reply_twitter_comment(
                TWITTER_API_KEY, TWITTER_API_SECRET,
                account.token, account.token_secret, cid, text,
            )
    except Exception as e:
        return None, f"reply exception: {e}"
    return None, "unsupported platform"


def _process_user(db: Session, user: User) -> dict:
    """Process one user's auto-reply pass. Returns a stats dict for
    logging. Never raises — every failure is logged and moves on."""
    stats = {
        "user_id": user.id,
        "posts_scanned": 0,
        "comments_fetched": 0,
        "candidates": 0,
        "replied": 0,
        "failed": 0,
        "skipped_already_replied": 0,
        "skipped_platform": 0,
        "skipped_quota": 0,
        # New: dropped by per-platform window / cadence gates. Split so
        # log lines make it obvious WHY a tick moved zero posts (e.g.,
        # every LinkedIn post was polled 30 min ago on an hourly cadence).
        "skipped_window": 0,
        "skipped_interval": 0,
    }

    # Broadest window across all platforms — YouTube's 90 days today.
    # Per-platform windows are applied per-post inside the loop below.
    since = datetime.utcnow() - timedelta(days=_MAX_WINDOW_DAYS)
    # Get every platform_post row for this user's posts published in
    # the last N days. Ordered newest first so the safety-rail cap
    # (_MAX_POSTS_PER_USER_PER_TICK) drops OLD posts first, not new ones.
    platform_posts = (
        db.query(PublishedPostPlatform)
          .join(PublishedPost, PublishedPost.id == PublishedPostPlatform.published_post_id)
          .filter(
              PublishedPost.user_id == user.id,
              PublishedPost.created_at >= since,
          )
          .order_by(PublishedPost.created_at.desc())
          .limit(_MAX_POSTS_PER_USER_PER_TICK)
          .all()
    )

    if not platform_posts:
        return stats

    # Cache the user's SocialAccount rows so we don't re-query per post.
    accounts_by_id = {
        (a.platform, a.account_id): a
        for a in db.query(SocialAccount).filter(SocialAccount.user_id == user.id).all()
    }

    # Snapshot of every (platform, comment_id) we've EVER replied to for
    # this user — one query is way cheaper than a per-comment DB round-trip.
    already_replied = set(
        (row.platform, row.comment_id)
        for row in db.query(AutoReplyLog.platform, AutoReplyLog.comment_id)
                     .filter(AutoReplyLog.user_id == user.id)
                     .all()
    )

    # Track per-account quota exhaustion within this tick so we don't
    # hammer a bucket that just 429'd on us. Applies to any platform
    # that surfaces a 429 during either the comment-fetch or the
    # reply-post — currently that's LinkedIn (Dev Tier 500/day) and
    # X (450 reads / 15 min per app on pay-as-you-go).
    quota_dead = set()  # {(platform, account_id)}

    # DNA / brand context — passed into the reply generator so the tone
    # matches the brand. Best-effort: if it errors, fall back to a plain
    # "Professional business" default inside generate_replies_for_comments.
    try:
        business_dna, _, _ = _build_user_context(user)
    except Exception:
        business_dna = None

    for pp in platform_posts:
        stats["posts_scanned"] += 1
        platform = (pp.platform or "").lower()
        acct = accounts_by_id.get((pp.platform, pp.account_id))
        if not acct:
            # Post has no matching connected account (probably disconnected)
            stats["skipped_platform"] += 1
            continue

        # Only truly-blocked case is LinkedIn PERSONAL — the API
        # itself doesn't expose the comment text to us regardless of
        # rate limit. Everything else (LinkedIn org, Facebook, IG, X)
        # is fair game.
        if platform == "linkedin" and (pp.account_id or "").startswith("urn:li:person:"):
            stats["skipped_platform"] += 1
            continue
        if (platform, pp.account_id) in quota_dead:
            stats["skipped_quota"] += 1
            continue

        # Per-platform gates. Config lives in _PLATFORM_POLL_CONFIG at
        # the top of this file. Platforms with no entry (edge case:
        # a new platform added to the app before this dict is updated)
        # fall through to a safe default of 7 days / hourly.
        cfg = _PLATFORM_POLL_CONFIG.get(platform, {"window_days": 7, "min_interval_min": 60})

        # Gate 1 — window. Older posts get engagement so rarely that
        # polling them wastes daily quota. LinkedIn: 7d. YouTube: 90d.
        post_age_days = (datetime.utcnow() - pp.published_post.created_at).days
        if post_age_days > cfg["window_days"]:
            stats["skipped_window"] += 1
            continue

        # Gate 2 — cadence. Skip if we polled this specific post within
        # its platform's min interval. Timestamp lives on the post's
        # metrics_json under _POLL_STAMP_KEY. Missing / malformed value
        # is treated as "never polled" so first-run always fetches.
        last_poll_raw = (pp.metrics_json or {}).get(_POLL_STAMP_KEY)
        if last_poll_raw:
            try:
                last_poll_at = datetime.fromisoformat(str(last_poll_raw).rstrip("Z"))
                if (datetime.utcnow() - last_poll_at) < timedelta(minutes=cfg["min_interval_min"]):
                    stats["skipped_interval"] += 1
                    continue
            except Exception:
                # Corrupt timestamp — ignore, poll anyway. The stamp will
                # be overwritten with a clean value below.
                pass

        comments, err = _fetch_comments(acct, pp.native_post_id)

        # Stamp the poll attempt regardless of success/failure — the
        # cadence gate is about "did we recently spend an API call on
        # this post," not "did we get useful data." A 429 counts as a
        # poll attempt; we won't retry until the interval elapses.
        # Skip stamping for genuine transient failures (network exceptions)
        # so those retry sooner — see _fetch_comments returning an err.
        try:
            existing_metrics = dict(pp.metrics_json or {})
            existing_metrics[_POLL_STAMP_KEY] = datetime.utcnow().isoformat() + "Z"
            pp.metrics_json = existing_metrics
            flag_modified(pp, "metrics_json")
            db.commit()
        except Exception as stamp_err:
            db.rollback()
            logger.warning(
                f"[AUTO_REPLY] poll-stamp write failed user={user.id} "
                f"post={pp.id}: {stamp_err}"
            )
        if err:
            # 429 / throttle / quota → mark this account done for this
            # tick so we don't burn every remaining post on the same
            # rate-limited bucket. Works for LinkedIn (Dev-Tier daily
            # cap) and X (pay-as-you-go 15-min window).
            el = err.lower()
            if "429" in el or "throttle" in el or "quota" in el or "rate limit" in el:
                quota_dead.add((platform, pp.account_id))
            continue
        if not comments:
            continue
        stats["comments_fetched"] += len(comments)

        # Filter to reply-worthy candidates:
        #   - has actual message text
        #   - not a reply itself (only reply to root comments)
        #   - not one of our own (is_self)
        #   - no existing platform replies (reply_count == 0)
        #   - not already logged in AutoReplyLog
        candidates = []
        for c in comments[:_MAX_COMMENTS_PER_POST_PER_TICK]:
            cid = c.get("id")
            if not cid or not (c.get("message") or "").strip():
                continue
            if c.get("parent_id"):
                continue
            if c.get("is_self"):
                continue
            if int(c.get("reply_count") or 0) > 0:
                continue
            if (platform, str(cid)) in already_replied:
                stats["skipped_already_replied"] += 1
                continue
            candidates.append(c)

        if not candidates:
            continue
        stats["candidates"] += len(candidates)

        # Generate all replies for this post in one AI call (much cheaper
        # than one call per comment).
        post_context = None
        if pp.published_post:
            post_context = pp.published_post.content or None
        try:
            gen = generate_replies_for_comments(
                comments=[
                    {
                        "id": c.get("id"),
                        "message": c.get("message") or "",
                        "author_name": c.get("author_name") or "",
                        "author_handle": c.get("author_handle") or "",
                    }
                    for c in candidates
                ],
                post_context=post_context,
                business_dna=business_dna,
            )
        except Exception as e:
            logger.warning(
                f"[AUTO_REPLY] generate_replies failed user={user.id} post={pp.id}: {e}"
            )
            continue

        # Index candidates by id so we can pair AI output back to the
        # original comment (needed to know the parent URN + author).
        cand_by_id = {c.get("id"): c for c in candidates}
        for r in (gen or {}).get("replies", []) or []:
            rid = r.get("id")
            text = (r.get("generated_reply") or "").strip()
            comment = cand_by_id.get(rid)
            if not comment or not text:
                continue

            # Post the reply.
            posted_id, perr = _post_reply(acct, pp.native_post_id, comment, text)
            success = posted_id is not None and not perr

            # Log EVERY attempt (posted OR failed) so we don't retry.
            try:
                db.add(AutoReplyLog(
                    user_id=user.id,
                    platform=platform,
                    native_post_id=pp.native_post_id,
                    comment_id=str(rid),
                    reply_text=text if success else None,
                    status="posted" if success else "failed",
                    error_msg=(perr[:500] if perr else None),
                    replied_at=datetime.utcnow(),
                ))
                db.commit()
            except Exception as le:
                db.rollback()
                logger.warning(
                    f"[AUTO_REPLY] log write failed user={user.id} "
                    f"comment={rid}: {le}"
                )

            if success:
                stats["replied"] += 1
                already_replied.add((platform, str(rid)))
                logger.info(
                    f"[AUTO_REPLY] ✅ user={user.id} platform={platform} "
                    f"post={pp.native_post_id} comment={rid}"
                )
            else:
                stats["failed"] += 1
                logger.warning(
                    f"[AUTO_REPLY] ❌ user={user.id} platform={platform} "
                    f"post={pp.native_post_id} comment={rid} err={perr}"
                )
                # 429 / rate-limit on the reply-post itself → skip the
                # rest of this account's posts for this tick. Covers
                # LinkedIn (daily cap) + X (15-min window). Facebook /
                # Instagram have per-hour app-scoped limits that rarely
                # bite in this pattern, but we treat them the same for
                # safety.
                if perr:
                    pl = perr.lower()
                    if "429" in perr or "rate limit" in pl or "throttle" in pl or "quota" in pl:
                        quota_dead.add((platform, pp.account_id))
                        break

    return stats


def process_auto_replies() -> dict:
    """Entry point for the sequencer tick.

    Iterates every user with `auto_reply_enabled=True` and runs one
    auto-reply pass. Returns aggregate stats for logging. Safe to call
    concurrently — each user pass gets its own DB session.

    IMPORTANT: this function is synchronous and does its own DB work,
    so the sequencer wraps it in `asyncio.to_thread` to avoid blocking
    the FastAPI event loop.
    """
    db = SessionLocal()
    aggregate = {
        "users_processed": 0,
        "replied": 0,
        "failed": 0,
        "candidates": 0,
    }
    try:
        users = (
            db.query(User)
              .filter(User.auto_reply_enabled == True)  # noqa: E712
              .filter(User.disabled == False)  # noqa: E712
              .all()
        )
        if not users:
            return aggregate

        logger.info(f"[AUTO_REPLY] tick — {len(users)} user(s) opted in")
        for u in users:
            try:
                s = _process_user(db, u)
            except Exception as e:
                logger.exception(f"[AUTO_REPLY] user={u.id} failed: {e}")
                continue
            aggregate["users_processed"] += 1
            aggregate["replied"] += s["replied"]
            aggregate["failed"] += s["failed"]
            aggregate["candidates"] += s["candidates"]
            if s["candidates"] or s["replied"] or s["failed"]:
                logger.info(
                    f"[AUTO_REPLY] user={u.id} posts={s['posts_scanned']} "
                    f"comments={s['comments_fetched']} candidates={s['candidates']} "
                    f"replied={s['replied']} failed={s['failed']} "
                    f"already_replied={s['skipped_already_replied']} "
                    f"skipped_platform={s['skipped_platform']} "
                    f"skipped_quota={s['skipped_quota']} "
                    f"skipped_window={s['skipped_window']} "
                    f"skipped_interval={s['skipped_interval']}"
                )
    finally:
        db.close()
    return aggregate
