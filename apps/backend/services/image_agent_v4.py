"""Image Agent v4 — pure Gemini image generation, no templates, no compositor.

Goal of v4:
  • Stop the freeform pipeline + the 11 pre-defined PIL templates temporarily.
  • Generate N distinct image variants directly from the AI-recommended text
    content (the variant the orchestrator's recommendation.best_variant points
    at) plus the campaign brief.
  • Let the image model fully control the rendering — no text overlay
    compositing, no aspect-ratio template fitting. The image IS the post.

Inputs the agent receives:
  • refined_brief                — strategic brief (USER GOAL + TOPIC + etc.)
  • recommended_copy_per_platform — { platform: copy_text } for the recommended
                                    variant, one copy per platform the user picked
  • primary_color                — hex from DNA (used as a colour cue in prompt)
  • aspect_ratio                 — "1:1" / "9:16" / "16:9" / "4:5" / "3:4" / "2:3"
  • n_variants                   — default 3

Returns: list of N dicts with { url, prompt, variant_idx, pipeline: "v4" }.
A failed variant returns None at its slot — caller should filter.

Model selection:
  Default is the env var IMAGE_AGENT_V4_MODEL, which falls back to
  `gemini-3.1-flash-image`. If that model name is not available in your project,
  set IMAGE_AGENT_V4_MODEL=gemini-2.5-flash-image as an environment override.
  The SDK call signature is identical between 2.5 and (announced) 3.x image
  models — only the model id string changes.
"""

import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

from google.genai import types

from core.config import GEMINI_API_KEY, S3_BUCKET_NAME
from core.s3_utils import get_s3_client, get_s3_url

logger = logging.getLogger("pipelyt.image_v4")

# Model id can be overridden via env var. Defaults to the next-gen Gemini image
# model the user requested; falls back automatically inside the call wrapper if
# the model is unavailable.
DEFAULT_MODEL = os.getenv("IMAGE_AGENT_V4_MODEL", "gemini-3.1-flash-image")
FALLBACK_MODEL = "gemini-2.5-flash-image"

# ────────────────────────────────────────────────────────────────────────────
# FEW-SHOT REFERENCE POOL
# ────────────────────────────────────────────────────────────────────────────
# Per-variant rotation of GOOD references + a constant set of BAD anti-examples.
# Gemini 3.1 Flash Image accepts up to 14 reference images per request
# (10 object + 4 character). Each variant sends:
#   5 good + 3 bad + 1 logo (when present) = max 9 images. Well under the cap.
#
# Per-variant rotation across the 15 curated references gives the 3 parallel
# variants three NON-OVERLAPPING good-example sets — this is the strongest
# steering signal we have against issue I-2 (every variant looking the same).
# The 3 bad anti-examples are constant across variants so the model always
# knows exactly which failure modes to avoid.
#
# Pool source: docs/image_generation_research.md §6.3.
_FEWSHOT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fewshot"

# Each reference is now a TRIPLE (path, expected_user_input, rationale).
#
#   path                 — the file under apps/backend/assets/fewshot/
#   expected_user_input  — a plausible CAMPAIGN BRIEF that would lead a senior
#                          designer to produce this image. Teaches the model
#                          "for THIS kind of input, produce THIS kind of output."
#   rationale            — for GOOD: WHY this image is good for that input
#                          for BAD : WHAT WENT WRONG + WHY this is bad
#
# Set A → Variant 1 (typography & editorial focus)
# Set B → Variant 2 (structured & infographic focus)
# Set C → Variant 3 (photo-led & creative focus)
_GOOD_SETS: list[list[tuple[str, str, str]]] = [
    # ──────────────────────────── Set A — typography & editorial
    [
        (
            "service-based/pwc_service_2.jpg",
            "PwC just sold its Indirect Tax technology platform Edge to Fonoa. "
            "Announce this strategic deal on LinkedIn to enterprise tax and finance "
            "leaders. Tone: corporate, authoritative, professional services firm.",
            "GOOD because: type IS the design. The full peach-gradient canvas holds "
            "only the bold serif headline and one minimal brand graphic. Zero imagery, "
            "zero glass, zero 3D. Brand wordmark is REAL, used ONCE, top-left. "
            "Generous breathing room. The composition trusts the message — exactly "
            "what a senior designer at PwC's level would produce."
        ),
        (
            "service-based/accenture_service_1.jpg",
            "Position Accenture as the partner helping high-tech organizations redefine "
            "their cloud strategy. Audience: enterprise IT leaders. Tone: aspirational, "
            "expert, confident.",
            "GOOD because: ONE editorial photo (man on cliff above clouds = aspirational "
            "metaphor for vision and altitude). ONE bold sans headline. ONLY the key "
            "phrase 'they're redefining it' is in brand purple — restrained colour "
            "accent, not rainbow. Real Accenture `>` glyph used ONCE in the corner. "
            "Zero glow, zero glass, zero AI render."
        ),
        (
            "product-based/lifesight_product_2.jpg",
            "Announce Lifesight's new product, the 'Unified Measurement OS', with the "
            "tagline 'The Science Behind Smarter, More Profitable Media Decisions'. "
            "Audience: performance marketers and growth leaders.",
            "GOOD because: when the announcement IS the message, text alone is enough. "
            "Real brand wordmark + heading + subhead, clear typographic hierarchy, "
            "neutral gradient background. No decoration. The design trusts that the "
            "wordmark + product name speak for themselves."
        ),
        (
            "product-based/mulesoft_product_3.jpg",
            "Promote MuleSoft's session at Gartner APPS. Headline: 'Manage and Govern AI "
            "Ecosystems with Agent Fabric'. Date: Tuesday, June 2, 2026, 3:15 PM, Forum "
            "114. Audience: enterprise architects.",
            "GOOD because: single brand orb top-right (real MuleSoft mark, used ONCE). "
            "Hashtag + headline + time = exactly what an event card needs. Cyan accent "
            "appears only inside ONE phrase ('Agent Fabric') for emphasis — not on "
            "every other letter. No imagery; gradient + typography carries it all."
        ),
        (
            "festival/pwc_festival_1.jpg",
            "Wish our LinkedIn followers Eid al-Adha Mubarak from PwC. Culturally "
            "respectful, minimal text, atmospheric photo. No long messaging.",
            "GOOD because: cultural greetings need RESTRAINT, not fireworks. ONE "
            "atmospheric photo (lantern + crescent moon = symbolic, not literal). "
            "SHORT greeting in elegant serif. Brand wordmark top-left, used ONCE. The "
            "bokeh background creates mood without competing with the greeting."
        ),
    ],
    # ──────────────────────────── Set B — structured & infographic
    [
        (
            "canva/canva_5_milestones.jpg",
            "Showcase Timmerman Industries' five-year journey: 2021 Company "
            "Establishment → 2022 First Major Achievement → 2023 Business & Team "
            "Expansion → 2024 Innovation & Digital Growth → 2025 Sustainable Growth. "
            "Tone: confident, structured.",
            "GOOD because: structured infographics use FLAT icons and breathing room "
            "— never 3D. Half-donut + dotted curve connect each milestone pill. ONE "
            "simple line icon per milestone. Generous whitespace on the left for the "
            "heading. Brand mark used ONCE. The structure SERVES the data."
        ),
        (
            "canva/canva_3_pioneers.jpg",
            "Introduce Wardiere Company Inc. as a forward-thinking business "
            "consultancy. Position with the tagline 'WE ARE PIONEERS AND ROLE MODELS "
            "IN EVERY RESPECT'. List three services (Branding Strategy, Data Analyst "
            "Strategy, Advertising Campaign) and include contact information.",
            "GOOD because: when the brief has MULTIPLE services to cover, a clean "
            "column grid is the right answer. Photo on top with three angled overlay "
            "panels carrying the tagline. Three-column service section with simple "
            "icons. Contact band at the foot ties it together. Brand mark used ONCE."
        ),
        (
            "service-based/tcs_service_2.jpg",
            "Position TCS Sustainability Services. Tell the concept-to-reality story "
            "visually: a blueprint sketch on the left transitioning into a real "
            "wind-farm photo on the right. Tagline: 'NOW FOR NEXT'. Brand voice: "
            "enterprise, capability-led.",
            "GOOD because: asymmetric 50/50 splits can carry a STRONG metaphor "
            "(planning becoming reality). Real TCS + Tata corporate marks (both used "
            "ONCE, opposite corners). The yellow tagline crosses the join — that's "
            "deliberate design, not random placement. Two-toned palette stays "
            "disciplined."
        ),
        (
            "product-based/lifesight_product_1.jpg",
            "Demonstrate how Lifesight's Causal Engine answers budget allocation "
            "questions. Show the actual product interface: a question prompt, the "
            "engine processing, and the resulting bar-chart recommendation.",
            "GOOD because: when the brief is product-focused, SHOW the actual product. "
            "Squircle icons connected by dotted lines = clean product flow. Real "
            "chart data (Retargeting 25%, Search Ads 45%, Video Ads 30%) — "
            "believable, not invented. Single brand wordmark top-left. Footer "
            "colour-band ties to brand."
        ),
        (
            "canva/canva_2_graphic_design.jpg",
            "Promote Wardiere Inc.'s graphic-design services. List services "
            "(Logo Design, Branding Materials, Marketing Collateral, Digital Assets, "
            "Illustrations, Website Graphics), include a team photo, contact "
            "information, and website.",
            "GOOD because: multi-element layouts CAN work IF balanced. Photo, text "
            "column, service-list card with bullet pills, URL pill — all clearly "
            "separated, each region breathes. Brand mark used ONCE."
        ),
    ],
    # ──────────────────────────── Set C — photo-led & creative
    [
        (
            "service-based/pwc_service_1.jpg",
            "Promote PwC's Global Health Report. Focus on how consumers and AI "
            "advances are transforming healthcare. Editorial tone, photo of a real "
            "person engaging with healthcare technology.",
            "GOOD because: magazine-cover discipline — top half typography on a soft "
            "gradient, bottom half full-bleed candid photo. The woman + tablet is a "
            "BELIEVABLE scene (not staged stock-photo cliché). Brand wordmark top-"
            "left, used ONCE. Generous breathing room above the headline."
        ),
        (
            "canva/canva_6_creative_agency.jpg",
            "Position Liceria & Co. as a creative and innovative agency. Include a "
            "headline ('We are a Creative Agency'), a body paragraph about the team, "
            "a 'Join Now' CTA, and contact information.",
            "GOOD because: when the brief has substantial copy AND a hero subject, a "
            "60/40 photo-text split is the right answer. Photo on the right anchors "
            "the identity, text block on the left does the heavy lifting. Brand "
            "monogram used ONCE."
        ),
        (
            "canva/canva_8_construction.jpg",
            "Promote a construction company offering Renovation & Remodeling, "
            "Infrastructure Development, General Contracting, and Design & Build. "
            "Show team-and-site photography and a 'Get In Touch' CTA with phone and "
            "website.",
            "GOOD because: triangular photo crops give the canvas energy WITHOUT "
            "resorting to 3D, glass, or glow. Each service has its own row with a "
            "small icon. CTA bar with phone + website at the foot. Photo cuts are "
            "deliberate geometry — not random."
        ),
        (
            "canva/canva_7_build_your_business.jpg",
            "Promote a youth-focused business-growth program. Bold visual identity, "
            "photo of a young professional, organic shapes for energy. Trendy but "
            "still polished.",
            "GOOD because: playful organic shapes + photo cutout work for younger / "
            "lifestyle brands WITHOUT becoming AI-generated. Limited palette (two "
            "accent colours + white). Photo lives inside a deliberate wavy frame, "
            "not floating inside a glass panel."
        ),
        (
            "product-based/mulesoft_product_1.jpg",
            "Position MuleSoft as the integration platform that goes wherever your "
            "team works. Highlight integrations with Teams, Slack, and AI "
            "assistants. Headline pills: 'Enhanced UI', 'Developer Hub', "
            "'Headless UX'.",
            "GOOD because: a branded product collage with pill elements stays clean "
            "when elements are properly spaced. Real MuleSoft brand orb used ONCE. "
            "Integration icons in a single row = clean information design, not a "
            "rainbow logo soup."
        ),
    ],
]

