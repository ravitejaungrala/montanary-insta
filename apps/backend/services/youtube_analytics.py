"""YouTube channel + video stats fetcher (Phase 4).

Two functions, each one Data API v3 call:

  fetch_channel_stats(account, db) -> {subscriber_count, view_count, video_count}
      Uses channels.list?mine=true&part=statistics (cost: 1 quota unit).
      Caller stamps subscriber_count onto SocialAccount.follower_count.

  fetch_video_stats(account, db, video_ids) -> {video_id: {views,likes,comments}}
      Uses videos.list?part=statistics&id=...  (cost: 1 unit per call,
      batches up to 50 video IDs per call).
      Caller fans the result back into PublishedPostPlatform.metrics_json.

Both refresh the access token first (using the same helper the publisher uses)
so the existing periodic analytics sync doesn't need to manage tokens itself.

Quota budget reminder: YouTube Data API gives 10,000 units/day per project.
Channel sync = 1 unit. Video sync = 1 unit per 50 videos. So even 100 active
YouTube channels with 50 videos each = 100 + 100 = 200 units, well within
budget.
"""
from __future__ import annotations

import logging
from typing import Iterable

import requests

from services.youtube_publisher import refresh_youtube_access_token

logger = logging.getLogger("pipelyt.youtube")

_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
_BATCH_SIZE = 50  # YouTube's documented max ids per videos.list call


def fetch_channel_stats(account, db) -> dict | None:
    """Pull subscriber/view/video count for the channel this SocialAccount
    represents. Returns None if the channel is unreachable (revoked token,
    deleted channel, etc.) so the analytics sync can keep going for other
    accounts.

    The returned dict shape matches what the rest of the analytics code
    expects to consume (raw ints, no unit suffixes).
    """
    access_token, err = refresh_youtube_access_token(account, db)
    if not access_token:
        logger.warning(f"[YT_STATS] channel sync skipped — {err}")
        return None

    try:
        resp = requests.get(
            _CHANNELS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"part": "statistics", "mine": "true"},
            timeout=15,
        )
    except Exception as exc:
        logger.error(f"[YT_STATS] channels.list network error: {exc}")
        return None

    if resp.status_code != 200:
        logger.error(
            f"[YT_STATS] channels.list failed {resp.status_code}: {resp.text[:200]}"
        )
        return None

    items = (resp.json() or {}).get("items") or []
    if not items:
        logger.warning(f"[YT_STATS] channels.list returned no items for {account.account_id}")
        return None

    stats = items[0].get("statistics") or {}
    # subscriberCount can be None if the user hides their subscriber count
    # (privacy setting). Treat that as 0 rather than failing the sync.
    sub_count = stats.get("subscriberCount")
    return {
        "subscriber_count": int(sub_count or 0),
        "view_count":       int(stats.get("viewCount") or 0),
        "video_count":      int(stats.get("videoCount") or 0),
        # Flag so callers can decide whether to surface "hidden" in the UI
        # instead of just showing 0 subs.
        "subscriber_count_hidden": sub_count is None,
    }


def fetch_video_stats(account, db, video_ids: Iterable[str]) -> dict[str, dict]:
    """Pull views/likes/comments for a batch of video IDs.

    Returns a dict keyed by video_id. Any IDs that the API didn't return
    (video deleted, made private, never existed) are simply omitted —
    callers should not assume every input ID will appear in the output.

    Batches automatically in groups of 50 — the YouTube Data API hard cap
    on videos.list?id=.
    """
    ids = [v for v in (video_ids or []) if v]
    if not ids:
        return {}

    access_token, err = refresh_youtube_access_token(account, db)
    if not access_token:
        logger.warning(f"[YT_STATS] video sync skipped — {err}")
        return {}

    out: dict[str, dict] = {}
    headers = {"Authorization": f"Bearer {access_token}"}

    for batch_start in range(0, len(ids), _BATCH_SIZE):
        batch = ids[batch_start : batch_start + _BATCH_SIZE]
        try:
            resp = requests.get(
                _VIDEOS_URL,
                headers=headers,
                params={"part": "statistics", "id": ",".join(batch)},
                timeout=15,
            )
        except Exception as exc:
            logger.error(f"[YT_STATS] videos.list network error: {exc}")
            continue

        if resp.status_code != 200:
            logger.error(
                f"[YT_STATS] videos.list failed {resp.status_code}: {resp.text[:200]}"
            )
            continue

        for item in (resp.json() or {}).get("items") or []:
            vid = item.get("id")
            stats = item.get("statistics") or {}
            if not vid:
                continue
            _views = int(stats.get("viewCount") or 0)
            _likes = int(stats.get("likeCount") or 0)
            _comments = int(stats.get("commentCount") or 0)
            out[vid] = {
                # Native YouTube fields, kept for any future tile that wants
                # the "true" YT terminology.
                "views":    _views,
                "likes":    _likes,
                "comments": _comments,
                # Dashboard-canonical fields so the existing Campaign
                # Performance / Posts widgets pick them up without needing
                # per-platform special-casing:
                #
                #   reach       — closest YouTube equivalent is total views.
                #                 Real "impressions" (the number of times the
                #                 thumbnail was shown) requires the YouTube
                #                 Analytics API — Phase 4b.
                #   engagement  — likes + comments. YouTube doesn't expose
                #                 shares via the free Data API, so we treat
                #                 shares as 0 in this sum.
                #   shares      — 0. YouTube Data API doesn't surface share
                #                 count to channel owners. (Channel-wide
                #                 "Shares" only appears in YouTube Studio.)
                "reach":      _views,
                "engagement": _likes + _comments,
                "shares":     0,
                # favoriteCount is always 0 in the v3 API (deprecated by
                # YouTube) — don't bother returning it.
            }

    logger.info(
        f"[YT_STATS] fetched stats for {len(out)}/{len(ids)} video(s) on channel {account.account_id}"
    )
    return out


def get_youtube_stats(account, post_map, db) -> tuple[dict, list[dict]]:
    """High-level entry point matching the shape of
    `analytics.get_linkedin_stats / get_meta_stats / get_twitter_stats`.

    Returns:
      stats         — {"followers": int}   (caller stamps onto
                                            SocialAccount.follower_count)
      detailed_posts — [{id, platform, views, likes, comments}, …]
                       (caller writes each into the corresponding
                        PublishedPostPlatform.metrics_json)
    """
    # ── 1. Channel-level (subscribers etc.) ───────────────────────────────
    ch_stats = fetch_channel_stats(account, db) or {}
    stats: dict = {
        "followers": ch_stats.get("subscriber_count", 0),
        # Extras the dashboards can show if they want — analytics.py only
        # consumes "followers" today but tomorrow's per-platform tiles can
        # reach for the rest without another API call.
        "total_views": ch_stats.get("view_count", 0),
        "total_videos": ch_stats.get("video_count", 0),
    }
    if ch_stats.get("subscriber_count_hidden"):
        stats["subscriber_count_hidden"] = True

    # ── 2. Per-video metrics ──────────────────────────────────────────────
    # `post_map` is a list of PublishedPostPlatform rows the caller has
    # already scoped to this YouTube account. Each row's `native_post_id`
    # is the YouTube video_id we got back from videos.insert.
    if not post_map:
        return stats, []

    # Map video_id → published_post_id so we can shape detailed_posts
    # correctly. Drop any rows that never got a native_post_id (publish
    # failed mid-flight).
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
            "platform": "youtube",
            "views":    ms.get("views", 0),
            "likes":    ms.get("likes", 0),
            "comments": ms.get("comments", 0),
        })

    return stats, detailed_posts
