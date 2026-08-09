"""YouTube video publisher (Phase 2 — Manual Post → Video Mode).

Refreshes the user's Google OAuth access token, downloads the video bytes from
the S3 URL the rest of the publishing pipeline already produced, and uploads
to the YouTube Data API v3 via simple multipart upload.

We use multipart (not resumable) because:
  - Videos posted from the Manual Post flow are typically < 100 MB.
  - Multipart is one round-trip instead of three.
  - The S3 bucket is in the same AWS region as the Lambda → the download is
    fast enough to stay inside the publish-request timeout.

If we hit videos that exceed the multipart 100 MB practical limit (the
documented hard limit is 256 MB but Google's reverse proxy tends to time out
above ~100 MB), we'll swap this for the resumable protocol — see the
`# TODO resumable` comments below.

This module deliberately does NOT depend on `google-api-python-client` so we
stay light on Lambda cold-start and avoid pulling in a 30 MB dependency tree.
Raw `requests` calls cover everything Phase 1 / 2 need.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from core.config import YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET

logger = logging.getLogger("pipelyt.youtube")

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_UPLOAD_URL = (
    "https://www.googleapis.com/upload/youtube/v3/videos"
    "?uploadType=multipart&part=snippet,status"
)

# Default category for the dropdown. The frontend always sends one explicitly,
# but if the caller omits it we fall back here so we never error on a missing
# field. 22 = "People & Blogs" — YouTube's catch-all bucket.
_DEFAULT_CATEGORY_ID = "22"

# Hard cap matching YouTube's documented limit. We refuse anything bigger
# rather than letting the upload time out + waste the user's quota.
_MAX_VIDEO_BYTES = 256 * 1024 * 1024  # 256 MB


def refresh_youtube_access_token(account, db) -> tuple[str | None, str | None]:
    """Swap the SocialAccount's refresh_token for a fresh access_token.

    Persists the new access_token back to the row so subsequent posts in the
    same minute can reuse it. Returns `(token, error)` — exactly one is set.
    """
    if not account or not account.refresh_token:
        return None, "no refresh_token on this YouTube account — reconnect required"
    if not YOUTUBE_CLIENT_ID or not YOUTUBE_CLIENT_SECRET:
        return None, "YouTube OAuth client not configured on the server"

    try:
        resp = requests.post(
            _TOKEN_URL,
            data={
                "client_id": YOUTUBE_CLIENT_ID,
                "client_secret": YOUTUBE_CLIENT_SECRET,
                "refresh_token": account.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
    except Exception as exc:
        logger.error(f"[YT_PUB] token refresh network error: {exc}")
        return None, f"token refresh network error: {exc}"

    if resp.status_code != 200:
        logger.error(f"[YT_PUB] token refresh failed {resp.status_code}: {resp.text}")
        # Google returns 400 'invalid_grant' when the refresh_token has been
        # revoked (user clicked "Remove access" in Google Account settings).
        # Surface that explicitly so the UI can prompt a reconnect.
        try:
            err = resp.json().get("error", "")
        except Exception:
            err = ""
        if err == "invalid_grant":
            return None, "YouTube authorization expired — please reconnect the channel"
        return None, f"token refresh failed: {resp.text}"

    data = resp.json()
    new_access = data.get("access_token")
    if not new_access:
        return None, "token refresh returned no access_token"

    # Persist for the next post so we don't burn refresh-token quota.
    account.token = new_access
    try:
        db.commit()
    except Exception as exc:
        logger.warning(f"[YT_PUB] failed to persist refreshed token: {exc}")
        db.rollback()

    return new_access, None


def _download_video(url: str) -> tuple[bytes | None, str | None]:
    """Pull the video bytes from S3 (or wherever).

    Returns `(body, error)` — exactly one set. Refuses anything > 256 MB up
    front so we don't blow Lambda memory or time out the upload.
    """
    try:
        head = requests.head(url, timeout=15, allow_redirects=True)
        size = int(head.headers.get("Content-Length") or 0)
        if size and size > _MAX_VIDEO_BYTES:
            return None, f"video is {size // (1024*1024)} MB — YouTube max is 256 MB"
    except Exception as exc:
        # HEAD failing is non-fatal — some S3 configs disallow it. Push on
        # to GET and rely on Content-Length there.
        logger.info(f"[YT_PUB] HEAD probe failed (continuing): {exc}")

    try:
        resp = requests.get(url, timeout=120, stream=True)
        resp.raise_for_status()
        total = 0
        chunks = []
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > _MAX_VIDEO_BYTES:
                return None, f"video exceeded 256 MB during download"
            chunks.append(chunk)
        return b"".join(chunks), None
    except Exception as exc:
        return None, f"video download failed: {exc}"


def publish_video(
    *,
    account,                       # SocialAccount row for the YouTube channel
    db,                            # SQLAlchemy session — used for token persist
    video_url: str,                # S3 URL of the user's uploaded video
    title: str,                    # 1–100 chars
    description: str = "",         # 0–5000 chars
    tags: list[str] | None = None, # already split from the comma input
    category_id: str | int = _DEFAULT_CATEGORY_ID,
    privacy: str = "public",       # "public" | "unlisted" | "private"
) -> dict[str, Any]:
    """Upload a video to YouTube. Returns a dict matching the shape the rest
    of `process_publishing_internal` expects, plus a `native_post_id` field.

    Shape:
      success → {"ok": True, "video_id": "...", "url": "https://...", "native_post_id": "..."}
      failure → {"ok": False, "error": "<user-facing message>"}
    """
    _t0 = time.monotonic()

    # ── Validate inputs early so we don't waste a token refresh on garbage ──
    if not video_url:
        return {"ok": False, "error": "no video file attached"}
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "YouTube title is required"}
    if len(title) > 100:
        return {"ok": False, "error": "YouTube title must be 100 characters or fewer"}
    if len(description or "") > 5000:
        return {"ok": False, "error": "YouTube description must be 5000 characters or fewer"}
    if privacy not in ("public", "unlisted", "private"):
        privacy = "public"
    tags = [t for t in (tags or []) if t and t.strip()][:30]  # YouTube hard-caps tag count

    # ── Refresh access token ──────────────────────────────────────────────
    access_token, err = refresh_youtube_access_token(account, db)
    if not access_token:
        return {"ok": False, "error": err or "token refresh failed"}

    # ── Download video bytes ──────────────────────────────────────────────
    video_bytes, err = _download_video(video_url)
    if not video_bytes:
        return {"ok": False, "error": err or "could not fetch video file"}

    logger.info(
        f"[YT_PUB] preparing upload — channel={account.account_id} "
        f"title={title!r} bytes={len(video_bytes)} privacy={privacy}"
    )

    # ── Build multipart/related body ──────────────────────────────────────
    # YouTube's "multipart" upload is RFC 2387 multipart/related (NOT
    # multipart/form-data). Two parts:
    #   1. application/json metadata (snippet + status)
    #   2. video/* binary
    # We build the body by hand because `requests`' multipart helper produces
    # form-data, which YouTube rejects.
    boundary = "pipelyt_yt_upload_boundary_x7f3a9c1d8b2e"
    metadata = {
        "snippet": {
            "title": title,
            "description": description or "",
            "tags": tags,
            "categoryId": str(category_id or _DEFAULT_CATEGORY_ID),
        },
        "status": {
            "privacyStatus": privacy,
            # Do NOT mark videos as for kids — that would lock down comments
            # + recommendations on every upload. Users who specifically need
            # MFK can change it in YouTube Studio post-publish.
            "selfDeclaredMadeForKids": False,
            "embeddable": True,
        },
    }

    body_parts = [
        f"--{boundary}".encode(),
        b"Content-Type: application/json; charset=UTF-8",
        b"",
        json.dumps(metadata).encode(),
        f"--{boundary}".encode(),
        b"Content-Type: video/*",
        b"",
        video_bytes,
        f"--{boundary}--".encode(),
    ]
    body = b"\r\n".join(body_parts)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": f"multipart/related; boundary={boundary}",
        "Content-Length": str(len(body)),
    }

    # ── POST to YouTube ───────────────────────────────────────────────────
    try:
        # 5 min timeout — large videos take time even over fast networks.
        resp = requests.post(_UPLOAD_URL, headers=headers, data=body, timeout=300)
    except Exception as exc:
        logger.error(f"[YT_PUB] upload network error: {exc}")
        return {"ok": False, "error": f"upload network error: {exc}"}

    elapsed = time.monotonic() - _t0
    if resp.status_code not in (200, 201):
        # Try to surface YouTube's specific error message — much more useful
        # than a generic 400.
        try:
            err_body = resp.json()
            err_msg = err_body.get("error", {}).get("message") or resp.text
        except Exception:
            err_msg = resp.text
        logger.error(f"[YT_PUB] upload failed in {elapsed:.1f}s — {resp.status_code}: {err_msg}")
        return {"ok": False, "error": f"YouTube upload failed: {err_msg}"}

    try:
        data = resp.json()
    except Exception as exc:
        logger.error(f"[YT_PUB] unparseable success response: {exc} body={resp.text[:200]}")
        return {"ok": False, "error": "YouTube returned an unparseable response"}

    video_id = data.get("id")
    if not video_id:
        return {"ok": False, "error": "YouTube returned no video id"}

    public_url = f"https://www.youtube.com/watch?v={video_id}"
    logger.info(
        f"[YT_PUB] uploaded video {video_id} in {elapsed:.1f}s — {public_url}"
    )
    return {
        "ok": True,
        "video_id": video_id,
        "native_post_id": video_id,
        "url": public_url,
    }