# 3 anti-examples sent on every variant. Each is a TRIPLE: path + the input the
# model was trying to serve + a structured 'what went wrong / why bad' block.
_BAD_EXAMPLES: list[tuple[str, str, str]] = [
    (
        "bad/bad_zyntegrate_glass_cube.jpg",
        "Position Zyntegrate as an autonomous integration platform that "
        "orchestrates complex enterprise systems. Show how data flows through the "
        "platform.",
        "WHAT WENT WRONG:\n"
        "  • TYPOS RENDERED AS TEXT — 'Compotazs' (should be 'Components'), 'Data "
        "ranting' (should be 'Routing'), 'Layoout' (should be 'Layout').\n"
        "  • DUPLICATE ICONS — 'REST API' appears twice.\n"
        "  • DUPLICATE BRAND MENTION — 'The Zyntegrate Platform' label PLUS the "
        "bottom-right 'Zyntegrate Autonomous Integration Platform' wordmark.\n"
        "  • DEFAULT AI ARCHETYPE — heavy 3D glass cube on a dark gradient with "
        "blue/green glow on a void background.\n\n"
        "WHY THIS IS BAD: misspelled text in the image is an automatic reject. The "
        "AI-render aesthetic screams 'machine output, not designer work'. The model "
        "defaulted to its built-in visualisation style instead of considering "
        "whether that style suited the brief at all. DO NOT produce anything like "
        "this."
    ),
    (
        "bad/bad_spenzo_brand_swatches.jpg",
        "Promote Spenzo AI's ability to connect data silos and streamline marketing "
        "workflows. Use the brand palette: primary orange #FF5722, dark #212B36.",
        "WHAT WENT WRONG:\n"
        "  • HEX CODES RENDERED AS VISIBLE TEXT — '#FF5722', '#212B36', '#FFFFFF', "
        "'#FFFFFF' (the hex list itself even has a duplicate).\n"
        "  • BRAND-GUIDE VOCABULARY RENDERED — 'Brand Color', 'Inter Family', "
        "'Tone tone include' (literally the word 'tone' typed twice).\n"
        "  • DUPLICATE BRAND ICONS — two Meta logos drawn in the diagram.\n"
        "  • PALETTE SWATCHES drawn directly onto the social post.\n\n"
        "WHY THIS IS BAD: the model interpreted 'use the brand palette' as 'DRAW "
        "the brand palette'. Hex codes, palette swatches, and brand-guide labels "
        "(font names, tone descriptors) are SPECIFICATIONS for the designer — "
        "they must NEVER appear as visible elements on the published post. This is "
        "one of the worst kinds of AI image leak. DO NOT produce anything like "
        "this."
    ),
    (
        "bad/bad_zyntegrate_ai_agents.jpg",
        "Promote Zyntegrate AI Agents that automate complex enterprise "
        "integrations. Show how the platform orchestrates Salesforce, AWS, and "
        "legacy databases.",
        "WHAT WENT WRONG:\n"
        "  • MISSPELLED VERB — 'Orchestate' instead of 'Orchestrate'.\n"
        "  • BROKEN PUNCTUATION — 'Months→Minutes', 'SOC 2, 'Active, 'Connected, "
        "'Synced — every label has an UNCLOSED opening apostrophe.\n"
        "  • INVENTED BRAND MARK — the Salesforce logo is replaced by a "
        "hallucinated gibberish wordmark ('Swrve' / random shape) instead of the "
        "real Salesforce cloud.\n"
        "  • DECORATIVE NOISE — stray plus-signs and dots in the top-right corner "
        "used as filler.\n\n"
        "WHY THIS IS BAD: misspelled verbs in headlines = automatic reject. "
        "Inventing brand marks for real third-party companies is an integrity "
        "failure (Pipelyt does not have permission to redesign Salesforce's mark; "
        "audiences immediately recognise the fake). Unclosed apostrophes are an "
        "obvious AI artefact. DO NOT produce anything like this."
    ),
]


def _load_pil(rel_path: str):
    """Open a fewshot reference image as PIL.Image, RGBA-normalised. Returns
    None if the file is missing — callers degrade gracefully."""
    try:
        from PIL import Image as PILImage
        full = _FEWSHOT_DIR / rel_path
        img = PILImage.open(full)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        return img
    except Exception as e:
        logger.warning(f"[image_v4] fewshot load failed for {rel_path}: {e}")
        return None


def _build_fewshot_contents(variant_idx: int, n_variants: int) -> list:
    """Build the multimodal `contents` segment.

    Structure (per example):
        ─ EXAMPLE N HEADER (good or bad)
        ─ EXPECTED USER INPUT (the brief a user would have written)
        ─ EXPECTED IMAGE (the PIL reference)
        ─ RATIONALE (why good / what went wrong + why bad)

    This is true in-context learning: input → output → explanation triples.
    The model learns "for THIS kind of input, produce THIS kind of output;
    for THAT kind of input, do NOT produce THIS kind of output."

    Variant 0 → Set A, Variant 1 → Set B, Variant 2 → Set C (mod len).
    """
    parts: list = []
    good_set = _GOOD_SETS[variant_idx % len(_GOOD_SETS)]
    set_letter = chr(ord("A") + variant_idx % len(_GOOD_SETS))

    # ── GOOD section header ──────────────────────────────────────────
    parts.append(
        "════════════════════════════════════════════════════════════════════\n"
        f"GOOD EXAMPLES — input → output → rationale triples (Set {set_letter})\n"
        "════════════════════════════════════════════════════════════════════\n"
        "Below are FIVE worked examples. Each shows: the kind of CAMPAIGN BRIEF\n"
        "a user would have written, followed by the EXPECTED IMAGE we would\n"
        "produce for that brief, followed by WHY this image is good. Study the\n"
        "pattern — for the real brief above, you should produce an image of\n"
        "comparable craft, comparable restraint, and a composition that fits\n"
        "the brief as cleanly as these examples fit theirs."
    )
    for i, (rel_path, expected_input, rationale) in enumerate(good_set, start=1):
        img = _load_pil(rel_path)
        if img is None:
            continue
        parts.append(
            f"\n──── GOOD EXAMPLE {i} ────\n\n"
            f"EXPECTED USER INPUT (campaign brief for this example):\n"
            f"  {expected_input}\n\n"
            f"EXPECTED IMAGE we would produce for that input:"
        )
        parts.append(img)
        parts.append(
            f"WHY THIS IMAGE IS GOOD FOR THAT INPUT:\n  {rationale}"
        )

    # ── BAD section header ──────────────────────────────────────────
    parts.append(
        "\n════════════════════════════════════════════════════════════════════\n"
        "BAD ANTI-EXAMPLES — input → BAD output → what went wrong + why bad\n"
        "════════════════════════════════════════════════════════════════════\n"
        "Below are THREE worked anti-examples. Each shows: the kind of brief a\n"
        "user wrote, the BAD image the model produced (which we do NOT want),\n"
        "and a precise breakdown of WHAT WENT WRONG and WHY THE IMAGE IS BAD.\n"
        "Do NOT produce anything that resembles these failure modes when you\n"
        "design the final image for the real brief above."
    )
    for i, (rel_path, expected_input, what_went_wrong) in enumerate(_BAD_EXAMPLES, start=1):
        img = _load_pil(rel_path)
        if img is None:
            continue
        parts.append(
            f"\n──── BAD ANTI-EXAMPLE {i} ────\n\n"
            f"EXPECTED USER INPUT for this anti-example:\n"
            f"  {expected_input}\n\n"
            f"IMAGE THE MODEL ACTUALLY PRODUCED (this is the OUTPUT WE DO NOT WANT):"
        )
        parts.append(img)
        parts.append(what_went_wrong)

    # ── Closer ──────────────────────────────────────────────────────
    parts.append(
        "\n════════════════════════════════════════════════════════════════════\n"
        "END OF REFERENCES.\n"
        "════════════════════════════════════════════════════════════════════\n"
        "If a brand-logo image is attached AFTER this point, treat it per [R4]\n"
        "(use exactly or omit). Now design the FINAL image for the real\n"
        "campaign brief at the top of this request:\n"
        "  • Match the craft, restraint, and composition discipline of the\n"
        "    GOOD examples above.\n"
        "  • Do NOT replicate any of the failure modes shown in the BAD\n"
        "    anti-examples.\n"
        "  • Output ONE high-resolution social-media image only."
    )
    return parts


def _strip_image_metadata(image_bytes: bytes) -> bytes:
    """Strip EXIF / metadata for smaller payloads + privacy. Best-effort.

    Re-encodes to PNG (lossless) so we preserve the model's pixel data exactly
    — useful for text rendering, sharp logos, and any downstream colour-grading
    we might apply. RGBA mode is preserved so transparent regions survive.
    """
    try:
        from PIL import Image
        img = Image.open(BytesIO(image_bytes))
        # Drop palette / CMYK / "L" greyscale into a publishable mode, but keep
        # RGBA so alpha channels (rare from Gemini, but possible) aren't lost.
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.mode else "RGB")
        out = BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception:
        return image_bytes


def _format_copy_block(recommended_copy_per_platform: dict) -> str:
    """Render the per-platform recommended copy into a readable block for the prompt."""
    if not recommended_copy_per_platform:
        return "(no copy provided)"
    blocks = []
    for platform, copy in recommended_copy_per_platform.items():
        if not copy:
            continue
        blocks.append(f"--- {platform.upper()} ---\n{copy}")
    return "\n\n".join(blocks) if blocks else "(no copy provided)"


def _select_caption_block_for_image(
    content_block: dict,
    selected_platforms: list,
) -> tuple[str, dict]:
    """Pick ALL variants from ONE platform for the image stage.

    Priority order (user's rule):
      1. LinkedIn — if LinkedIn is in selected_platforms AND has at least
         one non-empty variant, return its full variant dict.
      2. Otherwise — first selected platform whose variant dict has at
         least one non-empty entry.
      3. Otherwise — any platform with non-empty variants (last-resort).
      4. Otherwise — ("none", {}).

    Returns (platform_name, {variant_key: text}) — variant_key is one of
    viral_reach / high_interaction / follower_growth / festival_variant.
    """
    content_lc = {
        str(p).lower(): v
        for p, v in (content_block or {}).items()
        if isinstance(v, dict)
    }

    def _non_empty(d: dict) -> dict:
        return {k: v for k, v in d.items() if isinstance(v, str) and v.strip()}

    # Priority 1 — LinkedIn explicitly
    if "linkedin" in [str(p).lower() for p in (selected_platforms or [])]:
        li = _non_empty(content_lc.get("linkedin") or {})
        if li:
            return ("linkedin", li)

    # Priority 2 — first selected platform with non-empty variants
    for p in selected_platforms or []:
        v = _non_empty(content_lc.get(str(p).lower()) or {})
        if v:
            return (str(p).lower(), v)

    # Priority 3 — any platform that has at least one variant
    for p, variants in content_lc.items():
        v = _non_empty(variants)
        if v:
            return (p, v)

    return ("none", {})


def _format_caption_variants_block(platform: str, variants: dict) -> str:
    """Render the selected platform's variant set as a labelled block for the
    AD prompt. The AD reads this and chooses which variant's phrase to lift
    verbatim into the rendered image."""
    if not variants:
        return f"PLATFORM: {platform or 'none'}\n(no caption variants available)"
    lines = [f"PLATFORM: {platform}", ""]
    # Deterministic order — the AI-recommended viral_reach first (most-used),
    # then the rest, so the AD sees the strongest pitch at the top.
    order = ["viral_reach", "high_interaction", "follower_growth", "festival_variant"]
    seen = set()
    for k in order:
        if k in variants and variants[k]:
            lines.append(f"--- {k.upper()} ---")
            lines.append(variants[k])
            lines.append("")
            seen.add(k)
    # Catch any extra keys we don't recognise.
    for k, v in variants.items():
        if k not in seen and isinstance(v, str) and v.strip():
            lines.append(f"--- {k.upper()} ---")
            lines.append(v)
            lines.append("")
    return "\n".join(lines).rstrip()


# Deterministic order in which we distribute platform variants across the 3
# parallel image variants. First image gets viral_reach, second gets
# high_interaction, third gets follower_growth — so each image is paired
# with a DIFFERENT caption variant from the same platform.
_VARIANT_DISTRIBUTION_ORDER = ["viral_reach", "high_interaction", "follower_growth", "festival_variant"]


