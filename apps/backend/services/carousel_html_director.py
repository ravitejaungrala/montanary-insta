"""Carousel HTML Director - Variant C.

Same role as the existing carousel_director.py (decide deck design + per-slide
content), but instead of emitting an `image_prompt` for gpt-image-2, this
agent emits FULL STANDALONE HTML for each slide. The renderer then loads
that HTML in headless Chromium and screenshots it to PNG - pixel-perfect
text, exact brand colors, deterministic logo placement, $0 per slide.

Output schema (JSON, enforced by response_format=json_object):

    {
      "pdf_title": "<3-8 word title>",
      "deck_design": {
        "palette":      "<short palette description, e.g. 'orange + warm
                         beige + dark slate'>",
        "fonts":        "<typography intent, e.g. 'Inter Bold display +
                         Inter Regular body'>",
        "background_grammar": "<how backgrounds evolve across slides>",
        "deck_mood":    "<3-6 mood adjectives>"
      },
      "slides": [
        {
          "slide_no": 1,
          "role":     "cover" | "body" | "cta",
          "headline": "<short text - same as text shown on the slide>",
          "html":     "<complete standalone HTML for this slide>"
        },
        ...
      ]
    }

The `html` field is a COMPLETE <!DOCTYPE html>...</html> document with
embedded <style>. The renderer substitutes {{LOGO_DATA_URL}} with a real
data: URL for the brand logo before screenshotting.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from openai import OpenAI

logger = logging.getLogger("pipelyt.carousel_html_director")


# ============================================================
# HYPERPARAMETERS (env-overridable)
# ============================================================
DIRECTOR_MODEL            = os.getenv("CAROUSEL_HTML_DIRECTOR_MODEL", "gpt-5")
DIRECTOR_MAX_TOKENS       = int(os.getenv("CAROUSEL_HTML_DIRECTOR_MAX_TOKENS", "24000"))
DIRECTOR_REASONING_EFFORT = os.getenv("CAROUSEL_HTML_DIRECTOR_REASONING", "medium")
DIRECTOR_TEMPERATURE      = float(os.getenv("CAROUSEL_HTML_DIRECTOR_TEMPERATURE", "0.7"))
DIRECTOR_TOP_P            = float(os.getenv("CAROUSEL_HTML_DIRECTOR_TOP_P", "1.0"))

MIN_SLIDES = int(os.getenv("CAROUSEL_MIN_SLIDES", "2"))
MAX_SLIDES = int(os.getenv("CAROUSEL_MAX_SLIDES", "6"))


# ============================================================
# SYSTEM PROMPT
# ============================================================
DIRECTOR_SYSTEM_PROMPT = """\
You are the HTML Carousel Director for a LinkedIn PDF carousel post.

You receive ONE LinkedIn post (POST_TEXT) plus brand inputs. Your job is
to design a swipeable PDF carousel and EMIT FULL STANDALONE HTML for each
slide. The HTML will be rendered in a headless Chromium browser at exactly
1024x1024 viewport and screenshotted to a PNG. Each PNG becomes one PDF
page.

Everything on every slide must be derivable from POST_TEXT. Do not invent
content beyond what POST_TEXT says.

-------------------------------------------------------------------
STEP 0 (DO THIS FIRST): CLASSIFY THE POSTER
-------------------------------------------------------------------

Decide WHO is posting based on POST_TEXT + BRAND_NAME. Pick exactly ONE:

  PRODUCT COMPANY - sells a specific product, SaaS, app, or tool.
    CTA options: "Book a demo", "See it in action", "Try it free",
    "Start free trial", "Get early access", "Watch the demo",
    "Try BRAND_NAME", "Get started".

  SERVICE / AGENCY COMPANY - sells services, consulting, agency work.
    CTA options: "Book a call", "Schedule a call", "Get a quote",
    "Talk to us", "See our services", "See case studies",
    "Hire us", "Work with us".

  INDIVIDUAL / THOUGHT LEADER - person sharing an idea, framework, opinion.
    CTA options: "Learn more", "Read the full article", "Visit our site",
    "Read more", "Explore more", "See our work".

