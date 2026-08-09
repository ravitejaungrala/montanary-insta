"""Edit Style pipeline — single-call, Responses API `image_generation` tool.

Fires when the user picks the `edit_style` style. The simplest of the three
marker pipelines: ONE call to OpenAI's Responses API with the native
`image_generation` tool does BOTH the planning and the rendering that the
other two marker pipelines split across two agents (Art Director + gpt-image-2,
or Image Director + Image Agent). Business DNA, the campaign brief, and the
already-written post text are handed straight to the tool-enabled model — no
separate JSON design-brief step, no prompt-writing agent.

Hard constraints from the feature request this implements:
  - GENERATE ONLY. `action` is always forced to "generate" — never "edit" or
    "auto". There is no multi-turn refinement (no `previous_response_id`);
    every call is a single, independent turn.
  - Exactly 2 variants per campaign. The caller (magic_image_pipeline.py)
    truncates its normal 3/4-variant fan-out to 2 specifically for this
    style; this module is called once per variant, same as the other two
    marker pipelines.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Optional

from openai import OpenAI

logger = logging.getLogger("pipelyt.edit_style")

# Mainline reasoning model that plans the image inside the Responses API
# call. The image_generation tool's own `model` field (below) is the actual
# pixel renderer — these are two different knobs.
_EDIT_STYLE_MODEL = os.environ.get("EDIT_STYLE_MODEL", "gpt-5.1")

_EDIT_STYLE_IMAGE_MODEL   = os.environ.get("EDIT_STYLE_IMAGE_MODEL", "gpt-image-2")
_EDIT_STYLE_IMAGE_QUALITY = os.environ.get("EDIT_STYLE_IMAGE_QUALITY", "high").strip().lower()

# Aspect ratio -> size accepted by the image_generation tool. Kept to the
# tool's documented fixed set; anything unmapped falls back to "auto" and
# lets the renderer choose.
_ASPECT_TO_TOOL_SIZE: dict[str, str] = {
    "1:1":    "1024x1024",
    "4:5":    "1024x1536",
    "9:16":   "1024x1536",
    "16:9":   "1536x1024",
    "1.91:1": "1536x1024",
}


def _openai_client() -> OpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY env var not set — required by the Edit Style pipeline."
        )
    return OpenAI(api_key=key)


def _dna_context_summary(business_dna: Optional[dict]) -> str:
    """Compact DNA summary — brand name, tagline, colours, tone, overview.
    Duplicated (not shared) from the other marker pipelines by design —
    each marker pipeline owns its own small DNA formatter."""
    if not business_dna or not isinstance(business_dna, dict):
        return "(no Business DNA available)"

    lines: list[str] = []

    def _add(key: str, label: Optional[str] = None, limit: int = 300):
        v = business_dna.get(key)
        if not v:
            return
        if isinstance(v, (list, dict)):
            v = json.dumps(v)[:limit]
        s = str(v).strip()
        if s:
            lines.append(f"{label or key.upper()}: {s[:limit]}")

    _add("company_name", "COMPANY")
    _add("product_name", "PRIMARY PRODUCT")
    _add("tagline", "TAGLINE")
    _add("category", "CATEGORY")
    _add("brand_tone", "BRAND TONE")

    colors = business_dna.get("colors") or {}
    if isinstance(colors, dict) and colors:
        color_bits = [f"{k}={v}" for k, v in colors.items() if v]
        if color_bits:
            lines.append(
                "BRAND COLOURS (use ONLY these — never invent hex): "
                + ", ".join(color_bits)
            )

    overview = (business_dna.get("overview") or "").strip()
    if overview:
        lines.append("OVERVIEW:\n" + overview[:1200])

    return "\n\n".join(lines) if lines else "(no Business DNA available)"


_EDIT_STYLE_INSTRUCTIONS = """Read the post content below and figure out from the wording what kind of business this is. Then generate a social media post that combines BOTH meaningful imagery AND readable text — neither dominating, both integrated.

Hard rules:
  - GENERATE ONLY — this is always a brand-new generation, never an edit of a previous image.
  - Logo (attached, if provided) in TOP-LEFT, used exactly as provided.
  - Call-to-action in BOTTOM-LEFT or BOTTOM-RIGHT — pick whichever fits the composition.
  - Brand colours (listed below) used tastefully — never invent hex values not listed.
  - Image AND text BOTH visible and integrated — not one or the other.

Pick the style yourself based on what the content describes."""


def _build_edit_style_input_text(
    *,
    variant_type: str,
    post_text: str,
    campaign_brief: str,
    business_dna: Optional[dict],
    business_category: str,
    brand_name: str,
    primary_brand_color: str,
) -> str:
    dna_block = _dna_context_summary(business_dna)
    return f"""BRAND NAME: {brand_name or '(unknown)'}
