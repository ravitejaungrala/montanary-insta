# Agent Post — Agent Reference

Single source of truth for every agent in the Agent Post pipeline. Update this file whenever an agent's prompt, model, or contract changes.

**Entry point:** `services/ai_service.py :: generate_strategic_content()` (line 1154)

**Execution order:**

```
User brief + DNA + docs
   │
   ▼
[1] Refine Brief  ──────────►  structured strategic brief (string)
   │
   ▼
[2] Research     ──────────►  audience, trend, problem, product analysis (JSON)
   │
   ▼
[3] Content      ──────────►  3 variants × N platforms + recommendation (JSON)
   │
   ▼
[4] Visualist    ──────────►  3 image concepts with narrative devices (JSON array)
   │
   ▼
[5] Image Pipeline (Gemini-Image + PIL)
      │
      ▼   [5a] _gen_single_variant  ──►  raw background image bytes
      │
      ▼   [5b] Visual Critic        ──►  rating 0-10, retry up to 3× if <7
      │
      ▼   [5c] PIL Compositor       ──►  final composited JPEG → S3 URL
   │
   ▼
[6] Strategic Critic  ──────►  final payload audit (is_valid, critique, adjustments)
```

**Legend for status column:**
- 🟢 **Current** — last reviewed and accepted as-is
- 🟠 **Upgraded** — has been rewritten/improved recently (see date)
- 🔴 **Pending** — flagged for rework; do not assume current implementation is final

| # | Agent | File:line | Model | Status |
|---|---|---|---|---|
| 1 | Refine Brief      | `ai_service.py:132`  | gemini-2.5-flash       | 🟠 Upgraded April 2026 |
| 2 | Research          | `ai_service.py:265`  | gemini-2.5-flash       | 🟠 Upgraded April 2026 |
| 3 | Content           | `ai_service.py:345`  | gemini-2.5-flash       | 🟠 Upgraded April 2026 |
| 4 | LinkedIn Visualist| `ai_service.py:367`  | gemini-2.5-flash       | 🟢 Original |
| 5a| Image Generator   | `ai_service.py:836`  | gemini-2.5-flash-image | 🟢 Original |
| 5b| Visual Critic     | `ai_service.py:544`  | gemini-2.5-flash (multimodal) | 🟢 Original |
| 5c| PIL Compositor    | `services/visual_compositor.py` | (pure PIL, no LLM) | 🟢 Original |
| 6 | Strategic Critic  | `ai_service.py:599`  | gemini-2.5-flash       | 🟢 Original |
| — | Planner (campaign-mode) | `ai_service.py:755` | gemini-2.5-flash | 🟢 Original — only used by multi-day scheduler |

---

## 1. Refine Brief Agent 🟠

**Function:** `_refine_brief_agent(campaign_brief, user_context="")`
**File:line:** [ai_service.py:132](../apps/backend/services/ai_service.py)
**Model:** `gemini-2.5-flash` (default temperature)
**Status:** Upgraded April 2026

### Why this agent exists
The raw input from the user can be anything from a one-liner ("Announce Spenzo") to a full professional brief. Downstream agents (Research / Content / Visualist) can't execute reliably on a vague topic. This agent's job is to produce a **structured strategic brief document** that every downstream agent can execute against without guessing what the user meant.

### Input
| Parameter | Type | Example |
|---|---|---|
| `campaign_brief` | string | "Announce Spenzo's marketplace launch" |
| `user_context` | string | Labeled-sections text of the selected Business DNA + any uploaded docs. Built by `_build_user_context()`. Includes tagline, brand_values, brand_tone, brand_aesthetic, fonts, colors, overview, document contents. |

### Output
```json
{
  "refined_brief": "<structured multi-section text block>"
}
```