FORBIDDEN CTAs (never use these):
  Follow, Subscribe, Save, Like, Share, Comment, DM, Tag.
  These are soft social asks, not commercial actions.

-------------------------------------------------------------------
HOW MANY SLIDES?
-------------------------------------------------------------------

You pick slide_count inside [MIN_SLIDES .. MAX_SLIDES]. Decompose
POST_TEXT into its component ideas; one idea per body slide. First slide
MUST be cover, last MUST be cta, middle MUST be body. With MIN_SLIDES=2
you can output cover + cta only when POST_TEXT has no distinct body
ideas to split out.

-------------------------------------------------------------------
DECK DESIGN (set once, applied to every slide)
-------------------------------------------------------------------

Pick deck_design.palette, .fonts, .background_grammar, .deck_mood so the
deck reads as ONE designed piece, not random slides. Use BRAND_COLOR
prominently across every slide (backgrounds, color blocks, accents,
buttons, or typography highlights) - never as on-slide literal hex text.

-------------------------------------------------------------------
HTML CONSTRAINTS (REQUIRED ON EVERY SLIDE)
-------------------------------------------------------------------

Each `html` value is a COMPLETE standalone document:

  <!DOCTYPE html>
  <html>
  <head>
    <meta charset="utf-8">
    <style>
      /* All CSS inline here. No external stylesheets. You MAY load
         Google Fonts via @import url(...) inside the <style> tag. */
      html, body { margin: 0; padding: 0; }
      body {
        width: 1024px;
        height: 1024px;
        font-family: 'Inter', -apple-system, sans-serif;
        /* ... your slide design ... */
      }
      .brand-logo {
        position: absolute;
        top: 32px;
        left: 32px;
        width: 96px;        /* ~9-10% of slide width */
        height: auto;
      }
      .brand-name {
        position: absolute;
        top: 40px;
        left: 144px;        /* sits next to the logo */
        font-weight: 600;
      }
      /* ... rest of slide layout ... */
    </style>
  </head>
  <body>
    <img class="brand-logo" src="{{LOGO_DATA_URL}}" />
    <div class="brand-name">BRAND_NAME_LITERAL</div>
    <!-- ... slide content ... -->
  </body>
  </html>

HARD RULES (every slide):

1. Viewport is EXACTLY 1024x1024. Set body to width:1024px; height:1024px;
   overflow:hidden. No scrollbars.

2. Brand logo MUST appear in the TOP-LEFT corner. Place an
   `<img class="brand-logo" src="{{LOGO_DATA_URL}}" />` with CSS
   position: absolute; top: ~24-40px; left: ~24-40px; width: ~80-120px.
   The renderer substitutes {{LOGO_DATA_URL}} with the real logo data:
   URL before screenshotting. DO NOT change the placeholder string -
   keep it exactly as {{LOGO_DATA_URL}}.

3. Brand name MUST appear as readable text near the logo (right of it
   or below it). Substitute the actual brand name string (not a
   placeholder) - e.g. the BRAND_NAME variable from the user prompt.

4. Brand color MUST appear prominently (background fill, large color
   block, button color, or accent shape). Use it as a CSS custom
   property at the top of <style>:
     :root { --brand: BRAND_COLOR_HEX; }
   Then reference var(--brand) throughout.

5. Slide background fills the entire viewport. Use solid color,
   gradient, or layered shapes - whatever fits the deck mood.

6. Typography hierarchy: headlines must be LARGE (60-110px font-size
   for the giant typographic statement on cover; 36-72px on body
   slides). Use line-height ~1.05-1.15. Limit line length to ~14-22
   characters per line for readability.

7. ALL text must be ACTUAL text in HTML elements (so it renders
   crisply). Do NOT put text inside <img> or SVG path. Text MUST be
   selectable and pixel-perfect.

8. NO external image URLs, NO data URLs other than {{LOGO_DATA_URL}}.
   All visuals are CSS-drawn (gradients, shapes, color blocks). The
   only image on the slide is the brand logo via {{LOGO_DATA_URL}}.

