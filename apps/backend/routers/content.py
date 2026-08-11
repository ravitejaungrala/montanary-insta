"""
Pipelyt Content Router
Handles AI content generation, visual generation, image upload, and usage.
Publishing and scheduling are handled by routers/publishing.py.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, BackgroundTasks, Query
from typing import Optional
from sqlalchemy.orm import Session, joinedload
import os
import requests
import json
import uuid
import time
import pytz
import logging
import base64  # C-16: used at process_publishing_internal for data-URL decoding
from datetime import datetime

from core.database import get_db
logger = logging.getLogger("pipelyt.content")
from core.config import GEMINI_API_KEY, TWITTER_API_KEY, TWITTER_API_SECRET, SCHEDULER_SECRET

# Modular Services
from core.s3_utils import get_s3_client, get_s3_url, S3_BUCKET_NAME, AWS_REGION
from services.ai_service import (
    generate_strategic_content,
    generate_visual_variants,
    generate_campaign_plan,
    generate_single_post_content,
)
from models import User, SocialAccount, PublishedPost, PublishedPostPlatform, ScheduledPost, Draft
from schemas import GenerationRequest
from routers.auth import block_user_writes, get_current_user
from services.doc_processor import process_multiple_files
from services.social_service import (
    linkedin_upload_image,
    twitter_upload_media_from_url,
    refresh_twitter_token_v2,
    linkedin_upload_video,
    linkedin_upload_document,
    twitter_upload_video_chunked,
    facebook_post_video,
    instagram_post_reel,
)

# Pinterest API base — swappable prod/sandbox. See routers/social.py for docs.
_PINTEREST_API_BASE = os.getenv("PINTEREST_API_BASE", "https://api.pinterest.com").rstrip("/")
class MockResponse:
    """Tiny duck-typed stand-in for `requests.Response` so code that reads
    `res.status_code`, `res.text`, `res.headers`, and `res.json()` can treat
    successful and failed publishing branches uniformly. Previously imported
    by implicit cross-module reference — moved here so content.py is
    self-contained and doesn't NameError on Lambda cold starts."""
    def __init__(self, status_code, text, json_data=None, headers=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data or {}
        self.headers = headers or {}

    def json(self):
        return self._json_data


from services.team_service import (
    get_team_scope_user_ids,
    is_team_member,
    get_plan_owner,
    resolve_member_publish_dna,
)
from services.plan_service import (
    check_quota, check_feature, get_monthly_post_count,
    get_monthly_image_count, get_brand_count, get_channel_count, get_user_plan_config
)
router = APIRouter(dependencies=[Depends(block_user_writes)])  # Users blocked from writes


def _apply_member_dna_inheritance(db, user, product_name: Optional[str]):
    """For team members, graft admin's business_dna onto the member for this
    request so agents pick up the correct brand. Also clamps product_name to
    the member's assigned brand list so a tampered client can't generate
    under a brand the admin didn't assign.

    Returns the clamped product_name. Mutates `user.business_dna` in place —
    safe because this route never calls db.commit() on the user, so SQLA
    auto-rolls back when the request's session closes.
    """
    if not is_team_member(user):
        return product_name
    admin = get_plan_owner(db, user)
    if admin and admin.id != user.id:
        # Inherit the admin's full DNA (company + products) for agent context.
        user.business_dna = admin.business_dna
    # Clamp to assigned brand — falls back to member's primary brand if the
    # requested product_name isn't in their assigned list.
    clamped = resolve_member_publish_dna(user, product_name)
    if clamped and clamped != product_name:
        logger.info(
            f"Member {user.id} requested product_name={product_name!r} "
            f"not in assigned list; falling back to {clamped!r}"
        )
    return clamped or product_name


def _active_dna(user, product_name: Optional[str]) -> dict:
    """Returns the DNA dict that should drive branding for a generation call.

    When `product_name` matches a key in business_dna.products, return that
    product's DNA (product-specific logo, colors, tagline, brand tone, docs).
    Otherwise return the top-level company DNA. Keeps the visual pipeline in
    lockstep with the text pipeline — if the user picked "Spenzo" in the
    Dashboard, visuals use Spenzo's logo + primary color, not NeuZenAI's.

    Implementation note: we shallow-merge company DNA under product DNA so
    missing keys (e.g. fonts) transparently fall through to the company's
    defaults. The nested `products` dict is stripped from the merged result
    to avoid leaking it.
    """
    dna = getattr(user, "business_dna", None) or {}
    if not isinstance(dna, dict):
        return {}
    if product_name and isinstance(dna.get("products"), dict):
        product_dna = dna["products"].get(product_name)
        if isinstance(product_dna, dict):
            merged = {**dna, **product_dna}
            merged.pop("products", None)
            return merged
    return dna




@router.get("/debug-env")
async def debug_env():
    import os
    from core.config import BASE_URL, FACEBOOK_REDIRECT_URI, PUBLIC_URL
    return {
        "BASE_URL": BASE_URL,
        "FACEBOOK_REDIRECT_URI": FACEBOOK_REDIRECT_URI,
        "PUBLIC_URL": PUBLIC_URL,
        "os_environ_FACEBOOK_REDIRECT_URI": os.environ.get("FACEBOOK_REDIRECT_URI"),
        "os_environ_BASE_URL": os.environ.get("BASE_URL"),
    }


@router.get("/config/image-styles")
async def get_image_styles():
    """Return the visual style catalog for the CampaignBrief Style chip.

    Ordered + grouped exactly as the picker should render. Frontend
    fetches this once on Dashboard mount; no auth required — the list
    is public product surface (labels + descriptions, no secrets).
    """
    from services import style_catalog
    return {"styles": style_catalog.public_catalog()}


@router.get("/config/business-categories")
async def get_business_categories():
    """Canonical list of business categories used by the DNA dropdown +
    Auto Style pipeline routing. Frontend fetches this once and renders
    it inside the Business DNA editor.
    """
    from services.business_categorizer import BUSINESS_CATEGORIES
    return {"categories": BUSINESS_CATEGORIES}


@router.post("/generate-plan")
async def handle_generate_plan(
    brief: str = File(...),
    platforms: str = File("['linkedin']"),
    days: int = File(7),
    # Locks content_type for every slot to the user's chosen post type.
    # image | text | video | document. Defaults to image for backwards-compat
    # with older frontend builds that don't send this field.
    post_type: str = File("image"),
    context_files: list[UploadFile] = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)  # Added db for quota check
):
    """Generate a multi-day posting strategy plan with document context."""
    # Same pre-flight guard used by /generate-content — catches harmful /
    # generic / empty briefs before they waste LLM calls.
    from services.brief_guard import validate_brief
    ok, guard_reason = validate_brief(brief)
    if not ok:
        logger.warning(f"[BRIEF_GUARD] rejected plan brief from user={current_user.id}: {guard_reason[:120]}")
        raise HTTPException(status_code=422, detail=guard_reason)

    # Quota & Feature check
    check_feature(current_user, "full_scheduling")
    check_quota(db, current_user, "posts", days) # Treat each slot as a post usage
    try:
        platforms_list = json.loads(platforms)
        # Team members inherit their admin's business_dna for this request.
        _apply_member_dna_inheritance(db, current_user, None)
        extra_context = ""
        if context_files:
            files_data = [(f.filename, await f.read()) for f in context_files]
            extra_context = process_multiple_files(files_data)

        from fastapi.concurrency import run_in_threadpool
        plan_data = await run_in_threadpool(
            generate_campaign_plan,
            brief,
            platforms_list,
            days,
            user=current_user,
            extra_context=extra_context,
            post_type=post_type,
        )
        return plan_data
    except HTTPException:
        raise
    except Exception as e:
        from services.brief_guard import BriefRejected
        if isinstance(e, BriefRejected):
            logger.warning(f"[REFINER_GUARD] plan 422 → user={current_user.id} category={e.category} msg={str(e)[:120]}")
            raise HTTPException(status_code=422, detail=str(e))
        logger.error(f"Generate plan failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/start-campaign")
