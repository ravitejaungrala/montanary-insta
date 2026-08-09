"""Test harness — Step 1 (v2).

Real Spenzo DNA = AI-powered marketing intelligence platform.
Visualist v2 key shifts vs. v1:
  - Drop "people using the product" — product identity comes from composite layer
  - Image shows the AUDIENCE in their real INDUSTRY CONTEXT matching the headline
  - Editorial DOCUMENTARY style — sharp everywhere, NO portrait blur, NO shallow DOF
  - Clear faces, clear people, clear background, crisp focus end-to-end

Usage:
    cd apps/backend && PYTHONIOENCODING=utf-8 python scripts/visualist_harness/01_run_visualist.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")

from google import genai  # type: ignore

API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not API_KEY:
    raise SystemExit("Set GEMINI_API_KEY in apps/backend/.env")

client = genai.Client(api_key=API_KEY)

# ---------------------------------------------------------------------------
# Real Spenzo campaign fixture
# ---------------------------------------------------------------------------

SAMPLE_REFINED_BRIEF = """
TOPIC: Spenzo — AI-powered marketing intelligence platform launch
AUDIENCE: Marketing leaders, performance marketers, and data teams managing large-scale paid ad budgets at mid-market to enterprise companies
KEY MESSAGE: Stop guessing where your ad spend is going. Spenzo unifies every channel into one intelligent layer and forecasts outcomes with 92% model accuracy.
ANGLE: "Your MMM shouldn't take a quarter — it should take a Slack message."
SUPPORTING POINTS:
- Unified data across AWS, Snowflake, Google Ads, Meta Ads, TikTok [DNA: products.spenzo.integrations]
- 3.2x ROAS improvement across pilot accounts [user brief]
- MMM-based forecasting with 92% model accuracy [user brief]
- Conversational AI — ask questions in plain English, get budget-allocation answers [DNA: products.spenzo.capabilities]
TONE: Sharp, analytical, results-driven — no hype, let the numbers speak [DNA: brand_tone]
SOURCES REFERENCED: DNA products, DNA brand_tone, user brief numbers
VISUAL HINT: Marketing operators at work — dashboards, cross-channel data, executive conversations
CONSTRAINTS: Square 1:1 only; brand primary = orange #FF6B35
USER INPUT QUALITY: specific
ASSUMPTIONS MADE: None
""".strip()

SAMPLE_DNA_BLOB = """
company_name: Spenzo
product_category: AI-powered marketing intelligence and optimization platform
brand_tone: Sharp, analytical, results-driven — data-first, zero hype
brand_values: Precision, measurable ROI, speed of insight
brand_aesthetic: Editorial minimal, lots of white space, large serif headings, single orange accent
colors:
  primary: "#FF6B35"
  palette: ["#FF6B35", "#0A1F3F", "#F5F1EB", "#FFFFFF"]
products:
  spenzo:
    name: Spenzo
    type: saas_platform
    tagline: Precision performance, unlocked
    audience: Marketing leaders, performance marketers, data teams
    integrations: [AWS, Snowflake, Google Ads, Meta Ads, TikTok]
    capabilities:
      - Unified cross-channel data layer
      - MMM-based forecasting (92% model accuracy)
      - Budget optimization and ROAS lift
      - Conversational AI for analytics queries
""".strip()

PRIMARY_COLOR = "#FF6B35"

# Aspect ratio — user selection from Campaign Brief chip flows through here.
# Supported values: 1:1, 4:5, 9:16, 16:9, 3:2, 2:3 (per Gemini 2.5 Flash Image docs)
# Override via env: ASPECT_RATIO=9:16 python 01_run_visualist.py
ASPECT_RATIO = os.getenv("ASPECT_RATIO", "1:1")

# ---------------------------------------------------------------------------
# Visualist prompt v2
# ---------------------------------------------------------------------------

VISUALIST_PROMPT_V2 = f"""
You are an Art Director producing image briefs for Gemini 2.5 Flash Image.

The output feeds a compositor that overlays logo, serif headline, subheading,
and CTA onto the generated background. So the IMAGE must be a clean editorial
photograph with NO text, NO logos, NO UI mockups — those are composited in
PIL afterwards. Brand identity comes from the COMPOSITE LAYER, not the photo.

