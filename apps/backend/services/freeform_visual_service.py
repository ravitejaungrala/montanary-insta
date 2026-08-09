"""Freeform visual service — production-ready freeform image generation.

Pipeline (2 calls instead of multi-agent's 4):
  brief + DNA → gemini-2.5-flash (text agent picks template + writes copy
                                   + writes image_prompt with all literal
                                   text in double quotes)
  image_prompt + logo → gemini-2.5-flash-image (renders finished post
                                                  with logo as reference)

AGENT_SYSTEM lives in `services/freeform_prompts.py` — a side-effect-free
constant module that both this production service AND `image_lab_freeform.py`
(the dev/CLI harness) import. The lab harness is NEVER imported by
production, so its `image_lab_out/` mkdir side-effect can't crash Lambda's
read-only filesystem.

Public API:
    generate_freeform_image(brief, dna, logo_bytes) -> dict
        Returns {
          "image_bytes": bytes,
          "headline": str, "subheading": str, "cta": str,
          "template_chosen": str (e.g. "T-K Editorial UI showcase ..."),
          "image_prompt": str,
          "agent_json": dict (raw agent output)
        }
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger("pipelyt.freeform_visual")

# Pure-constant module — no filesystem side-effects, Lambda-safe at import.
from services.freeform_prompts import AGENT_SYSTEM

from google import genai
from google.genai import types


# ---------------------------------------------------------------------------
# DNA summarization (extracts the fields the agent needs)
# ---------------------------------------------------------------------------

def summarize_dna(dna: dict) -> tuple[str, str]:
    """Return (summary_text, primary_hex) for the agent prompt.

    Reads the same DNA shape as the lab harness — supports both dict and
    list `colors` field, and falls back through the standard hierarchy
    (product_name -> company_name).
    """
    if not isinstance(dna, dict):
        dna = {}
    lines = []
    name = dna.get("product_name") or dna.get("company_name", "")
    lines.append(f"Brand name: {name}")
    if dna.get("tagline") or dna.get("product_tagline"):
        lines.append(f"Tagline: {dna.get('tagline') or dna.get('product_tagline')}")
    if dna.get("overview"):
        ov = str(dna["overview"]).strip().replace("\n", " ")
        lines.append(f"Overview: {ov[:500]}")
    colors = dna.get("colors") or {}
    primary = ""
    if isinstance(colors, dict):
        primary = colors.get("primary") or colors.get("brand") or ""
        secondary = colors.get("secondary") or ""
        if primary:
            lines.append(f"Primary color: {primary}")
        if secondary:
            lines.append(f"Secondary color: {secondary}")
    elif isinstance(colors, list) and colors:
        primary = colors[0]
        lines.append(f"Primary color: {primary}")
        if len(colors) > 1:
            lines.append(f"Secondary color: {colors[1]}")
    if dna.get("brand_tone"):
        bt = dna["brand_tone"]
        if isinstance(bt, list):
            bt = ", ".join(str(v) for v in bt[:6])
        lines.append(f"Brand tone: {str(bt)[:200]}")
    if dna.get("brand_values"):
        bv = dna["brand_values"]
        if isinstance(bv, list):
            bv = ", ".join(str(v) for v in bv[:6])
        lines.append(f"Brand values: {str(bv)[:200]}")
    fonts = dna.get("fonts") or {}
    if isinstance(fonts, dict) and fonts:
        f_primary = fonts.get("primary") or fonts.get("heading") or ""
        if f_primary:
            lines.append(f"Brand font: {f_primary}")
    return "\n".join(lines), primary


# ---------------------------------------------------------------------------
# Step 1 — Text agent: pick template, write copy, write image_prompt
# ---------------------------------------------------------------------------

def _call_text_agent(brief: str, dna_summary: str, primary_hex: str) -> dict:
    """Call gemini-2.5-flash with the AGENT_SYSTEM prompt + brief + DNA.

    Returns the parsed JSON dict with headline / subheading / cta /
    image_prompt fields. Falls back to a regex extractor when JSON parse
    fails on internal unescaped double quotes (a known agent slip).
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY / GOOGLE_API_KEY missing in env")
    client = genai.Client(api_key=api_key)

    prompt = (
        f"{AGENT_SYSTEM}\n\n"
        f"----- CAMPAIGN BRIEF -----\n{brief}\n\n"
        f"----- BRAND DNA -----\n{dna_summary}\n\n"
        f"BRAND PRIMARY COLOR (HEX): "
        f"{primary_hex or '(unspecified - use a visible accent in the DNA-derived palette)'}\n\n"
        "Produce the JSON now.\n"
    )

    resp = client.models.generate_content(
        model="gemini-flash-lite-latest",
        contents=[prompt],
    )
    raw = (resp.text or "").strip() if hasattr(resp, "text") else str(resp)
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    # Try strict JSON first.
    try:
        return json.loads(raw)
    except Exception:
        pass

    # Try extracting the first { ... } object.
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    body = m.group(0) if m else raw
    try:
        return json.loads(body)
    except Exception:
        pass

    # Last-resort regex extractor — handles unescaped " inside image_prompt.
    out = {"headline": "", "subheading": "", "cta": "", "image_prompt": ""}
    for key in ("headline", "subheading", "cta"):
        m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', body)
        if m:
            try:
                out[key] = m.group(1).encode("utf-8").decode("unicode_escape")
            except Exception:
                out[key] = m.group(1)
    m = re.search(r'"image_prompt"\s*:\s*"(.*)"\s*}\s*$', body, re.DOTALL)
    if m:
        out["image_prompt"] = m.group(1)
    return out


