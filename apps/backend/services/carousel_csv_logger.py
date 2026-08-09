"""CSV logger for the Carousel pipeline.

One ROW per carousel. Per-slide data lives in fixed column groups so a
carousel of up to MAX_SLIDES (default 8) renders flat — easy to audit in
Excel.

Lambda-guarded (no writes in prod), thread-locked, BOM-prefixed for Excel.
ASCII-only headers so legacy CSV viewers stay clean.

File: apps/backend/coursel_post_test_results_20260702.csv
"""
from __future__ import annotations

import csv
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("pipelyt.carousel_csv")

_LOCK = threading.Lock()

_CSV_FILENAME = "coursel_post_test_results_20260702.csv"
_CSV_PATH = Path(__file__).resolve().parent.parent / _CSV_FILENAME


def _excel_text(value) -> str:
    """Force Excel to render a cell as plain text by prefixing with a
    leading apostrophe. Stops "1:1" from being parsed as a time value
    and displayed as "1:01". Excel hides the apostrophe on display.
    """
    if value is None:
        return ""
    s = str(value)
    return f"'{s}" if s else ""

# Cap slide columns at MAX_SLIDES. If a carousel has fewer slides the extra
# slots stay blank. If a future user bumps CAROUSEL_SLIDE_COUNT above this,
# extra slides won't get dedicated columns - they'll still render correctly
# in the PDF and the CSV will log the first MAX_SLIDES.
MAX_SLIDES = int(os.getenv("CAROUSEL_CSV_MAX_SLIDES", "8"))


# ─────────────────────────────────────────────────────────────────────────────
# PRICING (env-overridable - update when OpenAI changes prices)
# ─────────────────────────────────────────────────────────────────────────────
# All prices verified June 2026 against OpenAI's published API pricing page
# (https://openai.com/api/pricing). Pass env vars to override if prices change.

# GPT-5 chat completions pricing (per 1K tokens, USD).
#   Input  = $1.25 / 1M
#   Output = $10.00 / 1M (reasoning tokens are billed as output)
# (gpt-5.4 = $2.50/$15, gpt-5.5 = $5/$30 - override env if you swap models.)
GPT5_INPUT_PRICE_PER_1K  = float(os.getenv("CAROUSEL_GPT5_INPUT_PRICE_PER_1K",  "0.00125"))
GPT5_OUTPUT_PRICE_PER_1K = float(os.getenv("CAROUSEL_GPT5_OUTPUT_PRICE_PER_1K", "0.01000"))

# gpt-image-2 pricing per image (USD). gpt-image-2 is token-based ($8/M image
# input, $30/M image output, $5/M text input) — but for per-image budgeting
# we use OpenAI's own per-image estimate from their pricing calculator.
# Default = high quality at 1024x1024 (~$0.211 per image per OpenAI calc).
# If you switch quality or size, override via env:
#   low    1024x1024 ~= $0.014
#   medium 1024x1024 ~= $0.054
#   high   1024x1024 ~= $0.211   <-- default
#   high   1536x1024 ~= $0.290
#   high   1024x1536 ~= $0.290
# Note: gpt-image-1 was $0.167 at high 1024x1024 - don't reuse that number.
GPT_IMAGE_2_PRICE_PER_IMAGE = float(os.getenv("CAROUSEL_GPT_IMAGE_2_PRICE_PER_IMAGE", "0.211"))


def _compute_director_cost_usd(input_tokens: int, output_tokens: int) -> float:
    """gpt-5 cost: input tokens at input price + (output tokens) at output price.
    Reasoning tokens are already included in completion_tokens by the API."""
    cost = (
        (input_tokens  / 1000.0) * GPT5_INPUT_PRICE_PER_1K +
        (output_tokens / 1000.0) * GPT5_OUTPUT_PRICE_PER_1K
    )
    return round(cost, 6)


def _compute_image_cost_usd(slide_count: int, image_model: str = "") -> float:
    """Image cost only applies to gpt-image-* model rows. The HTML pipeline
    renders via headless Chromium for $0 - return 0 for those rows so the
    TOTAL_COST_USD column reflects reality, not the image-pipeline price."""
    m = (image_model or "").lower()
    if "playwright" in m or "chromium" in m or "html" in m:
        return 0.0
    return round(slide_count * GPT_IMAGE_2_PRICE_PER_IMAGE, 6)


