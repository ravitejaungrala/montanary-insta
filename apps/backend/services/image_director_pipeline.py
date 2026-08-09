"""Designer-Grade Post pipeline — Image Director + gpt-image-2 (high).

Fires ONLY when the user picks the `designer_grade_post` style. Bypasses the
standard Art Director prompt chain in favour of a purpose-built Image
Director that reads the FULL Business DNA, picks contrast-aware colours,
locks CTA vocabulary, and produces a structured design brief. The brief
is then rendered by gpt-image-2 in HIGH quality (same image model the
regular pipeline uses, but reached via a different prompt route).

Agent 1 — IMAGE DIRECTOR (GPT-5.1)
    Reads: campaign brief + FULL Business DNA (colours, tagline, brand tone,
    products/services, overview, extracted document text) + variant type +
    aspect ratio + business category.
    Emits STRUCTURED JSON containing:
        image_prompt       — the natural-language design brief for the image agent
        aspect_ratio       — chosen from a fixed set based on the campaign
        primary_bg         — hex, picked from DNA colours
        primary_text       — hex, picked from DNA colours, CONTRAST-AWARE
        accent             — hex, picked from DNA colours
        cta_text           — from a LOCKED vocabulary (Book a Demo / Explore / …)
        heading            — mandatory: the single hero line
        subheading         — optional supporting line
        supporting_points  — optional list of 2-3 short bullet phrases
        logo_placement     — "top-left" (hard rule for software/service categories)

Agent 2 — IMAGE AGENT (gpt-image-2, quality="high")
    Same image model + high-quality mode as the standard pipeline, but
    called with the Image Director's structured brief instead of the
    Art Director's free-form prompt. Uses `images.edit` when a logo is
    attached (so the logo is preserved verbatim), `images.generate`
    otherwise.

Return shape mirrors run_magic_image_pipeline's per-variant result so the
outer pipeline's CSV logger and S3 uploader keep working unchanged.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import random
import re
import time
from io import BytesIO
from typing import Optional

from openai import OpenAI

logger = logging.getLogger("pipelyt.image_director")

# ══════════════════════════════════════════════════════════════════════
# LOCKED CTA VOCABULARY
# ══════════════════════════════════════════════════════════════════════
# Image Director MUST pick from this list — no free-form CTAs. Keeps
# the poster feeling intentional and consistent with real product-marketing
# copy patterns.
CTA_VOCABULARY = [
    "Book a Demo",
    "Get Started",
    "Try Free",
    "Learn More",
    "See Live Demo",
    "Watch the Demo",
    "Talk to Sales",
    "Explore",
    "See it in Action",
    "Sign Up",
    "Start Free Trial",
    "View Pricing",
    "Contact Sales",
    "Request Access",
    "Apply Now",           # for admissions / education
    "Enrol Today",         # for education
    "Shop the Collection", # for physical product
    "Reserve Your Spot",   # for events
]

# ══════════════════════════════════════════════════════════════════════
# HARD-RULE CATEGORIES
# ══════════════════════════════════════════════════════════════════════
# The user's requirement: "Logo top-left" and "clean CTA vocabulary" are
# HARD rules ONLY for software / IT-consulting / service brands. Other
# categories keep the rules as strong recommendations but not hard locks.
HARD_RULE_CATEGORIES = {
    "software_product",
    "software_service",
    "consulting_services",
    "saas_product",   # legacy alias
}

# Aspect ratios the Image Director may pick from.
VALID_ASPECT_RATIOS = ("1:1", "4:5", "9:16", "16:9", "1.91:1")

# GPT-5.1 model for the Image Director.
_IMAGE_DIRECTOR_MODEL = os.environ.get("IMAGE_DIRECTOR_MODEL", "gpt-5.1")

# Image Agent — gpt-image-2 at HIGH quality. Same model + tier as the
# regular pipeline; the difference is only the calling agent (Image
# Director instead of Art Director) and the structured design brief
# that gets passed in.
_IMAGE_AGENT_MODEL   = os.environ.get("IMAGE_AGENT_MODEL",   "gpt-image-2")
_IMAGE_AGENT_QUALITY = os.environ.get("IMAGE_AGENT_QUALITY", "high").strip().lower()

# Aspect-ratio → OpenAI size mapping. Mirrors ASPECT_TO_SIZE in
# magic_image_pipeline.py so the two paths render at the same
# canvas sizes. Kept local to avoid a circular import.
_ASPECT_TO_SIZE: dict[str, str] = {
    "1:1":    "1024x1024",
    "4:5":    "1024x1280",
    "9:16":   "1024x1792",
    "16:9":   "1792x1024",
    "1.91:1": "1792x1024",
}
_DEFAULT_SIZE = "1024x1024"


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════
def _openai_client() -> OpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "OPENAI_API_KEY env var not set — required by the Image Director."
        )
    return OpenAI(api_key=key)


def _dna_summary_for_director(business_dna: Optional[dict]) -> str:
    """Compact string summary of the DNA fields the Image Director needs.

    Includes the extracted text from any uploaded DNA documents (universities,
    contact info, pricing, features, etc.) so the Director can pull real
    facts into the heading and supporting points instead of inventing them.
    """
    if not business_dna or not isinstance(business_dna, dict):
        return "(no Business DNA available)"

    lines: list[str] = []

    def _add(key: str, label: str | None = None, limit: int = 400):
        v = business_dna.get(key)
        if not v:
            return
        if isinstance(v, (list, dict)):
            v = json.dumps(v)[:limit]
        s = str(v).strip()
        if s:
            lines.append(f"{label or key.upper()}: {s[:limit]}")

    _add("company_name",     "COMPANY")
    _add("product_name",     "PRIMARY PRODUCT")
    _add("tagline",          "TAGLINE")
    _add("category",         "CATEGORY")
    _add("brand_tone",       "BRAND TONE")
    _add("brand_values",     "BRAND VALUES")
    _add("brand_aesthetic",  "BRAND AESTHETIC")
    _add("fonts",            "FONT PREFERENCES")
    _add("url",              "WEBSITE")

    # Colours as a compact block
    colors = business_dna.get("colors") or {}
    if isinstance(colors, dict) and colors:
        color_bits = [f"{k}={v}" for k, v in colors.items() if v]
        if color_bits:
            lines.append("BRAND COLOURS (Image Director MUST pick from these): "
                         + ", ".join(color_bits))

    # Overview — the meat of the DNA
    overview = (business_dna.get("overview") or "").strip()
    if overview:
        lines.append("OVERVIEW:\n" + overview[:2000])

    # DNA documents — extracted text (universities, pricing, contact, etc.)
    docs = business_dna.get("documents") or []
    used = 0
    for i, d in enumerate(docs, start=1):
        text = (d.get("text") or "").strip()
        if not text:
            continue
        name = d.get("name") or f"document_{i}"
        remaining = 6000 - used
        if remaining <= 200:
            break
        piece = text[:remaining]
        lines.append(f"UPLOADED DOC {i} — {name}:\n{piece}")
        used += len(piece)

    return "\n\n".join(lines) if lines else "(no Business DNA available)"


# ══════════════════════════════════════════════════════════════════════
# FEW-SHOT EXAMPLES — shown to the Ideation phase so the model
# understands what "variety" looks like across different briefs. Each
# example uses a completely different layout family — deliberately.
# ══════════════════════════════════════════════════════════════════════
FEW_SHOT_IDEATION_EXAMPLES = [
    {
        "brief":    "announce our Series B funding round",
        "concepts": [
            {"layout_family": "typographic_hero",
             "summary": "The words 'SERIES B' in gigantic display type dominating 70% of the canvas, one-line caption underneath. No supporting visual. Announcement feel.",
             "why": "raise announcements land hardest as pure typography — no distractions"},
            {"layout_family": "stat_card",
             "summary": "Giant '$25M' as the visual anchor, small caption 'Series B led by <VC>'. Whitespace-heavy.",
             "why": "the number IS the story"},
            {"layout_family": "editorial_quote",
             "summary": "Founder pull-quote in large italic serif, small headshot circle bottom-left, magazine-cover feel.",
             "why": "humanises the raise, differentiates from the two typography-first options"},
        ],
    },
    {
        "brief":    "showcase our new Kanban board feature",
        "concepts": [
            {"layout_family": "split_with_mockup",
             "summary": "Split — heading + one-line copy left, clean UI mockup of the Kanban board on the right.",
             "why": "product features need the visual to show what changed"},
            {"layout_family": "annotated_screenshot",
             "summary": "Full-bleed product screenshot as background, small callout circles pointing to 3 key UI moments.",
             "why": "shows the feature in real UX context"},
            {"layout_family": "before_after",
             "summary": "Two panels side-by-side: 'BEFORE' — a messy list; 'AFTER' — the new Kanban view. Minimal heading above.",
             "why": "before/after tells the value story faster than text ever could"},
        ],
    },
    {
        "brief":    "highlight our 92% customer satisfaction score",
        "concepts": [
            {"layout_family": "stat_hero",
             "summary": "The number '92%' rendered ENORMOUS as the visual anchor, one-line caption 'CSAT — Q3 2026' below.",
             "why": "a great stat needs no explanation — let it breathe"},
            {"layout_family": "quote_wall",
             "summary": "3 short customer quotes tiled in a grid, each with source attribution. 92% stat sits as a small badge in the corner.",
             "why": "quotes back up the number with proof"},
            {"layout_family": "chart_hero",
             "summary": "Clean line chart showing CSAT rising over 4 quarters, arriving at 92%. One-line heading above.",
             "why": "trend visualisation reframes the stat as momentum"},
        ],
    },
    {
        "brief":    "explain how our API connects to Salesforce and HubSpot",
        "concepts": [
            {"layout_family": "diagram_workflow",
             "summary": "Central brand logo with clean connector lines fanning out to Salesforce + HubSpot logos. Minimal text.",
             "why": "connection is the story — a diagram tells it in a glance"},
            {"layout_family": "icon_grid",
             "summary": "3-column grid: Salesforce icon → sync arrow → brand icon → sync arrow → HubSpot icon. Labels beneath.",
             "why": "for feed formats where a linear flow reads faster than a hub-and-spoke diagram"},
            {"layout_family": "code_snippet",
             "summary": "Terminal-style dark card showing 3 lines of API code with the integration in action. Small caption below.",
             "why": "developer-audience posts need to feel like the product looks"},
        ],
    },
    {
        "brief":    "share a customer story from Acme Corp",
        "concepts": [
            {"layout_family": "quote_card",
             "summary": "Large italic pull-quote from the customer, editorial magazine feel, name + role + Acme logo bottom.",
             "why": "quotes are the story"},
            {"layout_family": "case_study_stats",
             "summary": "3 big-number stat tiles across the canvas ('3x faster', '42% cost saved', '2 weeks setup'), Acme logo top.",
             "why": "leads with the outcome, not the person"},
            {"layout_family": "photo_hero",
             "summary": "Full-bleed photo of the customer team, single overlay caption '<name> chose <brand>'.",
             "why": "puts a human face on the story — visually distinct from the two text-first options"},
        ],
    },
]


# ══════════════════════════════════════════════════════════════════════
# PHASE A — IDEATION (small call, 3 distinct concepts)
# ══════════════════════════════════════════════════════════════════════
def _build_ideation_system_prompt() -> str:
    """System prompt for Phase A — the Director sketches 3 distinct concepts."""
    examples_block = ""
    for ex in FEW_SHOT_IDEATION_EXAMPLES:
        concepts_txt = "\n".join(
            f'    {{ "layout_family": "{c["layout_family"]}", '
            f'"summary": "{c["summary"]}", '
            f'"why": "{c["why"]}" }}'
            for c in ex["concepts"]
        )
        examples_block += (
            f'\nEXAMPLE BRIEF: "{ex["brief"]}"\n'
            f'EXAMPLE OUTPUT:\n'
            f'{{\n  "concepts": [\n{concepts_txt}\n  ]\n}}\n'
        )

    return f"""You are the IDEATION phase of an Image Director for designer-grade social media posts.

