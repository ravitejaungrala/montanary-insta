"""LinkedIn Carousel pipeline orchestrator.

Two-agent + stitcher flow:

   1. Agent 1 (carousel_director, GPT-5)
        -> decides pdf_title + per-slide {headline, image_prompt}
   2. Agent 2 (gpt-image-2)
        -> renders slide 1 as "anchor" first
        -> renders slides 2..N IN PARALLEL using anchor + logo as
           reference images (style consistency)
   3. carousel_pdf_stitcher
        -> composes N PNGs into a single PDF
   4. S3 upload
        -> returns a public PDF URL the existing LinkedIn document
           publisher can ship verbatim

Env knobs:
    USE_CAROUSEL_PIPELINE        (master switch in ai_service.py)
    CAROUSEL_SLIDE_COUNT         (default 3)
    CAROUSEL_ASPECT              (default 1:1)
    CAROUSEL_QUALITY             (default high)
    CAROUSEL_IMAGE_MODEL         (default gpt-image-2)
    CAROUSEL_RENDER_CONCURRENCY  (default 3 - cap parallel slide renders)

Returns a dict the orchestrator caller can fold into the existing
visual_variants shape so downstream review + publish keep working.
"""
from __future__ import annotations

import base64
import logging
import os
import time
import uuid
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
    wait,
    FIRST_COMPLETED,
)
from io import BytesIO
from typing import Any

from openai import OpenAI

from core.s3_utils import S3_BUCKET_NAME, get_s3_client, get_s3_url
from services.carousel_director import (
    MIN_SLIDES,
    MAX_SLIDES,
    DIRECTOR_SYSTEM_PROMPT,
    build_director_user_prompt,
    run_carousel_director,
    run_carousel_director_streaming,
)
from services.carousel_pdf_stitcher import build_carousel_pdf
from services.cost_ledger import get_current_ledger, _current_ledger
from services.retry_helper import call_with_retry

logger = logging.getLogger("pipelyt.carousel_pipeline")


# ============================================================
# HYPERPARAMETERS (env-overridable)
# ============================================================
IMAGE_MODEL          = os.getenv("CAROUSEL_IMAGE_MODEL", "gpt-image-2")
_ALLOWED_QUALITY     = {"low", "medium", "high", "auto"}
_raw_carousel_q      = os.getenv("CAROUSEL_QUALITY", "high").strip().lower()
IMAGE_QUALITY        = _raw_carousel_q if _raw_carousel_q in _ALLOWED_QUALITY else "high"
# PRIMARY:  gpt-image-2 at IMAGE_QUALITY (default 'high', ~$0.211/slide)
# FALLBACK: gpt-image-2 at IMAGE_FALLBACK_QUALITY (default 'medium',
#           ~$0.053/slide, 75% cheaper). Kicks in when primary retries
#           exhaust on transient errors. Same model, degraded quality.
_raw_carousel_fb_q       = os.getenv("CAROUSEL_FALLBACK_QUALITY", "medium").strip().lower()
IMAGE_FALLBACK_QUALITY   = _raw_carousel_fb_q if _raw_carousel_fb_q in _ALLOWED_QUALITY else "medium"
DEFAULT_ASPECT       = os.getenv("CAROUSEL_ASPECT", "1:1")
# Concurrency cap. Default 6 covers a full service-style carousel (6 slides)
# in a single parallel batch. Bump higher for longer carousels; OpenAI's SDK
# auto-retries 429s, so being generous is safe.
RENDER_CONCURRENCY   = int(os.getenv("CAROUSEL_RENDER_CONCURRENCY", "6"))
# FULL PARALLEL mode: render ALL slides concurrently (no anchor-first
# dependency). Trades a small amount of cross-slide style consistency for
# ~50% wall-clock reduction. Style consistency is preserved via the
# Director's per-slide prompt language (it embeds a shared "style bible"
# sentence in every slide's image_prompt), and via the brand logo being
# attached as a reference to every slide. Default ON since the carousel
# samples are typography-driven where anchor reference matters less.
# Set CAROUSEL_FULL_PARALLEL=false to revert to anchor-first behavior.
FULL_PARALLEL_MODE   = os.getenv("CAROUSEL_FULL_PARALLEL", "true").lower() not in ("0", "false", "no", "off")

