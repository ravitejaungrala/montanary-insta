"""Social Media Designer pipeline — fresh Art Director approach.

Deliberately different from user_intent, image_director, edit_style, and
free_style. Those all pre-decide layout (logo top-left, CTA bottom-right,
3-slot text schema) via forced JSON fields and long rule blocks. This
pipeline hands the Art Director design judgment instead: it returns one
free-form design brief and the aspect ratio, nothing else.

Principles baked into the Art Director's system prompt (in this order):
  - READ the post and figure out what it's DOING; intent drives visual language
    (not company category).
  - FORMAT FIRST: aspect ratio picked from post + platform, stated as line
    one of the design brief.
  - STRUCTURE, NOT VIBES: describe layout spatially, not with mood words.
  - EXACT TEXT IN QUOTES whenever any text appears on the image.
  - SMALL TEXT is OK per-element when short + high-contrast + on a flat
    surface + verbatim; drop the element otherwise instead of shipping
    unreadable copy.
  - TEXT COLORS MUST BE BRIGHT AND SATURATED — never grey, pastel,
    translucent, or muted.
  - LOGO must appear somewhere; position is a design choice.
  - DNA palette is the starting point, extendable when the design needs it;
    can't be fully abandoned.

Model: GPT-5.1 for the Art Director (one call per variant) + gpt-image-2
at quality=high for the renderer (via client.images.edit when a logo is
attached, else client.images.generate).

Return shape mirrors run_magic_image_pipeline's per-variant envelope so
the outer pipeline's S3 upload + CSV logger keep working unchanged.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from io import BytesIO
from typing import Optional

from openai import OpenAI

from services.cost_ledger import get_current_ledger

logger = logging.getLogger("pipelyt.social_media_designer")

_ART_DIRECTOR_MODEL = os.environ.get("SOCIAL_DESIGNER_MODEL",         "gpt-5.1")
_IMAGE_MODEL        = os.environ.get("SOCIAL_DESIGNER_IMAGE_MODEL",   "gpt-image-2")
_IMAGE_QUALITY      = os.environ.get("SOCIAL_DESIGNER_IMAGE_QUALITY", "high").strip().lower()

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
            "OPENAI_API_KEY env var not set — required by the Social Media Designer pipeline."
        )
    return OpenAI(api_key=key)


def _dna_read(business_dna: Optional[dict]) -> str:
    """Compact DNA context. Not a form to fill — a paragraph the designer reads.
    Trimmed on purpose so the Art Director isn't overwhelmed by every stored
    field."""
    if not business_dna or not isinstance(business_dna, dict):
        return "(no Business DNA provided)"

    parts: list[str] = []

    def _pick(key: str, label: Optional[str] = None, limit: int = 400):
        v = business_dna.get(key)
        if not v:
            return
        if isinstance(v, (list, dict)):
            v = json.dumps(v)[:limit]
        s = str(v).strip()
        if s:
            parts.append(f"{label or key}: {s[:limit]}")

    _pick("company_name", "Company")
    _pick("tagline",      "Tagline")
    _pick("category",     "Business category (a hint — post intent overrides)")
    _pick("brand_tone",   "Brand tone")

    colors = business_dna.get("colors") or {}
    if isinstance(colors, dict) and colors:
        color_bits = [f"{k}={v}" for k, v in colors.items() if v]
        if color_bits:
            parts.append(
                "Brand palette (starting point — extendable when the design needs it): "
                + ", ".join(color_bits)
            )

    overview = (business_dna.get("overview") or "").strip()
    if overview:
        parts.append("What the company does:\n" + overview[:1500])

    return "\n\n".join(parts) if parts else "(no Business DNA provided)"


_SYSTEM_PROMPT = """You are a senior social-media designer. You've been asked to design ONE social-media image for a specific post. You are NOT filling out a form — you are writing the brief a great human designer would hand a senior visualizer, in one paragraph of continuous prose.

Your only output is JSON with exactly two keys: `aspect_ratio` and `image_prompt`. Everything else — layout, text placement, colors, whether there's a CTA button, whether the logo sits in a corner or is integrated into the type, how many text elements exist, whether text lives on a solid band or floats freely — is a design decision you make INSIDE the `image_prompt` prose.

═══════════════════════════════════════════════════════════════════
HOW TO THINK ABOUT THIS BRIEF
═══════════════════════════════════════════════════════════════════

READ FIRST. Read the post text, then the campaign brief, then the DNA. What is the post actually DOING — an announcement, a feature launch, a case study, a thought piece, an offer, a single provocation, an educational breakdown? The post's INTENT drives the visual language, not the company's category. A software services firm can post a product-teaser graphic when they launch an offering. A software product company can post an editorial thought piece with a photographic hero. Read the post and decide what the visual should be doing FIRST.