async def handle_start_campaign(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Start a bulk campaign generation task.

    In Lambda we dispatch the heavy work via an async self-invoke
    (`InvocationType=Event`) so the user-facing /start-campaign returns
    instantly — without it, FastAPI BackgroundTasks deadlocks Lambda's
    response (the container won't return until the background task
    finishes, browser sees ERR_EMPTY_RESPONSE after ~30s).

    Outside Lambda (local dev or any non-Lambda runtime) we fall back to
    a daemon thread so the behaviour is the same to the developer.
    """
    body = await request.json()
    brief = body.get("brief")
    plan = body.get("plan", [])
    targets = body.get("targets", {})

    if not brief or not plan:
        raise HTTPException(status_code=400, detail="Brief and Plan are required to start a campaign.")

    # AWS_LAMBDA_FUNCTION_NAME is set ONLY when running inside a real
    # Lambda execution environment. We use its presence as the runtime
    # detection signal — works in both staging and production Lambdas,
    # and the local dev path stays untouched.
    lambda_func_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME")

    if lambda_func_name:
        # In Lambda → fire-and-forget self-invoke. The async invocation
        # spins up a SEPARATE Lambda container that runs
        # `process_bulk_campaign` for up to 15 min while this invocation
        # returns to the user in < 1 second.
        try:
            import boto3  # runtime import — keeps cold-start light when not in Lambda
            _lambda = boto3.client("lambda")
            _lambda.invoke(
                FunctionName=lambda_func_name,
                InvocationType="Event",  # async; AWS returns 202 instantly
                Payload=json.dumps({
                    "campaign_task": True,
                    "user_id": current_user.id,
                    "brief": brief,
                    "plan": plan,
                    "targets": targets,
                }),
            )
            logger.info(
                f"[BULK] dispatched async self-invoke user={current_user.id} "
                f"slots={len(plan)} fn={lambda_func_name}"
            )
        except Exception as exc:
            logger.error(
                f"[BULK] failed to dispatch async self-invoke: {exc}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    "Could not dispatch the campaign background job. "
                    "Check Lambda's IAM role allows lambda:InvokeFunction on itself."
                ),
            )
    else:
        # Local dev → daemon thread. process_bulk_campaign is sync so this
        # is straightforward; the thread dies with the process if the
        # request handler exits before it finishes.
        import threading
        threading.Thread(
            target=process_bulk_campaign,
            args=(current_user.id, brief, plan, targets),
            daemon=True,
        ).start()
        logger.info(
            f"[BULK] dispatched local thread user={current_user.id} slots={len(plan)}"
        )

    return {"status": "success", "message": f"Campaign processing started for {len(plan)} slots."}


def process_bulk_campaign(user_id: int, brief: str, plan: list, targets: dict):
    """Background task: split slots by needs_research and book each accordingly.

    Two-track booking model:
      • Research slot (needs_research=true): insert as `agentic` + `pending`
        immediately. Content materializes JIT on the scheduled date via the
        existing /scheduler/process tick. This catches today's news/festival
        moment because the data doesn't exist at plan time.
      • Static slot (needs_research=false): insert as `standard` +
        `awaiting_review` with empty content, then pre-generate sequentially
        in this same background task. The user reviews + edits + approves
        each pre-generated post on the Review Queue screen before it actually
        gets scheduled (status flips to `pending` on approve).

    Per-slot targeting: each plan slot belongs to ONE platform (the planner
    now emits a day × platform grid). We scope `targets` to just that
    platform's account ids so the publisher fires exactly one row per
    (day × platform) combination.
    """
    import time as _time
    from core.database import SessionLocal
    from services.ai_service import generate_strategic_content
    db = SessionLocal()
    _bulk_t0 = _time.monotonic()
    try:
        # Count up-front so the log tells us research vs static distribution
        # before we touch the DB. Helps diagnose "everything came back as
        # research" vs "everything came back as static" classifier issues.
        _research_count = sum(1 for s in (plan or []) if s and s.get("needs_research") is not False)
        _static_count = sum(1 for s in (plan or []) if s and s.get("needs_research") is False)
        logger.info(
            f"[BULK] Start campaign user={user_id} slots={len(plan)} "
            f"(research={_research_count} static={_static_count}) targets={list(targets.keys())}"
        )

        # Clear any abandoned review queue from a previous /start-campaign
        # click the user never approved. Keeps the review screen showing only
        # the cards from the NEW run; old un-approved cards are explicitly
        # being abandoned by re-running. We touch only awaiting_review and
        # failed rows — pending (already approved) and published stay put.
        _abandoned = db.query(ScheduledPost).filter(
            ScheduledPost.user_id == user_id,
            ScheduledPost.status.in_(("awaiting_review", "failed")),
        ).all()
        if _abandoned:
            for _r in _abandoned:
                db.delete(_r)
            db.commit()
            logger.info(
                f"[BULK] Cleared {len(_abandoned)} abandoned review row(s) from prior runs "
                f"for user={user_id} before starting new campaign"
            )

        user = db.query(User).filter(User.id == user_id).first()
        user_tz = pytz.timezone(user.timezone or "UTC") if user else pytz.UTC

        # Pass 1 — INSERT every row (fast). Static rows go in as
        # awaiting_review with empty content; we'll fill them in Pass 2.
        static_row_ids: list[int] = []
        for slot in plan:
            try:
                slot_channel = (slot.get("channel") or "").lower().strip()
                if not slot_channel:
                    logger.warning(f"Skipping slot — no channel: {slot}")
                    continue

                # Scope targets to ONLY this slot's platform — each row is
                # one (day, platform) combination.
                slot_targets = {slot_channel: targets.get(slot_channel, [])}
                if not slot_targets[slot_channel]:
                    logger.warning(
                        f"Skipping slot for {slot_channel} — no account selected for this platform"
                    )
                    continue

                slot_brief = (
                    f"CAMPAIGN OVERVIEW: {brief}\n\n"
                    f"SPECIFIC POST TOPIC: {slot.get('topic')}\n"
                    f"THEME: {slot.get('theme')}\n"
                    f"CTA: {slot.get('cta')}"
                )
                date_str = slot.get("date")
                time_str = slot.get("time", "09:00")
                local_dt = user_tz.localize(datetime.fromisoformat(f"{date_str}T{time_str}:00"))
                scheduled_for = local_dt.astimezone(pytz.UTC).replace(tzinfo=None)

                # Safety clamp: if a past date slipped through (LLM edge case
                # on midnight runs or malformed plans), push it to tomorrow at
                # the same time so it doesn't fire the moment it hits the DB.
                from datetime import timedelta as _td
                _now_utc = datetime.utcnow()
                if scheduled_for <= _now_utc:
                    original = scheduled_for
                    scheduled_for = _now_utc + _td(days=1)
                    logger.warning(
                        f"Plan slot date {original} was in the past — clamped to {scheduled_for} "
                        f"(topic: {slot.get('topic')})"
                    )

                is_research = False
                is_festival = slot.get("is_festival", False)

                # Read slot's locked content_type ("Image" / "Text" / "Video" /
                # "Document") and store it on the row's media_type field so
                # pre-gen + JIT both know which media pipeline to use. Without
                # this, generate_strategic_content() falls back to its image
                # default and burns Gemini Image tokens even when the user
                # picked a Text-only campaign.
                _ct_raw = (slot.get("content_type") or "image").strip().lower()
                _CT_MAP = {"image": "image", "text": "text", "video": "video", "document": "document"}
                slot_media_type = _CT_MAP.get(_ct_raw, "image")

                # Both research AND static slots land in awaiting_review.
                # Difference: static rows get content PRE-GENERATED now and the
                # user reviews it; research rows stay empty (content="{}") and
                # the user approves the slot — JIT generation runs at fire time.
                # Nothing auto-schedules. Approval is required for both tracks.
                if is_research:
                    row = ScheduledPost(
                        content="{}",
                        image_url=None,
                        targets=json.dumps(slot_targets),
                        scheduled_for=scheduled_for,
                        timezone="UTC",
                        post_type="agentic",
                        media_type=slot_media_type,
                        campaign_brief=slot_brief,
                        user_id=user_id,
                        status="awaiting_review",
                    )
                    db.add(row)
                    db.commit()
                    logger.info(
                        f"[BULK] Research slot booked (agentic+awaiting_review) — {slot_channel} @ {scheduled_for} UTC "
                        f"media={slot_media_type} topic={slot.get('topic')!r} festival={is_festival}"
                    )
                else:
                    # Static path — placeholder row first, populate in Pass 2.
                    row = ScheduledPost(
                        content="{}",
                        image_url=None,
                        targets=json.dumps(slot_targets),
                        scheduled_for=scheduled_for,
                        timezone="UTC",
                        post_type="standard",
                        media_type=slot_media_type,
                        campaign_brief=slot_brief,
                        user_id=user_id,
                        status="awaiting_review",
                    )
                    db.add(row)
                    db.commit()
                    db.refresh(row)
                    static_row_ids.append(row.id)
                    logger.info(
                        f"[BULK] Static slot booked (awaiting_review placeholder, id={row.id}) — "
                        f"{slot_channel} @ {scheduled_for} UTC media={slot_media_type} topic={slot.get('topic')!r}"
                    )
            except Exception as item_err:
                logger.error(f"[BULK] Failed to insert plan slot: {item_err}", exc_info=True)
                continue

        # Pass 2 — pre-generate content for every awaiting_review row.
        # Sequential to keep memory + API rate manageable; the review page
        # polls so the user sees cards appear as each one completes.
        logger.info(
            f"[BULK] Pass-1 done in {(_time.monotonic()-_bulk_t0):.2f}s — "
            f"{len(static_row_ids)} static row(s) queued for pre-gen, "
            f"{_research_count - (_static_count and 0)} research row(s) already awaiting_review"
        )
        for idx, sp_id in enumerate(static_row_ids, 1):
            _t = _time.monotonic()
            try:
                _pregenerate_awaiting_review(db, sp_id, user)
                logger.info(
                    f"[BULK] Pre-gen {idx}/{len(static_row_ids)} done for slot {sp_id} "
                    f"in {(_time.monotonic()-_t):.2f}s"
                )
            except Exception as gen_err:
                logger.error(
                    f"[BULK] Pre-gen {idx}/{len(static_row_ids)} FAILED for slot {sp_id} "
                    f"after {(_time.monotonic()-_t):.2f}s: {gen_err}",
                    exc_info=True,
                )
                row = db.query(ScheduledPost).filter(ScheduledPost.id == sp_id).first()
                if row:
                    row.status = "failed"
                    row.error_message = f"Pre-generation failed: {gen_err}"
                    db.commit()
                continue
        logger.info(
            f"[BULK] All done for user {user_id} in {(_time.monotonic()-_bulk_t0):.2f}s "
            f"({len(static_row_ids)} static pre-generated, {_research_count} research awaiting approval)"
        )
    finally:
        db.close()


def _pregenerate_awaiting_review(db, sp_id: int, user: User):
    """Run the full single-post pipeline for one awaiting_review row.

    Picks the recommended variant + best image and stores them flat on the
    row so the review UI can show "here's what we generated — approve, edit,
    or regenerate". Status stays `awaiting_review` until the user approves.
    """
    from services.ai_service import generate_strategic_content

    row = db.query(ScheduledPost).filter(ScheduledPost.id == sp_id).first()
    if not row:
        logger.warning(f"[PREGEN] Row {sp_id} vanished — skipping")
        return

    slot_targets = json.loads(row.targets) if row.targets else {}
    platforms = list(slot_targets.keys()) or ["linkedin"]
    # media_type was set at booking time from the plan slot's content_type.
    # Pass it through so the content agent + visual pipeline run in the right
    # mode (image → AD v2 + Gemini Image, text → skips visuals entirely,
    # video / document → skip critic + visuals).
    slot_media_type = (row.media_type or "image").lower()
    logger.info(
        f"[PREGEN] Starting slot {sp_id} platforms={platforms} "
        f"media_type={slot_media_type} brief_len={len(row.campaign_brief or '')}"
    )

    gen_data = generate_strategic_content(
        row.campaign_brief, platforms, user,
        post_type=slot_media_type,
    )

    # Pick recommendation.best_variant if present, else the first variant key.
    rec = (gen_data or {}).get("recommendation", {}) or {}
    recommended_variant = rec.get("best_variant", "viral_reach")

    all_content = (gen_data or {}).get("content", {}) or {}
    final_content_map = {}
    for p in platforms:
        p_variants = all_content.get(p.lower(), {}) or {}
        chosen = p_variants.get(recommended_variant) or (next(iter(p_variants.values()), "") if p_variants else "")
        final_content_map[p] = chosen

    # Sanity check on the returned content before we store. Empty maps mean
    # the content agent failed silently and we'd flash an empty card on the
    # review screen otherwise.
    _content_lens = {p: len(v or "") for p, v in final_content_map.items()}
    if not any(_content_lens.values()):
        raise RuntimeError(
            f"Content agent returned empty content for all platforms. "
            f"variant={recommended_variant} content_keys={list(all_content.keys())}"
        )

    row.content = json.dumps(final_content_map)

    # Same visual-picking rule the JIT scheduler uses: V1 (high_interaction)
    # historically scores highest in our CSV logger sample.
    visuals = (gen_data or {}).get("visuals", []) or []
    if len(visuals) >= 2:
        row.image_url = (visuals[1] or {}).get("url")
    elif visuals:
        row.image_url = (visuals[0] or {}).get("url")

    db.commit()
    logger.info(
        f"[PREGEN] Slot {sp_id} READY for review — variant={recommended_variant} "
        f"content_lens={_content_lens} has_image={bool(row.image_url)} "
        f"visual_count={len(visuals)}"
    )


@router.post("/generate-content")  # NOTE: marker for /generate-content trace
async def generate_content(
    campaign_brief: str = File(...),
    platforms: str = File("['linkedin']"),
    logo_image: UploadFile = File(None),
    logo_url: str = File(None),
    context_files: list[UploadFile] = File(None),
    # Agent post type: image (default) runs the full visualist (33 AI images),
    # text/video/document skip visuals so the user can pick a copy variant or
    # attach their own media at the approve step.
    post_type: str = File("image"),
    # Which Business DNA entity to generate against. When a product name is
    # passed and it exists in business_dna.products, the whole pipeline (text
    # agents AND visuals) uses that product's branding instead of the company's.
    product_name: Optional[str] = Query(None),
    # User-selected aspect ratio from CampaignBrief chip (1:1, 9:16, 16:9,
    # 4:5, 3:4, 2:3). Flows to Visualist v2 -> Gemini image_config.aspect_ratio.
    aspect_ratio: Optional[str] = Query(None),
    # JSON-encoded list of user-uploaded product reference image URLs (S3).
    # Each URL is fetched once and attached to every gpt-image-2 slide render
    # so the carousel features the actual product, not generic stock-style
    # imagery. Capped to 4 in the pipeline. Example: '["https://..png","..."]'.
    product_image_urls: Optional[str] = File(None),
    # User-selected visual style slug from the CampaignBrief Style chip.
    # None or "auto" (default) → current pipeline behavior, no prompt change.
    # Any explicit slug → STYLE LOCK block injected at top of Art Director
    # prompt + industry playbook suppressed. See services/style_catalog.py.
    image_style: Optional[str] = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Orchestrates strategic content and visual generation with logos and docs."""
    # -------- Pre-flight brief guard --------
    # Rule-based validation BEFORE we burn Gemini tokens on obviously bad
    # briefs (harmful language, "create a post" placeholder, empty input,
    # emoji spam). Returns a clean 422 with a user-facing message the
    # frontend can show verbatim. See services/brief_guard.py.
    import time as _trace_time
    from datetime import datetime, timezone
    _trace_t0_wall = datetime.now(timezone.utc)
    _trace_t0_mono = _trace_time.monotonic()
    _trace_request_id = f"trace_{int(_trace_t0_mono * 1000) % 1_000_000_000}"
    logger.info(
        f"[TRACE] {_trace_request_id} BEGIN /generate-content "
        f"user={current_user.id} brief_len={len(campaign_brief)} "
        f"post_type={post_type!r} ts={_trace_t0_wall.isoformat()}"
    )
    print(f"!!! /generate-content HIT  user={current_user.id} brief_len={len(campaign_brief)} post_type={post_type!r} !!!", flush=True)

    # Start a per-request cost ledger. All instrumented Gemini/OpenAI
    # calls that happen while this request runs will publish their token
    # counts + costs into it. Finalized in the finally block below —
    # writes one row to apps/backend/api_cost_ledger.csv and uploads the
    # whole file to s3://<bucket>/cost_ledgers/api_cost_ledger.csv.
    from services.cost_ledger import start_ledger, finalize_ledger
    # Resolve a stable label for the ledger's BUSINESS_DNA column. Same
    # fallback order used by the magic-image pipeline (ai_service.py):
    # picked product → company DNA name → user.company_name → user.email.
    # Prevents blank cells when the user posts against Company DNA (no
    # product picked) instead of a specific product.
    try:
        _dna_for_label = _active_dna(current_user, product_name) or {}
    except Exception:
        _dna_for_label = getattr(current_user, "business_dna", None) or {}
    _ledger_label = (
        (product_name or "").strip()
        or (isinstance(_dna_for_label, dict) and (
            _dna_for_label.get("company_name")
            or _dna_for_label.get("business_name")
        ))
        or getattr(current_user, "company_name", None)
        or getattr(current_user, "email", "")
        or ""
    )
    _cost_ledger = start_ledger(
        user_id=current_user.id,
        user_email=getattr(current_user, "email", "") or "",
        business_dna=str(_ledger_label).strip(),
        post_type=post_type,
        trace_id=_trace_request_id,
        image_style=(image_style or "auto").strip().lower(),
    )

    from services.brief_guard import validate_brief
    ok, guard_reason = validate_brief(campaign_brief)
    if not ok:
        logger.warning(f"[BRIEF_GUARD] rejected brief from user={current_user.id}: {guard_reason[:120]}")
        raise HTTPException(status_code=422, detail=guard_reason)

    # Each content generation produces visuals (AI images)
    check_quota(db, current_user, "images", 3) # Standard generation produces ~3 backgrounds
    try:
        platforms_list = json.loads(platforms)
        # Admin-only "None" option from the Business DNA chip. When set, the
        # whole pipeline runs WITHOUT any brand context — the agents see only
        # the campaign brief + any per-request docs. Members are silently
        # treated as their assigned brand (the inheritance helper clamps it).
        NO_DNA_SENTINEL = "__none__"
        no_dna = (product_name == NO_DNA_SENTINEL) and not is_team_member(current_user)
        if product_name == NO_DNA_SENTINEL:
            # Strip the sentinel so downstream helpers don't try to look it up
            # as a real product key.
            product_name = None
        # Team members inherit their admin's business_dna for this request.
        if not no_dna:
            product_name = _apply_member_dna_inheritance(db, current_user, product_name)
            active_dna = _active_dna(current_user, product_name)
        else:
            active_dna = {}

        # Resolve logo: uploaded file > explicit URL > selected-DNA logo_url.
        # When no_dna is set we skip every logo source — the user opted out of
        # brand context entirely, so a leftover logo would contradict the brief.
        #
        # DNA logo_url can be one of two shapes depending on where the user
        # uploaded it:
        #   • https://…  — extracted from the company website (DNA extractor)
        #   • data:image/png;base64,…  — user drag-dropped in the Business DNA
        #                                 Brand Mark card. FileReader gives us
        #                                 a data URL that gets stored verbatim
        #                                 in the JSONB business_dna column.
        # Both shapes must yield the same raw bytes for the pipeline.
        logo_bytes = None
        _logo_source = None  # for diagnostic log line below

        def _decode_logo_source(src: str) -> bytes | None:
            """Convert a logo reference (http URL or base64 data URL) to raw bytes."""
            if not isinstance(src, str) or not src:
                return None
            if src.startswith("data:") and ";base64," in src:
                import base64 as _b64
                try:
                    return _b64.b64decode(src.split(";base64,", 1)[1])
                except Exception as _e:
                    logger.warning(f"[LOGO] base64 decode failed: {_e}")
                    return None
            if src.startswith("http"):
                try:
                    r = requests.get(src, timeout=10)
                    if r.status_code == 200:
                        return r.content
                    logger.warning(f"[LOGO] URL fetch returned {r.status_code}: {src[:120]}")
                except Exception as _e:
                    logger.warning(f"[LOGO] URL fetch failed: {_e}")
            return None

        if not no_dna:
            if logo_image:
                logo_bytes = await logo_image.read()
                _logo_source = "upload"
            elif logo_url:
                logo_bytes = _decode_logo_source(logo_url)
                if logo_bytes:
                    _logo_source = f"logo_url:{logo_url[:60]}"

            if not logo_bytes:
                dna_logo = active_dna.get("logo_url")
                if dna_logo:
                    logo_bytes = _decode_logo_source(dna_logo)
                    if logo_bytes:
                        _logo_source = (
                            f"dna:{dna_logo[:60]}" if dna_logo.startswith("http")
                            else "dna:base64"
                        )
                    else:
                        logger.warning(
                            f"[LOGO] DNA logo_url present but decode failed — "
                            f"first 40 chars: {dna_logo[:40]!r}"
                        )
                else:
                    logger.warning(
                        f"[LOGO] active_dna has no logo_url — product_name={product_name!r}, "
                        f"dna keys={list(active_dna.keys())[:15]}"
                    )

        # Diagnostic line the user asked for: "why is the logo missing?"
        # This tells you exactly which source succeeded (or that none did).
        logger.info(
            f"[LOGO] resolved logo_bytes={'yes ('+str(len(logo_bytes))+' bytes)' if logo_bytes else 'NO'} "
            f"source={_logo_source or 'none'} no_dna={no_dna}"
        )

        # Process extra context
        extra_context_str = ""
        if context_files:
            files_data = [(f.filename, await f.read()) for f in context_files]
            extra_context_str = process_multiple_files(files_data)

        # Assemble extra_context — dict form so Visualist v2 can read aspect_ratio.
        # We always use the dict form now; the docs-string lives inside `docs`.
        extra_context = {
            "docs": extra_context_str,
            "aspect_ratio": aspect_ratio or "16:9",
            "product_name": product_name,
        }

        # Parse the optional product_image_urls list (JSON string).
        _product_image_urls = None
        if product_image_urls:
            try:
                _parsed = json.loads(product_image_urls)
                if isinstance(_parsed, list):
                    _product_image_urls = [u for u in _parsed if isinstance(u, str) and u.startswith("http")]
                    if _product_image_urls:
                        logger.info(
                            f"[product_refs] {len(_product_image_urls)} product reference URL(s) "
                            f"supplied for user={current_user.id}"
                        )
            except Exception as _e:
                logger.warning(f"[product_refs] failed to parse product_image_urls: {_e}")

        from fastapi.concurrency import run_in_threadpool
        data = await run_in_threadpool(
            generate_strategic_content,
            campaign_brief,
            platforms_list,
            # user=None when "None" was picked → orchestrator skips
            # _build_user_context entirely (no DNA, no docs, no brand colours).
            user=None if no_dna else current_user,
            logo_bytes=logo_bytes,
            extra_context=extra_context,
            product_name=product_name,
            post_type=post_type,
            product_image_urls=_product_image_urls,
            image_style=image_style,
        )
        # Echo the post_type back so the frontend can remember what was
        # generated (and route the approve screen accordingly).
        data["post_type"] = post_type

        # Local-only: append a debug row to apps/backend/local_results.csv so
        # we can eyeball the 4 variants × 4 platforms + 3 images per request
        # side-by-side. No-op when AWS_LAMBDA_FUNCTION_NAME is set, so
        # production never touches the disk. Wrapped in try/except as belt-and-
        # braces — local debugging must never break a request.
        try:
            from services.csv_logger import log_generation_result
            log_generation_result(
                data=data,
                campaign_brief=campaign_brief,
                current_user=current_user,
                product_name=product_name,
                business_dna_label=("__none__" if no_dna else None),
            )
        except Exception as _csv_err:
            logger.warning(f"[csv_logger] hook failed: {_csv_err}")
        # TRACE end-of-handler summary so the user can correlate stopwatch
        # against backend wall-clock at a glance.
        _trace_total = _trace_time.monotonic() - _trace_t0_mono
        _trace_t1_wall = datetime.now(timezone.utc)
        logger.info(
            f"[TRACE] {_trace_request_id} END /generate-content "
            f"total={_trace_total:.2f}s "
            f"ts_in={_trace_t0_wall.isoformat()} "
            f"ts_out={_trace_t1_wall.isoformat()} "
            f"post_type={post_type!r}"
        )
        # Finalize the cost ledger — writes one row to the local CSV and
        # uploads the whole file to S3. Non-fatal on any error.
        try:
            finalize_ledger(_cost_ledger)
        except Exception as _le:
            logger.warning(f"[cost_ledger] finalize failed (non-fatal): {_le}")
        return data
    except HTTPException:
        # Even on 422, still record whatever costs accrued before the reject.
        try:
            finalize_ledger(_cost_ledger)
        except Exception as _le:
            logger.warning(f"[cost_ledger] finalize (HTTPException path) failed: {_le}")
        raise
    except Exception as e:
        # Record cost even on 500 — partial API calls still cost money
        # (e.g. we spent on REFINER before hitting an OpenAI quota fail).
        try:
            finalize_ledger(_cost_ledger)
        except Exception as _le:
            logger.warning(f"[cost_ledger] finalize (exception path) failed: {_le}")
        # Semantic rejection from the refiner surfaces as BriefRejected;
        # convert to 422 with the refiner's user-facing message instead
        # of a 500 that hides the real reason.
        from services.brief_guard import BriefRejected
        if isinstance(e, BriefRejected):
            logger.warning(f"[REFINER_GUARD] 422 → user={current_user.id} category={e.category} msg={str(e)[:120]}")
            raise HTTPException(status_code=422, detail=str(e))
        logger.error(f"Generation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"AI Generation failed: {str(e)}")


@router.post("/generate-visuals")
async def handle_generate_visuals(
    campaign_brief: str = File(...),
    linkedin_content: str = File(...),
    platforms: str = File("['linkedin']"),
    logo_image: UploadFile = File(None),
    logo_url: str = File(None),
    product_name: Optional[str] = Query(None),
    # User-selected aspect ratio — flows to v2 Gemini image_config or to
    # gpt-image-2's size param in the magic pipeline.
    aspect_ratio: Optional[str] = Query(None),
    # JSON-encoded full content_dict {platform: {variant_key: text}}.
    # Required by the Magic Image Pipeline (Agent 1 needs per-variant text).
    # When None, only the magic pipeline degrades — legacy paths still work
    # off `linkedin_content` alone.
    content_variants_json: Optional[str] = File(None),
    # User-selected visual style slug (see /config/image-styles). None or
    # "auto" (default) = current behavior; explicit style = STYLE LOCK.
    image_style: Optional[str] = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Regenerate visual variants for an existing content draft.

    Default path (May 2026+): Freeform pipeline — text agent picks template
    + writes copy + image_prompt, image model renders ONE finished post per
    variant. Produces N=5 variants, each its own image with text/copy baked
    in. No PIL compositor.
    Legacy escape hatch: USE_VISUALIST_V2=false env → old 3-narrative-device
    pipeline.
    """
    # Freeform produces 3 variants (cost-trimmed from 5 → 3 May 2026).
    check_quota(db, current_user, "images", 3)
    import os
    try:
        # Team members inherit their admin's business_dna for this request.
        product_name = _apply_member_dna_inheritance(db, current_user, product_name)
        # Scope DNA to the selected product (if any); falls back to company.
        dna = _active_dna(current_user, product_name)

        # Same base64-or-http decoder as /generate-content — see comment there.
        def _decode_logo_source(src: str) -> bytes | None:
            if not isinstance(src, str) or not src:
                return None
            if src.startswith("data:") and ";base64," in src:
                import base64 as _b64
                try:
                    return _b64.b64decode(src.split(";base64,", 1)[1])
                except Exception as _e:
                    logger.warning(f"LOGO_TRACE: base64 decode failed: {_e}")
                    return None
            if src.startswith("http"):
                try:
                    r = requests.get(src, timeout=10)
                    if r.status_code == 200:
                        return r.content
                    logger.warning(f"LOGO_TRACE: URL fetch returned {r.status_code}: {src[:120]}")
                except Exception as _e:
                    logger.warning(f"LOGO_TRACE: URL fetch failed: {_e}")
            return None

        logo_bytes = None
        if logo_image:
            logger.info("LOGO_TRACE: Using uploaded multipart logo file.")
            logo_bytes = await logo_image.read()
        elif logo_url:
            logger.info(f"LOGO_TRACE: Decoding logo_url ({logo_url[:60]!r}...)")
            logo_bytes = _decode_logo_source(logo_url)
            if logo_bytes:
                logger.info(f"LOGO_TRACE: Decoded {len(logo_bytes)} bytes from logo_url.")

        if not logo_bytes:
            dna_logo = dna.get("logo_url")
            if dna_logo:
                logger.info(
                    f"LOGO_TRACE: Falling back to active-DNA logo (product_name={product_name!r}, "
                    f"format={'base64' if dna_logo.startswith('data:') else 'http' if dna_logo.startswith('http') else 'other'})"
                )
                logo_bytes = _decode_logo_source(dna_logo)
                if logo_bytes:
                    logger.info(f"LOGO_TRACE: Decoded DNA logo ({len(logo_bytes)} bytes).")

        if not logo_bytes:
            logger.warning(
                f"LOGO_TRACE: No logo found in upload, URL, or active DNA. "
                f"(product_name={product_name!r}, dna_has_logo_url={bool(dna.get('logo_url'))}, "
                f"dna_keys={list(dna.keys())[:10]})"
            )
        else:
            logger.info(
                f"LOGO_TRACE: logo_bytes ready ({len(logo_bytes)} bytes) → forwarding to magic pipeline"
            )

        primary_color = dna.get("colors", {}).get("primary", "#FF4500")
        raw_url = dna.get('url') or getattr(current_user, 'business_url', '') or ''
        domain_name = raw_url.split('//')[-1].split('/')[0].strip() or "pipelyt.com"

        _v2_flag = os.getenv("USE_VISUALIST_V2", "true").lower()
        use_v2 = _v2_flag not in ("0", "false", "no", "off")

        # ── MAGIC IMAGE PIPELINE BRANCH ──────────────────────────────
        # Was previously gated behind USE_MAGIC_IMAGE_PIPELINE env flag.
        # The Regenerate button in the frontend now always sends
        # content_variants_json, and users expect Art Director + gpt-image-2
        # for regeneration (not the v4 fallback). Route to magic pipeline
        # whenever the caller supplies the per-variant content dict.
        # Legacy v4 branch still catches callers that don't provide it.
        from fastapi.concurrency import run_in_threadpool

        if content_variants_json:
            logger.info(
                f"/generate-visuals using Magic Image Pipeline "
                f"(GPT-5 + gpt-image-2, aspect={aspect_ratio or '1:1'})"
            )
            import json as _json
            try:
                content_dict = _json.loads(content_variants_json)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"content_variants_json is not valid JSON: {exc}",
                )

            try:
                selected_platforms = _json.loads(platforms) if platforms else []
            except Exception:
                selected_platforms = ["linkedin"]
            if not isinstance(selected_platforms, list):
                selected_platforms = ["linkedin"]

            # Business DNA label for the CSV — prefer product name, then DNA
            # company name, then user email.
            business_dna_label = (
                product_name
                or dna.get("company_name")
                or dna.get("business_name")
                or getattr(current_user, "email", "")
                or "anonymous"
            )

            from services.magic_image_pipeline import run_magic_image_pipeline
            magic_results = await run_in_threadpool(
                run_magic_image_pipeline,
                campaign_brief=campaign_brief,
                content_dict=content_dict,
                selected_platforms=selected_platforms,
                primary_brand_color=primary_color,
                aspect_ratio=aspect_ratio or "1:1",
                logo_bytes=logo_bytes,
                business_dna_label=business_dna_label,
                image_style=image_style,
            )

            # Shape the response to match what the existing frontend expects:
            # a list of dicts with at minimum `url`. The magic pipeline already
            # produces that shape — pass through plus add legacy-compatible
            # fields (variant_idx, headline preview) so the gallery / preview
            # UI doesn't break.
            response_variants = [
                {
                    "url":           r["url"],
                    "variant_idx":   i,
                    "variant_type":  r["variant_type"],
                    "magic_prompt":  r["magic_prompt"],
                    "platform_used": r["platform_used"],
                    # Legacy shape so the existing gallery code keeps working:
                    "image_url":     r["url"],
                    "pipeline":      "magic-gpt-image-2",
                }
                for i, r in enumerate(magic_results)
            ]
            return {
                "visuals":    response_variants,
                "pipeline":   "magic-gpt-image-2",
                "n_variants": len(response_variants),
            }

        if use_v2:
            logger.info(f"/generate-visuals using Freeform pipeline (aspect={aspect_ratio or '1:1'})")
            from services.ai_service import _generate_visuals_v2, _build_user_context

            # Pull primary color from DNA via the user-context helper (same
            # way the main pipeline resolves it).
            _, _, dna_primary = _build_user_context(
                current_user, extra_context="", product_name=product_name
            )
            primary_color = dna_primary or primary_color
            aspect = aspect_ratio or "16:9"

            # Use the provided linkedin_content + campaign_brief as the refined
            # brief so the freeform agent has real selected copy to match.
            refined_brief = (
                f"{campaign_brief}\n\nSELECTED POST COPY (for visual alignment):\n{linkedin_content}"
                if linkedin_content
                else campaign_brief
            )

            visuals = await run_in_threadpool(
                _generate_visuals_v2,
                refined_brief,
                dna,
                logo_bytes,
                primary_color,
                aspect,
                current_user,
                3,  # n_variants — freeform tile count (cost-trimmed from 5 → 3)
                None,  # visualist_output (legacy)
                product_name,  # product-scope V2 PIL pipeline
            )

            # Persist to gallery (best-effort) — copy fields come from variant 0
            # since each freeform variant has its own headline/sub/cta.
            try:
                if current_user and visuals:
                    from models import GeneratedCampaign
                    first = visuals[0] or {}
                    db_session = next(get_db())
                    try:
                        db_session.add(GeneratedCampaign(
                            user_id=current_user.id,
                            campaign_id=first.get("campaign_id"),
                            headline=first.get("headline"),
                            subheading=first.get("subheading"),
                            cta=first.get("cta"),
                            primary_color=primary_color,
                            aspect_ratio=aspect,
                            visuals=visuals,
                        ))
                        db_session.commit()
                    finally:
                        db_session.close()
            except Exception as persist_err:
                logger.warning(f"Gallery persist failed (non-fatal): {persist_err}")

            first = (visuals[0] if visuals else {}) or {}
            return {
                "visuals": visuals,
                "visualist_meta": {
                    "overlay_copy": {
                        "headline": first.get("headline", ""),
                        "subheading": first.get("subheading", ""),
                        "cta": first.get("cta", ""),
                    },
                    "primary_color": primary_color,
                    "aspect_ratio": aspect,
                },
            }

        # Legacy narrative-device path REMOVED (May 2026). Even when the
        # USE_VISUALIST_V2 env flag is unset/false, we now run the freeform
        # pipeline so the user always gets the new 5-variant output. The
        # env flag is preserved only for opting OUT of visual generation
        # entirely (returns empty visuals).
        logger.info("/generate-visuals: env flag bypass — running Freeform pipeline anyway")
        from services.ai_service import _generate_visuals_v2
        aspect = aspect_ratio or "16:9"
        refined_brief = (
            f"{campaign_brief}\n\nSELECTED POST COPY (for visual alignment):\n{linkedin_content}"
            if linkedin_content else campaign_brief
        )
        visuals = await run_in_threadpool(
            _generate_visuals_v2,
            refined_brief,
            dna,
            logo_bytes,
            primary_color,
            aspect,
            current_user,
            3,  # n_variants — freeform tile count (cost-trimmed from 5 → 3)
            None,  # visualist_output (legacy)
            product_name,  # product-scope V2 PIL pipeline
        )
        first = (visuals[0] if visuals else {}) or {}
        return {
            "visuals": visuals,
            "visualist_meta": {
                "overlay_copy": {
                    "headline": first.get("headline", ""),
                    "subheading": first.get("subheading", ""),
                    "cta": first.get("cta", ""),
                },
                "primary_color": primary_color,
                "aspect_ratio": aspect,
            },
            "pipeline": "freeform_v3",
        }
    except Exception as e:
        logger.error(f"Visual regeneration failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-custom-image")
async def handle_custom_image(
    user_prompt: str = File(...),
    logo_image: UploadFile = File(None),
    logo_url: str = File(None),
    product_name: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Generate a single image using gpt-image-2 with the user's raw prompt.

    Was previously routing through `_gen_single_variant` (v4 Gemini
    pipeline). Users on the Agent Post review flow expect the Custom
    Prompt button to hit gpt-image-2 directly, matching the fidelity of
    the initial generation. The user's typed prompt IS the image_prompt —
    no Art Director rewrite in between.
    """
    try:
        # Team members inherit their admin's business_dna for this request.
        product_name = _apply_member_dna_inheritance(db, current_user, product_name)
        dna = _active_dna(current_user, product_name)

        # Same base64-or-http decoder as /generate-content — Business DNA
        # Brand Mark uploads store the logo as a base64 data URL; DNA
        # extractor stores an https URL. Both must yield raw bytes.
        def _decode_logo_source(src: str) -> bytes | None:
            if not isinstance(src, str) or not src:
                return None
            if src.startswith("data:") and ";base64," in src:
                import base64 as _b64
                try:
                    return _b64.b64decode(src.split(";base64,", 1)[1])
                except Exception:
                    return None
            if src.startswith("http"):
                try:
                    r = requests.get(src, timeout=10)
                    if r.status_code == 200:
                        return r.content
                except Exception:
                    pass
            return None

        logo_bytes = None
        if logo_image:
            logo_bytes = await logo_image.read()
        elif logo_url:
            logo_bytes = _decode_logo_source(logo_url)

        # Fall back to the active DNA's logo if nothing uploaded/URL-fetched.
        if not logo_bytes:
            dna_logo = dna.get("logo_url")
            if dna_logo:
                logo_bytes = _decode_logo_source(dna_logo)

        # Route to gpt-image-2 directly. User prompt becomes the image_prompt
        # verbatim; no Art Director / template rewriting in the middle.
        from services.magic_image_pipeline import (
            _call_agent2_render,
            _upload_png_to_s3,
        )
        from fastapi.concurrency import run_in_threadpool

        png_bytes = await run_in_threadpool(
            _call_agent2_render,
            magic_prompt=user_prompt,
            logo_bytes=logo_bytes,
            aspect_ratio="1:1",
            product_images=None,
        )
        if not png_bytes:
            raise Exception("gpt-image-2 returned no image bytes.")

        s3_url = await run_in_threadpool(
            _upload_png_to_s3, png_bytes, variant_type="custom_prompt",
        )

        return {
            "url": s3_url,
            "name": "Custom Generation",
            "heading": "Custom Prompt Result",
            "sub_heading": user_prompt,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Custom image generation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload-media/presign")
async def presign_media_upload(
    body: dict,
    current_user: User = Depends(get_current_user),
):
    """Return a presigned S3 PUT URL so the browser can upload large files
    (videos up to 1 GB, PDFs up to 100 MB) DIRECTLY to S3, bypassing Lambda.

    Why this matters:
      - API Gateway caps request bodies at 10 MB and Lambda at 6 MB synchronous
        payload. Anything larger must go to S3 directly.
      - Also avoids pulling a 1 GB video into Lambda memory just to forward it.
      - The client PUTs the raw bytes to `upload_url`, then tells us the final
        `public_url` when it's done. We validate media_type server-side at
        publish time.

    Request body: { filename, content_type, size_bytes }
    Response:     { upload_url, public_url, key, expires_in }
    """
    s3 = get_s3_client()
    if not s3 or not S3_BUCKET_NAME:
        raise HTTPException(status_code=500, detail="S3 is not configured on the backend")

    filename = (body.get("filename") or "upload.bin").strip()
    content_type = (body.get("content_type") or "application/octet-stream").strip()
    size_bytes = int(body.get("size_bytes") or 0)

    # Hard cap — 1 GB. Anything bigger is almost certainly a mistake / abuse,
    # and none of our target platforms accept >1.5 GB anyway.
    MAX_BYTES = 1 * 1024 * 1024 * 1024
    if size_bytes > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({size_bytes} bytes). Max allowed is 1 GB.",
        )

    # Classify by MIME prefix so we can route at publish time without
    # re-sniffing content.
    if content_type.startswith("image/"):
        media_type = "image"
    elif content_type.startswith("video/"):
        media_type = "video"
    elif content_type in {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }:
        media_type = "document"
    else:
        raise HTTPException(status_code=415, detail=f"Unsupported content type: {content_type}")

    # Sanitize extension — Facebook chokes on spaces/parens/unicode in URL
    # paths, see the earlier [URL_SAFE] guard at publish time.
    import re as _re
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()
        ext = _re.sub(r"[^a-z0-9.]", "", ext)[:8]
    if not ext:
        ext = {"image": ".jpg", "video": ".mp4", "document": ".pdf"}[media_type]

    key = f"uploads/{media_type}/{uuid.uuid4().hex}{ext}"

    try:
        upload_url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": S3_BUCKET_NAME,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=900,  # 15 minutes to upload
        )
        public_url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{key}"
        logger.info(f"[PRESIGN] user={current_user.id} media_type={media_type} size={size_bytes} key={key}")
        return {
            "upload_url": upload_url,
            "public_url": public_url,
            "key": key,
            "media_type": media_type,
            "expires_in": 900,
        }
    except Exception as e:
        logger.error(f"[PRESIGN] failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create presigned URL: {e}")


@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...), current_user: User = Depends(get_current_user)):
    s3 = get_s3_client()
    if not s3 or not S3_BUCKET_NAME:
        raise HTTPException(status_code=500, detail="S3 is not configured properly in the backend")
    try:
        logger.info(f"Starting S3 upload for file: {file.filename}")
        # Sanitize the filename so the resulting S3 URL is always safe to
        # hand to third-party fetchers (Facebook Graph API in particular
        # rejects URLs with spaces / parens / unicode in the path — it
        # returns a cryptic "Missing or invalid image file" #100 error).
        # Keep the original extension (Meta/LinkedIn need it to detect
        # mime type) but drop everything else; the uuid prefix already
        # guarantees uniqueness.
        import re as _re
        original = file.filename or "upload.jpg"
        ext = ""
        if "." in original:
            ext = "." + original.rsplit(".", 1)[-1].lower()
            ext = _re.sub(r"[^a-z0-9.]", "", ext)[:8] or ".jpg"
        else:
            ext = ".jpg"
        filename = f"{uuid.uuid4().hex}{ext}"
        s3.upload_fileobj(
            file.file,
            S3_BUCKET_NAME,
            filename,
            ExtraArgs={'ContentType': file.content_type}
        )
        url = f"https://{S3_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{filename}"
        logger.info(f"S3 upload success. URL: {url}")
        return {"url": url}
    except Exception as e:
        logger.error(f"S3 Upload Error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to upload to S3: {str(e)}")