The `refined_brief` string contains these labeled sections in order:
`TOPIC` · `AUDIENCE` · `KEY MESSAGE` · `ANGLE` · `SUPPORTING POINTS` (each with `[source]` tag) · `TONE` · `SOURCES REFERENCED` · `VISUAL HINT` · `CONSTRAINTS` · `USER INPUT QUALITY` (`empty`/`lean`/`specific`/`professional`) · `ASSUMPTIONS MADE`.

### Prompt in ~80 words
> "You are a Senior Social Media Growth Strategist. The user provided brief is classified as [empty|lean|specific|professional]. Your job is to emit a STRATEGIC BRIEF DOCUMENT that downstream agents can execute against. **Gap-filling rule**: if the brief is lean or empty, use the DNA + documents to aggressively fill the audience, angle, supporting points, tone, and visual direction. Never refuse. **Source-grounding rule**: every SUPPORTING POINT must cite its source (`[user brief]`, `[DNA: field]`, or `[doc: filename]`). **Anti-hallucination**: do not invent metrics, percentages, or partner counts unless explicitly sourced. Tag every assumption under ASSUMPTIONS MADE."

The prompt does NOT enforce hook protocol or banned-phrases — those are the content agent's responsibility. The refiner produces strategy, not prose.

### Behavior by input quality
| Input word count | Quality tag | Agent behavior |
|---|---|---|
| 0 | `empty` | Pulls topic, audience, angle entirely from DNA + brand_values |
| 1-19 | `lean` | Treats brief as topic-only seed; aggressively fills from DNA |
| 20-49 | `specific` | Respects user specifics; fills only blanks |
| 50+ | `professional` | Minimal assumptions; preserves every concrete detail |

### Tested outcomes (April 2026 smoke test, 17/17 assertions passed)
- Lean `"Announce Spenzo's marketplace launch"` → 2.4KB structured brief, every point sourced
- Professional 60-word brief with literal specifics (`May 1`, `40+ connectors`, `Coupa/Ariba/Salesforce/NetSuite`) → all specifics preserved

---

## 2. Research Agent 🟠

**Function:** `_research_agent(refined_brief, user_context="")`
**File:line:** [ai_service.py:265](../apps/backend/services/ai_service.py)
**Model:** `gemini-2.5-flash`
**Status:** Upgraded April 2026

### Why this agent exists
The refiner produces a structured brief. The content agent writes platform variants. This agent sits between them with ONE job: **protect the content agent from hallucinating** and sharpen the 3 angles it will A/B test.

It deliberately does NOT re-derive audience / tone / angle generically — the refiner already did that, source-tagged. Research sharpens (picks the 1-2 audience segments most likely to engage), guards (emits a `do_not_claim` list and a `grounding_confidence` label), and supplies 3 distinct angles for the content variants.

### Input
| Parameter | Type | Notes |
|---|---|---|
| `refined_brief` | string | Structured output of agent 1 (already source-tagged) |
| `user_context` | string | Same DNA + docs blob |

### Output
```json
{
  "target_audience":             "1-2 concrete segments, each source-tagged",
  "trending_context":            "Qualitative observation, [speculative] unless grounded",
  "problem_solving_opportunity": "Specific friction, tagged",
  "company_product_analysis":    "ONE edge worth leading with, tagged",
  "angles_to_test":              ["angle 1", "angle 2", "angle 3"],
  "do_not_claim":                ["forbidden claim 1", "..."],
  "grounding_confidence":        "grounded | partial | speculative"
}
```

Four legacy keys preserved so `research_report` in the API response stays backward-compatible. Three new keys (`angles_to_test`, `do_not_claim`, `grounding_confidence`) are consumed by the content agent's new **§0 RESEARCH GUARDRAILS** section.

### Source-tagging contract
Every factual sentence ends with exactly one tag: `[user brief]`, `[DNA: field]`, `[doc: filename]`, `[speculative]`, or `[inference]`. Numbers/percentages/dollar figures are ONLY allowed under `[user brief]`, `[DNA]`, or `[doc]` — never `[speculative]` or `[inference]`. This is the main anti-hallucination lever.