# ────────────────────────────────────────────────────────────────────────────
# FORCED STYLE BAND ASSIGNMENT (one band per variant slot)
# ────────────────────────────────────────────────────────────────────────────
# Without this, the AD picks "infographic" for every B2B/technical brief and
# all 3 variants collapse into the same visual archetype. By locking each
# variant index to a specific style band, we guarantee:
#   • genuine batch diversity (one photo, one typography, one infographic),
#   • drastically reduced text surface area (fewer typos),
#   • concentrated third-party-logo risk in one variant rather than three.
#
# Each band's `allowed_directions` is the closed list the AD must pick its
# `direction` slug from. The AD prompt enforces this as a hard rule.
# ────────────────────────────────────────────────────────────────────────────
# DYNAMIC STYLE CATEGORY ASSIGNMENT (per brief, not per fixed variant slot)
# ────────────────────────────────────────────────────────────────────────────
# Earlier approach: variant 1 → always people-led, 2 → always typography,
# 3 → always infographic. Result: every campaign produced the same 3 style
# families regardless of brief content.
#
# New approach: hash the refined brief, deterministically pick 3 DIFFERENT
# style categories from the 8 below. Same brief always picks the same 3
# categories (reproducible). Different briefs pick different combinations.
# This means a launch announcement might get [typography-led, product,
# template-layout] while a partnership post gets [photo-led, illustration,
# infographic].
_STYLE_CATEGORIES: list[dict] = [
    {
        "slug": "typography-led",
        "name": "Typography-Led (type IS the design)",
        "examples": [
            "bold-sans-serif-poster",
            "editorial-serif-headline",
            "all-caps-statement",
            "quote-card-flat",
            "pull-quote-with-attribution",
            "type-as-illustration",
            "numbered-list-typography",
            "statistic-led-poster",
            # NEW: extra typography styles
            "handwritten-script-feature",
            "variable-weight-typographic-poster",
            "monospace-tech-display",
            "typographic-stack-vertical",
            "italic-emphasis-headline",
            "letterform-as-frame",
            "kinetic-type-still",
            "headline-with-underline-accent",
        ],
        "no_third_party_logos_default": True,
    },
    {
        "slug": "photo-led-editorial",
        "name": "Photo-Led Editorial (real humans, real settings)",
        "examples": [
            "candid-workplace-portrait",
            "posed-headshot-overlay",
            "lifestyle-moment",
            "customer-testimonial-portrait",
            "documentary-team-photo",
            "behind-the-scenes-candid",
            "action-shot-in-progress",
            "magazine-cover-composition",
            "hands-only-product",
            "atmospheric-environment",
            # NEW: extra photo styles
            "aerial-overhead-photo",
            "close-up-emotion-portrait",
            "environmental-wide-shot",
            "subject-with-product-in-use",
            "silhouette-against-light",
            "moody-low-key-portrait",
            "bright-natural-light-portrait",
            "multi-subject-conversation",
            "split-second-action-frozen",
            "street-photography-style",
            "studio-portrait-clean-backdrop",
            "field-documentary-environment",
        ],
        "no_third_party_logos_default": True,
    },
    {
        "slug": "flat-infographic",
        "name": "Flat Infographic / Diagram (line icons, minimal text)",
        "examples": [
            "flat-timeline-horizontal",
            "step-by-step-process-flow",
            "comparison-split",
            "hub-and-spoke-connector",
            "three-column-structured",
            "layered-architecture",
            "tree-hierarchy-chart",
            "venn-overlap-diagram",
            "donut-with-callouts",
            "numbered-icon-grid",
            # NEW: extra infographic styles
            "circular-process-loop",
            "swimlane-diagram",
            "waterfall-progression",
            "horizontal-bar-comparison",
            "pie-chart-callouts",
            "stat-card-grid",
            "icon-list-bullet",
            "gantt-style-timeline",
            "matrix-2x2-quadrant",
            "ladder-progression-vertical",
            "before-after-arrows",
            "decision-tree-yes-no",
        ],
        "no_third_party_logos_default": False,
    },
    {
        "slug": "illustration-graphic",
        "name": "Illustration / Graphic (drawn, no photos)",
        "examples": [
            "single-subject-line-illustration",
            "flat-vector-spot-illustration",
            "risograph-print-style",
            "paper-collage-cut-paper",
            "hand-drawn-marker-sketch",
            "retro-vintage-poster",
            "mid-century-modern-poster",
            "botanical-organic-illustration",
            "geometric-abstract-pattern",
            # NEW: extra illustration styles
            "comic-book-panel",
            "isometric-flat-scene",
            "pixel-art-retro",
            "watercolor-illustration",
            "block-print-style",
            "low-poly-geometric",
            "doodle-sketch-overlay",
            "collage-mixed-media",
            "ink-brush-illustration",
            "art-deco-poster",
            "swiss-modernist-poster",
            "70s-disco-poster",
        ],
        "no_third_party_logos_default": True,
    },
    {
        "slug": "product-object",
        "name": "Product / Object as Hero",
        "examples": [
            "hero-product-still-life",
            "product-mockup-on-device",
            "floating-product-with-shadow",
            "product-in-lifestyle-context",
            "exploded-view-diagram",
            # NEW: extra product styles
            "flat-lay-overhead",
            "macro-detail-shot",
            "product-on-fabric-texture",
            "gradient-backdrop-product",
            "product-trio-arrangement",
            "packaging-mockup-shelf",
            "minimal-pedestal-display",
            "ingredient-breakdown-flat-lay",
            "product-with-natural-elements",
            "magazine-style-product-photo",
        ],
        "no_third_party_logos_default": True,
    },
    {
        "slug": "template-layout",
        "name": "Modular Template / Multi-Panel Layout",
        "examples": [
            "canva-style-shapes-and-accents",
            "corporate-brochure-multi-panel",
            "60-40-photo-text-split",
            "geometric-triangle-collage",
            "organic-wavy-frame-cutout",
            "magazine-spread-grid",
            # NEW: extra template styles
            "editorial-quote-spread",
            "carousel-style-poster",
            "50-50-vertical-split",
            "triptych-three-frame",
            "sidebar-with-photo-grid",
            "footer-heavy-cta-frame",
            "header-banner-photo",
            "asymmetric-three-block",
            "newspaper-front-page-style",
            "instagram-carousel-cover",
            "poster-with-large-margins",
            "corner-accent-frame",
        ],
        "no_third_party_logos_default": False,
    },
    {
        "slug": "festive-cultural",
        "name": "Festive / Cultural Greeting",
        "examples": [
            "atmospheric-bokeh-greeting",
            "symbolic-object-close-up",
            "minimalist-cultural-motif",
            # NEW: extra festive styles
            "candle-light-greeting",
            "floral-motif-card",
            "ornament-pattern-card",
            "traditional-text-greeting",
            "ribbon-banner-celebration",
            "lantern-warm-glow",
            "festive-typography-poster",
            "gold-foil-accent-greeting",
        ],
        "no_third_party_logos_default": True,
    },
    {
        "slug": "abstract-artistic",
        "name": "Abstract / Artistic",
        "examples": [
            "abstract-gradient-with-overlay",
            "soft-watercolour-wash",
            "minimal-line-art-mark",
            # NEW: extra abstract styles
            "geometric-color-blocks",
            "organic-shapes-overlapping",
            "texture-overlay-headline",
            "duotone-photo-effect",
            "noise-grain-poster",
            "linework-mesh-pattern",
            "wave-pattern-flow",
            "blob-shapes-bauhaus",
            "halftone-pattern-feature",
            "paper-fold-trompe-l-oeil",
        ],
        "no_third_party_logos_default": True,
    },
    # ────────────────────────────────────────────────────────────────────
    # NEW CATEGORIES (added for richer brief coverage)
    # ────────────────────────────────────────────────────────────────────
    {
        "slug": "data-visualization",
        "name": "Data Visualization / Chart-Led (real charts as hero)",
        "examples": [
            "bar-chart-hero",
            "line-graph-trend",
            "pie-chart-feature",
            "stacked-area-chart",
            "scatter-plot-with-labels",
            "bubble-chart-comparison",
            "heatmap-grid",
            "sankey-flow-diagram",
            "gauge-meter-visual",
            "bullet-chart-single-metric",
            "column-chart-comparison",
            "spider-radar-chart",
            "single-big-number-with-context",
            "growth-arrow-with-percentage",
        ],
        "no_third_party_logos_default": True,
    },
    {
        "slug": "event-announcement",
        "name": "Event / Conference / Webinar Card",
        "examples": [
            "date-time-venue-card",
            "conference-stage-promo",
            "webinar-registration-card",
            "speaker-card-with-bio",
            "multi-speaker-grid",
            "event-banner-with-logo",
            "agenda-overview-card",
            "countdown-timer-card",
            "venue-photo-with-overlay",
            "panel-discussion-promo",
            "save-the-date-poster",
            "ticket-style-card",
        ],
        "no_third_party_logos_default": True,
    },
    {
        "slug": "achievement-milestone",
        "name": "Achievement / Award / Milestone Celebration",
        "examples": [
            "award-medal-hero",
            "milestone-counter-statement",
            "ribbon-banner-achievement",
            "celebration-confetti-overlay",
            "trophy-icon-focus",
            "year-of-achievement-poster",
            "recognition-quote-card",
            "team-celebration-photo",
            "founders-celebration-portrait",
            "anniversary-mark-poster",
            "100x-customer-milestone",
            "yearly-recap-numbers",
        ],
        "no_third_party_logos_default": True,
    },
    {
        "slug": "founder-leader-spotlight",
        "name": "Founder / Leader Spotlight (Personal Brand)",
        "examples": [
            "founder-portrait-with-quote",
            "ceo-statement-card",
            "leader-thought-piece",
            "founder-with-handwritten-note",
            "personal-story-portrait",
            "behind-the-decision-portrait",
            "founder-with-product-portrait",
            "leader-side-profile",
            "founder-at-whiteboard",
            "leader-on-stage-photo",
            "founder-team-walking-shot",
            "leader-with-customer-portrait",
        ],
        "no_third_party_logos_default": True,
    },
    {
        "slug": "social-proof",
        "name": "Social Proof / Customer Wall (logos and quotes)",
        "examples": [
            "customer-logo-grid",
            "testimonial-quote-wall",
            "five-star-rating-feature",
            "press-mention-collage",
            "customer-photo-mosaic",
            "trusted-by-logo-strip",
            "review-card-with-photo",
            "case-study-headline-card",
            "media-logos-banner",
            "verified-checkmark-trust",
            "award-badges-row",
            "user-count-with-avatars",
        ],
        "no_third_party_logos_default": False,
    },
    {
        "slug": "playful-cultural-reference",
        "name": "Playful / Meme / Cultural Reference",
        "examples": [
            "meme-format-with-twist",
            "before-after-meme-style",
            "expectation-vs-reality-split",
            "playful-illustration-with-pun",
            "cultural-reference-poster",
            "nostalgic-pop-culture-mashup",
            "humorous-cartoon-strip",
            "gif-style-still-frame",
            "ironic-typography-stack",
            "playful-emoji-feature",
            "tongue-in-cheek-quote",
        ],
        "no_third_party_logos_default": True,
    },
]


def _render_style_catalog_for_prompt() -> str:
    """Render the full _STYLE_CATEGORIES catalog as the STEP 3 text in
    the AD prompt. Dynamic so any additions to _STYLE_CATEGORIES are
    reflected in the prompt immediately — no risk of the two drifting."""
    lines: list[str] = []
    style_no = 1
    for cat in _STYLE_CATEGORIES:
        lines.append("")
        lines.append(f"  {cat['name'].upper()}")
        for ex in cat["examples"]:
            lines.append(f"   {style_no:>3}. {ex}")
            style_no += 1
    lines.append("")
    lines.append(f"  + invent-your-own — if none fit, write a fresh slug.")
    lines.append("")
    lines.append(f"  TOTAL: {style_no - 1} catalogued styles + invent-your-own.")
    return "\n".join(lines)


def _total_style_count() -> int:
    """Total catalogued styles across all categories."""
    return sum(len(c["examples"]) for c in _STYLE_CATEGORIES)


def _get_style_categories_for_brief(
    refined_brief: str,
    n_variants: int = 3,
) -> list[dict]:
    """Deterministically pick N distinct style categories for THIS brief.

    The hash means: same brief always picks the same 3 categories
    (reproducible for testing) but different briefs pick different
    combinations (variety per campaign). Each variant in a batch gets
    a different category.
    """
    import hashlib

    seed_bytes = hashlib.sha256(
        (refined_brief or "").encode("utf-8", errors="ignore")
    ).digest()
    # Seeded Fisher-Yates shuffle over category indexes
    indexes = list(range(len(_STYLE_CATEGORIES)))
    rng = int.from_bytes(seed_bytes[:8], "big") or 1
    for i in range(len(indexes) - 1, 0, -1):
        rng = (rng * 6364136223846793005 + 1442695040888963407) & ((1 << 64) - 1)
        j = rng % (i + 1)
        indexes[i], indexes[j] = indexes[j], indexes[i]

    picked = []
    for slot in range(n_variants):
        category = _STYLE_CATEGORIES[indexes[slot % len(indexes)]]
        picked.append(category)
    return picked


def _get_style_category(refined_brief: str, variant_idx: int, n_variants: int) -> dict:
    """Convenience: return the style category for one variant slot."""
    return _get_style_categories_for_brief(refined_brief, n_variants)[
        variant_idx % n_variants
    ]


def _assign_caption_variant_key(
    variants_dict: dict,
    variant_idx: int,
) -> tuple[str, str]:
    """Assign a specific caption variant to one image-variant slot.

    Distributes the available variants across image-variant slots:
      variant_idx 0 → viral_reach
      variant_idx 1 → high_interaction
      variant_idx 2 → follower_growth
      variant_idx 3 → festival_variant (if present)

    Falls back to modulo when fewer variants exist than image slots (e.g.
    2 variants × 3 image slots → variant 0 reused for image 2).

    Returns (variant_key, variant_text). Empty strings when no variants.
    """
    available_keys = [
        k for k in _VARIANT_DISTRIBUTION_ORDER
        if k in (variants_dict or {}) and isinstance(variants_dict[k], str) and variants_dict[k].strip()
    ]
    # Catch unknown keys at the end so we still distribute them.
    for k in (variants_dict or {}).keys():
        if k not in available_keys and isinstance(variants_dict.get(k), str) and variants_dict[k].strip():
            available_keys.append(k)
    if not available_keys:
        return ("", "")
    chosen_key = available_keys[variant_idx % len(available_keys)]
    return (chosen_key, variants_dict[chosen_key])