async def process_publishing_internal(
    db: Session, user_id: int, content: str, image_url: str, targets: dict,
    media_type: str = None, dna_product_id: str = None,
    thumbnail_url: str = None,
    # YouTube-specific fields (Phase 2 — Manual Post → Video Mode). Only
    # consumed when targets["youtube"] is non-empty; all other platform
    # branches ignore them. Validated server-side again as belt-and-braces.
    youtube_title: str = None,
    youtube_description: str = None,
    youtube_tags: list = None,
    youtube_category_id: str = "22",
    youtube_privacy: str = "public",
    # TikTok-specific fields (Phase 2 — Manual Post → TikTok Mode). Only
    # consumed when targets["tiktok"] is non-empty.
    tiktok_caption: str = None,
    tiktok_privacy: str = "SELF_ONLY",
):
    # Start wall-clock timer so we can print a per-platform + total timing
    # summary at the end. Helps pinpoint whether LinkedIn video upload, IG
    # Reels polling, or the Stripe media-upload step is the slow branch.
    _publish_t0 = time.monotonic()
    _per_platform_times = {}
    logger.info(
        f"[PUBLISH_TIMING] start user_id={user_id} media_type={media_type!r} "
        f"platforms={list((targets or {}).keys())} has_media={bool(image_url)}"
    )
    saved_public_url = image_url
    image_data_bytes = None
    
    if image_url and image_url.startswith("data:image"):
        try:
            image_data_bytes = base64.b64decode(image_url.split(",", 1)[1])
        except Exception as e:
            logger.error(f"Failed to decode base64 image: {str(e)}")
            image_data_bytes = None

    if image_data_bytes:
        s3 = get_s3_client()
        if s3 and S3_BUCKET_NAME:
            filename = f"{uuid.uuid4().hex}.jpg"
            try:
                logger.info(f"Attempting base64 S3 upload: {filename}")
                from io import BytesIO
                s3.upload_fileobj(
                    BytesIO(image_data_bytes),
                    S3_BUCKET_NAME,
                    filename,
                    ExtraArgs={'ContentType': 'image/jpeg'}
                )
                saved_public_url = f"https://{S3_BUCKET_NAME}.s3.amazonaws.com/{filename}"
                logger.info(f"Base64 S3 upload success. Public URL: {saved_public_url}")
            except Exception as e:
                # CRITICAL: if S3 upload fails, DO NOT proceed with a base64
                # data-URL — social APIs (FB/IG/LinkedIn) require public HTTPS
                # URLs, and passing a data-URL makes the platform return an
                # opaque HTML error page that the user can't diagnose.
                # Surface the real S3 error instead.
                logger.error(f"Base64 S3 Upload Failure: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "Couldn't upload your image to storage before posting. "
                        "This is usually an IAM permissions issue on the backend — "
                        "the Lambda role needs s3:PutObject on the uploaded-images "
                        f"bucket. Underlying error: {str(e)[:300]}"
                    ),
                )
        else:
            logger.error("S3 not configured — cannot host uploaded images for social publishing.")
            raise HTTPException(
                status_code=500,
                detail="Image hosting is not configured (S3 bucket not set). Contact support.",
            )

    # URL-encode the path component of saved_public_url before handing to
    # social APIs. Facebook's Graph API rejects URLs that contain literal
    # spaces / parens / unicode in the path with a cryptic
    #   "Missing or invalid image file" (#100) error.
    # LinkedIn, Twitter and Instagram all tolerated the unencoded version
    # (they either fetch through our server-side code or normalise the URL
    # themselves) which is why only Facebook was failing.
    if saved_public_url and isinstance(saved_public_url, str) and saved_public_url.startswith("http"):
        try:
            from urllib.parse import urlparse, urlunparse, quote
            parsed = urlparse(saved_public_url)
            # Re-quote the path only — leave scheme/host/query alone.
            safe_path = quote(parsed.path, safe="/")
            if safe_path != parsed.path:
                saved_public_url = urlunparse((
                    parsed.scheme, parsed.netloc, safe_path,
                    parsed.params, parsed.query, parsed.fragment,
                ))
                logger.info(f"[URL_SAFE] Encoded image URL path for social fetchers")
        except Exception as e:
            logger.warning(f"[URL_SAFE] Could not normalise image URL: {e}")

    # Accept either `twitter` or `x` from clients; canonicalize to twitter.
    if isinstance(targets, dict) and "x" in targets and "twitter" not in targets:
        targets = {**targets, "twitter": targets.get("x") or []}

    # Include own accounts PLUS any admin-owned accounts assigned to this user
    # (team member). Same handling downstream — token lives on the admin row.
    from sqlalchemy import or_ as _or_
    user_accounts = db.query(SocialAccount).filter(
        _or_(
            SocialAccount.user_id == user_id,
            SocialAccount.assigned_to_user_id == user_id,
        )
    ).all()
    account_map = {(a.platform, a.account_id): a for a in user_accounts}
    results: dict[str, list[dict[str, str]]] = {}
    platform_ids = [] # List of tuples: (platform, account_id, native_post_id)

    # Check if content is a JSON mapping
    json_content = None
    if isinstance(content, str) and content.strip().startswith('{'):
        try:
            json_content = json.loads(content)
        except:
            json_content = None

    for platform in ["linkedin", "facebook", "instagram", "twitter", "youtube", "tiktok", "pinterest"]:
        platform_targets = targets.get(platform, [])
        if not platform_targets: continue
        _plat_t0 = time.monotonic()

        # Document posts are LinkedIn-only. Skip other platforms with an
        # informative error rather than crash mid-publish.
        if media_type == "document" and platform != "linkedin":
            results[platform] = [{
                "account_id": t, "status": "skipped",
                "error": "Document posts are only supported on LinkedIn. Facebook, Instagram and X do not accept attached documents."
            } for t in platform_targets]
            continue

        # Determine platform-specific content
        platform_content = content
        if json_content:
            platform_content = json_content.get(platform, json_content.get("default"))
            if not platform_content:
                platform_content = next(iter(json_content.values())) if json_content else content

        results[platform] = []
        for target_id in platform_targets:
            account = account_map.get((platform, target_id))
            if not account: continue
            try:
                token, acc_id = account.token, account.account_id
                if platform == "linkedin":
                    asset_urn = None
                    media_error = None
                    # Route by media_type. Legacy rows (media_type=None) fall
                    # through to the image path for backwards compat.
                    effective_media_type = media_type or ("image" if image_url else None)

                    # -------- Document path (different endpoint + shape) --------
                    # Per LinkedIn Documents API docs, PDF posts go through
                    # /rest/posts with the new "content.media.id" schema —
                    # NOT /v2/ugcPosts with shareMediaCategory=DOCUMENT (that
                    # category is silently rejected on the legacy endpoint).
                    if effective_media_type == "document" and image_url:
                        try:
                            doc_urn, media_error = linkedin_upload_document(token, acc_id, saved_public_url)
                        except Exception as me:
                            logger.exception(f"LinkedIn document upload raised: {me}")
                            media_error = str(me)
                            doc_urn = None

                        if not doc_urn:
                            res = MockResponse(status_code=500, text=f"LinkedIn document upload failed: {media_error}")
                        else:
                            # Document title shown on LinkedIn's carousel
                            # viewer next to the slides. We used to derive
                            # it from the S3 filename (carousel_<uuid>.pdf)
                            # which looked terrible. Better: use the first
                            # non-empty line of the post content as a
                            # human-readable headline. LinkedIn caps the
                            # title at ~100 chars; we trim conservatively
                            # to 80 to leave room for unicode etc.
                            try:
                                _first_line = ""
                                for _ln in (platform_content or "").splitlines():
                                    _ln = _ln.strip()
                                    if _ln:
                                        _first_line = _ln
                                        break
                                # Strip common markdown decorations + emojis
                                # at the start so titles read clean.
                                _first_line = _first_line.lstrip("#*•-> ").strip()
                                doc_title = _first_line[:80] if _first_line else "Carousel"
                            except Exception:
                                doc_title = "Carousel"
                            from services.social_service import LINKEDIN_REST_VERSION
                            post_body = {
                                "author": acc_id,
                                "commentary": platform_content or "",
                                "visibility": "PUBLIC",
                                "distribution": {
                                    "feedDistribution": "MAIN_FEED",
                                    "targetEntities": [],
                                    "thirdPartyDistributionChannels": [],
                                },
                                "content": {
                                    "media": {
                                        "title": doc_title,
                                        "id": doc_urn,
                                    }
                                },
                                "lifecycleState": "PUBLISHED",
                                "isReshareDisabledByAuthor": False,
                            }
                            res = requests.post(
                                "https://api.linkedin.com/rest/posts",
                                headers={
                                    "Authorization": f"Bearer {token}",
                                    "LinkedIn-Version": LINKEDIN_REST_VERSION,
                                    "X-Restli-Protocol-Version": "2.0.0",
                                    "Content-Type": "application/json",
                                },
                                json=post_body,
                                timeout=60,
                            )
                            if res.status_code not in (200, 201):
                                logger.error(f"LI_DOC_POST: failed status={res.status_code} body={res.text[:400]}")
                            else:
                                post_id = res.headers.get("x-restli-id") or res.headers.get("x-linkedin-id") or "ok"
                                logger.info(f"LI_DOC_POST: SUCCESS → {post_id}")
                    else:
                        # -------- Image / Video / Text path (legacy ugcPosts) --------
                        if effective_media_type == "video" and image_url:
                            try:
                                asset_urn, media_error = linkedin_upload_video(token, acc_id, saved_public_url)
                            except Exception as me:
                                logger.exception(f"LinkedIn video upload raised: {me}")
                                media_error = str(me)
                            share_category = "VIDEO" if asset_urn else "NONE"
                        elif image_url:
                            try:
                                asset_urn, media_error = linkedin_upload_image(token, acc_id, saved_public_url)
                                if media_error:
                                    logger.error(f"LinkedIn media upload returned error: {media_error}")
                            except Exception as media_err:
                                logger.exception(f"LinkedIn media upload raised: {media_err}")
                                media_error = str(media_err)
                            share_category = "IMAGE" if asset_urn else "NONE"
                        else:
                            share_category = "NONE"

                        share_content = {"shareCommentary": {"text": platform_content}, "shareMediaCategory": share_category}
                        if asset_urn:
                            share_content["media"] = [{"status": "READY", "media": asset_urn}]
                        res = requests.post("https://api.linkedin.com/v2/ugcPosts", headers={"Authorization": f"Bearer {token}"}, json={
                            "author": acc_id, "lifecycleState": "PUBLISHED", "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
                            "specificContent": {"com.linkedin.ugc.ShareContent": share_content}
                        })
                elif platform == "facebook":
                    # Video branch — Meta fetches file_url itself (Lambda never
                    # handles the bytes), returns a video post id.
                    if media_type == "video" and image_url:
                        fb_id, fb_err = facebook_post_video(acc_id, token, saved_public_url, description=platform_content)
                        if fb_err:
                            res = MockResponse(status_code=500, text=fb_err)
                        else:
                            res = MockResponse(status_code=200, text="ok", json_data={"id": fb_id})
                        # Skip the rest of the FB block (which is image/feed specific).
                        if res.status_code in (200, 201):
                            logger.info(f"[FB_VIDEO] SUCCESS → {fb_id}")
                        platform_posts_raw = res
                        # emulate downstream result handling
                        try:
                            data = res.json()
                            results[platform].append({"account_id": acc_id, "status": "success" if res.status_code in (200, 201) else "failed", "post_id": data.get("id"), "error": None if res.status_code in (200, 201) else res.text})
                            if res.status_code in (200, 201) and data.get("id"):
                                platform_ids.append((platform, acc_id, data.get("id")))
                        except Exception:
                            results[platform].append({"account_id": acc_id, "status": "failed", "error": res.text})
                        continue

                    url = f"https://graph.facebook.com/v19.0/{acc_id}/{'photos' if image_url else 'feed'}"
                    params = {"url": saved_public_url, "message": platform_content, "access_token": token} if image_url else {"message": platform_content, "access_token": token}
                    # Validate: if we have an image URL, make sure it's HTTPS — Meta rejects data: and file: URLs
                    if image_url and saved_public_url and not str(saved_public_url).startswith("http"):
                        logger.error(f"[FB_POST] refusing to call Meta with non-HTTP image URL ({str(saved_public_url)[:60]}...)")
                        res = MockResponse(status_code=400, text=f"Image URL is not publicly hosted: {str(saved_public_url)[:80]}")
                        # do not continue; let it record the Error in results below
                    else:
                        logger.info(f"[FB_POST] → {url} (has_image={bool(image_url)}, acc_id={acc_id}, content_len={len(platform_content or '')})")
                        res = requests.post(url, params=params, timeout=30)
                    if res.status_code not in (200, 201):
                        # Extract meaningful error — Meta sometimes returns HTML error pages
                        # (rate-limit, auth wall, etc.). Try JSON first; else pull <title>.
                        err_body_raw = res.text
                        try:
                            err_json = res.json()
                            err_body = str(err_json.get("error", {}).get("message") or err_json)[:400]
                        except Exception:
                            import re as _re
                            title_match = _re.search(r"<title>([^<]+)</title>", err_body_raw, _re.IGNORECASE)
                            err_body = (
                                f"HTML error page returned (likely rate-limit / auth wall). "
                                f"Title: {title_match.group(1) if title_match else 'unknown'}"
                            )
                        logger.error(f"[FB_POST] FAILED status={res.status_code} body={err_body}")
                        # Common causes:
                        #   #200 — "Requires pages_manage_posts" → config scope missing
                        #   #10  — "Application does not have permission" → token doesn't have the perm
                        #   #100 — invalid param → image_url not reachable by FB
                        if 'pages_manage_posts' in err_body.lower() or '(#200)' in err_body:
                            logger.error("[FB_POST] HINT: pages_manage_posts permission missing from token. Check Meta Login-for-Business Configuration includes this permission.")
                        elif 'url' in err_body.lower() and image_url:
                            logger.error(f"[FB_POST] HINT: Facebook couldn't fetch image at {saved_public_url} — check S3 bucket public-read policy.")
                    else:
                        logger.info(f"[FB_POST] SUCCESS → {res.json().get('id', 'no-id')}")
                elif platform == "twitter":
                    token_secret = account.token_secret

                    # SAFETY: Strict 280 character limit for Twitter Basic/Free accounts
                    if len(platform_content) > 280:
                        logger.info(f"Truncating Twitter content from {len(platform_content)} to 280 chars.")
                        platform_content = platform_content[:277] + "..."

                    # Video branch: use chunked upload (v1.1), then attach
                    # media_id to the tweet as usual. Note: Free-tier limits
                    # are 34 init/day, 170 append/day.
                    upload_err = None
                    media_id = None
                    if media_type == "video" and saved_public_url:
                        # v1.1 chunked upload REQUIRES OAuth 1.0a (token + token_secret).
                        # If the account only has OAuth 2.0, log clearly and
                        # fall through to a text-only tweet instead of silently
                        # making it look like video posted.
                        if not token_secret or token_secret.lower() == "none" or token_secret == "":
                            upload_err = (
                                "Twitter video upload requires OAuth 1.0a (token_secret). "
                                "This account was connected via OAuth 2.0 only. Reconnect the account "
                                "with full Read/Write permissions to enable video tweets."
                            )
                            logger.error(f"[TW_VIDEO] skipped: {upload_err}")
                        else:
                            media_id, upload_err = twitter_upload_video_chunked(saved_public_url, token, token_secret)
                            if upload_err:
                                logger.error(f"[TW_VIDEO] upload failed: {upload_err}")
                    elif saved_public_url:
                        media_id, upload_err = twitter_upload_media_from_url(saved_public_url, token, token_secret)
                    
                    payload = {"text": platform_content}
                    if media_id: payload["media"] = {"media_ids": [media_id]}

                    # Prefer OAuth 1.0a User Context if we have a token_secret (User Context is more reliable for media)
                    if token_secret and token_secret.lower() != "none" and token_secret != "":
                        from requests_oauthlib import OAuth1Session
                        user_oauth = OAuth1Session(
                            TWITTER_API_KEY,
                            client_secret=TWITTER_API_SECRET,
                            resource_owner_key=token,
                            resource_owner_secret=token_secret
                        )
                        res = user_oauth.post("https://api.twitter.com/2/tweets", json=payload)
                        
                        # Fallback for 401 (Unauthorized/Expired) - but NOT 403 (Duplicate/Forbidden)
                        if res.status_code == 401:
                            logger.warning(f"Twitter 1.0a 401, attempting OAuth 2.0 fallback...")
                            if account.refresh_token:
                                token = refresh_twitter_token_v2(account, db) or token
                                headers = {
                                    "Authorization": f"Bearer {token}", 
                                    "Content-Type": "application/json; charset=UTF-8"
                                }
                                res = requests.post("https://api.twitter.com/2/tweets", headers=headers, json=payload)
                    elif account.refresh_token:
                        # Fallback to OAuth 2.0 Bearer if only refresh_token exists
                        token = refresh_twitter_token_v2(account, db) or token
                        headers = {
                            "Authorization": f"Bearer {token}", 
                            "Content-Type": "application/json; charset=UTF-8"
                        }
                        res = requests.post("https://api.twitter.com/2/tweets", headers=headers, json=payload)
                    else:
                        # Direct Bearer (App only - rarely works for tweets)
                        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                        res = requests.post("https://api.twitter.com/2/tweets", headers=headers, json=payload)
                    
                    if res.status_code not in [200, 201]:
                        logger.error(f"Twitter Post Failed: {res.status_code} - {res.text}")
                        error_msg = f"Twitter Error ({res.status_code}): {res.text}"
                        if res.status_code == 403:
                            # Check if it's a duplicate or permission issue
                            res_json = {}
                            try: res_json = res.json()
                            except: pass
                            detail = res_json.get("detail", "Forbidden")
                            
                            if "duplicate" in detail.lower():
                                error_msg = "Twitter Error: Duplicate Post! ⚠️ You cannot post the exact same text twice in a row."
                            else:
                                error_msg = f"Twitter 403 Forbidden: {detail}. Ensure your Twitter App has 'Read and Write' permissions and the text is under 280 chars."
                        
                        res = MockResponse(status_code=res.status_code, text=error_msg, json_data={"detail": error_msg})
                elif platform == "instagram":
                    # Video (Reel) branch — posts a 2-step container to Meta.
                    if media_type == "video" and image_url:
                        ig_id, ig_err = instagram_post_reel(acc_id, token, saved_public_url, caption=platform_content)
                        if ig_err:
                            res = MockResponse(status_code=500, text=ig_err)
                        else:
                            res = MockResponse(status_code=200, text="ok", json_data={"id": ig_id})
                            logger.info(f"[IG_VIDEO] SUCCESS → {ig_id}")
                        try:
                            data = res.json()
                            results[platform].append({"account_id": acc_id, "status": "success" if res.status_code in (200, 201) else "failed", "post_id": data.get("id"), "error": None if res.status_code in (200, 201) else res.text})
                            if res.status_code in (200, 201) and data.get("id"):
                                platform_ids.append((platform, acc_id, data.get("id")))
                        except Exception:
                            results[platform].append({"account_id": acc_id, "status": "failed", "error": res.text})
                        continue

                    if not image_url:
                        logger.error("[IG_POST] FAILED — Instagram requires an image; text-only posts not supported by Meta Graph API.")
                        res = MockResponse(status_code=400, text='Instagram requires an image (Meta API does not support text-only posts)')
                    else:
                        logger.info(f"[IG_POST] creating media container for IG account={acc_id}, image={saved_public_url}")
                        con_res = requests.post(
                            f"https://graph.facebook.com/v19.0/{acc_id}/media",
                            params={"image_url": saved_public_url, "caption": platform_content, "access_token": token},
                            timeout=30,
                        )
                        if con_res.status_code == 200:
                            creation_id = con_res.json().get("id")
                            logger.info(f"[IG_POST] media container created: {creation_id}, polling for FINISHED status...")
                            is_ready = False
                            for _ in range(10):
                                try:
                                    status_res = requests.get(
                                        f"https://graph.facebook.com/v19.0/{creation_id}",
                                        params={"fields": "status_code", "access_token": token},
                                        timeout=15,
                                    )
                                    if status_res.status_code == 200 and status_res.json().get("status_code") == "FINISHED":
                                        is_ready = True
                                        break
                                except requests.exceptions.RequestException as _e:
                                    logger.warning(f"[IG_POST] container status poll failed: {_e}")
                                time.sleep(3)
                            if not is_ready:
                                logger.warning("[IG_POST] container not FINISHED after 30s — publishing anyway")
                                time.sleep(5)
                            logger.info(f"[IG_POST] publishing media container {creation_id}")
                            res = requests.post(
                                f"https://graph.facebook.com/v19.0/{acc_id}/media_publish",
                                params={"creation_id": creation_id, "access_token": token},
                                timeout=30,
                            )
                            if res.status_code not in (200, 201):
                                logger.error(f"[IG_POST] publish step FAILED status={res.status_code} body={res.text[:500]}")
                            else:
                                logger.info(f"[IG_POST] SUCCESS → {res.json().get('id', 'no-id')}")
                        else:
                            # Extract meaningful error, handling both JSON and HTML responses
                            err_raw = con_res.text
                            try:
                                err_json = con_res.json()
                                err_body = str(err_json.get("error", {}).get("message") or err_json)[:400]
                            except Exception:
                                import re as _re
                                title_match = _re.search(r"<title>([^<]+)</title>", err_raw, _re.IGNORECASE)
                                err_body = f"HTML error page. Title: {title_match.group(1) if title_match else 'unknown'}"
                            logger.error(f"[IG_POST] container creation FAILED status={con_res.status_code} body={err_body}")
                            if 'instagram_content_publish' in err_body.lower() or '(#200)' in err_body:
                                logger.error("[IG_POST] HINT: instagram_content_publish permission missing from token. Check Meta Login-for-Business Config includes this permission.")
                            elif 'image_url' in err_body.lower() or 'not accessible' in err_body.lower():
                                logger.error(f"[IG_POST] HINT: Meta couldn't fetch image at {saved_public_url} — check S3 bucket public-read policy (IG requires publicly accessible HTTPS images).")
                            elif not str(saved_public_url).startswith("http"):
                                logger.error(f"[IG_POST] HINT: image URL is not HTTP (got {str(saved_public_url)[:60]}...). IG requires publicly accessible HTTPS URLs.")
                            res = con_res

                elif platform == "youtube":
                    # YouTube doesn't share the per-platform `platform_content`
                    # field with other platforms — it needs an explicit Title
                    # + Description split. The Manual Post / Video Mode UI
                    # enforces YouTube is the ONLY target when selected.
                    from services.youtube_publisher import publish_video as _yt_publish
                    yt_video_url = saved_public_url
                    yt_result = _yt_publish(
                        account=account,
                        db=db,
                        video_url=yt_video_url,
                        title=(youtube_title or "").strip(),
                        description=youtube_description if youtube_description is not None else (platform_content or ""),
                        tags=youtube_tags or [],
                        category_id=youtube_category_id or "22",
                        privacy=(youtube_privacy or "public").lower(),
                    )
                    if yt_result.get("ok"):
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

                elif platform == "tiktok":
                    # TikTok Direct Post — same shape as YouTube above. The
                    # caption is a single text field (no separate title/
                    # description) and the privacy enum is TikTok-specific
                    # (PUBLIC_TO_EVERYONE / MUTUAL_FOLLOW_FRIENDS / etc).
                    # Manual Post UI forces TikTok to be the ONLY selected
                    # target because the caption format doesn't share with
                    # other platforms cleanly.
                    from services.tiktok_publisher import publish_video as _tt_publish
                    tt_result = _tt_publish(
                        account=account,
                        db=db,
                        video_url=saved_public_url,
                        caption=(tiktok_caption if tiktok_caption is not None else platform_content) or "",
                        privacy=(tiktok_privacy or "SELF_ONLY"),
                    )
                    if tt_result.get("ok"):
                        res = MockResponse(
                            status_code=200,
                            text=tt_result.get("url", ""),
                            json_data={"id": tt_result.get("video_id"), "url": tt_result.get("url")},
                        )
                    else:
                        res = MockResponse(
                            status_code=400,
                            text=tt_result.get("error", "TikTok publish failed"),
                            json_data={"detail": tt_result.get("error", "TikTok publish failed")},
                        )

                elif platform == "pinterest":
                    # Pinterest API v5: POST /v5/pins requires a board_id +
                    # image source. acc_id IS the board_id (saved during the
                    # OAuth callback). On 401 we refresh the token once and retry.
                    if not saved_public_url:
                        res = MockResponse(
                            status_code=400,
                            text="Pinterest requires an image",
                            json_data={"detail": "Pinterest requires an image"},
                        )
                    else:
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

                # Capture native ID if successful
                if res.status_code in [200, 201]:
                    try:
                        native_id = None
                        if platform == "linkedin":
                            # LinkedIn returns ID in X-RestLi-Id header or 'id' field
                            if hasattr(res, 'headers'):
                                native_id = res.headers.get("X-RestLi-Id")
                            if not native_id and hasattr(res, 'json') and callable(res.json):
                                try: native_id = res.json().get("id")
                                except: pass

                            # Standardise to urn:li:share: for analytics if it's a ugcPost
                            if native_id and isinstance(native_id, str) and "ugcPost" in native_id:
                                num_id = native_id.split(":")[-1]
                                native_id = f"urn:li:share:{num_id}"
                        elif platform == "youtube":
                            # YouTube branch returned a MockResponse with the
                            # video_id under res.json()["id"] — same path the
                            # elif below would take, but explicit so we're
                            # sure it captures.
                            try: native_id = res.json().get("id")
                            except: pass
                        elif hasattr(res, 'json') and callable(res.json):
                            try:
                                res_json = res.json()
                                if platform == "twitter":
                                    native_id = res_json.get("data", {}).get("id")
                                else:
                                    native_id = res_json.get("id")
                            except: pass
                        
                        if native_id:
                            platform_ids.append((platform, acc_id, str(native_id)))
                    except Exception as e:
                        logger.error(f"Failed to extract native ID for {platform}: {str(e)}")

                results[platform].append({"name": account.name, "status": final_status})
            except Exception as e:
                results[platform].append({"name": account.name, "status": f"Failed ❌: {str(e)}"})
        # End of per-account loop → record wall-clock for this platform.
        _per_platform_times[platform] = time.monotonic() - _plat_t0
        logger.info(f"[PUBLISH_TIMING] platform={platform} dur={_per_platform_times[platform]:.2f}s accounts={len(platform_targets)}")

    valid_platforms = set()
    for platform, res_list in results.items():
        # Bug fix — the Facebook (line ~1493) and Instagram (line ~1631)
        # VIDEO branches write status='success' (lowercase) via their early-
        # return `continue` path, while every other branch writes
        # final_status='Success ✅' via the general path at line ~1828. The
        # previous case-sensitive `startswith("Success")` compared with a
        # capital-S and silently dropped the lowercase entries, so
        # `platforms=","join(valid_platforms)` ended up missing FB and IG
        # for video posts. The Published card then rendered only 2 icons
        # (LinkedIn + Twitter) when actually 4 platforms had succeeded.
        # `.lower().startswith("success")` matches both spellings and
        # keeps the count right without touching the two video branches.
        if any(str(r.get("status", "")).lower().startswith("success") for r in res_list):
            valid_platforms.add(platform)
            
    if valid_platforms:
        # Use simple string for image_url to ensure DB compatibility
        final_image_url = str(saved_public_url) if saved_public_url else None
        post_record = PublishedPost(
            content=content,
            image_url=final_image_url,
            # Persist media_type so the Published page can render the right
            # preview (<video>, PDF chip, or <img>). Legacy rows have this
            # nullable and fall back to image treatment.
            media_type=media_type,
            # Slide-1 PNG URL for carousel posts so the Published feed can
            # render a real <img> preview instead of the ugly "PDF +
            # filename" tile.
            thumbnail_url=thumbnail_url,
            platforms=",".join(valid_platforms),
            user_id=user_id,
            dna_product_id=dna_product_id,
        )
        db.add(post_record)
        db.flush() # Get the post ID
        
        # Save platform-specific IDs
        for p_name, p_acc_id, p_native_id in platform_ids:
            db.add(PublishedPostPlatform(
                published_post_id=post_record.id,
                platform=p_name,
                account_id=p_acc_id,
                native_post_id=p_native_id
            ))
        
        db.commit()

    _total = time.monotonic() - _publish_t0
    per_plat = ", ".join(f"{k}={v:.2f}s" for k, v in _per_platform_times.items())
    logger.info(f"[PUBLISH_TIMING] total dur={_total:.2f}s media_type={media_type!r} per_platform=({per_plat})")
    return results

