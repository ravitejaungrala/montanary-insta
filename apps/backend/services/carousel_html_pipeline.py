"""Carousel HTML Pipeline (Variant C).

Same orchestration shape as services/carousel_pipeline.py but uses the
HTML director + Playwright renderer. Goal: 30x faster, ~5x cheaper,
pixel-perfect text, deterministic logo + CTA placement.

Flow:
  1. HTML director (gpt-5) -> structured JSON with deck_design +
     per-slide standalone HTML.
  2. Playwright renderer  -> screenshots each HTML to PNG.
  3. carousel_pdf_stitcher -> PDF from PNGs (reused from image pipeline).
  4. S3 upload (PDF + per-slide PNGs) - reused from image pipeline.
  5. CSV logger - new HTML-pipeline columns.

Output dict matches the existing run_carousel_pipeline contract so the
ai_service.py caller doesn't have to branch on shape:

    {
      "pdf_url":         str,
      "pdf_title":       str,
      "slide_count":     int,
      "aspect_ratio":    str,
      "slides": [...],
      "director_time_s": float,
      "render_time_s":   float,
      "stitch_time_s":   float,
      "upload_time_s":   float,
      "total_time_s":    float,
    }
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any

from core.s3_utils import S3_BUCKET_NAME, get_s3_client, get_s3_url
from services.carousel_html_director import (
    DIRECTOR_MODEL,
    DIRECTOR_MAX_TOKENS,
    DIRECTOR_REASONING_EFFORT,
    DIRECTOR_TEMPERATURE,
    DIRECTOR_TOP_P,
    DIRECTOR_SYSTEM_PROMPT,
    MIN_SLIDES,
    MAX_SLIDES,
    build_director_user_prompt,
    run_carousel_html_director,
)
from services.carousel_html_renderer import render_html_slides_to_pngs
from services.carousel_pdf_stitcher import build_carousel_pdf

logger = logging.getLogger("pipelyt.carousel_html_pipeline")


# ============================================================
# HYPERPARAMETERS
# ============================================================
DEFAULT_ASPECT = os.getenv("CAROUSEL_ASPECT", "1:1")


# ============================================================
# S3 helpers (mirrored from carousel_pipeline.py)
# ============================================================
def _upload_pdf_to_s3(pdf_bytes: bytes) -> str:
    s3 = get_s3_client()
    if not (s3 and S3_BUCKET_NAME):
        raise RuntimeError(
            "S3 is not configured (S3_BUCKET_NAME / AWS credentials missing)"
        )
    key = f"ai_gen/carousel_html/carousel_{uuid.uuid4().hex}.pdf"
    from io import BytesIO
    s3.upload_fileobj(
        BytesIO(pdf_bytes), S3_BUCKET_NAME, key,
        ExtraArgs={"ContentType": "application/pdf"},
    )
    return get_s3_url(key)


def _upload_slide_png_to_s3(png_bytes: bytes, slide_no: int) -> str:
    s3 = get_s3_client()
    if not (s3 and S3_BUCKET_NAME):
        return ""
    key = f"ai_gen/carousel_html/slide_{slide_no}_{uuid.uuid4().hex}.png"
    from io import BytesIO
    s3.upload_fileobj(
        BytesIO(png_bytes), S3_BUCKET_NAME, key,
        ExtraArgs={"ContentType": "image/png"},
    )
    return get_s3_url(key)


# ============================================================
# ORCHESTRATOR
# ============================================================
def run_carousel_html_pipeline(
    *,
    post_text: str,
    brand_name: str,
    brand_color: str,
    logo_bytes: bytes | None,
    aspect_ratio: str | None = None,
    slide_count: int | None = None,
    business_dna_label: str = "",
    business_category: str = "",  # accepted for signature parity with run_carousel_pipeline
    campaign_brief: str = "",
    upstream_stage_times: dict | None = None,
    product_image_urls: list[str] | None = None,  # signature parity — not used in HTML pipeline
    image_style: str | None = None,               # signature parity — not used in HTML pipeline
) -> dict[str, Any]:
    """Run the HTML carousel pipeline end-to-end."""
    pipeline_t0 = time.monotonic()
    aspect = (aspect_ratio or DEFAULT_ASPECT).strip()

    if slide_count is not None:
        logger.info(
            f"[carousel_html] starting pipeline brand={brand_name!r} "
            f"slides=forced={slide_count} aspect={aspect}"
        )
    else:
        logger.info(
            f"[carousel_html] starting pipeline brand={brand_name!r} "
            f"slides=director-decides [{MIN_SLIDES}..{MAX_SLIDES}] "
            f"aspect={aspect}"
        )

    # 1. Director
    director_out = run_carousel_html_director(
        post_text=post_text,
        brand_name=brand_name,
        brand_color=brand_color,
        aspect_ratio=aspect,
        slide_count=slide_count,
    )
    director_time_s     = float(director_out.pop("_director_time_s", 0.0))
    director_out.pop("_director_model", None)
    director_in_tokens  = int(director_out.pop("_director_input_tokens", 0) or 0)
    director_out_tokens = int(director_out.pop("_director_output_tokens", 0) or 0)
    director_reason_tok = int(director_out.pop("_director_reasoning_tokens", 0) or 0)
    pdf_title = director_out.get("pdf_title") or "Untitled carousel"
    slides    = director_out["slides"]
    htmls     = [s["html"] for s in slides]

    # 2. Renderer (Playwright)
    render_t0 = time.monotonic()
    pngs = render_html_slides_to_pngs(
        htmls,
        logo_bytes=logo_bytes,
        aspect_ratio=aspect,
    )
    for s, (png, per_render_s) in zip(slides, pngs):
        s["_png_bytes"]     = png
        s["_render_time_s"] = per_render_s
    render_time_s = round(time.monotonic() - render_t0, 2)

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
        s["png_s3_url"]    = _upload_slide_png_to_s3(s["_png_bytes"], s["slide_no"])
        s["render_time_s"] = s.pop("_render_time_s", 0.0)
        # For CSV / parity with the image pipeline, expose the HTML body
        # under image_prompt so the existing CSV column populates.
        s["image_prompt"]  = s.get("html", "")
        s.pop("_png_bytes", None)
    upload_time_s = round(time.monotonic() - upload_t0, 2)

    total_time_s = round(time.monotonic() - pipeline_t0, 2)
    logger.info(
        f"[carousel_html] pipeline done in {total_time_s}s "
        f"(director={director_time_s}s, render={render_time_s}s, "
        f"stitch={stitch_time_s}s, upload={upload_time_s}s) "
        f"pdf_url={pdf_url}"
    )

    # 5. CSV log (reuse the existing CSV logger - same columns, the
    # image_model column will say "playwright-chromium" so rows are
    # distinguishable from the gpt-image-2 rows).
    try:
        import json as _json
        from services.carousel_csv_logger import log_carousel_campaign

        _ust = upstream_stage_times or {}
        log_carousel_campaign(
            business_dna_label=business_dna_label or brand_name,
            campaign_brief=campaign_brief,
            post_text=post_text,
            primary_brand_color=brand_color,
            aspect_ratio=aspect,
            director_model=DIRECTOR_MODEL,
            director_max_tokens=DIRECTOR_MAX_TOKENS,
            director_reasoning_effort=DIRECTOR_REASONING_EFFORT,
            director_temperature=DIRECTOR_TEMPERATURE,
            director_top_p=DIRECTOR_TOP_P,
            director_min_slides=MIN_SLIDES,
            director_max_slides=MAX_SLIDES,
            director_system_prompt=DIRECTOR_SYSTEM_PROMPT,
            director_user_prompt=build_director_user_prompt(
                post_text=post_text,
                brand_name=brand_name,
                brand_color=brand_color,
                aspect_ratio=aspect,
                slide_count=slide_count,
            ),
            director_output_json=_json.dumps(
                {"pdf_title": pdf_title, "deck_design": director_out.get("deck_design"),
                 "slides": [{k: v for k, v in s.items() if not k.startswith("_")}
                            for s in slides]},
                ensure_ascii=False,
            ),
            director_input_tokens=director_in_tokens,
            director_output_tokens=director_out_tokens,
            director_reasoning_tokens=director_reason_tok,
            pdf_title=pdf_title,
            pdf_s3_url=pdf_url,
            image_model="playwright-chromium",   # distinguishes HTML pipeline rows
            image_quality="html-rendered",
            image_size=f"{1024}x{1024}",         # informational
            render_concurrency=1,                # sequential rendering
            full_parallel_mode=False,
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
        logger.warning(f"[carousel_html] CSV log failed (non-fatal): {exc}")

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