----- INPUT -----
REFINED BRIEF:
{SAMPLE_REFINED_BRIEF}

BRAND DNA:
{SAMPLE_DNA_BLOB}

BRAND PRIMARY COLOR: {PRIMARY_COLOR}
ASPECT RATIO (user-selected, passed to Gemini via API): {ASPECT_RATIO}

----- STEP 1. CAMPAIGN ANALYSIS -----
Extract:
  • product_name         — the specific product this campaign is about
  • product_category     — what kind of product/service (from DNA, one line)
  • audience             — concrete user archetype (role + industry)
  • industry_context     — the REAL WORKPLACE this audience works in
                           (e.g. performance-marketing war room, agency media
                           desk, in-house growth team pod) — NOT generic "office"
  • campaign_moment      — the SPECIFIC situation the post headline evokes
                           (e.g. "a marketer reviewing cross-channel spend
                           at quarter-end", "a CMO presenting ROAS lift to
                           the leadership team")

----- STEP 2. WRITE OVERLAY COPY -----
Copy must connect directly to the campaign brief's KEY MESSAGE and ANGLE,
NOT be generic. The image will show the `campaign_moment` — the copy names it.

  • headline   — ≤ 7 words, serif-friendly, outcome-oriented. Reads well
                 as a large editorial title (think Wall Street Journal /
                 Monocle cover line). One sentence, one period max.
                 GOOD: "Rising ad spend is outpacing attribution."
                 BAD:  "Expense Reports. No More Side Project." (choppy)
                 BAD:  "Spenzo is the future of marketing." (press release)
  • subheading — ≤ 15 words, one concrete benefit or tension statement
  • cta        — ≤ 4 words ("Book a demo", "See the report", "Learn more")

Brand name is OPTIONAL in headline. Prohibited openers: "[Brand] is…",
"[Brand] helps…", "Introducing…", "Did you know…".

----- STEP 3. WRITE 3 IMAGE PROMPTS -----

IMAGE STYLE (NON-NEGOTIABLE — applies to all 3 prompts):

  • High-quality editorial documentary photography — the aesthetic of a
    professional marketing/finance-magazine feature. Vibrant, warm,
    alive. NOT cinematic, NOT portrait, NOT sterile stock. Think of the
    working environments featured in Fast Company, Harvard Business
    Review, or Bloomberg Businessweek team profiles.
  • EVERYTHING IN SHARP FOCUS. Deep depth of field. Background elements
    (plants, windows, kitchenette, other furniture, charts on walls)
    are AS SHARP as the foreground people. NO bokeh, NO shallow DOF,
    NO background blur. Shot at f/11.
  • ALL FACES VISIBLE AND CLEAR. Every person in the scene has their
    face readable — a mix of front-facing and three-quarter angles is
    natural and good. Natural expressions — focused, thinking,
    discussing, listening. Not posed, not smiling at camera.
  • LIGHTING: Abundant BRIGHT sunlight streaming through multiple
    large windows. Warm golden highlights on wooden surfaces, warm
    key light on the subjects, soft cool fill from the opposite side.
    The room feels SUNLIT and alive — not dim, not flat. Distinct
    but gentle shadows, vibrant exposure.
  • COLOR GRADE: VIBRANT editorial with saturated natural color —
    rich wood tones, distinct clothing colors that jump off the
    frame, clear greens from plants, bright liquids in glasses, warm
    golden ambient. Saturated but realistic — NOT muted, NOT
    desaturated, NOT "corporate grey." One or two {PRIMARY_COLOR}
    accents appear naturally in the scene (a mug, a notebook cover,
    an item of clothing, OR one series on the background chart), but
    do NOT color-wash the whole image orange.
  • SCENE DRESSING — rich and lived-in, NOT sparse. Include ALL of:
      - Warm wooden desk or conference table with visible grain
      - Open laptops each showing the PRODUCT DASHBOARD — a clean
        analytics interface where the ONLY text visible is the
        product's name (the product_name you extracted in Step 1)
        rendered as the dashboard header in a bold clean sans-serif
        font AND in the brand primary color ({PRIMARY_COLOR}) so it
        reads as the brand's own product. Below the header are
        rectangular color cards and simple bar/line chart shapes in
        the brand palette (no readable labels, no numbers on those
        cards/charts).
        When you write each image prompt, you MUST substitute the
        actual product name as a quoted string AND specify the brand
        color, e.g.:
        `each laptop screen displays an analytics dashboard with the
        single word "Spenzo" in a bold sans-serif font rendered in
        {PRIMARY_COLOR} brand color as the dashboard header, and
        below it rectangular color cards and simple bar/line shapes
        with no readable labels`.
        (Replace "Spenzo" with the real product_name from Step 1
        and use the actual brand primary color.)
        This product-name header is the ONLY permitted text in the
        entire image. Every other surface, monitor, sign, whiteboard,
        poster, notebook, button, and label must be completely
        textless and numberless.
      - Large wall-mounted monitor in the background ALSO displays
        the product dashboard — the product name as header (quoted
        string, bold sans-serif, rendered in {PRIMARY_COLOR} brand
        color, top-left of monitor) with a clean bar/line/area chart
        below (shapes only)
      - A plate with croissants or pastries on the table
      - Multiple glasses of drinks on the table: one fresh orange
        juice, one green matcha or juice, one clear water glass
      - At least three espresso or coffee cups in white porcelain
      - A small green potted plant as table centerpiece
      - A notebook and pen, a few sheets of paper or a magazine
    Architectural dressing:
      - White brick or subway-tile wall OR warm wood-panelled wall
        on one side
      - Large leafy tropical plants in ceramic pots on BOTH sides
        of the scene
      - Two visible large windows framing bright outdoor daylight
      - A second piece of furniture in frame (a sideboard, kitchen
        counter, or low shelf) adding depth
  • COMPOSITION: People can be distributed naturally across the frame
    (around a table, across a workspace, standing and sitting). Leave
    the TOP-LEFT corner area (~25% of the canvas in the upper-left
    quadrant) visually calm — a plain wall section, a window, or a
    simple background element — so a logo and large serif headline
    can be composited there. The rest of the frame can be rich with
    scene life.
  • SUBJECT: Members of the `audience`, all engaged in the
    `campaign_moment`. Real body language — laptops open and being
    typed on, hands gesturing, one person looking at another while
    speaking. Mid-action, not posed.
  • TEXT RULE — exactly ONE exception: the product name appears as
    the dashboard header on laptop screens and on the background
    monitor. It MUST be written in the prompt as a quoted string
    (e.g. `the word "Spenzo" in a bold sans-serif font`) — this is
    Google's recommended text-rendering technique. Keep it short:
    the product name only, no tagline, no subheader text.
    Everywhere else — whiteboards, signage, posters, notebooks,
    chart axes, button labels, wall art, mugs, clothing — MUST be
    completely textless and numberless.
  • NO sci-fi, NO glowing HUDs, NO particle effects, NO 3D abstract
    overlays.

SHOT: 28mm or 35mm lens, camera at chest height, f/8+ for edge-to-edge
sharpness, full-frame sensor, high resolution. ISO low, clean grain-free.

SCENE TYPE SELECTION — you MUST pick 3 DIFFERENT scene types for
the 3 prompts. Do NOT make all 3 "team meetings" — that is monotonous
and reads like generic stock photography. Pick from this menu
(choose 3 DIFFERENT entries, matched to what fits the campaign):

  A. `team_moment`        — multiple people collaborating (meeting,
                             huddle, cross-functional review)
  B. `single_user_focus`  — ONE person deeply engaged with their work,
                             PwC-editorial portrait style (e.g. a
                             marketer reviewing a dashboard alone, a
                             CMO on a call, a data analyst at a clean
                             workspace). Face clearly visible, one
                             subject, rich environmental context.
  C. `product_workspace`  — workspace scene WITHOUT prominent people
                             (or people only as small peripheral
                             figures): close-up of a clean desk with
                             the product dashboard visible, laptop +
                             notebook + coffee composition, top-down
                             view of an active workstation.
  D. `presenter_moment`   — one person presenting to an audience or
                             to camera — gesturing at a big screen,
                             on stage, mid-sentence confidently.
                             Conveys authority / thought leadership.
  E. `environmental_wide` — wide shot of the industry context where
                             people are small figures in scale — a
                             marketing-agency floor, a retail store
                             at closing, a trading desk at opening,
                             emphasizing the ENVIRONMENT and scale.

Across 3 prompts, assign each scene a different scene_type from the
menu above. STRICT RULES (these are enforced — we check):

  RULE 1 — ONE of the 3 prompts MUST be `product_workspace`. Period.
  This scene has ZERO people in frame. It is the product, on the
  desk, in a rich editorial still-life composition. Example:
  "An open MacBook on a clean warm-wood desk, screen facing camera
  and displaying the Spenzo dashboard with the product name in
  brand-color. Beside it: a white ceramic coffee cup with steam
  rising, a leather notebook with a pen resting on top, a small
  potted succulent, morning light from the left casting long warm
  shadows, a folded Financial Times newspaper, a pair of wireless
  earbuds in an open case. Shot top-down or 3/4 overhead, cinematic
  but editorial, rich color." This image feels like a magazine
  product still-life. No people anywhere in frame. Not even partial
  hands or background figures.

  RULE 2 — the OTHER 2 prompts must be 2 DIFFERENT scene_types from
  the remaining menu: pick 2 of {team_moment, single_user_focus,
  presenter_moment, environmental_wide}.

  RULE 3 — For `single_user_focus`: EXACTLY ONE person visible. Not
  "one person plus 3 teammates." ONE person, full editorial portrait
  of the audience at their workspace. If you write about other people
  in this scene, you are violating the rule.

  RULE 4 — None of the 3 scene_types may repeat.

SCENE VARIATION across the 3 prompts — different scene_type AND
different physical environment AND different dressing details so the
three images look like three DIFFERENT moments, not the same scene
re-shot. You MUST vary the following across the 3 prompts:

  • Environment: pick 3 DIFFERENT setting types — e.g. one "modern
    loft-style analytics war room with exposed-brick wall + industrial
    windows", one "bright open-plan office with white walls + pale
    wooden floor + glass partition", one "minimal boutique conference
    room with matte concrete wall + floor-to-ceiling window + pendant
    lighting". DO NOT re-use the same "white brick + wooden table +
    two windows" description across all 3.
  • Furniture: vary — one rectangular wooden table, one oval light-
    wood table, one standing-height cafe-style shared desk.
  • Scene dressing: keep the "real working moment" feel but vary the
    items — one scene with croissants + OJ + coffee, one scene with
    bowls of fruit + water carafes + notebooks, one scene with just
    laptops + espresso + a sketched whiteboard in soft background.
  • Color accent placement: the {PRIMARY_COLOR} accent MUST appear
    in a DIFFERENT place in each scene (e.g. scene 1 on a laptop
    sticker, scene 2 on a chair cushion, scene 3 on a notebook cover).
  • Lighting direction: vary — one scene lit from left, one from
    right, one with overhead skylight ambient.

For EACH scene you MUST write out individual character + clothing
descriptions so the team looks like real distinct people, not a
generic "stock team." DO NOT reuse clothing/appearance descriptions
across the 3 scenes — every person across all 3 prompts should look
different.

  • Prompt 1 — TWO PEOPLE (pair). Two visibly distinct members of
              the `audience` mid-conversation at a workspace. Describe
              each individually — e.g. "one in a bright striped sweater
              with short dark hair and a beard, the other in a white
              blazer with long straight hair" — two different
              appearances, two different clothing colors. Both faces
              clearly visible, sharp, natural expressions. Workspace
              matches `industry_context`.

  • Prompt 2 — FOUR PEOPLE (quad). Four visibly distinct members
              around a shared working surface. Name each person's
              clothing individually — each in a different, distinct
              color (e.g. one in a checked shirt, one in a white
              blazer, one in a navy button-down, one in a gray blazer).
              Mix of hair styles and facial features so each person
              reads as an individual. All four faces sharp and clearly
              visible, natural mid-action body language, no one posed.

  • Prompt 3 — SIX PEOPLE (team). A team of six in a dynamic working
              moment around a warm wooden conference table. Describe
              each of the six with distinct clothing and appearance —
              for example: "one man in a bright striped sweater
              (red/white/blue) with short blond hair, one man in a
              gray blazer over a white shirt with a short beard, one
              woman in a white blazer with long straight brown hair,
              one woman in a checked shirt with shoulder-length hair,
              one man with thick curly hair and glasses and a beard in
              a blue button-down, one man clean-shaven in a dark suit
              jacket." All six faces visible and sharp, natural
              expressions of focus and discussion. Wider 28mm lens to
              fit everyone with edge-to-edge sharpness at f/11.

Each prompt should follow Google's canonical narrative-paragraph
structure (one flowing paragraph, 4-6 sentences). Order:
  style/shot-type → subject (detailed) → action → environment →
  lighting → grade → composition → explicit "no text / no logos /
  no blur" reminder.

Aspect ratio is {ASPECT_RATIO}, set via API `image_config.aspect_ratio` —
do NOT mention aspect in the prose. But DO compose the scene to fit
this aspect: portrait (9:16, 4:5, 2:3, 3:4) = taller, subject vertical
stack or head-and-shoulders framing. Square (1:1) = balanced. Landscape
(16:9, 3:2, 21:9) = wider framing, more horizontal spread.

----- OUTPUT (STRICT JSON, no markdown fences) -----
{{
  "brand": {{
    "primary_color": "{PRIMARY_COLOR}",
    "neutral_color": "#F5F1EB",
    "aspect_ratio": "{ASPECT_RATIO}"
  }},
  "campaign_analysis": {{
    "product_name": "...",
    "product_category": "...",
    "audience": "...",
    "industry_context": "...",
    "campaign_moment": "..."
  }},
  "overlay_copy": {{
    "headline": "...",
    "subheading": "...",
    "cta": "..."
  }},
  "image_prompts": [
    {{ "scene_type": "<one of team_moment|single_user_focus|product_workspace|presenter_moment|environmental_wide>", "prompt": "<Prompt 1 paragraph>" }},
    {{ "scene_type": "<different from above>", "prompt": "<Prompt 2 paragraph>" }},
    {{ "scene_type": "<different from both above>", "prompt": "<Prompt 3 paragraph>" }}
  ]
}}

IMPORTANT: Echo the `brand.primary_color` value as given above — this is
the brand's DNA color and flows through to the compositor. Do NOT invent
a different color.
""".strip()


def run_visualist() -> dict:
    print("Calling Gemini 2.5 Flash (Visualist v2 draft)...\n")
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[VISUALIST_PROMPT_V2],
    )
    text = (resp.text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lower().startswith("json"):
            text = text[4:].lstrip("\n")
        text = text.rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print("JSON parse failed. Raw text was:\n")
        print(text[:3000])
        raise SystemExit(f"Parse error: {e}")


if __name__ == "__main__":
    out_dir = Path(__file__).parent / "out"
    out_dir.mkdir(exist_ok=True)
    data = run_visualist()

    (out_dir / "visualist_output.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )

    print("=" * 72)
    print("CAMPAIGN ANALYSIS")
    print("=" * 72)
    for k, v in data.get("campaign_analysis", {}).items():
        print(f"  {k:18s} : {v}")

    print()
    print("=" * 72)
    print("OVERLAY COPY")
    print("=" * 72)
    for k, v in data.get("overlay_copy", {}).items():
        print(f"  {k:12s} : {v}")

    print()
    print("=" * 72)
    print("3 IMAGE PROMPTS")
    print("=" * 72)
    for i, p in enumerate(data.get("image_prompts", []), 1):
        if isinstance(p, dict):
            scene = p.get("scene_type", "?")
            text = p.get("prompt", "")
        else:
            scene = "(legacy-string)"
            text = p
        print(f"\n--- Prompt {i}  [scene_type: {scene}] ---")
        print(text)

    print("\nFull JSON saved to:", out_dir / "visualist_output.json")