def _format_single_assigned_variant_block(
    platform: str,
    assigned_key: str,
    assigned_text: str,
) -> str:
    """Render a single assigned caption variant as the AD prompt's CAPTION
    block. The AD is told this is the ONLY variant they may lift text from
    for this specific image variant."""
    if not assigned_key or not assigned_text:
        return f"PLATFORM: {platform or 'none'}\n(no caption assigned)"
    return (
        f"PLATFORM: {platform}\n"
        f"ASSIGNED VARIANT FOR THIS IMAGE: {assigned_key}\n"
        f"\n"
        f"--- {assigned_key.upper()} ---\n"
        f"{assigned_text}\n"
        f"\n"
        f"This is the ONLY variant whose text you may lift verbatim into the\n"
        f"image. The other 2 image variants in this batch are receiving\n"
        f"different variants from the same platform — together the 3 images\n"
        f"will showcase the campaign's full tonal range."
    )


ART_DIRECTOR_MODEL = os.getenv("ART_DIRECTOR_MODEL", "gemini-flash-lite-latest")
# When true, bypass the Art Director text agent and send the raw brief +
# caption + DNA straight to the image model as the prompt. Test mode to
# see if the image model produces something different without an
# intermediate text-agent interpretation layer.
BYPASS_ART_DIRECTOR = os.getenv("BYPASS_ART_DIRECTOR", "false").lower() not in ("0", "false", "no", "off")


def _build_direct_image_prompt(
    refined_brief: str,
    recommended_copy_per_platform: dict,
    user_context: str,
    dna_attached: str,
    primary_color: str,
    variant_idx: int,
    n_variants: int,
) -> str:
    """Bypass path — construct a direct image prompt from raw inputs.

    No text agent in between. The image model receives the brief + caption
    + DNA and decides everything for itself. Each variant gets the same
    text inputs but the diffusion model's stochastic decoding produces
    different images.
    """
    copy_block = _format_copy_block(recommended_copy_per_platform)
    knowledge_block = (
        user_context.strip()
        if (dna_attached == "yes" and user_context and user_context.strip())
        else "(no brand knowledge attached)"
    )
    return f"""
ROLE
You are a Senior Creative Director and Social-Media Image Designer with 12+
years of experience producing branded creative for high-performing organic and
paid campaigns on LinkedIn, X, Facebook, and Instagram. Your work has consistently
matched the standard of in-house design teams at Canva, Notion, Stripe, Linear,
and HubSpot — not generic AI output.

════════════════════════════════════════════════════════════════════
HOW TO USE THE REFERENCE EXAMPLES ATTACHED TO THIS REQUEST
════════════════════════════════════════════════════════════════════
Below this prompt you will see EIGHT worked examples — FIVE GOOD followed
by THREE BAD anti-examples. Each example is a TRIPLE:

  1. EXPECTED USER INPUT — the kind of campaign brief a user wrote.
  2. EXPECTED IMAGE — the actual reference image attached, showing the
     kind of output we would (or would NOT) produce for that input.
  3. RATIONALE — for a GOOD example: why this image is the right answer
     for that input. For a BAD anti-example: a precise list of WHAT WENT
     WRONG and WHY the image is unacceptable.

This is in-context learning. Study every triple so you understand the
pattern: "given THIS kind of input, produce THIS kind of output;
given THAT kind of input, do NOT produce THAT kind of output."

  • GOOD examples set the CRAFT BAR. For an input like theirs, output
    like theirs. Match polish, discipline with text, palette restraint,
    use of whitespace. Do NOT copy their literal subject matter — only
    their quality and design sensibility.

  • BAD anti-examples are concrete failure cases. Do NOT replicate any
    of the specific failure modes listed (typos rendered as text, hex
    codes drawn on the post, duplicated icons, invented third-party
    logos, glass/glow/void AI aesthetic, etc.). Each anti-example's
    RATIONALE block tells you exactly which mistakes to avoid.

GOAL
Generate ONE production-quality social-media post image that:
  (a) communicates the campaign's core message at a glance,
  (b) reads as a deliberate human design choice (not an AI-render artefact), and
  (c) pairs naturally with the caption that will be published alongside it.

──────────────────────────────────────────────────────────────────
INPUTS
──────────────────────────────────────────────────────────────────

CAMPAIGN BRIEF (the strategic intent — single source of truth):
{refined_brief}

CAPTION TO BE PUBLISHED ALONGSIDE THE IMAGE
(use ONLY this text when rendering any words on the image):
{copy_block}

BRAND CONTEXT
(use ONLY if the brief is brand-focused; ignore otherwise):
{knowledge_block}

PRIMARY BRAND COLOUR (apply visually — never render as text):
{primary_color}

VARIANT POSITION: {variant_idx + 1} of {n_variants}
   Each variant in the batch must explore a genuinely DIFFERENT visual approach
   so the campaign is presented as a considered creative set, not three near-
   identical renders of the same idea.

──────────────────────────────────────────────────────────────────
STAGE 1 — DESIGN DECISIONS (think before you draw)
──────────────────────────────────────────────────────────────────
Before composing the image, internally decide:

  1. PURPOSE — Is this image's job to STOP THE SCROLL, EXPLAIN A CONCEPT, or
     CARRY A QUOTE / POV? Pick one. Design accordingly.

  2. INFORMATION DENSITY — Match the caption. A rich, structured caption gets
     a richer composition (data points, supporting icons, structured layout).
     A short punchy caption gets a minimal, high-impact composition.

  3. VISUAL DIRECTION — Pick the direction that best serves THIS brief.
     Do NOT default to dark glass-morphism, neon glow, or void backgrounds.
     Acceptable directions include (these are inspirational examples, NOT a
     menu — feel free to invent your own):

       • Bold typography poster — type is the hero, imagery minimal
       • Quote / point-of-view card on a flat or gradient background
       • Editorial magazine layout — strong grid, generous whitespace
       • Canva-style template — accent shapes, sticker elements, badges
       • Infographic — simple icons, arrows, structured data points
       • Illustration or line art with a single focal subject
       • Candid / editorial photograph with restrained text overlay
       • Risograph / paper collage / hand-drawn marker / retro print
       • Minimalist still-life of a product, object, or symbol
       • Abstract gradient or geometric pattern + overlaid headline
       • Clean product UI / screenshot framing (when brief is product-focused)
       • Conversational chat-bubble layout (when brief is conversational)

     Variants 1, 2, and 3 must land on visibly different directions from this
     space. If variant 1 is a typography poster, variant 2 must NOT be a
     typography poster — it must be a different direction.

  4. COLOUR SYSTEM — Build a palette around the primary brand colour. The
     primary colour should appear as a meaningful accent or fill — not be the
     whole image. Pair it with one or two complementary neutrals or supporting
     hues you choose. Avoid orange-on-dark glow unless the brief explicitly
     calls for night / cinematic.

  5. TYPOGRAPHY — Pick a typographic system that matches the tone:
       - Editorial / authoritative → high-contrast serif headline + clean sans body
       - Tactical / marketing voice → bold sans display + tighter tracking
       - Friendly / approachable → rounded humanist sans
       - Tech / data → geometric sans, monospaced data lockups
     Use a clear hierarchy: ONE headline, AT MOST one subhead, AT MOST one CTA.

──────────────────────────────────────────────────────────────────
STAGE 2 — COMPOSITION RULES
──────────────────────────────────────────────────────────────────

  • One clear focal point. The viewer should know where to look first.
  • Breathing room. Generous, deliberate negative space.
  • Edge discipline. Nothing important cropped at the canvas edge unless it's a
    deliberate bleed for stylistic effect.
  • Balanced asymmetry over rigid centering, unless the chosen direction calls
    for a centered lockup.
  • Designed-for-feed legibility: small-screen readability is non-negotiable.

──────────────────────────────────────────────────────────────────
STAGE 3 — HARD GUARDRAILS (NON-NEGOTIABLE)
──────────────────────────────────────────────────────────────────

[R1] SPELLING DISCIPLINE
     Every word rendered in the image MUST be spelled correctly.
     - When rendering text, lift short phrases CHARACTER-FOR-CHARACTER from the
       CAPTION block above. Do not paraphrase, do not abbreviate creatively.
     - Do not invent slogans, taglines, product names, or hashtags that are
       not present in the caption.
     - If you are not 100% confident in the spelling of any word, OMIT TEXT
       ENTIRELY. A clean image with zero text is preferable to a striking
       image with a typo. Typos are an automatic reject.

[R2] BRAND-COLOUR HEX CODE MUST NEVER APPEAR AS TEXT
     The value "{primary_color}" — and any other "#RRGGBB" string anywhere in
     the inputs — is a colour specification meant for YOU, the designer.
     It MUST NOT appear in the rendered image as letters, digits, watermarks,
     captions, tags, or label text. Use the colour visually only.

[R3] NO DUPLICATED ELEMENTS
     Exactly one of each compositional component:
       - one headline
       - at most one subhead
       - at most one CTA
       - one focal subject / hero element
       - one logo (or none — see R4)
     Do NOT repeat the same icon, shape, glyph, or phrase twice in the same
     composition. The only exception is when the chosen direction is explicitly
     a pattern, grid, or repeated motif (e.g. a deliberate icon grid).

[R4] LOGO INTEGRITY
     If a logo is attached as a reference image, either:
       (a) use it EXACTLY as provided — no recolouring, no restyling, no
           re-drawing, no rotation, no perspective warp; OR
       (b) omit it entirely.
     Do not invent, hallucinate, or design a substitute brand mark. No fake
     wordmarks, no fake monograms, no fictional company logos anywhere in
     the composition.

[R5] STYLE DIVERSITY ACROSS THE BATCH
     This is variant {variant_idx + 1} of {n_variants}. The design must be
     visibly distinct from a default dark / glass / glow / void aesthetic.
     If you find yourself defaulting to that look, stop and pick a different
     direction from Stage 1.

──────────────────────────────────────────────────────────────────
OUTPUT
──────────────────────────────────────────────────────────────────
Produce a single high-resolution image. Aspect ratio and dimensions are
controlled by the model configuration — do not attempt to embed them.
The image should look like the work of a senior designer at a leading
in-house creative team, not like a generic AI render.
""".strip()


def _art_director_agent(
    refined_brief: str,
    recommended_copy_per_platform: dict,
    user_context: str,
    dna_attached: str,
    primary_color: str,
    variant_idx: int,
    n_variants: int,
    client,
) -> str:
    """Art Director — text agent that dynamically invents the visual concept
    for ONE variant and outputs a single-paragraph image-generation prompt.

    Called per variant. High temperature (0.95) so parallel variants land on
    genuinely different visual concepts rather than converging on the model's
    default for the brief.
    """
    copy_block = _format_copy_block(recommended_copy_per_platform)
    knowledge_block = (
        user_context.strip()
        if (dna_attached == "yes" and user_context and user_context.strip())
        else "(no brand DNA attached — pick a palette and aesthetic that fits the brief)"
    )

    director_prompt = f"""
You are an elite, highly creative Art Director and Visual Concept Designer. You have decades of experience designing high-performing social-media campaigns for top brands.

Your job: read the campaign brief, the social media caption, and the brand DNA — then dynamically invent the single best visual concept to represent this campaign on social media.

Your output must be a single paragraph containing ONLY the final image generation prompt for Gemini 3.1 Flash Image. No introductory or concluding text — only output the prompt.

This is variant {variant_idx + 1} of {n_variants} being generated in parallel for the same campaign. Make YOURS genuinely distinct from what another art director would create for the same brief — different medium, mood, subject, composition, layout. Trust your taste.

You have complete creative freedom. Pick the visual approach, style, layout, typography, colour, mood, and elements that will produce the strongest possible social-media post for this specific campaign.

═══════════════════════════════════════════════════════════════
INPUT DATA
═══════════════════════════════════════════════════════════════
- Refined Campaign Brief:
{refined_brief}

- Social Media Caption:
{copy_block}

- Brand DNA & Color Palette:
{knowledge_block}

Primary brand colour to consider weaving in: {primary_color}.

Design the visual concept and output the final image prompt:
""".strip()

    try:
        res = client.models.generate_content(
            model=ART_DIRECTOR_MODEL,
            contents=director_prompt,
            config=types.GenerateContentConfig(temperature=0.95),
        )
        out = (res.text or "").strip()
        if out.startswith("```"):
            out = out.split("```", 2)[-1].strip()
        return out
    except Exception as e:
        logger.error(f"[art_director] variant {variant_idx + 1} failed: {e}")
        return ""


