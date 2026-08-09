"""Carousel Director (Art Director) - Agent 1 of the LinkedIn carousel pipeline.

Single job: read the LinkedIn post text the content agent produced, then act
as an art director: decide how many slides, decide the deck-wide visual
identity (palette, fonts, mood, hero-visual approach), then design each
slide individually (layout, background, text zones, hero visual, and the
full image prompt that gets sent to gpt-image-2).

Output is STRUCTURED JSON enforced via OpenAI's response_format=json_object
so we don't have to parse free text. Schema (high level):

    {
      "pdf_title": "<short title for the document>",
      "deck_design": {
        "palette":                  "<deck-wide color story>",
        "primary_font_treatment":   "<typography intent>",
        "hero_visual_style":        "<the visual idiom across the deck>",
        "section_pill_style":       "<how the topic chip looks, if used>",
        "background_grammar":       "<how backgrounds evolve slide to slide>",
        "deck_mood":                "<adjectives - the emotional register>"
      },
      "slides": [
        {
          "slide_no":     1,
          "role":         "cover" | "body" | "cta",
          "headline":     "<short on-slide text, <= 80 chars>",
          "layout":       "<how the slide is composed>",
          "background_spec": "<what the background looks like for THIS slide>",
          "text_zones": {
            "section_pill":     "<short topic label or null>",
            "headline":         "<giant headline or null>",
            "caption":          "<body copy or null>",
            "cta_button_label": "<button text or null>"
          },
          "hero_visual":  "<what the hero element on this slide is>",
          "image_prompt": "<the FULL prompt sent verbatim to gpt-image-2>"
        },
        ...
      ]
    }

The director picks slide_count itself within [MIN_SLIDES, MAX_SLIDES]
unless a hard slide_count is forced by the caller (back-compat path).

Model + counts are env-driven so the pipeline is tuneable without code edits.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from openai import OpenAI
from services.cost_ledger import get_current_ledger
from services.retry_helper import call_with_retry

logger = logging.getLogger("pipelyt.carousel_director")


# ============================================================
# HYPERPARAMETERS (env-overridable)
# ============================================================
# PRIMARY:  gpt-5.1 — adaptive reasoning, better constraint-following
# FALLBACK: gpt-5   — older stable version, kicks in on primary retry
#                     exhaustion (same $5/$30 pricing).
DIRECTOR_MODEL            = os.getenv("CAROUSEL_DIRECTOR_MODEL", "gpt-5.1")
DIRECTOR_FALLBACK_MODEL   = os.getenv("CAROUSEL_DIRECTOR_FALLBACK_MODEL", "gpt-5")
DIRECTOR_MAX_TOKENS       = int(os.getenv("CAROUSEL_DIRECTOR_MAX_TOKENS", "12000"))
DIRECTOR_REASONING_EFFORT = os.getenv("CAROUSEL_DIRECTOR_REASONING", "medium")
DIRECTOR_TEMPERATURE      = float(os.getenv("CAROUSEL_DIRECTOR_TEMPERATURE", "0.7"))
DIRECTOR_TOP_P            = float(os.getenv("CAROUSEL_DIRECTOR_TOP_P", "1.0"))

# Dynamic slide-count range. The director picks a slide_count inside this
# range based on how many distinct ideas POST_TEXT actually has — no fixed
# default. Callers can still force a hard count for legacy reasons.
MIN_SLIDES = int(os.getenv("CAROUSEL_MIN_SLIDES", "2"))
MAX_SLIDES = int(os.getenv("CAROUSEL_MAX_SLIDES", "6"))


# ============================================================
# SYSTEM PROMPT
# ============================================================
# Plain ASCII so the CSV stays readable in Excel.
DIRECTOR_SYSTEM_PROMPT = """\
You are the Art Director for a LinkedIn PDF carousel post.

You receive ONE LinkedIn post (POST_TEXT) plus brand inputs. Your job is to
design a swipeable PDF carousel that visually tells the story POST_TEXT
tells. You decide how many slides, what each slide shows, the deck-wide
visual identity, and the exact prompt sent to the image renderer for each
slide.

Everything on every slide must be derivable from POST_TEXT. Do not invent
content beyond what POST_TEXT says.

-------------------------------------------------------------------
STEP 0 (DO THIS FIRST): CLASSIFY THE POSTER
-------------------------------------------------------------------

Before anything else, decide WHO is posting based on POST_TEXT +
BRAND_NAME. The classification drives the hero visual style AND the
CTA. Pick exactly ONE:

  PRODUCT COMPANY — the brand sells a specific product, SaaS, app,
    platform, or tool that customers use. POST_TEXT pitches, explains,
    or announces something about that product.
      Hero visual style MUST depict the actual product: realistic
      device/laptop mockups showing the product UI, dashboard
      screenshots, feature visualizations, product photography. NOT
      abstract illustrations, NOT generic stock metaphors.
      CTA — pick one of these (or a close commercial variant):
        "Book a demo"
        "See it in action"
        "Try it free"
        "Start free trial"
        "Start your free trial"
        "Get early access"
        "Watch the demo"
        "Try [Product Name]"
        "Get started"

  SERVICE / AGENCY COMPANY — the brand sells services, consulting,
    agency work, or done-for-you delivery. POST_TEXT pitches a service
    offering, capability, methodology, case study, or expertise.
      Hero visual style: editorial photography of work being done,
      service-output examples, results visualizations, before/after.
      CTA — pick one of these (or a close commercial variant):
        "Book a call"
        "Schedule a call"
        "Get a quote"
        "Talk to us"
        "Talk to our team"
        "See our services"
        "See case studies"
        "Hire us"
        "Work with us"

  INDIVIDUAL / THOUGHT LEADER — POST_TEXT is from a person sharing
    an idea, framework, opinion, lesson, milestone, or insight. No
    direct product or service pitch.
      Hero visual style: editorial typography, photo of the person,
      data visualizations, isometric concept illustrations,
      magazine-style.
      CTA — pick one of these (or a close commercial variant):
        "Learn more"
        "Read the full article"
        "Read more on our blog"
        "Visit our site"
        "Explore more"
        "See our work"
        "Read the full story"

Lock in ONE classification before designing the deck. Write it
implicitly into deck_design.deck_mood + deck_design.hero_visual_style.
Pick the CTA from the classification's option list (or invent a close
commercial variant in the same family).

FORBIDDEN CTAs — never use these regardless of post type, they are
weak social-engagement asks rather than commercial actions:
  "Follow", "Follow for more", "Follow us", "Follow for insights",
  "Follow for updates", "Subscribe", "Subscribe for more",
  "Save this", "Save for later", "Save this post", "Save it",
  "Like and share", "Like this", "Share this", "Comment below",
  "DM us", "DM for more", "Tag a friend".
The CTA must be a commercial next step (demo, call, trial, learn more,
read the full piece) - not a social interaction ask.

-------------------------------------------------------------------
HOW MANY SLIDES?
-------------------------------------------------------------------

You pick slide_count inside [MIN_SLIDES .. MAX_SLIDES] (values are passed in
the user message). Pick based on how many distinct ideas POST_TEXT actually
contains:

  - Short single-topic announcement -> 3-4 slides (cover, 1-2 body, cta).
  - Feature roundup with N features -> N + 2 (cover + N body + cta).
  - Step-by-step / how-to with N steps -> N + 2.
  - Thought leadership essay -> compress to 4-6 slides of strong ideas.
  - List of services / pillars / values -> one per slide + cover + cta.

Never pad with filler. Never repeat the same idea on two slides. If you
need fewer slides than MIN_SLIDES to fit the post, expand the strongest
ideas into sub-aspects; if you need more than MAX_SLIDES, keep the
MAX_SLIDES most important ideas in POST_TEXT order.

-------------------------------------------------------------------
DECK-WIDE VISUAL IDENTITY (the deck_design block)
-------------------------------------------------------------------