### Prompt in ~80 words
> "You are a Research Analyst feeding a downstream Content Agent. Do NOT re-summarize the refiner. (a) Sharpen angles, (b) protect downstream agents from hallucination, (c) pick the single product edge worth leading with. You have NO web access — tag any unsourced claim `[speculative]`. Never emit numbers with `[speculative]` or `[inference]`. Produce 3 distinct testable angles. Produce a `do_not_claim` list of up to 5 things the content agent must NOT say. Label overall grounding as grounded / partial / speculative."

### How the content agent consumes it
- `do_not_claim` → banned-phrase list for this specific post
- `grounding_confidence == "speculative"` → content agent forbidden from inventing numbers in the "follower_growth" proof-stack variant (must use qualitative proof instead)
- `angles_to_test` → each of the 3 content variants maps to a distinct angle

### Known smells
- Gemini-2.5-flash has no web tool, so `trending_context` is almost always `[speculative]`. For real grounding, wire a web-search tool (Jina Reader / Tavily) in a future upgrade — this is explicitly flagged as a follow-up, not a blocker.

---

## 3. Content Agent 🟠

**Function:** `_content_agent(refined_brief, research, platforms, user_context="")`
**File:line:** [ai_service.py:345](../apps/backend/services/ai_service.py)
**Model:** `gemini-2.5-flash`
**Status:** Upgraded April 2026

### Why this agent exists
Writes the actual post copy. Each post is a SaaS customer's sales / marketing asset — the agent's job is not "write nicely" but **maximize a specific engagement metric** per variant: reach, comments, or follows. The 3 variants target three different algorithm signals so the user can A/B by metric goal.

### Input
| Parameter | Type | Notes |
|---|---|---|
| `refined_brief` | string | Output of agent 1 |
| `research` | dict | Output of agent 2 |
| `platforms` | list of strings | e.g. `["linkedin", "twitter", "instagram", "facebook"]` |
| `user_context` | string | DNA + docs |

### Output
```json
{
  "recommendation": {
    "best_variant": "viral_reach | high_interaction | follower_growth",
    "reason": "..."
  },
  "content": {
    "linkedin":  { "viral_reach": "...", "high_interaction": "...", "follower_growth": "..." },
    "twitter":   { "viral_reach": "...", "high_interaction": "...", "follower_growth": "..." },
    "instagram": { "viral_reach": "...", "high_interaction": "...", "follower_growth": "..." },
    "facebook":  { "viral_reach": "...", "high_interaction": "...", "follower_growth": "..." }
  }
}
```

### Metric-targeted variants (core of the rewrite)

| Variant | Primary metric | Algo signal it must trigger | Tactic |
|---|---|---|---|
| `viral_reach` | Reach + Brand visibility | Shares / quote-tweets / retweets | Contrarian claim + specific counter-example. Share-with-comment worthy. |
| `high_interaction` | Comments + Engagement | Reply threads longer than one emoji | Open either/or question on a decision the audience is making THIS week. Demands a specific answer (letter, number, named choice). |
| `follower_growth` | Followers + Likes | Profile-click → follow | "I/we figured out X" authority post. Numeric proof when `grounding_confidence != speculative`; NAMED-SCENARIO proof otherwise. |

Each variant **must** map to a distinct entry in `research.angles_to_test` (enforced in §0 of the prompt).

### Post-processing — `_apply_content_post_processing`

Three server-side guarantees, applied to every variant after the LLM call:

1. **Unicode-style strip** — `𝗔𝗕𝗖` → `ABC` via NFKD normalization (a11y + SEO).
2. **Hashtag cap** — per-platform trim (Twitter 1, LinkedIn 5, Facebook 3, Instagram 12). Preserves earliest-seen ordering.
3. **Character cap — NEW April 2026** — hard truncation at the platform limit (Twitter 240, LinkedIn 1500, Facebook 1200, Instagram 2200). Preserves trailing hashtag block when possible; cuts the body at the last word boundary with `…` ellipsis. Guarantees the output fits the platform's API limit regardless of what Gemini emitted. See `_enforce_char_cap` + `_truncate_at_word`.