# ────────────────────────────────────────────────────────────────────────────
# ART DIRECTOR v2 — multimodal AD with few-shot, JSON blueprint output
# ────────────────────────────────────────────────────────────────────────────
# Two-stage architecture: this AD (gemini-2.5-flash, multimodal) reads the
# brief + caption variants + brand context AND looks at the same 5 GOOD +
# 3 BAD reference images the image model will see, then emits a structured
# JSON blueprint. The image model is then called with the AD's
# `final_render_prompt` as its text prompt + the SAME reference images +
# the brand logo (when present).
#
# Why this beats the direct-prompt path:
#   • AD is a TEXT model — physically cannot hallucinate typos. It writes
#     exact strings in "double quotes" (the Nano Banana text-rendering
#     technique). Image model treats double-quoted text as literal.
#   • AD emits structured fields like `third_party_logos_to_render: []`
#     that are numeric/explicit, not stylistic instructions.
#   • Two-stage means strategic decisions happen in cheap text-model tokens
#     before any expensive image-model tokens are spent.
#
# Enabled by default — `BYPASS_ART_DIRECTOR=false`. Falls back to direct
# prompt path if the AD call fails.
ART_DIRECTOR_V2_MODEL = os.getenv("ART_DIRECTOR_V2_MODEL", "gemini-2.5-flash")


