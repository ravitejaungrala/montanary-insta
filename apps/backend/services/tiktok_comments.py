"""TikTok comment fetch + reply (Phase 4).

Mirrors the contract the other connectors use so the frontend doesn't need
any per-platform branching:

  fetch_comments(account, db, video_id) -> (comments_list, error_or_None)
      Each comment dict has the canonical shape used across all platforms:
        id, author_name, author_handle, author_picture, message,
        created_at, like_count, reply_count, parent_id, platform='tiktok'

  post_reply(account, db, parent_id, text) -> (new_comment_id, error_or_None)
      Posts a reply under an existing top-level comment. TikTok's reply
      tree is flat (similar to YouTube — two levels max), so replying to
      a reply lands as a sibling under the same parent.

Both refresh the access token via the existing tiktok_publisher helper
before calling the API.

⚠ Scope caveat: comment.list + comment.create require the
   `comment.list` and `comment.list.manage` scopes. Pipelyt requests both
   in _TIKTOK_SCOPES at OAuth time, but TikTok approves them in audit
   only — sandbox/unaudited apps may get 403 on these endpoints. We
   surface the API's exact error so the caller can decide.
"""
from __future__ import annotations

import logging

import requests

from services.tiktok_publisher import refresh_tiktok_access_token

logger = logging.getLogger("pipelyt.tiktok")

# These endpoint paths are from TikTok's Display API / Comment Management spec.
# If TikTok ever versions these (v2 → v3) we update the URLs here only.
_COMMENT_LIST_URL = "https://open.tiktokapis.com/v2/video/comment/list/"
_COMMENT_REPLY_LIST_URL = "https://open.tiktokapis.com/v2/video/comment/reply/list/"
_COMMENT_REPLY_CREATE_URL = "https://open.tiktokapis.com/v2/video/comment/reply/create/"

_PAGE_SIZE = 30  # TikTok comment.list max page size


def _normalize_comment(item: dict, parent_id: str = "") -> dict:
    """Convert a TikTok API comment object into the canonical shape the
    rest of the codebase expects.

    TikTok comment fields per their docs:
      id, text, create_time (unix sec), like_count, reply_count,
      user: { display_name, username, avatar_url, ... }
    """
    user = (item or {}).get("user") or {}
    create_time = item.get("create_time")
    # Convert unix-seconds → ISO 8601 string for consistency with other
    # platforms (LinkedIn/Meta give ISO, YouTube gives ISO, Twitter gives ISO).
    created_at = None
    if create_time:
        try:
            from datetime import datetime, timezone
            created_at = datetime.fromtimestamp(int(create_time), tz=timezone.utc).isoformat()
        except Exception:
            created_at = str(create_time)

    return {
        "id":             item.get("id") or "",
        "author_name":    user.get("display_name") or user.get("username") or "TikTok user",
        "author_handle":  f"@{user.get('username')}" if user.get("username") else "",
        "author_picture": user.get("avatar_url") or "",
        "message":        item.get("text") or "",
        "created_at":     created_at,
        "like_count":     int(item.get("like_count") or 0),
        "reply_count":    int(item.get("reply_count") or 0),
        "parent_id":      parent_id,
        "platform":       "tiktok",
    }