def _build_header() -> list[str]:
    shared = [
        "S.NO",
        "BUSINESS_DNA",
        "CAMPAIGN_BRIEF",                  # raw user brief that triggered the request
        "POST_TEXT",                       # the LinkedIn caption Agent 1 read
        "PRIMARY_BRAND_COLOR",
        "ASPECT_RATIO",
        # ── Director (Agent 1) prompts + hyperparameters ─────────────────
        "DIRECTOR_MODEL",
        "DIRECTOR_MAX_TOKENS",
        "DIRECTOR_REASONING_EFFORT",
        "DIRECTOR_TEMPERATURE",
        "DIRECTOR_TOP_P",
        "DIRECTOR_MIN_SLIDES",
        "DIRECTOR_MAX_SLIDES",
        "DIRECTOR_SYSTEM_PROMPT",
        "DIRECTOR_USER_PROMPT",
        "DIRECTOR_OUTPUT_JSON",            # full structured-output JSON
        # ── Output ───────────────────────────────────────────────────────
        "PDF_TITLE",
        "PDF_S3_URL",
        # ── Image renderer (Agent 2) hyperparameters ─────────────────────
        "IMAGE_MODEL",
        "IMAGE_MODEL_QUALITY",
        "IMAGE_MODEL_SIZE",
        "RENDER_CONCURRENCY",
        "FULL_PARALLEL_MODE",
        "SLIDE_COUNT",
        # ── Stage timings ────────────────────────────────────────────────
        "DIRECTOR_TIME_SEC",                 # GPT-5 Director call latency (seconds)
        "RENDER_TIME_SEC",                   # sum of per-slide renders (wall-clock < this due to parallel)
        "STITCH_TIME_SEC",                   # reportlab PDF stitching (seconds)
        "UPLOAD_TIME_SEC",                   # S3 upload of PDF + per-slide PNGs (seconds)
        "TOTAL_TIME_SEC",                    # carousel pipeline wall-clock only (seconds)
        # Upstream Gemini agent stage timings (run BEFORE the carousel
        # pipeline as part of /generate-content). Shows the full
        # per-request profile, not just the carousel portion.
        "REFINE_AGENT_TIME_SEC",             # Gemini brief refinement (seconds)
        "CULTURAL_AGENT_TIME_SEC",           # Gemini festival/cultural calendar lookup (seconds; usually cached)
        "RESEARCH_AGENT_TIME_SEC",           # Gemini grounded research (seconds)
        "CONTENT_AGENT_TIME_SEC",            # Gemini copywriter that produces post text (seconds)
        "END_TO_END_TIME_SEC",               # refine + cultural + research + content + carousel total
        # ── Token usage + cost ───────────────────────────────────────────
        "DIRECTOR_INPUT_TOKENS",
        "DIRECTOR_OUTPUT_TOKENS",            # incl. reasoning tokens (gpt-5 bills these as output)
        "DIRECTOR_REASONING_TOKENS",
        "DIRECTOR_COST_USD",
        "IMAGE_COST_USD",
        "TOTAL_COST_USD",
    ]
    per_slide: list[str] = []
    for i in range(1, MAX_SLIDES + 1):
        per_slide.extend([
            f"SLIDE_{i}_ROLE",
            f"SLIDE_{i}_HEADLINE",
            f"SLIDE_{i}_IMAGE_PROMPT",
            f"SLIDE_{i}_PNG_URL",
            f"SLIDE_{i}_RENDER_TIME_SEC",    # gpt-image-2 latency for this slide (seconds)
        ])
    return shared + per_slide


_HEADER = _build_header()


def _is_local() -> bool:
    return not os.getenv("AWS_LAMBDA_FUNCTION_NAME")