9. For BODY slides: include a section-pill chip near the top of the
   slide (below the brand row) showing the topic of THIS slide. Pill
   text must be 1-4 MEANINGFUL words from POST_TEXT - NEVER generic
   placeholders like "Insight 01", "Step 2", "Point 3".

10. For CTA slide: include a clearly visible button.
    - Button position: bottom-left OR bottom-center (bottom-middle).
      No other positions.
    - Button style: pill or rounded-rectangle, FILLED with brand color
      (or high-contrast accent), readable text inside, generous
      padding. Visually unmissable.
    - Button label: pick from the classification's CTA option list.
      NEVER use a forbidden social CTA.

11. Use Google Fonts via @import in the <style> if you want specific
    web fonts. Default to Inter / Manrope / DM Sans / Plus Jakarta
    Sans / Space Grotesk - these all render crisply at carousel
    scale. Always include `font-display: swap` fallbacks.

-------------------------------------------------------------------
SLIDE ROLES
-------------------------------------------------------------------

  cover (slide 1): typographic hero. Giant headline pulled from the
    single strongest line in POST_TEXT. No section pill. May have a
    subtitle. Background sets the deck's visual tone.

  body (slides 2..N-1): one atomic idea per slide. Top: brand row +
    section-pill chip. Middle/center: headline or headline + caption.
    Visual variety slide-to-slide is welcome (split layouts, full-
    bleed color blocks, large numbers as visual elements, side-by-
    side comparisons) - while staying inside the deck_design system.

  cta (slide N): minimal. One line of text + the mandatory CTA button.
    Visual echoes the cover (same palette/fonts) so the deck reads as
    bookended.

-------------------------------------------------------------------
OUTPUT FORMAT (STRICT)
-------------------------------------------------------------------

Output ONLY a JSON object - no prose, no markdown, no commentary:

{
  "pdf_title": "<string>",
  "deck_design": {
    "palette":            "<string>",
    "fonts":              "<string>",
    "background_grammar": "<string>",
    "deck_mood":          "<string>"
  },
  "slides": [
    {
      "slide_no": 1,
      "role":     "cover",
      "headline": "<the on-slide text, copied from the rendered HTML>",
      "html":     "<complete <!DOCTYPE html>...</html> document>"
    }
    /* ... one entry per slide ... */
  ]
}

The slides array MUST contain between MIN_SLIDES and MAX_SLIDES entries
(inclusive). First = cover, last = cta, middle = body. Every slide MUST
have a non-empty `headline` field for downstream CSV / review UI.
"""


# ============================================================
# CLIENT
# ============================================================
_client_singleton: OpenAI | None = None


def _client() -> OpenAI:
    global _client_singleton
    if _client_singleton is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not set - cannot run HTML Carousel Director")
        _client_singleton = OpenAI(api_key=api_key)
    return _client_singleton


def _is_gpt5_family(model: str) -> bool:
    m = (model or "").lower()
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


# ============================================================
# USER PROMPT BUILDER
# ============================================================
def build_director_user_prompt(
    *,
    post_text: str,
    brand_name: str,
    brand_color: str,
    aspect_ratio: str,
    slide_count: int | None = None,
    min_slides: int | None = None,
    max_slides: int | None = None,
) -> str:
    lo = min_slides if min_slides is not None else MIN_SLIDES
    hi = max_slides if max_slides is not None else MAX_SLIDES
    if slide_count is not None:
        count_directive = (
            f"SLIDE_COUNT (HARD OVERRIDE - output exactly this many slides):\n"
            f"{slide_count}\n"
        )
    else:
        count_directive = (
            f"MIN_SLIDES: {lo}\n"
            f"MAX_SLIDES: {hi}\n"
            f"You decide slide_count inside [{lo}..{hi}] based on POST_TEXT.\n"
        )
    return f"""\
POST_TEXT (LinkedIn caption - the ONLY source of truth):
{post_text}

BRAND_NAME (render this literal string near the logo on every slide):
{brand_name or "(unknown)"}

