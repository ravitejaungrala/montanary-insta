"""YouTube comment fetch + reply (Phase 3).

Mirrors the contract the other connectors use so the frontend doesn't need
any per-platform branching:

  fetch_comments(account, db, video_id) -> (comments_list, error_or_None)
      Each comment dict has the canonical shape used across all platforms:
        id, author_name, author_handle, author_picture, message,
        created_at, like_count, reply_count, parent_id, platform='youtube'

  post_reply(account, db, parent_id, text) -> (new_comment_id, error_or_None)
      Posts a reply under an existing top-level comment OR an existing
      reply (YouTube only allows two levels of nesting — parent + replies).

Both refresh the access token via the existing youtube_publisher helper
before calling the API.

Quota budget: commentThreads.list costs 1 unit, comments.insert costs 50
units. Default project quota is 10,000/day. Even a high-activity user
fetching one video's comments every 5 minutes and posting 50 replies/day
uses 1*288 + 50*50 = 2838 units — well within budget.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from services.youtube_publisher import refresh_youtube_access_token

logger = logging.getLogger("pipelyt.youtube")

_THREADS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"
_COMMENTS_URL = "https://www.googleapis.com/youtube/v3/comments"


def _normalize_comment(item: dict, parent_id: str = "") -> dict:
    """Convert a YouTube API comment object (either a top-level snippet from
    commentThreads or a nested reply) into the canonical shape the rest of
    the codebase expects.
    """
    snippet = (item or {}).get("snippet") or {}
    # `topLevelComment.snippet` shape (nested) vs flat-comment shape.
    if "topLevelComment" in snippet:
        snippet = (snippet.get("topLevelComment") or {}).get("snippet") or {}
    text = snippet.get("textDisplay") or snippet.get("textOriginal") or ""
    return {
        "id": item.get("id") or "",
        "author_name": snippet.get("authorDisplayName") or "YouTube user",
        # YouTube doesn't have a stable @-handle in the comment snippet —
        # closest is the channel URL fragment. Surface the URL itself so
        # the UI can deep-link if it wants.
        "author_handle": snippet.get("authorChannelUrl") or "",
        "author_picture": snippet.get("authorProfileImageUrl") or "",
        "message": text,
        "created_at": snippet.get("publishedAt"),
        "like_count": int(snippet.get("likeCount") or 0),
        "reply_count": 0,  # filled in by the caller for top-level comments
        "parent_id": parent_id,
        "platform": "youtube",
    }


def fetch_comments(account, db, video_id: str):
    """List all top-level comments + their replies on a YouTube video.
    Returns `(comments_list, error_or_None)`.

    The list is flat (top-level + replies interleaved) — every reply's
    `parent_id` points back at its top-level comment so the frontend can
    rebuild the tree client-side without making N+1 API calls.
    """
    if not video_id:
        return [], "missing video_id"

    access_token, err = refresh_youtube_access_token(account, db)
    if not access_token:
        return [], err or "could not refresh YouTube access token"

    try:
        resp = requests.get(
            _THREADS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "part": "snippet,replies",
                "videoId": video_id,
                "maxResults": 50,
                "order": "time",  # newest-first; flip to "relevance" later if desired
                "textFormat": "plainText",
            },
            timeout=15,
        )
    except Exception as exc:
        return [], f"YouTube comments fetch network error: {exc}"

    if resp.status_code == 403:
        # Common cause: the video has comments disabled, or the channel hid
        # them. Surface a clear message instead of a raw 403.
        try:
            err_body = resp.json().get("error", {})
            reason = (err_body.get("errors") or [{}])[0].get("reason", "")
        except Exception:
            reason = ""
        if reason == "commentsDisabled":
            return [], "Comments are disabled on this video."
        return [], f"YouTube comments forbidden: {reason or resp.text[:200]}"

    if resp.status_code != 200:
        logger.error(f"[YT_COMMENTS] fetch failed {resp.status_code}: {resp.text[:300]}")
        return [], f"YouTube comments HTTP {resp.status_code}: {resp.text[:200]}"

    items = (resp.json() or {}).get("items") or []
    out: list[dict] = []
    for thread in items:
        # Top-level comment lives inside `snippet.topLevelComment`.
        top = _normalize_comment(thread)
        # Snippet's `totalReplyCount` reflects YT's real reply count even
        # when the inline `replies` array is truncated to the first 5.
        top["reply_count"] = int(
            ((thread.get("snippet") or {}).get("totalReplyCount") or 0)
        )
        out.append(top)

        # Nested replies, if any. The Data API only inlines the first
        # ~5 — if a thread has more we'd need a separate comments.list
        # call per thread, which we deliberately skip to keep this cheap.
        # Surfacing the 5 most recent + a "view more on YouTube" affordance
        # in the UI is the right product trade-off for now.
        reply_items = ((thread.get("replies") or {}).get("comments")) or []
        for r in reply_items:
            out.append(_normalize_comment(r, parent_id=top["id"]))

    logger.info(
        f"[YT_COMMENTS] fetched {len(out)} comment(s) for video {video_id} "
        f"({len(items)} top-level)"
    )
    return out, None


def post_reply(account, db, parent_id: str, text: str):
    """Post a reply to an existing YouTube comment.
    Returns `(new_comment_id, error_or_None)`.

    `parent_id` must be either a top-level comment id (from
    commentThreads.list → item.id) OR a reply id from the same thread.
    YouTube only allows two levels of nesting, so attempting to reply to a
    reply silently lands as a sibling reply under the same parent.
    """
    if not parent_id:
        return None, "missing parent_id"
    text = (text or "").strip()
    if not text:
        return None, "reply text is empty"
    if len(text) > 10000:  # YouTube hard caps comment length
        return None, "reply text exceeds 10,000 characters"

    access_token, err = refresh_youtube_access_token(account, db)
    if not access_token:
        return None, err or "could not refresh YouTube access token"

    body = {
        "snippet": {
            "parentId": parent_id,
            "textOriginal": text,
        }
    }

    try:
        resp = requests.post(
            _COMMENTS_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            params={"part": "snippet"},
            json=body,
            timeout=20,
        )
    except Exception as exc:
        return None, f"YouTube reply network error: {exc}"

    if resp.status_code not in (200, 201):
        try:
            err_body = resp.json().get("error", {})
            msg = err_body.get("message") or resp.text[:200]
        except Exception:
            msg = resp.text[:200]
        logger.error(f"[YT_COMMENTS] reply failed {resp.status_code}: {msg}")
        return None, f"YouTube reply HTTP {resp.status_code}: {msg}"

    new_id = (resp.json() or {}).get("id")
    if not new_id:
        return None, "YouTube returned no comment id on reply"
    logger.info(f"[YT_COMMENTS] posted reply {new_id} under parent {parent_id}")
    return new_id, None