def fetch_comments(account, db, video_id: str):
    """List top-level comments + their replies on a TikTok video.
    Returns `(comments_list, error_or_None)`.

    The list is flat (top-level + replies interleaved) — every reply's
    `parent_id` points back at its top-level comment so the frontend can
    rebuild the tree client-side without making N+1 API calls.

    Replies are fetched lazily per top-level comment that has reply_count
    > 0, capped at the API's first page (30 replies) to keep the round-trip
    count reasonable. UI can surface "view more on TikTok" affordance for
    longer threads.
    """
    if not video_id:
        return [], "missing video_id"

    access_token, err = refresh_tiktok_access_token(account, db)
    if not access_token:
        return [], err or "could not refresh TikTok access token"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json",
    }

    # ── 1. Top-level comments ─────────────────────────────────────────────
    try:
        resp = requests.post(
            _COMMENT_LIST_URL,
            headers=headers,
            json={"video_id": video_id, "max_count": _PAGE_SIZE},
            timeout=15,
        )
    except Exception as exc:
        return [], f"TikTok comments fetch network error: {exc}"

    if resp.status_code == 403:
        # Most common cause in sandbox: the comment scopes aren't approved
        # for an unaudited client. Surface a clear message.
        return [], (
            "TikTok comments forbidden — your app likely needs Content Sharing audit "
            "approval for comment.list scope. Sandbox apps cannot read comments."
        )
    if resp.status_code != 200:
        logger.error(f"[TT_COMMENTS] fetch failed {resp.status_code}: {resp.text[:300]}")
        return [], f"TikTok comments HTTP {resp.status_code}: {resp.text[:200]}"

    payload = resp.json() or {}
    err_code = (payload.get("error") or {}).get("code", "")
    if err_code and err_code != "ok":
        msg = (payload.get("error") or {}).get("message", "")
        return [], f"TikTok comments API error: {err_code} {msg}"

    top_items = ((payload.get("data") or {}).get("comments")) or []

    out: list[dict] = []
    for c in top_items:
        top = _normalize_comment(c)
        out.append(top)

        # ── 2. Replies for this top-level comment, if any ─────────────────
        # Only paginate-1 (first ~30 replies). If a thread is bigger the
        # frontend can deep-link to TikTok web for the full thread.
        if top["reply_count"] > 0 and top["id"]:
            try:
                rresp = requests.post(
                    _COMMENT_REPLY_LIST_URL,
                    headers=headers,
                    json={
                        "video_id":   video_id,
                        "comment_id": top["id"],
                        "max_count":  _PAGE_SIZE,
                    },
                    timeout=15,
                )
            except Exception as exc:
                logger.warning(f"[TT_COMMENTS] reply.list net error for {top['id']}: {exc}")
                continue

            if rresp.status_code != 200:
                # Don't fail the whole fetch if one thread's replies are
                # unreadable — just log and move on.
                logger.warning(
                    f"[TT_COMMENTS] reply.list failed {rresp.status_code} for {top['id']}"
                )
                continue

            rpayload = rresp.json() or {}
            for r in ((rpayload.get("data") or {}).get("comments")) or []:
                out.append(_normalize_comment(r, parent_id=top["id"]))

    logger.info(
        f"[TT_COMMENTS] fetched {len(out)} comment(s) for video {video_id} "
        f"({len(top_items)} top-level)"
    )
    return out, None


def post_reply(account, db, parent_id: str, text: str):
    """Post a reply to an existing TikTok comment.
    Returns `(new_comment_id, error_or_None)`.

    `parent_id` MUST be a comment id — TikTok's API has no concept of
    "reply to the video itself" (that's a top-level comment, which only
    the public TikTok app can create). If the UI lets a user start a new
    thread, that's a no-op via the API; we'd need TikTok's content
    creation scope which is gated on audit.
    """
    if not parent_id:
        return None, "missing parent_id (TikTok requires a comment to reply to)"
    text = (text or "").strip()
    if not text:
        return None, "reply text is empty"
    # TikTok soft-limits comments around 150 chars in the UI but the API
    # accepts up to 1000. We allow the longer form so power-user replies
    # don't get truncated server-side.
    if len(text) > 1000:
        return None, "reply text exceeds 1000 characters"

    access_token, err = refresh_tiktok_access_token(account, db)
    if not access_token:
        return None, err or "could not refresh TikTok access token"

    try:
        resp = requests.post(
            _COMMENT_REPLY_CREATE_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type":  "application/json",
            },
            json={"comment_id": parent_id, "text": text},
            timeout=20,
        )
    except Exception as exc:
        return None, f"TikTok reply network error: {exc}"

    if resp.status_code == 403:
        return None, (
            "TikTok reply forbidden — your app likely needs Content Sharing audit "
            "approval for comment.list.manage scope. Sandbox apps cannot reply."
        )
    if resp.status_code not in (200, 201):
        logger.error(f"[TT_COMMENTS] reply failed {resp.status_code}: {resp.text[:300]}")
        return None, f"TikTok reply HTTP {resp.status_code}: {resp.text[:200]}"

    payload = resp.json() or {}
    err_code = (payload.get("error") or {}).get("code", "")
    if err_code and err_code != "ok":
        msg = (payload.get("error") or {}).get("message", "")
        return None, f"TikTok reply API error: {err_code} {msg}"

    new_id = ((payload.get("data") or {}).get("comment") or {}).get("id")
    if not new_id:
        return None, "TikTok returned no comment id on reply"
    logger.info(f"[TT_COMMENTS] posted reply {new_id} under parent {parent_id}")
    return new_id, None
