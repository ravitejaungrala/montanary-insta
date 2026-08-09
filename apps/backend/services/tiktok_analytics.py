"""TikTok creator + video stats fetcher (Phase 3).

Three functions, each one Display API / Content Posting API call:

  fetch_creator_stats(account, db) -> {follower_count, following_count, likes_count, video_count}
      Uses /v2/user/info/?fields=follower_count,following_count,likes_count,video_count
      Cost: 1 quota unit on TikTok's user.info endpoint.
      Caller stamps follower_count onto SocialAccount.follower_count.

  fetch_video_stats(account, db, video_ids) -> {video_id: {views, likes, comments, shares}}
      Uses /v2/video/query/?fields=view_count,like_count,comment_count,share_count
      Batches up to 20 video IDs per call (TikTok's documented max).
      Caller fans the result back into PublishedPostPlatform.metrics_json.

  get_tiktok_stats(account, post_map, db) -> tuple[dict, list[dict]]
      High-level entry point matching get_youtube_stats / get_linkedin_stats etc.
      Returns (stats={"followers": int, ...}, detailed_posts=[{id, platform, ...}]).

All three refresh the access token first via tiktok_publisher's existing helper
so the periodic analytics sync doesn't need to manage tokens itself.
"""
from __future__ import annotations

import logging
from typing import Iterable

import requests

from services.tiktok_publisher import refresh_tiktok_access_token

logger = logging.getLogger("pipelyt.tiktok")

_USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
_VIDEO_QUERY_URL = "https://open.tiktokapis.com/v2/video/query/"
# TikTok caps /v2/video/query/ at 20 video IDs per call.
_BATCH_SIZE = 20