BUSINESS CATEGORY: {business_category or '(unset)'}
BRAND COLOUR HINT: {primary_brand_color or '(none)'}
VARIANT: {variant_type}

BUSINESS DNA:
{dna_block}

CAMPAIGN BRIEF (background context — do not paste onto the image):
{campaign_brief.strip()[:1500]}

POST TEXT CONTENT (already written — the image accompanies this):
{post_text.strip()}

Generate the image now."""


def _extract_image_bytes(response) -> bytes:
    """Pull the base64 PNG out of a Responses API result. Raises with a
    clear message if the tool didn't return an image (e.g. the model
    declined to call it)."""
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) == "image_generation_call" and getattr(item, "result", None):
            return base64.b64decode(item.result)
    raise RuntimeError(
        "Edit Style pipeline: Responses API returned no image_generation_call "
        "result — the model may have declined to call the image tool."
    )


def _call_edit_style(
    *,
    variant_type: str,
    post_text: str,
    campaign_brief: str,
    business_dna: Optional[dict],
    business_category: str,
    brand_name: str,
    primary_brand_color: str,
    logo_bytes: Optional[bytes],
    aspect_ratio: str,
) -> tuple[bytes, str]:
    """One Responses API call: plans AND renders the image in a single
    turn. Returns (png_bytes, input_text_used) — the input text is kept
    for CSV/logging parity with the other pipelines' `image_prompt` field.
    """
    client = _openai_client()
    input_text = _build_edit_style_input_text(
        variant_type=variant_type,
        post_text=post_text,
        campaign_brief=campaign_brief,
        business_dna=business_dna,
        business_category=business_category,
        brand_name=brand_name,
        primary_brand_color=primary_brand_color,
    )

    content: list[dict] = [{"type": "input_text", "text": input_text}]
    if logo_bytes:
        b64 = base64.b64encode(logo_bytes).decode("ascii")
        content.append({
            "type": "input_image",
            "image_url": f"data:image/png;base64,{b64}",
            "detail": "high",
        })

    size = _ASPECT_TO_TOOL_SIZE.get((aspect_ratio or "").strip(), "auto")

    t0 = time.monotonic()
    response = client.responses.create(
        model=_EDIT_STYLE_MODEL,
        instructions=_EDIT_STYLE_INSTRUCTIONS,
        input=[{"role": "user", "content": content}],
        tools=[{
            "type": "image_generation",
            "model": _EDIT_STYLE_IMAGE_MODEL,
            "action": "generate",   # never "edit" — always a fresh generation
            "quality": _EDIT_STYLE_IMAGE_QUALITY,
            "size": size,
            "background": "opaque",
        }],
        # No previous_response_id — one-shot only, no multi-turn refinement.
    )
    dur = time.monotonic() - t0

    png_bytes = _extract_image_bytes(response)
    logger.info(
        f"[edit-style] variant={variant_type} model={_EDIT_STYLE_MODEL} "
        f"image_model={_EDIT_STYLE_IMAGE_MODEL} quality={_EDIT_STYLE_IMAGE_QUALITY} "
        f"size={size} dur={dur:.2f}s bytes={len(png_bytes):,} "
        f"(logo={'yes' if logo_bytes else 'no'})"
    )
    return png_bytes, input_text


def run_edit_style_variant(
    *,
    variant_type: str,
    post_text: str,
    campaign_brief: str,
    business_dna: Optional[dict],
    business_category: str,
    brand_name: str,
    primary_brand_color: str,
    logo_bytes: Optional[bytes],
    aspect_ratio: str = "1:1",
) -> dict:
    """End-to-end for ONE variant. Returns a dict shaped so the outer
    pipeline (magic_image_pipeline.py) can slot it into the same
    per-variant result envelope the standard flow uses."""
    logger.info(f"[edit-style] variant={variant_type} BEGIN — single Responses API call")

    t0 = time.monotonic()
    png_bytes, input_text = _call_edit_style(
        variant_type=variant_type,
        post_text=post_text,
        campaign_brief=campaign_brief,
        business_dna=business_dna,
        business_category=business_category,
        brand_name=brand_name,
        primary_brand_color=primary_brand_color,
        logo_bytes=logo_bytes,
        aspect_ratio=aspect_ratio,
    )
    total_time = round(time.monotonic() - t0, 2)

    logger.info(f"[edit-style] variant={variant_type} DONE — total={total_time}s")

    return {
        "image_prompt": input_text,
        "png_bytes": png_bytes,
        # A single fused call does both planning and rendering — there is
        # no separate agent1/agent2 split here, but the outer pipeline's
        # branch code expects both fields for CSV/logging parity.
        "agent1_time_s": total_time,
        "agent2_time_s": 0.0,
        "director_model": _EDIT_STYLE_MODEL,
        "image_agent_model": _EDIT_STYLE_IMAGE_MODEL,
        "aspect_ratio": aspect_ratio,
    }