@router.post("/post")
async def publish_post(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Limit check
    check_quota(db, current_user, "posts")

    body = await request.json()
    content, image_url, targets = body.get("content"), body.get("image_url"), body.get("targets", {})
    media_type = body.get("media_type")  # image | video | document | None

    # YouTube-specific fields (Phase 2 — Manual Post → Video Mode). The UI
    # sends these alongside the standard fields whenever YouTube is the
    # exclusive target. content / caption text stays in `content`; for
    # YouTube we use `youtube_description` (falls back to content) and the
    # new `youtube_title` as the actual video title.
    youtube_title = (body.get("youtube_title") or "").strip()
    youtube_description = body.get("youtube_description")  # may be None → fallback to content
    youtube_tags = body.get("youtube_tags") or []
    if isinstance(youtube_tags, str):
        # Tolerate comma-separated string from the form input
        youtube_tags = [t.strip() for t in youtube_tags.split(",") if t.strip()]
    youtube_category_id = str(body.get("youtube_category_id") or "22")
    youtube_privacy = (body.get("youtube_privacy") or "public").lower()

    # TikTok-specific fields (Phase 2 — Manual Post → TikTok Mode). Caption
    # text falls back to `content` when not provided; privacy is the TikTok
    # enum (PUBLIC_TO_EVERYONE / MUTUAL_FOLLOW_FRIENDS / FOLLOWER_OF_CREATOR /
    # SELF_ONLY). Sandbox apps must use SELF_ONLY OR the test user's privacy
    # settings allow it; we default to SELF_ONLY to avoid surprising the user.
    tiktok_caption = body.get("tiktok_caption")  # may be None → fallback to content
    tiktok_privacy = (body.get("tiktok_privacy") or "SELF_ONLY").strip().upper()

    # YouTube-only sanity checks. We do these checks again inside
    # publish_video, but failing fast here means we don't log a publishing
    # attempt + we surface the error before the quota tick.
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
        # YouTube + TikTok posts can legitimately have an empty caption — the
        # title (YT) or caption falls back to empty (TT). For all other
        # platforms we still need caption text.
        raise HTTPException(status_code=400, detail="Post content is required")

    # Resolve and server-side lock the DNA tag: team members can only post
    # under brands they are assigned to. If they have multiple, honour the
    # composer's pick if it's in their list; otherwise fall back to primary.
    dna_product_id = body.get("dna_product_id")
    if is_team_member(current_user):
        from services.team_service import resolve_member_publish_dna
        dna_product_id = resolve_member_publish_dna(current_user, dna_product_id)

    results = await process_publishing_internal(
        db, current_user.id, content, image_url, targets,
        media_type=media_type, dna_product_id=dna_product_id,
        thumbnail_url=body.get("thumbnail_url"),
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
    # Brand / location filter params — admin-only. Shared resolver
    # narrows visible_ids by the full cascade (company → brand → member →
    # location). See services.team_service.resolve_filtered_visible_ids.
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
    """Fetch all published posts with filtering and sorting support.

    Admin can cascade through brand / member / location filters. Empty =
    full team scope. An admin-only sentinel (`?member_user_ids=<admin_id>`)
    resolves to admin-only rows so the Dashboard's default state shows
    just the admin's own content.
    """
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

    # Brand multi-filter at the post level — matches any of the selected
    # DNA products. Combines with visible_ids narrowing (already factored
    # in by the resolver).
    if dna_product_ids:
        brand_list = [x.strip() for x in dna_product_ids.split(",") if x.strip()]
        if brand_list:
            query = query.filter(PublishedPost.dna_product_id.in_(brand_list))

    # 1. Platform Filtering (accepts alias `x` as `twitter`)
    platform_key = (platform or "all").strip().lower()
    if platform_key == "x":
        platform_key = "twitter"
    if platform_key and platform_key != "all":
        query = query.filter(PublishedPost.platforms.contains(platform_key))
    
    # 2. Server-side Sorting
    if sort == "asc":
        query = query.order_by(PublishedPost.created_at.asc())
    else:
        query = query.order_by(PublishedPost.created_at.desc())
        
    posts = query.all()

    # Bulk-load the SocialAccount rows we'll need to enrich per-platform
    # account info (name + personal-vs-page badge). One query per response
    # instead of N queries per post.
    all_account_ids = {
        (pp.platform, pp.account_id)
        for p in posts for pp in p.platform_posts
        if pp.account_id
    }
    account_map = {}  # (platform, account_id) -> {name, type}
    if all_account_ids:
        platforms_set = {pl for pl, _ in all_account_ids}
        acc_ids_set = {aid for _, aid in all_account_ids}
        rows = db.query(SocialAccount).filter(
            SocialAccount.platform.in_(platforms_set),
            SocialAccount.account_id.in_(acc_ids_set),
        ).all()
        for a in rows:
            account_map[(a.platform, a.account_id)] = {
                "name": a.name or "",
                "type": a.type or "",
                # LinkedIn-specific: derive personal vs page from URN prefix
                # (source of truth is the URN, not the stored `type` string
                # which can be inconsistent across older rows).
                "is_personal": (a.platform == "linkedin"
                                and (a.account_id or "").startswith("urn:li:person:")),
            }

    def _metrics_for(p):
        """One metric row per platform_post — i.e. per (platform × account).
        A post published to LinkedIn:personal + LinkedIn:page + Facebook
        emits 3 rows so the UI can show separate chips (with each account's
        real numbers) rather than silently collapsing to the first
        platform_post's metrics and hiding the second account's stats.
        Rows without a matching SocialAccount fall back to platform-only
        metadata so the card still renders.
        """
        out = []
        seen_plainless = set()  # platforms with no per-account fallback yet
        for pp in p.platform_posts:
            m = pp.metrics_json or {}
            info = account_map.get((pp.platform, pp.account_id)) or {}
            out.append({
                "platform": pp.platform,
                "account_id": pp.account_id,
                "account_name": info.get("name") or "",
                "account_type": info.get("type") or "",
                "is_personal": bool(info.get("is_personal")),
                "likes": int(m.get("likes", 0) or 0),
                "comments": int(m.get("comments", 0) or 0),
                "reach": int(m.get("reach", 0) or 0),
            })
            seen_plainless.add(pp.platform)
        # Legacy safety: if `platforms` claims a platform but no
        # platform_post row exists (extremely rare — happens for older
        # posts imported without instance rows), emit a zeroed chip so
        # the platform icon in the card header still has a matching row.
        for plat in (p.platforms.split(",") if p.platforms else []):
            if plat not in seen_plainless:
                out.append({
                    "platform": plat,
                    "account_id": None,
                    "account_name": "",
                    "account_type": "",
                    "is_personal": False,
                    "likes": 0, "comments": 0, "reach": 0,
                })
        return out

    return [{
        "id": p.id,
        "content": p.content,
        "image_url": p.image_url,
        # Expose media_type so the Published page knows whether to render
        # <img> (image), <video> (video) or a PDF chip (document).
        "media_type": p.media_type,
        # Carousel slide-1 PNG — frontend renders it as an <img> preview
        # instead of the ugly "PDF + filename" tile for document posts.
        "thumbnail_url": p.thumbnail_url,
        "platforms": p.platforms.split(",") if p.platforms else [],
        # YouTube video id (native_post_id of the youtube platform_post) so the
        # UI can build a poster-frame thumbnail — a YT post's image_url is the
        # uploaded video file, which can't render in an <img>.
        "youtube_video_id": next((pp.native_post_id for pp in p.platform_posts if pp.platform == "youtube" and pp.native_post_id), None),
        "created_at": p.created_at.isoformat() + "Z" if p.created_at else None,
        "metrics": _metrics_for(p),
    } for p in posts]


@router.delete("/posts/{post_id}")
async def delete_post(post_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    post = db.query(PublishedPost).filter(PublishedPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Admins can delete posts owned by their team members. Members can only
    # delete their own. get_team_scope_user_ids returns:
    #   - admin → [admin_id, member1_id, member2_id, ...]
    #   - member → [member_id]
    visible_ids = get_team_scope_user_ids(db, current_user)
    if post.user_id not in visible_ids:
        raise HTTPException(status_code=403, detail="You do not have permission to delete this post")

    try:
        # Optional: Delete image from S3 if it exists and is stored in our bucket
        if post.image_url and S3_BUCKET_NAME in post.image_url:
            s3 = get_s3_client()
            if s3:
                # Extract key from URL
                key = post.image_url.split("/")[-1].split("?")[0]
                logger.info(f"Deleting object from S3: {key}")
                s3.delete_object(Bucket=S3_BUCKET_NAME, Key=key)
    except Exception as e:
        logger.error(f"Failed to delete image from S3: {str(e)}")
        # We continue even if S3 delete fails to ensure DB entry is removed
        
    db.delete(post)
    db.commit()
    return {"message": "Post deleted successfully"}

@router.post("/schedule")
async def schedule_post(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    body = await request.json()
    content = body.get("content")
    image_url = body.get("image_url")
    media_type = body.get("media_type")  # image | video | document | None
    # Carousel preview URLs — slide-1 PNG for the grid card + full slide list
    # for the swipeable modal. Same shape drafts already accept. Optional; a
    # scheduled post without a thumbnail falls back to the generic PDF tile.
    thumbnail_url = body.get("thumbnail_url")
    slide_thumbnail_urls = body.get("slide_thumbnail_urls")
    targets = body.get("targets", {})
    scheduled_for_str = body.get("scheduled_for")
    timezone_name = body.get("timezone", "UTC")
    post_type = body.get("post_type", "standard")
    campaign_brief = body.get("campaign_brief")

    if post_type == "standard" and not content:
        raise HTTPException(status_code=400, detail="Content is required for standard posts")
    
    if post_type == "agentic" and not campaign_brief:
        raise HTTPException(status_code=400, detail="Campaign brief is required for agentic posts")

    if not scheduled_for_str:
        raise HTTPException(status_code=400, detail="scheduled_for is required")

    # Limit check
    check_quota(db, current_user, "posts")

    try:
        # standard ISO format handling
        scheduled_for = datetime.fromisoformat(scheduled_for_str.replace('Z', '+00:00'))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")

    # Tag the DNA. For members we resolve against their assigned list
    # (accepts any, falls back to primary); admins pass any of their brands.
    scheduled_dna_id = body.get("dna_product_id")
    if is_team_member(current_user):
        from services.team_service import resolve_member_publish_dna
        scheduled_dna_id = resolve_member_publish_dna(current_user, scheduled_dna_id)

    # YouTube-only fields. Stored on the row so the cron fire path can hand
    # them to videos.insert at publish time. Required only when youtube is
    # one of the scheduled targets — symmetric with /post's validation.
    is_youtube_target = bool((targets or {}).get("youtube"))
    youtube_title = (body.get("youtube_title") or "").strip() if is_youtube_target else None
    youtube_description = body.get("youtube_description") if is_youtube_target else None
    youtube_tags = body.get("youtube_tags") if is_youtube_target else None
    if isinstance(youtube_tags, str):
        youtube_tags = [t.strip() for t in youtube_tags.split(",") if t.strip()]
    youtube_category_id = str(body.get("youtube_category_id") or "22") if is_youtube_target else None
    youtube_privacy = (body.get("youtube_privacy") or "public") if is_youtube_target else None
    if is_youtube_target:
        if not youtube_title:
            raise HTTPException(status_code=400, detail="YouTube title is required when scheduling to YouTube")
        if not image_url:
            raise HTTPException(status_code=400, detail="A video file is required when scheduling to YouTube")

    new_scheduled_post = ScheduledPost(
        content=content if content else "{}",
        image_url=image_url,
        media_type=media_type,
        thumbnail_url=thumbnail_url,
        slide_thumbnail_urls=slide_thumbnail_urls,
        targets=json.dumps(targets),
        scheduled_for=scheduled_for,
        timezone=timezone_name,
        post_type=post_type,
        campaign_brief=campaign_brief,
        user_id=current_user.id,
        dna_product_id=scheduled_dna_id,
        youtube_title=youtube_title,
        youtube_description=youtube_description,
        youtube_tags=youtube_tags,
        youtube_category_id=youtube_category_id,
        youtube_privacy=youtube_privacy,
        status="pending"
    )
    db.add(new_scheduled_post)
    db.commit()
    return {"message": "Post scheduled successfully", "id": new_scheduled_post.id}

# NOTE: /scheduled GET, DELETE, PUT endpoints used to live here too. They
# were duplicates of the canonical versions in routers/publishing.py. Because
# main.py registers content.router BEFORE publishing.router, the content.py
# copies were silently winning route resolution — every poll for awaiting_review
# rows was hitting the OLD code which only filtered status='pending'. That's
# why the strategy/schedule review queue never showed pre-generated cards.
# Removed in favour of the publishing.py copies which support the
# include_awaiting_review flag, the /approve sub-route, and the /regenerate
# sub-route. Do not re-add /scheduled endpoints here.

@router.get("/scheduler/process")
@router.post("/scheduler/process")
async def process_scheduler(request: Request, db: Session = Depends(get_db)):
    """Triggered by AWS EventBridge (via lambda_pinger). Requires SCHEDULER_SECRET header."""
    # Only enforce the shared secret when one is configured, so local dev still works.
    if SCHEDULER_SECRET:
        provided = request.headers.get("X-Scheduler-Secret")
        if not provided or provided != SCHEDULER_SECRET:
            logger.warning("Unauthorized hit to /scheduler/process (bad or missing X-Scheduler-Secret).")
            raise HTTPException(status_code=401, detail="Unauthorized")

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
            
            # 🔄 Autonomous AI Agent Content Generation
            if post.post_type == "agentic" and (not post.content or post.content == "{}" or post.content == ""):
                logger.info(f"🤖 Agentic Trigger: Generating content for Scheduled Post {post.id} - Brief: {post.campaign_brief[:50]}...")
                platforms = list(targets.keys())
                user = db.query(User).filter(User.id == post.user_id).first()
                
                try:
                    # 1. Generate content and visuals from brief
                    # We pass any existing context to get the orchestrator flow
                    gen_data = generate_strategic_content(post.campaign_brief, platforms, user=user)
                    
                    # 2. AI Recommendation Logic
                    # Select the variant recommended by the AI Critic (defaults to viral_reach if not specified)
                    rec = gen_data.get("recommendation", {})
                    recommended_variant = rec.get("best_variant", "viral_reach")
                    
                    # 3. Extract recommended content per platform
                    all_content = gen_data.get("content", {})
                    final_content_map = {}
                    for p in platforms:
                        p_variants = all_content.get(p.lower(), {})
                        # Try to get recommended, fallback to first available
                        final_content_map[p] = p_variants.get(recommended_variant, next(iter(p_variants.values()), ""))
                    
                    post.content = json.dumps(final_content_map)
                    
                    # 4. Visual Selection (Variant 2 as requested)
                    visuals = gen_data.get("visuals", [])
                    if len(visuals) >= 2:
                        post.image_url = visuals[1].get("url") # Use Visual 2 (index 1)
                    elif len(visuals) > 0:
                        post.image_url = visuals[0].get("url") # Fallback to Visual 1
                        
                    logger.info(f"✅ Autonomous generation successful for Post {post.id} (Variant: {recommended_variant})")
                except Exception as gen_err:
                    logger.error(f"❌ Autonomous generation failed for Post {post.id}: {str(gen_err)}")
                    post.status = "failed"
                    post.error_message = f"AI Generation Failed: {str(gen_err)}"
                    continue

            # Call our internal publishing logic
            publish_results = await process_publishing_internal(
                db=db,
                user_id=post.user_id,
                content=post.content,
                image_url=post.image_url,
                targets=targets,
                media_type=post.media_type,
                # Carry the DNA tag from the scheduled row through to the
                # resulting PublishedPost so analytics can filter by brand.
                dna_product_id=post.dna_product_id,
                # Carousel slide-1 preview — without this, a scheduled PDF
                # carousel that fires would land in Published with a NULL
                # thumbnail_url and revert to the generic "PDF" tile.
                thumbnail_url=post.thumbnail_url,
                # YouTube metadata stored at /schedule time. For non-YouTube
                # scheduled rows these are all NULL — process_publishing_internal
                # only reads them when the youtube branch fires.
                youtube_title=post.youtube_title,
                youtube_description=post.youtube_description,
                youtube_tags=post.youtube_tags or [],
                youtube_category_id=post.youtube_category_id or "22",
                youtube_privacy=post.youtube_privacy or "public",
            )
            
            # Check if at least one platform succeeded
            actual_results: dict = publish_results
            success = any(
                any(r.get("status", "").lower().startswith("success") for r in res_list)
                for res_list in actual_results.values()
            )
            
            if success:
                post.status = "published"
            else:
                post.status = "failed"
                post.error_message = json.dumps(publish_results)
            
            processed_results.append({"id": post.id, "status": post.status})
        except Exception as e:
            post.status = "failed"
            post.error_message = str(e)
            processed_results.append({"id": post.id, "status": "failed", "error": str(e)})
            
    db.commit()
    return {"processed": len(pending_posts), "results": processed_results}

# Draft CRUD endpoints are defined in routers/drafts.py (using typed Pydantic schemas).


@router.get("/usage")
async def get_usage(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return the plan owner's current monthly usage vs their plan quota.

    Team members see their admin's quota and team-wide consumption; standalone
    users see their own. Usage is counted across the full team scope so the
    admin's "posts this month" reflects everything their team has published.
    """
    from services.team_service import get_plan_owner, get_team_scope_user_ids
    owner = get_plan_owner(db, current_user)
    scope_ids = get_team_scope_user_ids(db, owner)
    config = get_user_plan_config(owner)
    # Normalise plan name for display (handle legacy 'basic'/'pro' aliases)
    plan_name = (owner.pricing_plan or "free").lower()
    if plan_name == "basic": plan_name = "starter"
    if plan_name == "pro": plan_name = "growth"

    return {
        "plan": plan_name,
        "limits": config["quotas"],
        "features": config["features"],
        "usage": {
            "posts": get_monthly_post_count(db, scope_ids),
            "images": get_monthly_image_count(db, scope_ids),
            "brands": get_brand_count(owner),
            "channels": get_channel_count(db, scope_ids),
        },
        "end_date": owner.subscription_end_date.isoformat() + "Z" if owner.subscription_end_date else None,
    }


# ------------------------------------------------------------------
# Twitter Refinement (Powers the Repost Modal Assistant)
# ------------------------------------------------------------------

@router.post("/refine-for-twitter")
async def handle_refine_for_twitter(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Refine a post specifically for Twitter constraints using Gemini."""
    content = body.get("content")
    if not content:
        raise HTTPException(status_code=400, detail="Content is required.")

    # Build user context so the optimizer knows the brand identity
    from services.ai_service import _build_user_context, refine_for_twitter_agent
    user_context, _, _ = _build_user_context(current_user)

    from fastapi.concurrency import run_in_threadpool
    result = await run_in_threadpool(refine_for_twitter_agent, content, user_context)
    return result


# ------------------------------------------------------------------
# Agent Post v2 — Gallery (powers Canva-style popup image picker)
# ------------------------------------------------------------------

@router.get("/campaigns/gallery")
async def campaigns_gallery(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List the current user's past Agent Post v2 generations (most recent first).

    Used by the frontend to render the gallery popup. Each campaign has up to
    3 backgrounds and 33 composited template thumbnails.
    """
    from models import GeneratedCampaign

    rows = (
        db.query(GeneratedCampaign)
        .filter(GeneratedCampaign.user_id == current_user.id)
        .order_by(GeneratedCampaign.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "items": [
            {
                "id": r.id,
                "campaign_id": r.campaign_id,
                "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
                "headline": r.headline,
                "subheading": r.subheading,
                "cta": r.cta,
                "primary_color": r.primary_color,
                "aspect_ratio": r.aspect_ratio,
                "visuals": r.visuals or [],
            }
            for r in rows
        ],
        "limit": limit,
        "offset": offset,
        "count": len(rows),
    }


@router.get("/campaigns/gallery/{campaign_id}")
async def campaign_detail(
    campaign_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return one gallery campaign by campaign_id. User-scoped — returns 404
    if the campaign doesn't belong to this user (no cross-tenant leakage)."""
    from models import GeneratedCampaign

    r = (
        db.query(GeneratedCampaign)
        .filter(
            GeneratedCampaign.campaign_id == campaign_id,
            GeneratedCampaign.user_id == current_user.id,
        )
        .first()
    )
    if not r:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {
        "id": r.id,
        "campaign_id": r.campaign_id,
        "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        "headline": r.headline,
        "subheading": r.subheading,
        "cta": r.cta,
        "primary_color": r.primary_color,
        "aspect_ratio": r.aspect_ratio,
        "visuals": r.visuals or [],
    }


@router.delete("/campaigns/gallery/{campaign_id}")
async def delete_campaign(
    campaign_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """User-initiated delete — purges the DB row. S3 objects are not deleted
    here (lifecycle rule handles it); this just removes it from the gallery."""
    from models import GeneratedCampaign

    r = (
        db.query(GeneratedCampaign)
        .filter(
            GeneratedCampaign.campaign_id == campaign_id,
            GeneratedCampaign.user_id == current_user.id,
        )
        .first()
    )
    if not r:
        raise HTTPException(status_code=404, detail="Campaign not found")
    db.delete(r)
    db.commit()
    return {"deleted": campaign_id}