def _build_art_director_v2_prompt(
    refined_brief: str,
    caption_platform: str,
    assigned_variant_key: str,
    assigned_variant_block_text: str,
    user_context: str,
    dna_attached: str,
    primary_color: str,
    variant_idx: int,
    n_variants: int,
) -> str:
    """Build the v2 AD prompt text. The 8 reference images + their rationale
    text are appended AFTER this prompt by _build_fewshot_contents()."""

    knowledge_block = (
        user_context.strip()
        if (dna_attached == "yes" and user_context and user_context.strip())
        else "(no brand knowledge attached — design from the brief and caption only)"
    )

    set_letter = chr(ord("A") + variant_idx % 3)

    # Dynamic style catalog — derived from _STYLE_CATEGORIES so it's
    # always in sync. Adding a new style to _STYLE_CATEGORIES auto-
    # updates the catalog the AD sees in STEP 3.
    style_catalog = _render_style_catalog_for_prompt()

    # Brief-aware style category — varies per brief, not per fixed variant
    # slot. Same brief always picks the same 3 categories (reproducible);
    # different briefs pick different combinations (variety per campaign).
    category = _get_style_category(refined_brief, variant_idx, n_variants)
    sibling_categories = _get_style_categories_for_brief(refined_brief, n_variants)
    sibling_summary = " · ".join(
        c["slug"] for i, c in enumerate(sibling_categories) if i != variant_idx
    )
    examples_list = ", ".join(f"`{e}`" for e in category["examples"][:8])
    if category["no_third_party_logos_default"]:
        third_party_clause = (
            f"For a {category['name']} composition, third-party brand logos "
            f"are usually unnecessary — the composition's focus is the "
            f"subject, not brand connectors. Default `third_party_logos_to_render` "
            f"to an empty array `[]`. If the brief mentions third-party "
            f"brands, they may appear in the rendered headline TEXT but should "
            f"NOT appear as visual marks in this image."
        )
    else:
        third_party_clause = (
            f"For a {category['name']} composition, third-party brand logos "
            f"may be relevant. If the brief mentions third-party brands and "
            f"showing them visually serves the composition, list each one in "
            f"`third_party_logos_to_render` (with `occurrences=1` and a "
            f"specific corner position). The image model will be told to "
            f"render the REAL official mark — never invent, never substitute "
            f"with a generic icon. For brands the model is unlikely to render "
            f"faithfully, leave them out."
        )

    return f"""
ROLE
You are a Senior Brand & Visual Director with 15+ years of experience leading
in-house creative teams at the calibre of PwC, Accenture, McKinsey, Stripe,
Notion, Linear, Canva, and HubSpot. You think like a senior designer — sharp,
opinionated, decisive — and you also think like a brand strategist who knows
what kind of visual ACTUALLY moves the needle for each kind of campaign.

You are NOT writing prose for a customer. You are writing a structured visual
brief that a top-tier image-generation model will execute. Your `final_render_prompt`
will be sent verbatim to gemini-3.1-flash-image, so write it with that model's
prompting style in mind (per Google's official Nano Banana guidance, summarised
in STEP 4 below).

JOB
You will see EIGHT reference examples below — FIVE GOOD designs whose craft
we want to match, and THREE BAD anti-examples whose failure modes we want to
avoid. Each reference is paired with the kind of brief it served and a
rationale.

Study the references. Then read the REAL campaign brief at the top of the
INPUTS section. Decide the single best visual composition for THIS specific
post, the way a senior in-house designer would after looking at those
references. Finally, write the JSON blueprint that will be handed to a
top-tier image-generation model.

The `final_render_prompt` field in your JSON output IS what gets sent to
the image model. Make it specific, sharp, and free of every failure mode
the BAD anti-examples demonstrate.

══════════════════════════════════════════════════════════════════
HOW TO USE THE REFERENCE EXAMPLES ATTACHED BELOW
══════════════════════════════════════════════════════════════════
After this prompt, you will see EIGHT worked examples interleaved with
their reference images. Each is a TRIPLE:

  1. EXPECTED USER INPUT — the kind of brief a user wrote for that example.
  2. EXPECTED IMAGE — the reference image attached, showing what we would
     (or would NOT) produce for that brief.
  3. RATIONALE — for GOOD: why this image is the right answer.
     For BAD: WHAT WENT WRONG + WHY the image is unacceptable.

Pattern to learn: "given THIS kind of brief → produce THIS kind of image;
given THAT kind of brief → do NOT produce THIS failure pattern."

  • GOOD references set the CRAFT BAR for your `final_render_prompt`.
    Match their polish, restraint with text, palette discipline, and use
    of whitespace. Do NOT copy their subject — only their quality.

  • BAD anti-examples are concrete failure cases the image model tends to
    produce by default. Your render prompt must steer it away from each
    failure listed in the rationales.

══════════════════════════════════════════════════════════════════
INPUTS
══════════════════════════════════════════════════════════════════

CAMPAIGN BRIEF (strategic intent — single source of truth):
{refined_brief}

ASSIGNED CAPTION (this is your locked-in source for any text rendered on
the image — you may NOT lift text from any other variant. The system
distributes variants across the 3 image variants so each image showcases
a different angle. Use ONLY the variant labelled "ASSIGNED VARIANT FOR
THIS IMAGE" below):
{assigned_variant_block_text}

BRAND CONTEXT (use when brand-focused; ignore for cross-topic briefs):
{knowledge_block}

PRIMARY BRAND COLOUR (use visually as an accent only — NEVER render as text):
{primary_color}

VARIANT POSITION: {variant_idx + 1} of {n_variants}
REFERENCE SET ATTACHED: Set {set_letter}

══════════════════════════════════════════════════════════════════
DYNAMIC STYLE CATEGORY FOR THIS VARIANT (computed from the brief)
══════════════════════════════════════════════════════════════════
Your assigned style category for THIS brief: **{category["name"]}**
Slug: `{category["slug"]}`

Example direction slugs in this category (pick one or invent your own
that still fits the category):
  {examples_list}

This category was selected dynamically based on the brief's content —
it is NOT a fixed slot. Different briefs receive different category
combinations so the 3 variants of this batch will differ in style
AND the 3 variants of the NEXT campaign will differ from these.

Your sibling variants for THIS brief are assigned different categories:
  {sibling_summary}

Lean into your assigned category — that is how the batch ends up as
three distinct creative directions instead of three near-identical
compositions. If the brief STRONGLY does not suit your category (e.g.
a brief specifically demands a product mockup but your category is
typography-led), you may pick a different direction — but the default
should be to commit to your category.

A note on rendered text: diffusion models hallucinate tiny rendered
text (chart axis labels, metric values, button text). The cleaner
your composition is — the fewer words you ask the image model to
render — the fewer typos the output will contain. Keep
`total_rendered_text_words` ≤ 12 whenever possible.

THIRD-PARTY LOGO POLICY FOR THIS VARIANT:
{third_party_clause}

══════════════════════════════════════════════════════════════════
BRIEF CENTRICITY — your image MUST clearly communicate THIS brief
══════════════════════════════════════════════════════════════════
A common AD failure mode: the composition is generic and could be
about any campaign. That is NOT acceptable. Your image must visually
communicate THIS SPECIFIC brief's core message.

The viewer test: imagine someone scrolling past this image WITHOUT
seeing the caption. What would they understand the post is about?
Write that one-sentence interpretation in your `viewer_takeaway`
output field. If it doesn't match the brief's USER GOAL, your
composition is wrong — revise before submitting.

Brief-relevance heuristics:
  • If brief is about a NEW PRODUCT LAUNCH → show the product OR a
    clear visual metaphor for what the product DOES (not just an
    abstract dashboard)
  • If brief is about a SERVICE OUTCOME → show the outcome visually
    (people doing the thing, results visible, real work happening)
  • If brief is about a PARTNERSHIP → show both parties (logos if
    famous, or visual nod otherwise)
  • If brief is about a REPORT / INSIGHT → show the data or visual
    representation of the insight
  • If brief is about a TEAM ACHIEVEMENT / CULTURE → show the team
    or the work in progress
  • If brief is THOUGHT LEADERSHIP → show the concept being argued
    (e.g. a visual metaphor of the POV) — not just typography
  • If brief is FESTIVE / CULTURAL → show culturally-resonant
    symbolism, not generic decorations

Your composition is allowed to be metaphorical — but the metaphor
must be DECODABLE. Anyone with the brief's USER GOAL in mind should
recognise the connection between brief and image.

══════════════════════════════════════════════════════════════════
STEP 1 — CLASSIFY THE BRIEF (what KIND of post is this?)
══════════════════════════════════════════════════════════════════
Different kinds of posts need different kinds of images. Pick ONE
classification and write your `brief_type` field with the slug shown:

  service_based      — Consulting / advisory / agency / B2B services /
                       professional services firm. Signals: "we help",
                       "advisory", "consulting", "agency", "we deliver",
                       team-of-experts framing. (e.g. PwC, Accenture,
                       McKinsey, Z-Ninth)
                       VISUAL TENDENCY: people, editorial photography,
                       client-success stories, advisors-in-action.

  product_based      — SaaS / hardware / app / physical product.
                       Signals: "launch", "feature", "platform", "app",
                       "now available", "version X.Y". (e.g. Spenzo AI,
                       Zyntegrate, Lifesight, MuleSoft, Stripe)
                       VISUAL TENDENCY: product UI showcase, typography-
                       led launch, abstract product representation.

  generic_industry   — News commentary / industry trend / thought
                       leadership where the brand voice is light and
                       the topic is broader than the brand itself.
                       Signals: "what's happening in", "industry trends",
                       "today's AI news", "report findings", cross-topic.
                       VISUAL TENDENCY: editorial photo or typography,
                       brand mark used sparingly or omitted entirely.

  individual_personal — Personal milestone, individual achievement,
                       founder-voice post, "my journey" content.
                       Signals: first-person "I", named individual,
                       personal pronouns.
                       VISUAL TENDENCY: personal portrait, candid
                       moment, individual hand-written feel.

══════════════════════════════════════════════════════════════════
STEP 2 — MAKE THE COMPOSITION DECISIONS (the design choices)
══════════════════════════════════════════════════════════════════
Decide each of these and reflect them in your JSON output AND in your
`final_render_prompt`:

  1. PEOPLE — should the image include real human(s)?
       YES when: service-based brief, customer-success story, team
                 announcement, individual_personal, "we help" framing,
                 lifestyle / use-case framing.
       NO  when: product launch (product is the hero), pure
                 typography post, abstract / thematic post, feature
                 announcement focused on UI.
       If YES: how many people? (1 / 2 / small group of 3-5 / no faces
       just hands). What setting (office, workshop, public, lifestyle)?

  2. CALL TO ACTION — should the image include a CTA?
       YES when: the assigned caption contains an actionable CTA URL
                 (sign up, register, learn more, get the report) AND
                 the URL is real (from research.referenced_entities,
                 brand DNA, or user-supplied).
       NO  when: brand-storytelling post, announcement without CTA,
                 thought-leadership without a destination URL,
                 festival greeting.

  3. KEY POINTS — should the image enumerate key points / features?
       YES when: educational explainer, "here are the 3 things",
                 process steps, list-style post.
       NO  when: emotional post, narrative post, single-message post.
       If YES: how many points? (typically 3, max 5).

  4. THIRD-PARTY BRAND LOGOS — should real third-party logos appear?
       YES when: the post is explicitly about an integration partnership
                 (e.g. "Spenzo + AWS announcement") AND the brand is
                 famous enough to render reliably (Google, AWS, Meta,
                 OpenAI, Microsoft, Apple, LinkedIn, X/Twitter, Slack).
                 List each in `third_party_logos_to_render`.
       NO  when: the brief merely mentions the brand in passing OR the
                 brand is obscure / niche (HubSpot, Snowflake, lesser-
                 known SaaS) where the image model is unlikely to
                 render the official mark faithfully.
       If YES: HOW MANY TIMES does each logo appear?
                 → Each logo appears EXACTLY ONCE per image, period.
                 → Specify the position (top-right, bottom-left, etc.)
                   in your render prompt.

  5. TEXT LAYOUT — how is rendered text positioned?
       Options: overlay_panel_left | overlay_panel_right |
                top_third | center | bottom_third |
                across_diagonal | left_aligned | right_aligned |
                center_aligned | no_text.
       Pick what the composition naturally supports.

  6. BACKGROUND TREATMENT — what's behind the focal element?
       Options:
         solid_color         — single flat colour (e.g. "warm cream",
                                "deep navy")
         gradient            — two-stop gradient (specify both colours
                                + direction)
         photo               — full-bleed photographic background
         texture             — paper, linen, riso, watercolour wash
         pattern             — geometric pattern, organic motif
         sticker_frame       — bordered "sticker" frame surrounding
                                content (retail / playful brands)
         out_of_focus_scene  — bokeh / blurred environment behind
                                a sharp subject
         minimal_white       — pristine white with subtle grain
       Pick what fits the brand tone AND the chosen composition style.

══════════════════════════════════════════════════════════════════
STEP 3 — PICK A DESIGN STYLE (full catalog below, or invent your own)
══════════════════════════════════════════════════════════════════
This is your VOCABULARY, not a menu of musts. Pick what fits THIS brief.
If none fit perfectly, invent your own slug — these are illustrative,
not exhaustive.
{style_catalog}

══════════════════════════════════════════════════════════════════
STEP 4 — WRITE THE `final_render_prompt` (per Google's guidance)
══════════════════════════════════════════════════════════════════
Per Google's official Nano Banana / Gemini 3.1 Flash Image prompting
guide, write a NARRATIVE PARAGRAPH (180-260 words). NOT a keyword list.

Include in the paragraph:
  ✓ The composition style (from STEP 3)
  ✓ The background treatment (from STEP 2.6) — described by COLOUR
    NAMES, not hex codes
  ✓ The focal subject (people / product / typography / shapes)
  ✓ The exact rendered text in DOUBLE QUOTES, lifted verbatim from
    the assigned caption — keep it SHORT (< 12 words total)
  ✓ Typography style (e.g. "bold geometric sans-serif", "high-contrast
    serif", "rounded humanist sans")
  ✓ The element COUNT — explicitly state numbers ("exactly 3 flat
    line icons in a row", "one headline panel", "single brand mark
    in the top-right corner")
  ✓ Camera / lens / lighting vocabulary (e.g. "85mm portrait lens",
    "soft golden-hour window light", "wide-angle establishing shot",
    "three-point softbox", "low-angle perspective")
  ✓ The palette as named colours (e.g. "warm cream, deep navy,
    muted terracotta accent")
  ✓ Positive framing — say what IS in the image, not what isn't
  ✓ End with: "High-resolution social-media image, designed in the
    style of a senior in-house designer at a top brand team — no
    AI-render aesthetic, no glass-morphism, no glow-on-void, no
    generic isometric 3D."

══════════════════════════════════════════════════════════════════
GOOD PROMPT EXAMPLES (what your `final_render_prompt` should look like)
══════════════════════════════════════════════════════════════════

✅ GOOD EXAMPLE A — service_based / people-led
"A candid editorial photograph of a senior consultant in her late
forties standing in a softly-lit conference room mid-sentence, gesturing
toward a wall of project plans behind her. Shot on an 85mm portrait
lens with soft natural light from a window on the right, warm golden
tones across her face and the wood-panelled wall. The subject occupies
the right two-thirds of the frame; the left third holds a clean
off-white overlay panel carrying the headline \\"Strategy that ships.\\"
in a bold modern serif typeface, deep navy. One Accenture-style brand
mark in the bottom-left corner of the photo, sized small. Palette is
warm cream, deep navy, and a single muted terracotta accent on the
period of the headline. Background out-of-focus to keep attention on
the subject. Single focal point, generous breathing room, magazine-
cover discipline. High-resolution social-media image, designed in the
style of a senior in-house designer at a top brand team — no AI-render
aesthetic, no glass-morphism, no glow-on-void, no generic isometric 3D."

✅ GOOD EXAMPLE B — product_based / typography-led
"A bold typography poster on a flat off-white background with a single
horizontal accent line in the brand's warm cyan running across the
lower third of the canvas. The headline \\"Six months → six weeks.\\" is
set in a heavy geometric sans-serif (Söhne or Inter Display weight),
near-black, left-aligned, occupying the upper two-thirds of the frame
with generous breathing room above. The arrow glyph is rendered in the
same warm cyan accent. Below the rule, in a smaller weight sans-serif,
the subhead \\"Now in production.\\" sits in muted dark grey. Single brand
wordmark in the top-left corner, sized small. Zero imagery, zero icons,
zero gradient. Pure typography on flat off-white. Single focal point.
Magazine-cover discipline. High-resolution social-media image, designed
in the style of a senior in-house designer at a top brand team — no
AI-render aesthetic, no glass-morphism, no glow-on-void, no generic
isometric 3D."

✅ GOOD EXAMPLE C — product_based / flat infographic
"A clean flat-timeline-horizontal infographic on a pristine off-white
background. Five evenly-spaced milestones run left-to-right across the
centre of the canvas, connected by a thin dotted line in muted slate
grey. Each milestone is a small circular node containing ONE flat line
icon (a flag, a calendar, a chart, a rocket, a checkmark) in deep
indigo. Below each node sits a single short label in a clean geometric
sans-serif, near-black, no more than two words each. The headline
\\"Five steps to live data.\\" sits above the timeline in a bold sans,
near-black, left-aligned. Single brand wordmark top-right, sized small.
Generous whitespace. Two columns visually — header up top, timeline
across centre. No 3D, no glass, no glow, no isometric. Single focal
point on the timeline. High-resolution social-media image, designed in
the style of a senior in-house designer at a top brand team — no
AI-render aesthetic."

══════════════════════════════════════════════════════════════════
BAD PROMPT EXAMPLES (what your `final_render_prompt` must NEVER look like)
══════════════════════════════════════════════════════════════════

❌ BAD EXAMPLE A — keyword list, vague
"infographic, AI, data, integration, modern, blue, professional,
dashboard, charts, icons, glow, futuristic, tech"
Why bad: keyword soup, no narrative, no composition direction. Image
model defaults to its built-in aesthetic (glass cube + glow + void).
Produces typos in chart labels because no exact text was specified.

❌ BAD EXAMPLE B — too generic
"A social media image about Spenzo. Make it look good. Show the
brand colour. Modern and clean."
Why bad: no subject, no composition, no element count, no rendered
text specified, no lighting. Image model invents everything from its
priors → glass-morphism dashboard with fake hex codes rendered as text.

❌ BAD EXAMPLE C — too many decisions
"A dashboard mockup showing a chart with axis labels January, February,
March, April, May, June, July, August, September, October, November,
December on the X-axis, with metric values $1.2k, $3.4k, $5.6k on the
Y-axis. Below the chart, three buttons labelled 'View report', 'Export
data', 'Settings'. In the sidebar, navigation items 'Dashboard',
'Reports', 'Campaigns', 'Insights', 'Settings'. In the top-right,
a notification bell with the number 7."
Why bad: way too much rendered text. Diffusion models hallucinate
tiny text at this scale. EVERY label will be misspelled or gibberish.
Maximum total rendered text in any composition should be ~12 words,
ideally fewer.

══════════════════════════════════════════════════════════════════
HOW TO THINK — design freedom, no forced template
══════════════════════════════════════════════════════════════════

The 4 STEPS above are a structured framework, not a script you must
follow rigidly. Once you've classified the brief, made the composition
decisions, and picked a style, you have FULL freedom to invent the
specific composition that fits THIS brief.

You do NOT need to render text just because text rendering is
available — some of the strongest GOOD references (festival greeting,
editorial photo) have very little text. You DO need to commit to ONE
clear composition that fits THIS brief better than any sibling did.

══════════════════════════════════════════════════════════════════
HARD RULES (NON-NEGOTIABLE)
══════════════════════════════════════════════════════════════════

[A] FAVOR OMITTING TEXT WHEN IN DOUBT.
    Every additional text element is another chance for a typo. If a
    headline / subhead / CTA is not clearly earning its place, set its
    "render" field to false. A clean image with one strong headline
    beats a busy one with four text blocks.

[B] EVERY TEXT STRING YOU EMIT MUST BE IN DOUBLE QUOTES, LIFTED VERBATIM
    FROM THE *ASSIGNED VARIANT* ABOVE (not from any other variant).
    Format: "Six months → six weeks."
    Your `caption_variant_used` field in the output JSON MUST equal
    "{assigned_variant_key}". Do not lift text from any other variant —
    the other variants are reserved for the other image variants in
    this batch. Lift CHARACTER-FOR-CHARACTER from the assigned variant.
    No paraphrasing. No abbreviating. No inventing slogans, hashtags,
    or product names. See BAD anti-example 1.

[C] HEX CODES STAY IN COLOUR FIELDS, NEVER IN TEXT.
    "{primary_color}" is a colour specification. It belongs in your
    `final_render_prompt` described by colour NAME ("warm cyan",
    "off-white"). It must NEVER appear as a rendered string — see BAD
    anti-example 2.

[D] THIRD-PARTY BRAND LOGOS — official mark or nothing.
    If the brief mentions real third-party brands (Salesforce / AWS /
    Google / Meta / Slack / OpenAI / HubSpot / Snowflake / TikTok /
    LinkedIn / Stripe / Notion / etc.) AND your composition would
    benefit from showing those brands visually, you must choose ONE
    of two approaches for the whole image:

      (a) RENDER THE OFFICIAL MARKS — instruct the image model to
          render each named third-party brand using its REAL official
          logo with correct shape, correct official colours, and
          correct wordmark/glyph. List every brand you are asking
          the image model to render in `third_party_logos_to_render`.
          The image model is told: "render the official, correctly-
          shaped, correctly-coloured version, faithful to the actual
          public mark of that company."

      (b) OMIT ENTIRELY — keep `third_party_logos_to_render` empty
          and write the composition so no third-party brand visual
          appears at all. The brief might still mention the brand
          in the rendered text (e.g. headline references it) but
          no logo or visual stand-in is shown.

    NEVER mix the two approaches in one image. NEVER substitute a
    generic icon (cloud / chart / cube / connector-line / database
    stack) where a real brand logo would go — that is a Pipelyt
    integrity failure. NEVER render an invented / hallucinated
    version of a real brand mark (the failure shown in BAD
    anti-example 3).

    When choosing (a), prefer brands the image model is most likely
    to render faithfully (Google, AWS, Meta, OpenAI, Slack are well-
    known). For obscure brands (small SaaS tools), prefer (b).

[E] BRAND LOGO RULES.
    If a brand logo image is attached to the IMAGE-MODEL call (it will
    be appended after these references): your `final_render_prompt`
    must instruct the image model to render the attached logo EXACTLY
    in one corner. If no logo is attached, do not invent one.

[F] AVOID THE DEFAULT AI AESTHETIC.
    In `final_render_prompt`, explicitly forbid: dark glass-morphism,
    glow-on-void backgrounds, generic isometric 3D render, floating UI
    cards, cinematic neon, blue digital glow on void.

[G] NO DUPLICATE ELEMENTS.
    Instruct the image model: exactly one headline, one focal subject,
    one logo. Never repeat the same icon, brand mark, or phrase twice.

[H] NARRATIVE PARAGRAPH, NOT KEYWORD LIST.
    `final_render_prompt` is a coherent 180–260 word paragraph per
    Google's official Nano Banana guidance — not a bullet list or
    comma-separated tag dump.

══════════════════════════════════════════════════════════════════
OUTPUT — return ONLY this JSON object (strict JSON, no markdown fences)
══════════════════════════════════════════════════════════════════
{{
  "brief_type": "<one of: service_based | product_based |
                  generic_industry | individual_personal>",

  "style_category": "<MUST equal the assigned category's slug from
                    DYNAMIC STYLE CATEGORY above (e.g. 'typography-led',
                    'photo-led-editorial', 'flat-infographic', etc.)>",

  "direction": "<specific slug within the assigned category — from the
                examples shown above, or invent your own that still fits
                the category.>",

  "style_chosen": "<same as direction — kept for analytics stability>",

  "rationale": "<one sentence: why this specific direction within the
                assigned category fits THIS brief specifically.>",

  "viewer_takeaway": "<one sentence — what someone scrolling past your
                     image WITHOUT seeing the caption would understand
                     the post is about. This must align with the brief's
                     USER GOAL or the composition is wrong.>",

  "people_decision":  "<one of: include_people | no_people>",
  "people_count":     "<integer when include_people, else null>",
  "people_setting":   "<string e.g. 'real office', 'workshop',
                      'public space', 'lifestyle scene', else null>",

  "cta_decision":     "<one of: include_cta | no_cta>",

  "key_points_decision": "<one of: list_key_points | no_key_points>",
  "key_points_count":    "<integer (typically 3, max 5), else null>",

  "third_party_logos_to_render": [
    {{"brand": "<name>", "occurrences": 1, "position": "<top-right | etc>"}}
  ],

  "text_layout":      "<one of: overlay_panel_left | overlay_panel_right |
                      top_third | center | bottom_third | left_aligned |
                      right_aligned | center_aligned | no_text>",

  "background_treatment": "<one of: solid_color | gradient | photo |
                           texture | pattern | sticker_frame |
                           out_of_focus_scene | minimal_white>",
  "background_value":     "<concrete description: 'warm cream solid',
                           'cream → soft peach vertical gradient',
                           'candid office photo', etc.>",

  "caption_variant_used": "<MUST equal {assigned_variant_key!r} when
                          render_text is true; null only when render_text
                          is false>",

  "render_text": true | false,

  "headline_verbatim": "\\"...\\"  // null when render_text is false",
  "subhead_verbatim":  "\\"...\\"  // null when not used",
  "cta_verbatim":      "\\"... →\\"  // null when no CTA",
  "cta_url":           "https://...  // null when no CTA",

  "total_rendered_text_words": "<integer count across headline + subhead +
                               cta + any labels — aim for ≤ 12, never > 20>",

  "final_render_prompt": "<-- A coherent 180-260 word PARAGRAPH for the
                            image-generation model. Must include:
                              • the chosen direction,
                              • the background described by colour NAMES,
                              • the focal point / hero subject if any,
                              • the typography style if text appears,
                              • exact verbatim text in DOUBLE QUOTES,
                              • the palette as named colours,
                              • the composition layout (where things sit),
                              • cinematic / camera vocabulary where useful,
                              • explicit instructions to avoid dark
                                glass-morphism, glow-on-void, generic 3D
                                render, floating UI cards, hex codes as
                                text, fake third-party logos, duplicate
                                elements.
                            End the paragraph with the literal sentence:
                              'High-resolution social-media image, designed
                               in the style of a senior in-house designer
                               at a top brand team — no AI-render aesthetic,
                               no glass-morphism, no glow-on-void, no
                               generic isometric 3D.'>"
}}
""".strip()


