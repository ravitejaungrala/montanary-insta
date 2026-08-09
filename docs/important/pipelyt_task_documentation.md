# Pipelyt — Task Documentation

> **Purpose.** This document explains the end-to-end engineering process for the AI features built into Pipelyt, for stakeholders who need to understand how a user's request is processed, validated, and turned into a published social-media post.
> **Audience.** Engineering management, product, and adjacent functions.
> **Scope of this revision.** Section 1 — **Text Content Generation Pipeline**. Further sections (Image Generation, Publishing, Analytics, Scheduling) will be added in subsequent revisions.

---

# Section 1 — Text Content Generation Pipeline

## 1.1 Overview

Pipelyt converts a single user "campaign brief" written in plain language into platform-ready social-media copy for LinkedIn, X (Twitter), Facebook, and Instagram. Each platform receives between three and four distinct caption variants — *Reach*, *Engagement*, *Brand*, and (when applicable) *Festival* — that the user can review, edit, and schedule.

The system is multi-stage. A brief travels through a rule-based validation tier, a semantic interpretation tier, a research tier, a copywriting tier, a quality-audit tier, and a post-processing tier before it is returned to the user. Each tier has its own model, temperature, and guardrails.

```
┌──────────────────────────────────────────────────────────────────────┐
│   USER INPUT — campaign brief + platform selection + Business DNA     │
└─────────────────────────────────┬────────────────────────────────────┘
                                  │
              ┌───────────────────┴───────────────────┐
              │  STAGE 1 — Brief Guard (rule-based)    │  rejects in < 50 ms
              │  banned terms · length · density       │  no AI cost
              │  generic-placeholder patterns          │
              └───────────────────┬───────────────────┘
                                  │ (pass)
              ┌───────────────────┴───────────────────┐
              │  STAGE 2 — Business DNA Resolution     │
              │  Admin · Team-member inheritance       │
              │  "None" sentinel · product scoping     │
              └───────────────────┬───────────────────┘
                                  │
              ┌───────────────────┴───────────────────┐
              │  STAGE 3 — Refiner Agent               │  gemini-flash-lite-latest
              │  semantic validation + structuring     │  temperature 0.3
              │  produces "STRATEGIC BRIEF"            │
              └───────────────────┬───────────────────┘
                                  │ (pass)
              ┌───────────────────┴───────────────────┐
              │  STAGE 4 — Cultural Calendar           │  cached daily
              │  (today + tomorrow festivals)          │  web-grounded
              └───────────────────┬───────────────────┘
                                  │
              ┌───────────────────┴───────────────────┐
              │  STAGE 5 — Researcher Agent             │  gemini-flash-lite-latest
              │  google_search grounded                │  temperature 0.2
              │  trends + angles + referenced URLs     │
              └───────────────────┬───────────────────┘
                                  │
              ┌───────────────────┴───────────────────┐
              │  STAGE 6 — Content Agent (copywriter)  │  gemini-flash-lite-latest
              │  4 variants × 4 platforms              │  temperature 0.7
              │  Hard Rules R1–R9                      │
              └───────────────────┬───────────────────┘
                                  │
              ┌───────────────────┴───────────────────┐
              │  STAGE 7 — Critic Agent                │  gemini-flash-lite-latest
              │  audits the assembled payload          │  temperature 0.3
              └───────────────────┬───────────────────┘
                                  │
              ┌───────────────────┴───────────────────┐
              │  STAGE 8 — Post-Processing             │  deterministic
              │  placeholder-URL strip                 │
              │  hashtag cap · char cap                │
              └───────────────────┬───────────────────┘
                                  │
┌─────────────────────────────────┴────────────────────────────────────┐
│   OUTPUT — 16 platform variants returned to the frontend             │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 1.2 Stage 1 — Brief Guard (rule-based, pre-AI)

**File:** `apps/backend/services/brief_guard.py`
**Cost:** zero (no model call)
**Latency:** < 50 ms

Before any AI cost is incurred, the brief is checked against four cheap rule-based filters. Failure produces an HTTP 422 response carrying a user-facing message the frontend displays inline beneath the brief textarea (not as a toast).

### 1.2.1 Empty / length filter

| Threshold | Value |
|---|---|
| Minimum characters | 15 |
| Minimum words | 4 |

If the brief contains fewer than 4 words or 15 characters, it is rejected with:

> *"Your brief is too short (N word). Please write at least 4 words describing what you want to promote, who it's for, and why it matters. Example: 'Launch our new Q2 pricing with a focus on mid-market teams — emphasize the 40% time savings.'"*

### 1.2.2 Policy filter — banned terms

The brief is matched (case-insensitive, word-boundary-aware) against a curated list of approximately 70 banned terms grouped into these categories:

| Category | Examples |
|---|---|
| Sexual / adult | `porn`, `nude`, `escort`, `nsfw`, `onlyfans`, etc. |
| Violence / harm | `bomb`, `murder`, `suicide`, `terrorist`, `genocide`, etc. |
| Drugs / illegal | `cocaine`, `heroin`, `cartel`, `illegal drugs`, etc. |
| Hate speech | `nazi`, `kkk`, `white supremacy` |
| Scams / fraud | `pyramid scheme`, `ponzi`, `money laundering`, `phishing` |
| Weapons | `automatic weapon`, `machine gun`, `silencer` |

A match triggers immediate rejection:

> *"We can't generate marketing content around this topic. Your brief contained language our policy doesn't support — please rewrite with a business-appropriate focus (e.g. a product, service, offer, or announcement relevant to your audience)."*

This is deliberately conservative — false positives are preferred over false negatives because the reputational cost of generating banned content far outweighs the friction of asking the user to rephrase.

### 1.2.3 Alpha-density filter

If alphabetical characters make up less than 45 % of the brief (typically because the brief is dominated by emoji, symbols, or numbers), the brief is rejected:

> *"Your brief has too few real words. Please describe the campaign in plain English — what you're promoting, who it's aimed at, and what the key benefit is."*

### 1.2.4 Generic placeholder filter

The brief is matched against a small set of regex patterns that detect placeholder commands rather than real briefs. Examples that fail:

| Brief text | Detected as |
|---|---|
| `Create a post` | Generic command |
| `Generate an ad` | Generic command |
| `make content` | Generic command |
| `test` / `hello` / `lorem ipsum` | Stub input |
| `create spenzo` | One-word "generate" command |
| `please help` | Lazy prompt |

Rejection message:

> *"Your brief is too generic. Please describe the actual campaign — what you're promoting, the audience, and the key message — instead of just a command. Example: 'Announce our new AI pricing insights feature to mid-market SaaS marketing leaders, highlighting how it replaces their weekly forecast meetings.'"*

If Stage 1 passes, the pipeline proceeds to AI agents.

---

## 1.3 Stage 2 — Business DNA Resolution

**File:** `apps/backend/routers/content.py`

Business DNA is the brand-knowledge object Pipelyt stores per user. It includes brand voice, tone, values, products, colours, logo URL, and resource URLs (website, product page, careers, social links, etc.). The DNA selection chip on the frontend determines what brand context flows into the downstream agents.

### 1.3.1 Three selection modes

| Selection | Behaviour |
|---|---|
| **Specific brand or product** (default) | The selected product's DNA is merged on top of the company DNA. Downstream agents see voice + values + product positioning. |
| **Team member inheritance** | If the user is a team member (not an admin), the system silently clamps to the admin's DNA. Members cannot bypass brand context. |
| **None** (admin-only sentinel `__none__`) | A deliberate opt-out. The pipeline runs with **zero brand context** — agents see only the brief and any uploaded session documents. Logo is also skipped. |

### 1.3.2 With DNA attached — what flows through

The `_build_user_context` helper produces a structured block containing:

- Entity name + target domain + tagline
- Brand values, brand tone, brand aesthetic
- Fonts and colours (as a JSON dict)
- Overview paragraph
- **Resource URLs** — `website_url`, `product_url`, `pricing_url`, `docs_url`, `blog_url`, `careers_url`, `demo_url`, `signup_url`, `trial_url`, `case_studies_url`, all `social_links.*`
- Any documents the admin uploaded against this DNA (full text)

This is the `user_context` string referenced by every downstream prompt.

### 1.3.3 Without DNA — what changes

When the `None` sentinel fires, `user_context` becomes the literal string:

> *"(no brand knowledge attached — work from brief only)"*

Downstream agents are explicitly instructed to skip DNA references and produce post copy from the brief alone — useful for cross-topic posts (industry trends, public news, generic thought leadership).

---

## 1.4 Stage 3 — Refiner Agent

**File:** `apps/backend/services/ai_service.py::_refine_brief_agent`
**Model:** `gemini-flash-lite-latest`
**Temperature:** 0.7 (default — set by `_call_agent` helper, not overridden)
**Approximate latency:** 5–12 s

**Full hyperparameter set:**

| Hyperparameter | Value | Source |
|---|---|---|
| `model` | `gemini-flash-lite-latest` | `_call_agent` default |
| `temperature` | `0.7` | `_call_agent` default (refiner does not override) |
| `top_p` | `0.95` | explicit (locked to Gemini default for auditability) |
| `top_k` | `40` | explicit (locked to Gemini default for auditability) |
| `max_output_tokens` | not set — model-specific limit | API default |
| `candidate_count` | `1` | API default |
| `safety_settings` | not set — Gemini default `BLOCK_MEDIUM_AND_ABOVE` for each category | API default |
| `tools` | `None` | No web grounding |
| `response_mime_type` | not set | API default |
| `system_instruction` | not set | All instructions in prompt |
| `thinking_config` | not set | API default |

### 1.4.1 Purpose

The refiner converts a raw, unstructured brief into a labelled **STRATEGIC BRIEF** document that downstream agents can execute against without guessing. It also performs the second tier of brief validation — a semantic check that catches harmful intent paraphrased past the keyword list, or substantive-looking-but-meaningless briefs that the rule-based guard cannot detect.

### 1.4.2 Output sections

The refiner produces a labelled string with these sections:

| Section | Purpose |
|---|---|
| USER GOAL | What the user is trying to achieve |
| TOPIC | The campaign subject |
| AUDIENCE | Who the post is for |
| KEY MESSAGE | The single most important sentence |
| ANGLE | The narrative direction |
| SUPPORTING POINTS | Up to 5 factual points, each tagged with source (DNA / doc / brief) |
| TONE | Voice directives |
| SOURCES REFERENCED | Where each field came from |
| **USER LINKS** | Every URL the user typed in the raw brief, captured verbatim |
| VISUAL HINT | One-sentence scene description for the image agent |
| CONSTRAINTS | Any restrictions from the brief |
| USER INPUT QUALITY | "empty" / "lean" / "specific" / "professional" |
| ASSUMPTIONS MADE | Any inferences made when the brief was sparse |

### 1.4.3 Semantic rejection (`BriefRejected`)

If the refiner detects a harmful, off-brand, or substantively empty brief, it raises the `BriefRejected` exception with a category and a user-facing message. The router catches this and returns HTTP 422. Categories:

| Category | Trigger |
|---|---|
| `harmful` | Harmful intent paraphrased past Stage 1's keyword list |
| `generic` | Real-looking brief that nonetheless describes no concrete campaign |
| `no_utility` | Brief that does not describe content any business would plausibly market |
| `invalid` | Catch-all for other semantic failures |

---

## 1.5 Stage 4 — Cultural Calendar

**File:** `apps/backend/services/ai_service.py::_get_cultural_calendar`
**Model:** `gemini-flash-lite-latest` with `google_search` tool
**Temperature:** 0.2 (low — calendar is factual lookup, not creative)
**Caching:** day-level cache (one live web search per day across all users)
**Approximate latency on cold cache:** 3–5 s

**Full hyperparameter set:**

| Hyperparameter | Value | Source |
|---|---|---|
| `model` | `gemini-flash-lite-latest` | passed explicitly |
| `temperature` | `0.2` | override at call site |
| `top_p` | `0.95` | explicit (locked to Gemini default for auditability) |
| `top_k` | `40` | explicit (locked to Gemini default for auditability) |
| `max_output_tokens` | not set — model-specific limit | API default |
| `candidate_count` | `1` | API default |
| `safety_settings` | not set — Gemini default | API default |
| `tools` | `[types.Tool(google_search=types.GoogleSearch())]` | enabled via `web_search=True` flag |
| `response_mime_type` | not set (cannot combine with google_search per Gemini API rules) | constraint |
| `cache_ttl` | day-level (one search per day) | application-level |

Returns a structured object listing major nation-wide festivals and federal holidays for India and the USA — *today* and *tomorrow*. Used by the researcher to fire a `festival_alerts` entry when a holiday lands and the user's brief hasn't already referenced it.

The strict allow-list is **nation-wide festivals only** — regional holidays (e.g. state-level observances) are excluded to avoid producing tone-deaf posts for the wrong audience.

---

## 1.6 Stage 5 — Researcher Agent

**File:** `apps/backend/services/ai_service.py::_research_agent`
**Model:** `gemini-flash-lite-latest` with `google_search` tool
**Temperature:** 0.3 (grounded path) · 0.7 (offline fallback path)
**Approximate latency:** 12–25 s

**Full hyperparameter set — Grounded path (primary):**

| Hyperparameter | Value | Source |
|---|---|---|
| `model` | `gemini-flash-lite-latest` | `_call_agent` default |
| `temperature` | `0.3` | override at call site (line 1174) |
| `top_p` | `0.95` | explicit (locked to Gemini default for auditability) |
| `top_k` | `40` | explicit (locked to Gemini default for auditability) |
| `max_output_tokens` | not set — model-specific limit | API default |
| `candidate_count` | `1` | API default |
| `safety_settings` | not set — Gemini default | API default |
| `tools` | `[types.Tool(google_search=types.GoogleSearch())]` | `web_search=True` |
| `response_mime_type` | not set (cannot combine with google_search) | constraint |
| `grounding_metadata` | parsed from response on every call | post-processing |

**Full hyperparameter set — Offline fallback path (only if grounded errors):**

| Hyperparameter | Value | Source |
|---|---|---|
| `model` | `gemini-flash-lite-latest` | `_call_agent` default |
| `temperature` | `0.7` | override at call site (line 1185) |
| `tools` | `None` | `web_search=False` |
| (other params same as grounded path) | | |

### 1.6.1 What it does

The researcher reads the refined brief and the DNA, then dynamically decides what to search for: topic news, company news, competitor news, and (always) trending hashtags and keywords. The model picks its own search strategy per brief — there is no hand-coded keyword logic.

### 1.6.2 Output fields

| Field | Used by |
|---|---|
| `target_audience` | Content agent |
| `trending_context` | Content agent + frontend Strategic Blueprint panel |
| `problem_solving_opportunity` | Content agent |
| `company_product_analysis` | Content agent |
| `angles_to_test` | Content agent (one variant per angle) |
| `do_not_claim` | Content agent (anti-hallucination guard) |
| `trending_topics` / `trending_hashtags` / `trending_keywords` | Content agent + frontend |
| `competitor_news` | Frontend Strategic Blueprint panel |
| `festival_alerts` | Triggers the `festival_variant` slot |
| `grounding_confidence` | `grounded` / `partial` / `speculative` — content agent uses this to decide whether numeric proof is allowed |
| `sources` | Real URLs the researcher cited (rendered in the Sources panel) |
| **`referenced_entities`** | List-style briefs only — per-entity homepages (name + URL + one-liner) for "top 10 X" / "compare X vs Y" posts |
| `_grounding.queries` | The actual Google queries the model ran — rendered as the Web Searches accordion |

### 1.6.3 Time-window honouring

The researcher reads the brief for time signals and restricts sources accordingly:

| Brief language | Source window |
|---|---|
| `today` / `this morning` / `right now` | last 24 hours |
| `yesterday` | last 48 hours |
| `this week` | last 7 days |
| `this month` | last 30 days |
| no time signal | 7-day default |

Older sources are allowed only as labelled background — never as the primary citation.

---

## 1.7 Stage 6 — Content Agent (Copywriter)

**File:** `apps/backend/services/ai_service.py::_content_agent`
**Model:** `gemini-flash-lite-latest`
**Temperature:** 0.7 (default — `_call_agent` default, not overridden)
**Approximate latency:** 15–30 s (depending on number of platforms)

**Full hyperparameter set:**

| Hyperparameter | Value | Source |
|---|---|---|
| `model` | `gemini-flash-lite-latest` | `_call_agent` default |
| `temperature` | `0.7` | `_call_agent` default (copywriter does not override) |
| `top_p` | `0.95` | explicit (locked to Gemini default for auditability) |
| `top_k` | `40` | explicit (locked to Gemini default for auditability) |
| `max_output_tokens` | not set — model-specific limit | API default |
| `candidate_count` | `1` | API default |
| `safety_settings` | not set — Gemini default | API default |
| `tools` | `None` | No web grounding |
| `response_mime_type` | not set | Strict-JSON discipline lives in prompt |
| `system_instruction` | not set | All instructions in prompt |

### 1.7.1 What it produces

For each selected platform, the copywriter emits three or four variants:

| Variant key | Purpose | Sweet-spot character cap |
|---|---|---|
| `viral_reach` | Visibility-flavoured (saves + follows) | platform max |
| `high_interaction` | Comment-driven | platform max |
| `follower_growth` | Authority / depth | platform max |
| `festival_variant` | Emitted only when `festival_alerts` non-empty | platform max |

The orchestrator also tags one variant as `recommendation.best_variant` — the **AI-Recommended** variant the frontend highlights.

### 1.7.2 Platform caps (enforced server-side)

| Platform | Character cap (safety buffered) | True platform limit | Hashtag cap |
|---|---|---|---|
| LinkedIn | 2 800 | 3 000 | 0–5 |
| X (Twitter) | 270 | 280 | 0–2 |
| Facebook | 2 200 | 63 000 | 0–3 |
| Instagram | 2 100 | 2 200 | 8–15 |

Caps are enforced in post-processing as a hard guarantee — the server truncates anything over the limit even if the model writes longer copy.

### 1.7.3 Hard Rules (R1–R9, enforced in prompt)

| Rule | What it enforces |
|---|---|
| R1 | Anti-hallucination — numbers, dates, customer counts, product names must trace to brief / DNA / doc / `research.sources` |
| R2 | Character caps (see table above) |
| R3 | Hashtag caps (see table above) |
| R4 | No share-bait — phrases like `"share this"`, `"tag someone who"`, `"RT if you agree"` are banned |
| R5 | Distinct angles — each variant maps to a different `angles_to_test` entry |
| R6 | Brand voice — match `brand_tone` + `brand_values` from DNA, do not open with press-release voice |
| R7 | No buzzword corporate-speak (`empower`, `democratize`, `seamlessly`, `leverage`, `unlock`, `streamline`, `transform`, `revolutionize`, etc.) |
| R8 | No lazy CTAs (`"Comment below"`, `"What are your thoughts?"`, `"Share your thoughts"`) |
| **R9** | **Real URLs only — no placeholder links.** Pull URLs from (a) USER LINKS (refiner-extracted), (b) `research.referenced_entities[].url` for list briefs, (c) `research.sources[].url` for further-reading, (d) DNA Resource URLs for brand CTAs. Never emit `[Insert Link N]`, `[link]`, `[URL]`, `[your website]`, `(link)`, `https://example.com`, or any placeholder. |