### Prompt sections (9 blocks)
0. **RESEARCH GUARDRAILS** — honor `do_not_claim`; no invented numbers when `grounding_confidence=="speculative"` (use named-scenario proof); each of the 3 variants maps to a distinct `angles_to_test` entry.
1. **HOOK PROTOCOL** — first 5 words must be pattern-interrupt. Forbidden openers include `"[Brand] is…"` to avoid press-release voice.
2. **BANNED CORPORATE PHRASES** — 35-entry list (prompt-level only; not server-enforced).
3. **PLATFORM CHAR LIMITS** — Twitter ≤240, LinkedIn ≤1500, IG ≤2200, FB ≤1200. Explicitly states: "server enforces too — rewrite Twitter variants over 240 or lose the ending."
4. **FORMATTING RULES** — plain text, arrow bullets on LI/FB, no emojis on Twitter.
5. **3 METRIC-TARGETED VARIANTS** (see table above). Grounded vs. speculative mode for `follower_growth` is explicit.
6. **HASHTAG POLICY** — Twitter 0-1, LinkedIn 3-5, Facebook 1-3, Instagram 8-12.
7. **CTA → METRIC MAPPING** — share-driving CTA on viral_reach; comment-driving on high_interaction; follow+save on follower_growth. "Never cross-wire."
8. **BRAND VOICE — SOFT SIGNATURE** — match DNA `brand_tone` / `brand_values`; **brand name NOT forced** (multi-tenant, different customers have different products); brand visibility comes from consistent voice + signature format, not name-stuffing. Prohibits `"[Brand] is…"` / `"[Brand] helps…"` openers.

### Known smells
- Banned phrases still prompt-only (no server-side substring match). Candidate for future post-process.
- `recommendation.best_variant` is chosen by the same model in the same call — an independent selector pass would be more objective.
- Variant archetypes still hardcoded; not DNA-configurable yet.

---

## 4. LinkedIn Visualist Agent 🟢

**Function:** `_linkedin_visualist_agent(linkedin_content, refined_brief, primary_color, domain_name)`
**File:line:** [ai_service.py:367](../apps/backend/services/ai_service.py)
**Model:** `gemini-2.5-flash`
**Status:** Original

### Why this agent exists
Converts the winning LinkedIn copy into **3 image concepts** with narrative devices, heading/sub-heading text, text-zone coordinates, and CTA placement — enough metadata for the PIL compositor to place everything deterministically. The Gemini Image model will later render pure-background versions using the `generation_prompt` field.

### Input
| Parameter | Type | Notes |
|---|---|---|
| `linkedin_content` | string | Best LinkedIn variant text (picked by `generate_strategic_content`) |
| `refined_brief` | string | Output of agent 1 |
| `primary_color` | string | Hex color from active DNA (e.g. `"#FF6B2E"`) |
| `domain_name` | string | e.g. `"spenzo.io"` — used on the CTA pill |

### Output
Array of exactly 3 objects:
```json
[
  {
    "campaign_anchor":    "Concrete visual subject (same across all 3)",
    "narrative_device":   "BEFORE/AFTER SPLIT | MOMENT OF USE | TRANSFORMATION ARROW | EDITORIAL HERO | METAPHORICAL STILL-LIFE | INTEGRATION FLOW",
    "name":               "Concept name",
    "heading":            "4-word outcome hook",
    "sub_heading":        "6-word benefit statement",
    "highlight_words":    ["Word1", "Word2"],
    "text_zone":          "TOP_LEFT_PANEL | TOP_RIGHT_PANEL | TOP_CENTER_BAND | LEFT_GLASS_CARD",
    "cta_corner":         "BOTTOM_LEFT | BOTTOM_RIGHT",
    "logo_corner":        "TOP_LEFT | TOP_RIGHT",
    "generation_prompt":  "Pure-background Gemini image directive (scene only, NO text, NO UI)"
  },
  { ... }, { ... }
]
```