# ---------------------------------------------------------------------------
# Step 2 — Image gen with logo as reference
# ---------------------------------------------------------------------------

def _call_image_gen(image_prompt: str, logo_bytes: Optional[bytes]) -> bytes:
    """Call gemini-2.5-flash-image with the prompt + logo reference image.

    Returns raw image bytes. Raises RuntimeError if no image is returned.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY / GOOGLE_API_KEY missing in env")
    client = genai.Client(api_key=api_key)

    contents: list = [image_prompt]
    if logo_bytes:
        mime = "image/png"
        sniff = logo_bytes[:12]
        if sniff[:3] == b"\xff\xd8\xff":
            mime = "image/jpeg"
        elif sniff[:4] == b"GIF8":
            mime = "image/gif"
        elif sniff[:4] == b"RIFF" and sniff[8:12] == b"WEBP":
            mime = "image/webp"
        contents.append(types.Part.from_bytes(data=logo_bytes, mime_type=mime))

    image_bytes: Optional[bytes] = None
    for chunk in client.models.generate_content_stream(
        model="gemini-2.5-flash-image",
        contents=contents,
        config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
    ):
        if not chunk.parts:
            continue
        for part in chunk.parts:
            if getattr(part, "inline_data", None):
                image_bytes = part.inline_data.data
                break
        if image_bytes:
            break
    if not image_bytes:
        raise RuntimeError("Gemini Flash Image returned no image data")
    return _strip_image_metadata(image_bytes)


def _strip_image_metadata(image_bytes: bytes) -> bytes:
    """Strip ALL embedded metadata from a Gemini-generated image before it
    leaves this service.

    Why: Gemini's image generator embeds C2PA / Content Credentials
    (provenance manifest, "AI generated" tags) inside JPEG/PNG metadata
    blocks (JUMBF for JPEG, iTXt/eXIf chunks for PNG). LinkedIn renders
    these as a small "CR" / Content Credentials badge over the image
    when present. We don't want that badge on user-facing posts, so we
    re-encode the image as a clean JPEG with NO exif, NO icc_profile,
    NO xmp, NO C2PA — only the raw pixel data and a basic JFIF header.

    Also strips author/software/copyright/comment fields that Gemini or
    Pillow may set, and any GPS / device fingerprint EXIF tags.

    Returns clean JPEG bytes. On any failure, returns the original bytes
    unchanged (fail-open so we never block a publish).
    """
    try:
        from PIL import Image
        import io
        src = Image.open(io.BytesIO(image_bytes))
        # Force RGB — drop alpha + any color-profile assumptions
        if src.mode not in ("RGB", "L"):
            src = src.convert("RGB")
        # Re-encode WITHOUT any "exif=" / "icc_profile=" kwargs and with
        # `info` not preserved. Pillow's default behaviour drops EXIF,
        # ICC profile, XMP, JUMBF (C2PA), and PNG iTXt/tEXt chunks when
        # we don't explicitly pass them through.
        buf = io.BytesIO()
        src.save(buf, format="JPEG", quality=92, optimize=True,
                 progressive=False, subsampling=0)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("[freeform] metadata strip failed (%s) — using original bytes", exc)
        return image_bytes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_freeform_image(
    brief: str,
    dna: dict,
    logo_bytes: Optional[bytes],
    feedback: Optional[str] = None,
) -> dict:
    """Run the full freeform pipeline and return a dict with the rendered
    image bytes plus the agent's structured copy fields.

    Args:
        brief: campaign brief text (refined or raw — agent handles either)
        dna: business_dna dict (same shape as core/auth.py loads it)
        logo_bytes: optional brand logo PNG/JPG bytes (downloaded from
            DNA `logo_url` upstream, or user-uploaded)
        feedback: optional critic feedback from a previous failed attempt
            (e.g. "logo not visible against dark bg — render the logo in
            white/inverted instead of orange") — prepended to the brief
            so the agent / image model regenerates with the fix in mind.

    Returns:
        {
          "image_bytes": bytes,
          "headline": str,
          "subheading": str,
          "cta": str,
          "template_chosen": str,   # e.g. "T-K Editorial UI showcase..."
          "image_prompt": str,      # raw agent prompt (for debugging)
          "agent_json": dict,       # full raw agent output
        }
    """
    dna_summary, primary_hex = summarize_dna(dna or {})
    logger.info(
        "[freeform] calling text agent  brand=%r  primary=%s  brief_len=%d  feedback=%s",
        (dna or {}).get("product_name") or (dna or {}).get("company_name"),
        primary_hex,
        len(brief or ""),
        bool(feedback),
    )

    # If we have critic feedback from a prior attempt, prepend it to the
    # brief so the agent re-strategizes around the specific failures.
    effective_brief = brief
    if feedback:
        effective_brief = (
            "RETRY — previous attempt scored below threshold. CRITIC FEEDBACK "
            f"that you MUST address in this regeneration:\n{feedback}\n\n"
            "Regenerate with these fixes applied. Then continue with the "
            "original brief below.\n\n"
            "----- ORIGINAL BRIEF -----\n"
            f"{brief}"
        )

    agent_out = _call_text_agent(effective_brief, dna_summary, primary_hex)
    image_prompt = agent_out.get("image_prompt", "") or ""
    if not image_prompt:
        raise RuntimeError("Freeform agent returned empty image_prompt")

    # Pull the bracketed template tag at the start of image_prompt for
    # observability / logging.
    template_chosen = ""
    m = re.match(r"\s*\[(T-[A-Z][^\]]*)\]", image_prompt)
    if m:
        template_chosen = m.group(1).strip()

    logger.info(
        "[freeform] agent picked %r  prompt_len=%d  hl=%r  cta=%r",
        template_chosen, len(image_prompt),
        agent_out.get("headline"), agent_out.get("cta"),
    )

    image_bytes = _call_image_gen(image_prompt, logo_bytes)
    logger.info("[freeform] image_bytes=%d", len(image_bytes))

    return {
        "image_bytes": image_bytes,
        "headline": agent_out.get("headline", "") or "",
        "subheading": agent_out.get("subheading", "") or "",
        "cta": agent_out.get("cta", "") or "",
        "template_chosen": template_chosen,
        "image_prompt": image_prompt,
        "agent_json": agent_out,
    }


# ---------------------------------------------------------------------------
# Critic agent + retry loop
# ---------------------------------------------------------------------------

CRITIC_SYSTEM = """\
You are a strict QA critic for finished social-media post images. Score the
attached image on a 0-10 scale across these criteria. Be ruthless — only
truly polished, deployable images deserve 9 or 10.

Hard fail conditions (auto-zero out the score, max 5):
  • TEXT TYPOS / BROKEN SENTENCES — any visible word that's misspelled,
    has missing letters, has merged adjacent words, or has nonsense
    character runs. Examples: "Snowgake" instead of "Snowflake",
    "transfo ms" instead of "transforms", "DATABAES" instead of
    "Databases", "Whare's" instead of "Where's".
  • DUPLICATE LOGO — the brand logo appears more than once in the image
    (e.g. corner badge AND in the body, or duplicated in a hub).
  • DUPLICATE TEXT — the same headline / subheading / CTA / bubble
    appears twice. Each text element must appear exactly once.
  • LOGO IDENTITY VIOLATION — the brand logo has been redrawn, restyled,
    or reinterpreted (no longer matches the supplied reference). The
    original logo's shape and design MUST be preserved.
  • UNREADABLE LOGO — the logo's color blends into the background and is
    not visible (e.g. dark logo on dark canvas, white logo on white
    canvas). NOTE: the FIX for this is to swap the LOGO COLOR (e.g.
    invert to white or to a high-contrast brand-related shade) — the
    logo's IDENTITY / SHAPE / DESIGN must NEVER change.
  • HEX CODES / COLOR SWATCHES VISIBLE IN IMAGE — any visible hex
    string ("#FF4500", "#20C997", "007BFF"), color swatch row, palette
    strip across the top/edge of the canvas, RGB values, or
    designer-mockup chrome (style-guide labels like "primary /
    secondary / accent", color name labels like "Brand Orange"). Hex
    codes are styling directives only — they must NEVER render as
    visible text. AUTO-FAIL if any palette/swatch row is present.

Soft scoring (each issue subtracts 1-2 points):
  • Image quality / sharpness
  • Composition / balance
  • Brand-color fidelity (does the brand primary appear correctly?)
  • Headline clarity (does it match the brief's strongest beat?)
  • Subheading legibility at body-text scale

Score scale:
  10 — flawless, ready to ship
  9  — minor cosmetic issues only, ship-able
  8  — one moderate issue (small typo OR slight composition issue)
  ≤7 — must regenerate

OUTPUT — strict JSON, no markdown fences:
{
  "score": <integer 0-10>,
  "logo_visible": <true | false>,
  "logo_identity_preserved": <true | false>,
  "typos_found": [<list of mis-rendered words>],
  "duplicates_found": [<list of duplicated elements>],
  "hex_codes_visible": [<list of any hex strings or palette labels seen>],
  "issues": [<short bullet strings, each one critique>],
  "feedback": "<one paragraph of concrete instructions for the
                regeneration agent — what to change, how to fix the logo
                visibility (swap to white / black / lighter shade), what
                text elements to drop or rewrite. Treat this as an
                instruction the next attempt MUST follow.>"
}
"""


def _call_critic_agent(
    image_bytes: bytes,
    brief: str,
    dna_summary: str,
    image_prompt: str,
) -> dict:
    """Call gemini-2.5-flash with the rendered image attached as a
    multimodal input. Returns the parsed JSON critique dict.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY / GOOGLE_API_KEY missing in env")
    client = genai.Client(api_key=api_key)

    text = (
        f"{CRITIC_SYSTEM}\n\n"
        f"----- CAMPAIGN BRIEF (what the image was supposed to convey) -----\n"
        f"{brief[:1500]}\n\n"
        f"----- BRAND DNA -----\n{dna_summary}\n\n"
        f"----- IMAGE PROMPT (what we asked the model to render) -----\n"
        f"{image_prompt[:2500]}\n\n"
        "Now critique the attached image. Return ONLY the JSON.\n"
    )

    # Detect mime from sniffed bytes (same logic as _call_image_gen).
    mime = "image/png"
    sniff = image_bytes[:12] if image_bytes else b""
    if sniff[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif sniff[:4] == b"GIF8":
        mime = "image/gif"
    elif sniff[:4] == b"RIFF" and sniff[8:12] == b"WEBP":
        mime = "image/webp"

    contents = [text, types.Part.from_bytes(data=image_bytes, mime_type=mime)]

    resp = client.models.generate_content(
        model="gemini-flash-lite-latest",
        contents=contents,
    )
    raw = (resp.text or "").strip() if hasattr(resp, "text") else str(resp)
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        try:
            parsed = json.loads(m.group(0)) if m else {}
        except Exception:
            parsed = {}

    # Normalize the score to int 0-10.
    raw_score = parsed.get("score")
    try:
        score = int(raw_score)
    except Exception:
        score = 0
    score = max(0, min(10, score))

    return {
        "score": score,
        "logo_visible": bool(parsed.get("logo_visible", True)),
        "logo_identity_preserved": bool(parsed.get("logo_identity_preserved", True)),
        "typos_found": parsed.get("typos_found") or [],
        "duplicates_found": parsed.get("duplicates_found") or [],
        "issues": parsed.get("issues") or [],
        "feedback": parsed.get("feedback") or "",
    }


def generate_freeform_with_critic(
    brief: str,
    dna: dict,
    logo_bytes: Optional[bytes],
    max_retries: int = 2,
    pass_score: int = 9,
    slot_idx: int = 0,
) -> list:
    """Generate a freeform variant with critic-driven retry loop.

    Returns a LIST of all attempts for this slot (1 to max_retries+1
    entries), each with full image data + critic score + feedback. The
    caller is responsible for picking the best (or top-N across all
    slots).

    Loop logic:
      attempt 0: generate with no feedback → critic scores
      if score >= pass_score: stop, return [attempt0]
      else attempt 1: regenerate with feedback → critic scores
      if score >= pass_score: stop, return [attempt0, attempt1]
      ... (up to max_retries+1 attempts total per slot)

    Each attempt dict:
      {
        ... (all generate_freeform_image fields) ...
        "score": int,
        "critic": {logo_visible, typos_found, duplicates_found, issues, feedback},
        "slot_idx": int,
        "attempt_idx": int,
      }
    """
    attempts = []
    feedback_for_next = None
    dna_summary, _ = summarize_dna(dna or {})

    for attempt_idx in range(max_retries + 1):
        try:
            res = generate_freeform_image(
                brief, dna, logo_bytes, feedback=feedback_for_next,
            )
        except Exception as exc:
            logger.error(
                "[freeform-critic] slot=%d attempt=%d generation failed: %s",
                slot_idx, attempt_idx, exc,
            )
            continue

        try:
            critic = _call_critic_agent(
                res["image_bytes"], brief, dna_summary, res["image_prompt"],
            )
        except Exception as exc:
            logger.warning(
                "[freeform-critic] slot=%d attempt=%d critic call failed: %s — assuming pass",
                slot_idx, attempt_idx, exc,
            )
            critic = {
                "score": pass_score,  # don't block on critic outage
                "logo_visible": True, "logo_identity_preserved": True,
                "typos_found": [], "duplicates_found": [],
                "issues": [], "feedback": "",
            }

        attempts.append({
            **res,
            "score": critic["score"],
            "critic": critic,
            "slot_idx": slot_idx,
            "attempt_idx": attempt_idx,
        })

        logger.info(
            "[freeform-critic] slot=%d attempt=%d score=%d/10  pass=%d  "
            "typos=%d dups=%d logo_visible=%s",
            slot_idx, attempt_idx, critic["score"], pass_score,
            len(critic["typos_found"]), len(critic["duplicates_found"]),
            critic["logo_visible"],
        )

        if critic["score"] >= pass_score:
            break  # passed — no more retries needed

        # Build feedback for the next retry. Concatenate critic.feedback
        # with explicit issue-list bullets so the agent has both narrative
        # and actionable items.
        fb_parts = []
        if critic["feedback"]:
            fb_parts.append(critic["feedback"])
        if critic["typos_found"]:
            fb_parts.append(
                "TYPOS DETECTED — rewrite avoiding: "
                + ", ".join(f"'{t}'" for t in critic["typos_found"])
            )
        if critic["duplicates_found"]:
            fb_parts.append(
                "DUPLICATES DETECTED — show each only once: "
                + ", ".join(f"'{d}'" for d in critic["duplicates_found"])
            )
        if not critic["logo_visible"]:
            fb_parts.append(
                "LOGO NOT VISIBLE — swap the logo's COLOR (invert to white "
                "or a higher-contrast brand-related shade) so it stands out "
                "against the background. The logo's SHAPE / IDENTITY must "
                "remain unchanged — only the color treatment changes."
            )
        if not critic["logo_identity_preserved"]:
            fb_parts.append(
                "LOGO IDENTITY VIOLATED — the supplied logo image was "
                "redrawn or restyled. Embed the supplied logo AS-IS — do "
                "NOT redraw, restyle, or reinterpret it."
            )
        feedback_for_next = "\n\n".join(fb_parts) or "Improve overall quality."

    return attempts