---

## 1.8 Stage 7 — Critic Agent

**File:** `apps/backend/services/ai_service.py::_critic_agent`
**Model:** `gemini-flash-lite-latest`
**Temperature:** 0.7 (default — `_call_agent` default, not overridden)

The critic audits the assembled payload — refined brief, content variants, recommendation — for brand consistency and rule adherence. Its `critique` field is surfaced as the **Verification** section in the API response, used by the frontend for transparency.

For non-image post types (text / video / document) the critic is skipped to save approximately 40 s of latency.

**Full hyperparameter set:**

| Hyperparameter | Value | Source |
|---|---|---|
| `model` | `gemini-flash-lite-latest` | `_call_agent` default |
| `temperature` | `0.7` | `_call_agent` default (critic does not override) |
| `top_p` | `0.95` | explicit (locked to Gemini default for auditability) |
| `top_k` | `40` | explicit (locked to Gemini default for auditability) |
| `max_output_tokens` | not set | API default |
| `candidate_count` | `1` | API default |
| `safety_settings` | not set — Gemini default | API default |
| `tools` | `None` | No web grounding |

---

## 1.9 Stage 8 — Post-Processing (Deterministic)

**File:** `apps/backend/services/ai_service.py::_apply_content_post_processing`

After the content agent returns, three deterministic transformations are applied to every variant before the payload is returned to the user:

| Pass | Function | Purpose |
|---|---|---|
| 1 | `_strip_placeholder_links` | Belt-and-braces removal of any `[Insert Link N]` / `[URL]` / `(link)` / `example.com` patterns the model may still emit despite R9. Dead resource-list lines are dropped entirely; inline tokens are scrubbed from prose. |
| 2 | `_enforce_hashtag_cap` | Caps hashtag count per platform (LinkedIn 5, X 2, Facebook 3, Instagram 15) |
| 3 | `_enforce_char_cap` | Caps character count per platform (LinkedIn 2 800, X 270, Facebook 2 200, Instagram 2 100) |

The user is guaranteed that no post returned by the API will exceed platform limits, contain placeholder URLs, or exceed hashtag norms — regardless of what the model produced.

---

## 1.10 Output Structure

The orchestrator returns a JSON payload with the following top-level fields:

| Field | Type | Description |
|---|---|---|
| `refined_brief` | string | Structured strategic brief from Stage 3 |
| `research_report` | object | Researcher output (all fields from §1.6.2) |
| `recommendation` | object | `{ best_variant: "viral_reach" \| "high_interaction" \| "follower_growth" \| "festival_variant", reasoning: "..." }` |
| `content` | object | `{ <platform>: { <variant_key>: "<post text>" } }` — 3 or 4 variants per platform |
| `visuals` | array | Image agent output (covered in Section 2 of this document, to follow) |
| `sources` | array | Real URLs the researcher cited |
| `web_searches` | array | The actual Google queries that produced the sources |
| `cultural_calendar` | object | Today + tomorrow festivals from Stage 4 |
| `festival_alerts` | array | Subset of `cultural_calendar` that triggered a `festival_variant` |
| `verification` | object | Critic agent's audit |
| `stage_times` | object | `{ refine, cultural, research, content, visuals, critic, image_check }` — wall-clock seconds per stage |
| `pipeline` | string | Pipeline marker — `"freeform_v3"` for verifiability |

---

## 1.11 Worked Scenarios

### Scenario A — Standard brand-focused brief with DNA attached

| Input | Path | Output |
|---|---|---|
| Brief: *"Announce Spenzo AI's new chat-based budget simulation feature to growth marketers"* | Stage 1 ✅ → Stage 2 (Spenzo DNA loaded) → Stage 3 refiner produces structured brief → Stage 4 cultural calendar (no festival match) → Stage 5 researcher runs `google_search` for Spenzo + competitors → Stage 6 content agent produces 3 variants × 4 platforms = 12 captions, infused with Spenzo brand voice → Stage 7 critic audits → Stage 8 caps + URL cleanup | 12 LinkedIn/X/Facebook/Instagram captions, AI-recommended variant tagged, sources rendered in Strategic Blueprint, real URLs from DNA's `product_url` / `website_url` |

### Scenario B — Cross-topic industry brief

| Input | Path | Output |
|---|---|---|
| Brief: *"Write a LinkedIn post about today's top AI news from Google I/O"* | Stage 1 ✅ → Stage 2 (Spenzo DNA loaded but only voice flows, no product pitch) → Stage 3 refiner tags brief as `cross-topic` → Stage 5 researcher runs grounded search restricted to the last 24 hours → Stage 6 content agent stays on topic, brand voice only, does not pivot to product promotion | LinkedIn caption discussing Google I/O news in Spenzo's tonal voice, with real source URLs cited |

### Scenario C — Admin selects "None" (no DNA)

| Input | Path | Output |
|---|---|---|
| Brief: *"Write a post about the day in the life of an AI engineer"* + DNA chip set to `None` | Stage 1 ✅ → Stage 2 (zero brand context) → Stage 3 refiner sees `dna_attached: no` → Stage 5 researcher grounds in real engineering content → Stage 6 content agent has no brand voice constraint, writes neutral/professional copy | Brand-agnostic post copy — no Spenzo wordmark, no product mentions, no DNA-bound CTA |

### Scenario D — Banned-term brief

| Input | Path | Output |
|---|---|---|
| Brief: *"Generate a violent ad threatening competitors"* | Stage 1 ❌ — banned term `violent`/`threatening` matched | HTTP 422 returned in < 50 ms. Frontend renders the policy message inline under the textarea. No AI cost incurred. |

### Scenario E — Too-short brief

| Input | Path | Output |
|---|---|---|
| Brief: `Promote it` | Stage 1 ❌ — 2 words, below the 4-word minimum | HTTP 422 with: *"Your brief is too short (2 words). Please write at least 4 words…"* |

### Scenario F — Generic placeholder brief

| Input | Path | Output |
|---|---|---|
| Brief: `create a post` | Stage 1 ❌ — generic-command regex match | HTTP 422 with: *"Your brief is too generic. Please describe the actual campaign…"* |

### Scenario G — Team-member uses Pipelyt without their own DNA

| Input | Path | Output |
|---|---|---|
| Brief: *"Announce our Q3 partnership with Acme"* by a team member of admin `info@z-ninth.com` | Stage 2 — system detects the user is a team member and clamps to the admin's DNA silently. The team member cannot bypass brand context. | The output uses Z-Ninth brand voice + Z-Ninth resource URLs even though the team member did not select a DNA explicitly |

### Scenario H — List-style brief (top 10 AI tools)

| Input | Path | Output |
|---|---|---|
| Brief: *"Generate a post about the top 10 AI tools for content creators in 2026"* | Stage 5 researcher detects the list pattern and emits `referenced_entities` with 10 entries — one Google search per entity to fetch each tool's official homepage URL → Stage 6 content agent uses those URLs verbatim in the post's numbered list | Post text reads: *"1. ChatGPT — https://chat.openai.com / 2. Claude — https://claude.ai / 3. Midjourney — https://midjourney.com …"* with real homepage URLs — no `[Insert Link]` placeholders |

### Scenario I — User-provided URLs in the brief

| Input | Path | Output |
|---|---|---|
| Brief: *"Write a LinkedIn post about Salman Shaik's beginner AI tutorial at https://example.com/ai-101"* | Stage 3 refiner extracts `https://example.com/ai-101` into the `USER LINKS` section of the structured brief → Stage 6 content agent treats USER LINKS as priority (a) — uses the URL verbatim in any CTA or reference | Caption includes the user's URL exactly as written, including query strings |

---

## 1.12 Error Message Catalogue (User-Facing)

All messages are returned as HTTP 422 with `{ "detail": "<message>" }` and rendered inline beneath the brief textarea.

| Trigger | Message |
|---|---|
| Empty brief | *"Please enter a campaign brief before generating."* |
| Brief < 15 chars or < 4 words | *"Your brief is too short (N word(s)). Please write at least 4 words describing what you want to promote, who it's for, and why it matters. Example: 'Launch our new Q2 pricing with a focus on mid-market teams — emphasize the 40% time savings.'"* |
| Banned policy term | *"We can't generate marketing content around this topic. Your brief contained language our policy doesn't support — please rewrite with a business-appropriate focus…"* |
| Low alpha density | *"Your brief has too few real words. Please describe the campaign in plain English…"* |
| Generic placeholder | *"Your brief is too generic. Please describe the actual campaign — what you're promoting, the audience, and the key message — instead of just a command."* |
| Refiner — harmful | Custom message from the refiner explaining the policy issue |
| Refiner — no_utility | *"This brief doesn't describe content any business could plausibly market. Please rewrite as a campaign idea — what you want to promote, who it's for, and why it matters."* |

---

## 1.13 Performance Profile

Wall-clock timings observed in production with the active pipeline (per `stage_times` in the API response):

| Stage | Typical | Worst case | Notes |
|---|---|---|---|
| Brief Guard | < 50 ms | 50 ms | Pure regex, no AI cost |
| DNA Resolution | < 100 ms | 300 ms | DB lookup + JSON merge |
| Refiner | 5–12 s | 15 s | One Gemini call |
| Cultural Calendar | < 100 ms (warm cache) | 5 s (cold) | Cached daily |
| Researcher | 12–25 s | 40 s | google_search grounding + N entity lookups for list briefs |
| Content Agent | 15–30 s | 45 s | One Gemini call per orchestration |
| Critic | 8–12 s | 20 s | Skipped for non-image post types |
| Post-Processing | < 50 ms | 200 ms | Deterministic |
| **End-to-end** | **45–80 s** | **2 min** | Image generation runs in parallel and is timed separately |