### Prompt in ~90 words
> "You are a LinkedIn visualist. Produce 3 image concepts for this campaign. Pick a **single `campaign_anchor`** (concrete visual subject) and reuse it across all 3 — only the `narrative_device` varies. Six narrative devices available: BEFORE/AFTER SPLIT, MOMENT OF USE, TRANSFORMATION ARROW, EDITORIAL HERO, METAPHORICAL STILL-LIFE, INTEGRATION FLOW. **RESERVED ZONES**: top 30% must stay pure background (for overlay text); bottom corners must stay clean (CTA pill). **NO TEXT inside the image** — all text is added by PIL after Gemini renders the scene."

### Known smells
- Only LinkedIn copy seeds visuals — the same images ship to Twitter/Instagram/Facebook even though those platforms have different aspect ratios and visual conventions. Flagged in audit as **bug C-2**.

---

## 5. Image Generation Pipeline

The image pipeline is **two-pass**: a Gemini image model renders a pure-background scene, then a Python/PIL compositor places the logo, heading, sub-heading, and CTA pill on top. This eliminates the "Gemini can't render readable typography" failure mode.

### 5a. Image Generator 🟢

**Function:** `_gen_single_variant(index, visual_concepts, logo_bytes, primary_color, raw_prompt, domain_name)`
**File:line:** [ai_service.py:836](../apps/backend/services/ai_service.py)
**Model:** `gemini-2.5-flash-image`
**Status:** Original

**Purpose:** Render ONE variant's background image, given the concept's `generation_prompt`.

**Input:** concept dict from agent 4, brand `primary_color`, `domain_name`, optional `logo_bytes` (ignored at this step — compositor uses it).

**Output:** raw image bytes (JPEG/PNG, pre-overlay).

