"""CSV logger for the Magic Image Pipeline (GPT-5 + gpt-image-2).

ONE ROW PER CAMPAIGN. All variants (V1=viral_reach, V2=high_interaction,
V3=follower_growth, V4=festival_variant) live side-by-side in column groups
so you can compare all four variant prompts + images for the same campaign
in a single row.

Sequential S.NO using csv.reader (record-aware count, not physical newlines)
so multi-line cells with embedded newlines don't inflate the counter.

GUARDED: only fires when AWS_LAMBDA_FUNCTION_NAME is NOT set (i.e. local dev
box). Production Lambda is untouched — no file system writes.

File: apps/backend/image_post_test_results_20260702.csv
"""
from __future__ import annotations

import csv
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("pipelyt.magic_image_csv")

_LOCK = threading.Lock()

_CSV_FILENAME = "image_post_test_results_20260702.csv"
_CSV_PATH = Path(__file__).resolve().parent.parent / _CSV_FILENAME


def _excel_text(value) -> str:
    """Wrap a value so Excel treats it as plain text (not auto-formatted).

    Excel aggressively auto-parses cell content that looks like dates, times,
    or numbers. The classic offender for us: "1:1" -> Excel sees a time and
    renders "1:01". Prefixing the cell with a single apostrophe forces Excel
    into text mode for that cell (and Excel hides the apostrophe on display).
    Other CSV consumers see a literal "'1:1" string — minor cosmetic cost
    but the data stays intact and parseable.

    Use ONLY for cells where Excel's autoparse causes display bugs.
    """
    if value is None:
        return ""
    s = str(value)
    return f"'{s}" if s else ""

# Fixed slot order — every campaign row has exactly these 4 variant slots,
# even if a slot is empty (e.g. no festival today → V4_* cells blank).
VARIANT_SLOTS = [
    ("V1", "viral_reach"),
    ("V2", "high_interaction"),
    ("V3", "follower_growth"),
    ("V4_FESTIVAL", "festival_variant"),
]


def _build_header() -> list[str]:
    """Header layout (attribute-grouped):

    Shared campaign cols, then ALL the V*_VARIANT_TYPEs side-by-side,
    then ALL the V*_POST_TEXTs side-by-side, then ALL the user prompts,
    then ALL the image prompts, then ALL the image URLs, then timings.

    This makes side-by-side variant comparison trivial in Excel:
      - You can read every variant's post text in 4 adjacent cells
      - You can compare every variant's image URL in 4 adjacent cells
      - You can compare Agent 1 / Agent 2 latency across all 4 variants
    """
    shared = [
        "S.NO",
        "BUSINESS_DNA",
        "CAMPAIGN_BRIEF",                  # RAW user-provided brief (not refined)
        "PLATFORM_USED",
        "PRIMARY_BRAND_COLOR",
        "ASPECT_RATIO",
        "MAGIC_PROMPT_MODEL",
        "MAGIC_PROMPT_TEMPERATURE",
        "MAGIC_PROMPT_TOP_P",
        "MAGIC_PROMPT_MAX_TOKENS",
        "MAGIC_PROMPT_SYSTEM_PROMPT",
        "IMAGE_MODEL",
        "IMAGE_MODEL_QUALITY",
        "IMAGE_MODEL_SIZE",
        "TOTAL_CAMPAIGN_TIME_SEC",          # wall-clock from start of pipeline to CSV write (seconds)
        # Upstream agent stage timings (Gemini agents that run BEFORE the
        # image pipeline). Lets you see where time was spent across the
        # whole /generate-content request, not just the visuals portion.
        "REFINE_AGENT_TIME_SEC",            # Gemini brief refinement (seconds)
        "CULTURAL_AGENT_TIME_SEC",          # Gemini festival/cultural calendar lookup (seconds; usually cached)
        "RESEARCH_AGENT_TIME_SEC",          # Gemini grounded research (seconds)
        "CONTENT_AGENT_TIME_SEC",           # Gemini copywriter that produces post variants (seconds)
    ]

    # Per-attribute groups — 4 columns per group (one per variant slot).
    # Order is V1 -> V2 -> V3 -> V4_FESTIVAL within each group.
    def _group(suffix: str) -> list[str]:
        return [f"{slot}_{suffix}" for slot, _expected in VARIANT_SLOTS]

    attribute_groups = (
        _group("VARIANT_TYPE")
        + _group("POST_TEXT")
        + _group("MAGIC_USER_PROMPT")
        + _group("IMAGE_PROMPT_OUTPUT")
        + _group("IMAGE_S3_URL")
        + _group("AGENT1_TIME_SEC")         # GPT-5 latency per variant's magic prompt (seconds)
        + _group("AGENT2_TIME_SEC")         # gpt-image-2 latency per variant's render (seconds)
    )

    return shared + attribute_groups