---

## 1.14 Operational Observability

Every `/generate-content` request is logged with `stage_times` per agent. In local development the request is also appended as a row to `apps/backend/fewshot_test_results_temperature_1.5.csv` for offline auditing. The CSV captures: business DNA used, brief text, all four variants × four platforms, three image URLs, agent timings, and per-image quality audits.

Production runs are gated by the `AWS_LAMBDA_FUNCTION_NAME` environment variable — the CSV logger is a no-op on Lambda.

---

## 1.15 Summary

The text content generation pipeline is composed of eight stages: a rule-based brief guard, business DNA resolution, refiner, cultural calendar, researcher, content agent, critic, and post-processing. Validation happens at three depths (rule, semantic-refiner, post-processing). Real-time grounding is achieved through Google Search integration at the researcher tier. Brand context flows through every agent via a structured `user_context` block surfaced from each user's Business DNA.

Quality guarantees enforced server-side regardless of model output:

- Character caps per platform (LinkedIn 2 800, X 270, Facebook 2 200, Instagram 2 100)
- Hashtag caps per platform (LinkedIn 5, X 2, Facebook 3, Instagram 15)
- Placeholder URL strip
- Real URLs from prioritised sources (user-typed → research entities → research sources → DNA URLs)
- Banned-term policy enforcement
- Semantic relevance check at the refiner

This document will be expanded in subsequent revisions to cover Image Generation, Publishing, Analytics, and Scheduling.

---

*End of Section 1.*

---

# Section 2 — Image Generation Pipeline

## 2.1 Overview

Pipelyt generates three image variants per `/generate-content` request, returned alongside the text captions for the user to pick from in the review step. Image generation is parallel — all three variants run concurrently via `asyncio.gather`. The active model is **`gemini-3.1-flash-image`** with automatic fallback to `gemini-2.5-flash-image` if the primary errors.

The pipeline has gone through four distinct iterations as we worked toward production-quality outputs. The current iteration uses **few-shot in-context learning** with hand-curated reference images.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ INPUT — refined brief + AI-recommended caption + DNA + logo (optional)   │
│         + user-selected aspect ratio (default 16:9)                       │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
              ┌───────────────────┴───────────────────┐
              │ Orchestrator (ai_service.py)           │
              │ Calls generate_image_variants_v4(...)  │
              └───────────────────┬───────────────────┘
                                  │
                  ┌───────────────┴───────────────┐
                  │  Fan-out 3 parallel variants   │
                  │  variant_idx ∈ {0, 1, 2}       │
                  └───────────────┬───────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        ▼                         ▼                         ▼
  ┌──────────┐              ┌──────────┐              ┌──────────┐
  │ VARIANT 1│              │ VARIANT 2│              │ VARIANT 3│
  │  Set A   │              │  Set B   │              │  Set C   │
  └────┬─────┘              └────┬─────┘              └────┬─────┘
       │                         │                         │
       │  Each variant:          │                         │
       │  • Builds 8,800-char    │                         │
       │    text prompt          │                         │
       │  • Loads 5 GOOD + 3 BAD │                         │
       │    reference PIL images │                         │
       │  • Interleaves prompt   │                         │
       │    + images + labels    │                         │
       │  • Appends logo (if any)│                         │
       │                         │                         │
       ▼                         ▼                         ▼
  client.models.generate_content_stream(
      model="gemini-3.1-flash-image",
      contents=[prompt, img1, label1, ..., bad3, label3, logo],
      config=GenerateContentConfig(
          response_modalities=["IMAGE", "TEXT"],
          temperature=1.5,
          image_config=ImageConfig(aspect_ratio=..., image_size="1K"),
      ),
  )
       │
       ▼
  PNG bytes → strip EXIF → S3 upload → public URL
       │
       ▼
  Image-Check Agent (local-only) — Gemini Vision audit of every variant
       │
       ▼
  CSV row append (local-only)
