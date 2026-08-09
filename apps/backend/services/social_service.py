import requests
import base64
import json
import logging
import time
from requests_oauthlib import OAuth1Session
from core.config import (
    TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET,
    TWITTER_CLIENT_ID, TWITTER_CLIENT_SECRET, TWITTER_BEARER_TOKEN
)

logger = logging.getLogger("pipelyt.social")

# LinkedIn REST API version — Documents + Posts APIs require this header.
# Format is YYYYMM. LinkedIn only keeps ~12 months active; per the docs page
# (li-lms-2026-04 is current default), valid range is 202505 → 202604 as of
# April 2026. Bump when the API returns 426 NONEXISTENT_VERSION.
LINKEDIN_REST_VERSION = "202603"

# In-memory throttle for /2/tweets/:id/quote_tweets — X caps this endpoint
# tight (~75/15min per app) and every 0-reply fetch was hammering it. On a
# 429, skip further calls for 15 min so the log doesn't fill with 429s.
# Simple monotonic (not persisted across reloads) — good enough.
_TW_QUOTES_BACKOFF_UNTIL = 0.0

# In-memory throttle for /2/tweets/:id/quote_tweets — X caps this at 75 req /
# 15 min per app, and every 0-reply fetch was hammering it. When we hit a 429,
# stop trying quotes for the rest of the window; next call after this timestamp
# retries. Simple monotonic (not persisted across reloads) — good enough.
_TW_QUOTES_BACKOFF_UNTIL = 0.0


# ──────────────────────────────────────────────────────────────────
# Metadata strippers — clean image/PDF bytes before uploading
# ──────────────────────────────────────────────────────────────────
# Why we do this:
#   * OpenAI's gpt-image-2 embeds a C2PA "Content Credentials" manifest
#     in every PNG it produces. LinkedIn renders that as the "cr" badge
#     on the image — visible to everyone scrolling past the post, looks
#     like an "AI-generated" watermark. Stripping it removes the badge.
#   * Images / PDFs can also carry EXIF (GPS coords, camera info),
#     XMP/IPTC metadata, author / producer fields, embedded thumbnails,
#     and ICC profiles. None of that should leak into a public post.
#
# Approach:
#   * Images: re-encode via PIL with NO `info` dict. PIL silently drops
#     anything it doesn't recognize (including C2PA `iTXt` chunks).
#   * PDFs: pypdf strips /Info dict and XMP metadata stream.
#
# Strip is best-effort. If anything fails, we log and fall through with
# the original bytes — a broken strip should NEVER prevent a publish.

def _strip_image_metadata(image_bytes: bytes) -> bytes:
    """Return a re-encoded copy of image_bytes with all metadata removed
    (EXIF, XMP, ICC, C2PA Content Credentials, embedded thumbnails)."""
    try:
        from PIL import Image as _PIL
        from io import BytesIO as _BIO
        src = _PIL.open(_BIO(image_bytes))
        # Determine output format — keep PNG as PNG, everything else JPEG.
        # JPEG produces smaller files which LinkedIn prefers; PNG kept only
        # when source was PNG (might have transparency).
        out_fmt = "PNG" if (src.format or "").upper() == "PNG" else "JPEG"
        if out_fmt == "JPEG" and src.mode in ("RGBA", "LA", "P"):
            src = src.convert("RGB")  # JPEG doesn't support alpha
        # Build a CLEAN copy via .data — this drops every PIL `info` entry,
        # which is where EXIF/XMP/ICC/C2PA all live.
        clean = _PIL.new(src.mode, src.size)
        clean.putdata(list(src.getdata()))
        out = _BIO()
        if out_fmt == "JPEG":
            clean.save(out, format="JPEG", quality=92, optimize=True)
        else:
            clean.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception as exc:
        logger.warning(f"image metadata strip failed (using original bytes): {exc}")
        return image_bytes


def _strip_pdf_metadata(pdf_bytes: bytes) -> bytes:
    """Return a copy of pdf_bytes with /Info dict + XMP metadata cleared.
    Page content is preserved exactly."""
    try:
        from pypdf import PdfReader, PdfWriter
        from io import BytesIO as _BIO
        reader = PdfReader(_BIO(pdf_bytes))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        # Overwrite /Info dict with a single empty Producer so PDF stays
        # technically valid (some readers refuse no-info PDFs).
        writer.add_metadata({"/Producer": ""})
        # Remove XMP metadata stream if present.
        try:
            if writer._root_object and "/Metadata" in writer._root_object:
                del writer._root_object["/Metadata"]
        except Exception:
            pass
        out = _BIO()
        writer.write(out)
        return out.getvalue()
    except Exception as exc:
        logger.warning(f"pdf metadata strip failed (using original bytes): {exc}")
        return pdf_bytes

def refresh_twitter_token_v2(account, db):
    """Refreshes a Twitter OAuth 2.0 access token using the stored refresh_token."""
    if not account.refresh_token:
        logger.warning("Twitter refresh skipped - no refresh_token stored")
        return None
    if not TWITTER_CLIENT_ID or not TWITTER_CLIENT_SECRET:
        logger.error("Twitter refresh failed - Client ID/Secret not configured")
        return None
    try:
        token_url = "https://api.twitter.com/2/oauth2/token"
        auth_str = f"{TWITTER_CLIENT_ID}:{TWITTER_CLIENT_SECRET}"
        encoded_auth = base64.b64encode(auth_str.encode()).decode()
        
        data = {
            "grant_type": "refresh_token",
            "refresh_token": account.refresh_token,
            "client_id": TWITTER_CLIENT_ID
        }
        headers = {
            "Authorization": f"Basic {encoded_auth}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        res = requests.post(token_url, data=data, headers=headers)
        if res.status_code == 200:
            new_data = res.json()
            account.token = new_data.get("access_token")
            account.refresh_token = new_data.get("refresh_token")
            db.commit()
            logger.info(f"Twitter token refreshed for account: {account.account_id}")
            return account.token
        else:
            logger.error(f"Twitter refresh failed: {res.status_code} {res.text}")
            return None
    except Exception as e:
        logger.error(f"Twitter refresh exception: {str(e)}")
        return None

def linkedin_upload_image(token, author_urn, image_path_or_url):
    """Register + upload an image to LinkedIn's asset API. Returns (asset_urn, error_msg).

    Rewritten April 2026 — previous version silently returned None on ANY failure,
    so media upload failures looked like successful text-only posts. Every branch
    now logs the LinkedIn response body so we can diagnose the actual issue.
    """
    # ---- 0. pre-flight validation ----
    if not token:
        logger.error("LI_UPLOAD: no token provided")
        return None, "LinkedIn access token missing"
    if not author_urn:
        logger.error("LI_UPLOAD: no author_urn provided")
        return None, "LinkedIn author URN missing"
    if not author_urn.startswith("urn:li:"):
        # Common bug: stored as bare "xxxxx" instead of "urn:li:person:xxxxx".
        # Fix up for /v2/ugcPosts compatibility (assumes person, not organization).
        logger.warning(f"LI_UPLOAD: author_urn missing urn:li: prefix ({author_urn!r}) — auto-prefixing as person")
        author_urn = f"urn:li:person:{author_urn}"
    if not image_path_or_url:
        logger.error("LI_UPLOAD: no image_path_or_url provided")
        return None, "Image URL missing"

    # ---- 1. Fetch image bytes first (so we fail fast on bad URL) ----
    try:
        if image_path_or_url.startswith("data:image"):
            image_data = base64.b64decode(image_path_or_url.split(",", 1)[1])
            logger.info(f"LI_UPLOAD: using data-URL image ({len(image_data):,} bytes)")
        elif image_path_or_url.startswith("http"):
            img_res = requests.get(image_path_or_url, timeout=30)
            if img_res.status_code != 200:
                logger.error(f"LI_UPLOAD: image fetch failed — status={img_res.status_code} url={image_path_or_url}")
                return None, f"Image fetch failed: HTTP {img_res.status_code}"
            image_data = img_res.content
            logger.info(f"LI_UPLOAD: fetched {len(image_data):,} bytes from {image_path_or_url}")
        else:
            logger.error(f"LI_UPLOAD: unsupported image_path_or_url scheme: {image_path_or_url[:40]}...")
            return None, "Unsupported image URL scheme"
    except Exception as exc:
        logger.exception(f"LI_UPLOAD: image fetch exception: {exc}")
        return None, f"Image fetch exception: {exc}"

    # ---- 1b. Strip metadata BEFORE upload ----
    # Removes OpenAI's C2PA Content Credentials (the "cr" badge LinkedIn
    # shows on AI-generated images) + EXIF/XMP/ICC profiles. Best-effort —
    # if strip fails we use the original bytes and still publish.
    _orig_size = len(image_data)
    image_data = _strip_image_metadata(image_data)
    logger.info(f"LI_UPLOAD: stripped metadata — {_orig_size:,} -> {len(image_data):,} bytes")

    # ---- 2. Register the upload ----
    register_url = "https://api.linkedin.com/v2/assets?action=registerUpload"
    headers_reg = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }
    register_data = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": author_urn,
            "serviceRelationships": [
                {"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}
            ],
        }
    }
    try:
        reg_res = requests.post(register_url, headers=headers_reg, json=register_data, timeout=30)
    except Exception as exc:
        logger.exception(f"LI_UPLOAD: register exception: {exc}")
        return None, f"Register request failed: {exc}"

    if reg_res.status_code != 200:
        logger.error(
            f"LI_UPLOAD: registerUpload failed — status={reg_res.status_code} "
            f"body={reg_res.text[:500]} author_urn={author_urn}"
        )
        return None, f"LinkedIn registerUpload HTTP {reg_res.status_code}: {reg_res.text[:200]}"

    # ---- 3. Extract upload URL + asset URN ----
    try:
        rj = reg_res.json()
        upload_url = rj["value"]["uploadMechanism"][
            "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
        ]["uploadUrl"]
        asset_urn = rj["value"]["asset"]
        logger.info(f"LI_UPLOAD: register OK — asset_urn={asset_urn}")
    except (KeyError, ValueError) as exc:
        logger.error(f"LI_UPLOAD: unexpected register response shape: {reg_res.text[:500]}")
        return None, f"Register response malformed: {exc}"

    # ---- 4. PUT the image bytes ----
    # The PUT uses the presigned upload_url. Content-Type should be set but not
    # the LinkedIn auth header on the PUT (auth is baked into the URL).
    put_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/octet-stream",
    }
    try:
        put_res = requests.put(upload_url, headers=put_headers, data=image_data, timeout=60)
    except Exception as exc:
        logger.exception(f"LI_UPLOAD: PUT exception: {exc}")
        return None, f"Image PUT failed: {exc}"

    if put_res.status_code not in (200, 201):
        logger.error(
            f"LI_UPLOAD: PUT failed — status={put_res.status_code} "
            f"body={put_res.text[:300]}"
        )
        return None, f"LinkedIn image PUT HTTP {put_res.status_code}: {put_res.text[:200]}"

    logger.info(f"LI_UPLOAD: success — asset_urn={asset_urn}")
    return asset_urn, None