# ── HEDGED REQUESTS (outlier killer) ──────────────────────────────
# gpt-image-2 at quality=high has wide latency variance — p50 ~195s,
# but the long tail goes to 280-330s+ on unlucky requests. With 6
# parallel slides, the slowest single render dictates wall-clock:
# 5 slides at 175s + 1 slide at 331s = 331s carousel (run #10 in
# outside_companies_carousel_post.csv).
#
# Strategy: when a slide has been pending much longer than its
# already-finished siblings, fire a SECOND request for the same prompt
# and use whichever returns first. Costs $0.211 extra per hedged slide
# but caps the worst case at roughly p75 of the population.
#
# Triggers ONLY when BOTH:
#   1. Absolute elapsed > HEDGE_FLOOR_SEC (don't hedge healthy renders)
#   2. Elapsed > median(completed_siblings) * HEDGE_FACTOR
#
# Threshold = max of the two. So we wait until both conditions agree
# the slide is anomalously slow before paying for a duplicate.
HEDGE_ENABLED      = os.getenv("CAROUSEL_HEDGE_ENABLED", "true").lower() not in ("0", "false", "no", "off")
# Defaults tightened after observing 282s outliers slipping under the old
# 1.4×median / 210s floor. New settings catch anything ≥25% slower than
# median once it's crossed 180s — the borderline outlier band.
HEDGE_FLOOR_SEC    = float(os.getenv("CAROUSEL_HEDGE_FLOOR_SEC", "180"))
HEDGE_FACTOR       = float(os.getenv("CAROUSEL_HEDGE_FACTOR", "1.25"))
HEDGE_MIN_SAMPLES  = int(os.getenv("CAROUSEL_HEDGE_MIN_SAMPLES", "2"))

# ── STREAMING DIRECTOR (Method 1 from research doc) ──────────────
# When ON: GPT-5 director streams its JSON output, and we fire each
# slide's image render the moment its image_prompt field arrives —
# overlapping director time with rendering.
#
# Wall-clock for a 6-slide carousel:
#   OFF (sequential): director ~100s + renders ~175s = ~275s
#   ON  (overlapped): max(director, last_render_start + render_time) = ~215s
#
# Same model, prompt, reasoning_effort, quality. ~60s saved end-to-end.
# Set CAROUSEL_DIRECTOR_STREAMING=false to revert to the sequential path
# (e.g., to A/B compare or fall back if the new path misbehaves).
DIRECTOR_STREAMING = os.getenv("CAROUSEL_DIRECTOR_STREAMING", "false").lower() not in ("0", "false", "no", "off")

# gpt-image-2 size map - matches services/magic_image_pipeline.py
ASPECT_TO_SIZE = {
    "1:1":  "1024x1024",
    "4:5":  "1024x1280",
    "16:9": "1792x1024",
    "9:16": "1024x1792",
}
DEFAULT_SIZE = "1024x1024"


_client_singleton: OpenAI | None = None


def _client() -> OpenAI:
    global _client_singleton
    if _client_singleton is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not set - cannot run Carousel image pipeline"
            )
        _client_singleton = OpenAI(api_key=api_key)
    return _client_singleton


# ============================================================
# Logo prep (same pattern as magic_image_pipeline)
# ============================================================
def _logo_uploadable(logo_bytes: bytes) -> tuple[str, BytesIO, str]:
    """Re-encode any input logo to PNG so OpenAI's edit endpoint accepts it.

    Raw BytesIO arrives as application/octet-stream which the API rejects.
    """
    from PIL import Image as PILImage

    img = PILImage.open(BytesIO(logo_bytes))
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGBA")
    out = BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return ("logo.png", out, "image/png")


def _png_uploadable(png_bytes: bytes, name: str) -> tuple[str, BytesIO, str]:
    return (name, BytesIO(png_bytes), "image/png")