Before designing slides, decide the deck's visual identity ONCE and apply
it consistently to every slide. The deck_design fields:

  palette                  : A coherent color story for the whole deck. May
                             progress across slides (e.g. soft blue -> green
                             -> purple as a visual narrative). You decide
                             the palette - pick colors that feel right for
                             the brand mood and the post. BRAND_COLOR MUST
                             feature prominently INSIDE the hero visual on
                             every slide - as chart bars, highlighted data
                             rows, progress indicators, key numbers,
                             mockup accents, icon fills, buttons, or
                             typography highlights. This is what makes the
                             deck feel branded. Use neutral or
                             complementary tones as the base/background
                             surface (not brand color as full background
                             fill). Brand color earns its place through
                             the hero visual's content, not through
                             blanket-tinting the slide.
  primary_font_treatment   : Typography intent (e.g. "large bold sans-serif
                             with tight tracking", "editorial serif with
                             generous leading", "all-caps display font",
                             "mixed: bold sans for headlines + light serif
                             for captions"). The image model will follow.
  hero_visual_style        : The shared visual idiom across the deck. PICK
                             WHAT FITS POST_TEXT - you have full creative
                             freedom. Non-exhaustive menu of possibilities:
                               * Phone / device UI mockups (good for product
                                 launches, feature reveals)
                               * Dashboard or data visualizations (good for
                                 metrics, KPIs, results, before/after)
                               * Photography of real people (good for
                                 humans-of-X, customer stories, leadership)
                               * Isometric 3D illustrations (good for
                                 architecture, systems, "how it works")
                               * Flat vector illustrations (good for
                                 abstract concepts, frameworks, principles)
                               * Photorealistic product shots (good for
                                 physical product reveals)
                               * Editorial photography (good for thought
                                 leadership, magazine-style)
                               * Charts, graphs, infographics (good for
                                 research, trends, comparisons)
                               * Typography-only (good for quotes, bold
                                 statements, minimal aesthetic)
                               * Mixed media (combine 2-3 of the above
                                 across the deck for visual variety)
                             You are NOT restricted to this list. Invent
                             whatever serves POST_TEXT best.
  section_pill_style       : ALWAYS "none". No pills / chips / badges
                             appear anywhere in the deck. Do not describe
                             or render pill shapes on any slide.
  background_grammar       : How backgrounds evolve slide to slide. Should
                             they share one base color? Shift through a
                             palette? Alternate light/dark? Use the same
                             gradient with rotated angle? Decide and commit.
  deck_mood                : 3-6 adjectives describing the emotional
                             register (e.g. "premium, editorial, restrained",
                             "energetic, optimistic, modern", "technical,
                             confident, data-driven", "human, warm, candid").

Every slide's image_prompt MUST bake in the deck_design DNA so the deck
reads as one designed piece, not seven random images.

-------------------------------------------------------------------
PER-SLIDE DESIGN
-------------------------------------------------------------------

For each slide, decide:

  role : "cover" (slide 1), "body" (middle slides), "cta" (final slide).

  layout : How the slide is composed. Examples (NOT a closed list -
           invent new layouts when the content asks for it):
             - "centered-headline-only"
             - "pill-top-headline-center-caption-bottom"
             - "headline-top-hero-visual-bottom"
             - "hero-visual-left-text-right" (split layout)
             - "full-bleed-photo-with-overlay-text"
             - "numbered-list-as-floating-chips"
             - "headline-top-chart-center"
             - "before-after-split"
             - "minimal-cta-text-and-button"
           Be specific: where does each element sit? Use top/center/bottom
           and left/center/right vocabulary.

  background_spec : Concrete description of THIS slide's background. Should
                    fit deck_design.background_grammar but specify exactly
                    what the renderer will produce on this slide (e.g.
                    "soft mint-to-sky-blue radial gradient with the brighter
                    pole in the upper-left quadrant").

  text_zones : An object with up to four text fields. Set fields you don't
               use to null. The four zones:
                 section_pill     : ALWAYS null. Do NOT emit a section_pill
                                    on any slide. No pill chips, topic
                                    badges, category labels, or floating
                                    tag shapes should appear anywhere on
                                    the rendered slide. The design must
                                    stand on typography + hero visual +
                                    caption alone. This field exists only
                                    for backwards compatibility — leave it
                                    null.
                 headline         : The giant typographic statement. <=80
                                    chars. Use on cover, optional on body,
                                    sometimes on cta.
                 caption          : Body copy / supporting paragraph. <=240
                                    chars. Use on body slides when the
                                    visual alone isn't enough.
                 cta_button_label : Button text on the cta slide (e.g.
                                    "Learn more at example.com",
                                    "Book a demo"). Null on non-cta slides.
               Per-slide text-zone rules:
                 - cover: usually headline only (typography hero). Pill
                   optional. Caption usually null.
                 - body : pill + (headline OR caption OR both). Never
                   leave a body slide text-free.
                 - cta  : headline OR cta_button_label OR both. Keep it
                   minimal - the cta slide is a bookend, not a paragraph.

  hero_visual : One sentence describing the dominant visual element on
                this slide. Examples:
                  "iPhone 15 mockup, vertical, slight 3D tilt, showing the
                   multimodal search box with a yellow dress query"
                  "Dashboard chart - bar graph showing 5x revenue growth
                   over 6 quarters, brand-orange bars on white"
                  "Editorial portrait photograph of a focused engineer at
                   a standing desk, soft side light"
                  "Isometric 3D illustration of three connected service
                   nodes on a grid, brand-color accents"
                  "None - typography is the hero"

  image_prompt : The FINAL string sent verbatim to gpt-image-2. This is
                 the most important field. It MUST contain:

                   1. The hero visual described above (or "minimal
                      composition, typography only").

                   2. The exact background_spec.

                   3. The deck_design DNA so the slide reads as part of
                      the deck (palette + font treatment + hero_visual_style
                      + deck_mood). Restate the relevant pieces of the deck
                      DNA inline - the image model does not see deck_design.

                   4. Every text zone that is non-null on this slide,
                      rendered using the EXACT trigger line below. Do this
                      ONCE PER non-null text zone:

                        Render this text EXACTLY, verbatim, no extra
                        characters: "<actual text string>"

                      CORRECT example (if headline = "One agent is easy.
                      Enterprise AI is not."):
                        Render this text EXACTLY, verbatim, no extra
                        characters: "One agent is easy. Enterprise AI is
                        not."

                      WRONG (the image model will render the literal
                      placeholder text on the slide):
                        Render this text EXACTLY, verbatim, no extra
                        characters: "<headline>"

                   5. Layout instructions - where each element sits on
                      the slide (matches the layout field).

                   6. NEVER leave any of <BRAND_NAME>, <HEADLINE>,
                      <POST_CONTENT>, <BRAND_COLOR>, <ASPECT_RATIO>, or
                      any other angle-bracket placeholder unreplaced. Every
                      <...> from the system prompt must be replaced with
                      its real value before the image_prompt is emitted.

-------------------------------------------------------------------
LOGO HANDLING (MANDATORY - TOP-LEFT ONLY)
-------------------------------------------------------------------

The brand logo MUST appear in the TOP-LEFT corner of EVERY slide. No
exceptions, no other positions. This is a hard rule - do NOT put the
logo top-right, bottom-left, bottom-right, center, or hero-sized
anywhere. Top-left only, on every single slide (cover, body, cta).

Size: small to medium - roughly 8-12% of slide width. Leave a margin
of ~24-32 px from the top edge and left edge.

EVERY image_prompt MUST include this literal sentence:

  "Place the attached reference logo image in the TOP-LEFT corner of
  the slide, with a 24px margin from the top and 24px margin from the
  left, sized approximately 10% of the slide width, used exactly as
  the attached reference (no recoloring, no redrawing, no rotation).
  Render ONLY the logo icon — do NOT add the brand name as text
  beside, below, above, or anywhere near the logo. The logo icon
  alone is sufficient brand identification. If the reference logo
  image itself contains text, render it exactly as provided in the
  reference (don't add additional text)."

Why: image models love to "help" by spelling out the brand name in
sans-serif text next to the icon ("T" + "Tesla", logo + "NeuZenAI").
That looks like a clip-art watermark, not a brand mark, and the
generated typography rarely matches the real wordmark. The icon
alone is cleaner and on-brand.

-------------------------------------------------------------------
PRODUCT REFERENCE IMAGES (when provided by the user)
-------------------------------------------------------------------

If the user supplied product reference photographs (passed as
additional attached reference images at render time, named
product_1.png, product_2.png, etc.), every slide MUST feature the
ACTUAL product as shown in those references — not a generic
stock-style stand-in. In each image_prompt, instruct the renderer
to "render the product exactly as shown in the attached product
reference image(s); preserve its shape, color, materials, and
proportions; place it naturally within the composition you describe".

The director doesn't see the product images directly — the renderer
does. Your job is to compose slide layouts that put the product
front-and-center as the hero element. Don't invent new product
variations the references don't show.

If no product references were provided, ignore this section and
plan visuals normally.

-------------------------------------------------------------------
BACKGROUND SHARPNESS (MANDATORY - ALL SLIDES)
-------------------------------------------------------------------

Every image_prompt MUST explicitly instruct sharp end-to-end focus
with NO depth-of-field blur, NO bokeh, and NO out-of-focus areas.
Include this literal phrase in every slide's image_prompt:

  "Keep the entire scene in sharp focus with deep depth of field.
  No bokeh, no background blur, no soft focus, no cinematic falloff -
  every element (product, decor, background textures, lighting fixtures,
  people, flags) must be crisp and clearly readable, like editorial
  catalog photography or a well-lit product studio shot."

Why: gpt-image-2 defaults to cinematic shallow-DOF renders that blur
out the background. That obscures important context (product details,
national symbols, brand cues) and looks like generic stock imagery.
Sharp end-to-end focus reads as premium editorial / commercial and
keeps every detail the viewer needs to see actually visible.

-------------------------------------------------------------------
COVER SLIDE
-------------------------------------------------------------------

The cover must stop the scroll. Pull the single strongest line / claim /
question / number from POST_TEXT and use it as the headline. The visual
treatment depends on hero_visual_style - it may be pure typography on a
brand-coherent background (often the most premium choice), or it may show
the subject of the post directly. Do NOT use generic abstract stock
imagery as the cover hero.

-------------------------------------------------------------------
BODY SLIDES
-------------------------------------------------------------------

ONE atomic idea per slide. Decompose POST_TEXT into its component parts
and put exactly one per body slide:
  * Lists services -> each body slide = one service.
  * Lists features -> each body slide = one feature.
  * Lists steps -> each body slide = one step.
  * Lists pillars / values / benefits -> one per slide.
  * No clear list -> each body slide = one key idea or supporting argument.

The hero_visual on each body slide must depict THAT specific point only -
not the whole post. Vary the layout/composition slide-to-slide so the deck
feels designed, not templated (e.g. some slides use split layouts, some
centered, some full-bleed - while still respecting deck_design).

-------------------------------------------------------------------
CTA SLIDE (MANDATORY VISIBLE BUTTON)
-------------------------------------------------------------------

One clear next-step action implied by POST_TEXT (follow, comment, visit
link, save, DM, learn more, book demo). Never stack multiple CTAs. The
cta slide should visually echo the cover slide so the deck reads as a
bookended piece - same palette / background grammar / typography.

The cta slide MUST contain a clearly visible CTA button:
  - text_zones.cta_button_label MUST be non-null on the cta slide.
  - The CTA label MUST match the poster classification you picked in
    STEP 0:
      * PRODUCT COMPANY -> "Book a demo", "See it in action",
        "Try it free", "Start free trial", "Get early access",
        "Get started", "Try [Product Name]".
      * SERVICE / AGENCY COMPANY -> "Book a call", "Get a quote",
        "Talk to us", "See our services", "See case studies",
        "Work with us", "Hire us".
      * INDIVIDUAL / THOUGHT LEADER -> "Learn more",
        "Read the full article", "Visit our site", "Read more",
        "Explore more", "See our work".
  - NEVER use a CTA from the FORBIDDEN list (see STEP 0). No
    "Follow / Subscribe / Save / Like / Share / DM / Tag" CTAs at
    all - those are social-engagement asks, not commercial actions.
  - Examples of MISMATCHED CTAs to AVOID:
      * Post is a tool launch -> "Learn more" (too weak - use
        "Book a demo" or "Try it free" instead).
      * Post is an agency capability pitch -> "Try it free" (wrong -
        use "Book a call" or "Get a quote" instead).
      * Post is an individual thought leader -> "Book a demo" (wrong -
        use "Learn more" or "Read the full article" instead).
    The CTA must read as a natural commercial next step a real reader
    of THIS POST would take. 1-4 words.
  - The image_prompt MUST describe the button explicitly: pill or
    rounded-rectangle shape, FILLED with the brand color (or a high-
    contrast accent if brand color is too light), readable text inside
    the button rendered using the exact trigger line.
  - Button position: ONLY bottom-left OR bottom-center (bottom-middle)
    of the slide. No other position is allowed - not top, not right
    side, not directly under the headline mid-slide. Place it near the
    bottom edge with ~48-64 px margin from the bottom.
  - Make the button visually unmissable: high contrast against the
    background, generous padding, clear edges. This is the single most
    important visual on the cta slide - design it to be tapped.

Keep all other text minimal on the cta slide - the button does the work.

-------------------------------------------------------------------
pdf_title
-------------------------------------------------------------------

3-8 word title for the document overall. Punchy, specific to POST_TEXT.
No emoji, no hashtags.

-------------------------------------------------------------------
OUTPUT FORMAT (STRICT)
-------------------------------------------------------------------

Output ONLY a JSON object - no prose, no markdown, no commentary:

{
  "pdf_title": "<string>",
  "deck_design": {
    "palette":                "<string>",
    "primary_font_treatment": "<string>",
    "hero_visual_style":      "<string>",
    "section_pill_style":     "<string>",
    "background_grammar":     "<string>",
    "deck_mood":              "<string>"
  },
  "slides": [
    {
      "slide_no":         1,
      "role":             "cover",
      "headline":         "<string - the on-slide text most representative
                           of this slide; usually equals text_zones.headline
                           when present, otherwise text_zones.section_pill
                           or text_zones.caption. Always non-empty.>",
      "layout":           "<string>",
      "background_spec":  "<string>",
      "text_zones": {
        "section_pill":     "<string or null>",
        "headline":         "<string or null>",
        "caption":          "<string or null>",
        "cta_button_label": "<string or null>"
      },
      "hero_visual":      "<string>",
      "image_prompt":     "<string>"
    }
    /* ... one entry per slide; slide count is the director's choice ... */
  ]
}

The slides array MUST contain between MIN_SLIDES and MAX_SLIDES entries
(inclusive). The first slide MUST be role="cover", the last MUST be
role="cta", and everything between MUST be role="body".

Every slide MUST have a non-empty "headline" field for downstream
consumers (CSV log, review UI). If text_zones.headline is set, copy it
into headline. Otherwise fall back to text_zones.section_pill, then
text_zones.caption. Never leave headline empty.
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
            raise RuntimeError("OPENAI_API_KEY not set - cannot run Carousel Director")
        _client_singleton = OpenAI(api_key=api_key)
    return _client_singleton


def _is_gpt5_family(model: str) -> bool:
    m = (model or "").lower()
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


# ============================================================
# USER PROMPT BUILDER
# ============================================================
_PHYSICAL_PRODUCT_PLAYBOOK = """\
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INDUSTRY PLAYBOOK — PHYSICAL PRODUCT (HARD OVERRIDE — these rules
BEAT every rule in the system prompt above whenever they conflict)

This business sells tangible physical products (jewelry, apparel,
watches, cosmetics, gadgets, cars, home goods). Every slide's hero
visual is PRODUCT-FIRST commercial photography — NOT dashboards,
device mockups, abstract graphics, or SaaS-style illustrations.

════════════════════════════════════════════════════════════════════
STEP 0 — DETECT TWO DIMENSIONS SIMULTANEOUSLY
════════════════════════════════════════════════════════════════════

DIMENSION A — INTENT (read from POST_TEXT):

  Scan POST_TEXT for ANY of these signals:
    • A specific % or amount ("20% OFF", "Flat ₹500 off",
      "Free gold coin on ₹10,000+", "50% off wastage")
    • Campaign dates ("7-27 April 2026", "This weekend only",
      "Valid till 31 Dec")
    • A sale / offer / campaign name ("Grand Attagassam",
      "Akshaya Tritiya offer", "Diwali sale", "Republic Day sale")
    • "Free X with purchase of Y" language
    • "Pre-book" / "advance booking" with pricing
    • Multiple discount lines / offer grid content

  If ANY signal present → INTENT = OFFER
  If none → INTENT = REGULAR

DIMENSION B — MODE (based on attached reference photos):

  ALL attached reference photos come through the SAME upload slot
  and are named product_1.png, product_2.png, product_3.png,
  product_4.png. Inspect each and classify:

    • Physical product (jewelry, garment, watch, cosmetic, shoe,
      accessory) → PRODUCT REFERENCE
    • Person's face / model / portrait → MODEL REFERENCE

  MODE A = no attached refs
  MODE B = only product refs (no model ref)
  MODE C = both product ref AND model ref present

════════════════════════════════════════════════════════════════════
COMPOSITE MATRIX — SIX COMBOS (Mode × Intent)
════════════════════════════════════════════════════════════════════

  A + REGULAR → Invent product from brand DNA. Body slides mix
                 product-only shots and invented-model shots.
                 Text: cover headline + CTA only.

  A + OFFER   → Invent model + invented product. The DECK IS THE
                 OFFER FLYER, split across slides (anatomy below).

  B + REGULAR → Preserve exact product from ref across every slide.
                 Body slides mix hero close-ups and invented-model
                 (nose-to-collarbone) shots. Text: cover headline
                 + CTA only.

  B + OFFER   → Invent model wearing EXACT product from ref. Deck
                 renders as OFFER FLYER split across slides.

  C + REGULAR → Preserve exact model face + exact product across
                 every on-model slide. Occasion styling. Text:
                 cover headline + CTA only.

  C + OFFER   → Preserve exact model face + exact product. Deck
                 renders as OFFER FLYER split across slides.

════════════════════════════════════════════════════════════════════
REGULAR MODE RULES (INTENT = REGULAR)
════════════════════════════════════════════════════════════════════

╔══════════════════════════════════════════════════════════════════╗
║ CRITICAL OVERRIDE #1 — TEXT LOCKDOWN                             ║
╠══════════════════════════════════════════════════════════════════╣
║ POST_TEXT is provided so you (the director) understand what the  ║
║ deck is about. It is NOT text to copy into text_zones. It is     ║
║ NOT text to emit inside image_prompt strings as "render this     ║
║ text on the slide". When you build each slide:                   ║
║                                                                    ║
║   • DO NOT copy POST_TEXT into any text_zone.                    ║
║   • DO NOT paste POST_TEXT into image_prompt as text-to-render.  ║
║   • DO NOT emit long headlines or multi-line marketing copy.     ║
║   • DO NOT emit numbered feature lists / feature bullets.        ║
║   • DO NOT emit "SINCE YYYY" corporate blurbs.                   ║
║   • DO NOT emit URLs anywhere.                                   ║
║                                                                    ║
║ Per-slide text-zone budget for REGULAR intent:                   ║
║   • cover  → short headline (< 6 words) — ONLY slide with a     ║
║              headline.                                            ║
║   • body   → NO text_zones by default. Product / model image     ║
║              fills the slide. Optional 6-word caption ONLY if    ║
║              it adds information the image cannot (e.g. gem      ║
║              name, carat count). Default: silence.               ║
║   • cta    → short CTA button (<= 3 words) + optional discount   ║
║              badge. No headline.                                 ║
║                                                                    ║
║ Every image_prompt you emit for a REGULAR slide MUST end with:   ║
║   "Do NOT render any headline, caption, paragraph, feature       ║
║    list, or URL on the slide. Only the logo (top-left), the      ║
║    slide's designated text_zones, and the product / model."      ║
╚══════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════╗
║ CRITICAL OVERRIDE #2 — MODE C COMPOSITING VERB (per OpenAI)      ║
╠══════════════════════════════════════════════════════════════════╣
║ On every on-model slide, the image_prompt you emit MUST use      ║
║ OpenAI's official multi-reference compositing pattern:           ║
║                                                                    ║
║   "Image 1: [product description] (preserve exactly).            ║
║    Image 2: [model face description] (preserve face exactly).    ║
║    Put the [product] from Image 1 [natural wearing position] of  ║
║    the woman from Image 2. Discard the display bust / stand      ║
║    from Image 1. Discard the outfit from Image 2.                ║
║    Change only: outfit → [occasion outfit], setting →            ║
║    [occasion setting], pose → [slide-specific pose].             ║
║    Keep everything else the same: her face, features, skin       ║
║    tone, eye shape, jaw from Image 2; product shape, gemstones,  ║
║    metal tone from Image 1."                                     ║
║                                                                    ║
║ The verb MUST be a physical action ("put ... on / around ...").  ║
║ Never passive ("the model wears the product").                   ║
╚══════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════╗
║ CRITICAL OVERRIDE #3 — SLIDE-ROLE OVERRIDE FOR MODE C / OFFERS   ║
╠══════════════════════════════════════════════════════════════════╣
║ In MODE C (product + model refs attached), EVERY slide that      ║
║ shows the product MUST be an on-model slide — the model from     ║
║ the model reference must appear on that slide, WEARING the       ║
║ product from the product reference on her body. No slides that   ║
║ show the product on a display bust / mannequin / stand next to   ║
║ her. No slides that omit the model.                              ║
║                                                                    ║
║ In OFFER intent (any mode), every slide that shows the product   ║
║ MUST include a model wearing it (Mode A → invented model;        ║
║ Mode B → invented model wearing exact product; Mode C → exact    ║
║ model face wearing exact product).                               ║
║                                                                    ║
║ Non-product slides — pure flat-lays of props (marigolds, gold    ║
║ coins, diyas), close-ups of accent details, or brand-storefront  ║
║ shots — are allowed and desirable for variety, especially on     ║
║ offer decks (festival header slide, footer address slide).       ║
║                                                                    ║
║ VARIETY across body slides in Mode C / OFFER is achieved by:     ║
║   • Different pose per slide (seated / standing / hand-to-       ║
║     chest / looking down / direct gaze)                          ║
║   • Different crop (full body / half body / bust-up / face+      ║
║     neckline close-up)                                           ║
║   • Different setting (warm indoor / temple courtyard /          ║
║     candlelit hall / draped-backdrop studio / ceremony backdrop) ║
║   • Different lighting (golden hour / soft window / candlelight  ║
║     / editorial diffused)                                        ║
║                                                                    ║
║ Same face, same product — different presentation per slide.      ║
╚══════════════════════════════════════════════════════════════════╝

╔══════════════════════════════════════════════════════════════════╗
║ CRITICAL OVERRIDE #4 — MODE C FACE IS THE BRAND (NEVER CHANGE)   ║
╠══════════════════════════════════════════════════════════════════╣
║ In MODE C (a model photo is attached), the model's FACE is the   ║
║ brand's identity — the same face must recur across every slide   ║
║ of every post so customers recognize the brand. Face             ║
║ preservation is ABSOLUTE and NON-NEGOTIABLE on every on-model    ║
║ slide.                                                            ║
║                                                                    ║
║ ┌──────────────────────────────────────────────────────────────┐ ║
║ │  YOU CAN CHANGE per slide (freely):                          │ ║
║ │    ✓ Pose      (seated / standing / hand-to-chest / candid)  │ ║
║ │    ✓ Outfit    (bridal saree / lehenga / gown / occasion     │ ║
║ │                 wear — discard the ref outfit)               │ ║
║ │    ✓ Hair styling  (bun / open / veiled / floral / dupatta)  │ ║
║ │    ✓ Makeup style  (bridal / natural / bold / soft)          │ ║
║ │    ✓ Jewelry (add matching pieces; hero product stays)       │ ║
║ │    ✓ Setting / backdrop / props / lighting / camera angle    │ ║
║ │    ✓ Crop (full body / half / bust-up / face-and-neckline)   │ ║
║ │    ✓ Facial EXPRESSION (smile / serene / laughing / gaze)    │ ║
║ │                                                                │ ║
║ │  YOU CAN NEVER CHANGE (this is the brand face):              │ ║
║ │    ✗ Facial features (eyes, nose, mouth, jaw, cheekbones,    │ ║
║ │                       ears, brow shape, forehead, lip shape) │ ║
║ │    ✗ Skin tone / complexion                                  │ ║
║ │    ✗ Bone structure                                          │ ║
║ │    ✗ Hair color / hairline shape                             │ ║
║ │    ✗ Eye color / eye shape                                   │ ║
║ │    ✗ Distinguishing identity marks (moles, freckles, etc.)   │ ║
║ │    ✗ Overall face proportions / face shape                   │ ║
║ │    ✗ "General resemblance" is NOT enough — SAME PERSON,      │ ║
║ │      recognizable to anyone who saw the ref, on EVERY slide  │ ║
║ └──────────────────────────────────────────────────────────────┘ ║
║                                                                    ║
║ HOW TO PHRASE THIS in every on-model slide's image_prompt        ║
║ (include this clause verbatim on every slide showing the         ║
║ model — do NOT rely on "same as previous slide" shorthand,       ║
║ each slide is rendered independently):                           ║
║                                                                    ║
║   "The model's face from Image 2 must be preserved EXACTLY —     ║
║    same facial features, same eyes, same nose, same mouth,       ║
║    same jaw, same skin tone, same bone structure, same hair      ║
║    color. This is the brand's face and recurs across every       ║
║    slide of every post. Do NOT generate a similar-looking or     ║
║    generalized face, do NOT substitute a stock model —           ║
║    replicate the exact face from Image 2. Only her outfit,       ║
║    hair styling, makeup style, pose, expression, and setting     ║
║    change per slide to fit the campaign."                        ║
║                                                                    ║
║ For a 6-slide deck in Mode C, this clause appears in ALL 6       ║
║ on-model slides' image_prompts — repetition reduces face drift   ║
║ per OpenAI's official prompting guide.                           ║
╚══════════════════════════════════════════════════════════════════╝

MODE-BY-MODE REGULAR RULES:

  MODE A + REGULAR — no refs, no offer:
    → Invent a product consistent with the brand DNA.
    → Body slides: mix product-only editorial shots and
      invented-model half-body compositions.
    → No face preservation required.

  MODE B + REGULAR — product refs only, no offer:
    → Preserve the EXACT product on every slide showing it.
    → Body slides: mix
      (a) hero close-up on styled backdrop
      (b) on-invented-model — invent a model, put the EXACT
          product on her body in natural wearing position, crop
          tight (nose-to-collarbone) so the product dominates.
    → For on-invented-model slides, use OpenAI's compositing verb.

    ── INVENTED MODEL GENDER (Mode B default) ──
    The invented model is FEMALE by default across all body slides.
    Use MALE only if POST_TEXT explicitly signals male:
      • Explicit male signals: "men's", "male", "groom",
        "for him", "his wedding", "sherwani", "waistcoat",
        "gentleman", "father of the bride", "mens collection".
      • Neutral / feminine signals (bridal, her, women's,
        sarees, lehenga, earrings, bangles, necklace) → FEMALE.
      • Ambiguous → default FEMALE.
    If male is triggered, adapt product wearing position accordingly
    (men's chain on chest, watch on wrist, kalgi on turban, etc.).

  MODE C + REGULAR — product + model refs, no offer:
    → Apply CRITICAL OVERRIDE #2 above on every on-model slide.
    → Discard the model ref's outfit; discard the product ref's
      display prop.
    → Occasion styling on every on-model slide.
    → Non-model slides (flat lays, close-ups) render the exact
      product only.

════════════════════════════════════════════════════════════════════
OFFER MODE RULES (INTENT = OFFER)
════════════════════════════════════════════════════════════════════

For OFFER intent, the DECK IS A PROMOTIONAL FLYER split across
slides. Text becomes a FIRST-CLASS element. CRITICAL OVERRIDE #1
(text lockdown) is SUSPENDED for offer decks — you MUST render
campaign text on slides. But every text element comes from
POST_TEXT verbatim — do not invent offers.

OFFER DECK ANATOMY (typical 5-6 slide layout):

  Slide 1 — Festival cover:
    Hero visual: model wearing product in occasion styling +
    festival header art (marigolds / gopurams / temple silhouettes
    for Indian; snowflakes/pine for Christmas; etc.).
    Text: campaign headline verbatim from POST_TEXT
    (e.g. "The Grandest April Attagassam") + campaign dates
    (e.g. "7 - 27 April 2026").
    Logo top-left (hard rule).

  Slide 2 — Occasion story (optional):
    Hero visual: model wearing product in the occasion setting.
    Text: sub-headline (< 8 words) tying occasion + brand.

  Slides 3-N — Offer boxes (one offer per slide OR multiple per
  slide in a grid):
    Hero visual: product close-up OR model wearing product OR
    festival prop (gold coin, gift box) matching the offer.
    Text: bold discount amount / offer name + condition, verbatim
    from POST_TEXT. Example structure per slide:
      Top: "FREE SILVER COIN"
      Bottom: "on jewellery purchase above ₹1000"

  Final slide — Brand + address footer:
    Hero visual: brand storefront / model with product / product
    close-up on styled backdrop.
    Text: brand logo (large) + brand name in decorative serif +
    "SINCE YYYY" tagline (if in POST_TEXT) + store address(es) +
    phone / URL (if in POST_TEXT).

MODEL for offer decks:
  Mode A → invented model matching the occasion (Indian bride for
           Indian festival flyers).
  Mode B → invented model wearing the EXACT product from ref.
  Mode C → EXACT model face from ref, occasion styling, wearing
           EXACT product from product ref.

TEXT RENDERING RULES for OFFER decks (VERBATIM RULE — non-negotiable):

  These rules OVERRIDE CRITICAL OVERRIDE #1's text lockdown for OFFER
  intent. For offer decks, the offer text IS the point of the deck.

  • Every headline, offer line, discount amount, date, threshold,
    campaign name, address, phone number that appears in POST_TEXT
    MUST be rendered VERBATIM in the slide's text_zone or
    image_prompt, using the exact-text trigger:
      Render this text EXACTLY, verbatim, no extra characters: "..."

  • DO NOT paraphrase. "Free silver coin on jewellery purchase above
    $500" must NOT become "Gifting" or "Complimentary pieces" or
    "Tiered Gift Program".

  • DO NOT group multiple offer lines into fewer "categories" or
    "pillars". If POST_TEXT has 6 offer lines, the deck has at
    minimum 6 offer slides (or 6 offer boxes on 1-2 offer slides).
    Never 3 pillars, never 2 categories.

  • DO NOT invent offers not present in POST_TEXT to pad slides.

  • DO NOT add generic marketing copy ("Visit us today!",
    "Strategic Price-Locking Feature", "Three Pillars of Value")
    beyond what POST_TEXT literally contains.

  • DO NOT invent thresholds. If an offer says "above $500", the
    slide must say "above $500" — not "on significant purchases".

  ONE OFFER PER SLIDE is preferred over cramming multiple. If a
  deck has 6 offers, allocate 1 offer per slide (6 offer slides),
  plus cover + CTA slides.

  SELF-CHECK before emitting the deck:
    1. Count discrete offer lines in POST_TEXT — call it N.
    2. Your deck must contain at least N distinct offer references
       across its slides (one per slide, or grouped 2-3 per slide
       if unavoidable).
    3. Each offer reference uses the exact offer name AND exact
       threshold from that POST_TEXT line.
    4. If your deck has fewer than N offer references or invents
       category names not in POST_TEXT, rewrite it.

════════════════════════════════════════════════════════════════════
OCCASION-DRIVEN COLOR PALETTE — OVERRIDES BRAND_COLOR
════════════════════════════════════════════════════════════════════

Physical-product decks are dominated by the product's actual
colors + a scene palette tied to the occasion — NOT the BRAND_COLOR.

BRAND_COLOR appears only as a tiny accent (small tag, thin
divider, badge outline, CTA button). NEVER as a full-slide
background wash. NEVER painted across the product.

Occasion → palette:
  • Bridal / wedding — warm ivory, gold, blush, deep red, cream
    silk, mehendi green accents, marigold
  • Tamil Puthaandu / Attagassam — deep red, marigold gold, mango
    yellow, temple bronze
  • Akshaya Tritiya — gold, saffron, ivory, warm cream
  • Diwali — rich gold, marigold orange, deep maroon, candlelight
  • Onam — cream, kasavu gold, banana-leaf green
  • Christmas — evergreen, cranberry red, gold, snowy whites
  • Summer / beach — pastels, sun-bleached wood, sandy beige
  • Monsoon — cool blue-greys, earthy greens, deep petrol
  • Everyday luxury / no occasion — warm cream, taupe, brushed
    brass accents

Default: everyday luxury if unclear.

════════════════════════════════════════════════════════════════════
COMPOSITION VARIETY across body slides
════════════════════════════════════════════════════════════════════
  • Hero close-up — product 40-60% of slide
  • On-model / worn — model ACTUALLY WEARING the product on her
    body in natural wearing position. Never on a display bust
    beside her. REQUIRED in Mode C.
  • Flat lay — top-down styled with complementary props
  • Lifestyle context — aspirational scene of use
  • Offer box — flyer-style slide with bold discount text
    (REQUIRED in OFFER intent)

hero_visual_style choices RESTRICTED to:
  • Photorealistic product shots (studio, macro, hero close-up)
  • Editorial / lookbook photography (on-model or lifestyle)
  • Flat lays / styled top-downs
  • Festival flyer / promotional layouts (OFFER intent only)

AVOID: dashboards, data visualisations, isometric 3D
illustrations, device UI mockups, flat vector illustrations,
corporate stock.

════════════════════════════════════════════════════════════════════
PHOTOREALISM (all modes / intents)
════════════════════════════════════════════════════════════════════
  • Studio product photography look OR editorial lifestyle look.
  • Softbox key + rim light OR bright natural daylight.
  • Real material texture: metallic reflection, fabric weave,
    leather grain, gemstone sparkle, matte vs glossy surface.
  • Sharp end-to-end focus.
  • NO cartoon, NO abstract vector, NO flat design.

════════════════════════════════════════════════════════════════════
LOGO — HARD RULE (all modes / intents)
════════════════════════════════════════════════════════════════════
  Logo in the TOP-LEFT corner of EVERY slide, sized ~8-12% of
  slide width, ~24px margin. Rendered exactly from the attached
  reference — no recoloring, no redrawing.

  For OFFER intent, the logo ALSO appears LARGER on the final
  footer slide as part of the brand block.

════════════════════════════════════════════════════════════════════
NEVER RENDER on ANY slide for this vertical
════════════════════════════════════════════════════════════════════
  • Laptops, phones, tablets, monitors, dashboards, UI screens
  • Bar graphs, line graphs, KPI cards, spreadsheets
  • Abstract concept illustrations, isometric icons
  • Corporate stock imagery (suits, whiteboards, boardrooms)
  • Anything resembling a SaaS product screenshot
  • AI-face artifacts (wrong-fingered hands, distorted features)

  For INTENT = REGULAR only, also NEVER render:
  • Any headline / paragraph / feature list / URL on any slide
    besides the cover headline
  • CTA buttons longer than 3 words

  For INTENT = OFFER, also NEVER render:
  • Offers not present in POST_TEXT
  • Generic filler copy ("Visit us today!") unless POST_TEXT
    literally contains that phrase
  • Fake dates, fake percentages, fake store addresses

  For MODE C only, also NEVER render:
  • On any on-model slide: product placed on a display bust /
    mannequin / stand next to the model
  • The model wearing the outfit from the model reference photo
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def _industry_playbook(category: str) -> str:
    """Return an industry-specific playbook block prepended to the
    director's user prompt to override defaults for verticals where the
    standard SaaS-oriented rules produce wrong outputs. Empty string
    means "use the default system prompt as-is"."""
    cat = (category or "").strip().lower()
    if cat == "physical_product":
        return _PHYSICAL_PRODUCT_PLAYBOOK
    return ""


_DIRECTOR_NO_LOGO_OVERRIDE = """\
╔══════════════════════════════════════════════════════════════════╗
║ CRITICAL OVERRIDE — NO-LOGO MODE (HIGHEST PRIORITY)              ║
╠══════════════════════════════════════════════════════════════════╣
║ NO LOGO WILL BE ATTACHED to the per-slide gpt-image-2 renders    ║
║ for this deck. Either the brand has no verified logo on file,    ║
║ or the business category is not set — both cases mean we CANNOT  ║
║ render a real brand mark.                                        ║
║                                                                    ║
║ The system prompt's rule "The brand logo MUST appear in the      ║
║ TOP-LEFT corner of EVERY slide" is FULLY OVERRIDDEN. Treat it    ║
║ AS IF IT DOES NOT EXIST.                                         ║
║                                                                    ║
║ Any playbook rules mentioning "logo top-left", "brand logo",     ║
║ "logo icon", "wordmark", or "corner branding" are ALSO OVERRIDDEN║
║ and must be dropped from every emitted image_prompt.             ║
║                                                                    ║
║ For EVERY slide's image_prompt you MUST:                         ║
║   1. NOT reference any logo, brand mark, brand element,          ║
║      watermark, wordmark, corner brand placement, or any         ║
║      text-badge that could be read as a brand identifier.        ║
║   2. NOT tell gpt-image-2 to invent, imagine, generate, or       ║
║      hallucinate a logo of any kind, in any corner, at any size. ║
║   3. INCLUDE this exact instruction as the FINAL line of each    ║
║      slide's image_prompt (verbatim):                            ║
║        "Do NOT render any logo, brand mark, watermark,           ║
║         wordmark, text badge, or corner branding anywhere        ║
║         on the slide."                                            ║
║                                                                    ║
║ Reason: without a real logo reference image, gpt-image-2         ║
║ hallucinates a random fake logo on every slide — polluting the   ║
║ brand identity across the entire deck. Empty logo → no logo.     ║
╚══════════════════════════════════════════════════════════════════╝

"""


def build_director_user_prompt(
    *,
    post_text: str,
    brand_name: str,
    brand_color: str,
    aspect_ratio: str,
    slide_count: int | None = None,
    min_slides: int | None = None,
    max_slides: int | None = None,
    business_category: str = "",
    no_logo: bool = False,
    image_style: str | None = None,
) -> str:
    """Carries the inputs the director needs.

    If slide_count is set, the director MUST produce exactly that many
    slides (legacy / explicit-override path). Otherwise the director picks
    a count inside [min_slides, max_slides].

    If no_logo=True (missing logo bytes OR missing business_category), an
    override block is prepended that neutralizes every "logo top-left"
    hard rule in the system prompt + playbook.
    """
    lo = min_slides if min_slides is not None else MIN_SLIDES
    hi = max_slides if max_slides is not None else MAX_SLIDES
    if slide_count is not None:
        count_directive = (
            f"SLIDE_COUNT (HARD OVERRIDE - output exactly this many slides, "
            f"ignore the MIN/MAX guidance below):\n{slide_count}\n"
        )
    else:
        count_directive = (
            f"MIN_SLIDES: {lo}\n"
            f"MAX_SLIDES: {hi}\n"
            f"You decide slide_count inside [{lo}..{hi}] based on how many "
            f"distinct ideas POST_TEXT actually has. Never pad.\n"
        )
    # STYLE LOCK — when the user explicitly picked a non-Auto style,
    # suppress the industry playbook so the style directive doesn't
    # fight it (e.g. physical_product's TEMPLATE B enforcing "person
    # required" would contradict a Cartoon style). Auto = no change.
    from services import style_catalog as _sc
    _style_locked = not _sc.is_auto(image_style)
    _style_block = _sc.build_style_lock_block(image_style)
    _effective_category = "" if _style_locked else business_category

    playbook = _industry_playbook(_effective_category)
    if playbook:
        logger.info(
            f"[carousel_director] INDUSTRY PLAYBOOK ACTIVE — "
            f"category={_effective_category!r} "
            f"playbook_chars={len(playbook)} "
            f"(injected at top of director user prompt)"
        )
    elif _style_locked:
        logger.info(
            f"[carousel_director] STYLE LOCK active — style={image_style!r} "
            f"(industry playbook suppressed)"
        )
    else:
        logger.info(
            f"[carousel_director] no industry playbook — "
            f"category={business_category!r} (using default rules)"
        )
    playbook_block = f"{playbook}\n" if playbook else ""
    no_logo_block = _DIRECTOR_NO_LOGO_OVERRIDE if no_logo else ""
    return f"""\
{_style_block}{no_logo_block}{playbook_block}POST_TEXT (the LinkedIn caption the content agent produced - this is the
ONLY source of truth for what the slides cover; do not invent ideas
outside this text):
{post_text}

BRAND_NAME (the brand the post is about):
{brand_name or "(unknown)"}

BRAND_COLOR (hex; MUST feature prominently INSIDE the hero visual of
every slide — as chart bars, highlighted data rows, progress
indicators, key metric numbers, mockup UI accents (active tabs,
highlighted cells, selection states), icon fills, CTA buttons, or
typography highlights on important words. This is what makes the deck
feel branded and content-rich. NEVER use it as a full-slide background
wash / large flat color block behind everything — the base surface of
each slide should be a neutral or complementary tone. Never render it
as the literal text "{brand_color}" on the slide):
{brand_color}

ASPECT_RATIO (every slide MUST be designed for this ratio):
{aspect_ratio}

{count_directive}
Emit the JSON object now.
"""


# ============================================================
# VALIDATION HELPERS
# ============================================================
def _derive_headline(text_zones: dict | None, fallback: str | None) -> str | None:
    """Backwards-compat: downstream consumers (CSV logger, review UI) read
    s['headline']. If the model only filled text_zones, derive a sensible
    headline string from the most-important non-null zone."""
    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip()
    if not isinstance(text_zones, dict):
        return None
    for key in ("headline", "section_pill", "caption", "cta_button_label"):
        v = text_zones.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _validate_director_output(parsed: Any, *, lo: int, hi: int, forced: int | None) -> None:
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Director output not a dict: {type(parsed).__name__}")
    slides = parsed.get("slides")
    if not isinstance(slides, list):
        raise RuntimeError(f"Director output 'slides' must be a list (got {type(slides).__name__})")
    n = len(slides)
    if forced is not None:
        if n != forced:
            raise RuntimeError(
                f"Director output 'slides' must have exactly {forced} entries "
                f"(forced override); got {n}"
            )
    else:
        if n < lo or n > hi:
            raise RuntimeError(
                f"Director output 'slides' must have between {lo} and {hi} entries; got {n}"
            )
    # deck_design is required for the new pipeline but missing is recoverable -
    # we don't fail hard, just warn so older callers don't crash.
    if "deck_design" not in parsed or not isinstance(parsed.get("deck_design"), dict):
        logger.warning(
            "[carousel_director] output missing deck_design block - deck cohesion may suffer"
        )
    # Reject section_pill values that are template placeholders rather
    # than real topic labels. Real content uses brand/feature/concept
    # words; placeholders are generic counters like "Insight 01" or
    # "Step 3". Pattern: a placeholder word followed by an integer, or
    # a word + "N" / "X". If we see one, fail fast so the director re-
    # runs (validator path) or the orchestrator falls back cleanly.
    import re as _re
    _placeholder_pill_re = _re.compile(
        r"^\s*("
        r"insight|problem|point|step|feature|slide|item|tip|fact|reason|"
        r"benefit|principle|pillar|idea|key|number|no\.?"
        r")\s*[#\-_]?\s*(\d+|n|x|i{1,3}|iv|v|vi{1,3}|ix|x)\s*$",
        _re.IGNORECASE,
    )
    for i, s in enumerate(slides, 1):
        if not isinstance(s, dict):
            raise RuntimeError(f"slides[{i-1}] not a dict")
        for f in ("slide_no", "role", "image_prompt"):
            if f not in s or not s[f]:
                raise RuntimeError(f"slides[{i-1}] missing required field '{f}'")
        # Headline: derive if missing, then store back so downstream code is happy.
        s["headline"] = _derive_headline(s.get("text_zones"), s.get("headline")) or s.get("role", "slide")
        tz = s.get("text_zones") or {}
        # Strip section_pill unconditionally — pills / topic badges are
        # removed from the deck design system. Even if the director
        # emits one, we blank it out so it never ships on the rendered
        # slide.
        if "section_pill" in tz:
            tz["section_pill"] = None
        # Also scrub any pill / badge language from the image_prompt so
        # the renderer doesn't paint a floating chip anyway.
        _ip = s.get("image_prompt")
        if isinstance(_ip, str):
            _pill_phrase_re = _re.compile(
                r"\b(section[- ]?pill|topic pill|topic chip|topic badge|"
                r"pill (chip|shape|tag|badge|label)|"
                r"rounded (pill|badge|chip)|"
                r"floating (pill|chip|badge|tag))\b",
                _re.IGNORECASE,
            )
            s["image_prompt"] = _pill_phrase_re.sub("small caption label", _ip)
        # CTA slide MUST have a visible button — enforce here so silent
        # button-less CTAs can't slip through. If the director forgot, we
        # fail loudly so the orchestrator's fallback kicks in.
        if s.get("role") == "cta":
            cta_label = tz.get("cta_button_label")
            if not isinstance(cta_label, str) or not cta_label.strip():
                raise RuntimeError(
                    f"slides[{i-1}] role=cta is missing text_zones.cta_button_label — "
                    f"every CTA slide must declare a visible button label"
                )
            # Reject soft social-engagement CTAs - we want commercial actions
            # (demo / call / trial / learn more), not "follow us" or "save".
            _cta_lower = cta_label.strip().lower()
            _forbidden_cta_words = (
                "follow", "subscribe", "save ", "save it", "save this",
                "like ", "like and", "share ", "share this",
                "comment", "dm ", "dm us", "dm for",
                "tag a", "tag your",
            )
            if any(w in _cta_lower for w in _forbidden_cta_words) or _cta_lower in (
                "follow", "subscribe", "save", "like", "share",
            ):
                raise RuntimeError(
                    f"slides[{i-1}] cta_button_label={cta_label!r} is a soft "
                    f"social-engagement CTA. Use a commercial CTA instead "
                    f"(e.g. 'Book a demo', 'Book a call', 'Learn more')."
                )


# ============================================================
# DIRECTOR CALL
# ============================================================
def run_carousel_director(
    *,
    post_text: str,
    brand_name: str,
    brand_color: str,
    aspect_ratio: str,
    slide_count: int | None = None,
    min_slides: int | None = None,
    max_slides: int | None = None,
    business_category: str = "",
    no_logo: bool = False,
    image_style: str | None = None,
) -> dict[str, Any]:
    """Run Agent 1. Returns the parsed director JSON: pdf_title + deck_design
    + slides[]. Each slide has slide_no, role, headline, layout,
    background_spec, text_zones, hero_visual, image_prompt.

    Pass slide_count=N to force exactly N slides (legacy / hard override).
    Pass it as None (default) to let the director pick inside
    [min_slides .. max_slides]. min_slides/max_slides default to the
    module-level MIN_SLIDES/MAX_SLIDES env-driven values.

    Raises RuntimeError on empty output or invalid JSON so the orchestrator
    can fall back cleanly.
    """
    lo = min_slides if min_slides is not None else MIN_SLIDES
    hi = max_slides if max_slides is not None else MAX_SLIDES
    if lo < 1:
        raise ValueError(f"min_slides must be >= 1 (got {lo})")
    if hi < lo:
        raise ValueError(f"max_slides ({hi}) must be >= min_slides ({lo})")
    if slide_count is not None and slide_count < 1:
        raise ValueError(f"slide_count must be >= 1 (got {slide_count})")

    if no_logo:
        logger.info(
            f"[carousel_director] NO-LOGO MODE (logo missing OR category empty) "
            f"— every slide's image_prompt will get 'no logo, no brand mark' instructions"
        )
    user_prompt = build_director_user_prompt(
        post_text=post_text,
        brand_name=brand_name,
        brand_color=brand_color,
        aspect_ratio=aspect_ratio,
        slide_count=slide_count,
        min_slides=lo,
        max_slides=hi,
        business_category=business_category,
        no_logo=no_logo,
        image_style=image_style,
    )

    api_kwargs: dict[str, Any] = {
        "model": DIRECTOR_MODEL,
        "messages": [
            {"role": "system", "content": DIRECTOR_SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        # Structured output - JSON only, no prose wrapping needed.
        "response_format": {"type": "json_object"},
    }
    if _is_gpt5_family(DIRECTOR_MODEL):
        # GPT-5 family: max_completion_tokens covers reasoning + output.
        api_kwargs["max_completion_tokens"] = DIRECTOR_MAX_TOKENS
        api_kwargs["reasoning_effort"]      = DIRECTOR_REASONING_EFFORT
    else:
        api_kwargs["max_tokens"]   = DIRECTOR_MAX_TOKENS
        api_kwargs["temperature"]  = DIRECTOR_TEMPERATURE
        api_kwargs["top_p"]        = DIRECTOR_TOP_P

    t0 = time.monotonic()
    # Retry + fallback strategy (same pattern as Magic Agent 1):
    #   1. Try PRIMARY (gpt-5.1) with 3 retries + exponential backoff
    #   2. On exhaustion, fall back to DIRECTOR_FALLBACK_MODEL (gpt-5)
    #      with its own 3-retry policy — same api_kwargs, different model.
    #   3. Both fail → propagate.
    # Ledger records whichever model actually served the response.
    _model_used = DIRECTOR_MODEL
    _client_inst = _client()
    try:
        resp = call_with_retry(
            lambda: _client_inst.chat.completions.create(**api_kwargs),
            label=f"OpenAI/CarouselDirector/{DIRECTOR_MODEL}",
        )
    except Exception as _primary_err:
        if DIRECTOR_FALLBACK_MODEL and DIRECTOR_FALLBACK_MODEL != DIRECTOR_MODEL:
            logger.warning(
                f"[carousel_director] primary model {DIRECTOR_MODEL!r} "
                f"exhausted retries — falling back to "
                f"{DIRECTOR_FALLBACK_MODEL!r}. Last error: {_primary_err}"
            )
            _fallback_kwargs = dict(api_kwargs)
            _fallback_kwargs["model"] = DIRECTOR_FALLBACK_MODEL
            try:
                resp = call_with_retry(
                    lambda: _client_inst.chat.completions.create(**_fallback_kwargs),
                    label=f"OpenAI/CarouselDirector/{DIRECTOR_FALLBACK_MODEL}",
                )
                _model_used = DIRECTOR_FALLBACK_MODEL
                logger.info(
                    f"[carousel_director] fallback model "
                    f"{DIRECTOR_FALLBACK_MODEL!r} succeeded after primary failed"
                )
            except Exception as _fallback_err:
                logger.error(
                    f"[carousel_director] BOTH models failed. "
                    f"primary={_primary_err} fallback={_fallback_err}"
                )
                raise
        else:
            raise
    elapsed = round(time.monotonic() - t0, 2)

    raw = (resp.choices[0].message.content or "").strip()

    # Token-usage breadcrumb so we know when to bump max_completion_tokens.
    # Also captured as ints for the CSV cost computation downstream.
    usage_info = ""
    _input_tokens = 0
    _output_tokens = 0
    _reasoning_tokens = 0
    try:
        u = resp.usage
        if u:
            _input_tokens = int(getattr(u, "prompt_tokens", 0) or 0)
            _output_tokens = int(getattr(u, "completion_tokens", 0) or 0)
            details = getattr(u, "completion_tokens_details", None)
            reasoning = getattr(details, "reasoning_tokens", None) if details else None
            if reasoning is not None:
                _reasoning_tokens = int(reasoning or 0)
            usage_info = (
                f" tokens=in:{u.prompt_tokens}/out:{u.completion_tokens}"
                + (f"/reasoning:{reasoning}" if reasoning is not None else "")
            )
    except Exception:
        pass

    # Record the carousel director call in the per-request cost ledger.
    # `_model_used` = whichever model ACTUALLY served the response (primary
    # if it succeeded, fallback otherwise) — matches OpenAI billing.
    # Uses ART_DIRECTOR slot to share with the magic pipeline's Agent 1
    # (both are "art director" roles). time_sec is wall-clock elapsed.
    try:
        _ledger = get_current_ledger()
        if _ledger is not None:
            _ledger.record_openai_text(
                model=_model_used,
                input_tokens=_input_tokens,
                output_tokens=_output_tokens,
                agent_slot="ART_DIRECTOR",
                time_sec=float(elapsed or 0.0),
            )
    except Exception as _le:
        logger.warning(f"[cost_ledger] failed to record carousel director: {_le}")

    target_str = f"forced={slide_count}" if slide_count is not None else f"range=[{lo}..{hi}]"
    logger.info(
        f"[carousel_director] produced {len(raw)} chars in {elapsed}s "
        f"model={DIRECTOR_MODEL} {target_str}{usage_info}"
    )

    if not raw:
        raise RuntimeError(
            f"Carousel Director ({DIRECTOR_MODEL}) returned empty output. "
            f"Bump CAROUSEL_DIRECTOR_MAX_TOKENS (current={DIRECTOR_MAX_TOKENS}) or "
            f"lower CAROUSEL_DIRECTOR_REASONING (current={DIRECTOR_REASONING_EFFORT})."
        )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Carousel Director returned invalid JSON: {exc}. First 200 chars: {raw[:200]!r}"
        ) from exc

    _validate_director_output(parsed, lo=lo, hi=hi, forced=slide_count)

    actual_n = len(parsed["slides"])
    logger.info(
        f"[carousel_director] director chose {actual_n} slides "
        f"(roles: {[s.get('role') for s in parsed['slides']]})"
    )

    # Attach director runtime + token usage so the CSV logger can capture
    # them without re-timing or re-querying. _-prefixed fields are
    # bookkeeping the pipeline strips before persisting director JSON.
    parsed["_director_time_s"]      = elapsed
    parsed["_director_model"]       = DIRECTOR_MODEL
    parsed["_director_input_tokens"]     = _input_tokens
    parsed["_director_output_tokens"]    = _output_tokens
    parsed["_director_reasoning_tokens"] = _reasoning_tokens

    return parsed


# ─────────────────────────────────────────────────────────────────
# STREAMING DIRECTOR (Method 1 — overlapping director with renders)
# ─────────────────────────────────────────────────────────────────
# Why this exists:
#   GPT-5 director normally blocks for ~100s before image rendering can
#   begin. But it generates the JSON token-by-token. We can stream the
#   response, parse it incrementally, and fire each slide's image render
#   the MOMENT its `image_prompt` field is complete — while the director
#   is still emitting later slides.
#
# End-to-end effect for a 6-slide carousel:
#   Before: director (102s) -> renders (175s) -> total ~277s
#   After:  director (102s) overlapping renders -> total ~215s
#   Savings: ~60s wall-clock per carousel, zero prompt/model/quality change.
#
# Same prompts. Same model. Same reasoning_effort. Same output JSON shape.
# Only the API call pattern + when-we-start-rendering changes.


def run_carousel_director_streaming(
    *,
    post_text: str,
    brand_name: str,
    brand_color: str,
    aspect_ratio: str,
    slide_count: int | None = None,
    min_slides: int | None = None,
    max_slides: int | None = None,
    on_slide_ready=None,   # callable(slide_dict) -> None, fires per slide
    business_category: str = "",
    no_logo: bool = False,
    image_style: str | None = None,
) -> dict[str, Any]:
    """Streaming variant of run_carousel_director.

    Identical input contract + return value as the non-streaming version,
    PLUS an `on_slide_ready` callback that fires once per slide as soon
    as that slide's full object (including image_prompt) has streamed in.

    The caller can use the callback to submit image-render work to a
    thread pool immediately, overlapping rendering with director output.
    """
    import ijson  # streaming JSON parser

    lo = min_slides if min_slides is not None else MIN_SLIDES
    hi = max_slides if max_slides is not None else MAX_SLIDES
    if lo < 1:
        raise ValueError(f"min_slides must be >= 1 (got {lo})")
    if hi < lo:
        raise ValueError(f"max_slides ({hi}) must be >= min_slides ({lo})")
    if slide_count is not None and slide_count < 1:
        raise ValueError(f"slide_count must be >= 1 (got {slide_count})")

    if no_logo:
        logger.info(
            f"[carousel_director] NO-LOGO MODE (logo missing OR category empty) "
            f"— every slide's image_prompt will get 'no logo, no brand mark' instructions"
        )
    user_prompt = build_director_user_prompt(
        post_text=post_text,
        brand_name=brand_name,
        brand_color=brand_color,
        aspect_ratio=aspect_ratio,
        slide_count=slide_count,
        min_slides=lo,
        max_slides=hi,
        business_category=business_category,
        no_logo=no_logo,
        image_style=image_style,
    )

    api_kwargs: dict[str, Any] = {
        "model": DIRECTOR_MODEL,
        "messages": [
            {"role": "system", "content": DIRECTOR_SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "stream": True,
        # OpenAI's streaming-usage hook — gives us token counts at the end.
        "stream_options": {"include_usage": True},
    }
    if _is_gpt5_family(DIRECTOR_MODEL):
        api_kwargs["max_completion_tokens"] = DIRECTOR_MAX_TOKENS
        api_kwargs["reasoning_effort"]      = DIRECTOR_REASONING_EFFORT
    else:
        api_kwargs["max_tokens"]   = DIRECTOR_MAX_TOKENS
        api_kwargs["temperature"]  = DIRECTOR_TEMPERATURE
        api_kwargs["top_p"]        = DIRECTOR_TOP_P

    t0 = time.monotonic()

    # We pipe streamed deltas through a writable buffer -> ijson parser.
    # ijson reads from an iterable of bytes; we accumulate text deltas and
    # feed each delta as bytes. Track per-slide accumulation so we can
    # fire the callback the moment each slide's image_prompt is complete.
    raw_chunks: list[str] = []
    fired_slides: set[int] = set()       # slide_nos already passed to callback
    current_slide: dict[str, Any] = {}   # accumulator for the slide being parsed
    completed_slides: list[dict[str, Any]] = []

    _input_tokens = 0
    _output_tokens = 0
    _reasoning_tokens = 0

    # ── streaming setup ────────────────────────────────────────
    # Wrap the OpenAI stream as a byte iterator so ijson can consume it
    # progressively. We tee into raw_chunks for final JSON parse + logging.
    def _byte_iter():
        nonlocal _input_tokens, _output_tokens, _reasoning_tokens
        stream = _client().chat.completions.create(**api_kwargs)
        for chunk in stream:
            # Token usage arrives in a final chunk where choices is empty.
            try:
                u = getattr(chunk, "usage", None)
                if u is not None:
                    _input_tokens = int(getattr(u, "prompt_tokens", 0) or 0)
                    _output_tokens = int(getattr(u, "completion_tokens", 0) or 0)
                    det = getattr(u, "completion_tokens_details", None)
                    rt = getattr(det, "reasoning_tokens", None) if det else None
                    if rt is not None:
                        _reasoning_tokens = int(rt or 0)
            except Exception:
                pass

            try:
                delta = chunk.choices[0].delta.content if chunk.choices else None
            except (IndexError, AttributeError):
                delta = None
            if not delta:
                continue
            raw_chunks.append(delta)
            yield delta.encode("utf-8")

    # ── stream-parse: detect each slide's image_prompt completing ──
    # ijson.parse() expects a FILE-LIKE OBJECT with .read(n), not a
    # plain byte-generator. Passing a generator directly triggers
    # "not enough values to unpack (expected 2, got 1)" deep inside
    # ijson.utils.coros2gen — because ijson's coroutine chain assumes
    # a real read() interface, not raw iteration.
    #
    # Fix: wrap _byte_iter() as an io.RawIOBase so ijson's C/Python
    # backends both see a proper file-like source.
    import io as _io

    class _ByteIterFile(_io.RawIOBase):
        def __init__(self, gen):
            self._gen = iter(gen)
            self._buf = b""
            self._eof = False
        def readable(self):
            return True
        def readinto(self, out):
            while not self._eof and len(self._buf) < len(out):
                try:
                    self._buf += next(self._gen)
                except StopIteration:
                    self._eof = True
                    break
            n = min(len(self._buf), len(out))
            out[:n] = self._buf[:n]
            self._buf = self._buf[n:]
            return n

    stream_file = _io.BufferedReader(_ByteIterFile(_byte_iter()))
    parser = ijson.parse(stream_file)
    _events_seen = 0
    try:
        for item in parser:
            _events_seen += 1
            if isinstance(item, tuple) and len(item) == 3:
                prefix, event, value = item
            elif isinstance(item, tuple) and len(item) == 2:
                prefix, event = item
                value = None
            else:
                logger.warning(
                    f"[carousel_director] streaming: unexpected ijson item "
                    f"type={type(item).__name__} — skipping"
                )
                continue

            if not prefix.startswith("slides.item"):
                continue

            field = prefix[len("slides.item"):].lstrip(".")

            if prefix == "slides.item" and event == "start_map":
                current_slide = {}
                continue

            if prefix == "slides.item" and event == "end_map":
                slide_no = current_slide.get("slide_no")
                if slide_no is not None and slide_no not in fired_slides:
                    fired_slides.add(slide_no)
                    completed_slides.append(dict(current_slide))
                    if on_slide_ready is not None:
                        try:
                            on_slide_ready(dict(current_slide))
                            logger.info(
                                f"[carousel_director] streamed slide {slide_no} "
                                f"({current_slide.get('role','?')}) → callback fired"
                            )
                        except Exception as exc:
                            logger.warning(
                                f"[carousel_director] on_slide_ready callback raised "
                                f"for slide {slide_no}: {exc}"
                            )
                current_slide = {}
                continue

            if event in ("string", "number", "boolean", "null"):
                current_slide[field] = value
    except Exception:
        # Log the FULL traceback so we can debug streaming failures.
        # Then re-raise so the caller's fallback (sequential director)
        # can kick in — nothing lost, just visibility gained.
        import traceback as _tb
        logger.error(
            f"[carousel_director] streaming ijson loop crashed after "
            f"{_events_seen} events. Traceback:\n{_tb.format_exc()}"
        )
        raise

    elapsed = round(time.monotonic() - t0, 2)
    raw = "".join(raw_chunks).strip()

    if not raw:
        raise RuntimeError(
            f"Carousel Director ({DIRECTOR_MODEL}) streamed empty output. "
            f"Bump CAROUSEL_DIRECTOR_MAX_TOKENS (current={DIRECTOR_MAX_TOKENS})."
        )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Carousel Director streamed invalid JSON: {exc}. "
            f"First 200 chars: {raw[:200]!r}"
        ) from exc

    _validate_director_output(parsed, lo=lo, hi=hi, forced=slide_count)

    actual_n = len(parsed["slides"])
    target_str = f"forced={slide_count}" if slide_count is not None else f"range=[{lo}..{hi}]"
    logger.info(
        f"[carousel_director] STREAMING produced {len(raw)} chars in {elapsed}s "
        f"model={DIRECTOR_MODEL} {target_str} slides_fired_early={len(fired_slides)} "
        f"tokens=in:{_input_tokens}/out:{_output_tokens}/reasoning:{_reasoning_tokens}"
    )
    logger.info(
        f"[carousel_director] director chose {actual_n} slides "
        f"(roles: {[s.get('role') for s in parsed['slides']]})"
    )

    parsed["_director_time_s"]      = elapsed
    parsed["_director_model"]       = DIRECTOR_MODEL
    parsed["_director_input_tokens"]     = _input_tokens
    parsed["_director_output_tokens"]    = _output_tokens
    parsed["_director_reasoning_tokens"] = _reasoning_tokens

    return parsed