```

---

## 2.2 Pipeline Evolution — What Came Before

Understanding how we arrived at the current few-shot pipeline matters because each prior iteration left lessons behind. The active production code is **v4 (few-shot)**.

### 2.2.1 v1 — PIL Template Compositor (deprecated)

| Aspect | Detail |
|---|---|
| File | `services/image_utils.py` (still present as legacy) |
| Approach | Eleven hand-crafted PIL templates rendered server-side; AI generated only the background, then text + logo were overlaid via PIL |
| Pros | Pixel-perfect text rendering, brand-safe |
| Cons | Templates felt rigid and template-y; outputs read as "Canva clip art" |
| Why we moved on | Templates capped the design ceiling — no editorial photo, no typography-poster, no creative composition possible |

### 2.2.2 v2 — Visualist v2 (template-aware AI backgrounds)

| Aspect | Detail |
|---|---|
| File | `_generate_visuals_v2` in `ai_service.py` (still present as fallback) |
| Approach | AI-generated backgrounds × 3 + PIL templates × 11; restricted templates per scene type (`product_workspace` → 2 templates only) |
| Pros | More aesthetic variety than v1 |
| Cons | Still template-bound; still composited PIL text overlays |
| Why we moved on | The compositor and the AI image model produced visually disconnected layers |

### 2.2.3 v3 — Freeform Visual Service (pure Gemini, basic prompt)

| Aspect | Detail |
|---|---|
| File | `services/freeform_visual_service.py` (still present as fallback) |
| Approach | Single Gemini call per variant with a ~500-char prompt; no templates, no overlay compositor; model decides everything |
| Pros | Variety jumped — editorial photos, typography, infographics, all possible |
| Cons | **Every variant gravitated to the same dark glass-morphism + orange glow on void archetype.** Hex codes leaked into images as visible text. Brand-guide vocabulary (`Brand Color`, `Inter Family`) rendered. Aspect ratio not honoured. |
| Why we moved on | Variant convergence was 100 %; aesthetic was unmistakably AI |

### 2.2.4 v4 — Image Agent v4 (current, few-shot in-context learning)

| Aspect | Detail |
|---|---|
| File | `services/image_agent_v4.py` |
| Approach | Pure Gemini call per variant + **8 hand-curated reference images** (5 GOOD + 3 BAD) interleaved with rationale text in the multimodal `contents` list |
| Prompt size | 8 800 characters (versus ~500 in v3) |
| Reference images | 15 GOOD curated from PwC, TCS, Accenture, Lifesight, MuleSoft, and 6 Canva templates, rotated as Sets A/B/C across the 3 variants. 3 BAD anti-examples constant. |
| Temperature | 1.5 (explicitly set; v3 used silent default ~1.0) |
| Aspect ratio | User-selected from chip; flows through to `ImageConfig.aspect_ratio` |
| Output format | PNG (lossless) re-encoded server-side; EXIF stripped; S3 `.png` ContentType |
| Status | **Live in production as of 2026-06-04** |

---

## 2.3 v4 Current Pipeline — Few-Shot Architecture

### 2.3.1 Entry point

**File:** `apps/backend/services/image_agent_v4.py`
**Function:** `generate_image_variants_v4()` at line 1027

Called from the orchestrator at `ai_service.py:3352`:

```python
visual_variants = generate_image_variants_v4(
    refined_brief=refined_brief,
    content_data=content_data,
    recommendation=content_data.get("recommendation") or {},
    user_context=user_context,
    primary_color=primary_color,
    logo_bytes=logo_bytes,
    aspect_ratio=aspect_ratio,    # user chip selection
    n_variants=3,
    client=client,
)
```

### 2.3.2 The bypass flag

A feature flag `BYPASS_ART_DIRECTOR` controls whether the pipeline runs in two-stage mode (Art Director text agent → image model) or direct mode (prompt + few-shot → image model). It is **currently `true`** because empirical testing showed the Art Director text agent (gemini-flash-lite-latest) introduced its own aesthetic bias rather than reducing it.

### 2.3.3 Per-variant work — `_generate_single_variant()`

For each variant index ∈ {0, 1, 2}:

1. **Build the 8 800-char prompt** via `_build_direct_image_prompt()` (see Appendix A.5)
2. **Build the few-shot multimodal payload** via `_build_fewshot_contents(variant_idx)`:
   - Set A (variant 1), Set B (variant 2), or Set C (variant 3) — 5 GOOD images
   - Always the same 3 BAD anti-examples
   - Each image is preceded by an `EXPECTED USER INPUT` text block and followed by a `WHY THIS IS GOOD` / `WHAT WENT WRONG + WHY BAD` rationale block
3. **Append the brand logo** (when present) as the last PIL image
4. **Call `client.models.generate_content_stream(model, contents, config)`** — one multimodal turn
5. **Receive PNG bytes**, strip EXIF, upload to S3, return `{url, prompt, variant_idx, ...}`

### 2.3.4 Generation config (verbatim from code)

```python
gen_config = types.GenerateContentConfig(
    response_modalities = ["IMAGE", "TEXT"],
    temperature         = 1.5,
    image_config = types.ImageConfig(
        aspect_ratio = aspect_ratio,   # default "16:9", from user chip
        image_size   = "1K",
    ),
)
```

**Full hyperparameter set for image generation:**

| Hyperparameter | Value | Source |
|---|---|---|
| `model` | `gemini-3.1-flash-image` | env `IMAGE_AGENT_V4_MODEL` override possible |
| `fallback_model` | `gemini-2.5-flash-image` | hard-coded; tried automatically on primary error |
| `temperature` | `1.5` | explicit override |
| `top_p` | `0.95` | explicit (locked to Gemini default for auditability) |
| `top_k` | `40` | explicit (locked to Gemini default for auditability) |
| `max_output_tokens` | not set | API default |
| `candidate_count` | `1` | API default |
| `safety_settings` | not set — Gemini default | API default |
| `response_modalities` | `["IMAGE", "TEXT"]` | explicit |
| `image_config.aspect_ratio` | user chip selection (default `"16:9"`) | flows from frontend |
| `image_config.image_size` | `"1K"` | hard-coded |
| `image_config.person_generation` | not set — Gemini default | API default |
| `image_config.seed` | not supported by API | n/a |
| `thinking_config` | not set | API default |
| **API method** | `client.models.generate_content_stream(...)` | streaming response |
| **Concurrency** | 3 variants in parallel via `asyncio.gather` | application-level |
| **Per-variant timeout** | no explicit timeout — relies on SDK defaults | SDK default |
| **Per-variant reference images** | 5 GOOD + 3 BAD + 1 logo (when present) = 8–9 PIL images | `_build_fewshot_contents()` |
| **Per-variant text bytes** | ~50 KB prompt + ~8 KB few-shot wrapper text | computed |
| **Per-variant image bytes uploaded** | ~3.5 MB (sum of 8 PIL images) | computed |
| **Post-render: strip EXIF** | yes (`_strip_image_metadata`) | application-level |
| **Post-render: re-encode** | PNG lossless (RGB / RGBA preserved) | application-level |
| **S3 ContentType** | `image/png` | application-level |
| **S3 key pattern** | `ai_gen/v4/visual_<uuid>.png` | application-level |

### 2.3.5 The curated reference pool — 15 GOOD + 3 BAD

**Storage:** `apps/backend/assets/fewshot/`
**Total size:** 3.4 MB

| Band | Set | Slug | Source brand | Composition style |
|---|---|---|---|---|
| Service | A | `pwc_service_2.jpg` | PwC | Pure typography poster on peach gradient |
| Service | A | `accenture_service_1.jpg` | Accenture | Full-bleed photo + bold headline + brand-purple accent |
| Service | B | `tcs_service_2.jpg` | TCS | Blueprint → real-photo concept split |
| Service | C | `pwc_service_1.jpg` | PwC | Editorial magazine cover (top text, bottom photo) |
| Festival | A | `pwc_festival_1.jpg` | PwC | Atmospheric bokeh + minimal serif greeting |
| Product | A | `lifesight_product_2.jpg` | Lifesight | All-text product launch on neutral gradient |
| Product | A | `mulesoft_product_3.jpg` | MuleSoft | Event-announcement typography on dark gradient |
| Product | B | `lifesight_product_1.jpg` | Lifesight | Product UI mocked into a flow diagram |
| Product | C | `mulesoft_product_1.jpg` | MuleSoft | Branded product collage with pill elements |
| Canva | B | `canva_5_milestones.jpg` | Canva | Flat timeline infographic with line icons |
| Canva | B | `canva_3_pioneers.jpg` | Canva | Structured corporate brochure, 3-column |
| Canva | B | `canva_2_graphic_design.jpg` | Canva | Small-business busy template, balanced |
| Canva | C | `canva_6_creative_agency.jpg` | Canva | 60/40 photo-text editorial |
| Canva | C | `canva_8_construction.jpg` | Canva | Geometric triangle photo collage |
| Canva | C | `canva_7_build_your_business.jpg` | Canva | Trendy organic-shapes + photo cutout |
| Anti | All | `bad_zyntegrate_glass_cube.jpg` | (failure case) | Typos + duplicates + glass cube |
| Anti | All | `bad_spenzo_brand_swatches.jpg` | (failure case) | Hex codes rendered as visible text |
| Anti | All | `bad_zyntegrate_ai_agents.jpg` | (failure case) | Misspelled verb + invented Salesforce logo |

Each variant request sends:

| Component | Bytes per request |
|---|---|
| Text prompt | ~50 KB |
| 5 GOOD images | ~1.0–1.5 MB |
| 3 BAD images | ~1.2 MB |
| Logo (when present) | ~50 KB |
| **Total per variant** | **~3.5 MB upload** |

3 variants per request → **~10 MB total per `/generate-content` call**

### 2.3.6 Multimodal payload structure (per variant)

The `contents` list is **one Python list of 27 items** passed to the model as **one multimodal turn**:

```
[0]  TEXT — Main prompt (ROLE + HOW TO USE + GOAL + STAGE 1/2/3 + R1–R5 + OUTPUT) ~ 8.8K chars
[1]  TEXT — GOOD section header
[2]  TEXT — GOOD example 1 intro (incl. EXPECTED USER INPUT)
[3]  IMAGE — pwc_service_2.jpg (PNG bytes)
[4]  TEXT — WHY THIS IMAGE IS GOOD rationale
[5]  TEXT — GOOD example 2 intro
[6]  IMAGE — accenture_service_1.jpg
[7]  TEXT — rationale
...   (5 GOOD examples total)
[16] TEXT — BAD anti-examples section header
[17] TEXT — BAD anti-example 1 intro
[18] IMAGE — bad_zyntegrate_glass_cube.jpg
[19] TEXT — WHAT WENT WRONG + WHY BAD rationale
...   (3 BAD anti-examples total)
[26] TEXT — END OF REFERENCES closer
(27) IMAGE — brand logo PNG bytes (only when logo provided)
```

Verified empirically: the model receives **real pixel bytes**, not file paths. No path string leaks into any text block.

### 2.3.7 Per-variant set rotation — Sets A / B / C

Variant 0 gets Set A, Variant 1 gets Set B, Variant 2 gets Set C — no overlap. This is the primary lever for batch diversity (Issue I-2 in the research doc).

| Set | Focus | Composition styles included |
|---|---|---|
| A | Typography & editorial | typography poster · editorial photo · all-text · event card · festival greeting |
| B | Structured & infographic | timeline infographic · corporate brochure · concept split · product UI · SMB busy template |
| C | Photo-led & creative | editorial cover · 60/40 photo-text · geometric collage · trendy organic shapes · pill collage |

---

## 2.4 Hard Guardrails (R1–R5) in the Image Prompt

Every variant's prompt closes with five non-negotiable rules:

| Rule | What it enforces |
|---|---|
| R1 SPELLING DISCIPLINE | Every word rendered must be spelled correctly; text lifted verbatim from caption; omit text entirely when uncertain |
| R2 HEX CODES NEVER AS TEXT | Brand colour value is a colour spec for the designer — must never appear in the image as letters, digits, or labels |
| R3 NO DUPLICATED ELEMENTS | Exactly one headline / one subhead / one CTA / one focal subject / one logo; never repeat the same icon, phrase, or brand mark |
| R4 LOGO INTEGRITY | If logo attached: use exactly or omit. Never invent a brand mark for any company. |
| R5 STYLE DIVERSITY | Each variant must look visibly different from the glass-morphism / glow / void default AI aesthetic |

---

## 2.5 Image-Check Agent (Local-Only Audit)

**File:** `apps/backend/services/image_check_agent.py`
**Model:** `gemini-2.5-flash` (vision-capable)
**Temperature:** 0.2
**Trigger:** runs **only on dev machines** — guarded by absence of `AWS_LAMBDA_FUNCTION_NAME` env var

**Full hyperparameter set:**

| Hyperparameter | Value | Source |
|---|---|---|
| `model` | `gemini-2.5-flash` | env `IMAGE_CHECK_MODEL` override possible |
| `temperature` | `0.2` | explicit override |
| `top_p` | `0.95` | explicit (locked to Gemini default for auditability) |
| `top_k` | `40` | explicit (locked to Gemini default for auditability) |
| `max_output_tokens` | not set | API default |
| `candidate_count` | `1` | API default |
| `safety_settings` | not set — Gemini default | API default |
| `tools` | `None` | No web grounding |
| **Per-call inputs** | 1 PIL image (audited) + audit prompt + brief + caption | application-level |
| **Concurrency** | 3 audits in parallel via `ThreadPoolExecutor` | application-level |
| **Per-audit timeout** | 60 s | application-level (`fut.result(timeout=60)`) |
| **Download timeout** | 15 s (S3 fetch) | application-level (`requests.get(timeout=15)`) |
| **Gate** | `not bool(AWS_LAMBDA_FUNCTION_NAME)` — local-only | env-guarded |

After the three image variants are generated, the audit agent downloads each PNG and asks Gemini Vision to spot quality issues. Findings are written into the CSV alongside the image URL.

### 2.5.1 Audit categories

| Category | What the audit detects |
|---|---|
| TYPOS | Hallucinated or misspelled words rendered on the image |
| DUPLICATED ELEMENTS | Repeated icons, logos, headlines, phrases |
| HEX CODES / META-DATA | Strings like `#FF4500`, `Brand Color`, `Inter Family`, `Aspect ratio` rendered as text |
| INVENTED BRAND MARKS | Fake third-party logos (Salesforce, AWS, Google, Meta, etc.) |
| AI-RENDER AESTHETIC | Glass cubes, glow-on-void, generic 3D, floating UI cards |
| GIBBERISH INSIDE SCREENS | Unreadable text inside laptop / phone / tablet mockups |
| BROKEN PUNCTUATION | Unclosed apostrophes, stray decorative dots / lines |

### 2.5.2 Output

Per variant, the audit returns a bullet list (or `• No issues detected`). Findings are surfaced into the CSV columns `IMAGE_VARIENT_1_AUDIT`, `IMAGE_VARIENT_2_AUDIT`, `IMAGE_VARIENT_3_AUDIT`.

