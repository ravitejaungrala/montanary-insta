"""Local-only CSV logger for /generate-content results.

Writes one row per generation to apps/backend/local_results.csv so a human can
eyeball the four-variant copy + three image URLs side-by-side without trawling
through DB rows or browser DevTools.

GUARDED: only fires when AWS_LAMBDA_FUNCTION_NAME is NOT set (i.e. we're on the
dev box, not inside the Lambda runtime). Production is untouched.

Column order is fixed by the spec the user asked for:

    S.NO, BUSSINESS_DNA, CAMPAIGN_BREIF,
    LINKEDIN_REACH_VARIENT, LINKEDIN_ENGAGEMENT_VARIENT, LINKEDIN_BRAND_VARIENT, LINKEDIN_AI_RECOMMENDED_VARIENT,
    X_REACH_VARIENT, X_ENGAGEMENT_VARIENT, X_BRAND_VARIENT, X_AI_RECOMMENDED_VARIENT,
    FACEBOOK_REACH_VARIENT, FACEBOOK_ENGAGEMENT_VARIENT, FACEBOOK_BRAND_VARIENT, FACEBOOK_AI_RECOMMENDED_VARIENT,
    INSTAGRAM_REACH_VARIENT, INSTAGRAM_ENGAGEMENT_VARIENT, INSTAGRAM_BRAND_VARIENT, INSTAGRAM_AI_RECOMMENDED_VARIENT,
    IMAGE_VARIENT_1, IMAGE_VARIENT_2, IMAGE_VARIENT_3
"""

from __future__ import annotations

import csv
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Lock so two concurrent requests don't interleave header writes.
_LOCK = threading.Lock()

# Active CSV filename. Change this string when starting a new experiment so
# the historical baseline file stays untouched and the new run lands in a
# separate file we can diff against.
#
# Current run: AD v2 (gemini-2.5-flash multimodal text agent → JSON blueprint
# → gemini-3.1-flash-image with the blueprint's final_render_prompt + same
# few-shot references the AD saw + R1-R5 guardrails). Flag in image_agent_v4
# default: BYPASS_ART_DIRECTOR=false.
_CSV_FILENAME = "art_director_few_shot_testing.csv"
_CSV_PATH = Path(__file__).resolve().parent.parent / _CSV_FILENAME

# CSV column prefix → list of acceptable content-dict keys, in lookup order.
# Using a list-per-prefix (instead of key→prefix) avoids the double-overwrite
# bug where iterating `twitter` then `x` blanks the X columns because the
# second pass finds no `x` key and writes empty strings on top of the good
# `twitter` values. With this structure each prefix is written exactly once.
_PLATFORM_PREFIXES = [
    ("LINKEDIN",  ["linkedin"]),
    ("X",         ["twitter", "x"]),
    ("FACEBOOK",  ["facebook"]),
    ("INSTAGRAM", ["instagram"]),
]

# REACH / ENGAGEMENT / BRAND map onto the variant keys the content agent emits.
_VARIANT_LABEL_TO_KEY = {
    "REACH": "viral_reach",
    "ENGAGEMENT": "high_interaction",
    "BRAND": "follower_growth",
}

