"""
Pipelyt Publishing Router
Handles post publishing, scheduling, and the EventBridge scheduler endpoint.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session, joinedload
import os
import requests
import json
import base64
import uuid
import logging
from datetime import datetime
from io import BytesIO

from core.database import get_db
from core.config import TWITTER_API_KEY, TWITTER_API_SECRET

# Pinterest API base — swappable prod/sandbox. See routers/social.py for docs.
_PINTEREST_API_BASE = os.getenv("PINTEREST_API_BASE", "https://api.pinterest.com").rstrip("/")
from core.s3_utils import get_s3_client, get_s3_url, S3_BUCKET_NAME
from services.social_service import (
    refresh_twitter_token_v2,
    linkedin_upload_image,
    upload_twitter_media,
    twitter_upload_media_from_url,
)
from services.ai_service import generate_strategic_content
from models import User, SocialAccount, PublishedPost, PublishedPostPlatform, ScheduledPost
from routers.auth import block_user_writes, get_current_user
from services.plan_service import check_quota, check_feature
from services.team_service import get_team_scope_user_ids, is_team_member

logger = logging.getLogger("pipelyt.publishing")

# Read-only Users are blocked from writes (publish/schedule); reads stay open.
router = APIRouter(dependencies=[Depends(block_user_writes)])


class MockResponse:
    def __init__(self, status_code, text, json_data=None, headers=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data or {}
        self.headers = headers or {}

    def json(self):
        return self._json_data


async def process_publishing_internal(
    db: Session, user_id: int, content: str, image_url: str, targets: dict,
    media_type: str = None,
    dna_product_id: str = None,
    thumbnail_url: str = None,
    # YouTube-specific fields. Only used when targets["youtube"] is non-empty;
    # all other platform branches ignore them. The Manual Post / Video Mode
    # frontend pre-validates these before submit, but we still apply the same
    # caps server-side as a belt-and-braces guard.
    youtube_title: str = None,
    youtube_description: str = None,
    youtube_tags: list = None,
    youtube_category_id: str = "22",
    youtube_privacy: str = "public",
    tiktok_caption: str = None,
    tiktok_privacy: str = "SELF_ONLY",
    **kwargs
):
    saved_public_url = image_url
    image_data_bytes = None

    # Accept either `twitter` or `x` from clients; canonicalize to twitter.
    if isinstance(targets, dict) and "x" in targets and "twitter" not in targets:
        targets = {**targets, "twitter": targets.get("x") or []}

    if image_url and image_url.startswith("data:image"):
        try:
            image_data_bytes = base64.b64decode(image_url.split(",", 1)[1])
        except Exception as e:
            logger.error(f"Failed to decode base64 image: {str(e)}")

    if image_data_bytes:
        s3 = get_s3_client()
        if s3 and S3_BUCKET_NAME:
            filename = f"{uuid.uuid4().hex}.jpg"
            try:
                s3.upload_fileobj(
                    BytesIO(image_data_bytes),
                    S3_BUCKET_NAME,
                    filename,
                    ExtraArgs={'ContentType': 'image/jpeg'}
                )
                saved_public_url = f"https://{S3_BUCKET_NAME}.s3.amazonaws.com/{filename}"
                logger.info(f"Base64 S3 upload success. Public URL: {saved_public_url}")
            except Exception as e:
                logger.error(f"Base64 S3 Upload Failure: {str(e)}. Proceeding to social posting fallback.")
        else:
            logger.warning("S3 not configured for base64 storage - proceeding with caution.")

    # Include accounts owned by this user PLUS any admin-owned accounts that
    # have been assigned to them (team member flow). Same publish semantics
    # either way — the token still lives on the admin's row.
    from sqlalchemy import or_
    user_accounts = db.query(SocialAccount).filter(
        or_(
            SocialAccount.user_id == user_id,
            SocialAccount.assigned_to_user_id == user_id,
        )
    ).all()
    account_map = {(a.platform, a.account_id): a for a in user_accounts}
    results: dict[str, list[dict[str, str]]] = {}
    platform_ids = []

    json_content = None
    if isinstance(content, str) and content.strip().startswith('{'):
        try:
            json_content = json.loads(content)
        except Exception:
            json_content = None

    for platform in ["linkedin", "facebook", "instagram", "twitter", "youtube", "tiktok", "pinterest"]:
        platform_targets = targets.get(platform, [])
        if not platform_targets:
            continue

        platform_content = content
        if json_content:
            platform_content = json_content.get(platform, json_content.get("default"))
            if not platform_content:
                platform_content = next(iter(json_content.values())) if json_content else content

        results[platform] = []
        for target_id in platform_targets:
            account = account_map.get((platform, target_id))
            if not account:
                continue
            try:
                token, acc_id = account.token, account.account_id

                if platform == "linkedin":
                    asset_urn = None
                    media_error = None
                    if image_url:
                        try:
                            asset_urn, media_error = linkedin_upload_image(token, acc_id, image_url)
                            if media_error:
                                logger.error(f"publishing: LinkedIn media upload returned error: {media_error}")
                        except Exception as media_err:
                            logger.exception(f"publishing: LinkedIn media upload raised: {media_err}")
                            media_error = str(media_err)

                    share_content = {
                        "shareCommentary": {"text": platform_content},
                        "shareMediaCategory": "IMAGE" if asset_urn else "NONE"
                    }
                    if asset_urn:
                        share_content["media"] = [{"status": "READY", "media": asset_urn}]
                    _acc_kind = (
                        "org" if "organization" in acc_id else
                        "personal" if "person" in acc_id else "?"
                    )
                    logger.info(
                        f"[LI_PUBLISH] POST /v2/ugcPosts — kind={_acc_kind} "
                        f"author={acc_id} media={'yes' if asset_urn else 'no'} "
                        f"text_len={len(platform_content or '')}"
                    )
                    res = requests.post(
                        "https://api.linkedin.com/v2/ugcPosts",
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "author": acc_id,
                            "lifecycleState": "PUBLISHED",
                            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
                            "specificContent": {"com.linkedin.ugc.ShareContent": share_content}
                        }
                    )
                    logger.info(
                        f"[LI_PUBLISH] response status={res.status_code} "
                        f"body={res.text[:400]}"
                    )

                elif platform == "facebook":
                    url = f"https://graph.facebook.com/v19.0/{acc_id}/{'photos' if image_url else 'feed'}"
                    params = (
                        {"url": saved_public_url, "message": platform_content, "access_token": token}
                        if image_url
                        else {"message": platform_content, "access_token": token}
                    )
                    res = requests.post(url, params=params)

                elif platform == "twitter":
                    token_secret = account.token_secret

                    if len(platform_content) > 280:
                        logger.info(f"Truncating Twitter content from {len(platform_content)} to 280 chars.")
                        platform_content = platform_content[:277] + "..."

                    media_id, upload_err = (
                        twitter_upload_media_from_url(saved_public_url, token, token_secret)
                        if saved_public_url
                        else (None, None)
                    )

                    payload = {"text": platform_content}
                    if media_id:
                        payload["media"] = {"media_ids": [media_id]}

                    if token_secret and token_secret.lower() != "none" and token_secret != "":
                        from requests_oauthlib import OAuth1Session
                        user_oauth = OAuth1Session(
                            TWITTER_API_KEY,
                            client_secret=TWITTER_API_SECRET,
                            resource_owner_key=token,
                            resource_owner_secret=token_secret
                        )
                        res = user_oauth.post("https://api.twitter.com/2/tweets", json=payload)

                        if res.status_code == 401:
                            logger.warning("Twitter 1.0a 401, attempting OAuth 2.0 fallback...")
                            if account.refresh_token:
                                token = refresh_twitter_token_v2(account, db) or token
                                headers = {
                                    "Authorization": f"Bearer {token}",
                                    "Content-Type": "application/json; charset=UTF-8"
                                }
                                res = requests.post("https://api.twitter.com/2/tweets", headers=headers, json=payload)
                    elif account.refresh_token:
                        token = refresh_twitter_token_v2(account, db) or token
                        headers = {
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json; charset=UTF-8"
                        }
                        res = requests.post("https://api.twitter.com/2/tweets", headers=headers, json=payload)
                    else:
                        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                        res = requests.post("https://api.twitter.com/2/tweets", headers=headers, json=payload)

                    if res.status_code not in [200, 201]:
                        logger.error(f"Twitter Post Failed: {res.status_code} - {res.text}")
                        error_msg = f"Twitter Error ({res.status_code}): {res.text}"
                        if res.status_code == 403:
                            res_json = {}
                            try:
                                res_json = res.json()
                            except Exception:
                                pass
                            detail = res_json.get("detail", "Forbidden")
                            if "duplicate" in detail.lower():
                                error_msg = "Twitter Error: Duplicate Post! ⚠️ You cannot post the exact same text twice in a row."
                            else:
                                error_msg = f"Twitter 403 Forbidden: {detail}. Ensure your Twitter App has 'Read and Write' permissions and the text is under 280 chars."
                        res = MockResponse(status_code=res.status_code, text=error_msg, json_data={"detail": error_msg})

                elif platform == "instagram":
                    # Video (Reel) branch — posts a 2-step container to Meta.
                    if media_type == "video" and image_url:
                        from services.social_service import instagram_post_reel
                        ig_id, ig_err = instagram_post_reel(acc_id, token, saved_public_url, caption=platform_content)
                        if ig_err:
                            res = MockResponse(status_code=500, text=ig_err)
                        else:
                            res = MockResponse(status_code=200, text="ok", json_data={"id": ig_id})
                            logger.info(f"[IG_VIDEO] SUCCESS → {ig_id}")
                    elif not image_url:
                        res = MockResponse(status_code=400, text='Image required')
                    else:
                        con_res = requests.post(
                            f"https://graph.facebook.com/v19.0/{acc_id}/media",
                            params={"image_url": saved_public_url, "caption": platform_content, "access_token": token}
                        )
                        if con_res.status_code == 200:
                            creation_id = con_res.json().get("id")
                            import time
                            is_ready = False
                            for _ in range(10):
                                status_res = requests.get(
                                    f"https://graph.facebook.com/v19.0/{creation_id}",
                                    params={"fields": "status_code", "access_token": token}
                                )
                                if status_res.status_code == 200 and status_res.json().get("status_code") == "FINISHED":
                                    is_ready = True
                                    break
                                time.sleep(3)
                            if not is_ready:
                                time.sleep(5)
                            res = requests.post(
                                f"https://graph.facebook.com/v19.0/{acc_id}/media_publish",
                                params={"creation_id": creation_id, "access_token": token}
                            )
                        else:
                            res = con_res

                elif platform == "youtube":
                    # YouTube doesn't share the per-platform `content` field
                    # with the other platforms — it needs an explicit Title +
                    # Description split. Manual-Post Video Mode enforces that
                    # YouTube is the ONLY target when it's selected, so the
                    # rest of the targets dict will be empty.
                    from services.youtube_publisher import publish_video as _yt_publish
                    yt_video_url = saved_public_url
                    yt_result = _yt_publish(
                        account=account,
                        db=db,
                        video_url=yt_video_url,
                        title=youtube_title or "",
                        description=youtube_description if youtube_description is not None else (platform_content or ""),
                        tags=youtube_tags or [],
                        category_id=youtube_category_id or "22",
                        privacy=(youtube_privacy or "public").lower(),
                    )
                    if yt_result.get("ok"):
                        # Wrap in MockResponse so the downstream success-
                        # detection block (which checks res.status_code)
                        # treats this as a normal 2xx publish.
                        res = MockResponse(
                            status_code=200,
                            text=yt_result.get("url", ""),
                            json_data={"id": yt_result.get("video_id"), "url": yt_result.get("url")},
                        )
                    else:
                        res = MockResponse(
                            status_code=400,
                            text=yt_result.get("error", "YouTube publish failed"),
                            json_data={"detail": yt_result.get("error", "YouTube publish failed")},
                        )

                elif platform == "pinterest":
                    # Pinterest API v5: POST /v5/pins requires a board_id and
                    # an image source. acc_id IS the board_id (saved during the
                    # OAuth callback). On 401 we refresh the token once and retry.
                    if not saved_public_url:
                        res = MockResponse(
                            status_code=400,
                            text="Pinterest requires an image",
                            json_data={"detail": "Pinterest requires an image"},
                        )
                    else:
                        # Pinterest description = caption (max 500 chars).
                        pin_description = (platform_content or "")[:500]
                        pin_payload = {
                            "board_id": acc_id,
                            "media_source": {
                                "source_type": "image_url",
                                "url": saved_public_url,
                            },
                            "description": pin_description,
                        }
                        logger.info(f"[PINTEREST_PUBLISH] using API base: {_PINTEREST_API_BASE}")
                        res = requests.post(
                            f"{_PINTEREST_API_BASE}/v5/pins",
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Content-Type": "application/json",
                            },
                            json=pin_payload,
                            timeout=20,
                        )

                        # Token expired — refresh once and retry.
                        if res.status_code == 401 and account.refresh_token:
                            from core.config import (
                                PINTEREST_CLIENT_ID as _PIN_ID,
                                PINTEREST_CLIENT_SECRET as _PIN_SECRET,
                            )
                            basic = base64.b64encode(
                                f"{_PIN_ID}:{_PIN_SECRET}".encode()
                            ).decode()
                            ref_resp = requests.post(
                                f"{_PINTEREST_API_BASE}/v5/oauth/token",
                                headers={
                                    "Authorization": f"Basic {basic}",
                                    "Content-Type": "application/x-www-form-urlencoded",
                                },
                                data={
                                    "grant_type": "refresh_token",
                                    "refresh_token": account.refresh_token,
                                },
                                timeout=15,
                            )
                            if ref_resp.status_code == 200:
                                new_tok = ref_resp.json().get("access_token")
                                if new_tok:
                                    account.token = new_tok
                                    db.commit()
                                    token = new_tok
                                    res = requests.post(
                                        f"{_PINTEREST_API_BASE}/v5/pins",
                                        headers={
                                            "Authorization": f"Bearer {token}",
                                            "Content-Type": "application/json",
                                        },
                                        json=pin_payload,
                                        timeout=20,
                                    )
                            else:
                                logger.error(f"Pinterest token refresh failed: {ref_resp.status_code} {ref_resp.text}")

                        if res.status_code not in [200, 201]:
                            logger.error(f"Pinterest Post Failed: {res.status_code} - {res.text}")

                final_status = "Success ✅" if res.status_code in [200, 201] else f"Error: {res.text}"

                if res.status_code in [200, 201]:
                    try:
                        native_id = None
                        if platform == "linkedin":
                            if hasattr(res, 'headers'):
                                native_id = res.headers.get("X-RestLi-Id")
                            if not native_id and hasattr(res, 'json') and callable(res.json):
                                try:
                                    native_id = res.json().get("id")
                                except Exception:
                                    pass
                            if native_id and isinstance(native_id, str) and "ugcPost" in native_id:
                                num_id = native_id.split(":")[-1]
                                native_id = f"urn:li:share:{num_id}"
                        elif platform == "youtube":
                            # YouTube branch produced a MockResponse with the
                            # video_id under res.json()["id"] already — same
                            # extraction path as the elif below would use, but
                            # spelled out so we're explicit.
                            try:
                                native_id = res.json().get("id")
                            except Exception:
                                pass
                        elif hasattr(res, 'json') and callable(res.json):
                            try:
                                res_json = res.json()
                                if platform == "twitter":
                                    native_id = res_json.get("data", {}).get("id")
                                else:
                                    native_id = res_json.get("id")
                            except Exception:
                                pass
                        if native_id:
                            platform_ids.append((platform, acc_id, str(native_id)))
                    except Exception as e:
                        logger.error(f"Failed to extract native ID for {platform}: {str(e)}")

                results[platform].append({"name": account.name, "status": final_status})
            except Exception as e:
                results[platform].append({"name": account.name, "status": f"Failed ❌: {str(e)}"})

    valid_platforms = set()
    for platform, res_list in results.items():
        if any(r.get("status", "").startswith("Success") for r in res_list):
            valid_platforms.add(platform)

    if valid_platforms:
        final_image_url = str(saved_public_url) if saved_public_url else None
        post_record = PublishedPost(
            content=content,
            image_url=final_image_url,
            platforms=",".join(valid_platforms),
            user_id=user_id,
            dna_product_id=dna_product_id,
        )
        db.add(post_record)
        db.flush()

        for p_name, p_acc_id, p_native_id in platform_ids:
            db.add(PublishedPostPlatform(
                published_post_id=post_record.id,
                platform=p_name,
                account_id=p_acc_id,
                native_post_id=p_native_id
            ))

        db.commit()

    return results


@router.post("/post")
async def publish_post(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    body = await request.json()
    content, image_url, targets = body.get("content"), body.get("image_url"), body.get("targets", {})
    media_type = body.get("media_type", "image")

    # YouTube-specific fields. The Manual Post / Video Mode UI sends these
    # alongside the standard fields whenever YouTube is the (exclusive)
    # target. The content / caption text is still in `content`; for YouTube
    # we use `youtube_description` (falls back to content if not provided
    # explicitly) and the new `youtube_title` as the actual video title.
    youtube_title = (body.get("youtube_title") or "").strip()
    youtube_description = body.get("youtube_description")  # may be None → fallback to content
    youtube_tags = body.get("youtube_tags") or []
    if isinstance(youtube_tags, str):
        # Tolerate comma-separated string from the form input
        youtube_tags = [t.strip() for t in youtube_tags.split(",") if t.strip()]
    youtube_category_id = str(body.get("youtube_category_id") or "22")
    youtube_privacy = (body.get("youtube_privacy") or "public").lower()

    # TikTok-specific fields — see content.py's /post for the contract.
    tiktok_caption = body.get("tiktok_caption")
    tiktok_privacy = (body.get("tiktok_privacy") or "SELF_ONLY").strip().upper()

    # 1. Quota Check
    check_quota(db, current_user, "posts")

    # 2. Feature Check (Video)
    if media_type == "video":
        check_feature(current_user, "video")

    # YouTube-only sanity checks. We do the same checks inside publish_video
    # but failing fast HERE means we don't burn a publishing-attempt log row.
    has_youtube_target = bool((targets or {}).get("youtube"))
    has_tiktok_target = bool((targets or {}).get("tiktok"))
    if has_youtube_target:
        if not image_url:
            raise HTTPException(
                status_code=400,
                detail="YouTube posts require a video file. Please add a video before publishing.",
            )
        if not youtube_title:
            raise HTTPException(
                status_code=400,
                detail="YouTube posts require a Title. Please enter a title before publishing.",
            )

    if has_tiktok_target and not image_url:
        raise HTTPException(
            status_code=400,
            detail="TikTok posts require a video file. Please add a video before publishing.",
        )

    if not content and not has_youtube_target and not has_tiktok_target:
        # YouTube + TikTok posts can have an empty caption — title (YT) or
        # bare caption fallback (TT). Everything else needs caption text.
        raise HTTPException(status_code=400, detail="Post content is required")

    # Team members: resolve incoming DNA against their assigned list.
    dna_product_id = body.get("dna_product_id")
    if is_team_member(current_user):
        from services.team_service import resolve_member_publish_dna
        dna_product_id = resolve_member_publish_dna(current_user, dna_product_id)

    results = await process_publishing_internal(
        db, current_user.id, content, image_url, targets,
        dna_product_id=dna_product_id,
        youtube_title=youtube_title,
        youtube_description=youtube_description,
        youtube_tags=youtube_tags,
        youtube_category_id=youtube_category_id,
        youtube_privacy=youtube_privacy,
        tiktok_caption=tiktok_caption,
        tiktok_privacy=tiktok_privacy,
    )
    return {"message": "Publishing attempt complete", "results": results}


@router.get("/posts")
@router.get("/posts/published")
async def get_published_posts(
    sort: str = "desc",
    platform: str = "all",
    # Dashboard / Analytics brand-filter params — all optional. Shared
    # resolver in services.team_service narrows visible_ids by company /
    # brand / member / country / state / city / pin. Admin-only; members
    # are always scoped to themselves.
    member_user_ids: str = None,
    dna_product_ids: str = None,
    filter_company: str = None,
    filter_country: str = None,
    filter_state: str = None,
    filter_city: str = None,
    filter_pin_code: str = None,
    user_type: str = None,   # 'team' | 'franchise' | None (both)
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Fetch all published posts with filtering and sorting support."""
    from services.team_service import resolve_filtered_visible_ids
    visible_ids = resolve_filtered_visible_ids(
        db, current_user,
        member_user_ids=member_user_ids, dna_product_ids=dna_product_ids,
        filter_company=filter_company, filter_country=filter_country,
        filter_state=filter_state, filter_city=filter_city,
        filter_pin_code=filter_pin_code,
        user_type=user_type,
    )
    query = db.query(PublishedPost).options(
        joinedload(PublishedPost.platform_posts)
    ).filter(PublishedPost.user_id.in_(visible_ids))

    # Brand filter at the post level (dna_product_id tag on the row).
    brand_ids = []
    if dna_product_ids:
        brand_ids = [x.strip() for x in dna_product_ids.split(",") if x.strip()]
    if brand_ids:
        query = query.filter(PublishedPost.dna_product_id.in_(brand_ids))

    platform_key = (platform or "all").strip().lower()
    if platform_key == "x":
        platform_key = "twitter"
    if platform_key and platform_key != "all":
        query = query.filter(PublishedPost.platforms.contains(platform_key))

    if sort == "asc":
        query = query.order_by(PublishedPost.created_at.asc())
    else:
        query = query.order_by(PublishedPost.created_at.desc())

    posts = query.all()

    return [{
        "id": p.id,
        "content": p.content,
        "image_url": p.image_url,
        "platforms": p.platforms.split(",") if p.platforms else [],
        "created_at": p.created_at.isoformat() + "Z" if p.created_at else None,
        "metrics": [{
            "platform": pp.platform,
            "likes": pp.metrics_json.get("likes", 0) if pp.metrics_json else 0,
            "comments": pp.metrics_json.get("comments", 0) if pp.metrics_json else 0,
            "reach": pp.metrics_json.get("reach", 0) if (pp.metrics_json and "reach" in pp.metrics_json) else 0
        } for pp in p.platform_posts]
    } for p in posts]