def _art_director_v2_agent(
    refined_brief: str,
    caption_platform: str,
    assigned_variant_key: str,
    assigned_variant_block_text: str,
    user_context: str,
    dna_attached: str,
    primary_color: str,
    variant_idx: int,
    n_variants: int,
    client,
) -> dict | None:
    """Call gemini-2.5-flash with the AD v2 prompt + 8 few-shot reference
    images. The AD is locked to one assigned caption variant — its
    `caption_variant_used` output must equal `assigned_variant_key`.

    Returns the parsed JSON blueprint, or None on failure (caller falls
    back to the direct prompt path).
    """
    if client is None:
        logger.error("[art_director_v2] client is None — cannot call AD")
        return None

    prompt = _build_art_director_v2_prompt(
        refined_brief, caption_platform, assigned_variant_key,
        assigned_variant_block_text, user_context, dna_attached,
        primary_color, variant_idx, n_variants,
    )

    # Same few-shot triple structure used on the image-model call. The AD
    # studies the craft bar from the GOOD references and the failure modes
    # from the BAD anti-examples BEFORE writing the render prompt.
    contents: list = [prompt]
    contents.extend(_build_fewshot_contents(variant_idx, n_variants))

    try:
        # response_mime_type forces strict JSON output. top_p/top_k pinned
        # to Gemini defaults for auditability (matches the convention used
        # by _call_agent in ai_service.py).
        res = client.models.generate_content(
            model=ART_DIRECTOR_V2_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.85,
                top_p=0.95,
                top_k=40,
                response_mime_type="application/json",
            ),
        )
        text = (res.text or "").strip()
        # Belt-and-braces: strip markdown fences if the model wrapped despite
        # response_mime_type. Some Gemini responses still include ```json.
        if text.startswith("```"):
            text = text.split("```", 2)[-1].strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
            text = text.rsplit("```", 1)[0].strip()
        import json as _json
        blueprint = _json.loads(text)
        if not isinstance(blueprint, dict):
            logger.error(f"[art_director_v2] variant {variant_idx + 1}: parsed JSON is not a dict")
            return None
        chosen_caption = blueprint.get("caption_variant_used")
        if chosen_caption and chosen_caption.lower() != assigned_variant_key.lower():
            logger.warning(
                f"[art_director_v2] variant {variant_idx + 1}: AD reported "
                f"caption_variant_used={chosen_caption!r} but assigned key was "
                f"{assigned_variant_key!r}. Forcing the field to the assigned key."
            )
            blueprint["caption_variant_used"] = assigned_variant_key

        # AD has full design freedom on `direction` — we don't substitute.
        # We DO still enforce the third-party-logo policy per category:
        # when the dynamically-picked category's default is no third-party
        # logos, we clear the array so the image model isn't tempted to
        # invent or substitute them.
        category = _get_style_category(refined_brief, variant_idx, n_variants)
        if category["no_third_party_logos_default"]:
            tplr = blueprint.get("third_party_logos_to_render") or []
            if tplr:
                logger.info(
                    f"[art_director_v2] variant {variant_idx + 1}: category "
                    f"{category['slug']!r} keeps third_party_logos_to_render "
                    f"empty (brand-safety policy); AD had listed {tplr!r}, "
                    f"clearing to []."
                )
                blueprint["third_party_logos_to_render"] = []
        # Record the dynamically-picked category on the blueprint so the
        # CSV logger can see which category drove this variant.
        blueprint.setdefault("style_category", category["slug"])
        # Pull the dynamic category for the log line.
        _cat = _get_style_category(refined_brief, variant_idx, n_variants)
        logger.info(
            f"[art_director_v2] variant {variant_idx + 1} (Set {chr(ord('A') + variant_idx % 3)}) "
            f"category={_cat['slug']!r} "
            f"assigned={assigned_variant_key!r} "
            f"direction={blueprint.get('direction')!r} "
            f"render_text={blueprint.get('render_text')}"
        )
        return blueprint
    except Exception as e:
        logger.error(f"[art_director_v2] variant {variant_idx + 1} failed: {e}")
        return None


# Guardrail block appended to every blueprint render prompt before it ships
# to the image model. Defence in depth: AD's prompt already steers away
# from failures, but the image model still sees the explicit rules at
# render time.
_IMAGE_MODEL_GUARDRAILS_BLOCK = """

══════════════════════════════════════════════════════════════════
NON-NEGOTIABLE GUARDRAILS
══════════════════════════════════════════════════════════════════

[R1] SPELLING — Every word you render must be spelled correctly. All
     rendered text is provided in DOUBLE QUOTES in the prompt above.
     Render those strings character-for-character. Do not paraphrase,
     do not abbreviate, do not invent.

[R2] HEX CODES NEVER AS TEXT — Do not render any "#RRGGBB" string as
     visible text. Colours are described above by NAME; apply them
     visually only.

[R3] NO DUPLICATED ELEMENTS — One headline, one focal subject, one
     logo. Never repeat the same icon, brand mark, or phrase.

[R4] LOGO INTEGRITY — three layers.
     (1) Brand's own logo (if attached at the END of this request as
         the only reference image): render EXACTLY in the position the
         prompt above specifies, OR omit entirely. No recolour, no
         restyle, no redraw.
     (2) Third-party brand logos (Salesforce, AWS, Google, Meta,
         OpenAI, Slack, HubSpot, Snowflake, TikTok, LinkedIn, Stripe,
         Notion, etc.) — if the prompt above instructs you to render
         specific third-party brand marks, render each one FAITHFULLY:
         correct official shape, correct official colours, correct
         official wordmark/glyph. The real, public, recognisable mark.
         If you cannot render a third-party brand mark faithfully at
         the size and quality required, OMIT it entirely from the image.
     (3) ABSOLUTE PROHIBITIONS:
           • NEVER invent a hallucinated version of any brand mark
             (no fake "Salesforce" cloud with a wrong shape, no
             squirrel-as-Spenzo).
           • NEVER substitute a generic icon (cloud / chart / cube /
             connector-line / database stack / building / infinity /
             music-note) where a real brand logo would go. Generic
             substitutes for real brands are an automatic reject.

[R5] AVOID THE DEFAULT AI AESTHETIC — Do NOT default to dark glass-
     morphism, glow-on-void backgrounds, generic isometric 3D render,
     floating UI cards, or cinematic neon. Render in the natural,
     designer-led style described in the prompt above.

OUTPUT
One high-resolution social-media image at the configured aspect ratio.
""".rstrip()


def _build_image_prompt_from_blueprint(blueprint: dict) -> str:
    """Concatenate the AD's `final_render_prompt` + R1–R5 guardrails block.
    This is the text payload sent to gemini-3.1-flash-image."""
    render_prompt = (blueprint or {}).get("final_render_prompt", "") or ""
    return render_prompt.rstrip() + _IMAGE_MODEL_GUARDRAILS_BLOCK


def _build_master_prompt(
    refined_brief: str,
    recommended_copy_per_platform: dict,
    user_context: str,
    dna_attached: str,
    primary_color: str,
    has_logo: bool,
    variant_idx: int,
    n_variants: int,
) -> str:
    """Build the image-gen prompt for one variant.

    v4 prompt philosophy: NO restrictions. NO fixed mood menu. NO forced
    template. The model decides everything — whether the image carries
    text, the design, the typography, the composition, the visual style.

    Primary source of truth = the refined brief + the AI-recommended
    caption that will accompany the image. The model designs FOR them.

    Business DNA is supplementary knowledge — consulted ONLY when the
    brief needs extra brand / product context the brief itself doesn't
    already carry. Cross-topic briefs (general AI news, industry trends)
    ignore DNA.
    """
    # NOTE: aspect ratio is no longer described in the prompt — it's passed to
    # the model via `types.ImageConfig(aspect_ratio=...)` on the GenerateContentConfig.
    # That is the SDK-sanctioned way for image-gen models and is more reliable
    # than asking the model in natural language to compose to a specific shape.

    knowledge_block = (
        user_context.strip() if (dna_attached == "yes" and user_context and user_context.strip())
        else "(no brand knowledge attached — design from the brief and the caption only)"
    )

    # Colours: model picks freely. No DNA-palette instruction. The user wants
    # variety and doesn't want the brand colour anchoring every output.
    colour_note = (
        "COLOURS: pick whatever palette fits the campaign and your composition. "
        "You are NOT bound to any brand colour — vary it across generations."
    )

    if has_logo:
        logo_note = (
            "LOGO: the brand's official logo is attached with this prompt as a "
            "reference image. USE this logo as a brand mark somewhere in the "
            "image (corner watermark, footer chip, hero block — your call where). "
            "It MUST be the attached logo EXACTLY — same shape, proportions, "
            "colours, typography. Do NOT redraw, modify, recolour, restyle, or "
            "invent your own version of the brand mark. If you cannot render the "
            "attached logo pixel-faithfully at the size and angle you need, omit "
            "it entirely rather than approximate it."
        )
    else:
        logo_note = (
            "LOGO: no logo provided — do not invent or render any brand mark or "
            "wordmark."
        )

    return f"""
Design a high-end social-media post image in the style of a senior CANVA / FIGMA
designer making a brand campaign. Editorial, polished, intentional — like a
magazine cover or a brand poster, NOT an AI-generated schematic infographic.

CAMPAIGN BRIEF:
{refined_brief}

PUBLISHED CAPTION that goes with this image:
{_format_copy_block(recommended_copy_per_platform)}

BRAND KNOWLEDGE (use if the brief is about this brand, ignore if it's a general topic):
{knowledge_block}

{colour_note}
{logo_note}

═══════════════════════════════════════════════════════════════
DESIGN APPROACH
═══════════════════════════════════════════════════════════════
• Think editorial / magazine / poster / Canva-template aesthetic. The kind of
  image a senior brand designer hands to a client.
• Strong TYPOGRAPHY HIERARCHY is a primary tool — a bold headline, a clean
  secondary line, designed type as a visual element. Treat type as part of the
  composition, not an afterthought.
• Real-world references for the LOOK: Stripe blog covers, Linear changelog
  headers, Adobe Firefly hero images, Apple marketing pages, magazine spreads,
  product-launch posters by Pentagram or Mother Design.
• Vary the LAYOUT every generation. Don't default to one composition or one
  style — different briefs deserve different approaches.

═══════════════════════════════════════════════════════════════
WHAT YOU DECIDE (your complete freedom)
═══════════════════════════════════════════════════════════════
• Whether to include people, product UI, photography, illustration, 3D, flat
  vector, infographics, flow diagrams, or pure typography — pick what fits
  the brief
• Headline / subheading / body / CTA — when and how to render text
• Layout, grid, asymmetry, focal point
• Colour palette and mood
• Visual style — editorial photo / brand poster / lifestyle / abstract /
  infographic / flow diagram / data-viz / whatever serves the brief

Design the strongest, most human-designer-looking image you can for this brief and caption.
""".strip()


