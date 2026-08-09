"""User Intent pipeline — Fortune 500 enterprise-brand direction.

Two-stage pipeline for the `user_intent_post` style:

  1. PROMPT WRITER (GPT-5.1) — reads Business DNA + raw campaign brief +
     post text; writes a fully structured, section-organised design brief
     modelled on the Fortune-500 enterprise-brand prompt template
     (Company / Business / Canvas / Design Style / Layout / Typography /
     Image / Brand / Content / Rendering Requirements).

  2. RENDERER (gpt-image-2 quality=high) — takes the Prompt Writer's
     `final_prompt` verbatim and renders it. Only the brand logo is
     attached (top-left placement handled inside the final_prompt);
     NO style reference images.

Aesthetic direction: "should belong on the LinkedIn page of Microsoft,
Nvidia, IBM, Salesforce, or OpenAI." Premium, enterprise, editorial —
Adobe Illustrator polish, not AI-generated look.
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

logger = logging.getLogger("pipelyt.user_intent")

# GPT-5.1 for the Prompt Writer call.
_USER_INTENT_MODEL = os.environ.get("USER_INTENT_MODEL", "gpt-5.1")

# gpt-image-2 at high quality for the render.
_IMAGE_AGENT_MODEL   = os.environ.get("USER_INTENT_IMAGE_MODEL",   "gpt-image-2")
_IMAGE_AGENT_QUALITY = os.environ.get("USER_INTENT_IMAGE_QUALITY", "high").strip().lower()

# Aspect-ratio → OpenAI size mapping. Kept local to avoid a circular import.
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
            "OPENAI_API_KEY env var not set — required by the User Intent agent."
        )
    return OpenAI(api_key=key)


# ══════════════════════════════════════════════════════════════════════
# DNA CONTEXT — compact summary for the Prompt Writer's user prompt
# ══════════════════════════════════════════════════════════════════════
def _dna_context_summary(business_dna: Optional[dict]) -> str:
    """Compact DNA summary — brand facts the Prompt Writer needs to
    author an accurate structured prompt (company name, tagline, colours,
    tone, overview, uploaded doc excerpts). No design opinions here."""
    if not business_dna or not isinstance(business_dna, dict):
        return "(no Business DNA available)"

    lines: list[str] = []

    def _add(key: str, label: str | None = None, limit: int = 300):
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
    _add("tagline",      "TAGLINE")
    _add("category",     "CATEGORY")
    _add("brand_tone",   "BRAND TONE")
    _add("website",      "WEBSITE")

    colors = business_dna.get("colors") or {}
    if isinstance(colors, dict) and colors:
        color_bits = [f"{k}={v}" for k, v in colors.items() if v]
        if color_bits:
            lines.append("BRAND COLOURS (use ONLY these — never invent hex): "
                         + ", ".join(color_bits))

    overview = (business_dna.get("overview") or "").strip()
    if overview:
        lines.append("OVERVIEW:\n" + overview[:1500])

    services = business_dna.get("services") or business_dna.get("offerings")
    if services:
        if isinstance(services, list):
            lines.append("SERVICES / OFFERINGS: " + ", ".join(str(s) for s in services)[:600])
        else:
            lines.append("SERVICES / OFFERINGS: " + str(services)[:600])

    docs = business_dna.get("documents") or []
    used = 0
    for i, d in enumerate(docs, start=1):
        text = (d.get("text") or "").strip()
        if not text:
            continue
        name = d.get("name") or f"document_{i}"
        remaining = 3000 - used
        if remaining <= 200:
            break
        piece = text[:remaining]
        lines.append(f"UPLOADED DOC {i} — {name}:\n{piece}")
        used += len(piece)

    return "\n\n".join(lines) if lines else "(no Business DNA available)"


# ══════════════════════════════════════════════════════════════════════
# AGENT 1 — PROMPT WRITER (GPT-5.1)
# ══════════════════════════════════════════════════════════════════════
_PROMPT_WRITER_SYSTEM_PROMPT = """You are an award-winning brand designer who has worked at Fortune 500 studios (Microsoft, Nvidia, IBM, Salesforce, OpenAI, Apple).

Your objective is to create premium social media graphics that look like they were designed in Adobe Illustrator or Figma by a senior human designer — not AI-generated.

Prioritize:
- Clean hierarchy
- Perfect typography
- Editorial polish
- White space when it earns it, density when it earns it
- Premium iconography and imagery
- Balanced composition
- Readability at feed thumbnail size

Never overcrowd the canvas.

Always make the image look like it belongs on the LinkedIn page of a top-tier global brand.