_HEADER = [
    "S.NO",
    "BUSSINESS_DNA",
    "CAMPAIGN_BREIF",
    "LINKEDIN_REACH_VARIENT",
    "LINKEDIN_ENGAGEMENT_VARIENT",
    "LINKEDIN_BRAND_VARIENT",
    "LINKEDIN_AI_RECOMMENDED_VARIENT",
    "X_REACH_VARIENT",
    "X_ENGAGEMENT_VARIENT",
    "X_BRAND_VARIENT",
    "X_AI_RECOMMENDED_VARIENT",
    "FACEBOOK_REACH_VARIENT",
    "FACEBOOK_ENGAGEMENT_VARIENT",
    "FACEBOOK_BRAND_VARIENT",
    "FACEBOOK_AI_RECOMMENDED_VARIENT",
    "INSTAGRAM_REACH_VARIENT",
    "INSTAGRAM_ENGAGEMENT_VARIENT",
    "INSTAGRAM_BRAND_VARIENT",
    "INSTAGRAM_AI_RECOMMENDED_VARIENT",
    "IMAGE_VARIENT_1",
    "IMAGE_VARIENT_2",
    "IMAGE_VARIENT_3",
    # Per-stage wall-clock durations (seconds, 2dp). Pulled from
    # data["stage_times"], surfaced by the orchestrator. Lets us
    # eyeball where the latency budget goes per run.
    "REFINE_AGENT_TIME_SEC",
    "RESEARCH_AGENT_TIME_SEC",
    "CONTENT_AGENT_TIME_SEC",
    "IMAGE_GEN_TIME_SEC",
    # Image-check audit findings per variant. Bullet list when issues are
    # found, "• No issues detected" when clean, "" when audit skipped or
    # failed. Local-only — empty on Lambda.
    "IMAGE_VARIENT_1_AUDIT",
    "IMAGE_VARIENT_2_AUDIT",
    "IMAGE_VARIENT_3_AUDIT",
    # AD v2 metadata — populated only when BYPASS_ART_DIRECTOR=false.
    # Records which platform's variant block fed the AD and what the AD
    # decided per image.
    "IMAGE_CAPTION_PLATFORM",                # one cell, applies to all 3 variants
    "IMAGE_VARIENT_1_DIRECTION",             # AD's chosen direction slug
    "IMAGE_VARIENT_2_DIRECTION",
    "IMAGE_VARIENT_3_DIRECTION",
    "IMAGE_VARIENT_1_CAPTION_USED",          # which variant key the AD lifted text from
    "IMAGE_VARIENT_2_CAPTION_USED",
    "IMAGE_VARIENT_3_CAPTION_USED",
    # The AD's authored `final_render_prompt` — the actual 180-260 word
    # paragraph the image model receives (R1-R5 guardrails block is appended
    # to this but is constant per row so we don't duplicate it in every cell).
    # Use these columns side-by-side with the image URLs to audit which
    # prompt produced which image.
    "IMAGE_VARIENT_1_AD_PROMPT",
    "IMAGE_VARIENT_2_AD_PROMPT",
    "IMAGE_VARIENT_3_AD_PROMPT",
    # Dynamic style category assigned to each variant (brief-hash driven).
    # Same brief → same 3 categories; different briefs → different combos.
    # Lets us see at a glance whether the style choices VARY per campaign
    # or whether they collapse into the same 3 every time.
    "IMAGE_VARIENT_1_CATEGORY",
    "IMAGE_VARIENT_2_CATEGORY",
    "IMAGE_VARIENT_3_CATEGORY",
    # AD's `viewer_takeaway` — one sentence describing what someone scrolling
    # past would understand the post is about, just from the image. If this
    # doesn't match the brief's USER GOAL, the composition is wrong.
    "IMAGE_VARIENT_1_VIEWER_TAKEAWAY",
    "IMAGE_VARIENT_2_VIEWER_TAKEAWAY",
    "IMAGE_VARIENT_3_VIEWER_TAKEAWAY",
]


def _is_local() -> bool:
    """True when we're running on a dev box (not inside AWS Lambda)."""
    return not os.getenv("AWS_LAMBDA_FUNCTION_NAME")


def _next_serial() -> int:
    """Count actual CSV data rows (NOT physical newlines, since cells can
    contain embedded newlines inside captions / audit findings). Returns
    the next 1-based serial.

    The previous implementation used `sum(1 for _ in f)` which counted every
    physical newline in the file. Multi-line audit findings inflated the
    count and produced S.NO jumps like 1, 116, 242, … Using csv.reader makes
    it record-aware so the count matches what a human sees in Excel.
    """
    if not _CSV_PATH.exists():
        return 1
    try:
        with _CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            # First row is the header — skip it.
            next(reader, None)
            count = sum(1 for _ in reader)
        return count + 1
    except Exception:
        return 1


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        # Fallback if the content agent ever nests something unexpected.
        import json as _json
        try:
            return _json.dumps(v, ensure_ascii=False)
        except Exception:
            return str(v)
    return str(v)