def _next_serial() -> int:
    if not _CSV_PATH.exists():
        return 1
    try:
        with _CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)
            count = sum(1 for _ in reader)
        return count + 1
    except Exception:
        return 1


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def log_carousel_campaign(
    *,
    business_dna_label: str,
    campaign_brief: str,
    post_text: str,
    primary_brand_color: str,
    aspect_ratio: str,
    # Director metadata
    director_model: str,
    director_max_tokens: int,
    director_reasoning_effort: str,
    director_temperature: float,
    director_top_p: float,
    director_min_slides: int,
    director_max_slides: int,
    director_system_prompt: str,
    director_user_prompt: str,
    director_output_json: str,
    director_input_tokens: int,
    director_output_tokens: int,
    director_reasoning_tokens: int,
    # Pipeline outputs
    pdf_title: str,
    pdf_s3_url: str,
    # Image renderer metadata
    image_model: str,
    image_quality: str,
    image_size: str,
    render_concurrency: int,
    full_parallel_mode: bool,
    # Per-slide data: list[dict] with role / headline / image_prompt /
    # png_s3_url / render_time_s
    slides: list[dict],
    # Stage timings
    director_time_s: float,
    render_time_s: float,
    stitch_time_s: float,
    upload_time_s: float,
    total_time_s: float,
    # Upstream Gemini stage timings (refine/cultural/research/content) — all
    # optional, passed from ai_service.py via run_carousel_pipeline.
    refine_time_sec:   float | None = None,
    cultural_time_sec: float | None = None,
    research_time_sec: float | None = None,
    content_time_sec:  float | None = None,
) -> None:
    """Append ONE row per carousel. No-op on Lambda. Never raises."""
    if not _is_local():
        return

    with _LOCK:
        try:
            new_file = not _CSV_PATH.exists()
            with _CSV_PATH.open("a", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                if new_file:
                    writer.writerow(_HEADER)

                serial = _next_serial()

                # Cost math
                director_cost = _compute_director_cost_usd(
                    director_input_tokens, director_output_tokens
                )
                image_cost = _compute_image_cost_usd(len(slides), image_model)
                total_cost = round(director_cost + image_cost, 6)

                # End-to-end time = sum of available upstream stages + carousel pipeline
                upstream_sum = sum(
                    t for t in (refine_time_sec, cultural_time_sec,
                                research_time_sec, content_time_sec)
                    if isinstance(t, (int, float))
                )
                end_to_end = round(float(upstream_sum) + float(total_time_s or 0.0), 2)

                row: list[str] = [
                    _safe_str(serial),
                    _safe_str(business_dna_label),
                    _safe_str(campaign_brief),
                    _safe_str(post_text),
                    _safe_str(primary_brand_color),
                    _excel_text(aspect_ratio),       # "1:1" -> "'1:1" so Excel doesn't autoparse as time
                    # ── Director hyperparameters + prompts + output ─────
                    _safe_str(director_model),
                    _safe_str(director_max_tokens),
                    _safe_str(director_reasoning_effort),
                    _safe_str(director_temperature),
                    _safe_str(director_top_p),
                    _safe_str(director_min_slides),
                    _safe_str(director_max_slides),
                    _safe_str(director_system_prompt),
                    _safe_str(director_user_prompt),
                    _safe_str(director_output_json),
                    # ── Output ──────────────────────────────────────────
                    _safe_str(pdf_title),
                    _safe_str(pdf_s3_url),
                    # ── Image renderer ──────────────────────────────────
                    _safe_str(image_model),
                    _safe_str(image_quality),
                    _excel_text(image_size),         # keep consistent so "1024x1024" stays plain text
                    _safe_str(render_concurrency),
                    _safe_str(full_parallel_mode),
                    _safe_str(len(slides)),
                    # ── Timings ─────────────────────────────────────────
                    _safe_str(director_time_s),
                    _safe_str(render_time_s),
                    _safe_str(stitch_time_s),
                    _safe_str(upload_time_s),
                    _safe_str(total_time_s),
                    _safe_str(refine_time_sec),
                    _safe_str(cultural_time_sec),
                    _safe_str(research_time_sec),
                    _safe_str(content_time_sec),
                    _safe_str(end_to_end),
                    # ── Tokens + cost ───────────────────────────────────
                    _safe_str(director_input_tokens),
                    _safe_str(director_output_tokens),
                    _safe_str(director_reasoning_tokens),
                    _safe_str(director_cost),
                    _safe_str(image_cost),
                    _safe_str(total_cost),
                ]
                for i in range(MAX_SLIDES):
                    if i < len(slides):
                        s = slides[i]
                        row.extend([
                            _safe_str(s.get("role")),
                            _safe_str(s.get("headline")),
                            _safe_str(s.get("image_prompt")),
                            _safe_str(s.get("png_s3_url")),
                            _safe_str(s.get("render_time_s")),
                        ])
                    else:
                        row.extend(["", "", "", "", ""])

                writer.writerow(row)

            logger.info(
                f"[carousel_csv] wrote row #{serial} slides={len(slides)} "
                f"pdf_title={pdf_title!r} cost=${total_cost:.4f}"
            )
        except Exception as exc:
            logger.warning(f"[carousel_csv] write failed (non-fatal): {exc}")