@router.delete("/posts/{post_id}")
async def delete_post(post_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    post = db.query(PublishedPost).filter(PublishedPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    # Admins can delete team-members' posts; members only their own.
    visible_ids = get_team_scope_user_ids(db, current_user)
    if post.user_id not in visible_ids:
        raise HTTPException(status_code=403, detail="You do not have permission to delete this post")
    try:
        if post.image_url and S3_BUCKET_NAME in post.image_url:
            s3 = get_s3_client()
            if s3:
                key = post.image_url.split("/")[-1].split("?")[0]
                logger.info(f"Deleting object from S3: {key}")
                s3.delete_object(Bucket=S3_BUCKET_NAME, Key=key)
    except Exception as e:
        logger.error(f"Failed to delete image from S3: {str(e)}")
    db.delete(post)
    db.commit()
    return {"message": "Post deleted successfully"}


@router.post("/schedule")
async def schedule_post(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    body = await request.json()
    content = body.get("content")
    image_url = body.get("image_url")
    targets = body.get("targets", {})
    scheduled_for_str = body.get("scheduled_for")
    timezone_name = body.get("timezone", "UTC")
    post_type = body.get("post_type", "standard")
    campaign_brief = body.get("campaign_brief")
    media_type = body.get("media_type", "image")

    if post_type == "standard" and not content:
        raise HTTPException(status_code=400, detail="Content is required for standard posts")
    if post_type == "agentic" and not campaign_brief:
        raise HTTPException(status_code=400, detail="Campaign brief is required for agentic posts")
    if not scheduled_for_str:
        raise HTTPException(status_code=400, detail="scheduled_for is required")

    # 1. Quota Check
    check_quota(db, current_user, "posts")

    # 2. Feature Check (Agentic Scheduling / Video)
    if post_type == "agentic":
        check_feature(current_user, "full_scheduling")
    if media_type == "video":
        check_feature(current_user, "video")

    # 3. Approval Workflow (Agency)
    # If the user is an Editor in a team (Agency feature), the post needs approval.
    status = "pending"
    if current_user.role == "editor" and current_user.team_id:
        status = "awaiting_approval"

    try:
        scheduled_for = datetime.fromisoformat(scheduled_for_str.replace('Z', '+00:00'))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")

    # Tag DNA. Members resolved against assigned list; admins from payload.
    scheduled_dna_id = body.get("dna_product_id")
    if is_team_member(current_user):
        from services.team_service import resolve_member_publish_dna
        scheduled_dna_id = resolve_member_publish_dna(current_user, scheduled_dna_id)

    new_scheduled_post = ScheduledPost(
        content=content if content else "{}",
        image_url=image_url,
        targets=json.dumps(targets),
        scheduled_for=scheduled_for,
        timezone=timezone_name,
        post_type=post_type,
        campaign_brief=campaign_brief,
        user_id=current_user.id,
        status=status,
        media_type=media_type,
        dna_product_id=scheduled_dna_id,
    )
    db.add(new_scheduled_post)
    db.commit()
    return {"message": "Post scheduled successfully", "id": new_scheduled_post.id}


@router.get("/scheduled")
async def get_scheduled_posts(
    # Brand-filter params (same set as /posts/published). Admin only.
    member_user_ids: str = None,
    dna_product_ids: str = None,
    filter_company: str = None,
    filter_country: str = None,
    filter_state: str = None,
    filter_city: str = None,
    filter_pin_code: str = None,
    user_type: str = None,   # 'team' | 'franchise' | None (both)
    # Two-track booking: when true, also include rows in the awaiting_review
    # state (pre-generated static posts not yet approved by the user). The
    # Review Queue screen sets this; the regular Calendar/Scheduled view
    # leaves it off so awaiting_review rows don't pollute the timeline.
    include_awaiting_review: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from services.team_service import resolve_filtered_visible_ids
    visible_ids = resolve_filtered_visible_ids(
        db, current_user,
        member_user_ids=member_user_ids, dna_product_ids=dna_product_ids,
        filter_company=filter_company, filter_country=filter_country,
        filter_state=filter_state, filter_city=filter_city,
        filter_pin_code=filter_pin_code,
        user_type=user_type,
    )
    allowed_statuses = ["pending"]
    if include_awaiting_review:
        allowed_statuses.append("awaiting_review")
    query = db.query(ScheduledPost).filter(
        ScheduledPost.user_id.in_(visible_ids),
        ScheduledPost.status.in_(allowed_statuses),
    )
    brand_ids = []
    if dna_product_ids:
        brand_ids = [x.strip() for x in dna_product_ids.split(",") if x.strip()]
    if brand_ids:
        query = query.filter(ScheduledPost.dna_product_id.in_(brand_ids))
    posts = query.order_by(ScheduledPost.scheduled_for.asc()).all()
    # Diagnostic — track exactly what the review queue is being told.
    # Helps us catch "rows exist in DB but visible_ids filter excludes them"
    # vs "filter is empty" vs "frontend is silently dropping the payload".
    awaiting_n = sum(1 for p in posts if p.status == "awaiting_review")
    pending_n  = sum(1 for p in posts if p.status == "pending")
    logger.info(
        f"[SCHEDULED] user={current_user.id} include_awaiting_review={include_awaiting_review} "
        f"visible_ids={list(visible_ids)[:5]}{'...' if len(visible_ids) > 5 else ''} "
        f"returning {len(posts)} rows (awaiting_review={awaiting_n} pending={pending_n})"
    )

    return [{
        "id": p.id,
        "content": p.content,
        "image_url": p.image_url,
        "media_type": p.media_type,
        # Slide-1 PNG + full slide list so Scheduled.jsx can render carousel
        # PDF previews the same way Drafts/Published do. NULL for text /
        # image / video / legacy rows and the frontend falls back gracefully.
        "thumbnail_url": p.thumbnail_url,
        "slide_thumbnail_urls": p.slide_thumbnail_urls,
        "targets": json.loads(p.targets) if p.targets else {},
        "scheduled_for": p.scheduled_for.isoformat() + "Z",
        "timezone": p.timezone,
        "status": p.status,
        "post_type": p.post_type,
        "campaign_brief": p.campaign_brief,
        "error_message": p.error_message,
    } for p in posts]


@router.post("/scheduled/{post_id}/approve")
async def approve_scheduled_post(
    post_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Approve a pre-generated post — flips awaiting_review → pending.

    Optional body: { "content": "...", "image_url": "..." } to apply user edits
    before scheduling. If omitted, the existing pre-generated content stands.
    """
    visible_ids = get_team_scope_user_ids(db, current_user)
    post = db.query(ScheduledPost).filter(
        ScheduledPost.id == post_id, ScheduledPost.user_id.in_(visible_ids)
    ).first()
    if not post:
        raise HTTPException(status_code=404, detail="Scheduled post not found")
    if post.status != "awaiting_review":
        raise HTTPException(
            status_code=400,
            detail=f"Post is in state '{post.status}', not awaiting_review"
        )

    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}

    if "content" in body and body["content"] is not None:
        # Accept either a flat string (single platform) or a JSON object map.
        if isinstance(body["content"], dict):
            post.content = json.dumps(body["content"])
        else:
            post.content = str(body["content"])
    if "image_url" in body:
        post.image_url = body["image_url"]
    if "scheduled_for" in body and body["scheduled_for"]:
        try:
            post.scheduled_for = datetime.fromisoformat(
                body["scheduled_for"].replace('Z', '+00:00')
            ).replace(tzinfo=None)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid date format: {e}")
    if "campaign_brief" in body and body["campaign_brief"]:
        # Research-card edits: user changed the topic/theme/CTA. Save it on
        # the row so the JIT pipeline reads the updated brief at fire time.
        post.campaign_brief = str(body["campaign_brief"])

    post.status = "pending"
    db.commit()
    logger.info(f"[REVIEW] Slot {post_id} approved by user {current_user.id} → status=pending")
    return {"message": "Post approved and scheduled", "id": post.id}


@router.post("/scheduled/{post_id}/regenerate")
async def regenerate_scheduled_post(
    post_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-run pre-generation for an awaiting_review row.

    Clears content + image, kicks off a background task to refill them. The
    Review Queue keeps polling and will show the new content when ready.
    """
    visible_ids = get_team_scope_user_ids(db, current_user)
    post = db.query(ScheduledPost).filter(
        ScheduledPost.id == post_id, ScheduledPost.user_id.in_(visible_ids)
    ).first()
    if not post:
        raise HTTPException(status_code=404, detail="Scheduled post not found")
    if post.status not in ("awaiting_review", "failed"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot regenerate from state '{post.status}'"
        )

    post.content = "{}"
    post.image_url = None
    post.error_message = None
    post.status = "awaiting_review"
    db.commit()
    logger.info(f"[REVIEW] Slot {post_id} cleared for regeneration by user {current_user.id}")

    def _regen(sp_id: int, user_id: int):
        from core.database import SessionLocal
        from routers.content import _pregenerate_awaiting_review
        _db = SessionLocal()
        try:
            user_row = _db.query(User).filter(User.id == user_id).first()
            _pregenerate_awaiting_review(_db, sp_id, user_row)
        except Exception as e:
            logger.error(f"[REVIEW] Regenerate failed for {sp_id}: {e}", exc_info=True)
            row = _db.query(ScheduledPost).filter(ScheduledPost.id == sp_id).first()
            if row:
                row.status = "failed"
                row.error_message = f"Regeneration failed: {e}"
                _db.commit()
        finally:
            _db.close()

    background_tasks.add_task(_regen, post.id, current_user.id)
    return {"message": "Regeneration started", "id": post.id}


@router.delete("/scheduled/{post_id}")
async def delete_scheduled_post(post_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Admins can cancel team-members' scheduled posts too.
    visible_ids = get_team_scope_user_ids(db, current_user)
    post = db.query(ScheduledPost).filter(
        ScheduledPost.id == post_id, ScheduledPost.user_id.in_(visible_ids)
    ).first()
    if not post:
        raise HTTPException(status_code=404, detail="Scheduled post not found")
    db.delete(post)
    db.commit()
    return {"message": "Scheduled post cancelled"}


@router.put("/scheduled/{post_id}")
async def update_scheduled_post(post_id: int, request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    body = await request.json()
    post = db.query(ScheduledPost).filter(
        ScheduledPost.id == post_id, ScheduledPost.user_id == current_user.id
    ).first()
    if not post:
        raise HTTPException(status_code=404, detail="Scheduled post not found")

    if "content" in body:
        post.content = body["content"]
    if "scheduled_for" in body:
        try:
            _new_dt = datetime.fromisoformat(body["scheduled_for"].replace('Z', '+00:00'))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")
        # Defense-in-depth — frontend guards this too, but a scripted /
        # tampered client could still POST a past time and the cron worker
        # would skip the row forever without any user-visible signal.
        from datetime import timezone as _tz
        _now = datetime.now(_tz.utc)
        _new_dt_aware = _new_dt if _new_dt.tzinfo else _new_dt.replace(tzinfo=_tz.utc)
        if _new_dt_aware <= _now:
            raise HTTPException(
                status_code=400,
                detail="Scheduled time must be in the future.",
            )
        post.scheduled_for = _new_dt
    if "targets" in body:
        post.targets = json.dumps(body["targets"])

    db.commit()
    return {"message": "Scheduled post updated successfully"}


@router.get("/scheduler/process")
@router.post("/scheduler/process")
async def process_scheduler(request: Request = None, db: Session = Depends(get_db)):
    """Triggered by AWS EventBridge every 1 minute. Processes pending scheduled posts."""
    now = datetime.utcnow()
    logger.info(f"EventBridge Trigger - Scheduler checking for posts due before {now}")

    pending_posts = db.query(ScheduledPost).filter(
        ScheduledPost.status == "pending",
        ScheduledPost.scheduled_for <= now
    ).all()

    logger.info(f"Found {len(pending_posts)} pending posts to process.")

    processed_results = []
    for post in pending_posts:
        try:
            targets = json.loads(post.targets) if post.targets else {}

            if post.post_type == "agentic" and (not post.content or post.content == "{}" or post.content == ""):
                logger.info(f"Agentic Trigger: Generating content for Scheduled Post {post.id}...")
                platforms = list(targets.keys())
                user = db.query(User).filter(User.id == post.user_id).first()

                try:
                    from fastapi.concurrency import run_in_threadpool
                    # media_type was stamped on the row at /start-campaign time
                    # from the plan slot's content_type. Honor it here so a
                    # Text-only research post doesn't burn the Image pipeline.
                    _jit_media_type = (post.media_type or "image").lower()
                    gen_data = await run_in_threadpool(
                        generate_strategic_content,
                        post.campaign_brief,
                        platforms,
                        user,
                        post_type=_jit_media_type,
                    )

                    rec = gen_data.get("recommendation", {})
                    recommended_variant = rec.get("best_variant", "viral_reach")

                    all_content = gen_data.get("content", {})
                    final_content_map = {}
                    for p in platforms:
                        p_variants = all_content.get(p.lower(), {})
                        final_content_map[p] = p_variants.get(recommended_variant, next(iter(p_variants.values()), ""))

                    post.content = json.dumps(final_content_map)

                    visuals = gen_data.get("visuals", [])
                    if len(visuals) >= 2:
                        post.image_url = visuals[1].get("url")
                    elif len(visuals) > 0:
                        post.image_url = visuals[0].get("url")

                    logger.info(f"Autonomous generation successful for Post {post.id} (Variant: {recommended_variant})")
                except Exception as gen_err:
                    logger.error(f"Autonomous generation failed for Post {post.id}: {str(gen_err)}")
                    post.status = "failed"
                    post.error_message = f"AI Generation Failed: {str(gen_err)}"
                    continue

            publish_results = await process_publishing_internal(
                db=db,
                user_id=post.user_id,
                content=post.content,
                image_url=post.image_url,
                targets=targets,
                # Same forwarding as content.py's fire path — YouTube videos
                # need their title/tags/category/privacy at publish time, and
                # these were stamped on the ScheduledPost row at /schedule
                # time. Non-YouTube rows have NULL here; the youtube branch
                # is the only consumer.
                media_type=post.media_type,
                dna_product_id=post.dna_product_id,
                youtube_title=post.youtube_title,
                youtube_description=post.youtube_description,
                youtube_tags=post.youtube_tags or [],
                youtube_category_id=post.youtube_category_id or "22",
                youtube_privacy=post.youtube_privacy or "public",
            )

            actual_results: dict = publish_results
            success = any(
                any(r.get("status", "").lower().startswith("success") for r in res_list)
                for res_list in actual_results.values()
            )

            post.status = "published" if success else "failed"
            if not success:
                post.error_message = json.dumps(publish_results)

            processed_results.append({"id": post.id, "status": post.status})
        except Exception as e:
            post.status = "failed"
            post.error_message = str(e)
            processed_results.append({"id": post.id, "status": "failed", "error": str(e)})

    db.commit()
    return {"processed": len(pending_posts), "results": processed_results}