def _generate_single_variant(
    refined_brief: str,
    recommended_copy_per_platform: dict,
    user_context: str,
    dna_attached: str,
    primary_color: str,
    logo_bytes: bytes | None,
    aspect_ratio: str,
    variant_idx: int,
    n_variants: int,
    model_id: str,
    client,
    # NEW (v2 AD path). Bypass path ignores these.
    caption_platform: str = "",
    assigned_variant_key: str = "",
    assigned_variant_block_text: str = "",
) -> dict | None:
    """Two-stage image generation for one variant.

    Three paths:
      (1) BYPASS_ART_DIRECTOR=true (legacy direct): _build_direct_image_prompt
          sends the full structured prompt + few-shot + logo to image model
          in one shot. The 8 reference images carry the design guidance.
      (2) BYPASS_ART_DIRECTOR=false (default): Art Director v2 — calls
          gemini-2.5-flash multimodal with the AD prompt + 8 reference
          images, parses a JSON blueprint, then uses blueprint
          .final_render_prompt as the image-model text payload.
      (3) Legacy AD (v1) is dead-code (`_art_director_agent` text-only
          agent) — preserved in source for git history but unreachable
          from the live path.

    Returns dict or None on failure.
    """
    has_logo = bool(logo_bytes)
    blueprint: dict | None = None   # surfaced into the return dict for CSV

    # ── Stage A: build the image-gen prompt ──
    if BYPASS_ART_DIRECTOR:
        director_prompt = _build_direct_image_prompt(
            refined_brief,
            recommended_copy_per_platform,
            user_context,
            dna_attached,
            primary_color,
            variant_idx,
            n_variants,
        )
        logger.info(
            f"[image_v4] variant {variant_idx + 1} BYPASS art director "
            f"(direct prompt path, {len(director_prompt)} chars)"
        )
    else:
        # AD v2 path — text agent reads brief + caption variants + few-shot,
        # emits structured JSON blueprint; image model receives blueprint's
        # final_render_prompt + R1-R5 guardrails + same few-shot.
        blueprint = _art_director_v2_agent(
            refined_brief=refined_brief,
            caption_platform=caption_platform,
            assigned_variant_key=assigned_variant_key,
            assigned_variant_block_text=assigned_variant_block_text,
            user_context=user_context,
            dna_attached=dna_attached,
            primary_color=primary_color,
            variant_idx=variant_idx,
            n_variants=n_variants,
            client=client,
        )
        if blueprint is None or not blueprint.get("final_render_prompt"):
            # AD failed — fall back to the direct path so the user still gets
            # an image. The CSV will show blueprint=null for this row, which
            # is what we want for debugging.
            logger.warning(
                f"[image_v4] variant {variant_idx + 1}: AD v2 failed, "
                f"falling back to direct prompt path"
            )
            director_prompt = _build_direct_image_prompt(
                refined_brief,
                recommended_copy_per_platform,
                user_context,
                dna_attached,
                primary_color,
                variant_idx,
                n_variants,
            )
            blueprint = None
        else:
            director_prompt = _build_image_prompt_from_blueprint(blueprint)
            logger.info(
                f"[image_v4] variant {variant_idx + 1} AD v2 blueprint applied "
                f"(direction={blueprint.get('direction')!r}, "
                f"render_text={blueprint.get('render_text')}, "
                f"prompt {len(director_prompt)} chars)"
            )

    # Append the logo rule as a technical constraint — the Art Director focuses
    # purely on the creative concept; logo fidelity is a guardrail attached at
    # the image-gen step so we never let the model invent a fake brand mark.
    if has_logo:
        director_prompt = (
            director_prompt
            + "\n\nIMPORTANT — BRAND LOGO: A reference logo image is attached "
            "alongside this prompt. If a brand mark appears anywhere in the "
            "image, you MUST use the attached reference logo EXACTLY — same "
            "shape, proportions, colours, typography. Do NOT redraw, recolour, "
            "restyle, or invent your own version. If you cannot render it "
            "pixel-faithfully at the size and angle you need, omit the logo "
            "entirely rather than approximate it."
        )
    else:
        director_prompt = (
            director_prompt
            + "\n\nIMPORTANT — NO BRAND LOGO available. Do not invent or render "
            "any brand mark, wordmark, or watermark."
        )

    # ── Stage B: Multimodal contents for the image model ──
    # Structure:
    #   [main prompt — AD's final_render_prompt + R1-R5 guardrails OR
    #                  the bypass-path direct prompt]
    #   [logo PIL image, optional]
    #
    # CHANGE 2026-06-05: few-shot reference images are NO LONGER attached
    # to the image-model call. They are only used at the AD stage, where
    # they inform the AD's design decisions. Attaching them to the image
    # model was biasing the diffusion model to copy the references' literal
    # subject matter (e.g. rendering infographics because most references
    # show structured compositions). Removing them frees the image model to
    # render exactly what the AD's render-prompt paragraph describes.
    contents: list = [director_prompt]
    logger.info(
        f"[image_v4] variant {variant_idx + 1}: image-model call uses "
        f"AD's render prompt + logo only "
        f"(no reference images at this stage)"
    )

    if has_logo:
        try:
            from PIL import Image as PILImage
            logo_img = PILImage.open(BytesIO(logo_bytes))
            if logo_img.mode not in ("RGB", "RGBA"):
                logo_img = logo_img.convert("RGBA")
            contents.append(logo_img)
        except Exception as e:
            logger.warning(
                f"[image_v4] variant {variant_idx + 1}: could not attach logo "
                f"reference image ({e}) — generating without it"
            )

    # Aspect ratio + size go through ImageConfig — the SDK-sanctioned channel
    # for image-gen parameters. Reliable than asking the model in prose.
    # Temperature 1.5 widens the sampling distribution so the 3 parallel variants
    # land on visibly different visual directions instead of all collapsing to the
    # model's default aesthetic (the glass-morphism + orange-glow archetype we
    # were stuck in). 1.0 was the silent default; 1.5 trades a small risk of
    # weirder compositions for noticeably more style scatter across the batch.
    # top_p and top_k pinned to Gemini API defaults — explicit so the config
    # is auditable and reproducible (no silent reliance on SDK defaults that
    # could drift across SDK versions).
    gen_config = types.GenerateContentConfig(
        response_modalities=["IMAGE", "TEXT"],
        temperature=1.5,
        top_p=0.95,
        top_k=40,
        image_config=types.ImageConfig(
            aspect_ratio=aspect_ratio,
            image_size="1K",
        ),
    )

    if client is None:
        logger.error("[image_v4] Gemini client not initialised — set GEMINI_API_KEY")
        return None

    used_model = model_id
    image_bytes = None
    try:
        try:
            stream = client.models.generate_content_stream(
                model=model_id,
                contents=contents,
                config=gen_config,
            )
            for chunk in stream:
                if chunk.parts:
                    for part in chunk.parts:
                        if part.inline_data:
                            image_bytes = part.inline_data.data
                            break
                if image_bytes:
                    break
        except Exception as primary_err:
            # If the configured model id is rejected (e.g. 3.1 not available in
            # this project yet), retry once with the fallback model.
            logger.warning(
                f"[image_v4] variant {variant_idx + 1}: primary model {model_id!r} failed "
                f"({primary_err}); retrying with fallback {FALLBACK_MODEL!r}"
            )
            used_model = FALLBACK_MODEL
            stream = client.models.generate_content_stream(
                model=FALLBACK_MODEL,
                contents=contents,
                config=gen_config,
            )
            for chunk in stream:
                if chunk.parts:
                    for part in chunk.parts:
                        if part.inline_data:
                            image_bytes = part.inline_data.data
                            break
                if image_bytes:
                    break

        if not image_bytes:
            logger.error(f"[image_v4] variant {variant_idx + 1}: model returned no image")
            return None

        clean = _strip_image_metadata(image_bytes)

        # Upload to S3
        s3 = get_s3_client()
        key = f"ai_gen/v4/visual_{uuid.uuid4().hex}.png"
        s3.upload_fileobj(
            BytesIO(clean),
            S3_BUCKET_NAME,
            key,
            ExtraArgs={"ContentType": "image/png"},
        )
        url = get_s3_url(key)
        logger.info(f"[image_v4] variant {variant_idx + 1} ready (model={used_model}) → {url}")

        return {
            "url": url,
            "prompt": director_prompt,
            "variant_idx": variant_idx,
            "model": used_model,
            "pipeline": "image_v4",
            # AD v2 metadata — None when bypass path or AD failed.
            "ad_blueprint": blueprint,
            "ad_direction": (blueprint or {}).get("direction"),
            "ad_caption_variant_used": (blueprint or {}).get("caption_variant_used"),
            "ad_render_text": (blueprint or {}).get("render_text"),
        }
    except Exception as e:
        logger.error(f"[image_v4] variant {variant_idx + 1} failed: {e}")
        return None


def generate_image_variants_v4(
    refined_brief: str,
    content_data: dict,
    recommendation: dict,
    user_context: str = "",
    primary_color: str = "#FF4500",
    logo_bytes: bytes | None = None,
    aspect_ratio: str = "16:9",
    n_variants: int = 3,
    client=None,
) -> list[dict]:
    """Top-level entrypoint called by the orchestrator.

    Reads the orchestrator's `content_data` + `recommendation`, extracts the
    recommended copy per platform, then fires N parallel image generations.

    `user_context` is the Business DNA block (same string produced by
    `_build_user_context` in the orchestrator). Passed through to the image
    model as supplementary knowledge — the model is told to use it only when
    the brief / caption is brand-focused, and ignore it otherwise. Empty
    string is treated as "no DNA attached".

    Returns a list of variant dicts (failed slots dropped).
    """
    # Extract recommended copy per platform (used by the BYPASS direct path).
    best_key = (recommendation or {}).get("best_variant") or "viral_reach"
    content_block = (content_data or {}).get("content") or {}
    recommended_copy_per_platform = {}
    for platform, variants in content_block.items():
        if not isinstance(variants, dict):
            continue
        copy = variants.get(best_key)
        if not copy:
            # Fall back to whichever variant exists if the recommended key
            # wasn't emitted for this platform.
            copy = next((v for v in variants.values() if isinstance(v, str) and v.strip()), None)
        if copy:
            recommended_copy_per_platform[platform] = copy

    if not recommended_copy_per_platform:
        logger.warning("[image_v4] no recommended copy found for any platform — variants will be brief-only")

    # NEW (AD v2 path) — pick ALL variants from ONE platform for the image
    # stage, then DISTRIBUTE them across the parallel image variants so
    # each image variant uses a DIFFERENT caption variant from the same
    # platform. LinkedIn first; if not selected, any platform with content.
    selected_platforms = list(content_block.keys())
    caption_platform, caption_variants_dict = _select_caption_block_for_image(
        content_block, selected_platforms,
    )

    # Compute the per-variant assignment up-front. Image variant 0 gets
    # viral_reach, variant 1 gets high_interaction, variant 2 gets
    # follower_growth (in order of platform-variant availability). Wraps
    # with modulo when fewer caption variants exist than image variants.
    variant_assignments: list[tuple[str, str]] = [
        _assign_caption_variant_key(caption_variants_dict, i)
        for i in range(n_variants)
    ]
    assignment_summary = ", ".join(
        f"img{i + 1}→{k or 'none'}" for i, (k, _t) in enumerate(variant_assignments)
    )

    dna_attached = "yes" if (user_context and user_context.strip()) else "no"
    model_id = DEFAULT_MODEL
    logger.info(
        f"[image_v4] firing {n_variants} variants (model={model_id}, aspect={aspect_ratio}, "
        f"platforms={list(recommended_copy_per_platform.keys())}, dna_attached={dna_attached}, "
        f"logo_attached={bool(logo_bytes)}, "
        f"AD_v2={'OFF (BYPASS)' if BYPASS_ART_DIRECTOR else 'ON'}, "
        f"caption_source={caption_platform}, assignments=[{assignment_summary}])"
    )

    results: list[dict | None] = [None] * n_variants
    with ThreadPoolExecutor(max_workers=n_variants) as ex:
        # Build per-variant futures with each variant's distinct assigned
        # caption block. THIS is what forces image variants 1/2/3 to use
        # DIFFERENT caption variants from the same platform.
        futures = {}
        for i in range(n_variants):
            assigned_key, assigned_text = variant_assignments[i]
            assigned_block = _format_single_assigned_variant_block(
                caption_platform, assigned_key, assigned_text,
            )
            fut = ex.submit(
                _generate_single_variant,
                refined_brief,
                recommended_copy_per_platform,
                user_context,
                dna_attached,
                primary_color,
                logo_bytes,
                aspect_ratio,
                i,
                n_variants,
                model_id,
                client,
                caption_platform,
                assigned_key,
                assigned_block,
            )
            futures[fut] = i
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:
                logger.error(f"[image_v4] variant {idx + 1} future raised: {e}")
                results[idx] = None

    # Stamp the caption-source platform onto every successful variant so
    # the CSV logger can record which platform's variant block fed the AD
    # for this row.
    for r in results:
        if isinstance(r, dict):
            r["caption_platform"] = caption_platform

    successful = [r for r in results if r]
    logger.info(f"[image_v4] {len(successful)}/{n_variants} variants succeeded")
    return successful