_HEADER = _build_header()


def _is_local() -> bool:
    return not os.getenv("AWS_LAMBDA_FUNCTION_NAME")


def _next_serial() -> int:
    """Sequential S.NO via csv.reader so multi-line cells don't inflate count."""
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


def log_magic_campaign(
    *,
    business_dna_label: str,
    raw_user_brief: str,
    platform_used: str,
    primary_brand_color: str,
    aspect_ratio: str,
    # Shared Agent 1 / Agent 2 metadata
    agent1_model: str,
    agent1_temperature: float,
    agent1_top_p: float,
    agent1_max_tokens: int,
    agent1_system_prompt: str,
    agent2_model: str,
    agent2_quality: str,
    agent2_size: str,
    # Total wall-clock time for the whole campaign pipeline (seconds, rounded to 2dp)
    total_campaign_time_s: float,
    # Upstream agent stage timings (Gemini agents). All optional — passed
    # from ai_service.py via run_magic_image_pipeline.
    refine_time_sec:   float | None = None,
    cultural_time_sec: float | None = None,
    research_time_sec: float | None = None,
    content_time_sec:  float | None = None,
    # Per-variant data: {variant_type: {"post_text", "user_prompt",
    #   "image_prompt_output", "image_url", "agent1_time_s", "agent2_time_s"}}
    variants: dict[str, dict[str, str]],
) -> None:
    """Append ONE row per campaign. No-op on Lambda. Never raises."""
    if not _is_local():
        return

    with _LOCK:
        try:
            new_file = not _CSV_PATH.exists()
            # utf-8-sig writes a BOM on the first write only. Excel on
            # Windows defaults to cp1252 for plain UTF-8 CSVs, which mojibakes
            # any non-ASCII char (em-dashes, smart quotes, box-drawing in the
            # system prompt). The BOM tells Excel "this is UTF-8" so the
            # cells render correctly in Excel, LibreOffice, and Numbers.
            with _CSV_PATH.open("a", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                if new_file:
                    writer.writerow(_HEADER)

                serial = _next_serial()

                # Pre-resolve the variant dicts in canonical slot order so
                # we can write attribute-by-attribute cleanly.
                ordered_variants = [
                    (slot, expected_variant_key, variants.get(expected_variant_key) or {})
                    for slot, expected_variant_key in VARIANT_SLOTS
                ]

                row: list[str] = [
                    _safe_str(serial),
                    _safe_str(business_dna_label),
                    _safe_str(raw_user_brief),
                    _safe_str(platform_used),
                    _safe_str(primary_brand_color),
                    _excel_text(aspect_ratio),  # "1:1" -> "'1:1" so Excel doesn't autoparse as time
                    _safe_str(agent1_model),
                    _safe_str(agent1_temperature),
                    _safe_str(agent1_top_p),
                    _safe_str(agent1_max_tokens),
                    _safe_str(agent1_system_prompt),
                    _safe_str(agent2_model),
                    _safe_str(agent2_quality),
                    _excel_text(agent2_size),   # "1024x1024" is safe but other sizes look like equations
                    _safe_str(total_campaign_time_s),
                    _safe_str(refine_time_sec),
                    _safe_str(cultural_time_sec),
                    _safe_str(research_time_sec),
                    _safe_str(content_time_sec),
                ]

                # VARIANT_TYPE group (4 cells)
                row.extend(
                    _safe_str(key if v else "")
                    for _slot, key, v in ordered_variants
                )
                # POST_TEXT group (4 cells)
                row.extend(_safe_str(v.get("post_text")) for _s, _k, v in ordered_variants)
                # MAGIC_USER_PROMPT group (4 cells)
                row.extend(_safe_str(v.get("user_prompt")) for _s, _k, v in ordered_variants)
                # IMAGE_PROMPT_OUTPUT group (4 cells)
                row.extend(_safe_str(v.get("image_prompt_output")) for _s, _k, v in ordered_variants)
                # IMAGE_S3_URL group (4 cells)
                row.extend(_safe_str(v.get("image_url")) for _s, _k, v in ordered_variants)
                # AGENT1_TIME_S group (4 cells)
                row.extend(_safe_str(v.get("agent1_time_s")) for _s, _k, v in ordered_variants)
                # AGENT2_TIME_S group (4 cells)
                row.extend(_safe_str(v.get("agent2_time_s")) for _s, _k, v in ordered_variants)

                writer.writerow(row)

            filled = sorted(variants.keys())
            logger.info(
                f"[magic_csv] wrote campaign row #{serial} "
                f"variants_filled={filled} brand={business_dna_label}"
            )
        except Exception as exc:
            logger.warning(f"[magic_csv] write failed (non-fatal): {exc}")