This local-only loop has been used to measure quality regressions and improvements across pipeline iterations. As of 2026-06-04, audits of 69 v4 images show:

| Metric | v3 (before) | v4 (current) |
|---|---|---|
| Variants converging on glass/glow archetype | 100 % | 9 % |
| Hex codes drawn as visible text | nearly every brand-focused image | 1 instance in 69 |
| Brand-guide vocabulary leaks (`Tone tone`, `Inter Family`) | frequent | eliminated |
| Variant diversity (3 distinct directions per batch) | 0 % | ~70 % |
| Typos in rendered text | recurring same words | new each time, ~40 instances across 69 |
| Third-party logo invention | always wrong | still wrong (Salesforce, Google, AWS, Meta) |

The remaining failure modes (typos, third-party logo invention) are diffusion-model-level limitations that prompt-only changes cannot fully eliminate. A two-stage Art Director architecture is documented as the next planned iteration.

---

# Appendix A — Full Prompts

The full text of every prompt used across the pipeline is listed below. Where the prompts are very long, key excerpts are shown verbatim with omitted middle sections marked `[…]` and a reference to the source file + line numbers for the complete text.

---

## A.1 Refiner Agent Prompt

**File:** `services/ai_service.py` · line 349 · function `_refine_brief_agent`
**Model:** `gemini-flash-lite-latest` · **Temperature:** 0.3

```
You are a Senior Social Media Growth Strategist.

Your job: take a user's raw campaign brief and produce a structured
STRATEGIC BRIEF that downstream agents (research, copywriter, visualist)
can execute against without guessing what the user meant.

═══════════════════════════════════════════════════════════════
PRIMARY SOURCE OF TRUTH = THE USER'S CAMPAIGN BRIEF
═══════════════════════════════════════════════════════════════
The brief is the only authoritative source for:
  • WHAT the campaign is about (topic, subject, announcement)
  • WHY it matters (the user's objective)
  • Any specific facts, numbers, names, dates the user included

The brief is what you respect. DNA is what you reach for when the
brief leaves a gap that must be filled to produce something usable.

═══════════════════════════════════════════════════════════════
SUPPORTING KNOWLEDGE = BUSINESS DNA + UPLOADED DOCUMENTS (optional)
═══════════════════════════════════════════════════════════════
DNA attached: {dna_attached}

===== BUSINESS DNA + KNOWLEDGE BASE =====
{knowledge_block}
===== END KNOWLEDGE =====

USE THIS KNOWLEDGE STRICTLY FOR:
  ✓ Voice / tone / brand values when shaping TONE
  ✓ Audience persona inference when the brief didn't name one
  ✓ Concrete supporting facts (only when sourced & cited)
  ✓ Visual flavor hints (brand aesthetic)

DO NOT USE KNOWLEDGE FOR:
  ✗ Changing the topic the user asked about
  ✗ Inserting a product into a brief that's about something else
  ✗ Inventing metrics, percentages, dollar figures, or claims not
    present in the brief OR in the knowledge text above
  ✗ Pivoting an industry-news / opinion / educational brief into
    a product pitch

If the user is campaigning ABOUT their own product, DNA naturally
becomes deeply relevant. If the user is campaigning about an adjacent
topic (industry trends, news, general education), DNA contributes
voice and audience only — the post stays on the user's topic.

═══════════════════════════════════════════════════════════════
USER'S RAW CAMPAIGN BRIEF
═══════════════════════════════════════════════════════════════
({word_count} words, quality hint: {quality_hint})

"""
{brief_text or '(empty — user did not provide any brief text)'}
"""

═══════════════════════════════════════════════════════════════
STEP 0 — VALIDATE THE BRIEF BEFORE REFINING (CRITICAL)
═══════════════════════════════════════════════════════════════
If ANY category below fires, DO NOT produce a strategic brief.
Return the rejection JSON shown at the end of this section.

A. HARMFUL — promotes, glorifies, instructs, or plans:
   • violence (killing, bombing, shootings, terrorism, weapons,
     beheadings, massacre, genocide)
   • self-harm or suicide
   • sexual / explicit content / adult services / minors
   • illegal activity (drug dealing, trafficking, fraud, scams,
     pyramid schemes, phishing, money laundering, hacking-for-hire)
   • hate speech, slurs, harassment, targeting of any group
   Even if framed as "marketing" for a product implying these — reject.

B. GENERIC / EMPTY INTENT — no actual campaign concept.

C. NO MARKETING UTILITY — the brief has no plausible social-media
   marketing use case for ANY business.

If REJECTED, return EXACTLY:
{
  "valid": false,
  "rejection_category": "harmful" | "generic" | "no_utility",
  "rejection_message": "<one short user-facing sentence>"
}

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT — produce these labelled sections, in this order
═══════════════════════════════════════════════════════════════
STRATEGIC BRIEF
──────────────────────────────────────────
USER GOAL
TOPIC
AUDIENCE
KEY MESSAGE
ANGLE
SUPPORTING POINTS  (each point cited with [user brief] / [DNA: x] / [doc: x])
TONE
SOURCES REFERENCED
USER LINKS          ← extract every URL the user typed VERBATIM
VISUAL HINT
CONSTRAINTS
USER INPUT QUALITY: {quality_hint}
ASSUMPTIONS MADE
```

Note: the live prompt also contains a detailed source-grounding policy block and explicit anti-hallucination rules. Full text at `ai_service.py:349-568`.

---

## A.2 Cultural Calendar Prompt

**File:** `services/ai_service.py` · function `_get_cultural_calendar`
**Model:** `gemini-flash-lite-latest` with `google_search` tool · **Temperature:** 0.2
**Cache:** day-level

Short prompt that runs `google_search` for current Indian + US national festivals and federal holidays for today and tomorrow. The strict allow-list filter ensures only nation-wide events are surfaced (regional holidays excluded). The result is a structured list of `{country, festival_name, when, date, type}` objects used by the researcher and content agents.

---

## A.3 Researcher Agent Prompt

**File:** `services/ai_service.py` · function `_research_agent`
**Model:** `gemini-flash-lite-latest` with `google_search` tool · **Temperature:** 0.2

Key sections of the live prompt:

```
You are a senior research agent producing FRESH, REAL, GROUNDED context
for a social-media campaign. You can search the web. Use it.

═══════════════════════════════════════════════════════════════
INPUTS
═══════════════════════════════════════════════════════════════
REFINED BRIEF:
{refined_brief}

BRAND PROFILE (use to disambiguate searches when brand-focused):
{user_context}

CULTURAL CALENDAR (today + tomorrow festivals):
{cultural_calendar_block}

═══════════════════════════════════════════════════════════════
SEARCH STRATEGY (you decide per brief)
═══════════════════════════════════════════════════════════════
1. TOPIC NEWS — recent developments on the brief's topic.
2. COMPANY NEWS — when DNA is attached and the brief touches the brand.
3. COMPETITOR NEWS — when DNA names competitors or the brief invites
   contrast.
4. TRENDING — always: topical hashtags + trending keywords for reach.
5. LIST RESOLUTION — when the brief asks for a list/roundup/comparison
   of named entities, do ONE additional google_search per entity to fetch
   each entity's OFFICIAL HOMEPAGE URL.

TIME WINDOW HONOURING:
  • "today" / "right now"  → sources from last 24h
  • "yesterday"            → sources from last 48h
  • "this week"            → last 7 days
  • "this month"           → last 30 days
  • no time signal         → 7-day default

═══════════════════════════════════════════════════════════════
OUTPUT JSON (strict)
═══════════════════════════════════════════════════════════════
{
  "target_audience":            "...",
  "trending_context":           "...",
  "problem_solving_opportunity": "...",
  "company_product_analysis":   "...",
  "angles_to_test":             ["angle 1", "angle 2", "angle 3"],
  "do_not_claim":               ["claim 1", "claim 2"],
  "trending_topics":            [...],
  "trending_hashtags":          [...],
  "trending_keywords":          [...],
  "competitor_news":            [{"competitor": "...", "headline": "...", "src": 1, "published": "YYYY-MM-DD"}],
  "festival_alerts":            [{"country":"india","festival_name":"...","when":"today","date":"YYYY-MM-DD","type":"...","mentioned_in_brief":false,"suggested_angle":"..."}],
  "grounding_confidence":       "grounded | partial | speculative",
  "sources":                    [{"id":1,"url":"...","title":"...","published":"YYYY-MM-DD","publisher":"..."}],
  "referenced_entities":        [{"name":"ChatGPT","url":"https://chat.openai.com","one_liner":"..."}]
}
```

---

## A.4 Content Agent (Copywriter) Prompt

**File:** `services/ai_service.py` · function `_content_agent`
**Model:** `gemini-flash-lite-latest` · **Temperature:** 0.7

Full prompt opens with creative-freedom framing, surfaces all available context, then enforces 9 hard rules. Key excerpt:

```
You are a senior social-media copywriter. Your goal: maximize REACH,
FOLLOWERS, COMMENTS, and SAVES on the brand's actual social presence.
Not shares. Not vanity likes. Real engagement that compounds over time.

═══════════════════════════════════════════════════════════════
INPUTS — your full context
═══════════════════════════════════════════════════════════════

BRAND PROFILE (voice, tone, values — your soft constraint):
{user_context}

REFINED BRIEF (the user's actual intent — primary source of truth):
{refined_brief}

⚠️ THE REFINED BRIEF IS A STRATEGY DOCUMENT, NOT POST COPY.
Labelled sections — USER GOAL, TOPIC, AUDIENCE, KEY MESSAGE, ANGLE,
SUPPORTING POINTS, TONE, SOURCES REFERENCED, USER LINKS, VISUAL HINT,
CONSTRAINTS, USER INPUT QUALITY, ASSUMPTIONS MADE — are instructions
FOR YOU. They are NEVER content to copy into the post.

RESEARCH REPORT:
{research_json}

CULTURAL CALENDAR (today + tomorrow, India + USA):
{cultural_calendar_block}

PLATFORMS: {platforms_str}
Festival alert active: {has_festival_alert}

═══════════════════════════════════════════════════════════════
HARD RULES (NON-NEGOTIABLE)
═══════════════════════════════════════════════════════════════

1. ANTI-HALLUCINATION.
   • Never emit any claim listed in research.do_not_claim.
   • Numbers / percentages / dollar figures / dates / customer counts /
     product names must trace to refined brief, DNA, uploaded doc tag,
     or research.sources.

2. CHAR CAPS (server truncates anything over).
     LinkedIn:  ≤ 2800
     Twitter/X: ≤ 270
     Facebook:  ≤ 2200
     Instagram: ≤ 2100

3. HASHTAG CAPS (server enforces).
     LinkedIn:  0-5
     Twitter/X: 0-2
     Facebook:  0-3
     Instagram: 8-15

4. NO SHARE-BAIT.
     Banned: "share this", "tag someone who", "send this to your team",
     "quote this with your take", "RT if you agree", "spread the word"

5. DISTINCT ANGLES — each variant maps to a DIFFERENT
   research.angles_to_test entry.

6. BRAND VOICE — read brand_tone + brand_values from DNA. Match them.
   No "[Brand] is the leader in…" press-release voice.

7. NO BUZZWORD CORPORATE-SPEAK.
     Banned: empower / democratize / leverage / unlock / transform /
     accelerate / unify / seamlessly / end-to-end / next-generation /
     best-in-class / streamline / drive (efficiency) / revolutionize /
     game-changer / paradigm shift.

8. NO LAZY CTA.
     Banned: "Comment below.", "Let us know in the comments.",
     "We want to hear your vision.", "Share your thoughts.",
     "What are your thoughts?"

9. REAL URLS ONLY — NEVER USE PLACEHOLDER LINKS.
   URL sources in priority order:
     (a) USER LINKS — URLs the user typed in the brief (HIGHEST)
     (b) research.referenced_entities[].url — per-entity homepages
         for "top N" / comparison briefs
     (c) research.sources[].url — Google-grounded citations
     (d) Brand DNA URL fields — website_url, product_url, social_links.*

   BANNED: "[Insert Link N]", "[Link]", "[URL]", "[your website]",
   "[Insert link here]", "(link)", "https://example.com",
   "https://placeholder.com".

   If real URLs run out, write fewer items — never fill with placeholders.
   Format URLs naked (no markdown).

═══════════════════════════════════════════════════════════════
SOFT GUIDANCE — pick what fits the brief, ignore the rest
═══════════════════════════════════════════════════════════════

HOOK MENU (9 patterns to choose from per variant — see source for full list)
CTA MENU (6 patterns)
FORMATTING USE / AVOID guides
PLATFORM-SPECIFIC LENGTH NORMS

═══════════════════════════════════════════════════════════════
OUTPUT — strict JSON, per platform
═══════════════════════════════════════════════════════════════
{
  "recommendation": {
    "best_variant": "viral_reach | high_interaction | follower_growth | festival_variant",
    "reasoning":    "...",
  },
  "content": {
    "linkedin": {
      "viral_reach":      "<post text>",
      "high_interaction": "<post text>",
      "follower_growth":  "<post text>",
      "festival_variant": "<post text>"   (only when festival_alerts non-empty)
    },
    "twitter":   { ... },
    "facebook":  { ... },
    "instagram": { ... }
  }
}
```

Full text at `ai_service.py:1091-1390`.

---

## A.5 Image Generation Prompt (Direct Bypass Path) — Full Text

**File:** `services/image_agent_v4.py` · function `_build_direct_image_prompt`
**Model:** `gemini-3.1-flash-image` · **Temperature:** 1.5

```
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
```

---

## A.6 Few-Shot Reference Wrapper Text

After the main prompt above, the following section headers and per-example intros are interleaved with the 8 reference images. This is built dynamically by `_build_fewshot_contents(variant_idx)` in `image_agent_v4.py:327`.

### Section header (GOOD)

```
════════════════════════════════════════════════════════════════════
GOOD EXAMPLES — input → output → rationale triples (Set A)
════════════════════════════════════════════════════════════════════
Below are FIVE worked examples. Each shows: the kind of CAMPAIGN BRIEF
a user would have written, followed by the EXPECTED IMAGE we would
produce for that brief, followed by WHY this image is good. Study the
pattern — for the real brief above, you should produce an image of
comparable craft, comparable restraint, and a composition that fits
the brief as cleanly as these examples fit theirs.
```

### Per-example intro (GOOD, repeated 5×)

```
──── GOOD EXAMPLE N ────

EXPECTED USER INPUT (campaign brief for this example):
  {expected_input}

EXPECTED IMAGE we would produce for that input:
```

`{expected_input}` is substituted from the `_GOOD_SETS` constant. Example for Set A example 1:

> *"PwC just sold its Indirect Tax technology platform Edge to Fonoa. Announce this strategic deal on LinkedIn to enterprise tax and finance leaders. Tone: corporate, authoritative, professional services firm."*

Then the actual PIL image is sent as raw PNG bytes (not as a path).

### After-image rationale (GOOD, repeated 5×)

```
WHY THIS IMAGE IS GOOD FOR THAT INPUT:
  {rationale}
```

Example rationale for the PwC example:

> *"GOOD because: type IS the design. The full peach-gradient canvas holds only the bold serif headline and one minimal brand graphic. Zero imagery, zero glass, zero 3D. Brand wordmark is REAL, used ONCE, top-left. Generous breathing room. The composition trusts the message — exactly what a senior designer at PwC's level would produce."*

### Section header (BAD)

```
════════════════════════════════════════════════════════════════════
BAD ANTI-EXAMPLES — input → BAD output → what went wrong + why bad
════════════════════════════════════════════════════════════════════
Below are THREE worked anti-examples. Each shows: the kind of brief a
user wrote, the BAD image the model produced (which we do NOT want),
and a precise breakdown of WHAT WENT WRONG and WHY THE IMAGE IS BAD.
Do NOT produce anything that resembles these failure modes when you
design the final image for the real brief above.
```

### Per-anti-example intro (BAD, repeated 3×)

```
──── BAD ANTI-EXAMPLE N ────

EXPECTED USER INPUT for this anti-example:
  {expected_input}

IMAGE THE MODEL ACTUALLY PRODUCED (this is the OUTPUT WE DO NOT WANT):
```

### After-image breakdown (BAD, repeated 3×)

Verbatim from the `_BAD_EXAMPLES` constant. Example for BAD #1:

```
WHAT WENT WRONG:
  • TYPOS RENDERED AS TEXT — 'Compotazs' (should be 'Components'), 'Data
    ranting' (should be 'Routing'), 'Layoout' (should be 'Layout').
  • DUPLICATE ICONS — 'REST API' appears twice.
  • DUPLICATE BRAND MENTION — 'The Zyntegrate Platform' label PLUS the
    bottom-right 'Zyntegrate Autonomous Integration Platform' wordmark.
  • DEFAULT AI ARCHETYPE — heavy 3D glass cube on a dark gradient with
    blue/green glow on a void background.

WHY THIS IS BAD: misspelled text in the image is an automatic reject. The
AI-render aesthetic screams 'machine output, not designer work'. The model
defaulted to its built-in visualisation style instead of considering whether
that style suited the brief at all. DO NOT produce anything like this.
```

### End closer

```
════════════════════════════════════════════════════════════════════
END OF REFERENCES.
════════════════════════════════════════════════════════════════════
If a brand-logo image is attached AFTER this point, treat it per [R4]
(use exactly or omit). Now design the FINAL image for the real
campaign brief at the top of this request:
  • Match the craft, restraint, and composition discipline of the
    GOOD examples above.
  • Do NOT replicate any of the failure modes shown in the BAD
    anti-examples.
  • Output ONE high-resolution social-media image only.
```

---

## A.7 Image-Check Agent Prompt (Audit)

**File:** `services/image_check_agent.py`
**Model:** `gemini-2.5-flash` · **Temperature:** 0.2 · **Local-only**

```
You are auditing an AI-generated social-media post image. Your job is to find
specific quality problems so they can be fixed in the next generation.

Inspect the attached image and report EVERY issue you can see. Be specific —
quote exact misspelled words, name exactly which elements repeat, list every
hex code or meta-data string visible.

Categories to check (in priority order):

  1. TYPOS — misspelled or hallucinated words rendered on the image. Quote
     the exact rendered text and (if you can infer it) the word it should be.

  2. DUPLICATED ELEMENTS — repeated icons, repeated logos (same or similar
     brand mark drawn more than once), repeated headlines, repeated phrases,
     or multiple instances of any element that should appear once.

  3. HEX CODES OR PALETTE META-DATA RENDERED AS TEXT — strings like
     "#FF4500", "#212B36", "RGB(255,69,0)", or labels like "Brand Color",
     "Inter Family", "Typography", "Tone", "Primary Colour", "Aspect ratio".
     Palette swatch grids drawn on the post.

  4. INVENTED / HALLUCINATED BRAND MARKS — fake wordmarks for real third-
     party brands (Salesforce, AWS, Google, Meta, OpenAI, Slack, etc.) where
     the rendered mark does not match the real company's official logo.

  5. AI-RENDER AESTHETIC — heavy 3D glass cubes, glow-on-void backgrounds,
     dark gradient + neon, generic isometric infographic with floating UI
     cards. Note if the image looks unmistakably AI-generated rather than
     designed by a human.

  6. GIBBERISH INSIDE DEVICE SCREENS — when the image contains a laptop,
     phone, or tablet mockup whose screen text is hallucinated nonsense.

  7. BROKEN PUNCTUATION — unclosed apostrophes, mismatched quotes, stray
     decorative dots / plus signs treated as filler.

CAMPAIGN CONTEXT (for reference — issues are still issues even if they
"match" the brief):

CAMPAIGN BRIEF:
{refined_brief}

CAPTION published with this image:
{copy_block}

OUTPUT FORMAT
Return a short bullet list. One bullet per concrete issue. Use the format:

  • <category> — <specific finding>

If you find no issues, output exactly the single line:

  • No issues detected

Do NOT include any other prose, explanation, or summary. Bullets only.
```