def _resolve_business_dna_label(business_dna_label: str | None, current_user: Any, product_name: str | None) -> str:
    """Pick the most informative short identifier for the DNA used."""
    if business_dna_label:
        return business_dna_label
    if product_name:
        return product_name
    dna = getattr(current_user, "business_dna", None) or {}
    if isinstance(dna, dict):
        for key in ("company_name", "business_name", "brand_name", "name"):
            v = dna.get(key)
            if v:
                return str(v)
    email = getattr(current_user, "email", None)
    return email or "anonymous"


def log_generation_result(
    *,
    data: dict,
    campaign_brief: str,
    current_user: Any,
    product_name: str | None = None,
    business_dna_label: str | None = None,
) -> None:
    """Append one row to local_results.csv. No-op on Lambda. Never raises."""
    if not _is_local():
        return
    try:
        content = (data or {}).get("content") or {}
        # Lowercase platform keys defensively (orchestrator already lowercases,
        # but cheap insurance).
        content_lc = {str(k).lower(): v for k, v in content.items() if isinstance(v, dict)}
        # Debug: log which platform keys the content agent emitted. Helps
        # diagnose missing-column issues (e.g. X) by showing whether the
        # platform is silently dropped upstream vs. dropped here.
        logger.info(f"[csv_logger] platform keys in content: {list(content_lc.keys())}")

        recommendation = (data or {}).get("recommendation") or {}
        best_variant = recommendation.get("best_variant") or ""

        # Build per-platform cells. For each output prefix we walk the
        # candidate key list and take the FIRST one that produced a non-empty
        # variants dict — never overwrite a populated column with an empty one.
        row_by_col: dict[str, str] = {}
        for prefix, candidate_keys in _PLATFORM_PREFIXES:
            variants: dict = {}
            for k in candidate_keys:
                maybe = content_lc.get(k)
                if isinstance(maybe, dict) and maybe:
                    variants = maybe
                    break
            for label, vkey in _VARIANT_LABEL_TO_KEY.items():
                row_by_col[f"{prefix}_{label}_VARIENT"] = _safe_str(variants.get(vkey, ""))
            ai_rec = variants.get(best_variant) if best_variant else ""
            row_by_col[f"{prefix}_AI_RECOMMENDED_VARIENT"] = _safe_str(ai_rec)

        # Image variants.
        visuals = (data or {}).get("visuals") or []
        def _img(i: int) -> str:
            if i < len(visuals) and isinstance(visuals[i], dict):
                return _safe_str(visuals[i].get("url", ""))
            return ""

        def _audit(i: int) -> str:
            if i < len(visuals) and isinstance(visuals[i], dict):
                return _safe_str(visuals[i].get("audit", ""))
            return ""

        # AD v2 metadata helpers.
        def _direction(i: int) -> str:
            if i < len(visuals) and isinstance(visuals[i], dict):
                return _safe_str(visuals[i].get("ad_direction") or "")
            return ""

        def _caption_used(i: int) -> str:
            if i < len(visuals) and isinstance(visuals[i], dict):
                return _safe_str(visuals[i].get("ad_caption_variant_used") or "")
            return ""

        def _ad_prompt(i: int) -> str:
            """The AD's final_render_prompt — exactly what the image model
            received as its text input (the R1-R5 guardrails block is the
            constant boilerplate appended to this; not included here)."""
            if i < len(visuals) and isinstance(visuals[i], dict):
                bp = visuals[i].get("ad_blueprint") or {}
                return _safe_str(bp.get("final_render_prompt", ""))
            return ""

        def _category(i: int) -> str:
            """The dynamic style category assigned to this variant."""
            if i < len(visuals) and isinstance(visuals[i], dict):
                bp = visuals[i].get("ad_blueprint") or {}
                return _safe_str(bp.get("style_category", ""))
            return ""

        def _viewer_takeaway(i: int) -> str:
            """The AD's `viewer_takeaway` — what a viewer would understand
            the post is about from the image alone."""
            if i < len(visuals) and isinstance(visuals[i], dict):
                bp = visuals[i].get("ad_blueprint") or {}
                return _safe_str(bp.get("viewer_takeaway", ""))
            return ""

        # caption_platform is stamped on every variant by the image agent;
        # take it from the first non-empty one. Empty when no variants
        # succeeded (no AD to record from).
        caption_platform = ""
        for v in visuals:
            if isinstance(v, dict) and v.get("caption_platform"):
                caption_platform = _safe_str(v.get("caption_platform"))
                break

        # Per-stage durations (seconds, 2 dp). Orchestrator surfaces these on
        # data["stage_times"] as {refine, cultural, research, content,
        # visuals, critic}. We log the four the user cares about.
        stage_times = (data or {}).get("stage_times") or {}
        def _dur(key: str) -> str:
            v = stage_times.get(key)
            if v is None:
                return ""
            try:
                return f"{float(v):.2f}"
            except (TypeError, ValueError):
                return ""

        with _LOCK:
            new_file = not _CSV_PATH.exists()
            # utf-8-sig writes a UTF-8 BOM at file creation. Excel-on-Windows
            # uses the BOM to detect UTF-8 instead of falling back to its
            # legacy Windows-1252 default — which is what turns "→" into "â†'"
            # and emoji into garbage. In append mode Python's utf-8-sig does
            # NOT re-emit the BOM, so existing files stay one-BOM clean.
            # Mode = "w" only when we're creating the file fresh.
            mode = "w" if new_file else "a"
            with _CSV_PATH.open(mode, encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                if new_file:
                    writer.writerow(_HEADER)
                row = [
                    _next_serial(),
                    _resolve_business_dna_label(business_dna_label, current_user, product_name),
                    _safe_str(campaign_brief),
                    row_by_col.get("LINKEDIN_REACH_VARIENT", ""),
                    row_by_col.get("LINKEDIN_ENGAGEMENT_VARIENT", ""),
                    row_by_col.get("LINKEDIN_BRAND_VARIENT", ""),
                    row_by_col.get("LINKEDIN_AI_RECOMMENDED_VARIENT", ""),
                    row_by_col.get("X_REACH_VARIENT", ""),
                    row_by_col.get("X_ENGAGEMENT_VARIENT", ""),
                    row_by_col.get("X_BRAND_VARIENT", ""),
                    row_by_col.get("X_AI_RECOMMENDED_VARIENT", ""),
                    row_by_col.get("FACEBOOK_REACH_VARIENT", ""),
                    row_by_col.get("FACEBOOK_ENGAGEMENT_VARIENT", ""),
                    row_by_col.get("FACEBOOK_BRAND_VARIENT", ""),
                    row_by_col.get("FACEBOOK_AI_RECOMMENDED_VARIENT", ""),
                    row_by_col.get("INSTAGRAM_REACH_VARIENT", ""),
                    row_by_col.get("INSTAGRAM_ENGAGEMENT_VARIENT", ""),
                    row_by_col.get("INSTAGRAM_BRAND_VARIENT", ""),
                    row_by_col.get("INSTAGRAM_AI_RECOMMENDED_VARIENT", ""),
                    _img(0),
                    _img(1),
                    _img(2),
                    _dur("refine"),
                    _dur("research"),
                    _dur("content"),
                    _dur("visuals"),
                    _audit(0),
                    _audit(1),
                    _audit(2),
                    caption_platform,
                    _direction(0),
                    _direction(1),
                    _direction(2),
                    _caption_used(0),
                    _caption_used(1),
                    _caption_used(2),
                    _ad_prompt(0),
                    _ad_prompt(1),
                    _ad_prompt(2),
                    _category(0),
                    _category(1),
                    _category(2),
                    _viewer_takeaway(0),
                    _viewer_takeaway(1),
                    _viewer_takeaway(2),
                ]
                writer.writerow(row)
        logger.info(f"[csv_logger] appended row to {_CSV_PATH}")
    except Exception as e:
        # Local debugging convenience must NEVER break a request.
        logger.warning(f"[csv_logger] skipped due to error: {e}")