Your job on this call is small and specific: given a campaign brief and business context, sketch THREE distinct design concepts in prose. Each concept is 1-3 sentences describing the whole scene. That's it. No JSON schemas, no colour picks, no CTAs — just three genuinely different design directions.

THE MOST IMPORTANT RULE:
The three concepts MUST span THREE DIFFERENT TOP-LEVEL FAMILIES. Not three variants of the same idea. Each concept must belong to a distinct family such as:
  • TYPOGRAPHY-FIRST (headline dominates, minimal else)
  • DIAGRAM-FIRST (nodes / connectors / workflows / hub-and-spoke)
  • PHOTO-FIRST (full-bleed photograph with corner text)
  • STAT-FIRST (giant number as the visual anchor)
  • PRODUCT-MOCKUP-FIRST (UI screenshot / device frame)
  • QUOTE / TESTIMONIAL (editorial pull-quote)
  • ICON-GRID (3–6 icon+label cards, no hero visual)
  • BEFORE / AFTER (comparison split)
  • ANNOUNCEMENT BANNER (release-note / launch style)
  • ILLUSTRATION-FIRST (custom drawn or abstract graphic)

A valid triplet: [typography-first, diagram-first, stat-first]. An INVALID triplet is anything like [hub-and-spoke, connector-diagram, node-network] — those are three restatements of "diagram-first" and count as one family. If you catch yourself producing two concepts in the same family, replace one before returning.