def upload_twitter_media(image_bytes, user_token=None, user_token_secret=None):
    """Upload image bytes to Twitter v1.1 Media API. Returns (media_id, error_msg).

    Rewritten April 2026 to log response bodies. v1.1 media upload still works
    on Twitter Basic/Pro tiers even though v2 tweet posting is the norm.
    Free tier does NOT have media upload — if you're on Free, this will 403.
    """
    api_key = TWITTER_API_KEY
    api_secret = TWITTER_API_SECRET
    access_token = user_token or TWITTER_ACCESS_TOKEN
    access_token_secret = user_token_secret or TWITTER_ACCESS_TOKEN_SECRET

    # Diagnostic: list which credentials are missing
    missing = []
    if not api_key: missing.append("TWITTER_API_KEY")
    if not api_secret: missing.append("TWITTER_API_SECRET")
    if not access_token: missing.append("access_token")
    if not access_token_secret: missing.append("access_token_secret")
    if missing:
        logger.error(f"TW_UPLOAD: credentials missing: {missing}")
        return None, f"Twitter credentials missing: {', '.join(missing)}"

    if not image_bytes:
        logger.error("TW_UPLOAD: no image bytes provided")
        return None, "Image bytes empty"

    try:
        oauth = OAuth1Session(
            api_key,
            client_secret=api_secret,
            resource_owner_key=access_token,
            resource_owner_secret=access_token_secret,
        )
        # PNG is what the v4 image agent re-encodes to (lossless), so we keep
        # the filename + MIME aligned with the bytes the caller is uploading.
        files = {"media": ("image.png", image_bytes, "image/png")}
        logger.info(f"TW_UPLOAD: posting {len(image_bytes):,} bytes to v1.1 media/upload...")
        res = oauth.post(
            "https://upload.twitter.com/1.1/media/upload.json",
            files=files,
            timeout=60,
        )
        if res.status_code in (200, 201):
            media_id = res.json().get("media_id_string")
            logger.info(f"TW_UPLOAD: success — media_id={media_id}")
            return media_id, None

        # Failure: log body for diagnostics. 403 usually = Free-tier no-media,
        # 401 = OAuth signature/token mismatch, 400 = bad image.
        logger.error(
            f"TW_UPLOAD: FAILED — status={res.status_code} body={res.text[:500]}"
        )
        hint = ""
        if res.status_code == 403:
            hint = " (HINT: Twitter Free tier blocks media upload — Basic/Pro paid tier required)"
        elif res.status_code == 401:
            hint = " (HINT: OAuth 1.0a signature invalid — check API key/secret + user access token/secret)"
        return None, f"Twitter upload HTTP {res.status_code}: {res.text[:200]}{hint}"
    except Exception as exc:
        logger.exception(f"TW_UPLOAD: exception: {exc}")
        return None, f"Twitter upload exception: {exc}"

def twitter_upload_media_from_url(path_or_url, user_token=None, user_token_secret=None):
    """Fetch image bytes from URL/data-URL → upload to Twitter. Returns (media_id, err)."""
    if not path_or_url:
        logger.error("TW_UPLOAD: path_or_url is empty")
        return None, "Image URL missing"
    try:
        if path_or_url.startswith("data:image"):
            image_bytes = base64.b64decode(path_or_url.split(",", 1)[1])
            logger.info(f"TW_UPLOAD: decoded {len(image_bytes):,} bytes from data URL")
        elif path_or_url.startswith("http"):
            image_res = requests.get(path_or_url, timeout=30)
            if image_res.status_code != 200:
                logger.error(
                    f"TW_UPLOAD: image download failed — status={image_res.status_code} "
                    f"url={path_or_url}"
                )
                return None, f"Image download HTTP {image_res.status_code}"
            image_bytes = image_res.content
            logger.info(f"TW_UPLOAD: downloaded {len(image_bytes):,} bytes from {path_or_url}")
        else:
            logger.error(f"TW_UPLOAD: unsupported URL scheme: {path_or_url[:40]}...")
            return None, "Unsupported image URL scheme"
        return upload_twitter_media(image_bytes, user_token, user_token_secret)
    except Exception as exc:
        logger.exception(f"TW_UPLOAD: exception: {exc}")
        return None, f"Twitter media fetch+upload exception: {exc}"


# =====================================================================
# VIDEO + DOCUMENT upload helpers (Phase 2)
# ---------------------------------------------------------------------
# Design notes:
#   - Videos up to 1 GB live on S3. FB/IG call video_url directly so
#     Lambda never sees the bytes. LinkedIn + Twitter need their bytes
#     uploaded via their own chunked-upload protocols.
#   - _fetch_url_to_bytes pulls from S3 with a hard size cap so a broken
#     upload can't OOM the Lambda.
# =====================================================================


def _fetch_url_to_bytes(url, max_bytes=1024 * 1024 * 1024):
    """Stream an HTTP URL fully into memory. Caps at max_bytes to avoid OOM."""
    r = requests.get(url, stream=True, timeout=300)
    r.raise_for_status()
    buf = bytearray()
    for chunk in r.iter_content(chunk_size=1024 * 1024):
        if chunk:
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise ValueError(f"Payload exceeds {max_bytes} bytes cap")
    return bytes(buf)