# ============================================================
# Single-slide render
# ============================================================
def _render_slide(
    *,
    slide_no: int,
    image_prompt: str,
    logo_bytes: bytes | None,
    anchor_png: bytes | None,
    size: str,
    product_images: list[bytes] | None = None,  # user-uploaded product photos
) -> tuple[int, bytes, float]:
    """Render one slide via gpt-image-2. Returns (slide_no, png_bytes, elapsed_s).

    If logo_bytes provided, sends it as reference image.
    If anchor_png provided (slides 2..N), also sends it so the model
    locks style + palette to slide 1.
    If product_images provided, sends each as an additional reference so
    the model features the actual product in slides instead of generic
    stock-style imagery. Capped at 4 product images to stay under
    gpt-image-2's 16-image limit and to keep per-slide render time sane.
    """
    client = _client()

    references: list[tuple[str, BytesIO, str]] = []
    if logo_bytes:
        references.append(_logo_uploadable(logo_bytes))
    if anchor_png:
        references.append(_png_uploadable(anchor_png, "anchor.png"))
    if product_images:
        for i, pbytes in enumerate(product_images[:4]):
            if pbytes:
                references.append(_png_uploadable(pbytes, f"product_{i+1}.png"))

    t0 = time.monotonic()
    # Retry + quality-fallback strategy for gpt-image-2 slide renders:
    #   1. Try PRIMARY quality (IMAGE_QUALITY, default 'high') with 3
    #      retries + exponential backoff. Transient errors (rate limit,
    #      Cloudflare 502, 5xx, timeout) get retried; non-transient fails
    #      fast.
    #   2. On exhaustion, fall back to FALLBACK quality (default 'medium')
    #      with its own 3-retry policy. Same model (gpt-image-2), degraded
    #      quality but ~75% cheaper and 5-6x faster to render.
    #   3. If both qualities fail → propagate. The hedge system in
    #      _render_slides_with_hedging catches this at the slide level.
    def _do_render(quality: str):
        if references:
            return call_with_retry(
                lambda: client.images.edit(
                    model=IMAGE_MODEL,
                    image=references,
                    prompt=image_prompt,
                    quality=quality,
                    size=size,
                ),
                label=f"OpenAI/CarouselSlide/{slide_no}/edit/{quality}",
            )
        else:
            return call_with_retry(
                lambda: client.images.generate(
                    model=IMAGE_MODEL,
                    prompt=image_prompt,
                    quality=quality,
                    size=size,
                ),
                label=f"OpenAI/CarouselSlide/{slide_no}/generate/{quality}",
            )

    _quality_used = IMAGE_QUALITY
    try:
        resp = _do_render(IMAGE_QUALITY)
    except Exception as _primary_err:
        if IMAGE_FALLBACK_QUALITY and IMAGE_FALLBACK_QUALITY != IMAGE_QUALITY:
            logger.warning(
                f"[carousel] slide {slide_no} primary quality={IMAGE_QUALITY!r} "
                f"exhausted retries — falling back to quality={IMAGE_FALLBACK_QUALITY!r}. "
                f"Last error: {_primary_err}"
            )
            try:
                resp = _do_render(IMAGE_FALLBACK_QUALITY)
                _quality_used = IMAGE_FALLBACK_QUALITY
                logger.info(
                    f"[carousel] slide {slide_no} fallback quality="
                    f"{IMAGE_FALLBACK_QUALITY!r} succeeded after primary failed"
                )
            except Exception as _fallback_err:
                logger.error(
                    f"[carousel] slide {slide_no} BOTH qualities failed. "
                    f"primary={_primary_err} fallback={_fallback_err}"
                )
                raise
        else:
            raise
    elapsed = round(time.monotonic() - t0, 2)
    png_bytes = base64.b64decode(resp.data[0].b64_json)

    # Record this slide render in the per-request cost ledger. Uses the
    # IMAGE_GENERATOR slot (same as Agent 2 in the magic pipeline). The
    # ContextVar is propagated into worker threads by _render_slides_with_hedging.
    # `_quality_used` = whichever quality tier ACTUALLY served the response.
    try:
        _ledger = get_current_ledger()
        if _ledger is not None:
            _text_in = 0
            _img_in = 0
            try:
                _u = getattr(resp, "usage", None)
                if _u:
                    _text_in = int(getattr(_u, "input_tokens", 0) or 0)
                    _det = getattr(_u, "input_tokens_details", None)
                    if _det:
                        _img_in = int(getattr(_det, "image_tokens", 0) or 0)
            except Exception:
                pass
            _ledger.record_openai_image(
                model=IMAGE_MODEL,
                quality=str(_quality_used).lower(),
                images=1,
                text_input_tokens=_text_in,
                image_input_tokens=_img_in,
                agent_slot="IMAGE_GENERATOR",
                time_sec=elapsed,
            )
    except Exception as _le:
        logger.warning(f"[cost_ledger] failed to record carousel slide {slide_no}: {_le}")

    logger.info(
        f"[carousel] slide {slide_no} rendered {len(png_bytes):,} bytes in {elapsed}s "
        f"model={IMAGE_MODEL} quality={_quality_used} size={size} "
        f"refs={len(references)}"
    )
    return slide_no, png_bytes, elapsed


# ============================================================
# Helper: propagate the per-request CostLedger contextvar into a worker
# thread. ContextVars don't cross into ThreadPoolExecutor workers by
# default, so instrumented calls (Agent 2 / gpt-image-2) inside worker
# threads would see get_current_ledger()==None and record no cost.
# Capture the parent ledger BEFORE submitting; each worker sets the same
# ledger on its own contextvar before running the actual render.
def _run_with_ledger(parent_ledger, fn, *args, **kwargs):
    """Set the parent-thread ledger on the worker's contextvar, then run."""
    if parent_ledger is not None:
        _current_ledger.set(parent_ledger)
    return fn(*args, **kwargs)


