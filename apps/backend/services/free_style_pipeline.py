"""Free-Style pipeline — zero-engineering minimal path.

Fires when the user picks the `free_style_post` style. Deliberately the
simplest pipeline in the codebase: no Prompt Writer agent, no structured
prompt template, no rules, no style vocabulary. The prompt is literally
just:

    generate a image {raw_campaign_brief} and {post_content}

The brand logo is attached to `client.images.edit()` so it appears in the
rendered image. Everything else — layout, typography, imagery choice, CTA,
colours — is left entirely to gpt-image-2's own judgement.

Use this style when you want to see what the raw image model produces
without any of the Fortune-500 direction, section templates, or hard
rules that the User Intent pipeline injects.
"""
from __future__ import annotations

import base64
import logging
import os
import time
from io import BytesIO
from typing import Optional

from openai import OpenAI

logger = logging.getLogger("pipelyt.free_style")

_IMAGE_MODEL   = os.environ.get("FREE_STYLE_IMAGE_MODEL",   "gpt-image-2")
_IMAGE_QUALITY = os.environ.get("FREE_STYLE_IMAGE_QUALITY", "high").strip().lower()

_ASPECT_TO_SIZE: dict[str, str] = {
    "1:1":    "1024x1024",
    "4:5":    "1024x1280",
    "9:16":   "1024x1792",
    "16:9":   "1792x1024",
    "1.91:1": "1792x1024",
}
_DEFAULT_SIZE = "1024x1024"
VALID_ASPECT_RATIOS = ("1:1", "4:5", "9:16", "16:9", "1.91:1")


def _openai_client() -> OpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY env var not set — required by the Free-Style renderer."
        )
    return OpenAI(api_key=key)


def _build_prompt(raw_brief: str, post_text: str) -> str:
    """Minimal prompt — brief + post text, nothing more, nothing less."""
    return f"""generate a image Understand this campion breif: {raw_brief.strip()} and Post Text Content :{post_text.strip()} you have freedome that you text include in the image or not the imaginary and evrything but my main goal is to generate best image for this campion and text content not some generic images and to much content images so brands and company post on their social media
     Hard Rules: 
     1. Logo Should be top left with company name 
     2. Call to action should be bottom right or bottom middle or bottom left only
     3. you can remove unwanted text which you want so for better image"""


def run_free_style_variant(
    *,
    variant_type: str,
    post_text: str,
    campaign_brief: str,
    logo_bytes: Optional[bytes],
    aspect_ratio: str = "1:1",
) -> dict:
    """End-to-end for ONE variant. Returns a dict shaped so the outer
    pipeline can slot it into the same per-variant result envelope the
    standard flow uses.

    `campaign_brief` should already be the RAW user-typed brief (the
    caller in magic_image_pipeline is responsible for passing raw, not
    refined).
    """
    aspect_ratio = (aspect_ratio or "1:1").strip()
    if aspect_ratio not in VALID_ASPECT_RATIOS:
        logger.warning(
            f"[free-style] variant={variant_type} aspect_ratio "
            f"{aspect_ratio!r} not in {VALID_ASPECT_RATIOS} — coercing to '1:1'"
        )
        aspect_ratio = "1:1"

    size   = _ASPECT_TO_SIZE.get(aspect_ratio, _DEFAULT_SIZE)
    prompt = _build_prompt(campaign_brief, post_text)

    logger.info(
        f"[free-style] variant={variant_type} BEGIN — minimal prompt path "
        f"(aspect={aspect_ratio!r}, size={size}, model={_IMAGE_MODEL}, "
        f"quality={_IMAGE_QUALITY}, logo={'yes' if logo_bytes else 'no'})"
    )
    logger.info(
        f"\n\n══════════════════════════════════════════════════════════════════\n"
        f"[free-style] variant={variant_type} — PROMPT (sent to gpt-image-2 verbatim):\n"
        f"══════════════════════════════════════════════════════════════════\n"
        f"{prompt}\n"
        f"══════════════════════════════════════════════════════════════════\n"
    )

    client = _openai_client()
    t0 = time.monotonic()
    if logo_bytes:
        resp = client.images.edit(
            model=_IMAGE_MODEL,
            image=[("logo.png", BytesIO(logo_bytes), "image/png")],
            prompt=prompt,
            quality=_IMAGE_QUALITY,
            size=size,
        )
    else:
        resp = client.images.generate(
            model=_IMAGE_MODEL,
            prompt=prompt,
            quality=_IMAGE_QUALITY,
            size=size,
        )
    dur = time.monotonic() - t0

    if not resp.data or not getattr(resp.data[0], "b64_json", None):
        raise RuntimeError(
            f"Free-Style renderer returned no image bytes "
            f"(model={_IMAGE_MODEL}, quality={_IMAGE_QUALITY}, size={size})"
        )
    png_bytes = base64.b64decode(resp.data[0].b64_json)

    logger.info(
        f"[free-style] variant={variant_type} DONE — rendered "
        f"{len(png_bytes):,} bytes in {dur:.2f}s aspect={aspect_ratio!r}"
    )

    return {
        "director_brief":    {
            "final_prompt":  prompt,
            "aspect_ratio":  aspect_ratio,
            "heading":       "",
            "cta_text":      "",
        },
        "image_prompt":      prompt,
        "png_bytes":         png_bytes,
        "agent1_time_s":     0.0,          # no prompt-writer agent in this path
        "agent2_time_s":     round(dur, 2),
        "director_model":    "(none)",
        "image_agent_model": _IMAGE_MODEL,
        "aspect_ratio":      aspect_ratio,
        "style_refs":        0,
    }