def fetch_creator_stats(account, db) -> dict | None:
    """Pull follower/following/likes/video count for this TikTok account.
    Returns None if the API call fails (revoked token, sandbox restrictions,
    etc.) so the analytics sync can keep going for other accounts.
    """
    access_token, err = refresh_tiktok_access_token(account, db)
    if not access_token:
        logger.warning(f"[TT_STATS] creator sync skipped — {err}")
        return None

    # TikTok requires the fields list as a comma-separated query param. The
    # SAME endpoint /v2/user/info/ that we used at OAuth callback time —
    # just with a different fields list now that we want metrics, not the
    # display profile.
    fields = "follower_count,following_count,likes_count,video_count"
    try:
        resp = requests.get(
            f"{_USER_INFO_URL}?fields={fields}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
    except Exception as exc:
        logger.error(f"[TT_STATS] user.info network error: {exc}")
        return None

    if resp.status_code != 200:
        logger.error(
            f"[TT_STATS] user.info failed {resp.status_code}: {resp.text[:200]}"
        )
        return None

    payload = resp.json() or {}
    # TikTok wraps responses: { "data": { "user": {...} }, "error": {"code": "ok"} }
    err_code = (payload.get("error") or {}).get("code", "")
    if err_code and err_code != "ok":
        msg = (payload.get("error") or {}).get("message", "")
        logger.error(f"[TT_STATS] user.info API error: {err_code} {msg}")
        return None

    user = ((payload.get("data") or {}).get("user")) or {}
    return {
        "follower_count":  int(user.get("follower_count") or 0),
        "following_count": int(user.get("following_count") or 0),
        "likes_count":     int(user.get("likes_count") or 0),
        "video_count":     int(user.get("video_count") or 0),
    }


def fetch_video_stats(account, db, video_ids: Iterable[str]) -> dict[str, dict]:
    """Pull view/like/comment/share count for a batch of TikTok video IDs.

    Returns a dict keyed by video_id. Any IDs the API doesn't return (video
    deleted, made private after publish, in a region the API can't reach)
    are simply omitted — callers should not assume every input ID will
    appear in the output.

    Batches automatically in groups of 20 (TikTok's documented cap).
    """
    ids = [v for v in (video_ids or []) if v]
    if not ids:
        return {}

    access_token, err = refresh_tiktok_access_token(account, db)
    if not access_token:
        logger.warning(f"[TT_STATS] video sync skipped — {err}")
        return {}

    out: dict[str, dict] = {}
    headers = {"Authorization": f"Bearer {access_token}"}
    # TikTok requires the fields list in a query param AND the video_ids
    # in the JSON body — split request style, mirrors what their docs show.
    fields = "id,view_count,like_count,comment_count,share_count"

    for batch_start in range(0, len(ids), _BATCH_SIZE):
        batch = ids[batch_start : batch_start + _BATCH_SIZE]
        try:
            resp = requests.post(
                f"{_VIDEO_QUERY_URL}?fields={fields}",
                headers={**headers, "Content-Type": "application/json"},
                json={"filters": {"video_ids": batch}},
                timeout=15,
            )
        except Exception as exc:
            logger.error(f"[TT_STATS] video.query network error: {exc}")
            continue

        if resp.status_code != 200:
            logger.error(
                f"[TT_STATS] video.query failed {resp.status_code}: {resp.text[:300]}"
            )
            continue

        payload = resp.json() or {}
        err_code = (payload.get("error") or {}).get("code", "")
        if err_code and err_code != "ok":
            msg = (payload.get("error") or {}).get("message", "")
            logger.error(f"[TT_STATS] video.query API error: {err_code} {msg}")
            continue

        videos = ((payload.get("data") or {}).get("videos")) or []
        for v in videos:
            vid = v.get("id")
            if not vid:
                continue
            _views = int(v.get("view_count") or 0)
            _likes = int(v.get("like_count") or 0)
            _comments = int(v.get("comment_count") or 0)
            _shares = int(v.get("share_count") or 0)
            out[str(vid)] = {
                # Native TikTok fields, for any tile that wants the
                # "true" TikTok terminology.
                "views":    _views,
                "likes":    _likes,
                "comments": _comments,
                "shares":   _shares,
                # Dashboard-canonical fields so the existing Campaign
                # Performance / Posts widgets pick them up without
                # per-platform special-casing:
                "reach":      _views,
                "engagement": _likes + _comments + _shares,
            }

    logger.info(
        f"[TT_STATS] fetched stats for {len(out)}/{len(ids)} video(s) on account {account.account_id}"
    )
    return out


def get_tiktok_stats(account, post_map, db) -> tuple[dict, list[dict]]:
    """High-level entry point matching get_linkedin_stats / get_meta_stats
    / get_twitter_stats / get_youtube_stats.

    Returns:
      stats          — {"followers": int, ...}   (caller stamps onto
                                                  SocialAccount.follower_count)
      detailed_posts — [{id, platform, views, likes, comments, shares}, ...]
                       (caller writes each into the corresponding
                        PublishedPostPlatform.metrics_json)
    """
    # ── 1. Creator-level (followers etc.) ─────────────────────────────────
    cr_stats = fetch_creator_stats(account, db) or {}
    stats: dict = {
        "followers": cr_stats.get("follower_count", 0),
        # Extras the dashboards can show if they want — analytics.py only
        # consumes "followers" today but tomorrow's per-platform tiles can
        # reach for the rest without another API call.
        "total_likes":  cr_stats.get("likes_count", 0),
        "total_videos": cr_stats.get("video_count", 0),
    }

    # ── 2. Per-video metrics ──────────────────────────────────────────────
    # `post_map` is a list of PublishedPostPlatform rows the caller has
    # already scoped to this TikTok account. Each row's `native_post_id` is
    # the TikTok video id stamped by tiktok_publisher.publish_video.
    if not post_map:
        return stats, []

    vid_to_post_id = {
        (row.native_post_id or "").strip(): row.published_post_id
        for row in post_map
        if row.native_post_id
    }
    if not vid_to_post_id:
        return stats, []

    video_stats = fetch_video_stats(account, db, list(vid_to_post_id.keys()))

    detailed_posts: list[dict] = []
    for vid, ms in video_stats.items():
        post_id = vid_to_post_id.get(vid)
        if not post_id:
            continue
        detailed_posts.append({
            "id":       post_id,
            "platform": "tiktok",
            "views":    ms.get("views", 0),
            "likes":    ms.get("likes", 0),
            "comments": ms.get("comments", 0),
            "shares":   ms.get("shares", 0),
        })

    return stats, detailed_posts