# ============================================================
# Hedged parallel render — fires a duplicate request when a slide is
# anomalously slow compared to its siblings, then uses whichever
# response (original or hedge) returns first. See HEDGE_* config above.
# ============================================================
def _render_slides_with_hedging(
    slides: list[dict],
    *,
    logo_bytes: bytes | None,
    size: str,
    workers: int,
    product_images: list[bytes] | None = None,
) -> None:
    """In-place: writes _png_bytes + _render_time_s + _hedged onto each
    slide dict. Caller passes the full slides list; this submits them
    all in parallel and watches for outliers.
    """
    # Pool needs 2x capacity to hold both primary + hedge requests when
    # an outlier triggers. Hedges are rare so the extra threads sit idle
    # most of the time.
    pool = ThreadPoolExecutor(max_workers=max(workers * 2, len(slides) * 2))
    # Capture the parent-thread cost ledger so each worker can re-attach
    # it (see _run_with_ledger above). Without this, per-slide gpt-image-2
    # calls inside worker threads silently no-op the ledger.
    _parent_ledger = get_current_ledger()
    try:
        primary_futs: dict[int, Any] = {}   # slide_no -> Future
        hedge_futs: dict[int, Any] = {}     # slide_no -> Future
        submit_t:   dict[int, float] = {}
        completed_times: list[float] = []
        results: dict[int, tuple[bytes, float, bool]] = {}
        by_no = {s["slide_no"]: s for s in slides}

        from datetime import datetime as _dt, timezone as _tz
        for s in slides:
            submit_t[s["slide_no"]] = time.monotonic()
            logger.info(
                f"[TRACE] carousel slide {s['slide_no']} ({s.get('role','?')}) "
                f"submit ts={_dt.now(_tz.utc).isoformat()}"
            )
            primary_futs[s["slide_no"]] = pool.submit(
                _run_with_ledger,
                _parent_ledger,
                _render_slide,
                slide_no=s["slide_no"],
                image_prompt=s["image_prompt"],
                logo_bytes=logo_bytes,
                anchor_png=None,
                size=size,
                product_images=product_images,
            )

        pending = set(s["slide_no"] for s in slides)
        while pending:
            # Watch every in-flight future (primary + any hedges) for
            # the next completion. Poll every 5s so we can also re-
            # evaluate hedge thresholds as siblings complete.
            watch = []
            for n in pending:
                if n in primary_futs and not primary_futs[n].done():
                    watch.append(primary_futs[n])
                if n in hedge_futs and not hedge_futs[n].done():
                    watch.append(hedge_futs[n])
            if watch:
                wait(watch, timeout=5, return_when=FIRST_COMPLETED)

            # Settle whatever completed during the wait
            for n in list(pending):
                for futs_dict, hedged_flag in ((primary_futs, False), (hedge_futs, True)):
                    fut = futs_dict.get(n)
                    if fut is None or not fut.done():
                        continue
                    try:
                        _, png, elapsed = fut.result()
                    except Exception as exc:  # one variant failed — let the other finish
                        logger.warning(
                            f"[hedge] slide {n} ({'hedge' if hedged_flag else 'primary'}) "
                            f"failed: {exc}"
                        )
                        continue
                    if n in results:
                        continue  # the other variant already won
                    results[n] = (png, elapsed, hedged_flag)
                    completed_times.append(elapsed)
                    pending.discard(n)
                    # Cancel the loser. Cancellation only works if the
                    # task hasn't started executing — for in-flight ones,
                    # the result is just discarded. OpenAI still bills us
                    # for any started request.
                    other = hedge_futs if not hedged_flag else primary_futs
                    if n in other:
                        other[n].cancel()
                    if hedged_flag:
                        logger.warning(
                            f"[hedge] slide {n} HEDGE WIN in {elapsed:.1f}s "
                            f"(original was still pending — saved time, cost $0.211 extra)"
                        )

            if not pending:
                break

            # Decide if we should fire hedges for anomalously slow slides.
            # Requires at least HEDGE_MIN_SAMPLES completed siblings so
            # the median is meaningful.
            if HEDGE_ENABLED and len(completed_times) >= HEDGE_MIN_SAMPLES:
                ct_sorted = sorted(completed_times)
                median = ct_sorted[len(ct_sorted) // 2]
                threshold = max(HEDGE_FLOOR_SEC, median * HEDGE_FACTOR)
                now = time.monotonic()
                for n in pending:
                    if n in hedge_futs:
                        continue
                    elapsed_so_far = now - submit_t[n]
                    if elapsed_so_far > threshold:
                        logger.warning(
                            f"[hedge] slide {n} pending {elapsed_so_far:.1f}s "
                            f"(threshold={threshold:.1f}s, median={median:.1f}s, "
                            f"floor={HEDGE_FLOOR_SEC}s, factor={HEDGE_FACTOR}). "
                            f"Firing duplicate request."
                        )
                        hedge_futs[n] = pool.submit(
                            _run_with_ledger,
                            _parent_ledger,
                            _render_slide,
                            slide_no=n,
                            image_prompt=by_no[n]["image_prompt"],
                            logo_bytes=logo_bytes,
                            anchor_png=None,
                            product_images=product_images,
                            size=size,
                        )

        # Stamp results onto slide dicts
        for s in slides:
            png, elapsed, hedged = results[s["slide_no"]]
            s["_png_bytes"]     = png
            s["_render_time_s"] = elapsed
            s["_hedged"]        = hedged
            logger.info(
                f"[TRACE] carousel slide {s['slide_no']} done "
                f"ts={_dt.now(_tz.utc).isoformat()} dur={elapsed}s "
                f"hedged={hedged}"
            )
    finally:
        pool.shutdown(wait=False)


# ============================================================
# Streaming director + early render fire (Method 1)
#
# Submits each slide's image render to a shared thread pool the
# instant the streaming director emits that slide's image_prompt.
# Returns (slides_list, pool, futures_by_slide, render_t0) so the
# caller can wait + collect results when the director finishes.
# ============================================================
def _make_streaming_render_orchestrator(
    *,
    logo_bytes: bytes | None,
    size: str,
    workers: int,
    product_images: list[bytes] | None = None,
):
    """Returns (on_slide_ready, slides_seen, futures, pool, render_t0).

    on_slide_ready(slide_dict) submits a render to the pool the moment
    the streaming director hands us a fully-parsed slide object.
    Captures render_t0 the first time a slide is fired (the real start
    of rendering, which is BEFORE the director finishes)."""
    from datetime import datetime as _dt, timezone as _tz

    # 2× capacity so hedge requests fit if we add them later.
    pool = ThreadPoolExecutor(max_workers=max(workers * 2, 12))
    futures: dict[int, Any] = {}      # slide_no -> Future
    slides_seen: list[dict] = []      # ordered list of slide dicts as fired
    start_marker = {"t0": None}
    # Snapshot the request-thread cost ledger so streaming workers can
    # re-attach it (see _run_with_ledger). Captured at closure setup
    # time — same ledger for every slide fire in this request.
    _parent_ledger = get_current_ledger()

    def on_slide_ready(slide: dict):
        slide_no = slide.get("slide_no")
        if slide_no is None or slide_no in futures:
            return
        if start_marker["t0"] is None:
            start_marker["t0"] = time.monotonic()
        slides_seen.append(slide)
        logger.info(
            f"[carousel] streaming: fire slide {slide_no} ({slide.get('role','?')}) "
            f"render at t+{time.monotonic() - start_marker['t0']:.1f}s "
            f"ts={_dt.now(_tz.utc).isoformat()}"
        )
        futures[slide_no] = pool.submit(
            _run_with_ledger,
            _parent_ledger,
            _render_slide,
            slide_no=slide_no,
            image_prompt=slide.get("image_prompt", ""),
            logo_bytes=logo_bytes,
            anchor_png=None,
            size=size,
            product_images=product_images,
        )

    return on_slide_ready, slides_seen, futures, pool, start_marker


# ============================================================
# S3 upload (PDF + per-slide PNGs for the review UI thumbnails)
# ============================================================
def _upload_pdf_to_s3(pdf_bytes: bytes) -> str:
    s3 = get_s3_client()
    if not s3 or not S3_BUCKET_NAME:
        raise RuntimeError("S3 not configured")
    key = f"ai_gen/carousel/carousel_{uuid.uuid4().hex}.pdf"
    s3.upload_fileobj(
        BytesIO(pdf_bytes),
        S3_BUCKET_NAME,
        key,
        ExtraArgs={"ContentType": "application/pdf"},
    )
    return get_s3_url(key)


def _upload_slide_png_to_s3(png_bytes: bytes, slide_no: int) -> str:
    s3 = get_s3_client()
    if not s3 or not S3_BUCKET_NAME:
        raise RuntimeError("S3 not configured")
    key = f"ai_gen/carousel/slide_{slide_no}_{uuid.uuid4().hex}.png"
    s3.upload_fileobj(
        BytesIO(png_bytes),
        S3_BUCKET_NAME,
        key,
        ExtraArgs={"ContentType": "image/png"},
    )
    return get_s3_url(key)


# ============================================================
# ORCHESTRATOR
# ============================================================
def run_carousel_pipeline(
    *,
    post_text: str,
    brand_name: str,
    brand_color: str,
    logo_bytes: bytes | None,
    aspect_ratio: str | None = None,
    slide_count: int | None = None,
    business_dna_label: str = "",
    business_category: str = "",               # 'saas_product' | 'software_service' | 'physical_product' | 'hardware_service' | ''
    campaign_brief: str = "",                  # raw user brief that triggered the request
    upstream_stage_times: dict | None = None,  # {refine, cultural, research, content} seconds
    product_image_urls: list[str] | None = None,  # optional user-uploaded product photos
    image_style: str | None = None,            # user-selected visual style; None/"auto" = current behaviour
) -> dict[str, Any]:
    """Run the full carousel pipeline. Returns:

        {
          "pdf_url":        str,            # final S3 PDF URL (ready for LinkedIn)
          "pdf_title":      str,
          "slide_count":    int,
          "aspect_ratio":   str,
          "slides": [
            {
              "slide_no":     int,
              "role":         str,
              "headline":     str,
              "image_prompt": str,
              "png_s3_url":   str,         # per-slide thumbnail for review UI
              "render_time_s": float,
            },
            ...
          ],
          "director_time_s":  float,
          "render_time_s":    float,        # sum of per-slide render times
          "stitch_time_s":    float,
          "upload_time_s":    float,
          "total_time_s":     float,
        }
    """
    pipeline_t0 = time.monotonic()

    aspect = (aspect_ratio or DEFAULT_ASPECT).strip()
    size   = ASPECT_TO_SIZE.get(aspect, DEFAULT_SIZE)

    # Fetch user-uploaded product reference images ONCE up front so each
    # parallel slide-render uses the cached bytes (avoid 6 redundant S3
    # GETs). Capped at 4 to keep per-slide render time sane and well
    # under gpt-image-2's 16-image hard limit.
    product_image_bytes: list[bytes] = []
    if product_image_urls:
        import requests as _requests
        for url in (product_image_urls or [])[:4]:
            try:
                r = _requests.get(url, timeout=30)
                if r.status_code == 200 and r.content:
                    product_image_bytes.append(r.content)
                    logger.info(
                        f"[carousel] product ref loaded: {url[:80]} "
                        f"({len(r.content):,} bytes)"
                    )
                else:
                    logger.warning(
                        f"[carousel] product ref fetch failed: {url[:80]} "
                        f"status={r.status_code}"
                    )
            except Exception as exc:
                logger.warning(f"[carousel] product ref fetch error {url[:80]}: {exc}")
    if product_image_bytes:
        logger.info(
            f"[carousel] using {len(product_image_bytes)} product reference image(s) "
            f"in every slide render"
        )

    # slide_count is now optional. If the caller passes a fixed N (legacy
    # path), the director respects it as a hard override. Otherwise the
    # director picks a count inside [MIN_SLIDES..MAX_SLIDES] based on how
    # many distinct ideas POST_TEXT actually contains.
    if slide_count is not None:
        logger.info(
            f"[carousel] starting pipeline brand={brand_name!r} "
            f"slides=forced={slide_count} aspect={aspect} size={size} quality={IMAGE_QUALITY}"
        )
    else:
        logger.info(
            f"[carousel] starting pipeline brand={brand_name!r} "
            f"slides=director-decides [{MIN_SLIDES}..{MAX_SLIDES}] "
            f"aspect={aspect} size={size} quality={IMAGE_QUALITY}"
        )

    # 1. Agent 1 -- Director
    # Streaming path (default ON): director's JSON streams in token by
    # token; we fire each slide's image render the moment its
    # image_prompt is parsed, overlapping ~60s of director time with
    # rendering. Disable via CAROUSEL_DIRECTOR_STREAMING=false to fall
    # back to the sequential (block-until-director-done) path.
    workers_guess = max(1, min(RENDER_CONCURRENCY, max(MAX_SLIDES, 6)))

    # NO-LOGO MODE: without a real logo attached OR without a business
    # category set, gpt-image-2 hallucinates a random logo on every slide.
    # Compute once + thread through both streaming and non-streaming director
    # paths so per-slide image_prompts get "no logo, no brand mark" instead
    # of the default "logo top-left" hard rule.
    # Relaxed — fires ONLY when logo_bytes is empty. Business_category has
    # no bearing on whether a logo can be rendered (it only picks the
    # industry playbook downstream). A user with a valid DNA logo but no
    # explicit category was losing their brand mark on every slide.
    _no_logo = (not logo_bytes)
    if _no_logo:
        logger.info(
            f"[carousel] NO-LOGO MODE for this deck "
            f"(logo_present=False, category={business_category!r}) "
            f"— director will emit no-logo instructions to every slide"
        )

    if DIRECTOR_STREAMING and FULL_PARALLEL_MODE:
        logger.info(
            f"[carousel] STREAMING DIRECTOR mode: renders fire as slides stream in "
            f"(workers={workers_guess}, hedge=off-during-stream)"
        )
        on_slide_ready, slides_seen, stream_futures, stream_pool, stream_start = (
            _make_streaming_render_orchestrator(
                logo_bytes=logo_bytes,
                size=size,
                workers=workers_guess,
                product_images=product_image_bytes or None,
            )
        )
        try:
            director_out = run_carousel_director_streaming(
                post_text=post_text,
                brand_name=brand_name,
                brand_color=brand_color,
                aspect_ratio=aspect,
                slide_count=slide_count,
                on_slide_ready=on_slide_ready,
                business_category=business_category,
                no_logo=_no_logo,
                image_style=image_style,
            )
        except Exception as exc:
            # Streaming path failed (parser error, network drop, etc).
            # Cancel any pending renders + cleanly fall back.
            logger.warning(
                f"[carousel] streaming director failed: {exc} — "
                f"cancelling {len(stream_futures)} pending renders and "
                f"falling back to sequential director"
            )
            for f in stream_futures.values():
                f.cancel()
            stream_pool.shutdown(wait=False)
            director_out = run_carousel_director(
                post_text=post_text,
                brand_name=brand_name,
                brand_color=brand_color,
                aspect_ratio=aspect,
                slide_count=slide_count,
                business_category=business_category,
                no_logo=_no_logo,
                image_style=image_style,
            )
            stream_futures = None  # signal fallback below
    else:
        director_out = run_carousel_director(
            post_text=post_text,
            brand_name=brand_name,
            brand_color=brand_color,
            aspect_ratio=aspect,
            slide_count=slide_count,
            business_category=business_category,
            no_logo=_no_logo,
        )
        stream_futures = None

    director_time_s     = float(director_out.pop("_director_time_s", 0.0))
    director_out.pop("_director_model", None)
    director_in_tokens  = int(director_out.pop("_director_input_tokens", 0) or 0)
    director_out_tokens = int(director_out.pop("_director_output_tokens", 0) or 0)
    director_reason_tok = int(director_out.pop("_director_reasoning_tokens", 0) or 0)
    pdf_title = director_out.get("pdf_title") or "Untitled carousel"
    slides    = director_out["slides"]

    # 2. Agent 2 -- render slides.
    render_t0 = time.monotonic()
    workers = max(1, min(RENDER_CONCURRENCY, len(slides)))
    from datetime import datetime as _dt, timezone as _tz

    if stream_futures is not None:
        # Streaming path: renders have already been firing in the background.
        # Collect results from the futures we already have, and submit any
        # slides that didn't get an early-fire (shouldn't happen, but
        # defend against ijson edge cases).
        already_fired = set(stream_futures.keys())
        for s in slides:
            if s["slide_no"] not in already_fired:
                logger.warning(
                    f"[carousel] streaming: slide {s['slide_no']} was NOT "
                    f"fired early; submitting now as catch-up"
                )
                stream_futures[s["slide_no"]] = stream_pool.submit(
                    _run_with_ledger,
                    get_current_ledger(),
                    _render_slide,
                    slide_no=s["slide_no"],
                    image_prompt=s.get("image_prompt", ""),
                    logo_bytes=logo_bytes,
                    anchor_png=None,
                    size=size,
                    product_images=product_image_bytes or None,
                )

        # Collect results onto each slide dict.
        for s in slides:
            try:
                _, png, render_time = stream_futures[s["slide_no"]].result()
                s["_png_bytes"]     = png
                s["_render_time_s"] = render_time
                s["_hedged"]        = False
                logger.info(
                    f"[carousel] streaming: slide {s['slide_no']} done "
                    f"dur={render_time}s"
                )
            except Exception as exc:
                logger.error(
                    f"[carousel] streaming: slide {s.get('slide_no')} render "
                    f"failed: {exc}"
                )
                raise
        stream_pool.shutdown(wait=False)
    elif FULL_PARALLEL_MODE:
        logger.info(
            f"[carousel] FULL PARALLEL mode: {len(slides)} slides x "
            f"{workers} workers (CAROUSEL_RENDER_CONCURRENCY={RENDER_CONCURRENCY}, "
            f"hedge={'ON' if HEDGE_ENABLED else 'off'} "
            f"floor={HEDGE_FLOOR_SEC}s factor={HEDGE_FACTOR})"
        )
        # Hedged render — duplicates anomalously-slow slides so the
        # carousel wall-clock isn't dragged by a single unlucky request.
        # See _render_slides_with_hedging() for the policy.
        _render_slides_with_hedging(
            slides,
            logo_bytes=logo_bytes,
            size=size,
            workers=workers,
            product_images=product_image_bytes or None,
        )
    else:
        # Anchor-first mode (legacy). Slide 1 sequential, slides 2..N parallel.
        logger.info(
            f"[carousel] ANCHOR-FIRST mode: slide 1 sequential, "
            f"slides 2..{len(slides)} parallel x {workers-0} workers"
        )
        slide1 = slides[0]
        logger.info(f"[TRACE] carousel anchor (slide 1) BEGIN ts={_dt.now(_tz.utc).isoformat()}")
        _, anchor_png, anchor_time = _render_slide(
            slide_no=1,
            image_prompt=slide1["image_prompt"],
            logo_bytes=logo_bytes,
            anchor_png=None,
            size=size,
            product_images=product_image_bytes or None,
        )
        slide1["_png_bytes"]     = anchor_png
        slide1["_render_time_s"] = anchor_time
        logger.info(f"[TRACE] carousel anchor (slide 1) END   ts={_dt.now(_tz.utc).isoformat()} dur={anchor_time}s")

        if len(slides) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {}
                # Snapshot parent ledger for worker-thread propagation.
                _parent_ledger = get_current_ledger()
                for s in slides[1:]:
                    logger.info(
                        f"[TRACE] carousel slide {s['slide_no']} ({s.get('role','?')}) "
                        f"submit ts={_dt.now(_tz.utc).isoformat()}"
                    )
                    fut = pool.submit(
                        _run_with_ledger,
                        _parent_ledger,
                        _render_slide,
                        slide_no=s["slide_no"],
                        image_prompt=s["image_prompt"],
                        logo_bytes=logo_bytes,
                        anchor_png=anchor_png,
                        size=size,
                        product_images=product_image_bytes or None,
                    )
                    futures[fut] = s
                for fut in as_completed(futures):
                    s = futures[fut]
                    try:
                        _, png, render_time = fut.result()
                        s["_png_bytes"]     = png
                        s["_render_time_s"] = render_time
                        logger.info(
                            f"[TRACE] carousel slide {s['slide_no']} done "
                            f"ts={_dt.now(_tz.utc).isoformat()} dur={render_time}s"
                        )
                    except Exception as exc:
                        logger.error(
                            f"[carousel] slide {s.get('slide_no')} render failed: {exc}"
                        )
                        raise

    render_time_s = round(time.monotonic() - render_t0, 2)

    # 2b. Strip C2PA Content Credentials + EXIF from each slide PNG
    # BEFORE stitching them into the PDF. OpenAI embeds a C2PA manifest
    # in every gpt-image-2 PNG; LinkedIn renders it as a "cr" badge on
    # each carousel page. Re-encoding via PIL drops the C2PA chunk +
    # any EXIF / XMP / ICC profile metadata. Best-effort — failure on
    # one slide just leaves that slide's PNG untouched.
    from services.social_service import _strip_image_metadata as _strip_png
    for s in slides:
        try:
            _orig = len(s["_png_bytes"])
            s["_png_bytes"] = _strip_png(s["_png_bytes"])
            logger.info(
                f"[carousel] slide {s['slide_no']} stripped metadata "
                f"({_orig:,} -> {len(s['_png_bytes']):,} bytes)"
            )
        except Exception as _e:
            logger.warning(f"[carousel] slide {s['slide_no']} metadata strip failed: {_e}")

    # 3. Stitch into PDF
    stitch_t0 = time.monotonic()
    pdf_bytes = build_carousel_pdf(
        [s["_png_bytes"] for s in slides],
        pdf_title=pdf_title,
        aspect_ratio=aspect,
    )
    stitch_time_s = round(time.monotonic() - stitch_t0, 2)

    # 4. Upload PDF + per-slide thumbnails
    upload_t0 = time.monotonic()
    pdf_url = _upload_pdf_to_s3(pdf_bytes)
    for s in slides:
        s["png_s3_url"]     = _upload_slide_png_to_s3(s["_png_bytes"], s["slide_no"])
        s["render_time_s"]  = s.pop("_render_time_s", 0.0)
        s.pop("_png_bytes", None)
    upload_time_s = round(time.monotonic() - upload_t0, 2)

    total_time_s = round(time.monotonic() - pipeline_t0, 2)
    logger.info(
        f"[carousel] pipeline done in {total_time_s}s "
        f"(director={director_time_s}s, render={render_time_s}s, "
        f"stitch={stitch_time_s}s, upload={upload_time_s}s) "
        f"pdf_url={pdf_url}"
    )

    # CSV row for the whole carousel - local dev only, never raises.
    try:
        import json as _json
        from services.carousel_csv_logger import log_carousel_campaign
        from services.carousel_director import (
            DIRECTOR_MODEL as _D_MODEL,
            DIRECTOR_MAX_TOKENS as _D_MAX_TOK,
            DIRECTOR_REASONING_EFFORT as _D_REASONING,
            DIRECTOR_TEMPERATURE as _D_TEMP,
            DIRECTOR_TOP_P as _D_TOP_P,
            MIN_SLIDES as _D_MIN_SLIDES,
            MAX_SLIDES as _D_MAX_SLIDES,
        )

        _ust = upstream_stage_times or {}
        log_carousel_campaign(
            business_dna_label=business_dna_label or brand_name,
            campaign_brief=campaign_brief,
            post_text=post_text,
            primary_brand_color=brand_color,
            aspect_ratio=aspect,
            # Director hyperparameters + prompts
            director_model=_D_MODEL,
            director_max_tokens=_D_MAX_TOK,
            director_reasoning_effort=_D_REASONING,
            director_temperature=_D_TEMP,
            director_top_p=_D_TOP_P,
            director_min_slides=_D_MIN_SLIDES,
            director_max_slides=_D_MAX_SLIDES,
            director_system_prompt=DIRECTOR_SYSTEM_PROMPT,
            director_user_prompt=build_director_user_prompt(
                post_text=post_text,
                brand_name=brand_name,
                brand_color=brand_color,
                aspect_ratio=aspect,
                slide_count=slide_count,  # None when director picked dynamically
            ),
            director_output_json=_json.dumps(
                {"pdf_title": pdf_title, "slides": [
                    {k: v for k, v in s.items() if not k.startswith("_")}
                    for s in slides
                ]},
                ensure_ascii=False,
            ),
            director_input_tokens=director_in_tokens,
            director_output_tokens=director_out_tokens,
            director_reasoning_tokens=director_reason_tok,
            pdf_title=pdf_title,
            pdf_s3_url=pdf_url,
            # Image renderer hyperparameters
            image_model=IMAGE_MODEL,
            image_quality=IMAGE_QUALITY,
            image_size=size,
            render_concurrency=RENDER_CONCURRENCY,
            full_parallel_mode=FULL_PARALLEL_MODE,
            slides=slides,
            director_time_s=director_time_s,
            render_time_s=render_time_s,
            stitch_time_s=stitch_time_s,
            upload_time_s=upload_time_s,
            total_time_s=total_time_s,
            refine_time_sec=_ust.get("refine"),
            cultural_time_sec=_ust.get("cultural"),
            research_time_sec=_ust.get("research"),
            content_time_sec=_ust.get("content"),
        )
    except Exception as exc:
        logger.warning(f"[carousel] CSV log failed (non-fatal): {exc}")

    return {
        "pdf_url":         pdf_url,
        "pdf_title":       pdf_title,
        "slide_count":     len(slides),
        "aspect_ratio":    aspect,
        "slides":          slides,
        "director_time_s": director_time_s,
        "render_time_s":   render_time_s,
        "stitch_time_s":   stitch_time_s,
        "upload_time_s":   upload_time_s,
        "total_time_s":    total_time_s,
    }