def linkedin_upload_video(token, author_urn, video_url, file_size=None):
    """Upload a video to LinkedIn via the /v2/assets API. Returns (asset_urn, error).

    Uses the SAME API as linkedin_upload_image (which works on your app) with
    the `feedshare-video` recipe instead of `feedshare-image`. The previous
    /rest/videos endpoint requires Community Management API access, which is
    a restricted LinkedIn program most developer apps are not approved for —
    that's why the video was silently dropping to a text-only post.
    """
    if not token or not author_urn or not video_url:
        return None, "Missing required arguments for LinkedIn video upload"
    if not author_urn.startswith("urn:li:"):
        author_urn = f"urn:li:person:{author_urn}"

    # ---- 1. Fetch video bytes (cap 200 MB for LinkedIn consumer-tier upload) ----
    try:
        video_data = _fetch_url_to_bytes(video_url, max_bytes=200 * 1024 * 1024)
        logger.info(f"LI_VIDEO: fetched {len(video_data):,} bytes from {video_url[:80]}")
    except Exception as exc:
        logger.exception(f"LI_VIDEO: fetch exception: {exc}")
        return None, f"Video fetch exception: {exc}"

    # ---- 2. Register the upload with feedshare-video recipe ----
    register_url = "https://api.linkedin.com/v2/assets?action=registerUpload"
    headers_reg = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }
    register_data = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-video"],
            "owner": author_urn,
            "serviceRelationships": [
                {"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}
            ],
        }
    }
    try:
        reg_res = requests.post(register_url, headers=headers_reg, json=register_data, timeout=30)
    except Exception as exc:
        logger.exception(f"LI_VIDEO: register exception: {exc}")
        return None, f"Register request failed: {exc}"

    if reg_res.status_code != 200:
        logger.error(
            f"LI_VIDEO: registerUpload failed — status={reg_res.status_code} "
            f"body={reg_res.text[:500]} author_urn={author_urn}"
        )
        return None, f"LinkedIn registerUpload HTTP {reg_res.status_code}: {reg_res.text[:200]}"

    # ---- 3. Extract upload URL + asset URN ----
    try:
        rj = reg_res.json()
        upload_url = rj["value"]["uploadMechanism"][
            "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
        ]["uploadUrl"]
        asset_urn = rj["value"]["asset"]
        logger.info(f"LI_VIDEO: register OK — asset_urn={asset_urn}")
    except (KeyError, ValueError) as exc:
        logger.error(f"LI_VIDEO: unexpected register response shape: {reg_res.text[:500]}")
        return None, f"Register response malformed: {exc}"

    # ---- 4. PUT the video bytes ----
    put_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/octet-stream",
    }
    try:
        # Longer timeout for large video uploads — 5 min.
        put_res = requests.put(upload_url, headers=put_headers, data=video_data, timeout=300)
    except Exception as exc:
        logger.exception(f"LI_VIDEO: PUT exception: {exc}")
        return None, f"Video PUT failed: {exc}"

    if put_res.status_code not in (200, 201):
        logger.error(
            f"LI_VIDEO: PUT failed — status={put_res.status_code} "
            f"body={put_res.text[:300]}"
        )
        return None, f"LinkedIn video PUT HTTP {put_res.status_code}: {put_res.text[:200]}"

    logger.info(f"LI_VIDEO: success — asset_urn={asset_urn}")
    return asset_urn, None