FORMAT FIRST. Line one of `image_prompt` states the aspect ratio in words: "1:1 square feed post", "9:16 vertical story", "4:5 LinkedIn feed", "16:9 landscape hero", or "1.91:1 wide banner". Pick the ratio that fits the post's platform and content — then build the composition inside that canvas.

STRUCTURE, NOT VIBES. Describe layout SPATIALLY. "Bold headline anchored upper-left across two lines in a wide grotesque sans, hero visual filling the right two-thirds, brand tag in the bottom-right corner in a saturated accent color" is a brief. "Modern minimal premium aspirational atmospheric" is not. If a sentence in your prompt could be swapped between two totally different posts without changing which one it belongs to, it's vibes — rewrite it as structure.

═══════════════════════════════════════════════════════════════════
TEXT ON THE IMAGE — WHEN, HOW, WHAT COLOR
═══════════════════════════════════════════════════════════════════

EVERY text string that appears on the canvas must be stated VERBATIM inside quotes in the `image_prompt`. If you want the headline to say "One AI model doesn't fit every enterprise," write EXACTLY that inside quotes. Never write "add a punchy headline about industries" — the renderer will invent copy and it will be wrong.

You decide whether there is a headline, subhead, CTA button, brand tag, footer domain, or just one line of type across the whole canvas — or NO text at all beyond the logo. Pick what the post actually needs. Some posts are pure image. Some are pure typography. Some layer both. The post decides — not a template.

SMALL TEXT is FINE per-element when ALL FOUR conditions hold: (1) it's short (a few words), (2) it sits on a FLAT high-contrast surface, (3) it's stated verbatim in quotes, (4) it uses a BRIGHT SATURATED color that reads unmissable against the background behind it. Small text FAILS when any of those miss — low contrast, wrapping around a curved surface, model has to invent the copy. If you can't meet all four conditions for a particular element, DROP that element rather than shipping unreadable text.

═══════════════════════════════════════════════════════════════════
TEXT COLOR RULE — NON-NEGOTIABLE
═══════════════════════════════════════════════════════════════════