**Prompt prefix (prepended before the concept's generation_prompt):**
> "PURE BACKGROUND ONLY. Do not include any logo, any text, any CTA button, or any pill-shaped element. Leave all 4 corners clean (solid background). Place all creative composition (people, objects, scene) in the MIDDLE 70% of the canvas (y=30% to y=88%)."

**Output spec:** STRICT SQUARE 1:1 at 1024×1024 (enforced by the compositor — aspect-ratio support is planned but not wired yet).

**Retry behavior:** called inside a loop by `generate_visual_variants`; retries up to 3× if the critic rates <7.

---

### 5b. Visual Critic 🟢

**Function:** `_visual_critic_agent(image_bytes, original_prompt, refined_brief, primary_color)`
**File:line:** [ai_service.py:544](../apps/backend/services/ai_service.py)
**Model:** `gemini-2.5-flash` (multimodal — takes image input)
**Status:** Original

**Purpose:** Quality-gate each raw background image BEFORE the compositor runs. If the image fails, the generator retries with improvement advice. Max 3 attempts per variant.

**Input:** image bytes + the prompt that generated it + refined brief + primary color.

**Output:**
```json
{
  "rating_out_of_10":    7,
  "reason":              "Short audit summary",
  "improvement_advice":  "Specific visual fix to try on the next attempt",
  "is_valid":            true
}
```

**Audit checklist (what it scores):**
- **Campaign anchor fidelity** — does the image unmistakably depict the subject?
- **Text zone respect** — is the top 30% clear for overlay?
- **No hallucinated text** — are there NO gibberish labels inside dashboards/UIs?
- **Brand colors** — does the primary hex appear somewhere?
- **Sharpness** — is the scene sharp and in focus?

**Does NOT audit:** headline typos / CTA text / logo placement — the compositor owns those.

**Pass threshold:** rating ≥ 7. Below 7 → retry with `improvement_advice` appended to the generation_prompt.

---

### 5c. PIL Compositor 🟢

**File:** [visual_compositor.py](../apps/backend/services/visual_compositor.py)
**Model:** none — pure Python + Pillow
**Status:** Original

**Purpose:** Deterministically place heading, sub-heading, logo, and CTA pill on top of the raw background. Solves the "AI-rendered typography is gibberish" problem.

**Input:**
- background image bytes
- heading text
- sub-heading text
- logo bytes (optional)
- primary_color hex
- text_zone name (TOP_LEFT_PANEL / TOP_RIGHT_PANEL / TOP_CENTER_BAND / LEFT_GLASS_CARD)
- cta_corner (BOTTOM_LEFT / BOTTOM_RIGHT)
- logo_corner (TOP_LEFT / TOP_RIGHT)
- domain_name (for the CTA pill text)

**Output:** composited JPEG bytes → uploaded to S3 → URL returned.

**Renders:**
- Frosted glass panel behind the text block for readability on any background
- Heading line 1 in `primary_color`, line 2 in white, with drop shadows
- Sub-heading in light gray
- Logo padded to 1:1 square, top-left or top-right corner
- Rounded CTA pill with domain name in the opposite bottom corner

---

## 6. Strategic Critic Agent 🟢

**Function:** `_critic_agent(final_payload, campaign_brief, user_context="")`
**File:line:** [ai_service.py:599](../apps/backend/services/ai_service.py)
**Model:** `gemini-2.5-flash`
**Status:** Original — **review candidate** (flagged in architecture discussion as "produces data that never reaches the UI")

### Why this agent exists
Final verification pass over the complete assembled payload (refined brief + research + content + visuals). Checks strategic fitness — is every hook sharp, are hashtags within caps, are CTAs reply-bait not share-beg, are the 3 variants structurally distinct, are the 3 visuals distinct in layout.

### Input
| Parameter | Type |
|---|---|
| `final_payload` | dict — the combined output of agents 1-5 |
| `campaign_brief` | string — original user brief |
| `user_context` | string — DNA + docs |

### Output
```json
{
  "is_valid":   true,
  "critique":   "Overall strategic assessment",
  "adjustments":"Specific fixes if anything failed"
}
```

### Open questions (from the architecture discussion)
- The `critique` and `adjustments` fields are currently **not surfaced in the UI**. Either wire them into the review pane as user-facing feedback, or remove the agent to save latency + cost. This is on the backlog — no decision yet.

---

## Orchestrator — `generate_strategic_content` 🟢

**File:line:** [ai_service.py:1154](../apps/backend/services/ai_service.py)
**Signature:** `generate_strategic_content(campaign_brief, platforms, user=None, logo_bytes=None, extra_context="", product_name=None)`

Runs all 6 agents in sequence. Picks the best LinkedIn variant for the Visualist. Calls `generate_visual_variants` to run image gen + critic in a retry loop. Assembles the final payload:

```json
{
  "refined_brief":   "<string>",
  "research_report": { ... },
  "recommendation":  { ... },
  "content":         { ...platforms... },
  "visuals":         [ {url, name, rating_out_of_10, ...} × 3 ],
  "verification":    { is_valid, critique, adjustments }
}
```

---

## How to update this file

When redesigning an agent:
1. Update the **status** emoji and date on the table at top + on the agent's section
2. Rewrite the **Prompt in ~N words** summary — keep it short; the source of truth for the full prompt is the code
3. If the I/O contract changed, update **Input** and **Output** tables
4. Add a **Tested outcomes** block if you wrote a smoke test
5. List any **Known smells / open questions** that remained after the change

When adding a new agent:
1. Add a row to the top table with correct status emoji
2. Insert a new section following the same template (Why / Input / Output / Prompt summary / Known smells)
3. Update the execution-order diagram at the top of this file

When removing an agent:
1. Mark it 🔴 in the top table with the reason
2. Cross-out the section but keep it for historical reference — don't delete