def linkedin_upload_document(token, author_urn, document_url, file_size=None):
    """Upload a PDF/DOCX/PPTX to LinkedIn. Returns (document_urn, error).

    Documents MUST go through /rest/documents — the /v2/assets path rejects
    the feedshare-document recipe ("ACCESS_DENIED on recipes/relationshipType").
    Requires LinkedIn-Version header + product access; if your app isn't
    approved for it, registerUpload will 403 with a clear message.
    """
    if not token or not author_urn or not document_url:
        return None, "Missing required arguments for LinkedIn document upload"
    if not author_urn.startswith("urn:li:"):
        author_urn = f"urn:li:person:{author_urn}"

    try:
        doc_bytes = _fetch_url_to_bytes(document_url, max_bytes=100 * 1024 * 1024)
        logger.info(f"LI_DOC: fetched {len(doc_bytes):,} bytes from {document_url[:80]}")
    except Exception as exc:
        logger.exception(f"LI_DOC: fetch exception: {exc}")
        return None, f"Document fetch exception: {exc}"

    # Strip PDF metadata (Producer/Author/XMP) so internal carousel
    # generation details don't leak into the published file's properties.
    _orig_size = len(doc_bytes)
    doc_bytes = _strip_pdf_metadata(doc_bytes)
    logger.info(f"LI_DOC: stripped metadata — {_orig_size:,} -> {len(doc_bytes):,} bytes")

    init_url = "https://api.linkedin.com/rest/documents?action=initializeUpload"
    init_headers = {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": LINKEDIN_REST_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }
    init_body = {"initializeUploadRequest": {"owner": author_urn}}
    try:
        r = requests.post(init_url, headers=init_headers, json=init_body, timeout=30)
    except Exception as exc:
        return None, f"LinkedIn /rest/documents init exception: {exc}"
    if r.status_code not in (200, 201):
        logger.error(f"LI_DOC: init failed status={r.status_code} body={r.text[:500]}")
        # Give the operator a clear hint when the app isn't approved for the API
        hint = ""
        if r.status_code == 403:
            hint = " — Your LinkedIn app may not be approved for the Documents API. Apply for Community Management API access in LinkedIn Developer Portal."
        elif r.status_code == 426:
            hint = f" — LinkedIn-Version {LINKEDIN_REST_VERSION} not active. Bump LINKEDIN_REST_VERSION."
        return None, f"LinkedIn document init HTTP {r.status_code}: {r.text[:200]}{hint}"
    try:
        value = r.json()["value"]
        doc_urn = value["document"]
        upload_url = value["uploadUrl"]
    except (KeyError, ValueError) as exc:
        return None, f"LinkedIn init response malformed: {exc}"

    try:
        put_res = requests.put(
            upload_url,
            headers={"Authorization": f"Bearer {token}"},
            data=doc_bytes,
            timeout=180,
        )
    except Exception as exc:
        return None, f"Document PUT failed: {exc}"

    if put_res.status_code not in (200, 201):
        logger.error(f"LI_DOC: PUT failed status={put_res.status_code} body={put_res.text[:300]}")
        return None, f"LinkedIn document PUT HTTP {put_res.status_code}"

    logger.info(f"LI_DOC: success — urn={doc_urn}")
    return doc_urn, None


def twitter_upload_video_chunked(video_url, user_token, user_token_secret, total_bytes=None):
    """3-step chunked video upload for Twitter/X. Returns (media_id, error)."""
    if not user_token or not user_token_secret:
        return None, "Twitter video upload requires OAuth 1.0a user context"
    try:
        video_bytes = _fetch_url_to_bytes(video_url, max_bytes=512 * 1024 * 1024)
        total = total_bytes or len(video_bytes)
    except Exception as e:
        return None, f"Fetch video for Twitter failed: {e}"

    oauth = OAuth1Session(
        TWITTER_API_KEY, client_secret=TWITTER_API_SECRET,
        resource_owner_key=user_token, resource_owner_secret=user_token_secret,
    )
    endpoint = "https://upload.twitter.com/1.1/media/upload.json"

    init = oauth.post(endpoint, data={
        "command": "INIT", "total_bytes": total,
        "media_type": "video/mp4", "media_category": "tweet_video",
    })
    if init.status_code not in (200, 201, 202):
        logger.error(f"TW_VIDEO: INIT status={init.status_code} body={init.text[:300]}")
        return None, f"Twitter INIT HTTP {init.status_code}"
    try:
        media_id = init.json()["media_id_string"]
    except (KeyError, ValueError):
        return None, "Twitter INIT malformed response"

    CHUNK = 4 * 1024 * 1024
    for segment, start in enumerate(range(0, total, CHUNK)):
        chunk = video_bytes[start : start + CHUNK]
        ap = oauth.post(endpoint, data={
            "command": "APPEND", "media_id": media_id, "segment_index": segment,
        }, files={"media": chunk})
        if ap.status_code not in (200, 201, 204):
            logger.error(f"TW_VIDEO: APPEND seg={segment} status={ap.status_code} body={ap.text[:200]}")
            return None, f"Twitter APPEND HTTP {ap.status_code}"

    fin = oauth.post(endpoint, data={"command": "FINALIZE", "media_id": media_id})
    if fin.status_code not in (200, 201):
        logger.error(f"TW_VIDEO: FINALIZE status={fin.status_code} body={fin.text[:300]}")
        return None, f"Twitter FINALIZE HTTP {fin.status_code}"

    processing = fin.json().get("processing_info")
    if processing:
        for _ in range(30):
            time.sleep(processing.get("check_after_secs", 2))
            st = oauth.get(endpoint, params={"command": "STATUS", "media_id": media_id})
            if st.status_code != 200:
                return None, f"Twitter STATUS HTTP {st.status_code}"
            sp = st.json().get("processing_info", {})
            state = sp.get("state")
            if state == "succeeded":
                break
            if state == "failed":
                return None, f"Twitter video processing failed: {sp.get('error')}"
            processing = sp

    logger.info(f"TW_VIDEO: success — media_id={media_id}")
    return media_id, None


def facebook_post_video(page_id, token, video_url, description=""):
    """Post a video to a Facebook Page. Returns (post_id, error).
    Non-resumable — Meta fetches file_url itself, so Lambda never
    touches the bytes. Supports up to 1 GB / 20 min."""
    url = f"https://graph.facebook.com/v19.0/{page_id}/videos"
    try:
        r = requests.post(url, data={
            "file_url": video_url,
            "description": description or "",
            "access_token": token,
        }, timeout=120)
    except Exception as e:
        return None, f"FB video post exception: {e}"
    if r.status_code not in (200, 201):
        logger.error(f"FB_VIDEO: failed status={r.status_code} body={r.text[:400]}")
        return None, f"FB video post HTTP {r.status_code}: {r.text[:200]}"
    try:
        post_id = r.json().get("id")
        logger.info(f"FB_VIDEO: success — id={post_id}")
        return post_id, None
    except ValueError:
        return None, "FB video post response not JSON"


# =====================================================================
# COMMENT FETCH + REPLY helpers
# ---------------------------------------------------------------------
# Scope assumption: user has granted
#   LinkedIn ORG : w_organization_social + rw_organization_admin
#   Facebook     : pages_manage_engagement (or pages_read_engagement)
#   Instagram    : instagram_manage_comments
# Member LinkedIn comments are NOT covered here — no r_member_social.
# Returns a normalised list of dicts:
#   { id, author_name, author_picture, message, created_at, like_count,
#     reply_count, parent_id }
# =====================================================================


def _iso(ts):
    try:
        from datetime import datetime, timezone
        if isinstance(ts, (int, float)):
            # LinkedIn returns epoch MILLISECONDS (13-digit). datetime
            # .fromtimestamp expects SECONDS — a ms value is out of range
            # and raises, which previously fell through to None and made
            # the UI show today's date. Detect ms (> 1e12) and convert.
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        return str(ts) if ts else None
    except Exception:
        return None


def fetch_linkedin_org_comments(token, post_urn, org_urn):
    """List comments on a LinkedIn ORGANIZATION or PERSONAL post.
    post_urn: urn:li:ugcPost:... or urn:li:share:...
    org_urn:  urn:li:organization:... (for org posts) or urn:li:person:...
    Returns (comments_list, error_msg).

    Tries the classic /v2/socialActions path first (works with plain
    r_organization_social / w_member_social) and falls back to
    /rest/socialActions (Community Management API — needs
    r_organization_social_feed for org, w_member_social_feed for personal).
    If the stored URN is share-form (content.py rewrites ugcPost→share),
    we also retry with the ugcPost form because some endpoints only
    resolve one of the two.
    """
    from urllib.parse import quote
    logger.info(
        f"[LI_COMMENTS] BEGIN post_urn={post_urn!r} owner_urn={org_urn!r} "
        f"token_prefix={(token or '')[:10]}..."
    )
    if not post_urn:
        logger.warning("[LI_COMMENTS] missing post_urn — returning empty")
        return [], "Missing post_urn"
    if not post_urn.startswith("urn:li:"):
        post_urn = f"urn:li:share:{post_urn}"

    # ─────────────────────────────────────────────────────────────
    # Personal accounts (urn:li:person:*) — short-circuit.
    # LinkedIn does NOT allow reading personal-post comment text via any
    # public API. The endpoint (r_member_social_feed) is a closed program
    # not accepting new applications. Fail fast with a friendly message
    # instead of burning 4 API calls that will all 429/404.
    # ─────────────────────────────────────────────────────────────
    if (org_urn or "").startswith("urn:li:person:"):
        logger.info(
            f"[LI_COMMENTS] personal account — skipping fetch (LinkedIn API "
            f"restriction). owner_urn={org_urn!r}"
        )
        return [], "LinkedIn does not support fetching personal-post comments via API."

    # Build the list of URN variants to try — share and ugcPost with the
    # same numeric tail. Stops at first variant that returns 200.
    num_id = post_urn.split(":")[-1]
    urn_variants = []
    if "ugcPost" in post_urn:
        urn_variants = [post_urn, f"urn:li:share:{num_id}"]
    else:
        urn_variants = [post_urn, f"urn:li:ugcPost:{num_id}"]

    attempts = []  # list of (label, status_code, body_excerpt)
    auth_error_seen = False
    throttled = False  # trip on first 429 → skip remaining attempts (same bucket)

    for urn_try in urn_variants:
        if throttled:
            # Same-day throttle already hit — remaining variants use the same
            # bucket and would all 429 too. Stop wasting quota.
            break
        encoded = quote(urn_try, safe="")

        # ─────────────────────────────────────────────────────────────
        # Attempt 1: REST (versioned) endpoint under Community Management API
        # This is what the current LinkedIn docs specify:
        #   GET /rest/socialActions/{urn}/comments
        # NO ?q=comments (adding that causes 404 "No virtual resource found"
        # because LinkedIn tries to route to a finder that doesn't exist).
        # Requires r_organization_social_feed for org posts, r_member_social_feed
        # for personal posts (the latter is RESTRICTED to select developers).
        # This lives in a different throttle bucket from /v2/socialActions —
        # so it works even when /v2 has hit its daily cap.
        # ─────────────────────────────────────────────────────────────
        h_rest = {
            "Authorization": f"Bearer {token}",
            "Linkedin-Version": LINKEDIN_REST_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        }
        url_rest = f"https://api.linkedin.com/rest/socialActions/{encoded}/comments?count=50"
        try:
            r2 = requests.get(url_rest, headers=h_rest, timeout=15)
        except Exception as e:
            attempts.append((f"rest {urn_try.split(':')[2]}", "ERR", str(e)[:200]))
        else:
            if r2.status_code == 200:
                try:
                    data = r2.json()
                    parsed = _parse_linkedin_comments(data, urn_try)
                    # Fetch replies for each root comment that has them
                    all_comments = []
                    for c in parsed:
                        all_comments.append(c)
                        if c.get("reply_count", 0) > 0:
                            replies, _ = fetch_linkedin_replies(token, c["id"])
                            all_comments.extend(replies)
                    logger.info(
                        f"[LI_COMMENTS] ✅ rest OK for {urn_try} — "
                        f"{len(all_comments)} comment(s) returned"
                    )
                    _resolve_linkedin_actor_names(all_comments, token)
                    return all_comments, None
                except Exception as je:
                    logger.error(f"LI_COMMENTS rest parse error: {je}")
                    attempts.append((f"rest {urn_try.split(':')[2]}", 200, "Malformed JSON"))
            if r2.status_code in (401, 403):
                auth_error_seen = True
            if r2.status_code == 429:
                throttled = True
            attempts.append((f"rest {urn_try.split(':')[2]}", r2.status_code, r2.text[:200]))
            logger.warning(f"LI_COMMENTS rest {urn_try} → {r2.status_code} {r2.text[:200]}")
            if throttled:
                # Skip the v2 fallback for this variant — same throttle bucket.
                continue

        # ─────────────────────────────────────────────────────────────
        # Attempt 2: classic v2 endpoint (legacy fallback)
        # Kept as a safety net for edge cases where /rest/ doesn't cover
        # the URN. Same throttle bucket as most legacy calls — will 429
        # if we've been storming that bucket.
        # ─────────────────────────────────────────────────────────────
        h_v2 = {
            "Authorization": f"Bearer {token}",
            "X-Restli-Protocol-Version": "2.0.0",
        }
        proj = "(elements*(id,object,message,created,parentComment,likesSummary,commentsSummary,actor~(localizedFirstName,localizedLastName,profilePicture(displayImage~:playableStreams))))"
        url_v2 = f"https://api.linkedin.com/v2/socialActions/{encoded}/comments?count=50&projection={proj}"
        try:
            r = requests.get(url_v2, headers=h_v2, timeout=15)
        except Exception as e:
            attempts.append((f"v2 {urn_try.split(':')[2]}", "ERR", str(e)[:200]))
            continue
        else:
            if r.status_code == 200:
                try:
                    data = r.json()
                    parsed = _parse_linkedin_comments(data, urn_try)
                    all_comments = []
                    for c in parsed:
                        all_comments.append(c)
                        if c.get("reply_count", 0) > 0:
                            replies, _ = fetch_linkedin_replies(token, c["id"])
                            all_comments.extend(replies)
                    logger.info(
                        f"[LI_COMMENTS] ✅ v2 OK for {urn_try} — "
                        f"{len(all_comments)} comment(s) returned"
                    )
                    _resolve_linkedin_actor_names(all_comments, token)
                    return all_comments, None
                except Exception as je:
                    logger.error(f"LI_COMMENTS v2 parse error: {je}")
                    attempts.append((f"v2 {urn_try.split(':')[2]}", 200, "Malformed JSON"))
            if r.status_code in (401, 403):
                auth_error_seen = True
            if r.status_code == 429:
                throttled = True
            attempts.append((f"v2 {urn_try.split(':')[2]}", r.status_code, r.text[:200]))
            logger.warning(f"LI_COMMENTS v2 {urn_try} → {r.status_code} {r.text[:200]}")

    # Build the combined error message. The friendly, human-readable summary
    # is what the frontend surfaces to the user. Diagnostic detail goes to
    # the log only — never dumped into UI as raw status codes.
    detail = " | ".join(f"{label}: {status}" for label, status, _ in attempts)
    logger.warning(
        f"[LI_COMMENTS] ❌ ALL attempts FAILED for {post_urn} — attempts=[{detail}]"
    )

    if throttled:
        friendly = (
            "LinkedIn's daily quota for reading comments has been reached. "
            "Comments will re-fetch automatically once the quota resets "
            "(usually within 24 hours)."
        )
    elif auth_error_seen:
        friendly = (
            "Reconnect LinkedIn — the stored token is missing the scope "
            "needed to read comments (r_organization_social_feed for pages)."
        )
    else:
        friendly = (
            "Unable to fetch LinkedIn comments right now. Please try again "
            "later — if the issue persists, reconnect the account."
        )
    return [], friendly

def _resolve_linkedin_actor_names(comments: list, token: str) -> None:
    """Post-process a parsed comment list and fill in missing author_name /
    author_picture by calling the right LinkedIn endpoint per actor type.

    Background: the `actor~(localizedFirstName,localizedLastName,...)`
    projection on /v2/socialActions returns 403 when the commenter is an
    organization (orgs have localizedName, not first/last). It also can
    return unresolved for persons that the calling app can't see. The
    projection failure leaves us with bare URNs like
    `urn:li:organization:108092066` or `urn:li:person:42746029` instead
    of human names.

    Mutates `comments` in place. Best-effort: any actor that still can't
    be resolved keeps the placeholder name set by _parse_linkedin_comments.
    """
    if not comments:
        return

    # Collect unique unresolved actors. An actor is "unresolved" when the
    # current author_name looks like a placeholder (no display name was
    # returned by the projection).
    unresolved = {}  # urn -> [comment refs] needing name + picture filled
    for c in comments:
        actor = (c.get("author_handle") or "").strip()
        nm = (c.get("author_name") or "").strip()
        looks_unresolved = (
            not nm
            or nm in ("LinkedIn user", "LinkedIn member")
            or nm.isdigit()
            or (actor and nm == actor.split(":")[-1])
        )
        if not looks_unresolved or not actor.startswith("urn:li:"):
            continue
        unresolved.setdefault(actor, []).append(c)

    if not unresolved:
        return

    h = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    for actor_urn, refs in unresolved.items():
        try:
            tail = actor_urn.split(":")[-1]
            display_name = None
            picture_url = None

            if "organization" in actor_urn:
                # /v2/organizations/{id} works with r_organization_social.
                # logoV2 has a similar resolution shape to profilePicture
                # (an Image object with displayImage~ to dereference).
                url = f"https://api.linkedin.com/v2/organizations/{tail}?projection=(localizedName,logoV2(original~:playableStreams))"
                r = requests.get(url, headers=h, timeout=8)
                if r.status_code == 200:
                    j = r.json() or {}
                    display_name = j.get("localizedName")
                    try:
                        streams = (j.get("logoV2", {}).get("original~", {})).get("elements", [])
                        if streams:
                            picture_url = streams[-1].get("identifiers", [{}])[0].get("identifier")
                    except Exception:
                        pass
            elif "person" in actor_urn:
                # /v2/people/{id} requires r_basicprofile / r_liteprofile —
                # not always granted; try and fall back gracefully on 403.
                # We use the lite-profile schema to avoid asking for fields
                # we don't have permission to read.
                url = f"https://api.linkedin.com/v2/people/(id:{tail})?projection=(localizedFirstName,localizedLastName,profilePicture(displayImage~:playableStreams))"
                r = requests.get(url, headers=h, timeout=8)
                if r.status_code == 200:
                    j = r.json() or {}
                    first = j.get("localizedFirstName") or ""
                    last = j.get("localizedLastName") or ""
                    if first or last:
                        display_name = f"{first} {last}".strip()
                    try:
                        streams = (j.get("profilePicture", {}).get("displayImage~", {})).get("elements", [])
                        if streams:
                            picture_url = streams[-1].get("identifiers", [{}])[0].get("identifier")
                    except Exception:
                        pass

            if display_name:
                for c in refs:
                    c["author_name"] = display_name
                    if picture_url and not c.get("author_picture"):
                        c["author_picture"] = picture_url
        except Exception as e:
            logger.warning(f"LI actor resolve failed for {actor_urn}: {e}")


def fetch_linkedin_replies(token, comment_urn):
    """Fetches one level of replies for a specific comment."""
    from urllib.parse import quote
    encoded = quote(comment_urn, safe="")
    h = {
        "Authorization": f"Bearer {token}",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    proj = "(elements*(id,message,created,parentComment,likesSummary,commentsSummary,actor~(localizedFirstName,localizedLastName,profilePicture(displayImage~:playableStreams))))"
    url = f"https://api.linkedin.com/v2/socialActions/{encoded}/comments?count=50&projection={proj}"
    try:
        r = requests.get(url, headers=h, timeout=10)
        if r.status_code == 200:
            return _parse_linkedin_comments(r.json(), comment_urn), None
    except: pass
    return [], None


def _parse_linkedin_comments(payload, parent_post_urn):
    """Normalise LinkedIn comment elements from either v2 or /rest response.

    Constructs the full comment URN `urn:li:comment:(parentPostUrn,commentId)`
    so it can be used as `parentComment` in reply bodies — LinkedIn's v2 API
    returns only a numeric `id` in element rows, not the full URN, and
    parentComment needs the composite form.
    """
    out = []
    for el in (payload or {}).get("elements", []) or []:
        actor = el.get("actor") or ""
        # LinkedIn changed field names across API versions. Check both message.text (v2) and text/comment (rest).
        # LinkedIn changed field names across API versions. Check message.text (v2), message as string, or text/comment (rest).
        msg_obj = el.get("message")
        if isinstance(msg_obj, dict):
            message = msg_obj.get("text") or ""
        elif isinstance(msg_obj, str):
            message = msg_obj
        else:
            message = el.get("text") or el.get("comment") or ""

        # Resolve the comment's full URN. Priority order:
        #   1. Explicit $URN / commentV2Urn fields (present without projection)
        #   2. Construct from `object` (which LinkedIn returns as the real
        #      `urn:li:activity:...` ID — different from the post's
        #      `urn:li:share:...` URN we asked the request with) and the
        #      numeric comment ID. THIS IS WHAT MAKES REPLY FETCHING WORK:
        #      LinkedIn requires the comment URN to use the activity form,
        #      not the share form, otherwise /socialActions/{urn}/comments
        #      returns a 200 with empty elements (silently broken).
        #   3. Fall back to parent_post_urn (the share form) — last resort,
        #      replies on these will be unfetchable.
        #   4. Final fallback: a synthetic ID to avoid empty keys in the UI.
        urn = el.get("$URN") or el.get("commentV2Urn")
        if not urn and el.get("id"):
            cid = str(el.get("id"))
            if ":" not in cid:
                real_parent = el.get("object") or parent_post_urn
                urn = f"urn:li:comment:({real_parent},{cid})"
            else:
                urn = cid
        
        if not urn:
            import hashlib
            raw_data = f"{actor}-{message}-{el.get('created_at', '')}"
            urn = f"synthetic-{hashlib.md5(raw_data.encode()).hexdigest()}"

        # Resolve author name and picture from resolved actor sub-field (author~ or actor~)
        actor_resolved = el.get("actor~") or el.get("author~") or {}
        first = actor_resolved.get("localizedFirstName") or ""
        last = actor_resolved.get("localizedLastName") or ""
        if first or last:
            name = f"{first} {last}".strip()
        else:
            # LinkedIn often returns the comment with the actor URN unresolved
            # for non-Community-Management apps — the projection (`actor~`) is
            # silently ignored and we fall back to the raw URN like
            # `urn:li:person:42746029`. Showing the numeric ID makes the UI
            # look broken, so render a friendly placeholder instead.
            tail = actor.split(":")[-1] if actor else ""
            if tail and tail.isdigit():
                name = "LinkedIn member"
            elif tail:
                name = tail
            else:
                name = "LinkedIn user"
        
        # Extract profile picture URL if present
        pic = None
        try:
            streams = (actor_resolved.get("profilePicture", {}).get("displayImage~", {})).get("elements", [])
            if streams:
                # Take the highest resolution (last one usually)
                pic = streams[-1].get("identifiers", [{}])[0].get("identifier")
        except: pass

        out.append({
            "id": urn,
            "author_name": name,
            "author_handle": actor,
            "author_picture": pic,
            "message": message,
            "created_at": _iso((el.get("created") or {}).get("time")),
            "like_count": int((el.get("likesSummary") or {}).get("totalLikes") or 0),
            "reply_count": int((el.get("commentsSummary") or {}).get("aggregatedTotalComments") or 0),
            "parent_id": (el.get("parentComment") or ""),
            "platform": "linkedin",
        })
    return out


def reply_linkedin_org_comment(token, post_urn, org_urn, text, parent_comment_urn=None):
    """Create a comment on an ORG post as that org, or reply to a specific
    comment when parent_comment_urn is provided.
    Returns (comment_urn, error_msg).

    Tries /v2/socialActions first (works with w_organization_social) and
    falls back to /rest/socialActions (partner-gated — the one that was
    returning partnerApiSocialActions.CREATE ACCESS_DENIED). Also tries
    both URN forms because content.py rewrites ugcPost→share on publish.
    """
    from urllib.parse import quote
    if not post_urn:
        return None, "Missing post_urn"
    if not post_urn.startswith("urn:li:"):
        post_urn = f"urn:li:share:{post_urn}"
    if not org_urn.startswith("urn:li:organization:"):
        org_urn = f"urn:li:organization:{org_urn}"

    num_id = post_urn.split(":")[-1]
    if "ugcPost" in post_urn:
        urn_variants = [post_urn, f"urn:li:share:{num_id}"]
    else:
        urn_variants = [post_urn, f"urn:li:ugcPost:{num_id}"]

    attempts = []
    for urn_try in urn_variants:
        encoded = quote(urn_try, safe="")

        # Attempt 1: v2 POST (non-partner) — body is {actor, message}.
        h_v2 = {
            "Authorization": f"Bearer {token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }
        body_v2 = {"actor": org_urn, "message": {"text": text}}
        if parent_comment_urn:
            body_v2["parentComment"] = parent_comment_urn
        try:
            r = requests.post(
                f"https://api.linkedin.com/v2/socialActions/{encoded}/comments",
                headers=h_v2, json=body_v2, timeout=15,
            )
        except Exception as e:
            attempts.append((f"v2 {urn_try.split(':')[2]}", "ERR", str(e)[:200]))
            r = None

        if r is not None and r.status_code in (200, 201):
            try:
                j = r.json()
                return j.get("$URN") or j.get("id") or "", None
            except ValueError:
                return "", None
        if r is not None:
            attempts.append((f"v2 {urn_try.split(':')[2]}", r.status_code, r.text[:200]))
            logger.warning(f"LI_REPLY v2 {urn_try} → {r.status_code} {r.text[:200]}")

        # Attempt 2: REST POST (partner-gated) — different body shape.
        h_rest = {
            "Authorization": f"Bearer {token}",
            "Linkedin-Version": LINKEDIN_REST_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }
        body_rest = {"actor": org_urn, "object": urn_try, "message": {"text": text}}
        if parent_comment_urn:
            body_rest["parentComment"] = parent_comment_urn
        try:
            r2 = requests.post(
                f"https://api.linkedin.com/rest/socialActions/{encoded}/comments",
                headers=h_rest, json=body_rest, timeout=15,
            )
        except Exception as e:
            attempts.append((f"rest {urn_try.split(':')[2]}", "ERR", str(e)[:200]))
            continue

        if r2.status_code in (200, 201):
            try:
                j = r2.json()
                return j.get("$URN") or j.get("id") or "", None
            except ValueError:
                return "", None
        attempts.append((f"rest {urn_try.split(':')[2]}", r2.status_code, r2.text[:200]))
        logger.warning(f"LI_REPLY rest {urn_try} → {r2.status_code} {r2.text[:200]}")

    detail = " | ".join(f"{label}: {status}" for label, status, _ in attempts)
    hint = ""
    if any(isinstance(s, int) and s in (401, 403) for _, s, _ in attempts):
        if all(isinstance(s, int) and s == 403 for _, s, _ in attempts):
            hint = (" — All LinkedIn endpoints rejected the write. The REST "
                    "/rest/socialActions CREATE requires Community Management API "
                    "partner access. Check your LinkedIn app scope includes "
                    "w_organization_social and reconnect the page.")
        else:
            hint = " — Reconnect LinkedIn (token missing w_organization_social)."
    return None, f"LinkedIn reply failed [{detail}]{hint}"


def fetch_facebook_comments(token, post_id):
    """List comments on a Facebook Page post.
    post_id may be either the raw fb_post_id or {page_id}_{post_id} — the
    Graph API accepts both. Returns (comments_list, error_msg)."""
    fields = "id,from{name,picture},message,created_time,like_count,comment_count,parent"
    url = f"https://graph.facebook.com/v19.0/{post_id}/comments"
    params = {
        "fields": fields,
        "access_token": token,
        "limit": 50,
        "order": "reverse_chronological",
    }
    try:
        r = requests.get(url, params=params, timeout=12)
    except Exception as e:
        return [], f"FB comments fetch exception: {e}"
    if r.status_code != 200:
        logger.warning(f"FB_COMMENTS fetch failed ({r.status_code}): {r.text[:500]}")
        # Try fallback without the `from{picture}` subfield — some tokens
        # lack the permission for the nested avatar and will 400 on the
        # whole query even though `from{name}` alone would succeed.
        simple_fields = "id,from,message,created_time,like_count,comment_count,parent"
        try:
            r2 = requests.get(url, params={**params, "fields": simple_fields}, timeout=12)
        except Exception as e:
            return [], f"Facebook comments fetch exception (fallback): {e}"
        if r2.status_code != 200:
            try:
                err_body = r2.json().get("error", {})
                detail = err_body.get("message") or r2.text[:300]
                code = err_body.get("code")
            except Exception:
                detail = r2.text[:300]
                code = None
            hint = ""
            if r2.status_code in (401, 403) or code in (10, 200, 190):
                hint = " — Reconnect Facebook: stored token lacks pages_read_engagement / pages_manage_engagement."
            return [], f"Facebook comments HTTP {r2.status_code}: {detail}{hint}"
        r = r2

    try:
        data = r.json().get("data", []) or []
    except Exception as je:
        logger.error(f"FB_COMMENTS parse error: {je}")
        return [], "Facebook returned a malformed response (non-JSON)"

    out = []
    for c in data:
        frm = c.get("from") or {}
        pic = ((frm.get("picture") or {}).get("data") or {}).get("url") if isinstance(frm.get("picture"), dict) else None
        out.append({
            "id": c.get("id"),
            "author_name": frm.get("name") or "Facebook user",
            "author_handle": frm.get("id") or "",
            "author_picture": pic,
            "message": c.get("message") or "",
            "created_at": _iso(c.get("created_time")),
            "like_count": int(c.get("like_count") or 0),
            "reply_count": int(c.get("comment_count") or 0),
            "parent_id": (c.get("parent") or {}).get("id") or "",
            "platform": "facebook",
        })
    return out, None


def reply_facebook_comment(token, comment_id, text):
    """Reply to a Facebook comment. Returns (new_comment_id, error)."""
    url = f"https://graph.facebook.com/v19.0/{comment_id}/comments"
    try:
        r = requests.post(url, data={"message": text, "access_token": token}, timeout=15)
    except Exception as e:
        return None, f"FB reply exception: {e}"
    if r.status_code not in (200, 201):
        logger.warning(f"FB_COMMENTS reply failed ({r.status_code}): {r.text[:300]}")
        return None, f"Facebook reply HTTP {r.status_code}: {r.text[:200]}"
    return r.json().get("id"), None


def fetch_instagram_comments(token, ig_media_id):
    """List comments on an Instagram Business media object.
    Returns (comments_list, error_msg)."""
    fields = "id,username,text,timestamp,like_count,replies{id,username,text,timestamp,like_count}"
    url = f"https://graph.facebook.com/v19.0/{ig_media_id}/comments"
    params = {"fields": fields, "access_token": token, "limit": 50}
    try:
        r = requests.get(url, params=params, timeout=12)
    except Exception as e:
        return [], f"IG comments fetch exception: {e}"
    if r.status_code != 200:
        logger.warning(f"IG_COMMENTS fetch failed ({r.status_code}): {r.text[:500]}")
        try:
            err_body = r.json().get("error", {})
            detail = err_body.get("message") or r.text[:300]
            code = err_body.get("code")
        except Exception:
            detail = r.text[:300]
            code = None
        hint = ""
        if r.status_code in (401, 403) or code in (10, 200, 190):
            hint = " — Reconnect Instagram: stored token lacks instagram_manage_comments permission."
        return [], f"Instagram comments HTTP {r.status_code}: {detail}{hint}"

    try:
        data = r.json().get("data", []) or []
    except Exception as je:
        logger.error(f"IG_COMMENTS parse error: {je}")
        return [], "Instagram returned a malformed response (non-JSON)"

    out = []
    for c in data:
        out.append({
            "id": c.get("id"),
            "author_name": c.get("username") or "Instagram user",
            "author_handle": c.get("username") or "",
            "author_picture": None,
            "message": c.get("text") or "",
            "created_at": _iso(c.get("timestamp")),
            "like_count": int(c.get("like_count") or 0),
            "reply_count": len(((c.get("replies") or {}).get("data")) or []),
            "parent_id": "",
            "platform": "instagram",
        })
        for rep in ((c.get("replies") or {}).get("data") or []):
            out.append({
                "id": rep.get("id"),
                "author_name": rep.get("username") or "Instagram user",
                "author_handle": rep.get("username") or "",
                "author_picture": None,
                "message": rep.get("text") or "",
                "created_at": _iso(rep.get("timestamp")),
                "like_count": int(rep.get("like_count") or 0),
                "reply_count": 0,
                "parent_id": c.get("id"),
                "platform": "instagram",
            })
    return out, None


def reply_instagram_comment(token, comment_id, text):
    """Reply to an Instagram comment. Returns (new_comment_id, error)."""
    url = f"https://graph.facebook.com/v19.0/{comment_id}/replies"
    try:
        r = requests.post(url, data={"message": text, "access_token": token}, timeout=15)
    except Exception as e:
        return None, f"IG reply exception: {e}"
    if r.status_code not in (200, 201):
        logger.warning(f"IG_COMMENTS reply failed ({r.status_code}): {r.text[:300]}")
        return None, f"Instagram reply HTTP {r.status_code}: {r.text[:200]}"
    return r.json().get("id"), None


def fetch_twitter_comments(api_key, api_secret, token, token_secret, tweet_id):
    """List replies to a tweet using conversation_id search.

    Strategy — try FULL ARCHIVE first, then fall back to RECENT:
      1. `/2/tweets/search/all` — full-archive back to 2006. Per X's own
         docs table, this is accessible on pay-per-use accounts (not
         Enterprise-only as third-party guides sometimes claim).
      2. `/2/tweets/search/recent` — last 7 days only. Fallback if the
         `all` endpoint 403s (account doesn't actually have access) or
         hits rate limits.

    Under X's pay-per-use system (Feb 2026+):
      - /search/recent: 300 req/15min per user under OAuth 1.0a
      - /search/all: 1 req/sec, 300 req/15min ceiling
    Both cost the same per-request read fee (~$0.005).

    Full rate-limit headers are logged so we can see the actual
    X-enforced ceiling instead of guessing at outdated tier assumptions.

    Returns (comments_list, error_msg).
    """
    if not (api_key and api_secret and token and token_secret):
        return [], "Twitter credentials incomplete — reconnect via OAuth 1.0a"
    try:
        oauth = OAuth1Session(api_key, client_secret=api_secret,
                              resource_owner_key=token, resource_owner_secret=token_secret)
    except Exception as e:
        return [], f"Twitter OAuth session build exception: {e}"

    # ─── Look up the tweet's true conversation_id ────────────────────
    # CRITICAL: If our tweet is part of a thread (a self-reply), its
    # conversation_id is the ROOT of the thread, NOT its own tweet_id.
    # Searching `conversation_id:{tweet_id}` returns 0 in that case
    # because no tweet has conversation_id == tweet_id (they all point
    # to the root). We must first get the ACTUAL conversation_id, then
    # search using that, then filter results to only direct replies.
    conv_id = tweet_id
    if TWITTER_BEARER_TOKEN:
        try:
            lookup = requests.get(
                f"https://api.twitter.com/2/tweets/{tweet_id}",
                headers={"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"},
                params={"tweet.fields": "conversation_id"}, timeout=10,
            )
            if lookup.status_code == 200:
                looked_up = ((lookup.json() or {}).get("data") or {}).get("conversation_id")
                if looked_up and str(looked_up) != str(tweet_id):
                    logger.info(
                        f"TW_COMMENTS lookup tweet_id={tweet_id} is thread-member; "
                        f"true conversation_id={looked_up}"
                    )
                    conv_id = looked_up
        except Exception as e:
            logger.info(f"TW_COMMENTS conversation_id lookup failed: {e} — using tweet_id")

    params = {
        "query": f"conversation_id:{conv_id}",
        "tweet.fields": "author_id,created_at,public_metrics,in_reply_to_user_id,referenced_tweets",
        "expansions": "author_id",
        "user.fields": "username,name,profile_image_url",
        "max_results": 100,
        "sort_order": "recency",  # newest replies first (default is relevance)
        # CRITICAL: /2/tweets/search/all defaults to the last 30 DAYS if
        # start_time is not specified — even though the endpoint can search
        # back to 2006. Set start_time to a safe overreach (Jan 2020) so we
        # actually get the full archive we're paying for. Without this,
        # replies to posts older than 30 days silently return raw=0.
        "start_time": "2020-01-01T00:00:00Z",
    }

    # ─── Attempt 1: full archive ─────────────────────────────────────
    # /2/tweets/search/all ONLY accepts OAuth 2.0 App-Only Bearer Token
    # (X returns 403 "Unsupported Authentication" on OAuth 1.0a User Context).
    # We use the app-level TWITTER_BEARER_TOKEN from .env — a single
    # credential for the whole app, not per-user. If it's not set, we skip
    # /all and go straight to /recent.
    r = None
    if TWITTER_BEARER_TOKEN:
        try:
            r = requests.get(
                "https://api.twitter.com/2/tweets/search/all",
                headers={"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"},
                params=params, timeout=15,
            )
        except Exception as e:
            return [], f"Twitter comments fetch exception (full archive): {e}"
    else:
        logger.info(
            "TW_COMMENTS[all] skipped — TWITTER_BEARER_TOKEN not set in .env; "
            "falling back to /recent (7-day window only). Add the token to "
            "unlock replies on older posts."
        )

    rl_limit = rl_remaining = rl_reset = None
    if r is not None:
        rl_limit = r.headers.get("x-rate-limit-limit")
        rl_remaining = r.headers.get("x-rate-limit-remaining")
        rl_reset = r.headers.get("x-rate-limit-reset")
        logger.info(
            f"TW_COMMENTS[all] status={r.status_code} rl_limit={rl_limit} "
            f"rl_remaining={rl_remaining} rl_reset={rl_reset} tweet_id={tweet_id}"
        )

        if r.status_code == 200:
            # Paginate — /all returns 100/page; follow next_token up to 5 pages
            # (500 replies) so posts with many replies are fully captured.
            raw_total = len(r.json().get("data") or [])
            all_replies = _parse_twitter_search_response(r.json(), tweet_id)
            next_token = (r.json().get("meta") or {}).get("next_token")
            pages_fetched = 1
            while next_token and pages_fetched < 5:
                page_params = {**params, "next_token": next_token}
                try:
                    r2 = requests.get(
                        "https://api.twitter.com/2/tweets/search/all",
                        headers={"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"},
                        params=page_params, timeout=15,
                    )
                except Exception:
                    break
                if r2.status_code != 200:
                    logger.info(f"TW_COMMENTS[all] page {pages_fetched+1} status={r2.status_code} — stopping pagination")
                    break
                raw_total += len(r2.json().get("data") or [])
                all_replies.extend(_parse_twitter_search_response(r2.json(), tweet_id))
                next_token = (r2.json().get("meta") or {}).get("next_token")
                pages_fetched += 1

            # If /all returned nothing, try /2/tweets/:id/quote_tweets —
            # X's reply_count metric INCLUDES quote tweets, but they don't
            # share our tweet's conversation_id so search misses them.
            # Throttled: X caps this endpoint at 75/15min, so back off after
            # a 429 until _TW_QUOTES_BACKOFF_UNTIL expires (see top of file).
            global _TW_QUOTES_BACKOFF_UNTIL
            if not all_replies and time.time() >= _TW_QUOTES_BACKOFF_UNTIL:
                try:
                    qr = requests.get(
                        f"https://api.twitter.com/2/tweets/{tweet_id}/quote_tweets",
                        headers={"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"},
                        params={
                            "tweet.fields": "author_id,created_at,public_metrics",
                            "expansions": "author_id",
                            "user.fields": "username,name,profile_image_url",
                            "max_results": 100,
                        }, timeout=15,
                    )
                    logger.info(f"TW_COMMENTS[quotes] status={qr.status_code} tweet_id={tweet_id}")
                    if qr.status_code == 429:
                        # Skip /quotes for 15 min (the X window). Prevents
                        # every subsequent 0-reply fetch from re-triggering.
                        _TW_QUOTES_BACKOFF_UNTIL = time.time() + 15 * 60
                        logger.info(
                            "TW_COMMENTS[quotes] 429 hit — backing off for 15 min"
                        )
                    elif qr.status_code == 200:
                        qdata = qr.json()
                        qusers = {u["id"]: u for u in (qdata.get("includes", {}).get("users") or [])}
                        for t in qdata.get("data", []) or []:
                            u = qusers.get(t.get("author_id"), {})
                            pm = t.get("public_metrics") or {}
                            all_replies.append({
                                "id": t.get("id"),
                                "author_name": u.get("name") or u.get("username") or "X user",
                                "author_handle": f"@{u.get('username')}" if u.get("username") else "",
                                "author_picture": u.get("profile_image_url"),
                                "message": f"[quote tweet] {t.get('text') or ''}",
                                "created_at": _iso(t.get("created_at")),
                                "like_count": int(pm.get("like_count") or 0),
                                "reply_count": int(pm.get("reply_count") or 0),
                                "parent_id": tweet_id,
                                "platform": "twitter",
                            })
                except Exception as e:
                    logger.info(f"TW_COMMENTS[quotes] exception: {e}")

            logger.info(
                f"TW_COMMENTS[all] tweet_id={tweet_id} raw={raw_total} "
                f"kept={len(all_replies)} across {pages_fetched} page(s)"
            )
            return all_replies, None

    # Fall back to /recent when: bearer wasn't set (r is None), or /all
    # returned 403/404 (auth/product issue). /recent uses OAuth 1.0a
    # User Context and only indexes the last 7 days.
    if r is None or r.status_code in (403, 404):
        if r is not None:
            logger.info(
                f"TW_COMMENTS[all] {r.status_code} — falling back to /recent. "
                f"X returned: {r.text[:400]}"
            )
        # /recent only spans the last 7 days — the wide 2020-01-01
        # start_time we pass to /all would be out-of-range here and X 400s.
        recent_params = {k: v for k, v in params.items() if k != "start_time"}
        try:
            r = oauth.get(
                "https://api.twitter.com/2/tweets/search/recent",
                params=recent_params, timeout=15,
            )
        except Exception as e:
            return [], f"Twitter comments fetch exception (recent): {e}"
        rl_limit = r.headers.get("x-rate-limit-limit")
        rl_remaining = r.headers.get("x-rate-limit-remaining")
        rl_reset = r.headers.get("x-rate-limit-reset")
        logger.info(
            f"TW_COMMENTS[recent] status={r.status_code} rl_limit={rl_limit} "
            f"rl_remaining={rl_remaining} rl_reset={rl_reset} tweet_id={tweet_id}"
        )
        if r.status_code == 200:
            parsed = _parse_twitter_search_response(r.json(), tweet_id)
            return parsed, None

    if r.status_code == 429:
        # Diagnostic detail (rl headers + reset window) goes to the log
        # only — the UI gets a single short sentence users can act on.
        # The old verbose message about "user-context OAuth" and
        # "app-level counter" was noise for non-engineer users.
        from datetime import datetime, timezone
        mins_str = "~15 min"
        if rl_reset:
            try:
                reset_dt = datetime.fromtimestamp(int(rl_reset), tz=timezone.utc)
                mins = max(1, int((reset_dt - datetime.now(timezone.utc)).total_seconds() // 60))
                mins_str = f"~{mins} min"
            except Exception:
                pass
        logger.warning(
            f"TW_COMMENTS 429 tweet_id={tweet_id} rl_limit={rl_limit} "
            f"rl_remaining={rl_remaining} rl_reset={rl_reset}"
        )
        return [], (
            f"X's rate limit reached. Comments will re-fetch automatically "
            f"in {mins_str}."
        )
    if r.status_code != 200:
        logger.warning(f"TW_COMMENTS fetch failed ({r.status_code}): {r.text[:400]}")
        hint = ""
        if r.status_code in (401, 403):
            hint = " — Reconnect X (OAuth token may be revoked)."
        return [], f"Twitter comments HTTP {r.status_code}: {r.text[:200]}{hint}"

    return _parse_twitter_search_response(r.json(), tweet_id), None


def _parse_twitter_search_response(data: dict, tweet_id) -> list:
    """Extract direct replies to `tweet_id` from an X search API response.

    Response contains every tweet in the conversation, including:
      - the original tweet
      - thread self-replies from the OP
      - direct replies to our target tweet_id
      - replies-to-replies (nested)
      - quote-tweets (via other conversations)

    We keep ONLY tweets whose `referenced_tweets` has a `replied_to`
    edge pointing at our specific tweet_id — those are the direct
    reply comments the user cares about.
    """
    users = {u["id"]: u for u in (data.get("includes", {}).get("users") or [])}
    out = []
    tid_str = str(tweet_id)
    for t in data.get("data", []) or []:
        # Skip the original tweet itself (always appears in results)
        if str(t.get("id")) == tid_str:
            continue
        # Only keep DIRECT replies to our specific tweet_id.
        refs = t.get("referenced_tweets") or []
        is_direct_reply = any(
            (ref.get("type") == "replied_to" and str(ref.get("id")) == tid_str)
            for ref in refs
        )
        if not is_direct_reply:
            continue
        u = users.get(t.get("author_id"), {})
        pm = t.get("public_metrics") or {}
        out.append({
            "id": t.get("id"),
            "author_name": u.get("name") or u.get("username") or "X user",
            "author_handle": f"@{u.get('username')}" if u.get("username") else "",
            "author_picture": u.get("profile_image_url"),
            "message": t.get("text") or "",
            "created_at": _iso(t.get("created_at")),
            "like_count": int(pm.get("like_count") or 0),
            "reply_count": int(pm.get("reply_count") or 0),
            "parent_id": tweet_id,
            "platform": "twitter",
        })
    return out


def reply_twitter_comment(api_key, api_secret, token, token_secret, parent_tweet_id, text):
    """Reply to a tweet using the v2 OAuth 1.0a user-context POST endpoint.
    Returns (new_tweet_id, error_msg)."""
    if not (api_key and api_secret and token and token_secret):
        return None, "Twitter credentials incomplete"
    try:
        oauth = OAuth1Session(api_key, client_secret=api_secret,
                              resource_owner_key=token, resource_owner_secret=token_secret)
        body = {"text": text, "reply": {"in_reply_to_tweet_id": str(parent_tweet_id)}}
        r = oauth.post("https://api.twitter.com/2/tweets", json=body, timeout=15)
    except Exception as e:
        return None, f"Twitter reply exception: {e}"
    if r.status_code not in (200, 201):
        logger.warning(f"TW_REPLY failed ({r.status_code}): {r.text[:400]}")
        hint = " (X rate-limited on POST /2/tweets — 15-min window; auto-resets)" if r.status_code == 429 else ""
        return None, f"Twitter reply HTTP {r.status_code}: {r.text[:200]}{hint}"
    try:
        return r.json().get("data", {}).get("id"), None
    except ValueError:
        return None, "Twitter reply response not JSON"


def instagram_post_reel(ig_user_id, token, video_url, caption=""):
    """Post a Reel to Instagram. 2-step: create container then publish.
    Returns (post_id, error). Reel processing takes 10-60s typically."""
    create_url = f"https://graph.facebook.com/v19.0/{ig_user_id}/media"
    try:
        cr = requests.post(create_url, data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption or "",
            "access_token": token,
        }, timeout=60)
    except Exception as e:
        return None, f"IG container create exception: {e}"
    if cr.status_code not in (200, 201):
        logger.error(f"IG_VIDEO: container create status={cr.status_code} body={cr.text[:400]}")
        return None, f"IG container HTTP {cr.status_code}: {cr.text[:200]}"
    container_id = cr.json().get("id")
    if not container_id:
        return None, "IG container create returned no id"

    status_url = f"https://graph.facebook.com/v19.0/{container_id}"
    for _ in range(30):
        time.sleep(2)
        sr = requests.get(status_url, params={"fields": "status_code", "access_token": token}, timeout=30)
        if sr.status_code != 200:
            continue
        status = sr.json().get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            return None, "IG Reels container processing failed"
    else:
        return None, "IG Reels container did not finish processing in time"

    publish_url = f"https://graph.facebook.com/v19.0/{ig_user_id}/media_publish"
    try:
        pr = requests.post(publish_url, data={
            "creation_id": container_id,
            "access_token": token,
        }, timeout=60)
    except Exception as e:
        return None, f"IG publish exception: {e}"
    if pr.status_code not in (200, 201):
        logger.error(f"IG_VIDEO: publish status={pr.status_code} body={pr.text[:400]}")
        return None, f"IG publish HTTP {pr.status_code}: {pr.text[:200]}"
    post_id = pr.json().get("id")
    logger.info(f"IG_VIDEO: success — id={post_id}")
    return post_id, None
