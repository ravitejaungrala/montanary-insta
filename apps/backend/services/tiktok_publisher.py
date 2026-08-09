"""TikTok video publisher (Phase 2 — Manual Post → TikTok video mode).

Refreshes the user's TikTok access_token, downloads the video bytes from the
S3 URL Pipelyt produced at upload time, and publishes via the Content
Posting API's **Direct Post** flow (push_by_file method).

TikTok Direct Post is a 3-step protocol:
  1. POST /v2/post/publish/video/init/   → returns {publish_id, upload_url}
  2. PUT chunks → upload_url             → TikTok ingests video bytes
  3. POST /v2/post/publish/status/fetch/ → poll until status=PUBLISH_COMPLETE

Why push_by_file (not pull_by_url):
  - pull_by_url requires the host domain to be verified in the TikTok
    Developer Portal — Pipelyt's S3 bucket isn't (and shouldn't be) a
    verified domain.
  - push_by_file works for any video source as long as we can read the bytes.

Tokens:
  - access_token expires in 24 hours
  - refresh_token lasts 365 days
  - We persist a refreshed access_token back to SocialAccount.token so subsequent
    publishes in the same session don't burn refresh-token quota.

This module deliberately uses raw `requests` (not the TikTok SDK — there's
no Python SDK Pipelyt should depend on for cold-start size reasons).
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from core.config import TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET

logger = logging.getLogger("pipelyt.tiktok")

# ── TikTok endpoints (Open API v2) ─────────────────────────────────────────
_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

# TikTok's video limits (per Content Posting API docs).
_MAX_VIDEO_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB hard limit per spec
_DEFAULT_CHUNK_BYTES = 10 * 1024 * 1024     # 10 MB — TikTok's recommended chunk size
_MAX_CHUNK_BYTES = 64 * 1024 * 1024         # 64 MB — TikTok's max chunk size; videos
                                            # under this go as a single chunk to keep
                                            # init params trivially correct.

# Valid privacy levels for Direct Post. TikTok rejects anything else with
# a 400. We mirror their exact strings so the frontend can pass through.
_VALID_PRIVACY = {
    "PUBLIC_TO_EVERYONE",
    "MUTUAL_FOLLOW_FRIENDS",
    "FOLLOWER_OF_CREATOR",
    "SELF_ONLY",
}

# Status-poll cadence. TikTok takes 5-30 seconds typically; we cap at 3 min
# to avoid hanging the publish request indefinitely on a stuck encode.
_POLL_INTERVAL_SECONDS = 5
_POLL_MAX_SECONDS = 180


def refresh_tiktok_access_token(account, db) -> tuple[str | None, str | None]:
    """Swap the SocialAccount's refresh_token for a fresh access_token.

    Persists the new access_token + refresh_token back to the row — TikTok
    rotates the refresh_token on every refresh, so we must save BOTH.
    Returns `(token, error)` — exactly one is set.
    """
    if not account or not account.refresh_token:
        return None, "no refresh_token on this TikTok account — reconnect required"
    if not TIKTOK_CLIENT_KEY or not TIKTOK_CLIENT_SECRET:
        return None, "TikTok OAuth client not configured on the server"

    try:
        resp = requests.post(
            _TOKEN_URL,
            data={
                "client_key": TIKTOK_CLIENT_KEY,
                "client_secret": TIKTOK_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": account.refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
    except Exception as exc:
        logger.error(f"[TT_PUB] token refresh network error: {exc}")
        return None, f"token refresh network error: {exc}"

    if resp.status_code != 200:
        logger.error(f"[TT_PUB] token refresh failed {resp.status_code}: {resp.text}")
        return None, f"token refresh failed: {resp.text}"

    data = resp.json() or {}
    new_access = data.get("access_token")
    new_refresh = data.get("refresh_token")  # TikTok rotates this!
    if not new_access:
        return None, "token refresh returned no access_token"

    # Persist both — refresh_token rotation means next call will fail if we
    # don't save the new one.
    account.token = new_access
    if new_refresh:
        account.refresh_token = new_refresh
    try:
        db.commit()
    except Exception as exc:
        logger.warning(f"[TT_PUB] failed to persist refreshed tokens: {exc}")
        db.rollback()

    return new_access, None


def _download_video(url: str) -> tuple[bytes | None, str | None]:
    """Pull the video bytes from S3 (or wherever). Same approach as YouTube
    publisher — HEAD probe for size, stream GET with cap to avoid blowing
    Lambda memory.
    """
    try:
        head = requests.head(url, timeout=15, allow_redirects=True)
        size = int(head.headers.get("Content-Length") or 0)
        if size and size > _MAX_VIDEO_BYTES:
            return None, f"video is {size // (1024*1024)} MB — TikTok max is 4 GB"
    except Exception as exc:
        # HEAD failing is non-fatal — some S3 configs disallow it.
        logger.info(f"[TT_PUB] HEAD probe failed (continuing): {exc}")

    try:
        resp = requests.get(url, timeout=180, stream=True)
        resp.raise_for_status()
        total = 0
        chunks = []
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > _MAX_VIDEO_BYTES:
                return None, "video exceeded 4 GB during download"
            chunks.append(chunk)
        return b"".join(chunks), None
    except Exception as exc:
        return None, f"video download failed: {exc}"


def _compute_source_info(video_bytes: int) -> dict:
    """Build TikTok's `source_info` block with valid chunk math.

    TikTok's rule (often missed): the LAST chunk is the REMAINDER added to a
    chunk_size-sized chunk, NOT a chunk smaller than chunk_size. That makes
    the right formula `total_chunk_count = floor(video / chunk)`, not ceil.

    For videos under 64 MB (TikTok's max chunk size) we just use a single
    chunk equal to the file size — simpler and avoids the floor/ceil trap.
    """
    if video_bytes <= _MAX_CHUNK_BYTES:
        return {
            "source": "FILE_UPLOAD",
            "video_size": video_bytes,
            "chunk_size": video_bytes,
            "total_chunk_count": 1,
        }
    # Multi-chunk path: chunk_size = 10 MB, last chunk absorbs the remainder
    # and ends up slightly larger than chunk_size (valid per TikTok spec).
    chunk_size = _DEFAULT_CHUNK_BYTES
    total_chunk_count = video_bytes // chunk_size  # floor — not ceil
    return {
        "source": "FILE_UPLOAD",
        "video_size": video_bytes,
        "chunk_size": chunk_size,
        "total_chunk_count": total_chunk_count,
    }


def _initiate_publish(
    access_token: str,
    *,
    video_bytes: int,
    caption: str,
    privacy: str,
    disable_duet: bool = False,
    disable_comment: bool = False,
    disable_stitch: bool = False,
) -> tuple[dict | None, str | None]:
    """Call POST /v2/post/publish/video/init/ to begin a Direct Post.

    Returns `(data, error)` — exactly one set. On success, `data` contains
    `publish_id` and `upload_url`. The upload_url is a TikTok-signed URL we
    PUT the video bytes to in the next step.
    """
    body = {
        "post_info": {
            "title": caption,
            "privacy_level": privacy,
            "disable_duet": disable_duet,
            "disable_comment": disable_comment,
            "disable_stitch": disable_stitch,
            "video_cover_timestamp_ms": 1000,  # use the frame at 1s as the cover
        },
        "source_info": _compute_source_info(video_bytes),
    }

    try:
        resp = requests.post(
            _INIT_URL,
            json=body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            timeout=30,
        )
    except Exception as exc:
        return None, f"init network error: {exc}"

    if resp.status_code != 200:
        logger.error(f"[TT_PUB] init failed {resp.status_code}: {resp.text}")
        return None, f"init failed: {resp.text}"

    payload = resp.json() or {}
    # TikTok wraps the response: { "data": {...}, "error": {"code": "ok"} }
    err = (payload.get("error") or {}).get("code", "")
    if err and err != "ok":
        msg = (payload.get("error") or {}).get("message", "")
        return None, f"init API error: {err} {msg}"

    data = payload.get("data") or {}
    if not data.get("publish_id") or not data.get("upload_url"):
        return None, f"init response missing publish_id/upload_url: {payload}"

    return data, None


def _upload_chunks(upload_url: str, video_data: bytes) -> str | None:
    """PUT the video bytes to TikTok's signed upload URL in chunks.

    Returns None on success, or an error string. TikTok responds with 201
    Created for each accepted chunk and 200 OK for the final chunk.

    Chunk math MUST match `_compute_source_info`: the last chunk absorbs
    the remainder (i.e. ends up at chunk_size + remainder bytes), it does
    NOT come out smaller than chunk_size. Otherwise TikTok rejects with
    "The total chunk count is invalid".
    """
    total = len(video_data)
    src = _compute_source_info(total)
    chunk = src["chunk_size"]
    total_chunks = src["total_chunk_count"]
    offset = 0
    chunk_index = 0

    while offset < total:
        chunk_index += 1
        # Last chunk: everything remaining (could be > chunk_size if there
        # was a remainder). All other chunks: exactly chunk_size.
        is_last = (chunk_index == total_chunks)
        end = total if is_last else min(offset + chunk, total)
        body = video_data[offset:end]
        # Content-Range header tells TikTok which byte range this chunk is.
        # Format: "bytes <start>-<end-1>/<total>"
        range_header = f"bytes {offset}-{end - 1}/{total}"

        try:
            resp = requests.put(
                upload_url,
                data=body,
                headers={
                    "Content-Range": range_header,
                    "Content-Type": "video/mp4",  # TikTok accepts mp4 + mov; we always upload as mp4
                    "Content-Length": str(len(body)),
                },
                timeout=120,
            )
        except Exception as exc:
            return f"chunk upload network error at offset={offset}: {exc}"

        # 201 = chunk accepted, more expected. 200 = upload complete.
        # Anything else = error.
        if resp.status_code not in (200, 201, 206):
            return (
                f"chunk upload failed at offset={offset} status={resp.status_code}: "
                f"{resp.text[:300]}"
            )

        offset = end

    return None


def _poll_publish_status(access_token: str, publish_id: str) -> tuple[str | None, str | None]:
    """Poll until TikTok finishes processing the video.

    Returns `(public_video_id, error)`. On success, public_video_id is the
    canonical TikTok video ID we store as native_post_id for analytics later.
    """
    elapsed = 0
    while elapsed < _POLL_MAX_SECONDS:
        try:
            resp = requests.post(
                _STATUS_URL,
                json={"publish_id": publish_id},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                timeout=15,
            )
        except Exception as exc:
            return None, f"status poll network error: {exc}"

        if resp.status_code != 200:
            return None, f"status poll failed {resp.status_code}: {resp.text}"

        payload = resp.json() or {}
        data = payload.get("data") or {}
        status = data.get("status", "")

        if status == "PUBLISH_COMPLETE":
            # publicaly_available_post_id is the canonical TikTok video ID.
            # We use it as native_post_id so analytics + comments can target
            # this exact post later. Sometimes TikTok puts it in a list.
            ids = data.get("publicaly_available_post_id") or []
            if isinstance(ids, list) and ids:
                return str(ids[0]), None
            return publish_id, None  # fallback — still useful for status checks
        if status == "FAILED":
            fail_reason = data.get("fail_reason", "unknown")
            return None, f"TikTok rejected the video: {fail_reason}"

        # PROCESSING_UPLOAD, PROCESSING_DOWNLOAD, etc — keep polling
        time.sleep(_POLL_INTERVAL_SECONDS)
        elapsed += _POLL_INTERVAL_SECONDS

    return None, f"timed out after {_POLL_MAX_SECONDS}s waiting for TikTok to publish"


def publish_video(
    *,
    account,                       # SocialAccount row for the TikTok account
    db,                            # SQLAlchemy session — used for token persist
    video_url: str,                # S3 URL of the user's uploaded video
    caption: str = "",             # 0–2200 chars; first 150 shown above the fold
    privacy: str = "SELF_ONLY",    # SELF_ONLY is the safest default for sandbox testing
    disable_duet: bool = False,
    disable_comment: bool = False,
    disable_stitch: bool = False,
) -> dict[str, Any]:
    """Publish a video to TikTok via the Direct Post API.

    Returns a dict matching the shape the rest of `process_publishing_internal`
    expects, plus `native_post_id` for analytics later.

    Shape:
      success → {"ok": True, "video_id": "...", "url": "https://...", "native_post_id": "..."}
      failure → {"ok": False, "error": "<user-facing message>"}
    """
    _t0 = time.monotonic()

    # ── Validate inputs early ──────────────────────────────────────────────
    if not video_url:
        return {"ok": False, "error": "no video file attached"}

    caption = (caption or "").strip()
    if len(caption) > 2200:
        return {"ok": False, "error": "TikTok caption must be 2200 characters or fewer"}

    privacy = (privacy or "SELF_ONLY").strip().upper()
    if privacy not in _VALID_PRIVACY:
        return {
            "ok": False,
            "error": f"invalid privacy '{privacy}' — must be one of {sorted(_VALID_PRIVACY)}",
        }

    # ── 1. Refresh access token ────────────────────────────────────────────
    access_token, err = refresh_tiktok_access_token(account, db)
    if not access_token:
        return {"ok": False, "error": err}

    # ── 2. Download the video from S3 ──────────────────────────────────────
    video_data, err = _download_video(video_url)
    if not video_data:
        return {"ok": False, "error": err}
    video_size = len(video_data)
    logger.info(f"[TT_PUB] downloaded {video_size // 1024} KB from S3")

    # ── 3. Init publish — get publish_id + upload_url ──────────────────────
    init_data, err = _initiate_publish(
        access_token,
        video_bytes=video_size,
        caption=caption,
        privacy=privacy,
        disable_duet=disable_duet,
        disable_comment=disable_comment,
        disable_stitch=disable_stitch,
    )
    if not init_data:
        return {"ok": False, "error": err}
    publish_id = init_data["publish_id"]
    upload_url = init_data["upload_url"]
    logger.info(f"[TT_PUB] init ok publish_id={publish_id}")

    # ── 4. Upload chunks ───────────────────────────────────────────────────
    err = _upload_chunks(upload_url, video_data)
    if err:
        return {"ok": False, "error": err}
    logger.info(f"[TT_PUB] upload chunks ok ({video_size // 1024} KB)")

    # ── 5. Poll status until PUBLISH_COMPLETE ──────────────────────────────
    public_id, err = _poll_publish_status(access_token, publish_id)
    if not public_id:
        return {"ok": False, "error": err}

    elapsed = round(time.monotonic() - _t0, 1)
    # Build a direct URL to the user's posted video. account.account_id is
    # the TikTok open_id which doesn't directly map to the @handle — use the
    # stored `account.name` (display name) when constructing a friendly URL.
    # If we don't have a username we still surface the post ID for support.
    video_url_out = f"https://www.tiktok.com/@{account.name or 'me'}/video/{public_id}"

    logger.info(
        f"[TT_PUB] published video_id={public_id} in {elapsed}s "
        f"(account={account.id} privacy={privacy})"
    )

    return {
        "ok": True,
        "video_id": public_id,
        "url": video_url_out,
        "native_post_id": public_id,
    }