TEXT COLORS MUST BE BRIGHT AND SATURATED. Not grey. Not pastel. Not translucent. Not muted. Not a low-opacity dark color. Real designer choices only:

  - Pure white (#FFFFFF) on dark backgrounds
  - Deep saturated black (#000000) or near-black (#0F0F1A / #111827) on light backgrounds
  - A SATURATED brand accent used as an ACCENT color on hero text you want to POP:
    bright orange (#FF4500 / #F97316), electric green (#00B050 / #10B981),
    hot magenta (#EC4899), cobalt/electric blue (#2563EB / #0EA5E9),
    saturated red (#DC2626), lemon yellow (#FACC15 — only on dark bg)

For EVERY text element you specify in the image_prompt, name its exact color and explicitly confirm the contrast pairing (e.g. `headline in pure white #FFFFFF on the deep navy #0A1F44 background — extreme contrast, unmissable at thumbnail size`). Vague color language like "dark text" or "brand color" is not acceptable — pick a hex or a saturated named color and state the pairing.

═══════════════════════════════════════════════════════════════════
COLORS AND LOGO
═══════════════════════════════════════════════════════════════════

The DNA palette is your STARTING point. You CAN extend it — add a complementary hue if the design needs one, shift emphasis (use the accent as dominant instead of the primary), pick a photograph-driven background that lives outside the DNA colors when the mood calls for it. What you CAN'T do is fully ignore the palette and render, say, a NeuZenAI post in Coca-Cola red. Keep the brand recognizable.

The brand logo MUST appear somewhere in the image. WHERE is your design decision — top-left corner, top-right, tucked in a corner opposite the hero visual, overlaid at low opacity across a full-bleed image, integrated as part of the type composition, or placed as a small footer brand-tag. Not one fixed position. Pick what fits the specific composition you're designing.

═══════════════════════════════════════════════════════════════════
COMPOSITION CHOICE — AVOID THE LAZY DEFAULT
═══════════════════════════════════════════════════════════════════

gpt-image-2's laziest default for any software/product post is: "big headline stacked on the left / floating product-mockup card tilted on the right, sitting in empty space." It looks like a template. It's the composition every mediocre landing page uses. DO NOT default to this — it's a sign you're not designing, you're falling back.

Reach for the floating-mockup-card composition ONLY when the post is LITERALLY about a specific feature, screen, dashboard, or UX moment the user needs to see. Otherwise, actively pick from richer composition families:

  - PURE TYPOGRAPHY HERO — giant statement (60-80% of canvas), no supporting visual, generous whitespace. When the post is one strong idea, let the words carry it.
  - EDITORIAL PHOTO HERO — real photograph of a person, scene, or object filling most of the canvas, text overlay integrated with the image. When the post has human/scene/story energy.
  - FULL-BLEED SCENE — no floating card, no isolated element. The whole canvas is one continuous image (an environment, an abstract graphic, a texture, a landscape).
  - SPLIT WITH REAL PHOTO — headline one side, real photograph other side (not a mockup card). When you want people/place/product context.
  - POSTER-STYLE — event-poster or magazine-cover language. Bold graphic elements, layered typography, decorative accents. Confident and print-inspired.
  - STAT-FIRST HERO — one giant number as the visual anchor (120pt+), one-line caption underneath. When a specific number IS the story.
  - QUOTE / EDITORIAL CARD — pull-quote in large italic serif, small attribution. When the post is opinion, story, or testimonial.
  - ILLUSTRATION-FIRST — custom illustration, abstract graphic, or metaphorical visual. When the concept is abstract enough that no photo fits.

Two variants of the same brief must NEVER both land on the same composition family. If variant 1 is typography-hero, variant 2 should be photo-hero or full-bleed or poster — NOT another typography-hero and NOT the floating-mockup default.

═══════════════════════════════════════════════════════════════════
POPULATED CONTENT RULE — NO SKELETONS, NO PLACEHOLDERS
═══════════════════════════════════════════════════════════════════

If the design includes ANY chart, dashboard, product screen, UI panel, card, graph, or diagram, you MUST specify the actual populated content in verbatim quotes:

  - REAL NUMBERS: "$2.4M / $1.8M / $1.2M / $900K" — not "some values" or "growing bars"
  - REAL CATEGORY LABELS: "Q1 2026 / Q2 2026 / Q3 2026 / Q4 2026" — not "time periods" or "axis labels"
  - REAL COLORS PER ELEMENT: "bar 1 in bright orange #FF4500, bar 2 in electric blue #2563EB, bar 3 in emerald #10B981" — not "colorful bars" or "brand-color bars"
  - REAL COPY IN CARDS/ROWS: "row 1: 'Increase spend on Meta by +12%' / row 2: 'Reduce TikTok by -8%'" — not "recommendation cards" or "content rows"

The following patterns are BANNED — they are the skeleton-loading default the image model falls back to and they make the finished image look unfinished:

  ✗ Grey horizontal placeholder bars/lines (Lorem-Ipsum-style filler)
  ✗ Empty axis frames with no data plotted
  ✗ Chart shapes with unlabeled bars in a single grey/pastel color
  ✗ Card interiors filled with anonymous grey rectangles or wireframe boxes
  ✗ Generic "chat bubble with example question" floating without context
  ✗ Faint grid patterns pretending to be a spreadsheet or dashboard
  ✗ Wireframe UI panels, ghost-lined nodes, dashed placeholder outlines

If you can't spec real populated content for a chart/UI element, DROP that element and design something else instead. A skeleton chart is worse than no chart at all — it screams "AI-generated placeholder" and kills the design.

═══════════════════════════════════════════════════════════════════
QUALITY BAR
═══════════════════════════════════════════════════════════════════

The finished image should feel like it belongs alongside posts from Linear, Vercel, Stripe, Framer, Ramp, Apple, Nike, MSCHF, or a well-designed editorial magazine — sharp, intentional, print-ready. No AI-slop artifacts (extra fingers, warped edges, melted objects, invented gibberish text). No depth-of-field blur unless it serves a specific compositional purpose. No low-contrast washes. Every text overlay razor-sharp and correctly spelled.

═══════════════════════════════════════════════════════════════════
OUTPUT SCHEMA (return ONLY this JSON — no code fences, no prose outside)
═══════════════════════════════════════════════════════════════════

{
  "aspect_ratio": "1:1" | "4:5" | "9:16" | "16:9" | "1.91:1",
  "image_prompt": "300-500 word design brief in natural language. Line one is the aspect ratio in words. Every text string that appears on the image is quoted verbatim. Every text element names its exact color and confirms the contrast pairing. Layout is spatially explicit. Logo placement is a design decision you make and describe. Structured as continuous designer prose, not a template with slots to fill."
}"""


def _build_user_message(
    *,
    variant_type: str,
    post_text: str,
    campaign_brief: str,
    business_dna: Optional[dict],
) -> str:
    """Compact user message. Post text first (it's the primary signal), then
    the raw brief, then the DNA. No stacking of Refiner-expanded strategic
    briefs — this pipeline deliberately reads lean inputs."""
    # Per-variant intent + composition steer. Because the two variants run
    # in parallel and can't see each other's picks, we bias each toward a
    # DIFFERENT composition family so they don't both land on the same
    # default. Art Director is free to deviate when the post genuinely
    # calls for it — this is a starting nudge, not a lock.
    intent_hint = {
        "viral_reach": (
            "Targeting maximum scroll-stop reach — hook-driven, thumb-stop instinct.\n"
            "Composition steer for THIS variant: prefer PURE TYPOGRAPHY HERO or "
            "STAT-FIRST HERO. Let one bold statement or one giant number carry the "
            "canvas. Avoid the floating-mockup-card default."
        ),
        "follower_growth": (
            "Targeting authority and follower gain — clear value, prompts a follow or click.\n"
            "Composition steer for THIS variant: prefer EDITORIAL PHOTO HERO, "
            "FULL-BLEED SCENE, or SPLIT WITH REAL PHOTO. Human/scene/story energy "
            "beats another UI mockup. Avoid the floating-mockup-card default."
        ),
        "high_interaction": (
            "Targeting comments and saves — a question, provocation, or takeaway worth pinning.\n"
            "Composition steer for THIS variant: prefer QUOTE/EDITORIAL CARD or "
            "POSTER-STYLE composition — magazine-cover energy. Avoid the "
            "floating-mockup-card default."
        ),
        "festival_variant": (
            "Festival/culturally themed while staying brand-aligned.\n"
            "Composition steer for THIS variant: prefer POSTER-STYLE, FULL-BLEED "
            "SCENE, or ILLUSTRATION-FIRST composition. Cultural/festive energy needs "
            "richer visual language than a floating mockup card."
        ),
    }.get(variant_type, "Value-driven, confident.")

    return f"""VARIANT: {variant_type}
{intent_hint}

═══════════════════════════════════════════════════════════════════
POST TEXT (already written — the image accompanies this)
═══════════════════════════════════════════════════════════════════
{post_text.strip()}

═══════════════════════════════════════════════════════════════════
CAMPAIGN BRIEF (raw user intent — background context, do not paste onto the image)
═══════════════════════════════════════════════════════════════════
{(campaign_brief or '').strip()[:1500]}

═══════════════════════════════════════════════════════════════════
BUSINESS DNA
═══════════════════════════════════════════════════════════════════
{_dna_read(business_dna)}

═══════════════════════════════════════════════════════════════════

Read the post, decide what it's DOING, pick the aspect ratio and the whole visual approach, and return the JSON now."""


def _call_art_director(
    *,
    variant_type: str,
    post_text: str,
    campaign_brief: str,
    business_dna: Optional[dict],
) -> dict:
    client = _openai_client()
    user_msg = _build_user_message(
        variant_type=variant_type,
        post_text=post_text,
        campaign_brief=campaign_brief,
        business_dna=business_dna,
    )

    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=_ART_DIRECTOR_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=3000,
    )
    dur = time.monotonic() - t0

    raw = (resp.choices[0].message.content or "").strip()
    logger.info(
        f"[social-designer/art] variant={variant_type} model={_ART_DIRECTOR_MODEL} "
        f"dur={dur:.2f}s tokens=in:{resp.usage.prompt_tokens}/out:{resp.usage.completion_tokens}"
    )

    # Cost ledger — reuse the ART_DIRECTOR slot so the CSV column stays consistent
    # across pipelines.
    try:
        _ledger = get_current_ledger()
        if _ledger is not None:
            _ledger.record_openai_text(
                model=_ART_DIRECTOR_MODEL,
                input_tokens=int(getattr(resp.usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(resp.usage, "completion_tokens", 0) or 0),
                agent_slot="ART_DIRECTOR",
                time_sec=round(dur, 2),
            )
    except Exception as _le:
        logger.warning(f"[cost_ledger] social-designer art director record failed: {_le}")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(f"[social-designer] invalid JSON: {exc}\nRaw: {raw[:500]}")
        raise RuntimeError("Social Media Designer produced invalid JSON") from exc

    ar = str(parsed.get("aspect_ratio", "")).strip()
    if ar not in VALID_ASPECT_RATIOS:
        logger.warning(
            f"[social-designer] aspect_ratio {ar!r} not in {VALID_ASPECT_RATIOS} — coercing to '1:1'"
        )
        parsed["aspect_ratio"] = "1:1"

    img_prompt = str(parsed.get("image_prompt", "")).strip()
    if not img_prompt:
        raise RuntimeError("Social Media Designer returned empty image_prompt")

    logger.info(
        f"[social-designer/art] variant={variant_type} "
        f"aspect={parsed['aspect_ratio']!r} prompt_chars={len(img_prompt)} "
        f"preview={img_prompt[:160]!r}"
    )
    return {
        "aspect_ratio":  parsed["aspect_ratio"],
        "image_prompt":  img_prompt,
        "agent_time_s":  round(dur, 2),
    }


def _call_renderer(
    image_prompt: str,
    aspect_ratio: str,
    logo_bytes: Optional[bytes],
) -> tuple[bytes, float, str]:
    client = _openai_client()
    size = _ASPECT_TO_SIZE.get(aspect_ratio, _DEFAULT_SIZE)

    t0 = time.monotonic()
    if logo_bytes:
        resp = client.images.edit(
            model=_IMAGE_MODEL,
            image=[("logo.png", BytesIO(logo_bytes), "image/png")],
            prompt=image_prompt,
            quality=_IMAGE_QUALITY,
            size=size,
        )
    else:
        resp = client.images.generate(
            model=_IMAGE_MODEL,
            prompt=image_prompt,
            quality=_IMAGE_QUALITY,
            size=size,
        )
    dur = time.monotonic() - t0

    if not resp.data or not getattr(resp.data[0], "b64_json", None):
        raise RuntimeError(
            f"Social Media Designer renderer returned no image bytes "
            f"(model={_IMAGE_MODEL}, quality={_IMAGE_QUALITY}, size={size})"
        )
    png_bytes = base64.b64decode(resp.data[0].b64_json)

    # Cost ledger — IMAGE_GENERATOR slot, same as other pipelines.
    try:
        _ledger = get_current_ledger()
        if _ledger is not None:
            _text_in = 0
            _img_in  = 0
            try:
                _u = getattr(resp, "usage", None)
                if _u:
                    _text_in = int(getattr(_u, "input_tokens", 0) or 0)
                    _details = getattr(_u, "input_tokens_details", None)
                    if _details is not None:
                        _img_in = int(getattr(_details, "image_tokens", 0) or 0)
            except Exception:
                pass
            _ledger.record_openai_image(
                model=_IMAGE_MODEL,
                quality=str(_IMAGE_QUALITY).lower(),
                images=1,
                text_input_tokens=_text_in,
                image_input_tokens=_img_in,
                agent_slot="IMAGE_GENERATOR",
                time_sec=round(dur, 2),
            )
    except Exception as _le:
        logger.warning(f"[cost_ledger] social-designer render record failed: {_le}")

    logger.info(
        f"[social-designer/render] model={_IMAGE_MODEL} quality={_IMAGE_QUALITY} "
        f"size={size} rendered {len(png_bytes):,} bytes in {dur:.2f}s "
        f"aspect={aspect_ratio!r} (logo={'yes' if logo_bytes else 'no'})"
    )
    return png_bytes, round(dur, 2), size


def run_social_media_designer_variant(
    *,
    variant_type: str,
    post_text: str,
    campaign_brief: str,
    business_dna: Optional[dict],
    logo_bytes: Optional[bytes],
) -> dict:
    """End-to-end for ONE variant. Returns a dict shaped so the outer
    magic_image_pipeline can slot it into the same per-variant result
    envelope the standard flow uses.

    `campaign_brief` should be the RAW user-typed brief where possible —
    this pipeline deliberately reads lean inputs and lets the Art Director
    make design decisions from what the post itself is doing."""
    logger.info(
        f"[social-designer] variant={variant_type} BEGIN — Art Director + gpt-image-2"
    )

    ad = _call_art_director(
        variant_type=variant_type,
        post_text=post_text,
        campaign_brief=campaign_brief,
        business_dna=business_dna,
    )
    png_bytes, render_time, size = _call_renderer(
        image_prompt=ad["image_prompt"],
        aspect_ratio=ad["aspect_ratio"],
        logo_bytes=logo_bytes,
    )

    logger.info(
        f"[social-designer] variant={variant_type} DONE — "
        f"art={ad['agent_time_s']}s render={render_time}s "
        f"aspect={ad['aspect_ratio']} size={size}"
    )

    return {
        "director_brief": {
            "aspect_ratio":  ad["aspect_ratio"],
            "image_prompt":  ad["image_prompt"],
        },
        "image_prompt":      ad["image_prompt"],
        "png_bytes":         png_bytes,
        "agent1_time_s":     ad["agent_time_s"],
        "agent2_time_s":     render_time,
        "director_model":    _ART_DIRECTOR_MODEL,
        "image_agent_model": _IMAGE_MODEL,
        "aspect_ratio":      ad["aspect_ratio"],
    }