═══════════════════════════════════════════════════════════════════
YOUR JOB
═══════════════════════════════════════════════════════════════════
You will be given a Business DNA, a raw campaign brief, the post text the customer wrote, and a FIXED aspect ratio. Your job is to write ONE fully structured design prompt (`final_prompt`) that will be passed VERBATIM to an image generation model (gpt-image-2). Return it inside a JSON object.

The `final_prompt` MUST be organised in labelled sections. Follow this exact structure and section order:

  Create a premium LinkedIn social media post.

  Company:
  {brand name}

  Business:
  {2-6 short lines: category, sub-services, focus areas — adapted to what this business actually does}

  Canvas
  {the aspect ratio you were given, exact}
  {pixel size matching that aspect, e.g. 2048x2048 for 1:1, 2048x2560 for 4:5, 2048x1152 for 16:9}

  Design Style
  {bullet list — pick the language that fits THIS brand and post. Draw from: Modern enterprise / Editorial magazine / Minimal / Premium / Corporate infographic / Bold hero / Documentary / Product-first / Craftsmanship / Cinematic. Apple + Microsoft + OpenAI + Bloomberg + Wired design language references welcome. Soft shadows / rounded cards / glass morphism / gradients — use when they serve the design; skip when they don't.

   **BACKGROUND IS LOCKED TO PURE WHITE #FFFFFF.** Do not use off-white, cream, warm white, light grey, dark navy, gradients, or any other background colour. The main canvas background must be pure #FFFFFF everywhere except inside dark-band footer strips or the CTA button. State this explicitly in the Design Style bullet list.}

  Layout
  {clear spatial description: where the logo goes (always top-left unless the brand explicitly does otherwise), where the hero headline sits, what supporting content sits where, where the CTA lives, how whitespace is used. Be specific about proportions and alignment.

   **LOGO + BRAND WORDMARK RULE (critical):** The attached logo image is usually an ICON ONLY (no wordmark text). To make the brand recognisable, ALWAYS instruct the render to place the brand name as a WORDMARK immediately to the right of the logo icon, forming a clean logo-lockup — the icon and wordmark together. Specify:
     - "Icon: [the attached logo], top-left corner"
     - "Wordmark: '{brand name}' rendered immediately to the right of the icon in the heading colour, matched to the icon's height, single line"

   **CATEGORY / SECTION TAG PLACEMENT (critical):** Any small category or section tag (e.g. "Marketing Intelligence", "Enterprise AI Manifesto") MUST be placed clearly SEPARATE from the logo lockup — put it in the TOP-RIGHT corner, or above the headline as an eyebrow label, or below the subheadline. NEVER place it immediately adjacent to the logo icon. If it sits next to the icon, the image model will treat it as the brand wordmark and replace "{brand name}" with the tag text — which breaks brand identity.}

  Typography
  {heading size + weight, subheading, body, caption. Font-family suggestions welcome (SF Pro, Inter, Neue Haas, Söhne, Suisse). Line count caps for each element. Colour of text.

   **ACCENT HIGHLIGHT IN HEADLINE (important editorial pattern):** When the headline contains a distinctive phrase or 2-4 key words that carry the emotional punch (like "doesn't have to be a black box", "From X to Y", "Real Impact", "transformed", "won't fix", "is closing"), colour those specific words in the PRIMARY ACCENT hex while the rest of the headline stays in the heading colour. This is standard editorial magazine practice and adds strong visual hierarchy.

   Specify this in the Content > Headline block explicitly, e.g.:
     Headline text: "Marketing Mix Modeling (MMM) doesn't have to be a black box."
     Highlighted words (in Primary Accent #FF5722): "doesn't have to be a black box"
     Rest of headline (in Heading colour #212B36): "Marketing Mix Modeling (MMM)"

   Pick the phrase that carries the punch. Don't highlight everything — pick a maximum of 5 consecutive words that would carry the emotional weight if you read the headline out loud.}

  Image
  {THIS IS CRITICAL — do NOT default to a category-locked treatment. Read the RAW CAMPAIGN BRIEF + POST TEXT + BUSINESS DNA together and pick the imagery approach that best serves THIS SPECIFIC POST. The business category is a hint, not a lane — a software company's thought-leadership post might work better with real editorial photography of a person using the product than with a UI mockup; a jewellery brand's "our story" post might work better with a founder portrait than with product shots.

   AVAILABLE IMAGERY APPROACHES (pick freely, mix if useful):
    • Real professional photography — a shopper, a doctor, executives in a meeting, wind turbines at sunset, a woman using a laptop, a factory floor, a founder portrait, a workshop scene
    • Product photography — hero product shots, lifestyle shots of the product in use, studio lighting, high-detail close-ups
    • UI mockups — dashboard screens, mobile app screens, product interfaces (only when the POST is genuinely about the product's UI)
    • Editorial double-exposure — human profile merged with cityscape / data / product context
    • Cinematic scene — one striking composed shot with strong lighting and mood
    • Abstract data visualisation — flowing lines, glowing nodes, geometric shapes (as PURE GRAPHIC — no fake text labels)
    • Photo + UI composite — real person using the product with product screens visible in scene
    • Illustration / infographic — flat vector illustration when the post is inherently a diagram/process
    • Documentary photography — real environments, unstaged moments, service-in-action

   HOW TO PICK — ask yourself these questions in order:
     1. What is the ONE thing this post is really about? (a person's story? a product feature? a process? a mindset shift? multiple industries?)
     2. What single image would a top editorial designer at Bloomberg / Wired / Fast Company / Apple use here?
     3. Would a real photograph land harder than an illustration for this specific message? (Often yes — real people, real environments, real products are usually more thumb-stopping than abstract data grids.)
     4. Does the POST reference specific things (industries, sectors, tools, people, environments)? If YES, show them literally with real imagery.
     5. Is the POST inherently a diagram/process/comparison? Only THEN reach for illustration / UI mockup / abstract data viz.

   HARD OVERRIDES:
    • **INDUSTRY SHOWCASE:** When the POST is about multiple industries/sectors the brand serves (e.g. "we serve Retail, Energy, Finance, Healthcare, Media, Logistics"), use REAL PROFESSIONAL PHOTOGRAPHY per industry — a shopper for Retail, wind turbines for Energy, a doctor for Healthcare, a trading floor for Finance, a streaming interface for Media, a truck for Transportation. One real photo per sector, NOT icons, NOT abstract nodes.
    • **PRODUCT LAUNCH:** For a physical product launch, use real product photography as the hero — the product itself, cinematically shot.
    • **THOUGHT-LEADERSHIP / MINDSET SHIFT:** Consider a real person / editorial portrait / double-exposure treatment — the human element carries authority.

   Do NOT default to "abstract vector illustrations only" or "no photography" or "always UI mockup for SaaS". Real photography is usually preferred when it fits.

   **HERO VISUAL MUST DECODE THE MESSAGE (critical):** The supporting visual has one job — a viewer scrolling the feed who has NOT read the headline yet should be able to look at the image and think "oh, this is about {marketing mix modeling / API integrations / enterprise AI adoption / etc.}" from the visual alone. If your visual could apply to any generic tech post, it fails this test.

   FORBIDDEN default visuals — do NOT describe any of these:
     - Abstract hub-and-spoke diagrams with generic app icons floating around them (search / mail / play / thumbs-up / share / message / cart / bell / globe) UNLESS those exact apps are the literal subject of the post
     - Generic "glowing sphere with spokes", "translucent 3D cube with connections", "abstract intelligence engine" that could apply to any AI post
     - Random floating icon clusters that don't decode to anything specific
     - Icon collages of consumer app symbols surrounding a brand mark
     - "Abstract data core" or "abstract enterprise architecture" — these are meaningless placeholders

   INSTEAD, pick imagery that ENCODES the post's specific concept. Ask: "what one thing is this post really teaching or claiming?" Then draw THAT.
     - Post about MMM / marketing analytics → real marketing dashboard mockup with actual channel names (Facebook, TV, Search, YouTube) and a real saturation curve chart (orange curve rising and plateauing) with actual axis labels
     - Post about API integrations / workflow → real named connections (e.g. "Salesforce → Snowflake → Slack") drawn as a labelled data-flow diagram, or a photo of an engineer wiring systems
     - Post about enterprise AI adoption → real executives in a modern boardroom looking at populated dashboards, or a data team at work
     - Post about a product feature → real UI mockup of THAT feature populated with realistic data
     - Post about a technique or method → literal visual of that technique (for "saturation curves", draw the curve labelled; for "constraint-based optimization", show a Pareto-frontier plot with labelled axes)
     - Post about industries → real photography of each industry (retail shopper, wind turbines, doctor with tablet, trading floor, etc.)
     - Thought-leadership post → real editorial portrait of a professional, cinematic scene, or documentary-style photograph

   Every visual element should answer: "what specific concept does this represent?" If the answer is "just tech / just AI / just data / just intelligence" — that's generic decoration and it must be replaced with something specific to THIS post.

   **NO SKELETON / WIREFRAME SUPPORTING VISUALS.** Do not describe:
     - Empty dashboard tab skeletons or blank chart wireframes
     - Placeholder graph outlines with no data
     - Generic browser window frames or empty phone frames
     - Faint grid patterns pretending to be a "spreadsheet" or "chart"
     - Wireframe UI panels with grey blocks or dashed lines as filler
     - Ghost-lined nodes or empty diagram scaffolding
   If you show UI, a dashboard, a chart, or a product screen, it must be POPULATED with realistic content: real bars/lines/curves with clear colours, real category labels (from the DNA or post text), populated cards with real copy. If you cannot populate it with real content, use a real photograph or a solid graphic instead — never an empty skeleton.

   When you specify imagery, be concrete: describe the subject, lighting, mood, angle, and how it composes with the text. Name the treatment: cinematic / documentary / editorial / product-hero / environmental / studio / candid.}

  Brand
  Primary Accent: {hex — pick from DNA colours or a complementary hex that pairs well. DNA is baseline, not a cage — if the DNA palette lacks a colour that would serve the design, add one that pairs harmoniously}
  Headings: {hex — dark navy, deep charcoal, or brand-appropriate}
  CTA button: {hex}

  Content
  Headline:
  Full text: {the exact headline text — bold, punchy}
  Highlighted phrase (rendered in Primary Accent colour): {the 2-5 key words within the headline that carry the emotional punch — must be a substring of the full text}
  Rest of headline (rendered in Heading colour): {the remaining part of the headline}

  Subheadline:
  {1-2 sentence sub-headline — adds context}

  {If the post genuinely calls for structured supporting content, ADD blocks like the below — otherwise skip them entirely. A short announcement or thought-leadership post might have just headline + subheadline + CTA with a strong hero image. Don't force cards where prose + a great image would be stronger.}

  Optional blocks (include ONLY if the post's substance warrants them):
    Industry Cards / Feature Cards / Service Cards / Pillar Cards
    {N cards — you decide how many, and how much detail per card. Some cards need only a title; others need title + 1 line; others need title + 2-3 bullets. Match card depth to what the post is actually saying.}

    Metrics
    {only when real numbers are in the DNA or post text — never invent percentages}

    Process / Timeline / Steps
    {only when the post describes a process}

    Quote / Testimonial
    {only when there's a real quote}

  CTA
  {short, natural CTA text — match the post's ask, e.g. "Explore our approach", "Book a Demo", "Talk to Us"}

  URL
  {clean brand domain from DNA `website` or from the post's URL, e.g. "spenzo.io" or "neuzenai.com" — no https://, no www., no trailing slash. Placed to the right of or beneath the CTA button. Skip only if no URL exists in the inputs.}

  Rendering Requirements
  Ultra sharp
  Readable text
  Professional spacing
  Magazine-quality composition
  No watermark
  No spelling mistakes
  No distorted icons
  No duplicate objects
  High detail
  Enterprise quality

═══════════════════════════════════════════════════════════════════
CRITICAL RULES
═══════════════════════════════════════════════════════════════════
- ASPECT RATIO IS FIXED — use exactly the aspect ratio provided in the input. Do NOT pick a different one. Set the pixel size to match.
- Every text string that must appear on the image (headline, subheadline, card names, metrics, CTA, URL) must be spelled EXACTLY as it will appear. Never leave placeholders.
- Colours: DNA colours are the baseline. You may add ONE complementary colour if it makes the design stronger (e.g. add a deep navy for headings if the DNA lacks one). Do not go wildly off-palette.
- **CTA BUTTON COLOUR MUST MATCH THE BRAND COLOUR HINT** provided in the input (usually the loudest brand accent — orange, red, or the primary brand identity colour). Do NOT swap to a secondary DNA colour like green for the CTA. If the input says `BRAND COLOUR HINT: #ff4500`, the CTA button background is `#ff4500` — non-negotiable.
- **SHOW THE BRAND URL position below the cta or bottom left or bottom righton the canvas .** Include the brand's clean domain (e.g. `spenzo.io`, `neuzenai.com`) somewhere on the image — usually to the right of or directly beneath the CTA button, or in the bottom-right corner as a small footer element. Pick the URL from the DNA `website` field OR from a URL that appears in the campaign brief / post text. Format rules:
    - Use the CLEAN domain form only: `spenzo.io`, `neuzenai.com`, `zyntegrate.com` — NEVER `https://` prefix, NEVER `www.`, NEVER trailing slashes, NEVER a full path
    - Render in the heading colour or the brand primary accent (whichever gives higher contrast against pure white)
    - Size: 22-32pt with a semi-bold weight so it stays crisp
    - If no URL exists in the DNA or the post, skip it — don't invent one
- Content sections are OPTIONAL — include only what the post actually needs. A great post with a bold headline, a subheadline, and a striking hero image needs no cards. A results-heavy post benefits from metrics. Match structure to substance.
- The logo will be attached as an image and MUST be placed top-left exactly as provided (mention this in the Layout section explicitly).
- Content must come from the DNA + post text + campaign brief. Do NOT invent product names, statistics, industries, or claims that aren't supported by the inputs.
- IDENTIFY the business type first. A jewellery brand should get product photography, not abstract vectors. A software brand should get UI + real user photography. A consulting brand should get editorial cinematic scenes. Match imagery to business — do not default to one look.
- If the campion breif provide any contact information like email and mobile number it should include in the image
═══════════════════════════════════════════════════════════════════
LEGIBILITY RULE — EVERY VISIBLE TEXT MUST BE READABLE
═══════════════════════════════════════════════════════════════════
Image models render small text as gibberish when it also has low contrast. The rule is: **text can be small ONLY if it also uses a bright, saturated, high-contrast colour that stays crisp against the pure white background.**

Every text element you place on the canvas must be readable. This applies to headline, subheadline, cards, metrics, CTA, brand tag, captions — everything.

**SMALL-TEXT COLOUR RULE (critical):** If a text element is below ~28pt at 2048x2048, it MUST use a bright, saturated, dark colour that pops against pure white:
  - GOOD small-text colours: deep navy (#0a1f44 / #12234a), deep charcoal (#111111 / #212B36), the brand's primary accent (e.g. bright orange #FF4500), a saturated dark brand colour
  - BAD small-text colours: light grey (#999, #AAA, #BBB), medium grey (#666, #777, #888), muted pastels, low-opacity dark colours (#111111 at 50% opacity), thin-weight fonts in any grey tone
  - Never render small text in colours where the contrast ratio against #FFFFFF is below 7:1

For any text under 28pt, state in the Typography section: text colour + weight + why it will remain legible.

**THIS RULE APPLIES TO ALL TEXT ON THE CANVAS — INCLUDING TEXT INSIDE THE SUPPORTING VISUAL.** Any text embedded in a dashboard mockup, chart, UI panel, chatbot bubble, product screen, phone frame, tooltip, legend, axis label, or in-image caption MUST follow the same small-text colour rule. When you describe a chart or UI mockup in the Image section, EXPLICITLY specify the colours of:
  - Chart axis labels and tick marks
  - Chart legend text
  - Chart series labels ("Search", "Social", "TV", etc.)
  - Data labels / percentages on bars, curves, and cards
  - Dashboard panel titles ("Channel Impact on Revenue", "Optimal Budget Allocation", etc.)
  - Chatbot message text (user bubbles AND assistant bubbles)
  - Any UI microtext (tab labels, buttons, filters, "Ask anything about..." placeholder, etc.)

Do NOT let the image model default these to light grey. Say "all chart labels in deep charcoal #212B36, semi-bold" or "chatbot messages in deep navy #0a1f44 on white bubbles". If a chart is on a dark navy card, in-card text must be white (#FFFFFF) at ≥ 24pt. Never leave embedded UI text colour unspecified — the model will pick grey and everything becomes unreadable.

**Metrics ARE welcome** when they're supported by the DNA, campaign brief, or post text — render them as BIG hero numbers with a short label underneath (like a magazine stat block):
  - GOOD:  "30%+" as a 120pt hero number, "OPERATIONAL EFFICIENCY" as a 28pt label under it
  - GOOD:  "$2.4M" as a 100pt figure, "annual savings" as a 24pt caption
  - BAD:   "98.7%" tucked inside a tiny dashboard mockup at 10pt with three other unreadable labels around it

Do NOT invent fake UI-panel labels purely as visual filler:
  - Avoid describing "small dashboard panels with labels like MODEL PERFORMANCE, BUSINESS IMPACT, USER ADOPTION" as background decoration — these become illegible microtext that ruins the design
  - If a dashboard or chart appears in the imagery, describe it as either (a) a graphic shape with NO text labels, OR (b) one clear panel with big legible text
  - Any percentage, statistic, or number that appears must come from a real source (DNA, brief, post text) — never invent numbers just to look "enterprise"

Sizing checklist for the final_prompt — call out sizes in the Typography section:
  - Headline: 80-120pt
  - Subheadline: 36-52pt
  - Hero metrics (if used): 100-160pt for the number, 24-32pt for the label
  - Card titles: 32-44pt
  - Card body: 24-32pt
  - CTA button text: 32-44pt
  - URL (near CTA): 22-32pt, semi-bold, in heading colour or brand accent
  - Brand tag (optional): 20-28pt

Nothing below ~20pt. Nothing invented purely as decoration.

═══════════════════════════════════════════════════════════════════
VISUAL RESTRAINT RULE
═══════════════════════════════════════════════════════════════════
Editorial designs use ONE strong hero visual, not five competing visual elements. Overloaded canvases look AI-generated; single-focus visuals look editorial.

Prefer:
  - ONE striking hero image (a double-exposure profile, a product hero, a cinematic scene, a bold illustration) + typography + cards + CTA
  - Big whitespace around the hero visual

Avoid:
  - Layering many small visualisations (chaos particles + UI panels + human silhouette + node network + charts + brand tag) — that's noise, not design
  - Filling every inch of the canvas with something

Rule of thumb: if you find yourself describing more than 3 distinct visual elements outside the text + cards, you're over-designing. Simplify.

═══════════════════════════════════════════════════════════════════
OUTPUT (return ONLY this JSON — no code fences, no prose)
═══════════════════════════════════════════════════════════════════
{
  "final_prompt":  "the full structured prompt text as specified above, ready to send verbatim to gpt-image-2",
  "aspect_ratio":  "MUST equal the aspect ratio provided in the input, verbatim",
  "heading":       "the exact headline that will appear on the image (for logging)",
  "cta_text":      "the exact CTA button text (for logging)",
  "reasoning":     "one sentence — why this composition and imagery approach fits THIS business and post"
}"""


_ASPECT_TO_PIXELS: dict[str, str] = {
    "1:1":    "2048x2048",
    "4:5":    "2048x2560",
    "9:16":   "1152x2048",
    "16:9":   "2048x1152",
    "1.91:1": "2048x1072",
}


def _build_prompt_writer_user_prompt(
    variant_type: str,
    post_text: str,
    campaign_brief: str,
    business_dna: Optional[dict],
    business_category: str,
    brand_name: str,
    primary_brand_color: str,
    aspect_ratio: str,
) -> str:
    variant_intent = {
        "viral_reach":       "Punchy, hook-driven — maximise thumb-stop reach. The headline should be a strong hook.",
        "follower_growth":   "Value-driven — clear proof of authority. Structured supporting content welcome IF the post substance warrants it.",
        "high_interaction":  "Question-based or thought-provoking — designed to draw comments and saves.",
        "festival_variant":  "Culturally warm — festival-themed while staying brand-aligned.",
    }.get(variant_type, "Value-driven, clean and confident.")

    dna_block = _dna_context_summary(business_dna)
    pixel_size = _ASPECT_TO_PIXELS.get(aspect_ratio, "2048x2048")

    return f"""RAW CAMPAIGN BRIEF (background context — do NOT paste onto the image):
{campaign_brief.strip()[:2000]}

POST TEXT CONTENT (the customer wrote this — the image should support this specific post):
{post_text.strip()}

VARIANT INTENT — {variant_type}:
{variant_intent}

BRAND NAME: {brand_name or '(unknown)'}
BUSINESS CATEGORY: {business_category or '(unset)'}
BRAND COLOUR HINT: {primary_brand_color or '(none)'}

═══════════════════════════════════════════════════════════════════
ASPECT RATIO — FIXED, DO NOT CHANGE
═══════════════════════════════════════════════════════════════════
Aspect ratio: {aspect_ratio}
Pixel size:   {pixel_size}

Use this exact aspect ratio in the Canvas section of your final_prompt. Return "aspect_ratio": "{aspect_ratio}" in your JSON. Do NOT pick a different aspect — this is the customer's choice for this campaign.

═══════════════════════════════════════════════════════════════════
BUSINESS DNA — colours are the baseline; you may add one complementary hex if it strengthens the design
═══════════════════════════════════════════════════════════════════
{dna_block}
═══════════════════════════════════════════════════════════════════

Now write the JSON with `final_prompt`. Structure the final_prompt in the exact labelled section format specified in your system prompt. IDENTIFY the business type first (physical product / software / service / consulting / etc.) and pick the imagery approach accordingly. Every text string that will appear on the image must be spelled out exactly. Include content sections (cards / metrics / process / etc.) ONLY when the post substance warrants them.
"""


def _call_prompt_writer(
    *,
    variant_type: str,
    post_text: str,
    campaign_brief: str,
    business_dna: Optional[dict],
    business_category: str,
    brand_name: str,
    primary_brand_color: str,
    aspect_ratio: str,
) -> dict:
    """Single GPT-5.1 call. Returns the validated JSON design brief."""
    client = _openai_client()
    user_prompt = _build_prompt_writer_user_prompt(
        variant_type=variant_type,
        post_text=post_text,
        campaign_brief=campaign_brief,
        business_dna=business_dna,
        business_category=business_category,
        brand_name=brand_name,
        primary_brand_color=primary_brand_color,
        aspect_ratio=aspect_ratio,
    )

    # Full-text log of the Prompt Writer INPUT for debugging.
    logger.info(
        f"\n\n══════════════════════════════════════════════════════════════════\n"
        f"[user-intent/writer] variant={variant_type} — PROMPT WRITER INPUT (user message):\n"
        f"══════════════════════════════════════════════════════════════════\n"
        f"{user_prompt}\n"
        f"══════════════════════════════════════════════════════════════════\n"
    )

    t0 = time.monotonic()
    resp = client.chat.completions.create(
        model=_USER_INTENT_MODEL,
        messages=[
            {"role": "system", "content": _PROMPT_WRITER_SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_completion_tokens=4000,
    )
    dur = time.monotonic() - t0
    raw = (resp.choices[0].message.content or "").strip()
    logger.info(
        f"[user-intent/writer] variant={variant_type} model={_USER_INTENT_MODEL} "
        f"dur={dur:.2f}s tokens=in:{resp.usage.prompt_tokens}/"
        f"out:{resp.usage.completion_tokens}"
    )

    # Record Prompt Writer (OpenAI text) in the per-request cost ledger.
    # Uses the ART_DIRECTOR agent slot so the CSV columns line up with the
    # standard flow — that column represents "the OpenAI prompt-writer for
    # this variant" whether it's Art Director, Prompt Writer, or Image
    # Director.
    try:
        _ledger = get_current_ledger()
        if _ledger is not None:
            _ledger.record_openai_text(
                model=_USER_INTENT_MODEL,
                input_tokens=int(getattr(resp.usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(resp.usage, "completion_tokens", 0) or 0),
                agent_slot="ART_DIRECTOR",
                time_sec=round(dur, 2),
            )
    except Exception as _le:
        logger.warning(f"[cost_ledger] failed to record Prompt Writer: {_le}")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(f"[user-intent/writer] invalid JSON: {exc}\nRaw: {raw[:600]}")
        raise RuntimeError("Prompt Writer produced invalid JSON") from exc

    # HARD-ENFORCE the customer's aspect ratio choice. If the LLM drifted
    # to a different ratio (it does — a lot), overwrite it. This is the
    # customer's campaign choice, not the model's.
    ar = str(parsed.get("aspect_ratio", "")).strip()
    if ar != aspect_ratio:
        logger.warning(
            f"[user-intent/writer] LLM returned aspect_ratio={ar!r} but "
            f"customer selected {aspect_ratio!r} — overwriting to enforce"
        )
        parsed["aspect_ratio"] = aspect_ratio
    if parsed["aspect_ratio"] not in VALID_ASPECT_RATIOS:
        logger.warning(
            f"[user-intent/writer] aspect_ratio {parsed['aspect_ratio']!r} "
            f"invalid — coercing to '1:1'"
        )
        parsed["aspect_ratio"] = "1:1"

    final_prompt = str(parsed.get("final_prompt", "")).strip()
    if not final_prompt:
        raise RuntimeError("Prompt Writer produced empty `final_prompt`")

    logger.info(
        f"[user-intent/writer] variant={variant_type} "
        f"aspect={parsed.get('aspect_ratio')!r} "
        f"heading={str(parsed.get('heading', ''))[:80]!r} "
        f"cta={str(parsed.get('cta_text', ''))!r} "
        f"final_prompt_chars={len(final_prompt)} "
        f"reasoning={str(parsed.get('reasoning', ''))[:120]!r}"
    )

    # Full-text log of the FINAL PROMPT (what gpt-image-2 will see verbatim).
    logger.info(
        f"\n\n══════════════════════════════════════════════════════════════════\n"
        f"[user-intent/writer] variant={variant_type} — FINAL PROMPT (sent to gpt-image-2):\n"
        f"══════════════════════════════════════════════════════════════════\n"
        f"{final_prompt}\n"
        f"══════════════════════════════════════════════════════════════════\n"
    )
    return parsed


# ══════════════════════════════════════════════════════════════════════
# AGENT 2 — RENDERER (gpt-image-2 high)
# ══════════════════════════════════════════════════════════════════════
def _call_image_renderer(brief: dict, logo_bytes: Optional[bytes]) -> tuple[bytes, dict]:
    """Call gpt-image-2 (quality=high). Returns (png_bytes, meta).

    Sends the Prompt Writer's `final_prompt` VERBATIM. Only the brand
    logo is attached (top-left placement is described inside final_prompt).
    """
    client = _openai_client()
    aspect_ratio = str(brief.get("aspect_ratio", "1:1")).strip()
    size = _ASPECT_TO_SIZE.get(aspect_ratio, _DEFAULT_SIZE)
    prompt_text = str(brief.get("final_prompt", "")).strip()

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
            f"User Intent renderer returned no image bytes "
            f"(model={_IMAGE_AGENT_MODEL}, quality={_IMAGE_AGENT_QUALITY}, size={size})"
        )
    png_bytes = base64.b64decode(resp.data[0].b64_json)

    # Record gpt-image-2 render in the per-request cost ledger.
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
                model=_IMAGE_AGENT_MODEL,
                quality=str(_IMAGE_AGENT_QUALITY).lower(),
                images=1,
                text_input_tokens=_text_in,
                image_input_tokens=_img_in,
                agent_slot="IMAGE_GENERATOR",
                time_sec=round(dur, 2),
            )
    except Exception as _le:
        logger.warning(f"[cost_ledger] failed to record User Intent render: {_le}")

    logger.info(
        f"[user-intent/render] model={_IMAGE_AGENT_MODEL} quality={_IMAGE_AGENT_QUALITY} "
        f"size={size} rendered {len(png_bytes):,} bytes in {dur:.2f}s "
        f"aspect={aspect_ratio!r} (logo={'yes' if logo_bytes else 'no'}, "
        f"style_refs=0 [Fortune-500 direction in prompt only])"
    )
    return png_bytes, {
        "model":       _IMAGE_AGENT_MODEL,
        "quality":     _IMAGE_AGENT_QUALITY,
        "size":        size,
        "render_time": round(dur, 2),
        "size_bytes":  len(png_bytes),
        "style_refs":  0,
    }


# ══════════════════════════════════════════════════════════════════════
# ORCHESTRATOR — one variant, end-to-end
# ══════════════════════════════════════════════════════════════════════
def run_user_intent_variant(
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
    pipeline can slot it into the same per-variant result envelope the
    standard flow uses.

    `aspect_ratio` comes from the customer's campaign choice — the LLM
    is NOT allowed to override it.
    """
    # Normalise the incoming aspect — fall back to 1:1 if the caller
    # sent something we don't support.
    aspect_ratio = (aspect_ratio or "1:1").strip()
    if aspect_ratio not in VALID_ASPECT_RATIOS:
        logger.warning(
            f"[user-intent] variant={variant_type} incoming aspect_ratio "
            f"{aspect_ratio!r} not in {VALID_ASPECT_RATIOS} — coercing to '1:1'"
        )
        aspect_ratio = "1:1"

    logger.info(
        f"[user-intent] variant={variant_type} BEGIN — Fortune-500 direction "
        f"(aspect locked to {aspect_ratio!r} from campaign)"
    )

    a1_t0 = time.monotonic()
    brief = _call_prompt_writer(
        variant_type=variant_type,
        post_text=post_text,
        campaign_brief=campaign_brief,
        business_dna=business_dna,
        business_category=business_category,
        brand_name=brand_name,
        primary_brand_color=primary_brand_color,
        aspect_ratio=aspect_ratio,
    )
    a1_time = round(time.monotonic() - a1_t0, 2)

    a2_t0 = time.monotonic()
    png_bytes, agent_meta = _call_image_renderer(brief, logo_bytes)
    a2_time = round(time.monotonic() - a2_t0, 2)

    logger.info(
        f"[user-intent] variant={variant_type} DONE — "
        f"writer={a1_time}s render={a2_time}s "
        f"aspect={brief.get('aspect_ratio')} cta={brief.get('cta_text')!r}"
    )

    return {
        "director_brief":    brief,
        "image_prompt":      brief.get("final_prompt", ""),
        "png_bytes":         png_bytes,
        "agent1_time_s":     a1_time,
        "agent2_time_s":     a2_time,
        "director_model":    _USER_INTENT_MODEL,
        "image_agent_model": agent_meta["model"],
        "aspect_ratio":      brief.get("aspect_ratio", "1:1"),
        "style_refs":        0,
    }