---

# Appendix B — Comprehensive Hyperparameter Map (every agent, every setting)

This appendix is the single source of truth for every hyperparameter set on every agent across the pipeline. Empty cells indicate the value uses the Gemini API default (the next table after this one documents what those defaults are).

## B.1 Per-agent hyperparameter matrix

| Agent | Model | Temp. | Top-p | Top-k | Max tokens | Candidates | Safety | Tools | Response modal. | Image config | Streaming | Mime type | Local-only |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Brief Guard | (regex — no LLM) | — | — | — | — | — | — | — | — | — | — | — | — |
| Refiner | `gemini-flash-lite-latest` | 0.7 | 0.95 | 40 | default | 1 | default | — | TEXT | — | non-stream | not set | No |
| Cultural Calendar | `gemini-flash-lite-latest` | 0.2 | 0.95 | 40 | default | 1 | default | google_search | TEXT | — | non-stream | n/a* | No |
| Researcher (grounded) | `gemini-flash-lite-latest` | 0.3 | 0.95 | 40 | default | 1 | default | google_search | TEXT | — | non-stream | n/a* | No |
| Researcher (offline fallback) | `gemini-flash-lite-latest` | 0.7 | 0.95 | 40 | default | 1 | default | — | TEXT | — | non-stream | not set | No |
| Content Agent (Copywriter) | `gemini-flash-lite-latest` | 0.7 | 0.95 | 40 | default | 1 | default | — | TEXT | — | non-stream | not set | No |
| Critic | `gemini-flash-lite-latest` | 0.7 | 0.95 | 40 | default | 1 | default | — | TEXT | — | non-stream | not set | No |
| **Image Generation (v4)** | `gemini-3.1-flash-image` → fallback `gemini-2.5-flash-image` | **1.5** | **0.95** | **40** | default | 1 | default | — | `["IMAGE","TEXT"]` | aspect from chip, `image_size="1K"` | **stream** | not set | No |
| Art Director (bypassed) | `gemini-flash-lite-latest` | 0.95 | 0.95 | 40 | default | 1 | default | — | TEXT | — | non-stream | not set | No |
| Image-Check Agent (audit) | `gemini-2.5-flash` | 0.2 | **0.95** | **40** | default | 1 | default | — | TEXT | — | non-stream | not set | **Yes** |
| Sentiment Analyser | `gemini-flash-lite-latest` | 0.0 | 0.95 | 40 | default | 1 | default | — | TEXT | — | non-stream | not set | No |
| Comment Reply Agent | `gemini-flash-lite-latest` | 0.7 | 0.95 | 40 | default | 1 | default | — | TEXT | — | non-stream | not set | No |
| Bulk Copywriter | `gemini-flash-lite-latest` | 0.7 | 0.95 | 40 | default | 1 | default | — | TEXT | — | non-stream | not set | No |
| Twitter Optimiser | `gemini-flash-lite-latest` | 0.7 | 0.95 | 40 | default | 1 | default | — | TEXT | — | non-stream | not set | No |
| Planner | `gemini-flash-lite-latest` | 0.7 | 0.95 | 40 | default | 1 | default | — | TEXT | — | non-stream | not set | No |
| Visualist (legacy v2) | `gemini-flash-lite-latest` | 0.7 | 0.95 | 40 | default | 1 | default | — | TEXT | — | non-stream | not set | No |

`*` Gemini does not allow `response_mime_type="application/json"` when `google_search` is enabled — strict-JSON discipline relies on prompt-side instructions instead.

## B.2 What the "default" values are

| Hyperparameter | Gemini API default | Effect on output |
|---|---|---|
| `temperature` | `1.0` (text models) / `1.0` (image models, empirical) | Higher = more diverse / less deterministic; lower = more consistent |
| `top_p` (nucleus sampling) | `0.95` | Considers tokens up to 95 % cumulative probability |
| `top_k` | `40` (model-family-specific) | Considers top 40 candidate tokens at each step |
| `max_output_tokens` | model-specific (e.g. `8 192` for flash-lite) | Hard cap on response length |
| `candidate_count` | `1` | Number of independent completions returned per call |
| `safety_settings` | `BLOCK_MEDIUM_AND_ABOVE` for each of HARM_CATEGORY_HARASSMENT, HATE_SPEECH, SEXUALLY_EXPLICIT, DANGEROUS_CONTENT | Blocks responses likely to trip safety thresholds |
| `thinking_config` | not enabled by default for `gemini-3.1-flash-image` | When enabled, model can produce internal reasoning |

## B.3 Custom (non-LLM) hyperparameters

These are application-level settings that shape behaviour but are not Gemini API parameters.

### Brief Guard

| Parameter | Value | File |
|---|---|---|
| `MIN_WORD_COUNT` | `4` | `brief_guard.py` |
| `MIN_CHAR_COUNT` | `15` | `brief_guard.py` |
| `_ALPHA_RATIO_MIN` | `0.45` (45 % alphabetical density floor) | `brief_guard.py` |
| `BANNED_TERMS` | ~70 entries across 6 categories | `brief_guard.py` |
| `GENERIC_PATTERNS` | 8 regex patterns | `brief_guard.py` |

### Cultural Calendar

| Parameter | Value | File |
|---|---|---|
| Cache key | `YYYY-MM-DD` (one cache entry per UTC day) | `ai_service.py` |
| Cache TTL | day-level (cache rolls at UTC midnight) | `ai_service.py` |
| Allow-list scope | India + USA nation-wide festivals only | `ai_service.py` |

### Content Agent — Server-Enforced Caps

| Parameter | LinkedIn | X | Facebook | Instagram | File |
|---|---|---|---|---|---|
| Char cap (safety-buffered) | `2 800` | `270` | `2 200` | `2 100` | `ai_service.py` |
| Hashtag cap (max) | `5` | `2` | `3` | `15` | `ai_service.py` |

### Image Agent v4

| Parameter | Value | File |
|---|---|---|
| `DEFAULT_MODEL` | `"gemini-3.1-flash-image"` (env `IMAGE_AGENT_V4_MODEL`) | `image_agent_v4.py` |
| `FALLBACK_MODEL` | `"gemini-2.5-flash-image"` | `image_agent_v4.py` |
| `BYPASS_ART_DIRECTOR` | `True` (env-toggleable) | `image_agent_v4.py` |
| `_FEWSHOT_DIR` | `apps/backend/assets/fewshot/` | `image_agent_v4.py` |
| `n_variants` | `3` (passed from orchestrator) | `image_agent_v4.py` |
| Variants per fewshot Set | 5 GOOD per Set (A/B/C) × 3 Sets = 15 unique GOOD references | `image_agent_v4.py` |
| Anti-examples | 3 BAD references (constant across all variants) | `image_agent_v4.py` |
| Logo budget | Up to 1 PIL image appended at end | `image_agent_v4.py` |
| Max images per request (Gemini cap) | 14 (10 object + 4 character) | `gemini-3.1-flash-image` spec |
| Images per request (our usage) | 8–9 (5 good + 3 bad + 0–1 logo) | computed |

### Image-Check Agent

| Parameter | Value | File |
|---|---|---|
| `AUDIT_MODEL` | `"gemini-2.5-flash"` (env `IMAGE_CHECK_MODEL`) | `image_check_agent.py` |
| `ThreadPoolExecutor` max_workers | `min(3, len(visuals))` | `image_check_agent.py` |
| Per-future timeout | `60 s` | `image_check_agent.py` |
| S3 fetch timeout | `15 s` | `image_check_agent.py` |
| Brief / caption truncation | `2 000` chars each | `image_check_agent.py` |
| Gate | `not bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))` | `image_check_agent.py` |

### CSV Logger (Local Only)

| Parameter | Value | File |
|---|---|---|
| `_CSV_FILENAME` | `"fewshot_test_results_temperature_1.5.csv"` | `csv_logger.py` |
| Encoding | `utf-8-sig` (BOM for Excel compatibility) | `csv_logger.py` |
| `_HEADER` columns | 29 | `csv_logger.py` |
| Gate | `not bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))` | `csv_logger.py` |

## B.4 Orchestrator-level settings

| Parameter | Value | File |
|---|---|---|
| Concurrency model (text) | sequential — refiner → cultural → researcher → content → critic | `ai_service.py::generate_strategic_content` |
| Concurrency model (image) | 3 variants in parallel via `asyncio.gather` | `image_agent_v4.py::generate_image_variants_v4` |
| Per-stage timing instrumentation | recorded on every call, returned as `stage_times` field | `ai_service.py` |
| Total per-request token budget | not explicitly capped | none |

---

*End of Section 2.*