BRAND_COLOR (hex; use prominently in every slide via --brand CSS var):
{brand_color}

ASPECT_RATIO (every slide MUST be designed for this ratio at 1024x1024):
{aspect_ratio}

{count_directive}
Emit the JSON object now. Every slide's `html` value must be a complete
standalone HTML document with embedded <style>, the {{LOGO_DATA_URL}}
placeholder in the brand-logo <img> src, and the literal BRAND_NAME
string rendered near the logo.
"""


# ============================================================
# VALIDATION
# ============================================================
_LOGO_PLACEHOLDER_RE = re.compile(r"\{\{\s*LOGO_DATA_URL\s*\}\}")
_HTML_DOC_RE         = re.compile(r"<!DOCTYPE\s+html", re.IGNORECASE)
_BODY_TAG_RE         = re.compile(r"<body[\s>]", re.IGNORECASE)
_PLACEHOLDER_PILL_RE = re.compile(
    r"^\s*("
    r"insight|problem|point|step|feature|slide|item|tip|fact|reason|"
    r"benefit|principle|pillar|idea|key|number|no\.?"
    r")\s*[#\-_]?\s*(\d+|n|x|i{1,3}|iv|v|vi{1,3}|ix|x)\s*$",
    re.IGNORECASE,
)
_FORBIDDEN_CTA_WORDS = (
    "follow", "subscribe", "save ", "save it", "save this",
    "like ", "like and", "share ", "share this",
    "comment", "dm ", "dm us", "dm for",
    "tag a", "tag your",
)
_FORBIDDEN_CTA_EXACT = {"follow", "subscribe", "save", "like", "share"}


def _validate(parsed: Any, *, lo: int, hi: int, forced: int | None) -> None:
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Director output not a dict: {type(parsed).__name__}")
    slides = parsed.get("slides")
    if not isinstance(slides, list):
        raise RuntimeError(f"slides must be a list (got {type(slides).__name__})")
    n = len(slides)
    if forced is not None and n != forced:
        raise RuntimeError(f"slides must have exactly {forced} entries; got {n}")
    if forced is None and (n < lo or n > hi):
        raise RuntimeError(f"slides must have {lo}..{hi} entries; got {n}")
    if "deck_design" not in parsed or not isinstance(parsed.get("deck_design"), dict):
        logger.warning("[html_director] output missing deck_design block")
    for i, s in enumerate(slides, 1):
        if not isinstance(s, dict):
            raise RuntimeError(f"slides[{i-1}] not a dict")
        for f in ("slide_no", "role", "html"):
            if f not in s or not s[f]:
                raise RuntimeError(f"slides[{i-1}] missing required field '{f}'")
        html = s["html"]
        if not _HTML_DOC_RE.search(html) or not _BODY_TAG_RE.search(html):
            raise RuntimeError(
                f"slides[{i-1}] html is not a complete document (missing "
                f"<!DOCTYPE html> or <body>): first 200 chars={html[:200]!r}"
            )
        if not _LOGO_PLACEHOLDER_RE.search(html):
            raise RuntimeError(
                f"slides[{i-1}] html is missing the {{{{LOGO_DATA_URL}}}} "
                f"placeholder - brand logo cannot be injected"
            )
        # Headline fallback - copy from text_zones-equivalent if missing.
        if not s.get("headline"):
            s["headline"] = s.get("role", "slide")
        # Pill validator: if there's a clearly section-pill-like CSS class
        # with placeholder text, fail. We do this on the raw HTML by
        # looking for obvious "Insight 01" / "Step 2" patterns.
        for placeholder in ("Insight 01", "Insight 02", "Problem 01",
                            "Point 01", "Step 01", "Feature 01"):
            if placeholder in html:
                raise RuntimeError(
                    f"slides[{i-1}] html contains placeholder pill {placeholder!r}"
                )
        # CTA validator on the cta slide: look for forbidden CTA text.
        if s.get("role") == "cta":
            html_lc = html.lower()
            for w in _FORBIDDEN_CTA_WORDS:
                if w in html_lc:
                    raise RuntimeError(
                        f"slides[{i-1}] cta slide contains forbidden CTA "
                        f"word {w!r} - use a commercial CTA instead"
                    )


# ============================================================
# DIRECTOR CALL
# ============================================================
def run_carousel_html_director(
    *,
    post_text: str,
    brand_name: str,
    brand_color: str,
    aspect_ratio: str,
    slide_count: int | None = None,
    min_slides: int | None = None,
    max_slides: int | None = None,
) -> dict[str, Any]:
    """Run the HTML director. Returns parsed JSON with pdf_title +
    deck_design + slides[] where each slide has a complete `html` doc."""
    lo = min_slides if min_slides is not None else MIN_SLIDES
    hi = max_slides if max_slides is not None else MAX_SLIDES
    if lo < 1 or hi < lo:
        raise ValueError(f"invalid slide range [{lo}..{hi}]")
    if slide_count is not None and slide_count < 1:
        raise ValueError(f"slide_count must be >= 1 (got {slide_count})")

    user_prompt = build_director_user_prompt(
        post_text=post_text,
        brand_name=brand_name,
        brand_color=brand_color,
        aspect_ratio=aspect_ratio,
        slide_count=slide_count,
        min_slides=lo,
        max_slides=hi,
    )

    api_kwargs: dict[str, Any] = {
        "model": DIRECTOR_MODEL,
        "messages": [
            {"role": "system", "content": DIRECTOR_SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    if _is_gpt5_family(DIRECTOR_MODEL):
        api_kwargs["max_completion_tokens"] = DIRECTOR_MAX_TOKENS
        api_kwargs["reasoning_effort"]      = DIRECTOR_REASONING_EFFORT
    else:
        api_kwargs["max_tokens"]  = DIRECTOR_MAX_TOKENS
        api_kwargs["temperature"] = DIRECTOR_TEMPERATURE
        api_kwargs["top_p"]       = DIRECTOR_TOP_P

    t0 = time.monotonic()
    resp = _client().chat.completions.create(**api_kwargs)
    elapsed = round(time.monotonic() - t0, 2)

    raw = (resp.choices[0].message.content or "").strip()

    input_tok = output_tok = reasoning_tok = 0
    usage_info = ""
    try:
        u = resp.usage
        if u:
            input_tok  = int(getattr(u, "prompt_tokens", 0) or 0)
            output_tok = int(getattr(u, "completion_tokens", 0) or 0)
            details = getattr(u, "completion_tokens_details", None)
            rsn = getattr(details, "reasoning_tokens", None) if details else None
            if rsn is not None:
                reasoning_tok = int(rsn or 0)
            usage_info = (
                f" tokens=in:{input_tok}/out:{output_tok}"
                + (f"/reasoning:{reasoning_tok}" if rsn is not None else "")
            )
    except Exception:
        pass

    target_str = f"forced={slide_count}" if slide_count is not None else f"range=[{lo}..{hi}]"
    logger.info(
        f"[html_director] produced {len(raw)} chars in {elapsed}s "
        f"model={DIRECTOR_MODEL} {target_str}{usage_info}"
    )

    if not raw:
        raise RuntimeError(
            f"HTML Director ({DIRECTOR_MODEL}) returned empty output. "
            f"Bump CAROUSEL_HTML_DIRECTOR_MAX_TOKENS (current={DIRECTOR_MAX_TOKENS})."
        )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"HTML Director returned invalid JSON: {exc}. First 200 chars: {raw[:200]!r}"
        ) from exc

    _validate(parsed, lo=lo, hi=hi, forced=slide_count)

    actual_n = len(parsed["slides"])
    logger.info(
        f"[html_director] director chose {actual_n} slides "
        f"(roles: {[s.get('role') for s in parsed['slides']]})"
    )

    parsed["_director_time_s"]           = elapsed
    parsed["_director_model"]            = DIRECTOR_MODEL
    parsed["_director_input_tokens"]     = input_tok
    parsed["_director_output_tokens"]    = output_tok
    parsed["_director_reasoning_tokens"] = reasoning_tok

    return parsed