Think like a designer at a real agency who's been asked "give me three directions" for the same brief. Each direction takes the brief seriously but sees it through a totally different visual lens. If the brief is a raise announcement, one direction might be pure typography, another a giant stat, another an editorial quote card. If the brief is a product feature, one might be a mockup split, another a before/after, another an annotated screenshot.

Restraint is not a downside. A concept that reads "just the heading, huge, on a plain background, nothing else" is often the best one — do NOT feel obligated to include supporting visuals, subheadings, or bullet points unless the brief clearly benefits from them.

If the user prompt includes an AVOID THESE PAST DESIGNS block, ALL THREE of your concepts must be visibly different from every past design in that block. This is not negotiable — a repeat is a failure.

OUTPUT FORMAT (return ONLY this JSON — no code fences, no prose):
{{
  "concepts": [
    {{ "layout_family": "short-slug",
       "summary":       "1-3 sentence description of the whole scene, concrete enough for another designer to render",
       "why":           "one sentence on why this concept fits the brief" }},
    {{ ... 2nd concept, structurally different from #1 ... }},
    {{ ... 3rd concept, structurally different from #1 and #2 ... }}
  ]
}}

FEW-SHOT EXAMPLES (learn the VARIETY pattern, not any single example):
{examples_block}
Return the JSON now."""


def _build_ideation_user_prompt(
    variant_type: str,
    campaign_brief: str,
    post_text: str,
    business_dna: Optional[dict],
    business_category: str,
    brand_name: str,
    avoid_block: str = "",
) -> str:
    """User prompt for Phase A — brief + DNA + past history."""
    variant_intent = {
        "viral_reach":      "Punchy, hook-driven — maximise thumb-stop reach.",
        "follower_growth":  "Value-driven — proof of authority, prompts a CTA click.",
        "high_interaction": "Question-based — designed to draw comments and saves.",
        "festival_variant": "Culturally warm — festival-themed while staying brand-aligned.",
    }.get(variant_type, "Value-driven, clean and confident.")

    dna_block = _dna_summary_for_director(business_dna)

    return f"""CAMPAIGN BRIEF (what the customer wants to post about):
{campaign_brief.strip()}

VARIANT INTENT — {variant_type}:
{variant_intent}

PLATFORM POST COPY (background context — do NOT paste onto the image):
{post_text.strip()[:1500]}

BRAND NAME: {brand_name or '(unknown)'}
BUSINESS CATEGORY: {business_category or '(unset)'}

═══════════════════════════════════════════════════════════════════
BUSINESS DNA
═══════════════════════════════════════════════════════════════════
{dna_block}
═══════════════════════════════════════════════════════════════════

{avoid_block}Sketch three structurally different design concepts now."""


_STOP_WORDS = {
    "a", "an", "and", "the", "of", "in", "on", "at", "to", "for", "with",
    "by", "from", "as", "is", "are", "or", "one", "two", "three", "left",
    "right", "top", "bottom", "centre", "center", "canvas", "layout",
    "composition", "small", "large", "big", "clean", "minimal",
}


def _norm_words(text: str) -> set[str]:
    """Lowercase, alphanumeric-only tokens minus stop words. Used for cheap
    composition-overlap detection."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOP_WORDS}


def _concept_matches_history(concept: dict, past_summaries: list[str],
                              overlap_threshold: float = 0.5) -> bool:
    """True when the concept's family + summary shares >=50% of its
    meaningful tokens with any past composition summary. Cheap proxy for
    'this is a rehash of what we just made'."""
    fam  = str((concept or {}).get("layout_family") or "")
    summ = str((concept or {}).get("summary") or "")
    tokens = _norm_words(fam + " " + summ)
    if not tokens:
        return False
    for past in past_summaries:
        past_tokens = _norm_words(past)
        if not past_tokens:
            continue
        overlap = len(tokens & past_tokens) / max(1, len(tokens))
        if overlap >= overlap_threshold:
            return True
    return False


def _diversify_concepts(
    concepts: list[dict],
    recent_history: list[dict],
    variant_type: str,
) -> list[dict]:
    """Take raw ideation concepts, strip anything that clones recent
    designs, then SHUFFLE to eliminate position bias in elaboration.

    Never returns an empty list — if every concept clones history, keeps
    all of them (better a repeat than a blank menu). Always logs the
    filter + shuffle decision so we can trace what happened.
    """
    if not concepts:
        return []

    past_summaries = [
        str((e or {}).get("composition_summary") or "")
        for e in (recent_history or [])
    ]
    past_summaries = [p for p in past_summaries if p.strip()]

    if past_summaries:
        kept:    list[dict] = []
        dropped: list[dict] = []
        for c in concepts:
            (dropped if _concept_matches_history(c, past_summaries) else kept).append(c)
        if kept:
            if dropped:
                logger.info(
                    f"[image-director/diversify] variant={variant_type} "
                    f"dropped {len(dropped)} concept(s) that clone recent "
                    f"history: {[str(c.get('layout_family')) for c in dropped]}"
                )
            concepts = kept
        else:
            logger.info(
                f"[image-director/diversify] variant={variant_type} "
                f"all {len(concepts)} concept(s) clone recent history — "
                f"keeping them anyway (better than empty menu)"
            )

    random.shuffle(concepts)  # kill position bias in elaboration
    logger.info(
        f"[image-director/diversify] variant={variant_type} "
        f"final concept order after shuffle: "
        f"{[str(c.get('layout_family')) for c in concepts]}"
    )
    return concepts


def _call_ideation(
    *,
    variant_type: str,
    campaign_brief: str,
    post_text: str,
    business_dna: Optional[dict],
    business_category: str,
    brand_name: str,
    avoid_block: str = "",
) -> list[dict]:
    """Phase A — return a list of 3 concept dicts. Retries once on JSON
    decode failure. Returns [] on total failure so elaboration can still
    fire (it will invent its own concept)."""
    client = _openai_client()
    system_prompt = _build_ideation_system_prompt()
    user_prompt = _build_ideation_user_prompt(
        variant_type=variant_type,
        campaign_brief=campaign_brief,
        post_text=post_text,
        business_dna=business_dna,
        business_category=business_category,
        brand_name=brand_name,
        avoid_block=avoid_block,
    )

    last_err: Optional[Exception] = None
    for attempt in (1, 2):  # one retry — sometimes the model returns empty
        t0 = time.monotonic()
        try:
            resp = client.chat.completions.create(
                model=os.environ.get("IMAGE_DIRECTOR_IDEATION_MODEL", "gpt-5-mini"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=1500,
            )
            dur = time.monotonic() - t0
            raw = (resp.choices[0].message.content or "").strip()
            if not raw:
                raise ValueError("ideation returned empty content")
            parsed = json.loads(raw)
            concepts = parsed.get("concepts") or []
            if not isinstance(concepts, list) or not concepts:
                raise ValueError("ideation returned no concepts")
            logger.info(
                f"[image-director/ideation] variant={variant_type} attempt={attempt} "
                f"dur={dur:.2f}s produced {len(concepts)} concept(s): "
                + " | ".join(
                    f"{c.get('layout_family', '?')}={str(c.get('summary', ''))[:60]!r}"
                    for c in concepts
                )
            )
            return concepts
        except Exception as exc:
            last_err = exc
            logger.warning(
                f"[image-director/ideation] variant={variant_type} "
                f"attempt={attempt} failed ({type(exc).__name__}: {exc})"
            )
    logger.warning(
        f"[image-director/ideation] variant={variant_type} — both attempts "
        f"failed; elaboration will run without a concept menu "
        f"(last err: {last_err})"
    )
    return []


# ══════════════════════════════════════════════════════════════════════
# PHASE B — ELABORATION (main call, loose schema, free-form image_prompt)
# ══════════════════════════════════════════════════════════════════════
def _build_image_director_system_prompt(
    business_category: str, cta_vocab: list[str]
) -> str:
    """System prompt for the Image Director (GPT-5.1)."""
    hard_rule = business_category.strip().lower() in HARD_RULE_CATEGORIES
    hard_rule_block = (
        "\n\nHARD RULES (this brand is in the software / IT-consulting / "
        "service category — these rules MUST be honoured):\n"
        "  1. Logo placement is TOP-LEFT — no exceptions.\n"
        "  2. The CTA MUST be one of the exact strings in the locked list "
        "     — never a paraphrase.\n"
        "  3. Colours in `primary_bg`, `primary_text`, and `accent` MUST "
        "     come from the BRAND COLOURS block in the user prompt. Do NOT "
        "     invent hex values.\n"
        "  4. Only the HEADING is mandatory. Subheading, supporting_points, "
        "     and supporting_visual are OPTIONAL — omit them unless the "
        "     campaign brief clearly needs them.\n"
        "  5. Text placed on ANY background must be clearly legible — the "
        "     `primary_text` you pick must have STRONG contrast against "
        "     `primary_bg`. Do NOT combine similar-luminance colours."
        if hard_rule else
        "\n\nRULES (strong preference — honour unless a specific brief "
        "reason justifies deviation):\n"
        "  1. Logo placement should be TOP-LEFT unless the composition "
        "     genuinely benefits from a different corner.\n"
        "  2. Pick a CTA from the locked list.\n"
        "  3. Prefer BRAND COLOURS from the DNA for the primary colour "
        "     picks; deviate only when contrast forces you to.\n"
        "  4. Only the HEADING is mandatory. Subheading, supporting_points, "
        "     and supporting_visual are OPTIONAL — omit them unless the "
        "     campaign brief clearly needs them.\n"
        "  5. Text placed on any background must be legible."
    )
    cta_list = "\n".join(f"  - \"{c}\"" for c in cta_vocab)

    return f"""You are the ELABORATION phase of an Image Director for designer-grade social media posts.

Phase A already gave you THREE candidate design concepts for this brief. Your job here is to (1) pick the ONE concept that best fits the brief AND is most different from any past designs shown in the AVOID block, and (2) turn that concept into a full free-form image prompt for the image renderer.

Think of yourself as a senior designer given three junior sketches — you pick the strongest, then finalise it into something a printer could execute. Do not merge the concepts. Do not fall back to a "safe" hybrid. Pick one and commit.

CRITICAL — the image_prompt is FREE-FORM PROSE
The `image_prompt` field is where the actual composition lives. Describe the entire scene in natural language: what the canvas looks like, where the heading sits, whether there's a supporting visual (there often ISN'T), what accent shapes appear, what the mood is. This is NOT a form to fill — it's a design brief you'd write to a print designer. 250-450 words is the sweet spot.

The image_prompt MUST specify verbatim:
  - The exact heading text (in quotes) and roughly how big / where placed
  - The exact CTA text (in quotes) and what it looks like (pill button, underlined link, etc.)
  - Where the logo goes (top-left unless you have a specific reason)
  - Whether there's a supporting visual — if yes, describe it concretely; if no, say "no supporting visual, empty space to the right/below"
  - Whether there's a subheading or bullet points — most posts don't need them; when a post is just heading + CTA, SAY THAT EXPLICITLY

RESTRAINT is a valid design choice. If the concept is "typographic hero, no supporting visual", the image_prompt should describe a mostly-empty canvas with big text. Do not add fake support text or invented visuals to feel "complete". A great designer knows when to leave whitespace.

CTA VOCABULARY (pick EXACTLY one, verbatim):
{cta_list}

Do NOT invent a CTA outside this list. If none quite fits, pick the closest.

FIXED PLACEMENTS (hard rules — apply to EVERY design regardless of composition):
  • CTA button placement: ALWAYS bottom-right corner, rendered as a rounded pill in the accent colour with white text. When you write the `image_prompt`, describe the CTA as sitting in the bottom-right — never centre it, never move it to bottom-left.
  • Logo placement: ALWAYS top-left corner. When you write the `image_prompt`, describe the logo as sitting in the top-left — never elsewhere.

ASPECT RATIO: pick ONE of "1:1", "4:5", "9:16", "16:9", "1.91:1" based on the campaign — feed hero → 1:1 or 4:5, stories/reels → 9:16, LinkedIn banners → 1.91:1, landscape hero → 16:9. Vary the ratio across generations when the brief allows it — do not default to 4:5 every time.

COLOURS: use the BRAND COLOURS listed in the user prompt. Pick the pair that gives the BEST contrast for text legibility. Do NOT pair two similar luminances. Do NOT invent new hex values.
{hard_rule_block}

OUTPUT SCHEMA (return ONLY this JSON — no code fences, no prose):
{{
  "picked_concept_idx":   0,       // integer — index of the concept you picked from the ideation output (0/1/2)
  "picked_concept_reason": "one sentence — why THIS concept beat the other two AND why it differs from any past designs shown in the AVOID block",
  "composition_summary":  "A short (5-10 words) freeform label describing the chosen composition — e.g. 'typographic hero, no supporting visual', 'stat-hero with 300% number and one-line caption', 'diagram of API connectors'. Used by the memory layer to prevent repetition — MUST honestly describe the composition.",
  "image_prompt":         "250-450 words of free-form design brief for the image renderer. Describe the WHOLE scene: layout, imagery (or lack thereof), text placement, mood, negative space. Include the exact heading and CTA text in quotes.",
  "heading":              "the exact heading text (also referenced verbatim inside image_prompt)",
  "cta_text":             "one of the CTA vocabulary strings verbatim (also referenced verbatim inside image_prompt)",
  "primary_bg":           "hex from the BRAND COLOURS block",
  "primary_text":         "hex from the BRAND COLOURS block, contrast-safe against primary_bg",
  "accent":               "hex from the BRAND COLOURS block, used for the CTA button and accent lines",
  "aspect_ratio":         "one of the 5 allowed ratios"
}}
"""


def _build_image_director_user_prompt(
    variant_type: str,
    campaign_brief: str,
    post_text: str,
    business_dna: Optional[dict],
    business_category: str,
    brand_name: str,
    primary_brand_color: str,
    avoid_block: str = "",
    concepts: Optional[list[dict]] = None,
) -> str:
    """User prompt for the ELABORATION phase.

    `concepts` is the list of 3 candidate concept dicts from Phase A
    (ideation). When None or empty, the elaborator runs without a menu
    (fallback path) and must invent a concept itself.

    `avoid_block` is the pre-formatted "AVOID THESE PAST DESIGNS" block
    from services.image_director_history. Empty string when this DNA has
    no recent signatures — the block is then omitted entirely.
    """
    variant_intent = {
        "viral_reach":       "Punchy, hook-driven — maximise thumb-stop reach on the feed.",
        "follower_growth":   "Value-driven — clear proof of authority, prompts a follow / CTA click.",
        "high_interaction":  "Question-based — designed to draw comments and saves.",
        "festival_variant":  "Culturally warm — festival-themed while staying brand-aligned.",
    }.get(variant_type, "Value-driven, clean and confident.")

    dna_block = _dna_summary_for_director(business_dna)

    # Format the 3 candidate concepts from Phase A. When ideation failed
    # (empty list), we fall back to a "invent your own concept" note.
    if concepts:
        concept_lines = []
        for i, c in enumerate(concepts):
            fam = str((c or {}).get("layout_family") or "unspecified")
            summ = str((c or {}).get("summary") or "").strip()
            why  = str((c or {}).get("why") or "").strip()
            concept_lines.append(
                f"  CONCEPT {i} — layout_family={fam!r}\n"
                f"     Summary: {summ}\n"
                f"     Why: {why}"
            )
        concepts_block = (
            "═══════════════════════════════════════════════════════════════════\n"
            "CANDIDATE CONCEPTS (from ideation phase — pick ONE and elaborate)\n"
            "═══════════════════════════════════════════════════════════════════\n"
            + "\n\n".join(concept_lines) + "\n"
            "═══════════════════════════════════════════════════════════════════\n\n"
            "IMPORTANT: The concepts above are shown in RANDOM ORDER. Position 0 "
            "is NOT the safe default — pick on merit and on how different the "
            "chosen concept is from the AVOID block above. Concepts have already "
            "been filtered to drop any that clone recent designs, so any "
            "remaining option is fair game.\n\n"
            "Set `picked_concept_idx` to the index (0, 1, or 2) of the concept "
            "you pick. Elaborate that one concept into a full image_prompt. "
            "Do not merge concepts; commit to one.\n\n"
        )
    else:
        concepts_block = (
            "(Ideation phase produced no concepts — invent your own composition "
            "for this brief, set picked_concept_idx to 0.)\n\n"
        )

    return f"""CAMPAIGN BRIEF (what the customer wants to post about):
{campaign_brief.strip()}

VARIANT INTENT — {variant_type}:
{variant_intent}

PLATFORM POST COPY (background context — do NOT paste onto the image):
{post_text.strip()[:1500]}

BRAND NAME: {brand_name or '(unknown)'}
BUSINESS CATEGORY: {business_category or '(unset)'}
BRAND COLOUR HINT (from Business DNA primary): {primary_brand_color or '(none)'}

═══════════════════════════════════════════════════════════════════
BUSINESS DNA — you MUST use these facts / colours only. No inventing.
═══════════════════════════════════════════════════════════════════
{dna_block}
═══════════════════════════════════════════════════════════════════

{avoid_block}{concepts_block}Pick a concept, elaborate it, return the JSON now."""


# ══════════════════════════════════════════════════════════════════════
# AGENT 1 — IMAGE DIRECTOR
# ══════════════════════════════════════════════════════════════════════
def _call_image_director(
    *,
    variant_type: str,
    campaign_brief: str,
    post_text: str,
    business_dna: Optional[dict],
    business_category: str,
    brand_name: str,
    primary_brand_color: str,
    avoid_block: str = "",
    recent_history: Optional[list[dict]] = None,
) -> dict:
    """Two-phase Image Director.

    Phase A (Ideation) — a small GPT-5-mini call sketches THREE distinct
    design concepts in prose, each a different layout family.

    Between phases — the concepts are diversified: any that clones a
    recent-history composition is dropped, then the survivors are
    SHUFFLED to strip position bias out of the elaboration pick.

    Phase B (Elaboration) — the main GPT-5.1 call sees those (shuffled,
    de-duped) concepts + the past-designs history and picks ONE, then
    produces a fully-elaborated free-form image_prompt plus minimal
    metadata (colour trio, CTA, heading, aspect_ratio).

    Returns the elaboration output, augmented with the ideation concepts
    (for logging + debugging).
    """
    # ── Phase A — IDEATION ───────────────────────────────────────────
    raw_concepts = _call_ideation(
        variant_type=variant_type,
        campaign_brief=campaign_brief,
        post_text=post_text,
        business_dna=business_dna,
        business_category=business_category,
        brand_name=brand_name,
        avoid_block=avoid_block,
    )

    # ── Diversify — filter history-clones, then shuffle ──────────────
    concepts = _diversify_concepts(raw_concepts, recent_history or [], variant_type)

    # ── Phase B — ELABORATION ────────────────────────────────────────
    client = _openai_client()
    system_prompt = _build_image_director_system_prompt(
        business_category, CTA_VOCABULARY
    )
    user_prompt = _build_image_director_user_prompt(
        variant_type=variant_type,
        campaign_brief=campaign_brief,
        post_text=post_text,
        business_dna=business_dna,
        business_category=business_category,
        brand_name=brand_name,
        primary_brand_color=primary_brand_color,
        avoid_block=avoid_block,
        concepts=concepts,
    )

    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=_IMAGE_DIRECTOR_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=3000,
    )
    dur = time.monotonic() - t0
    raw = (resp.choices[0].message.content or "").strip()
    logger.info(
        f"[image-director/elaboration] variant={variant_type} "
        f"model={_IMAGE_DIRECTOR_MODEL} dur={dur:.2f}s "
        f"tokens=in:{resp.usage.prompt_tokens}/out:{resp.usage.completion_tokens}"
    )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(f"[image-director] invalid JSON: {exc}\nRaw: {raw[:500]}")
        raise RuntimeError("Image Director produced invalid JSON") from exc

    # Enforce CTA lock — if the model went off-script, snap to closest fallback.
    cta = str(parsed.get("cta_text", "")).strip()
    if cta not in CTA_VOCABULARY:
        logger.warning(
            f"[image-director] CTA {cta!r} not in vocabulary — coercing to 'Learn More'"
        )
        parsed["cta_text"] = "Learn More"

    # Enforce aspect ratio.
    ar = str(parsed.get("aspect_ratio", "")).strip()
    if ar not in VALID_ASPECT_RATIOS:
        logger.warning(
            f"[image-director] aspect_ratio {ar!r} invalid — coercing to '1:1'"
        )
        parsed["aspect_ratio"] = "1:1"

    # Log which concept was picked + a preview of the composition + prompt.
    picked_idx = parsed.get("picked_concept_idx")
    picked_summary = ""
    if isinstance(picked_idx, int) and 0 <= picked_idx < len(concepts):
        picked_summary = str(concepts[picked_idx].get("summary", ""))[:80]

    # Attach the concept menu + picked slot to the brief for CSV / debug.
    parsed["_ideation_concepts"] = concepts
    parsed["_picked_concept_idx"] = picked_idx

    logger.info(
        f"[image-director/elaboration] variant={variant_type} "
        f"picked_concept_idx={picked_idx} "
        f"picked_summary={picked_summary!r} "
        f"composition={str(parsed.get('composition_summary', ''))[:80]!r} "
        f"aspect={parsed.get('aspect_ratio')!r} "
        f"heading={str(parsed.get('heading', ''))[:60]!r} "
        f"cta={parsed.get('cta_text')!r} "
        f"bg={parsed.get('primary_bg')!r} text={parsed.get('primary_text')!r} "
        f"accent={parsed.get('accent')!r} "
        f"prompt_chars={len(str(parsed.get('image_prompt', '')))}"
    )
    return parsed


# ══════════════════════════════════════════════════════════════════════
# AGENT 2 — IMAGE AGENT (gpt-image-2, quality=high)
# ══════════════════════════════════════════════════════════════════════
def _build_image_agent_prompt(director_brief: dict) -> str:
    """Compose the natural-language prompt the image model actually sees.

    In the two-phase pipeline, `image_prompt` is the primary output —
    it's a full 250-450 word design brief written by the Director.
    This function wraps it with the immutable overlays: exact-string
    heading + CTA (so the renderer can't paraphrase them), exact colour
    hex values (so the renderer can't substitute), and the aspect ratio.
    """
    heading      = str(director_brief.get("heading",       "")).strip()
    cta_text     = str(director_brief.get("cta_text",      "")).strip()
    primary_bg   = str(director_brief.get("primary_bg",    "")).strip()
    primary_text = str(director_brief.get("primary_text",  "")).strip()
    accent       = str(director_brief.get("accent",        "")).strip()
    aspect_ratio = str(director_brief.get("aspect_ratio",  "1:1")).strip()
    image_prompt = str(director_brief.get("image_prompt",  "")).strip()

    return f"""Generate a designer-grade social media post image at aspect ratio {aspect_ratio}.

DESIGN BRIEF (this is the composition — follow it faithfully):
{image_prompt}

MANDATORY EXACT-TEXT OVERLAYS (render EXACTLY as written, no typos, no paraphrasing, high legibility):
  • HEADING: "{heading}"
  • CTA BUTTON: "{cta_text}"  — placement is ALWAYS the BOTTOM-RIGHT corner, rendered as a rounded pill in the accent colour with white text. Do NOT move it to the centre, bottom-left, or anywhere else, even if the DESIGN BRIEF suggests otherwise.

COLOUR SYSTEM (use these exact hex values verbatim — no substitutions, no lightening / darkening):
  • Primary background: {primary_bg}
  • Primary text colour: {primary_text} (used for heading and body — MUST be clearly legible against {primary_bg})
  • Accent colour: {accent} (used for the CTA button and small accent details)

LOGO: place the attached brand logo in the TOP-LEFT corner, exactly as provided, unchanged in colour or shape. Do NOT move it elsewhere.

QUALITY BAR — this is not negotiable:
  • Feel like a top brand designer made it (think Vercel, Stripe, Linear, Framer).
  • Sharp, print-ready quality. NO blur, NO bokeh, NO soft-focus, NO watermarks.
  • Text is razor-sharp, correctly spelled, high contrast, no fake gibberish.
  • No AI-slop artefacts (extra fingers, warped edges, melted objects, invented text).
  • No gratuitous 3D chrome, glass, or neon — restraint is the point.
  • No generic stock-photo overlays. If a supporting visual is included, it must feel intentional.
"""


def _call_image_agent(
    director_brief: dict,
    logo_bytes: Optional[bytes],
) -> tuple[bytes, dict]:
    """Call gpt-image-2 (quality=high) with the design brief.

    Uses the OpenAI `images.edit` endpoint when a logo is attached so the
    provided logo is preserved verbatim in the top-left / configured
    corner. Falls back to `images.generate` when no logo bytes are
    available.

    Returns (png_bytes, meta) where meta contains model + timing info for
    downstream CSV logging.
    """
    client = _openai_client()
    aspect_ratio = str(director_brief.get("aspect_ratio", "1:1")).strip()
    size = _ASPECT_TO_SIZE.get(aspect_ratio, _DEFAULT_SIZE)
    prompt_text = _build_image_agent_prompt(director_brief)

    t0 = time.monotonic()
    if logo_bytes:
        resp = client.images.edit(
            model=_IMAGE_AGENT_MODEL,
            image=[("logo.png", BytesIO(logo_bytes), "image/png")],
            prompt=prompt_text,
            quality=_IMAGE_AGENT_QUALITY,
            size=size,
        )
    else:
        resp = client.images.generate(
            model=_IMAGE_AGENT_MODEL,
            prompt=prompt_text,
            quality=_IMAGE_AGENT_QUALITY,
            size=size,
        )
    dur = time.monotonic() - t0

    if not resp.data or not getattr(resp.data[0], "b64_json", None):
        raise RuntimeError(
            f"Image Agent returned no image bytes (model={_IMAGE_AGENT_MODEL}, "
            f"quality={_IMAGE_AGENT_QUALITY}, size={size})"
        )
    png_bytes = base64.b64decode(resp.data[0].b64_json)

    logger.info(
        f"[image-agent] model={_IMAGE_AGENT_MODEL} quality={_IMAGE_AGENT_QUALITY} "
        f"size={size} rendered {len(png_bytes):,} bytes in {dur:.2f}s "
        f"aspect={aspect_ratio!r} (logo={'yes' if logo_bytes else 'no'})"
    )
    return png_bytes, {
        "model":        _IMAGE_AGENT_MODEL,
        "quality":      _IMAGE_AGENT_QUALITY,
        "size":         size,
        "render_time":  round(dur, 2),
        "size_bytes":   len(png_bytes),
    }


# ══════════════════════════════════════════════════════════════════════
# ORCHESTRATOR — per-variant end-to-end
# ══════════════════════════════════════════════════════════════════════
def run_designer_grade_variant(
    *,
    variant_type: str,
    post_text: str,
    campaign_brief: str,
    business_dna: Optional[dict],
    business_category: str,
    brand_name: str,
    primary_brand_color: str,
    logo_bytes: Optional[bytes],
    user_id: Optional[int] = None,
    product_name: Optional[str] = None,
) -> dict:
    """End-to-end for ONE variant of the designer-grade post style.

    When `user_id` is provided, the Image Director sees a 7-day
    "avoid these past designs" block sourced from
    `business_dna._image_director_history`, and the completed signature
    is recorded back onto the DNA on success. Without `user_id` the
    memory hooks are no-ops — the pipeline still works, just without
    the rotation guard.
    """
    logger.info(
        f"[designer-grade] variant={variant_type} BEGIN — bypassing Art Director + gpt-image-2"
    )

    # ── 7-day memory: load recent signatures for this DNA ─────────────
    avoid_block = ""
    recent: list[dict] = []
    try:
        from services.image_director_history import (
            recent_signatures, build_avoid_block,
        )
        recent = recent_signatures(business_dna, product_name=product_name)
        avoid_block = build_avoid_block(recent) if recent else ""
        if recent:
            logger.info(
                f"[designer-grade] variant={variant_type} — 7d memory found "
                f"{len(recent)} recent design(s) for this DNA; injecting "
                f"AVOID block into Image Director prompt"
            )
    except Exception as _mem_read_err:
        logger.warning(
            f"[designer-grade] 7d memory read failed (non-fatal): {_mem_read_err}"
        )

    a1_t0 = time.monotonic()
    brief = _call_image_director(
        variant_type=variant_type,
        campaign_brief=campaign_brief,
        post_text=post_text,
        business_dna=business_dna,
        business_category=business_category,
        brand_name=brand_name,
        primary_brand_color=primary_brand_color,
        avoid_block=avoid_block,
        recent_history=recent,
    )
    a1_time = round(time.monotonic() - a1_t0, 2)

    a2_t0 = time.monotonic()
    png_bytes, agent_meta = _call_image_agent(brief, logo_bytes)
    a2_time = round(time.monotonic() - a2_t0, 2)

    logger.info(
        f"[designer-grade] variant={variant_type} DONE — "
        f"director={a1_time}s agent={a2_time}s "
        f"aspect={brief.get('aspect_ratio')} cta={brief.get('cta_text')!r}"
    )

    # ── 7-day memory: persist this signature so the next generation
    # for the same DNA sees it in the AVOID block ────────────────────
    if user_id:
        try:
            from services.image_director_history import record_signature
            record_signature(
                user_id=user_id,
                product_name=product_name,
                director_brief=brief,
                variant_type=variant_type,
            )
        except Exception as _mem_write_err:
            logger.warning(
                f"[designer-grade] 7d memory write failed (non-fatal): "
                f"{_mem_write_err}"
            )

    return {
        "director_brief":    brief,
        "image_prompt":      _build_image_agent_prompt(brief),
        "png_bytes":         png_bytes,
        "agent1_time_s":     a1_time,
        "agent2_time_s":     a2_time,
        "director_model":    _IMAGE_DIRECTOR_MODEL,
        "image_agent_model": agent_meta["model"],
        "aspect_ratio":      brief.get("aspect_ratio", "1:1"),
    }
