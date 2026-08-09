# Pipelyt — Content Generation Pipeline (End-to-End)

> Document scope: every stage that runs between the user hitting **Generate** in
> the dashboard and a finished post payload being returned to the frontend.
> Covers brief validation, DNA resolution, the refiner, cultural calendar,
> researcher, copywriter, critic, and post-processing. The image-generation
> pipeline (Art Director v2 + Gemini 3.1 Flash Image) is referenced where it
> intersects the text flow but documented in detail in
> `docs/art_director_pipeline.md`.

---

## 0. High-level flow

```
┌────────────────────────────────────────────────────────────────────────┐
│  USER INPUT                                                            │
│    brief_text · platforms[] · post_type · selected_dna · aspect_ratio  │
└────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
        Stage 1 — Brief Guard (rule-based, local, <10 ms)
                  brief_guard.validate_brief()
                                  │ pass
                                  ▼
        Stage 2 — Business DNA Resolution
                  resolve_business_dna(selected_dna)
                                  │
                                  ▼
        Stage 3 — Refiner Agent  (gemini-flash-lite-latest, STEP-0 semantic guard)
                  ai_service._refine_brief_agent()
                                  │ valid=true
                                  ▼
        Stage 4 — Cultural Calendar  (cached daily, web-grounded)
                  ai_service._get_cultural_calendar()
                                  │
                                  ▼
        Stage 5 — Research Agent  (web-grounded, intent-driven)
                  ai_service._research_agent()
                                  │
                                  ▼
        Stage 6 — Content Agent  (4 variants × N platforms, R1-R9)
                  ai_service._content_agent()
                                  │
                                  ▼
        Stage 7 — Post-Processing  (placeholder strip, hashtag/char caps)
                  ai_service._apply_content_post_processing()
                                  │
                                  ▼
        Stage 8 — Critic Agent  (optional growth-fitness audit)
                  ai_service._critic_agent()
                                  │
                                  ▼
        Stage 9 — Post-type branch
                  ┌──────────────────────────────────────────────┐
                  │ text     → return payload                    │
                  │ image    → image_agent_v4.generate_image_    │
                  │            variants_v4()  (AD v2 → Imagen)   │
                  │ video    → return payload (video gen TBD)    │
                  │ document → return payload (doc gen TBD)      │
                  └──────────────────────────────────────────────┘
                                  │
                                  ▼
        Final JSON payload → frontend
```

Total wall-clock for a typical text-only LinkedIn brief: 18–25 s.
Image post adds 25–35 s for AD v2 + image model + critic.

---

## 1. Inputs

| Field           | Type    | Required | Description                                                              |
| --------------- | ------- | -------- | ------------------------------------------------------------------------ |
| `brief_text`    | string  | yes      | Raw campaign brief typed by the user                                     |
| `platforms`     | array   | yes      | Subset of `["linkedin","twitter","facebook","instagram"]`                 |
| `post_type`     | string  | yes      | `"image"` \| `"text"` \| `"video"` \| `"document"`                       |
| `selected_dna`  | string  | no       | DNA id chosen from dropdown. `"__none__"` = run without DNA              |
| `aspect_ratio`  | string  | no       | `"1:1"` (default) \| `"4:5"` \| `"16:9"` \| `"9:16"` — image post only   |

The frontend wires these through `App.jsx` → `Dashboard.jsx` → POST `/api/generate-content`.

---

## 2. Stage 1 — Brief Guard (rule-based)

**File:** `apps/backend/services/brief_guard.py`
**Cost:** local regex only, no LLM call.
**Failure mode:** raises HTTP 422 with a user-facing message; frontend renders inline below the textarea.

The guard runs four checks in order. The first failure short-circuits the rest.

### 2.1 Scenarios + exact user-facing messages

| #   | Trigger                                             | Example brief                                            | User-facing message                                                                                                                                                                                                                                                                                                                                                                |
| --- | --------------------------------------------------- | -------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1.1 | Empty brief                                         | `""` or whitespace                                       | `Please enter a campaign brief before generating.`                                                                                                                                                                                                                                                                                                                                 |
| 1.2 | Too short (< 4 words or < 15 chars)                 | `launch new product`                                     | `Your brief is too short. Please describe what you want to promote, who it's for, and why it matters. Example: 'Launch our new Q2 pricing with a focus on mid-market teams — emphasize the 40% time savings.'` *(generic — no dynamic word count surfaced)*                                                                                                                          |
| 1.3 | Banned policy term (sexual / violent / illegal etc) | `sell cocaine kits for college students`                 | `We can't generate marketing content around this topic. Your brief contained language our policy doesn't support — please rewrite with a business-appropriate focus (e.g. a product, service, offer, or announcement relevant to your audience).`                                                                                                                                  |
| 1.4 | Low alpha ratio (mostly emoji / symbols)            | `🚀🚀🚀🚀🚀🚀 #@$ 12345`                                  | `Your brief has too few real words. Please describe the campaign in plain English — what you're promoting, who it's aimed at, and what the key benefit is.`                                                                                                                                                                                                                       |
| 1.5 | Generic placeholder                                  | `create a post`                                           | `Your brief is too generic. Please describe the actual campaign — what you're promoting, the audience, and the key message — instead of just a command. Example: 'Announce our new AI pricing insights feature to mid-market SaaS marketing leaders, highlighting how it replaces their weekly forecast meetings.'`                                                              |

### 2.2 Banned-term list (70+ terms across 6 categories)

```python
BANNED_TERMS = [
    # Sexual: sex, sexual, sexy, porn, nude, ... onlyfans, nsfw ...
    # Violence: kill, murder, bomb, shooter, terrorist, beheading, ...
    # Drugs/illegal: cocaine, heroin, meth, cartel, illegal drugs, ...
    # Hate/slurs: nazi, kkk, white supremacy
    # Scams/fraud: ponzi, pyramid scheme, money laundering, phishing
    # Weapons: automatic weapon, machine gun, silencer
]
```

Match is `\b...\b` case-insensitive — substring matches are intentional (e.g.
"masturbat" catches every conjugation).

### 2.3 Generic-placeholder regex

```python
GENERIC_PATTERNS = [
    r"^\s*create\s+(?:a|an|the)?\s*(?:post|ad|content|image|video)\s*\.?\s*$",
    r"^\s*generate\s+(?:a|an|the)?\s*(?:post|ad|content|image|video)\s*\.?\s*$",
    r"^\s*make\s+(?:a|an|the)?\s*(?:post|ad|content|image|video)\s*\.?\s*$",
    r"^\s*(?:post|write)\s+(?:about\s+)?(?:something|anything)?\s*\.?\s*$",
    r"^\s*(?:test|testing|hello|hi|hey|yo|asdf|lorem ipsum)\s*\.?\s*$",
    r"^\s*create\s+\w+\s*\.?\s*$",          # "create spenzo"
    r"^\s*generate\s+\w+\s*\.?\s*$",
    r"^\s*(?:do|pls|please)\s+.{0,20}$",
]
```

---

## 3. Stage 2 — Business DNA Resolution

**File:** `apps/backend/services/dna_service.py`

| Selection                | Resolution                                                                                       | `user_context` passed downstream                                       |
| ------------------------ | ------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| `"__none__"`             | Admin chose to run without DNA.                                                                  | `""` (empty) → refiner runs in **no-DNA** mode                         |
| DNA id (admin-owned)     | Loads DNA from Postgres + concatenates uploaded knowledge-base files                              | Full DNA JSON pretty-printed + KB blocks                               |
| DNA id (team-member)     | Looks up which **org-level** DNA the team member inherits, then loads that DNA                    | Same as admin case                                                     |
| Missing/invalid DNA id   | Falls back to empty string + log warning. Refiner treats it as no-DNA.                            | `""`                                                                   |

`user_context` is the single string the refiner, researcher, and copywriter all
see. Never edit it mid-pipeline.

---

## 4. Stage 3 — Refiner Agent

**File:** `apps/backend/services/ai_service.py::_refine_brief_agent`

| Setting             | Value                              |
| ------------------- | ---------------------------------- |
| Model               | `gemini-flash-lite-latest`         |
| Temperature         | `0.7`                              |
| top_p               | `0.95`                             |
| top_k               | `40`                               |
| Response MIME       | `application/json` (no grounding)  |
| Web search          | off                                |
| Streaming           | off                                |
| Typical latency     | 1.5–3 s                            |

### 4.1 Role

1. Run a **semantic STEP-0 validation** (catches what the regex guard misses —
   paraphrased harmful intent, briefs that look fine on a regex but have no
   marketable subject).
2. If valid, emit a labelled **Strategic Brief** the downstream agents anchor to.

### 4.2 Semantic rejection — STEP-0 categories

| Category    | Triggers                                                                                                                                | Generic user-facing message *(emitted by Python wrapper, refiner's `rejection_message` is logged but never shown)*                                                                                |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `harmful`   | Violence, self-harm, sexual content, illegal activity, hate — **paraphrased** so it escaped the regex                                    | `We can't generate marketing content for briefs that involve violence, sexual content, or illegal activity. Please rewrite with a business-appropriate focus.`                                                              |
| `generic`   | No actual campaign concept ("write something about our company", "test")                                                                | `Your brief is too generic. Please describe what you want to promote, the audience, and the key message — not just a command like 'create a post'.`                                                                       |
| `no_utility`| No plausible marketing use case for *any* business ("what is 2+2", personal diary, code/legal/medical advice request)                    | `This brief doesn't describe content any business could plausibly market. Please rewrite as a campaign idea — what you want to promote, who it's for, and why it matters.`                                                  |
| `off_brand` | (Legacy alias of `no_utility` for cached responses)                                                                                     | Same as `no_utility`                                                                                                                                                                                |
| `invalid`   | Fallback                                                                                                                                | `Please provide a clearer campaign brief describing what to promote, who it's for, and the key message.`                                                                                                                  |

⚠️ Cross-topic briefs (e.g. a marketing SaaS posting about *general AI industry
news*) are **allowed** — they are not rejected for being "off-brand vs DNA".
DNA contributes voice only.

### 4.3 Strategic Brief output sections

```
USER GOAL
TOPIC
AUDIENCE
KEY MESSAGE
ANGLE
SUPPORTING POINTS         ← every point sourced [user brief] | [DNA: field] | [doc: file]
TONE
SOURCES REFERENCED
USER LINKS                ← every URL the user typed, verbatim, one per line
VISUAL HINT
CONSTRAINTS
USER INPUT QUALITY
ASSUMPTIONS MADE
```

`USER LINKS` is critical — the copywriter's Rule 9a treats these as the
highest-priority URLs for in-post links.

### 4.4 Refiner output JSON

Valid:
```json
{ "valid": true, "refined_brief": "<labelled-sections text block>" }
```

Rejected (Python wrapper substitutes generic message before raising
`BriefRejected`):
```json
{ "valid": false, "rejection_category": "harmful|generic|no_utility", "rejection_message": "..." }
```

### 4.5 Full prompt — see `ai_service.py` L357–576

(Reproduced verbatim in `docs/refiner_prompt.md` if a clean copy is needed
without surrounding code.)

---

## 5. Stage 4 — Cultural Calendar

**File:** `apps/backend/services/ai_service.py::_get_cultural_calendar`

| Setting           | Value                                                                       |
| ----------------- | --------------------------------------------------------------------------- |
| Model             | `gemini-flash-lite-latest`                                                  |
| Temperature       | `0.2`                                                                       |
| top_p / top_k     | `0.95 / 40`                                                                 |
| Web search        | **on** (Gemini google_search tool)                                          |
| Cache             | `_CULTURAL_CACHE` keyed by UTC date — **one call per day** across all users |
| Latency           | 4–7 s on cold miss; ~0 ms on hit                                            |

### 5.1 Job

Find ONLY major nation-wide cultural moments observed today or tomorrow in
**India** and **USA** that a mainstream marketer would actually acknowledge.

### 5.2 Strict include rules

- India: national gazetted public holiday OR major Hindu/Muslim/Sikh/Christian
  festival broadly observed across multiple regions.
- USA: federal public holiday OR top-tier mainstream marketing day
  (Valentine's, St. Patrick's, Halloween, Super Bowl Sunday, Black Friday).

### 5.3 Explicitly excluded

- Single-state or single-city holidays
- Regional / niche festivals (Onam outside Kerala, Pongal outside Tamil Nadu)
- "National X Day" novelty days (National Pizza Day)
- UN/WHO international observance days
- Minor religious vrats / fasts
- Anything that cannot be grounded in a reliable source

### 5.4 Output schema

```json
{
  "today_date": "2026-06-08",
  "tomorrow_date": "2026-06-09",
  "india": [{"name": "...", "date": "...", "type": "...", "note": "..."}],
  "usa":   [{"name": "...", "date": "...", "type": "...", "note": "..."}]
}
```

Empty arrays are the expected default on quiet days.

---

## 6. Stage 5 — Research Agent

**File:** `apps/backend/services/ai_service.py::_research_agent`

| Setting          | Value (grounded path)                       |
| ---------------- | ------------------------------------------- |
| Model            | `gemini-flash-lite-latest`                  |
| Temperature      | `0.3`                                       |
| top_p / top_k    | `0.95 / 40`                                 |
| Web search       | **on** (google_search grounding)            |
| Streaming        | off                                         |
| Latency          | 6–12 s (3–6 real Google queries)            |

If the grounded call returns an error dict, falls back to an **offline** prompt
at `temperature=0.7, web_search=False` (rare — network blip or quota).

### 6.1 Strategy — intent-driven, picked by the LLM

| Brief type                              | Search strategy                                                                                              |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| (A) Topic / news / trend                | Last 7 days news on that topic. Stay on topic. Do NOT pivot to product.                                       |
| (B) User's own product (DNA + product)  | Fresh angles + DNA-grounded facts + competitor news (last 7 days) under `competitor_news`.                    |
| (C) Brand + topic hybrid                | Combine A and B.                                                                                              |

### 6.2 Always-on auxiliary research

Runs on EVERY call regardless of brief type:

| Item                  | Source                                                                                            |
| --------------------- | ------------------------------------------------------------------------------------------------- |
| AUX-1 Cultural alerts | Read pre-fetched cultural calendar; fire `festival_alerts[]` ONLY when major festival + not in brief |
| AUX-2 Trending topics | 3-7 broader phrases in the user's industry                                                        |
| AUX-3 Trending hashtags | 3-10 grounded `#WithHash` formatted                                                              |
| AUX-4 Trending keywords | 3-10 grounded SEO/SMO phrases                                                                    |

### 6.3 LITERAL-QUERY RULE

If the brief contains a specific entity / product / person, that string is
passed **VERBATIM** as the first google_search query before any paraphrased
searches.

Example: brief says `"research Gemini 3.5 release"` → query #1 MUST be
`Gemini 3.5 release`.

### 6.4 Time window (strict)

| Brief language                                 | Source window                |
| ---------------------------------------------- | ---------------------------- |
| "today" / "right now" / "this morning"         | Last 24 h only               |
| "yesterday"                                    | Last 48 h                    |
| No window / "this week" / "recently"           | Last 7 days (default)        |
| "this month"                                   | Last 30 days                 |
| Specific date / month / year                   | Exact window                 |

### 6.5 Output schema

```json
{
  "target_audience": "...[src:1]",
  "trending_context": "... [src:1] [src:2]",
  "problem_solving_opportunity": "...",
  "company_product_analysis": "[DNA: tagline] ... [src:3]",
  "angles_to_test": ["angle1", "angle2", "angle3"],
  "do_not_claim": ["thing1", "thing2"],
  "trending_topics":   ["..."],
  "trending_hashtags": ["#one", "#two"],
  "trending_keywords": ["..."],
  "competitor_news": [{"competitor":"...","headline":"...","src":2,"published":"YYYY-MM-DD"}],
  "festival_alerts": [{"country":"india","festival_name":"Diwali","when":"today","suggested_angle":"..."}],
  "referenced_entities": [{"name":"...","url":"https://...","one_liner":"..."}],
  "sources": [{"id":1,"title":"...","url":"https://...","published":"YYYY-MM-DD"}],
  "grounding_confidence": "strong | moderate | speculative"
}
```

`referenced_entities` powers Rule 9b — list/roundup briefs ("top 10 AI tools")
get every entity's official homepage URL looked up here so the copywriter never
emits a placeholder link.

### 6.6 Citation rules

- Every concrete fact in `trending_context`, `target_audience`,
  `problem_solving_opportunity`, `company_product_analysis`, and
  `competitor_news` carries an inline `[src:N]` marker → `sources[]`.
- Brand/DNA-derived claims use `[DNA: field]` / `[doc: file]` / `[user brief]`.
- Hashtags ungroundable → drop. 2 grounded > 10 invented.
- Claims that cannot be grounded → push to `do_not_claim`.

---

## 7. Stage 6 — Content Agent (Copywriter)

**File:** `apps/backend/services/ai_service.py::_content_agent`

| Setting          | Value                                       |
| ---------------- | ------------------------------------------- |
| Model            | `gemini-flash-lite-latest`                  |
| Temperature      | `0.7`                                       |
| top_p / top_k    | `0.95 / 40`                                 |
| Web search       | off                                         |
| Response MIME    | `application/json`                          |
| Latency          | 8–14 s                                      |

### 7.1 Output shape

For each platform in the user's selection, produces **3 stable variants**
(plus an optional 4th):

| Key                  | Goal                                                  |
| -------------------- | ----------------------------------------------------- |
| `viral_reach`        | Saves + follows; broad-audience hook                   |
| `high_interaction`   | Comment-driven; specific decision/take question        |
| `follower_growth`    | Authority/depth; profile-click → follow                |
| `festival_variant`   | Emitted **only when** `research.festival_alerts != []` |

### 7.2 Hard rules (R1–R9)

| Rule | Constraint                                                                                                                  |
| ---- | --------------------------------------------------------------------------------------------------------------------------- |
| R1   | Anti-hallucination. Never emit anything in `research.do_not_claim`. Numbers/dates/dollar figures must be tagged.              |
| R2   | Char caps (server enforces): LinkedIn ≤ 2800 · Twitter/X ≤ 270 · Facebook ≤ 2200 · Instagram ≤ 2100.                          |
| R3   | Hashtag caps (server enforces): LinkedIn 0-5 · Twitter/X 0-2 · Facebook 0-3 · Instagram 8-15.                                  |
| R4   | No share-bait. Banned: "share this", "tag someone who", "RT if you agree", "spread the word", "quote this".                   |
| R5   | Distinct angles per variant — each maps to a different entry in `research.angles_to_test`.                                    |
| R6   | Brand voice — match DNA `brand_tone` + `brand_values`. No press-release "[Brand] is the leader in…" voice.                    |
| R7   | No buzzword corporate-speak. Banned list includes empower, democratize, leverage, unlock, seamlessly, transform, accelerate.  |
| R8   | No lazy CTA. Banned shapes: "Comment below.", "Let us know in the comments.", "What are your thoughts?".                       |
| R9   | Real URLs only. Sourcing priority: (a) USER LINKS (b) `research.referenced_entities[]` (c) `research.sources[].url` (d) DNA URLs. Placeholders like `[Insert Link 1]` are **banned** — the copywriter writes fewer items or uses a non-URL CTA. |

### 7.3 Soft guidance (model picks per variant)

- **Hook menu:** milestone, time-stamped news, audience question, stat + claim,
  bold POV, human story lead, pain-then-solve, product reveal + themed emoji,
  punchy contrast.
- **CTA menu:** arrow + URL, direct verb, open question, soft prompt for replies,
  personal close, thread continuation (X only).
- **Platform-native patterns:** LinkedIn line-break paragraphs, X under 400 chars,
  Facebook hook in first 480 chars, Instagram hook in first 125 chars.

### 7.4 Output JSON

```json
{
  "mode": "product | service | hybrid | topic",
  "mode_reason": "one sentence citing DNA / brief",
  "recommendation": {
    "best_variant": "viral_reach | high_interaction | follower_growth | festival_variant",
    "reason": "..."
  },
  "content": {
    "linkedin":  { "viral_reach": "...", "high_interaction": "...", "follower_growth": "..." },
    "twitter":   { "viral_reach": "...", "high_interaction": "...", "follower_growth": "..." },
    "facebook":  { "viral_reach": "...", "high_interaction": "...", "follower_growth": "..." },
    "instagram": { "viral_reach": "...", "high_interaction": "...", "follower_growth": "..." }
  }
}
```

`festival_variant` is added under every requested platform when
`festival_alerts != []`.

### 7.5 Full prompt — see `ai_service.py` L1208–1546

---

## 8. Stage 7 — Post-Processing

**File:** `apps/backend/services/ai_service.py::_apply_content_post_processing`

Runs deterministically over every variant on the Python side — **belt-and-braces
guarantees** even when the LLM strays.

| Step                                  | Action                                                                                                          |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Unicode normalization                  | NFKD strip of Math Bold / Sans Bold / decorative Unicode → plain ASCII letters                                  |
| Placeholder URL strip                  | Removes any `[Insert Link N]`, `[URL]`, `https://example.com`, `https://placeholder.com`, etc.                   |
| Hashtag cap                            | Counts `#WithHash` per post; trims any beyond platform's `HASHTAG_CAP_PER_PLATFORM`                              |
| Char cap                               | Hard-truncates anything beyond `CHAR_CAP_PER_PLATFORM` (LinkedIn 2800 / X 270 / FB 2200 / IG 2100)               |
| Trim                                   | Collapses runaway whitespace                                                                                     |

---

## 9. Stage 8 — Critic Agent (growth-fitness audit)

**File:** `apps/backend/services/ai_service.py::_critic_agent`

| Setting          | Value                                       |
| ---------------- | ------------------------------------------- |
| Model            | `gemini-flash-lite-latest`                  |
| Temperature      | `0.7`                                       |
| top_p / top_k    | `0.95 / 40`                                 |
| Web search       | off                                         |
| Response MIME    | `application/json`                          |

Verifies the full payload against PIPELYT STYLE RULES:

1. Strong hook (not setup) on every variant.
2. No banned corporate phrases.
3. Hashtag counts within caps.
4. CTAs reply-bait or save-bait (not "share this" / "follow us").
5. Factually aligned with the brief.
6. 3 variants structurally different.
7. Visuals (when present) distinct in layout.

Output:
```json
{ "is_valid": true|false, "critique": "...", "adjustments": "..." }
```

Critic runs **read-only** in the current pipeline — its `adjustments` are
attached to the response payload as `_critic` but not auto-applied. Future work:
loop-back when `is_valid=false`.

---

## 10. Stage 9 — Post-type branch

After Stages 1–8 the text payload exists. What happens next depends on
`post_type`:

| `post_type`  | Branch                                                                                                                                                          |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `"text"`     | Return text payload as-is. Frontend renders the 3 variants per platform.                                                                                         |
| `"image"`    | Call `image_agent_v4.generate_image_variants_v4()` — 3 parallel variants, each going through Art Director v2 (Gemini 2.5 Flash) → Gemini 3.1 Flash Image.        |
| `"video"`    | Currently returns the text payload only — video generation is on the roadmap.                                                                                    |
| `"document"` | Currently returns the text payload only — PDF/document generation is on the roadmap.                                                                              |

### 10.1 Image branch — caption distribution

`image_agent_v4._select_caption_block_for_image()` picks the caption block to
embed into the Art Director prompt:

1. If LinkedIn is in `platforms`, use the LinkedIn block (priority).
2. Else use the first available platform's block.

Each of the 3 image variants gets a **different caption variant** as its hook:

| Image variant | Caption variant key | Source                                          |
| ------------- | ------------------- | ----------------------------------------------- |
| V0            | `viral_reach`       | `content[platform]["viral_reach"]`              |
| V1            | `high_interaction`  | `content[platform]["high_interaction"]`         |
| V2            | `follower_growth`   | `content[platform]["follower_growth"]`          |

The Art Director then translates the caption into a `viewer_takeaway` and
`final_render_prompt` for Gemini 3.1 Flash Image.

See `docs/art_director_pipeline.md` for the full image branch.

---

## 11. Worked scenarios

### 11.1 With DNA — product launch on LinkedIn + X

| Input                      |                                                                                  |
| -------------------------- | -------------------------------------------------------------------------------- |
| `brief_text`               | `Announce our new AI pricing insights feature for mid-market SaaS marketers. Emphasize how it replaces their weekly forecast meetings. https://spenzo.io/pulse` |
| `platforms`                | `["linkedin","twitter"]`                                                          |
| `post_type`                | `"image"`                                                                         |
| `selected_dna`             | `"dna_spenzo_v3"` (admin DNA, tagline = "Pricing intelligence for SaaS")          |
| `aspect_ratio`             | `"1:1"`                                                                           |

Pipeline:
1. **Brief Guard** — 21 words, no banned terms, no generic pattern → pass.
2. **DNA resolution** — loads Spenzo DNA + KB.
3. **Refiner** — STEP-0 valid. Emits strategic brief with USER LINKS line
   `- https://spenzo.io/pulse`.
4. **Cultural calendar** — empty arrays for both countries today (June 8 is a
   quiet day).
5. **Research** — strategy B (DNA + own product). Pulls 4 sources, surfaces
   2 competitor moves under `competitor_news`, populates trending_hashtags
   `["#PricingStrategy", "#SaaSGrowth", "#RevOps"]`. No festival alert.
6. **Content Agent** — emits 3 variants × 2 platforms = 6 posts. LinkedIn
   `viral_reach` uses arrow + URL CTA pointing at `https://spenzo.io/pulse`
   (USER LINK, R9a). Twitter `high_interaction` asks a SaaS-pricing-specific
   question, ≤ 270 chars, 1 hashtag.
7. **Post-process** — caps OK, no placeholders to strip.
8. **Critic** — `is_valid: true`.
9. **Image branch** — LinkedIn block selected. V0 caption = LinkedIn
   viral_reach, V1 = high_interaction, V2 = follower_growth. AD v2 fires
   3× in parallel; image model returns 3 PNGs.

### 11.2 Without DNA — industry-news topic post

| Input                      |                                                                                  |
| -------------------------- | -------------------------------------------------------------------------------- |
| `brief_text`               | `Write about the latest Gemini 3.5 release and what it means for marketing teams this week.` |
| `platforms`                | `["linkedin","instagram"]`                                                        |
| `post_type`                | `"text"`                                                                          |
| `selected_dna`             | `"__none__"`                                                                       |

Pipeline:
1. **Brief Guard** — pass.
2. **DNA resolution** — `user_context = ""`.
3. **Refiner** — `dna_attached=no`. Source tags restricted to `[user brief]`.
   Refiner emits strategic brief with `AUDIENCE = "General audience interested
   in AI and marketing"`.
4. **Cultural calendar** — quiet day.
5. **Research** — strategy A (topic). LITERAL-QUERY rule fires:
   `Gemini 3.5 release` is query #1. Returns last-7-days sources, 5
   `referenced_entities` for tools mentioned across the sources.
6. **Content Agent** — `mode: "topic"`. No festival variant. LinkedIn variants
   are educational-explainer-flavored; Instagram variants use hashtag wall.
7. **Post-process**, **Critic**, return text payload.

### 11.3 With DNA + festival alert

| Input                      |                                                                                  |
| -------------------------- | -------------------------------------------------------------------------------- |
| `brief_text`               | `Promote our Diwali sale on PWC consulting workshops — 30% off all Q3 enrolments.` |
| `platforms`                | `["linkedin","twitter","instagram"]`                                              |
| `post_type`                | `"image"`                                                                         |
| `selected_dna`             | `"dna_pwc_v1"`                                                                    |

- Cultural calendar fires Diwali (India, today).
- Brief already mentions Diwali → research's `festival_alerts` is **empty**
  (suppression rule: only fire when brief doesn't already reference it).
- 3 variants per platform, no festival_variant key (brief is already on-theme).

### 11.4 Without DNA + festival alert (forgot the festival)

| Input                      |                                                                                  |
| -------------------------- | -------------------------------------------------------------------------------- |
| `brief_text`               | `Share a quick recap of our Q2 marketing analytics report.`                       |
| `platforms`                | `["linkedin"]`                                                                    |
| `post_type`                | `"text"`                                                                          |
| `selected_dna`             | `"__none__"`                                                                       |

- Cultural calendar shows Diwali (India, today). User's brief doesn't mention it.
- Research fires `festival_alerts: [{festival_name:"Diwali", when:"today", ...}]`.
- Content Agent emits 3 + 1 variants per platform — the `festival_variant`
  pairs the Q2 recap angle with a short Diwali greeting voice.

### 11.5 Brief Guard rejection — too short

| Input         |                       |
| ------------- | --------------------- |
| `brief_text`  | `launch new product`  |

Stops at Stage 1.2 → HTTP 422 → frontend renders the generic "too short"
message inline.

### 11.6 Brief Guard rejection — banned term

| Input         |                                       |
| ------------- | ------------------------------------- |
| `brief_text`  | `sell adult onlyfans subscriptions`   |

Stops at Stage 1.3 → HTTP 422 → policy message.

### 11.7 Refiner rejection — paraphrased harmful

| Input         |                                                                                |
| ------------- | ------------------------------------------------------------------------------ |
| `brief_text`  | `Promote our weekend retreat that teaches young people to handle automatic firearms for self-defense, with overnight stays at remote campsites.` |

Brief Guard passes (no exact banned term). Refiner's STEP-0 catches the
paraphrased violence-promotion intent → `rejection_category: "harmful"` →
generic message: *"We can't generate marketing content for briefs that
involve violence, sexual content, or illegal activity..."*

### 11.8 Refiner rejection — no marketing utility

| Input         |                                          |
| ------------- | ---------------------------------------- |
| `brief_text`  | `What is 2+2 and tell me a poem about it.` |

Brief Guard passes (long enough, no banned terms). Refiner STEP-0 →
`no_utility` → generic message.

### 11.9 Cross-topic with DNA — allowed

| Input                      |                                                                                  |
| -------------------------- | -------------------------------------------------------------------------------- |
| `brief_text`               | `Our take on the latest US Federal Reserve rate decision and what it means for B2B SaaS budgets.` |
| `selected_dna`             | `"dna_zyntegrate_v2"` (SaaS analytics)                                            |

Refiner does NOT reject. `mode: "hybrid"`. Research strategy C. DNA
contributes voice + audience; topic stays on Fed rates.

### 11.10 Team-member DNA inheritance

| Input                      |                                                                                  |
| -------------------------- | -------------------------------------------------------------------------------- |
| User                       | Team member of org `pwc_india`                                                    |
| `selected_dna`             | (whatever they picked from their dropdown)                                        |

Backend looks up `team_member.org_id` → resolves to `pwc_india`'s admin DNA →
loads that DNA + KB. Rest of pipeline identical to scenario 11.1.

### 11.11 USER LINK propagation through to image post

| Input                      |                                                                                  |
| -------------------------- | -------------------------------------------------------------------------------- |
| `brief_text`               | `Launch our pricing page redesign — https://spenzo.io/pricing`                    |
| `post_type`                | `"image"`                                                                         |

- Refiner extracts `https://spenzo.io/pricing` into USER LINKS.
- Content Agent uses it in LinkedIn viral_reach CTA via R9a.
- Caption selected for image is the LinkedIn block (priority).
- AD v2 embeds the caption into `viewer_takeaway`; image model renders the
  scene anchored to the caption's promise.

### 11.12 List/roundup brief with referenced_entities

| Input                      |                                                                                  |
| -------------------------- | -------------------------------------------------------------------------------- |
| `brief_text`               | `Share the top 5 AI tools every B2B marketer should be testing right now.`        |

- Research fires LITERAL-QUERY + populates `referenced_entities` with each
  tool's official homepage.
- Content Agent's R9b kicks in: every tool name in the post is followed by
  its verbatim URL from `referenced_entities`. Zero placeholders.

---

## 12. Output payload shape (returned to frontend)

```json
{
  "refined_brief": "<labelled text block>",
  "research": { ... full research JSON ... },
  "cultural_calendar": { "india": [...], "usa": [...] },
  "content": {
    "linkedin":  { "viral_reach": "...", "high_interaction": "...", "follower_growth": "...", "festival_variant": "..." },
    "twitter":   { ... },
    "facebook":  { ... },
    "instagram": { ... }
  },
  "recommendation": { "best_variant": "...", "reason": "..." },
  "mode": "product | service | hybrid | topic",
  "_critic": { "is_valid": true, "critique": "...", "adjustments": "" },
  "images": [                       // only when post_type=image
    { "url": "...", "blueprint": {...}, "ad_audit": {...} },
    { "url": "...", "blueprint": {...}, "ad_audit": {...} },
    { "url": "...", "blueprint": {...}, "ad_audit": {...} }
  ],
  "stage_times": {
    "guard_ms": 4,
    "refiner_ms": 2410,
    "calendar_ms": 0,
    "research_ms": 8920,
    "content_ms": 11340,
    "post_ms": 18,
    "critic_ms": 3120,
    "image_ms": 28430
  }
}
```

---

## 13. Performance characteristics

| Stage              | p50      | p95      | Notes                                                            |
| ------------------ | -------- | -------- | ---------------------------------------------------------------- |
| Brief Guard        | < 5 ms   | 10 ms    | Local regex                                                       |
| DNA Resolution     | 20 ms    | 60 ms    | Postgres + KB file reads                                          |
| Refiner            | 2.0 s    | 3.5 s    | 1 LLM call, no grounding                                          |
| Cultural Calendar  | 0 ms     | 6.0 s    | Day-cached — cold miss only once per day                          |
| Research           | 8.5 s    | 14 s     | 3-6 grounded google_search queries                                |
| Content Agent      | 10 s     | 16 s     | LLM call with full context window                                 |
| Post-process       | 15 ms    | 40 ms    | Python only                                                       |
| Critic             | 2.8 s    | 4.5 s    | LLM call, read-only                                                |
| Image (per variant)| 9 s      | 14 s     | AD v2 (2.5 Flash) → Gemini 3.1 Flash Image streaming               |
| **Total text**     | **22 s** | **35 s** |                                                                  |
| **Total image**    | **45 s** | **65 s** | 3 image variants run in parallel                                 |

---

## 14. Component map

| Concern                      | File                                                                  |
| ---------------------------- | --------------------------------------------------------------------- |
| Rule-based brief guard       | `apps/backend/services/brief_guard.py`                                |
| Refiner + STEP-0 semantic    | `apps/backend/services/ai_service.py::_refine_brief_agent`            |
| Cultural calendar (daily)    | `apps/backend/services/ai_service.py::_get_cultural_calendar`         |
| Research (grounded)          | `apps/backend/services/ai_service.py::_research_agent` + `_build_grounded_research_prompt` |
| Content (copywriter)         | `apps/backend/services/ai_service.py::_content_agent`                 |
| Critic                       | `apps/backend/services/ai_service.py::_critic_agent`                  |
| Post-processing              | `apps/backend/services/ai_service.py::_apply_content_post_processing` |
| Orchestrator                 | `apps/backend/services/ai_service.py::generate_strategic_content`     |
| DNA service                  | `apps/backend/services/dna_service.py`                                |
| Image pipeline (image post)  | `apps/backend/services/image_agent_v4.py` (+ `docs/art_director_pipeline.md`) |
| CSV logger                   | `apps/backend/services/csv_logger.py`                                 |
| Frontend brief form          | `apps/product-page/src/pages/Dashboard/components/CampaignBrief.jsx`  |
| API entrypoint               | `apps/backend/main.py::/api/generate-content`                         |

---

## Appendix A — Full Refiner Prompt (verbatim)

> Built by `_refine_brief_agent()`. Variables interpolated at runtime:
> `{word_count}`, `{quality_hint}`, `{dna_attached}`, `{knowledge_block}`,
> `{brief_text}`.

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

B. GENERIC / EMPTY INTENT — no actual campaign concept:
   • "create a post" / "generate an ad" / "make me content"
   • "write something about our company"
   • "test", "hello", random words, single product name with no detail
   • briefs that name no subject, no audience, no purpose

C. NO MARKETING UTILITY — the brief has no plausible social-media
   marketing use case for ANY business (e.g. "what is 2+2", personal
   diary entries, requests for code/legal/medical advice).
   IMPORTANT: do NOT reject for being "off-brand vs DNA". Cross-topic
   briefs (e.g. a marketing tool company posting about general AI
   industry news) are allowed — DNA contributes voice only. Only
   reject when no business could plausibly market this content.

If REJECTED, return EXACTLY this JSON (no markdown fences):
{
  "valid": false,
  "rejection_category": "harmful" | "generic" | "no_utility",
  "rejection_message": "<one short user-facing sentence, specific to the reason, no apology boilerplate>"
}

If VALID, set "valid": true and produce the refined_brief below.

═══════════════════════════════════════════════════════════════
GAP-FILLING (only when brief is lean/empty AND a downstream agent
needs the field)
═══════════════════════════════════════════════════════════════
QUALITY = empty | lean   → you MAY infer AUDIENCE, TONE, VISUAL HINT,
                           ANGLE format from knowledge or general
                           plausibility. You MUST list every inferred
                           field under ASSUMPTIONS MADE.

QUALITY = specific | professional
                         → respect everything the user wrote. Only
                           fill fields they left blank. ASSUMPTIONS
                           MADE = "Minimal".

NEVER fill TOPIC or KEY MESSAGE by invention — both must trace
directly to the brief. If the brief is too thin to produce these,
that's STEP 0 Category B (generic) territory — reject.

NEVER refuse to produce a brief for being "too short" once it passes
STEP 0. Always emit a best-effort strategic brief.

═══════════════════════════════════════════════════════════════
SOURCE-GROUNDING (CRITICAL — anti-hallucination)
═══════════════════════════════════════════════════════════════
Every entry under SUPPORTING POINTS MUST cite its source:
  1. "[user brief]"          — the user literally wrote this
  2. "[DNA: <field name>]"   — only if DNA was attached above
  3. "[doc: <filename>]"     — only if that file is in KNOWLEDGE

When DNA is not attached ({dna_attached} = "no"), only [user brief]
tags are valid.

If you cannot cite a source for a factual claim, DO NOT include it.
Fewer well-sourced points > more unverifiable ones.

DO NOT invent specific metrics, percentages, dollar amounts, partner
counts, time savings, customer counts, or other numeric proof unless
the source explicitly states them.

═══════════════════════════════════════════════════════════════
OUTPUT FORMAT — produce these labelled sections, in this order
═══════════════════════════════════════════════════════════════
STRATEGIC BRIEF
──────────────────────────────────────────
USER GOAL
  [One sentence. Paraphrase the user's literal intent — what they
   asked you to help produce. This is the anchor every other field
   must serve.]

TOPIC
  [2 sentences. Faithfully describe the subject of the campaign
   exactly as the user framed it. Do NOT pivot to a related brand
   topic. If the user said "latest AI updates", the topic is latest
   AI updates — not "how our product uses AI".]

AUDIENCE
  [Specific persona in 1-2 sentences with concrete role titles,
   company size, or stage words. If the user named an audience, use
   theirs. Else if DNA suggests one that fits the topic, use it.
   Else state "General audience interested in [topic]".]

KEY MESSAGE
  [One sentence — the single thing the audience should walk away
   knowing. Must serve USER GOAL, not a brand pitch.]

ANGLE
  [The POV / framing that delivers the USER GOAL compellingly. Choose
   what fits the brief: announcement, educational explainer, opinion,
   contrarian take, breakdown, story, listicle, news commentary, etc.
   No fixed formula. No "lead with the pain" by default.]

SUPPORTING POINTS
  - [Fact or claim] [source tag]
  - [Fact or claim] [source tag]
  - [Fact or claim] [source tag]
  (2-5 points. Every point sourced. Fewer is better than fabricated.)

TONE
  [Voice directives. If DNA brand_tone exists, use it verbatim. Else
   pick a sensible default for the topic (e.g. "informative, neutral,
   plain English"). Be specific: "direct, not salesy" / "curious,
   not authoritative".]

SOURCES REFERENCED
  - User brief: [yes / no — set "no" only if every field came from DNA]
  - Business DNA: [fields used, or "not attached"]
  - Uploaded docs: [filenames you sourced from, or "none"]

USER LINKS
  [Extract EVERY URL the user typed in their raw brief, VERBATIM.
   List each one on its own line as "- https://...". These are
   authoritative — the copywriter will use them as real resource
   links in the post (rule #9c). Common patterns to look for:
     • "Promote my course at https://..."
     • "Read more here: https://..."
     • "Sign up: https://..."
     • "https://" or "http://" appearing anywhere in the brief
   Capture the URL exactly, including query strings and trailing
   slashes. Do NOT shorten, edit, or "clean up" the URL. If the
   user typed no URLs, write a single line: "- none"]

VISUAL HINT
  [One sentence describing what the ideal visual should CAPTURE —
   a scene, metaphor, or moment that delivers the USER GOAL. Not
   "an image of the product" unless the brief is about the product.]

CONSTRAINTS
  - [Things to avoid for THIS brief, e.g. "no invented stats",
     "don't pivot to product pitch", "match brand_tone"]
  - [Platform-agnostic — no hook rules or banned phrases here]

USER INPUT QUALITY: {quality_hint}
ASSUMPTIONS MADE:
  [If quality is empty/lean: list every field above you inferred.
   If specific/professional: write "Minimal — user brief covered
   the main points."]

═══════════════════════════════════════════════════════════════
Return STRICTLY this JSON (no markdown code fences, raw JSON):
═══════════════════════════════════════════════════════════════
If STEP 0 rejected:
  { "valid": false, "rejection_category": "...", "rejection_message": "..." }

If STEP 0 passed:
  { "valid": true, "refined_brief": "<the labeled-sections text block above, with real newlines — NOT escaped>" }
```

---

## Appendix B — Full Cultural Calendar Prompt (verbatim)

> Built by `_get_cultural_calendar()`. Variables interpolated at runtime:
> `{today_iso}`, `{tomorrow_iso}`.

```
You have access to google_search. Find ONLY the major, nation-wide
cultural moments actually being observed today or tomorrow that a
mainstream marketer would actually acknowledge in social content.

Today's date (UTC): {today_iso}
Tomorrow's date (UTC): {tomorrow_iso}

═══════════════════════════════════════════════════════════════
STRICT INCLUSION RULES — be conservative, empty arrays are fine
═══════════════════════════════════════════════════════════════

INCLUDE for INDIA only if it qualifies on at least ONE of:
  • National gazetted public holiday (observed across the WHOLE
    country, not a single state) — e.g. Independence Day,
    Republic Day, Gandhi Jayanti, Diwali, Holi, Eid-ul-Fitr,
    Eid-ul-Adha (Bakrid), Christmas, Good Friday.
  • Major nationally-recognised Hindu / Muslim / Sikh / Christian
    festival broadly observed across multiple regions — e.g.
    Diwali, Holi, Raksha Bandhan, Janmashtami, Ganesh Chaturthi,
    Navratri, Dussehra, Maha Shivratri, Eid-ul-Fitr (Ramzan Eid),
    Eid-ul-Adha (Bakrid), Muharram, Guru Nanak Jayanti, Christmas,
    Easter, Good Friday.

INCLUDE for USA only if it qualifies on at least ONE of:
  • Federal public holiday (observed nationally) — e.g. New Year's
    Day, MLK Day, Presidents Day, Memorial Day, Juneteenth,
    Independence Day (July 4), Labor Day, Columbus / Indigenous
    Peoples Day, Veterans Day, Thanksgiving, Christmas.
  • Top-tier mainstream culturally-marketed day — Valentine's Day,
    St. Patrick's Day, Mother's Day, Father's Day, Halloween,
    Easter, Hanukkah, Super Bowl Sunday, Black Friday, Cyber Monday.

EXPLICITLY EXCLUDE — do NOT include any of these:
  ✗ Single-state or single-city public holidays (e.g. "Public
    holiday in Jammu & Kashmir", "Holiday in Himachal Pradesh",
    "Local election holiday", "Statehood Day for X").
  ✗ Regional / niche festivals observed only in one state or
    community (e.g. Vaikasi Visakam, Tibetan Cultural Festival,
    Onam outside Kerala, Pongal outside Tamil Nadu — UNLESS the
    brief's audience is specifically in that region).
  ✗ "National X Day" novelty days (National Pizza Day, National
    Donut Day, etc.) UNLESS they are top-tier marketing days
    listed in the INCLUDE rules above.
  ✗ UN / WHO international observance days (International Day of
    Peace, International Day of UN Peacekeepers, World MS Day,
    etc.).
  ✗ Heritage months observed only in one city / state / community
    (Croatian American Heritage Month, etc.). National-level
    heritage months (Black History Month, Hispanic Heritage Month,
    Pride Month) can be included on their start/end dates only.
  ✗ Minor religious observances (vrats, fasts, partial-day rituals)
    that aren't broadly observed nationally.
  ✗ Anything you cannot ground in a reliable source.

Other rules:
  - Date-shifting festivals (Diwali, Eid, Easter, Holi, etc.):
    include ONLY on the actual observance date this year.
  - Multi-day festivals: include on each observed date in the
    window. Note in the "note" field if it's day 1 / final day.
  - Return EMPTY arrays when nothing qualifies — do NOT pad with
    minor events.

Return STRICTLY this JSON (no markdown, no code fences):
{
  "today_date": "{today_iso}",
  "tomorrow_date": "{tomorrow_iso}",
  "india": {
    "today":    [{"name":"...","type":"national_holiday|major_festival","note":"one-line context"}],
    "tomorrow": [{"name":"...","type":"...","note":"..."}]
  },
  "usa": {
    "today":    [{"name":"...","type":"federal_holiday|major_observance","note":"..."}],
    "tomorrow": [{"name":"...","type":"...","note":"..."}]
  }
}
```

---

## Appendix C — Full Research Agent Prompt (verbatim, grounded path)

> Built by `_build_grounded_research_prompt()`. Variables: `{today_iso}`,
> `{seven_days_ago_iso}`, `{refined_brief}`, `{user_context}`, `{cal_block}`,
> `{has_dna}`.

```
You are a Research Analyst feeding a downstream Content Agent.
Today's date is {today_iso}.

You HAVE access to Google Search via the google_search tool.
Use it aggressively to ground EVERY factual claim in real, recent
sources. Do NOT rely on training-data memory for anything time-sensitive.

═══════════════════════════════════════════════════════════════
INPUTS
═══════════════════════════════════════════════════════════════
REFINED BRIEF (the user's intent — primary source of truth):
{refined_brief}

USER / BRAND CONTEXT (DNA + uploaded docs — supporting knowledge):
{user_context}

LIVE CULTURAL CALENDAR (today + tomorrow, India + USA — already
fetched, do not re-search):
{cal_block}

DNA attached: {has_dna ? "yes" : "no"}

═══════════════════════════════════════════════════════════════
PART 1 — MAIN RESEARCH (intent-driven, depends on brief)
═══════════════════════════════════════════════════════════════
Decide what to search BASED ON THE BRIEF:

A. If the brief is about a TOPIC / NEWS / TREND (e.g. "latest AI
   updates", "marketing trends", "industry news"):
     • Search for the most recent news, launches, and developments on
       that specific topic in the last 7 days.
     • Pull from primary sources (company blogs, press releases) when
       possible.
     • DO NOT pivot to brand promotion — stay on the topic.

B. If the brief is about the USER'S OWN PRODUCT / COMPANY (DNA
   attached AND the brief is about their product, launch, feature,
   milestone):
     • Search for fresh angles + supporting context for the product.
     • ALSO search for COMPETITOR news in the last 7 days (look at DNA
       overview/tagline to identify the category, then find who else
       operates in it). Surface this under competitor_news.
     • Surface DNA-grounded facts with [DNA: …] tags; web-grounded
       facts with [src:N] tags.

C. If the brief is BRAND + TOPIC HYBRID (e.g. "our take on the latest
   AI trends"):
     • Combine A and B. Topic news for the angle; DNA + competitor
       news to position the brand's POV.

Pick whichever of A/B/C applies and produce trending_context,
company_product_analysis, problem_solving_opportunity, and (when B)
competitor_news from the main-research searches.

═══════════════════════════════════════════════════════════════
PART 2 — AUXILIARY RESEARCH (always-on, runs EVERY call,
independent of the main brief topic)
═══════════════════════════════════════════════════════════════
These four items run on EVERY research call regardless of what
Part 1 looks like. Even if the brief is purely brand-focused
(Strategy B), you still do all four. They give the downstream
content agent fresh cultural + discoverability context for every post.

AUX-1. CULTURAL CALENDAR — already pre-fetched above. Do NOT
       re-search. Read the calendar block and decide whether any
       listed events fit the brief / audience for inclusion in
       angles_to_test or trending_context.

       ALSO — fire a festival_alert WHEN AND ONLY WHEN:
         • The calendar shows a major nation-wide festival OR a
           federal/national public holiday for India or USA today
           or tomorrow (type = "national_holiday", "major_festival",
           "federal_holiday", "major_observance"); AND
         • The user's brief does NOT already mention that festival
           by name or by theme.
       The alert flags the moment for the downstream copywriter so
       it can add an EXTRA festival-themed variant the user might
       have forgotten about ("today is Diwali — I forgot, let me
       post a Diwali greeting").
       Populate the `festival_alerts` array (see schema). Empty
       array on quiet days. Multiple alerts allowed if multiple
       qualifying festivals fall in the window.

AUX-2. TRENDING TOPICS — broader topics currently moving in the
       user's industry / audience space (NOT just the brief's
       specific subject). Examples:
         • Marketing-analytics brief → search "trending topics in
           marketing analytics this week"
         • AI-news brief → search "what AI topics are getting
           attention on social media this week"
         • B2B SaaS brief → search "B2B SaaS topics trending on
           LinkedIn this week"
       Emit 3-7 short phrases under trending_topics. Grounded only.

AUX-3. TRENDING HASHTAGS — currently used in conversations about
       the topic / industry / audience. Search Twitter/X trending,
       LinkedIn trending, niche hashtag aggregators. Format
       "#WithHash". Emit 3-10 under trending_hashtags.

AUX-4. TRENDING KEYWORDS — keywords + phrases people use when
       discussing this topic right now (for SEO / SMO
       discoverability). Emit 3-10 under trending_keywords.

LITERAL-QUERY RULE (READ BEFORE BUILDING SEARCHES)
If the brief contains a specific entity, product name, person,
event, or phrasing that the user explicitly wants researched,
pass that string VERBATIM as one of your google_search queries
BEFORE doing any paraphrased queries. The user's wording is
the ground truth for intent.
Examples:
  • Brief says "research Gemini 3.5 release" → one query MUST be
    the literal string "Gemini 3.5 release" (plus a date qualifier
    if the brief mentions one).
  • Brief says "what did Sundar Pichai say at I/O" → one query
    MUST be "Sundar Pichai Google I/O" (verbatim person + event).
  • Brief says "Spenzo Pulse launch" → one query MUST be
    "Spenzo Pulse launch" (verbatim product name).
Do NOT replace the user's wording with synonyms or category
terms. Their phrasing is intentional — search it as written.

Run 3-6 distinct google_search queries total covering both PART 1
and the auxiliary items (combine where you can — one search for
"{topic} news this week" can cover both A and AUX-2 if it returns
a related-topics roundup). Common query shapes:
  • "<verbatim string from brief>"  ← MANDATORY when brief is specific
  • "{topic} news this week site:*.com -roundup"
  • "{competitor brand} {topic} announcement {month year}"
  • "trending topics in {industry} {month year}"
  • "trending hashtags {industry} {platform} {month year}"
  • "{topic} keywords marketers using {year}"

═══════════════════════════════════════════════════════════════
TIME WINDOW (STRICT — honor what the brief asked for)
═══════════════════════════════════════════════════════════════
Read the brief and pick the tightest window that fits:
  • Brief says "today" / "this morning" / "right now" → only sources
    from {today_iso} (last 24 h). If nothing fresh enough exists,
    say so explicitly in trending_context. Do NOT fall back to
    7-day-old news and present it as "today".
  • Brief says "yesterday" → sources from the last 48 h.
  • Brief says "this week" / "past week" / "recently" / no explicit
    window → sources on or after {seven_days_ago_iso} (7-day default).
  • Brief says "this month" → sources from the last 30 days.
  • Brief says a specific date or month/year (e.g. "Q2 2026 launches",
    "November 2025") → match that exact window.
• Record the publication date for every source you cite.
• Older sources allowed ONLY as labelled background context — never
  cited as "fresh news".

═══════════════════════════════════════════════════════════════
CITATION RULES (CRITICAL — anti-hallucination)
═══════════════════════════════════════════════════════════════
• Every concrete fact in trending_context, target_audience,
  problem_solving_opportunity, company_product_analysis, and
  competitor_news MUST carry an inline [src:N] marker pointing to
  sources[].
• Numbers, dates, product names, and company names need [src:N] tags.
• Brand/DNA-derived claims use [DNA: field] / [doc: file] / [user brief].
• Trending hashtags/keywords must be grounded — if you can't ground
  a hashtag in a real source, drop it. Better to return 2 grounded
  hashtags than 10 invented ones.
• Claims you cannot ground go into do_not_claim — never speculate.

═══════════════════════════════════════════════════════════════
OUTPUT FIELDS
═══════════════════════════════════════════════════════════════
1. target_audience — 1-2 specific segments most likely to engage.
   Role titles + company size / life stage + pain trigger. Tagged.

2. trending_context — 1-4 sentences of fresh news / launches /
   trends relevant to the brief. Every factual sentence has [src:N].
   If nothing fresh exists, say so explicitly.

3. problem_solving_opportunity — the specific friction this post
   should name. Tagged.

4. company_product_analysis — the ONE edge worth leading with.
   Brand context: [DNA: …]. Cross-topic angles: [src:N].

5. angles_to_test — exactly 3 distinct strategic angles. Meaningfully
   different (contrarian / story-led / data-led / news-led / cultural-
   moment-led / etc.). Each is a directive sentence, not a headline.

6. do_not_claim — up to 5 things the content agent MUST NOT say.

7. trending_topics — array of 3-7 short phrases naming broader
   topics CURRENTLY trending in the user's industry / audience
   space (not just the brief's specific subject). These exist for
   editorial planning — even if the current post doesn't use them,
   the user can see what's in the air. Grounded only.

8. trending_hashtags — array of 3-10 hashtags currently being used in
   conversations about this topic / audience. Format: "#WithHash".
   Order: most relevant first. Each grounded in a source. Empty array
   if nothing real surfaced — never invent.

9. trending_keywords — array of 3-10 keywords / phrases that are
   trending in the topic discourse right now (for SEO/discoverability).
   Each grounded. Empty if nothing real.

10. competitor_news — array of 0-5 items. ONLY populate when the brief
    is brand/product-focused AND DNA is attached. Each item: a recent
    competitor launch / move / announcement that's strategically
    relevant. Format: { "competitor": "...", "headline": "...",
    "src": N, "published": "YYYY-MM-DD" }. Empty array otherwise.

11. festival_alerts — array (0-3 items) of major festivals the
    copywriter should consider creating an EXTRA themed variant
    for. Fires ONLY when (a) the cultural_calendar shows a major
    nation-wide festival / federal holiday today or tomorrow AND
    (b) the user's brief does not already reference it.
    Each item:
      { "country": "india" | "usa",
        "festival_name": "Diwali",
        "when": "today" | "tomorrow",
        "date": "YYYY-MM-DD",
        "type": "national_holiday|major_festival|federal_holiday|major_observance",
        "mentioned_in_brief": false,
        "suggested_angle": "one-line nudge for the copywriter, e.g. 'short Diwali greeting tying brand voice to the festival'" }
    Empty array when nothing qualifies.

13. grounding_confidence — overall label:
    • "grounded"    — facts well-supported by sources[]
    • "partial"     — mix of fresh sources + DNA + qualitative inference
    • "speculative" — searches returned little fresh; content agent
      should stay qualitative and avoid numbers

14. sources — array of objects, ONE per cited URL:
    { "id": 1, "url": "...", "title": "...", "published": "YYYY-MM-DD",
      "publisher": "..." }

15. referenced_entities — Per-entity homepages for list-style briefs.
    FIRES ONLY when the brief asks for a list / roundup / comparison
    of distinct named entities. Detect patterns like:
      • "top N <thing>", "best N <thing>", "list of <thing>"
      • "X vs Y", "compare X, Y, Z"
      • "tools / platforms / frameworks / books / channels / brands
         to follow / try / read / watch"
      • "newsletters / podcasts / repos / databases worth knowing"
    For each entity the post will name, run ONE additional Google
    search like "<entity name> official site" to find the entity's
    canonical homepage. Capture:
      { "name": "ChatGPT",
        "url": "https://chat.openai.com",
        "one_liner": "OpenAI's flagship conversational assistant." }
    Include 5-15 entities depending on what the brief asks for
    (e.g. "top 10" → 10 entries). The copywriter will use these
    URLs verbatim when listing each entity in the post.
    For non-list briefs (product announcements, brand storytelling,
    thought-leadership), emit an empty array — do NOT force-fill.

Return STRICTLY this JSON (no markdown, no code fences):
{
    "target_audience": "...",
    "trending_context": "...",
    "problem_solving_opportunity": "...",
    "company_product_analysis": "...",
    "angles_to_test": ["...", "...", "..."],
    "do_not_claim": ["...", "..."],
    "trending_topics": ["topic one", "topic two"],
    "trending_hashtags": ["#One", "#Two"],
    "trending_keywords": ["phrase one", "phrase two"],
    "competitor_news": [
        { "competitor": "...", "headline": "...", "src": 1, "published": "YYYY-MM-DD" }
    ],
    "festival_alerts": [
        {
            "country": "india",
            "festival_name": "Diwali",
            "when": "today",
            "date": "YYYY-MM-DD",
            "type": "major_festival",
            "mentioned_in_brief": false,
            "suggested_angle": "Short Diwali greeting tying brand voice to the festival of lights."
        }
    ],
    "grounding_confidence": "grounded | partial | speculative",
    "sources": [
        { "id": 1, "url": "...", "title": "...", "published": "YYYY-MM-DD", "publisher": "..." }
    ],
    "referenced_entities": [
        { "name": "ChatGPT", "url": "https://chat.openai.com", "one_liner": "OpenAI's flagship conversational assistant." }
    ]
}
```

### Appendix C.1 — Offline Research Fallback Prompt (verbatim)

> Used when the grounded call returns an error dict. Built by
> `_build_offline_research_prompt()`. Variables: `{refined_brief}`,
> `{user_context}`, `{cal_block}`.

```
You are a Research Analyst feeding a downstream Content Agent.
Your job is NOT to re-summarize what the Refiner already produced.
Your job is to (a) sharpen the angles, (b) protect downstream agents from
hallucinating numbers or trends, and (c) pick the single product edge worth
leading with.

REFINED BRIEF (already source-tagged by upstream refiner):
{refined_brief}

USER / BRAND CONTEXT (DNA + uploaded docs):
{user_context}

LIVE CULTURAL CALENDAR (today + tomorrow, India + USA — fetched live
via web search; safe to reference because already grounded):
{cal_block}

Use the cultural calendar ONLY if it's relevant to the brief or the
audience. Do NOT force a festival mention into an unrelated brief.
When relevant, you may surface it in `angles_to_test` as a topical
angle ("tie the post to {festival}") or in `trending_context` as
cultural backdrop.

### GROUNDING RULES (STRICT)
- You have NO web access. You cannot know 2025/2026 statistics, competitor
  launches, or current news. If a claim is not supported by the refined
  brief, the DNA, or a [doc: filename], tag it [speculative].
- Every factual sentence in your output must end with exactly ONE tag:
  [user brief] | [DNA: field_name] | [doc: filename] | [speculative] | [inference].
  Use [inference] when you are reasoning FROM tagged facts (e.g. combining
  two DNA fields into an audience segment).
- NEVER emit a number, percentage, or dollar figure with [speculative] or
  [inference]. If you have no grounded number, write the qualitative claim
  instead. Numbers belong ONLY under [user brief], [DNA: ...], or [doc: ...].

### FIELDS TO PRODUCE

1. target_audience — 1-2 segments MOST likely to engage. Concrete (role,
   company size / life stage, pain trigger). One sentence per segment,
   each source-tagged.

2. trending_context — 1-3 sentences on the market/category context. If you
   have no grounded source, write a conservative qualitative observation
   and tag [speculative]. Do NOT invent percentages.

3. problem_solving_opportunity — the specific friction this post should
   name. Tied to at least one DNA or doc tag when possible.

4. company_product_analysis — the ONE edge worth leading with (unique
   capability, proof point, or positioning). Tagged.

5. angles_to_test — exactly 3 distinct strategic angles the content agent
   can A/B. Each is a short directive sentence, not a headline. They must
   be meaningfully different (e.g. contrarian vs. story-led vs. data-led).

6. do_not_claim — up to 5 concrete things the content agent MUST NOT say
   because we cannot verify them. Examples: specific user counts, ranking
   claims, fabricated client names, comparison superlatives. If refined
   brief / DNA / docs give no grounding for a tempting claim, add it here.

7. grounding_confidence — overall label:
   • "grounded"    — majority of facts come from [user brief] / [DNA] / [doc]
   • "partial"     — mix of grounded + inference
   • "speculative" — mostly inference/speculative; content agent should
     stay qualitative and avoid numbers

Return STRICTLY this JSON (no markdown, no code fences):
{
    "target_audience": "...",
    "trending_context": "...",
    "problem_solving_opportunity": "...",
    "company_product_analysis": "...",
    "angles_to_test": ["...", "...", "..."],
    "do_not_claim": ["...", "..."],
    "grounding_confidence": "grounded | partial | speculative"
}
```

---

## Appendix D — Full Content Agent Prompt (verbatim)

> Built by `_content_agent()`. Variables: `{user_context}`, `{refined_brief}`,
> `{research_json}`, `{cultural_calendar_block}`, `{platforms_str}`,
> `{has_festival_alert}`.

```
You are a senior social-media copywriter. Your goal: maximize REACH,
FOLLOWERS, COMMENTS, and SAVES on the brand's actual social presence.
Not shares. Not vanity likes. Real engagement that compounds over time.

You have full creative freedom over voice, structure, hook style,
CTA shape, and visual format. Pick what fits THIS brief, THIS brand,
THIS platform, and THIS moment. There is no single "winning template" —
the best posts match their brand's voice and the post's purpose.

═══════════════════════════════════════════════════════════════
INPUTS — your full context
═══════════════════════════════════════════════════════════════

BRAND PROFILE (voice, tone, values — your soft constraint):
{user_context}

REFINED BRIEF (the user's actual intent — primary source of truth):
{refined_brief}

⚠️ THE REFINED BRIEF IS A STRATEGY DOCUMENT, NOT POST COPY.
It contains labelled sections — USER GOAL, TOPIC, AUDIENCE, KEY MESSAGE,
ANGLE, SUPPORTING POINTS, TONE, SOURCES REFERENCED, VISUAL HINT,
CONSTRAINTS, USER INPUT QUALITY, ASSUMPTIONS MADE.
These are instructions FOR YOU about how to write. They are NEVER content
to copy into the post. The user must never see the literal strings
"Visual hint:", "Audience:", "Key message:", "Tone:", "Supporting points:",
"Angle:", "Topic:", "Sources referenced:", "Constraints:", "Assumptions
made:", "User goal:", or any other labelled-section header in the
generated post text. If you find yourself writing one, delete it.

RESEARCH REPORT (everything the researcher found, including
angles_to_test, do_not_claim, grounding_confidence, trending_topics,
trending_hashtags, trending_keywords, competitor_news,
festival_alerts, sources):
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
     product names / company names must trace to the refined brief, the
     DNA, an uploaded doc tag, or research.sources. No tag = drop it.
   • If research.grounding_confidence == "speculative", you may NOT
     use numeric proof. Use named-scenario qualitative proof instead.

2. CHAR CAPS (server truncates anything over — write to be read, not cut).
   Safety-buffered against each platform's true hard limit so a post
   from a free-tier (non-premium) account never gets rejected:
     LinkedIn:  ≤ 2800   (true cap 3000 · sweet spot 500-1500)
     Twitter/X: ≤ 270    (true free-tier cap 280 — write punchy)
     Facebook:  ≤ 2200   (first 480 chars above the "See more" line)
     Instagram: ≤ 2100   (true cap 2200 · first 125 chars above "more")

3. HASHTAG CAPS (server enforces).
     LinkedIn:  0-5    (prestige brands use 0; growth brands use 3-5)
     Twitter/X: 0-2    (most brands use 0-1)
     Facebook:  0-3
     Instagram: 8-15   (engagement sweet spot, also the upper cap)

4. NO SHARE-BAIT — banned across every variant:
     "share this", "tag someone who", "send this to your team",
     "quote this with your take", "RT if you agree", "spread the word"
   This account does NOT optimize for shares.

5. DISTINCT ANGLES — each variant you produce for a given platform
   must map to a DIFFERENT entry in research.angles_to_test. Do not
   collapse two variants onto the same angle.

6. BRAND VOICE — read brand_tone + brand_values from DNA. Match them.
   If the brand is technical-warm, write technical-warm. If playful,
   write playful. If formal-analytical (PwC-style), write that.
   Brand name does NOT need to appear in every variant — voice
   consistency matters more than name repetition. Do NOT open with
   "[Brand] is the leader in…" press-release voice. First-person
   "We're [verb]…" / "We just [verb]…" is fine.

7. NO BUZZWORD CORPORATE-SPEAK. These words kill engagement on real
   social feeds — avoid unless the DNA literally requires them:
     empower / empowers / empowering
     democratize / democratizing
     leverage (as a verb)
     unlock (the power of / the potential)
     transform (your workflow / your business)
     accelerate (deployment / growth)
     unify (fragmented systems)
     seamlessly / seamless
     end-to-end (as filler)
     next-generation / next-gen
     best-in-class / world-class
     streamline (your workflow)
     drive (efficiency / growth / outcomes)
     revolutionize / game-changer / paradigm shift
   Write what the product actually does in plain English. "Drag your
   data sources onto a canvas" beats "empower your team to seamlessly
   unify fragmented systems."

8. NO LAZY CTA. These specific shapes are banned because they read
   cliché and reduce real engagement:
     "Comment below."
     "Let us know in the comments."
     "We want to hear your vision."
     "Share your thoughts."
     "What are your thoughts?"
   If you ask a question, ask a SPECIFIC one tied to the reader's
   actual workflow / experience (see CTA MENU below).

9. REAL URLS ONLY — NEVER USE PLACEHOLDER LINKS.
   Every URL you write in a post must be a REAL, working URL drawn
   from one of these sources (in priority order):

     (a) **USER LINKS** — URLs the user typed VERBATIM in their raw
         brief. The refiner extracted these into the USER LINKS
         section of the REFINED BRIEF above. HIGHEST priority — the
         user explicitly chose them. Use them exactly as written.

     (b) **research.referenced_entities[]** — for list / roundup /
         comparison briefs (e.g. "top 10 AI tools", "5 newsletters to
         read", "ChatGPT vs Claude vs Gemini"), the researcher has
         already looked up each named entity's OFFICIAL HOMEPAGE.
         Each entry is `{name, url, one_liner}`. When the post
         lists entities, use the entity URL verbatim alongside the
         name. Example format:
            "1. ChatGPT — https://chat.openai.com"
            "2. Claude — https://claude.ai"
            "3. Midjourney — https://midjourney.com"
         Do not paraphrase the name. Do not shorten the URL.

     (c) **research.sources[].url** — Google-grounded research
         citations. Use these for "further reading", "best overview
         for beginners", news citation, or any other "go deeper"
         link in the post.

     (d) **Brand DNA URL fields** visible in BRAND PROFILE above —
         `website_url`, `product_url`, `pricing_url`, `docs_url`,
         `blog_url`, `careers_url`, `social_links.*`. Use these for
         product CTAs, sign-up CTAs, careers posts, etc.

   BANNED — you must NEVER emit any of these placeholders:
     "[Insert Link 1]", "[Insert Link 2]", "[Insert Link 3]",
     "[Link 1]", "[Link]", "[Insert link here]", "[URL]",
     "[your website]", "[brand website]", "[product URL]",
     "[learn more here]", "(link)", "https://example.com",
     "https://placeholder.com", or any other bracketed / fake URL.

   Decision rule:
     • If you need N resource links and `research.sources` has ≥ N
       items, use the top N most relevant sources verbatim.
     • If you have fewer real URLs than you wanted to list, WRITE
       FEWER ITEMS — do NOT fill remaining slots with placeholders.
       A 1-link list with a real URL beats a 3-link list with two
       placeholders.
     • If you have NO real URL for a CTA, omit the CTA URL entirely
       and use a non-URL CTA from the CTA MENU instead (e.g.
       "When did you start learning AI?" rather than
       "Learn more here: [link]").

   Format URLs naked (no markdown link syntax — social platforms
   don't render markdown). Example: "Take a look → https://spenzo.io"
   NOT "Take a look [here](https://spenzo.io)".

═══════════════════════════════════════════════════════════════
SOFT GUIDANCE — pick what fits the brief, ignore the rest
═══════════════════════════════════════════════════════════════

HOOK MENU (real-brand patterns observed across LinkedIn + X)
Pick a DIFFERENT one for each variant — do not repeat hook style:
  • Milestone / announcement       ("We're opening our second center…")
  • Time-stamped news              ("Last week at Google I/O…")
  • Open audience question         ("What's the one skill people should be building right now?")
  • Stat + claim                   ("92% of workers feel cognitive strain.")
  • Bold POV                       ("Click-based measurement undervalues video.")
  • Human story lead               ("Donald Overton lost his sight from a blast in Iraq…")
  • Pain-then-solve                ("Most marketing teams have a measurement gap. They just don't know how big it is.")
  • Product reveal + themed emoji  ("Nano Banana for video is here 🍌🎥")
  • Punchy contrast                ("One agent is easy. Enterprise AI is not.")

CTA MENU (real-brand patterns observed)
Match CTA shape to variant intent — do not force "comment your letter":
  • Arrow + URL                    ("Take a look inside → https://…")
  • Direct verb                    ("Learn more →", "Register now:", "Reserve your spot:")
  • Open question                  ("When did you join LinkedIn?", "What's one interview take you stand by?")
  • Soft prompt for replies        ("What have you learned from your most popular posts?")
  • Personal close                 ("Congratulations to all the recent grads…")
  • Thread continuation (X only)   ("Here's how it works ↓")

FORMATTING — use freely; avoid forbidden patterns
  USE:
    • Single-line paragraphs with white space rhythm
    • Emoji bullets (✅ ⚡ 🔩 🧠 1️⃣ 2️⃣ 3️⃣)
    • Numbered lists
    • Pull-quotes from the brief / research
    • Unicode bold 𝐀𝐁𝐂 — ONLY when DNA brand_tone signals a tactical /
      marketing voice. Skip for prestige / corporate tones.
  AVOID:
    • Arrow bullets ↳  (zero of 36 reference posts use these)
    • "Click the link in bio" — only valid on Instagram
    • Press-release "[Brand] is the leader in / [Brand] helps / [Brand] is…"
    • Walls of text without breaks

PLATFORM-NATIVE PATTERNS (only when they fit)
  LinkedIn:
    • Line-break paragraphs for readability
    • Hashtags on a final line if used at all (or none for prestige)
    • Long-form OK up to 1500 chars; engagement falls past ~2000
  Twitter/X:
    • Punchy. Often under 400 chars.
    • `↓` arrow at end signals a continuing thread — use when the
      brief has enough depth for a 2-3-tweet thread
    • @-mention partner brands / own sub-products / executive voices
      when relevant for reach amplification
    • Sparse emoji (1-2 themed) for corporate; heavier (3-5) for tactical
  Facebook:
    • Hook value MUST land in the first 480 chars (before "See more")
    • Conversational, story-led, photo-paired
    • Question CTAs still work here (unlike LinkedIn which has
      moved past them)
    • 0-2 hashtags
  Instagram:
    • First 125 chars MUST carry the hook + core promise
    • Visual rhythm via line breaks + decorative emoji
    • Hashtag wall at end (8-15 typical), separated from copy by
      2-3 line breaks
    • Captions cannot carry clickable links — push to bio link

═══════════════════════════════════════════════════════════════
VARIANT STRUCTURE — what to emit per platform
═══════════════════════════════════════════════════════════════

For each platform in {platforms_str}, produce 3 variants tied to
DIFFERENT entries in research.angles_to_test. Variant keys are stable
for downstream compatibility — the intent is fixed, the form is yours:

  • viral_reach      — VISIBILITY-flavored variant. Goal: saves + follows
                       via an angle the broadest audience can grasp.
                       NOT share-bait. NOT click-bait. Strong hook,
                       concrete proof, save-driving CTA.

  • high_interaction — COMMENT-DRIVEN variant. Goal: genuine replies
                       from people who've lived the brief's subject.
                       Hook frames a decision / take / scenario the
                       reader has an opinion on. CTA invites a real
                       reply — NOT "comment A or B and one reason".

  • follower_growth  — AUTHORITY / DEPTH variant. Goal: profile click →
                       follow + save. Promise of more value to come
                       from the brand. Frameworks, sharp POV,
                       signature voice.

Pick a DISTINCT angle and a DISTINCT hook style for each.

═══════════════════════════════════════════════════════════════
FESTIVAL VARIANT (conditional)
═══════════════════════════════════════════════════════════════

Festival alert active: {has_festival_alert}

If "yes" (research.festival_alerts is non-empty), ALSO emit a
`festival_variant` per platform — a short voice-faithful festival
post tied to the brand. Read the festival_alert's suggested_angle
for guidance. Keep it under each platform's "sweet spot" length
(festival posts read better short). Same hard rules apply.

If "no", OMIT the festival_variant key entirely from the output
(do not emit it as null or empty string).

═══════════════════════════════════════════════════════════════
COMPANY MODE (informs tone, not template)
═══════════════════════════════════════════════════════════════

Glance at the BRAND PROFILE + BRIEF and decide whether THIS post is:
  • "product"  — about a usable app / platform / SaaS / tool
  • "service"  — about expertise / done-for-you work / case study
  • "hybrid"   — DNA covers both, brief sits in between
  • "topic"    — cross-topic post; brand contributes voice only
Tag this in `mode` for downstream / analytics signals. Variants
follow voice, not this label.

═══════════════════════════════════════════════════════════════
Return STRICTLY this JSON (no markdown, no code fences):
═══════════════════════════════════════════════════════════════
{
    "mode": "product | service | hybrid | topic",
    "mode_reason": "one sentence citing DNA / brief",
    "recommendation": {
        "best_variant": "viral_reach | high_interaction | follower_growth | festival_variant",
        "reason": "which variant is strongest for this moment and why"
    },
    "content": {
        "<platform_name>": {
            "viral_reach":      "...",
            "high_interaction": "...",
            "follower_growth":  "...",
            "festival_variant": "..."
        }
    }
}
OMIT the `festival_variant` key entirely when Festival alert active = "no".
Do NOT omit any platform from {platforms_str}.
```

---

## 15. Strategy → Scheduled → Publish (multi-day campaign pipeline) — v2

This is the second top-level pipeline in the system, separate from the single-post
flow in §0–§9. The user types ONE campaign brief, gets back a multi-day calendar
of (day × platform) slots, **reviews + approves each one** in a card grid, and a
heartbeat scheduler fires the approved ones at their scheduled time. Research
slots generate content JIT on the post date; static slots are pre-generated at
approval time and locked in once the user accepts them.

> **Major changes vs v1** (the strategy pipeline as originally implemented):
>
> 1. **Slot model = day × platform.** A 7-day plan with 3 platforms = 21 slots, not 7.
>    Each slot is for ONE channel. Topics, times, themes can differ per platform on the
>    same day so the post reads native on each network.
> 2. **post_type is locked globally.** Whatever the user picks on the brief screen
>    (Image / Text / Video / Document) becomes every slot's `content_type`. The
>    planner can't mix Text + Image any more.
> 3. **needs_research classifier per slot.** Planner labels each slot Static (pre-
>    generate now, user reviews) or Research (JIT-generate on the post date).
> 4. **Festival injection** from a multi-day cultural calendar — auto-adds an extra
>    slot per festival inside the plan window, marked `is_festival=true` +
>    `needs_research=true`.
> 5. **Best-time bands per platform**, not random LLM times. Times are pulled from
>    a fixed matrix (LinkedIn Tue 09:00, X Tue 09:00 peak, FB Wed 13:00, IG Tue 11:00,
>    etc.).
> 6. **Nothing auto-schedules.** Both research AND static slots land in
>    `status='awaiting_review'`. The user explicitly Approves each card before it
>    moves to `status='pending'` (publisher-ready).
> 7. **Two-track approval flow.** Static cards pre-generate at click-time and the
>    user reviews content + image + edits before approve. Research cards show
>    topic + date/time only; user can edit and approve.
> 8. **Auto-cleanup on re-run.** Clicking Approve & Generate Campaign first wipes
>    every previous `awaiting_review` + `failed` row for the user, so the review
>    queue only shows the new run.
> 9. **media_type stamped on every row** at booking time so the JIT scheduler
>    + pre-gen pipeline both know which mode to run (skip visuals for Text).

### 15.1 End-to-end flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│  USER (Strategic Blueprint screen)                                      │
│    brief · platforms · days · post_type (Image/Text/Video/Document)     │
│         · context_files (optional)                                      │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
   Stage A — PLAN GENERATION  (sync HTTP, 22-35 s)
     POST /generate-plan  (now also accepts post_type)
     services/ai_service.py::generate_campaign_plan(post_type=…)
        Refiner → Researcher → planning_window_calendar (multi-day festivals)
        → Planner (Agent 7) — day × platform grid, post_type-locked,
          best-time bands, needs_research classifier, festival injection.
                                  │  plan JSON: N×M slots, each with
                                  │  needs_research + research_reason +
                                  │  is_festival + festival_name
                                  ▼
   User reviews / edits the plan TABLE in the UI
   (date / time / topic / theme / Research-vs-Static chip — all editable)
                                  │  click "Approve & Generate Campaign"
                                  ▼
   Stage B — CALENDAR BOOKING  (fire-and-forget background task)
     POST /start-campaign
     routers/content.py::process_bulk_campaign()
        Step 0 — AUTO-CLEANUP: DELETE every awaiting_review + failed row
                 for this user from any previous campaign run.
        Step 1 — Pass 1 (insert placeholders):
                 For each slot:
                   - Scope targets to that slot's single channel only
                   - Compose slot_brief
                   - local time → UTC via user.timezone (past-date clamp)
                   - Map slot.content_type → media_type ("image"/"text"/…)
                   - INSERT ScheduledPost(
                       content="{}", image_url=None,
                       post_type = "agentic" if needs_research else "standard",
                       media_type = <slot_media_type>,   ← NEW
                       status = "awaiting_review",       ← was "pending"
                       campaign_brief = slot_brief, targets = single_platform_map
                     )
        Step 2 — Pass 2 (pre-generate STATIC rows only, sequential):
                 _pregenerate_awaiting_review(sp_id, user)
                   → generate_strategic_content(
                       slot_brief, [channel], user,
                       post_type=row.media_type      ← skips visuals for Text
                     )
                   → row.content = json.dumps({channel: chosen_variant})
                   → row.image_url = visuals[1] if ≥2 else visuals[0]
                                  │  (status stays awaiting_review)
                                  ▼
   Stage C — REVIEW & APPROVE  (the user-facing gate, NEW)
     Frontend: CampaignReviewQueue.jsx
        Polls GET /scheduled?include_awaiting_review=true every 2.5 s.
        Card grid (1/2/4 col responsive):
          • Static card: image preview + caption preview + edit/approve/
                         regenerate/reject. Shows amber "Generating…" until
                         pre-gen finishes, then flips to ready.
          • Research card: blue placeholder "Content generates on <date>".
                           Editable topic + date/time + approve/edit/reject.
                           No image (intentional — JIT path).
     User actions per card:
          Approve  → POST /scheduled/{id}/approve  → status='pending'
          Edit     → save edits + approve in one call
          Regenerate (static only) → POST /scheduled/{id}/regenerate
                      → re-runs _pregenerate_awaiting_review in background
          Reject   → DELETE /scheduled/{id} → row removed
          Approve all ready → bulk sequential approve
                                  │  rows move to status='pending' as approved
                                  ▼  (rows sit in DB until due)
   Stage D — HEARTBEAT (every 60 s)
     AWS Lambda  lambda_pinger.py  →  POST /scheduler/process
     routers/publishing.py::process_scheduler()
        SELECT * FROM scheduled_posts
          WHERE status='pending' AND scheduled_for <= utcnow()
                                  │  for each due row
                                  ▼
   Stage E — JUST-IN-TIME CONTENT MATERIALIZATION (research only)
     IF post_type=='agentic' AND content is empty:
       _jit_media_type = post.media_type or "image"   ← NEW: honors media_type
       generate_strategic_content(
         slot_brief, platforms, user,
         post_type=_jit_media_type                    ← Text skips Image pipeline
       )
       pick rec.best_variant → build final_content_map per platform
       pick visuals[1] if ≥2 else visuals[0] → post.image_url
     ELSE (standard / already-populated): skip — content + image are
       already on the row from the pre-gen step.
                                  │
                                  ▼
   Stage F — PUBLISH DISPATCH
     process_publishing_internal()
        Per platform in [linkedin, facebook, instagram, twitter]:
          look up SocialAccount (own OR assigned team-member token)
          upload media → call platform API → collect native_post_id
                                  │
                                  ▼
   Stage G — STATUS FINALIZATION
     ANY platform success → ScheduledPost.status = 'published'
                          + INSERT PublishedPost + PublishedPostPlatform rows
     ALL platforms failed → ScheduledPost.status = 'failed'
                          + error_message stored
```

### 15.2 Inputs (Strategic Blueprint screen)

| Field            | Type    | Required | Notes                                                                  |
| ---------------- | ------- | -------- | ---------------------------------------------------------------------- |
| `brief`          | string  | yes      | One campaign brief that covers the WHOLE multi-day arc                  |
| `platforms`      | array   | yes      | Subset of `[linkedin, twitter, facebook, instagram]`                    |
| `days`           | int     | no       | Default 7. Total slots = `days × len(platforms)` + festival slots       |
| `post_type`      | string  | yes      | `image` / `text` / `video` / `document` — **locked globally**           |
| `context_files`  | files   | no       | PDFs/DOCs merged into `extra_context` for refiner + research            |
| `targets`        | object  | yes (B)  | `{ platform: [account_id, …] }` — passed at booking time, not plan time. Frontend auto-defaults to ALL connected accounts on each selected platform when the user hasn't picked specific ones (matches single-post schedule behaviour). |

`POST /generate-plan` runs the same Brief Guard as `/generate-content` (Stage 1) and
the same Refiner STEP-0 semantic guard (Stage 3). Reject paths surface the same
HTTP 422 with the generic per-category messages.

**Quota math:** quota is debited per slot at plan generation time. A 7-day
× 3-platform Image plan costs 21 `posts` quota up-front, plus 1 per festival
slot the planner injects. Cancelling slots in the review queue does NOT refund.

### 15.3 Stage A — Plan generation (in detail)

**File:** `apps/backend/services/ai_service.py::generate_campaign_plan`

Five steps run sequentially on the request thread:

1. **Build user context** (`_build_user_context`) — DNA + KB + uploaded docs.
2. **Refiner Agent** (§4) — labelled Strategic Brief that anchors every
   downstream agent. Same prompt as the single-post flow.
3. **Research Agent** (§6) — `temperature=0.3`, `web_search=True`. Reads
   `refined_brief` + DNA, emits `angles_to_test`, `trending_topics`,
   `trending_hashtags`, `referenced_entities` for list briefs, etc.
4. **Planning-window cultural calendar** — new in v2.
   `_get_planning_window_calendar(days)` runs `gemini-flash-lite-latest`
   (temp 0.2, web_search ON) and returns every major nation-wide festival /
   federal holiday falling inside `[tomorrow .. today+days]`. Cached by
   `(today_iso, days)` so concurrent users planning the same window share
   the lookup.
5. **Planner Agent (Agent 7)** — receives `refined_brief`, `research`,
   `platforms`, `days`, `user_context`, `post_type`, and
   `planning_window_calendar`. Returns the day × platform plan with festival
   slots injected.

After the planner returns, Python:
- Normalizes `day` of week (Mon/Tue/…) from each slot's `date`.
- Stamps defaults so older planner outputs don't break downstream code:
  `needs_research=true`, `research_reason=""`, `is_festival=false`,
  `festival_name=None`.

**Final response:**

```json
{
  "refined_brief": "<labelled-sections text>",
  "research":      { ...full research dict... },
  "planning_window_calendar": {
    "window_start": "2026-06-09",
    "window_end":   "2026-06-15",
    "festivals":    []
  },
  "plan": [
    {
      "week": 1,
      "date": "2026-06-09",
      "day": "TUESDAY",
      "channel": "LinkedIn",
      "content_type": "Image",
      "topic": "Why pricing meetings are the silent revenue killer",
      "theme": "Education",
      "cta": "Read the breakdown →",
      "time": "09:30",
      "needs_research": false,
      "research_reason": "Static product-led explainer; no fresh data needed.",
      "is_festival": false,
      "festival_name": null
    },
    {
      "week": 1,
      "date": "2026-06-09",
      "day": "TUESDAY",
      "channel": "Twitter",
      "content_type": "Image",
      "topic": "Stop guessing your marketing ROI — three ways pricing meetings break down",
      "theme": "Education",
      "cta": "Read the breakdown →",
      "time": "09:00",
      "needs_research": false,
      "research_reason": "Product-led; static.",
      "is_festival": false,
      "festival_name": null
    }
  ]
}
```

> Note: 2 slots on the same date because `platforms=["linkedin","twitter"]`
> — every (day, platform) combination gets its own slot.

#### Planner Agent — model + hyperparameters

| Setting          | Value                                       |
| ---------------- | ------------------------------------------- |
| Model            | `gemini-flash-lite-latest`                  |
| Temperature      | `0.7`                                       |
| top_p / top_k    | `0.95 / 40`                                 |
| Web search       | off                                         |
| Response MIME    | `application/json`                          |
| Latency          | 3–6 s                                       |

#### Planner Agent — full prompt (v2 verbatim)

> Built by `_planner_agent()`. Variables: `{user_context}`, `{today}`,
> `{tomorrow}`, `{last_day}`, `{days}`, `{platforms_str}`, `{refined_brief}`,
> `{research_json}`, `{pt_norm}` (= Image/Text/Video/Document),
> `{window_cal_block}`.

```
You are a Senior Social Media Campaign Architect for the following profile:
{user_context}

TODAY'S DATE:     {today} (reference only — do NOT schedule on today)
FIRST POST DATE:  {tomorrow} (start the plan HERE)
LAST POST DATE:   {last_day} (plan ends on or before this)
POST TYPE (LOCKED): {pt_norm}  ← every slot's content_type MUST be this value.

Create a high-impact {days}-day posting plan for these platforms: {platforms_str}.

BRIEF: "{refined_brief}"
RESEARCH: {research_json}

PLANNING-WINDOW CULTURAL CALENDAR (festivals/holidays falling within
[{tomorrow} .. {last_day}]):
{window_cal_block}

═══════════════════════════════════════════════════════════════
PLATFORM BEST-TIME GUIDE — you MUST pick a time from these bands.
Do NOT invent random times. Pick the optimal slot for the platform on
that day-of-week.
═══════════════════════════════════════════════════════════════
LinkedIn  → Tue/Wed/Thu peak. Use 09:00-10:30 or 12:00-13:00.
            Mon/Fri OK at 09:30. Avoid Sat/Sun unless brief is consumer.
Twitter/X → Mon-Fri. Use 09:00, 12:00, or 17:00-18:00.
            Tue 09:00 is the peak engagement slot — prefer it when available.
Facebook  → Wed/Thu best. Use 13:00-15:00.
            Mon/Tue OK at 09:00. Avoid weekends for B2B.
Instagram → Tue/Wed/Thu/Fri. Use 11:00-13:00 or 19:00-21:00.
            Weekends 10:00-12:00 work for consumer brands.

For multi-platform same-day slots, pick the time best for the PRIMARY
platform (first in the user's selection).
If the day-of-week is suboptimal for the platform, still pick the closest
in-band time AND mention it briefly in `theme`.

═══════════════════════════════════════════════════════════════
NEEDS_RESEARCH CLASSIFIER — per slot
═══════════════════════════════════════════════════════════════
Set `needs_research: true` when the topic depends on information that
DOES NOT EXIST YET at plan time — news, trending events, this-week's
numbers, fresh launches, today's market state, festival cultural moments.
Example: "Latest AI news this week", "Today's Google I/O announcements",
"Diwali greeting", "This week's trending marketing topic".

Set `needs_research: false` when the topic is grounded in STATIC info
already known from the brand DNA, uploaded docs, or evergreen knowledge —
product features, brand story, team milestones, how-to explainers.
Example: "Spotlight on Spenzo Budget Planner feature", "Our company's
journey from 2021", "How attribution modeling works (educational)".

Always include `research_reason` — one short sentence explaining the call.

═══════════════════════════════════════════════════════════════
FESTIVAL INJECTION (mandatory when calendar shows entries)
═══════════════════════════════════════════════════════════════
The PLANNING-WINDOW CULTURAL CALENDAR above lists every major festival/
federal holiday inside the plan window. For EACH festival listed:
  • Add ONE extra slot on that festival's exact date PER PLATFORM.
  • topic: festival-themed angle tied to the brand voice.
  • theme: "Festival".
  • is_festival: true
  • festival_name: "<festival name from the calendar>"
  • needs_research: true   (festivals catch the day-of moment, JIT-generate)
  • research_reason: "Festival post — fires on the day to catch the
                     actual cultural moment."

These are EXTRA slots BEYOND the {days × len(platforms)} primary grid.

If the calendar shows no festivals, do NOT invent any. Skip injection.

═══════════════════════════════════════════════════════════════
OUTPUT — STRICT JSON (no markdown, no code fences)
═══════════════════════════════════════════════════════════════
{
    "plan": [
        {
            "week": 1,
            "date": "YYYY-MM-DD",
            "day": "DayName",
            "channel": "Platform",
            "content_type": "{pt_norm}",
            "topic": "Creative Hook or Title",
            "theme": "Strategy (e.g. Awareness, Conversion, Education, Community, Festival)",
            "cta": "Primary Call to Action",
            "time": "HH:MM (24h, from the best-time bands above)",
            "needs_research": true,
            "research_reason": "one-line classifier reason",
            "is_festival": false,
            "festival_name": null
        }
    ]
}

RULES (HARD):
1. First post MUST be on {tomorrow}. Last post MUST be
   on or before {last_day}. NEVER schedule on or before
   today ({today}).
2. SLOT GRID — emit one PRIMARY slot for EVERY (day × platform) combination.
   Total primary slots = {days} days × {len(platforms)} platforms.
   Same day, different platform = a SEPARATE slot. Topic + time + theme may
   differ across platforms on the same day (LinkedIn long-form vs Twitter
   punchy take). channel is exactly ONE platform per slot — never a list.
3. FESTIVAL SLOTS — for each festival in the planning-window calendar,
   emit one EXTRA slot PER platform on the festival's date.
   Total festival slots = (festivals in window) × {len(platforms)}.
   Mark each with is_festival=true + festival_name + needs_research=true.
4. content_type MUST equal "{pt_norm}" on EVERY slot. No mixing.
5. Time MUST come from the PLATFORM BEST-TIME GUIDE bands above.
6. needs_research + research_reason are MANDATORY on every slot.
7. is_festival defaults to false. Set true only on festival-injected slots
   and populate festival_name.
8. Vary topics across (day, platform) — don't repeat the same headline on
   LinkedIn + Twitter the same day; reframe it for each platform's native
   voice. Themes can repeat across platforms on a day (same theme, different
   copy angle) but should vary day-to-day for a balanced feed.
```

#### Planning-window calendar — model + hyperparameters

| Setting          | Value                                         |
| ---------------- | --------------------------------------------- |
| Function         | `_get_planning_window_calendar(days)`         |
| Model            | `gemini-flash-lite-latest`                    |
| Temperature      | `0.2`                                         |
| Web search       | **ON** (google_search grounding)              |
| Cache            | `_PLANNING_WINDOW_CACHE` keyed by `(today_iso, days)` |
| Latency          | 0 ms on cache hit, 4–7 s on cold miss         |

Strict include/exclude rules mirror the single-day cultural calendar (§5) —
only major nation-wide India + USA observances qualify. Empty list on quiet
windows; never invents.

### 15.4 Stage B — Calendar booking (two-track)

**File:** `apps/backend/routers/content.py::handle_start_campaign` →
`process_bulk_campaign` (FastAPI `BackgroundTasks`)

The user reviews the plan in `CampaignPlanTable.jsx`, edits any cell
(Date / Time / Topic / Theme / Research-vs-Static chip), then clicks
**Approve & Generate Campaign**. Payload:

```json
{
  "brief": "<original campaign brief>",
  "plan":  [ ...slots from Stage A, possibly edited... ],
  "targets": { "linkedin": ["urn:li:person:..."], "twitter": ["1234..."] }
}
```

The endpoint validates (`brief` + `plan` required), opens a `BackgroundTasks`
slot, and returns instantly:

```json
{ "status": "success", "message": "Campaign processing started for N slots." }
```

The frontend immediately navigates to the new **review** dashboard step
(`CampaignReviewQueue.jsx`).

`process_bulk_campaign(user_id, brief, plan, targets)` then runs out-of-band.

#### Step 0 — Auto-cleanup (NEW)

Before inserting anything, the task wipes every `awaiting_review` + `failed`
row that belongs to this user from a previous campaign click:

```python
abandoned = db.query(ScheduledPost).filter(
    ScheduledPost.user_id == user_id,
    ScheduledPost.status.in_(("awaiting_review", "failed")),
).all()
for r in abandoned:
    db.delete(r)
db.commit()
logger.info(f"[BULK] Cleared {len(abandoned)} abandoned review row(s) from prior runs")
```

What's wiped: only `awaiting_review` + `failed`. `pending` (already approved
and scheduled), `published`, `awaiting_approval` (Agency editor) all stay
intact. Each Approve & Generate click is a clean slate for the review queue.

#### Step 1 — Pass 1 (insert placeholders)

For each plan slot:

1. **Scope targets to this slot's platform only.** A slot has a single
   `channel`; the targets dict gets narrowed to `{slot_channel: [accounts]}`.
   Slots whose platform has no connected account are skipped + logged.
2. **Compose slot brief** — concatenates the global campaign brief with the
   per-slot specifics so the downstream content agent has full context:

   ```
   CAMPAIGN OVERVIEW: <brief>

   SPECIFIC POST TOPIC: <slot.topic>
   THEME: <slot.theme>
   CTA: <slot.cta>
   ```

3. **Resolve scheduled time** — the LLM emits a LOCAL `date + time` (`HH:MM`).
   Localized against `user.timezone` (defaults `UTC` if unset), converted to
   UTC, stripped of tzinfo for `DateTime` column storage.

4. **Past-date safety clamp** — if `scheduled_for <= utcnow()`, push it to
   `utcnow() + 1 day` and log a warning. Prevents posts from firing the moment
   they hit the DB.

5. **Map content_type → media_type** — `slot.content_type` is "Image" /
   "Text" / "Video" / "Document"; lowercased and stamped on `row.media_type`.
   This is what Stage C and Stage E read to decide which downstream pipeline
   to run.

6. **Insert row — both tracks land in `awaiting_review`:**

   ```python
   # Research / agentic track (needs_research=true)
   ScheduledPost(
       content       = "{}",
       image_url     = None,
       targets       = json.dumps({slot_channel: [accounts]}),
       scheduled_for = <UTC datetime>,
       timezone      = "UTC",
       post_type     = "agentic",          # JIT pipeline at fire time
       media_type    = slot_media_type,    # "image" / "text" / "video" / "document"
       campaign_brief = slot_brief,
       user_id       = user_id,
       status        = "awaiting_review",  # waits for user approval
   )

   # Static / standard track (needs_research=false) — placeholder, populated in Pass 2
   ScheduledPost(
       content       = "{}",
       image_url     = None,
       targets       = json.dumps({slot_channel: [accounts]}),
       scheduled_for = <UTC datetime>,
       timezone      = "UTC",
       post_type     = "standard",         # pre-gen, never JIT
       media_type    = slot_media_type,
       campaign_brief = slot_brief,
       user_id       = user_id,
       status        = "awaiting_review",
   )
   ```

7. Per-slot failures are caught + logged; **other slots continue**. No
   partial rollback — booking is best-effort.

After Pass 1 ends, the task logs total time + how many static rows are
queued for Pass 2.

#### Step 2 — Pass 2 (pre-generate static slots sequentially)

For each `static_row_id` collected in Pass 1, run
`_pregenerate_awaiting_review(db, sp_id, user)`:

```python
def _pregenerate_awaiting_review(db, sp_id: int, user: User):
    row = db.query(ScheduledPost).filter(ScheduledPost.id == sp_id).first()
    slot_targets = json.loads(row.targets) if row.targets else {}
    platforms = list(slot_targets.keys()) or ["linkedin"]
    slot_media_type = (row.media_type or "image").lower()      # ← NEW

    gen_data = generate_strategic_content(
        row.campaign_brief, platforms, user,
        post_type=slot_media_type,                              # ← NEW: skips Image pipeline on Text
    )

    rec = gen_data.get("recommendation", {})
    recommended_variant = rec.get("best_variant", "viral_reach")
    all_content = gen_data.get("content", {})
    final_content_map = {}
    for p in platforms:
        p_variants = all_content.get(p.lower(), {})
        chosen = p_variants.get(recommended_variant) or next(iter(p_variants.values()), "")
        final_content_map[p] = chosen

    # Sanity check — never store an empty content map silently
    if not any(len(v or "") for v in final_content_map.values()):
        raise RuntimeError("Content agent returned empty content for all platforms")

    row.content = json.dumps(final_content_map)

    visuals = gen_data.get("visuals", [])
    if len(visuals) >= 2:
        row.image_url = visuals[1].get("url")     # V1 (high_interaction) by default
    elif visuals:
        row.image_url = visuals[0].get("url")

    db.commit()
```

Status stays `awaiting_review` after pre-gen — the user still has to click
Approve. If `generate_strategic_content` throws, the row is moved to `failed`
+ `error_message` set + the next slot continues.

**Backend log timeline (4-slot static Spenzo campaign):**

```
[BULK] Start campaign user=1 slots=4 (research=0 static=4) targets=['linkedin','twitter']
[BULK] Static slot booked (awaiting_review placeholder, id=233) — linkedin @ 2026-06-09 04:00 UTC media=image topic='...'
[BULK] Static slot booked (awaiting_review placeholder, id=234) — twitter  @ 2026-06-09 03:30 UTC media=image topic='...'
[BULK] Static slot booked (awaiting_review placeholder, id=235) — linkedin @ 2026-06-10 06:30 UTC media=image topic='...'
[BULK] Static slot booked (awaiting_review placeholder, id=236) — twitter  @ 2026-06-10 06:30 UTC media=image topic='...'
[BULK] Pass-1 done in 5.92s — 4 static row(s) queued for pre-gen
[PREGEN] Starting slot 233 platforms=['linkedin'] media_type=image brief_len=201
[PREGEN] Slot 233 READY for review — variant=follower_growth content_lens={'linkedin': 1101} has_image=True visual_count=3
[BULK] Pre-gen 1/4 done for slot 233 in 80.73s
…
[BULK] All done for user 1 in 344.20s (4 static pre-generated, 0 research awaiting approval)
```

### 15.5 Stage C — Review & Approve (the user-facing gate)

**Frontend file:** `apps/product-page/src/pages/Dashboard/components/CampaignReviewQueue.jsx`
**API:** `GET /scheduled?include_awaiting_review=true` (polled every 2.5 s)

This is the new user-facing step. After `/start-campaign` returns, the
dashboard navigates to the `review` step and renders the card grid. Each
card represents one (day × platform) slot.

#### Two card flavours

| Flavour | When | What renders | Actions |
| --- | --- | --- | --- |
| **Static** (`post_type=standard`) | Pre-generated row | Image preview (160 px) + caption preview (line-clamp 5) + date/time | **Approve** · Edit · Regenerate · Reject |
| **Static — generating** | Static row before Pass 2 fills it | Dashed border + amber spinner *"Generating…"* placeholder + topic | (disabled until ready) |
| **Static — failed** | Pre-gen raised | Red border + error message | Retry (= Regenerate) · Reject |
| **Research** (`post_type=agentic`) | Always ready immediately | Blue dashed placeholder *"Content generates on Tue Jun 9 at 09:30"* + editable topic + date/time | **Approve** · Edit · Reject (no Regenerate) |

#### Polling + race-condition guard

The component initially renders *"Setting up your campaign…"* with a spinner.
Polling uses an internal `hasSeenRows` flag — it does NOT auto-bounce back to
the brief screen until at least one row has appeared, even if `/scheduled`
returns `[]` on the first few polls (which happens while the BackgroundTask
is still inserting Pass-1 placeholders).

Once any row appears, `hasSeenRows` flips true. If the queue later goes empty
(user approved or rejected every card), the screen auto-navigates back to the
brief screen after 50 ms.

Polling is 2.5 s. The endpoint returns 401 if unauthenticated and 200 with
the full row list otherwise. Filter on the client: only rows with
`status ∈ {awaiting_review, failed}` are rendered.

#### Approve endpoint

```
POST /scheduled/{post_id}/approve
Body (all fields optional):
  { "content": "...", "image_url": "...", "scheduled_for": "...", "campaign_brief": "..." }
```

The endpoint:
1. Looks up the row scoped to the user's team.
2. Rejects 400 if `status != "awaiting_review"`.
3. Applies any body fields that were sent (so edited content / new time /
   edited topic are saved).
4. Flips `status = "pending"`.
5. Logs `[REVIEW] Slot N approved by user X → status=pending`.

`campaign_brief` editing supports the research-card topic editor — when the
user changes the topic on a research card, the new brief replaces the old
one so the JIT pipeline reads the latest version at fire time.

#### Regenerate endpoint

```
POST /scheduled/{post_id}/regenerate
(no body)
```

Clears `content`, `image_url`, `error_message`; sets `status` back to
`awaiting_review` immediately; queues `_pregenerate_awaiting_review` in a
`BackgroundTasks` slot to refill them. Only valid from `awaiting_review` or
`failed`. The review queue's polling picks up the new content as soon as
pre-gen completes.

#### Reject

Plain `DELETE /scheduled/{post_id}` (the existing endpoint). Removes the row
entirely. The card disappears from the queue on the next poll.

#### Bulk "Approve all ready"

Sequential client-side loop over every `ready` card, calling `/approve` for
each. The Approve-all button only counts and approves cards whose pre-gen
has completed; "Generating…" placeholders are skipped.

### 15.6 Stage D — Heartbeat scheduler

**File:** `apps/backend/lambda_pinger.py` (AWS Lambda)

A standalone Lambda fires every 60 s via EventBridge. It POSTs to 5 endpoints
each tick; only the first is relevant to the strategy pipeline:

```
POST {BACKEND_URL}/scheduler/process
Headers: X-Scheduler-Secret: <SCHEDULER_SECRET>
```

**Endpoint:** `apps/backend/routers/publishing.py::process_scheduler`

```python
@router.get("/scheduler/process")
@router.post("/scheduler/process")
async def process_scheduler(request, db):
    now = datetime.utcnow()
    pending_posts = db.query(ScheduledPost).filter(
        ScheduledPost.status == "pending",
        ScheduledPost.scheduled_for <= now,
    ).all()
    for post in pending_posts:
        ...
```

Critical: the heartbeat ONLY picks up `status="pending"` rows. Rows in
`awaiting_review` are invisible to it. The user MUST approve a slot before
it can fire.

No cron or scheduler library inside FastAPI — the tick is purely external.
If the Lambda is paused, approved posts back up but nothing fires.

### 15.7 Stage E — JIT content materialization (research only)

For every due row where `post_type == "agentic"` AND `content` is empty/`{}`,
the scheduler runs the full §0–§9 pipeline at that moment:

```python
_jit_media_type = (post.media_type or "image").lower()   # ← NEW: honors media_type

gen_data = await run_in_threadpool(
    generate_strategic_content,
    post.campaign_brief,         # slot_brief from Stage B
    platforms,                   # from json.loads(post.targets).keys()
    user,                        # owner row
    post_type=_jit_media_type,   # ← NEW: Text skips Image pipeline
)
```

If the row is `post_type == "standard"` (a static one the user approved
earlier), the heartbeat **skips generation entirely** — content + image are
already on the row from the pre-gen step. It goes straight to Stage F.

The pipeline returns the same shape documented in §12. The scheduler then
picks:

1. **Best variant per platform** — from `gen_data["recommendation"]["best_variant"]`:

   ```python
   recommended_variant = rec.get("best_variant", "viral_reach")  # fallback
   final_content_map = {}
   for p in platforms:
       p_variants = all_content.get(p.lower(), {})
       final_content_map[p] = p_variants.get(
           recommended_variant,
           next(iter(p_variants.values()), "")  # fallback to first variant
       )
   post.content = json.dumps(final_content_map)
   ```

2. **Best visual** — `visuals[1]` if there are ≥2, else `visuals[0]`. (V0 =
   `viral_reach`, V1 = `high_interaction` — V1 historically scored highest in
   our CSV logger sample, hence the index-1 default.)

3. If content generation throws — `post.status = "failed"`, `error_message`
   captured, the row is skipped and the loop continues to the next due post.

**Why JIT for research and pre-gen for static?**

- **Static** (product/brand/evergreen): the user wants to *see + approve*
  the actual generated post before it goes out. Pre-gen at click time, store
  it on the row, let the user review.
- **Research** (news/trends/festival cultural moments): the data doesn't
  exist 7 days ahead. JIT-generating on the post date catches the actual
  cultural moment, fresh news, the festival's day-of context.
- **Why media_type passthrough matters:** without it, a Text-only Spenzo
  campaign was running the full Image pipeline (AD v2 + Gemini 3.1 Flash
  Image × 3 variants) and burning ~60 s + Image tokens per slot. With the
  `media_type` fix, Text slots skip visuals entirely (~15 s, no image
  tokens).

### 15.8 Stage F — Publish dispatch

**File:** `apps/backend/routers/publishing.py::process_publishing_internal`

Receives `(user_id, content, image_url, targets, dna_product_id)`. Two pre-flight
steps:

1. **Canonicalize Twitter alias** — clients send `x` or `twitter`; both are
   mapped to `twitter` so the downstream API call is unambiguous.
2. **Base64 → S3** — if `image_url` is a `data:image/...` URI, decode and upload
   to S3 first, then use the S3 URL going forward (LinkedIn/X require fetchable
   URLs, not data URIs).

Then for each platform in `[linkedin, facebook, instagram, twitter]` it looks
up the user's `SocialAccount` row (own user OR `assigned_to_user_id == user_id`
for team-member token-sharing) and fires the platform-specific API:

| Platform   | Endpoint                                                         | Media path                                              |
| ---------- | ---------------------------------------------------------------- | ------------------------------------------------------- |
| LinkedIn   | `POST /v2/ugcPosts`                                              | `linkedin_upload_image()` returns `asset_urn` first     |
| Facebook   | `POST /v19.0/{page_id}/{photos\|feed}`                           | Inline URL param                                        |
| Twitter/X  | v1.1 `media/upload` then v2 statuses                             | `twitter_upload_media_from_url()`, content capped @ 277 |
| Instagram  | (via FB Graph) `POST /v19.0/{ig_id}/media` then `media_publish`  | Two-step container then publish                         |

JSON content is multi-platform aware: when `content` parses as JSON,
`json_content[platform]` is used; otherwise the literal string is used for all
platforms. For day×platform slots, the row's `targets` only contains ONE
platform anyway, so there's no cross-platform ambiguity at fire time.

### 15.9 Stage G — Status finalization

Back in `process_scheduler`:

```python
success = any(
    any(r.get("status", "").lower().startswith("success") for r in res_list)
    for res_list in actual_results.values()
)

post.status = "published" if success else "failed"
if not success:
    post.error_message = json.dumps(publish_results)
```

**Definition of success:** at least ONE account on ANY platform returned a
success status. If every account on every platform failed, the row is marked
`failed` and the full per-platform error payload is stored in `error_message`
for the user to see in the Scheduled screen.

On success, a `PublishedPost` row is also written so the post moves from the
"Scheduled" tab to the "Published" tab in the UI and counts against the monthly
`posts` quota (already debited at plan generation, so this is bookkeeping only).

### 15.10 Frontend surface

| Screen                       | File                                                                          | Hits                                                                  |
| ---------------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Strategic Blueprint (insights) | `apps/product-page/src/pages/Dashboard/components/StrategicBlueprint.jsx`   | (display-only — read of `research`, `cultural_calendar`)              |
| Campaign Plan Table          | `apps/product-page/src/pages/Dashboard/components/CampaignPlanTable.jsx`      | `POST /generate-plan`, `POST /start-campaign`                          |
| **Review Queue** (NEW)       | `apps/product-page/src/pages/Dashboard/components/CampaignReviewQueue.jsx`   | `GET /scheduled?include_awaiting_review=true` (poll 2.5 s), `POST /scheduled/{id}/approve`, `POST /scheduled/{id}/regenerate`, `DELETE /scheduled/{id}` |
| Scheduled list / calendar    | `apps/product-page/src/pages/Scheduled.jsx`                                   | `GET /scheduled` (defaults to pending-only), `PUT/DELETE /scheduled/{id}` |
| Published feed (post-fire)   | `apps/product-page/src/pages/Posts.jsx`                                       | `GET /published`                                                       |

**Important distinction:**
- The **Review Queue** passes `include_awaiting_review=true` to see static
  rows being pre-generated + research rows awaiting topic/date approval.
- The **Scheduled / Calendar** screen leaves the flag off (default false),
  so users only see rows in `pending` (= already approved and waiting to fire).
- The Calendar UI groups by date and timezone-converts back to the user's
  local time for display.

#### Dashboard step flow

```
brief ──(Generate Plan)──► plan ──(Approve & Generate Campaign)──► review
                                                                       │
                                                                       │  (queue emptied
                                                                       │   after approves
                                                                       ▼   or rejects)
                                                                    brief
```

`Dashboard.jsx::handleApprovePlan` always routes to `review` on success — even
if every slot is research-only — so the user gets a chance to confirm topics
and dates before anything moves to `pending`.

### 15.11 Statuses + transitions

```
   booking (process_bulk_campaign Pass 1)
            │
            ▼
   ┌─────────────────────────┐
   │   awaiting_review       │ ◄────────── regenerate (clears + re-runs pre-gen)
   │   content="{}",         │
   │   image_url=None         │
   └────┬────────────┬───────┘
        │            │
   (static row)  (user clicks "Reject")
        │            │
        ▼            ▼
   pre-gen      DELETE /scheduled/{id}
   (Pass 2)            │
        │              └──► (row gone)
        ▼
   awaiting_review (ready):
     content = {platform: text}
     image_url = S3 url  (only if media_type=image)
        │
   (user clicks "Approve")
        │
        ▼
   POST /scheduled/{id}/approve
        │
        ▼
   pending  ◄────────────── (research rows go straight here on approve,
        │                    with content still "{}" — JIT will fill it)
        │
   (heartbeat tick when scheduled_for <= now)
        │
        ▼
   Stage E (JIT if agentic+empty, skip if standard+populated)
        │
        ▼
   Stage F publish attempt
        │
   ┌────┴────────┐
   │             │
   ▼             ▼
   published   failed (publish error or JIT error)
   + PublishedPost
   + PublishedPostPlatform
```

**State summary:**

| Status              | Where it comes from                            | Who reads it                                              |
| ------------------- | ----------------------------------------------- | --------------------------------------------------------- |
| `awaiting_review`   | Pass 1 of `process_bulk_campaign`               | Review Queue only (with `include_awaiting_review=true`)   |
| `pending`           | `/scheduled/{id}/approve` flips it from awaiting_review; also written by single-post `/schedule` | Heartbeat scheduler + Calendar UI                          |
| `published`         | Stage F on success                              | Published feed UI; counted against quota                  |
| `failed`            | Pre-gen sanity check failure, JIT exception, or publish all-fail | Review Queue (red border + Retry) or Scheduled UI (error) |
| `awaiting_approval` | Agency editor flow (team-member-created post awaiting admin sign-off) | Not used in the strategy path                              |

### 15.12 Worked example (7-day Spenzo campaign, mixed Static + Research)

**Brief:**
```
Run a 7-day educational arc about how mid-market SaaS teams can replace
weekly pricing meetings with always-on pricing intelligence. End the week
with a soft pitch to Spenzo Pulse → https://spenzo.io/pulse. Include
this week's biggest AI marketing news on Wednesday.
```

**Inputs:** `platforms=["linkedin","twitter"]`, `days=7`, `post_type="image"`,
**DNA:** Spenzo v3.

1. **`POST /generate-plan` (`post_type=image`)** — refiner extracts USER LINKS
   `[https://spenzo.io/pulse]`. Research strategy B/C (hybrid product + AI news).
   Planning-window calendar (Jun 9–15) returns `festivals: []`. Planner emits
   **14 primary slots** (7 days × 2 platforms), all `content_type="Image"`,
   times from the best-time bands:

   | Date       | Channel   | Topic                                                  | Time   | Research |
   | ---------- | --------- | ------------------------------------------------------ | ------ | -------- |
   | 2026-06-09 | LinkedIn  | Why pricing meetings are the silent revenue killer     | 09:30  | Static   |
   | 2026-06-09 | Twitter   | Stop guessing — three ways pricing meetings break down | 09:00  | Static   |
   | 2026-06-10 | LinkedIn  | The 3 inputs a pricing meeting always misses           | 09:30  | Static   |
   | 2026-06-10 | Twitter   | Quick take: stale ledger data → bad budgets            | 12:00  | Static   |
   | 2026-06-11 | LinkedIn  | This week's biggest AI marketing news, our take        | 09:30  | **Research** |
   | 2026-06-11 | Twitter   | Hot thread on this week's AI marketing news ↓          | 09:00  | **Research** |
   | …          | …         | …                                                      | …      | …        |
   | 2026-06-15 | LinkedIn  | What "always-on pricing" actually looks like           | 09:30  | Static   |
   | 2026-06-15 | Twitter   | See it live → spenzo.io/pulse                          | 09:00  | Static   |

2. **User reviews + edits the plan table** — flips one slot's Research/Static
   chip, edits a topic. No festival slots in the window.

3. **`POST /start-campaign`** with `targets={linkedin:[<urn>], twitter:[<x_id>]}`.
   `process_bulk_campaign` runs in the background:
   - Step 0: clears 0 abandoned rows (first run).
   - Pass 1: 14 `ScheduledPost` rows inserted — 12 with `post_type="standard"`,
     2 with `post_type="agentic"`. All `media_type="image"`,
     `status="awaiting_review"`, `content="{}"`, single-platform targets.
   - Pass 2: 12 static rows pre-generated sequentially (~80 s each →
     ~16 min total).

4. **User immediately lands on the Review Queue screen.** First 2 s shows
   *"Setting up your campaign…"* spinner; once Pass 1 finishes, 14 cards
   appear. The 12 static cards show "Generating…" placeholders that flip to
   ready (image + caption) as Pass 2 completes per slot. The 2 research
   cards are immediately ready (blue placeholder, editable topic + time).

5. **User reviews + approves each card.** Edits the LinkedIn caption on
   one, regenerates the Twitter image on another, rejects one slot
   entirely. Each approve flips the row to `status="pending"`.

6. **Queue empties** → dashboard auto-bounces to the brief screen. Calendar /
   Scheduled tab now shows 13 pending rows (1 rejected).

7. **Tuesday 2026-06-09 at 09:30 IST** (= 04:00 UTC). Heartbeat sees row's
   `scheduled_for <= now`. Row is `post_type="standard"`, `content` already
   populated. **Skips JIT** — goes straight to `process_publishing_internal`.
   LinkedIn `ugcPosts` call returns 201. `post.status = "published"`,
   `PublishedPost` row inserted.

8. **Thursday 2026-06-11 at 09:30 IST** — heartbeat sees the agentic
   LinkedIn AI-news row. `media_type="image"`, content still `{}`. JIT path
   fires `generate_strategic_content(..., post_type="image")` which now hits
   the FRESH AI news from this morning's news cycle. Pipeline produces 3
   variants + 3 images. Scheduler picks V1, sets content + image_url, publishes.

### 15.13 Performance + failure modes

| Failure                                  | Where it surfaces                                          | What happens                                                                |
| ---------------------------------------- | ---------------------------------------------------------- | --------------------------------------------------------------------------- |
| Brief Guard rejects                       | Stage A — `POST /generate-plan` returns HTTP 422             | Generic message inline on Strategic Blueprint screen                        |
| Refiner STEP-0 rejects                    | Stage A                                                    | Generic per-category message (§4.2)                                          |
| Plan slot in the past                     | Stage B — safety clamp                                     | Pushed to `utcnow()+1day`, logged warning, row still booked                 |
| Pre-gen returns empty content             | Stage B — `_pregenerate_awaiting_review` sanity check       | `RuntimeError` → row → `failed`, surfaces as red card in Review Queue       |
| Pre-gen throws (Gemini error, etc)        | Stage B Pass 2                                              | Row → `failed`, error_message set, the next slot continues                  |
| User clicks Approve & Generate again      | Stage B — auto-cleanup                                      | All awaiting_review + failed rows for this user are deleted first           |
| JIT generation throws (Stage E)           | Stage E                                                    | Row → `failed`, scheduler moves on                                          |
| Stage F LinkedIn/X/FB/IG API error        | Stage F                                                    | Per-account error captured; if ALL fail → row → `failed` + error_message    |
| Lambda paused / EventBridge stopped       | Stage D                                                    | Approved (pending) posts back up, nothing fires until heartbeat resumes      |
| User edits topic on a pending row         | UI (Calendar)                                               | `PUT /scheduled/{id}` updates `campaign_brief`; JIT reads new brief at fire |
| User rejects a card in review              | UI (Review Queue)                                           | `DELETE /scheduled/{id}` removes row entirely                                |

| Stage                                | p50    | p95    | Notes                                                       |
| ------------------------------------ | ------ | ------ | ----------------------------------------------------------- |
| A — `/generate-plan`                 | 22 s   | 38 s   | Refiner + researcher + planning_window_calendar + planner   |
| B — `/start-campaign` (return)       | 30 ms  | 80 ms  | Instant; BackgroundTask runs out-of-band                    |
| B — auto-cleanup                     | 20 ms  | 200 ms | DELETE on awaiting_review + failed for the user             |
| B — Pass 1 (insert N placeholders)   | ~0.7 s/slot | 1.5 s/slot | DB-only                                              |
| B — Pass 2 pre-gen, **Image**         | 75 s   | 100 s  | Full Refiner + Research + Content + AD v2 + Gemini Image × 3 + Critic |
| B — Pass 2 pre-gen, **Text**          | **15 s** | 22 s | Skips Visualist / AD / Image / Critic                       |
| C — Review Queue first poll (warm)   | < 100 ms | 200 ms | GET `/scheduled?include_awaiting_review=true`               |
| C — `/scheduled/{id}/approve`        | 30 ms  | 80 ms  | DB write + status flip                                       |
| D — heartbeat tick (0 due)           | 5–15 ms | 50 ms  | Empty query                                                  |
| E — JIT (per slot, Image)            | 60 s   | 90 s   | Same Image pipeline as B Pass 2                              |
| E — JIT (per slot, Text)             | **15 s** | 22 s | Text mode (media_type=text passed through)                   |
| F — publish per platform              | 800 ms | 3 s    | LinkedIn media upload is the slowest path                    |

### 15.14 Component map (strategy-specific)

| Concern                          | File                                                                              |
| -------------------------------- | --------------------------------------------------------------------------------- |
| Plan generation entry            | `apps/backend/routers/content.py::handle_generate_plan` (accepts `post_type`)     |
| Plan orchestration               | `apps/backend/services/ai_service.py::generate_campaign_plan` (threads `post_type` + fetches planning_window_calendar) |
| Planner agent                    | `apps/backend/services/ai_service.py::_planner_agent` (day×platform grid, best-time bands, needs_research, festival injection) |
| Planning-window calendar         | `apps/backend/services/ai_service.py::_get_planning_window_calendar`              |
| Campaign booking entry           | `apps/backend/routers/content.py::handle_start_campaign`                          |
| Bulk-insert background task      | `apps/backend/routers/content.py::process_bulk_campaign` (Step 0 auto-cleanup → Pass 1 insert → Pass 2 pre-gen) |
| Static slot pre-generator        | `apps/backend/routers/content.py::_pregenerate_awaiting_review` (passes media_type → post_type) |
| Review Queue list endpoint       | `apps/backend/routers/publishing.py::get_scheduled_posts` (honors `include_awaiting_review`) |
| Approve endpoint                 | `apps/backend/routers/publishing.py::approve_scheduled_post` (`POST /scheduled/{id}/approve`) |
| Regenerate endpoint              | `apps/backend/routers/publishing.py::regenerate_scheduled_post` (`POST /scheduled/{id}/regenerate`) |
| Reject endpoint (existing)       | `apps/backend/routers/publishing.py::delete_scheduled_post` (`DELETE /scheduled/{id}`) |
| Edit endpoint (existing)         | `apps/backend/routers/publishing.py::update_scheduled_post` (`PUT /scheduled/{id}`) |
| Heartbeat lambda                 | `apps/backend/lambda_pinger.py`                                                    |
| Scheduler tick + JIT             | `apps/backend/routers/publishing.py::process_scheduler` (honors media_type for JIT) |
| Per-platform publish              | `apps/backend/routers/publishing.py::process_publishing_internal`                  |
| ScheduledPost model               | `apps/backend/models.py::ScheduledPost` (`status`, `post_type`, `media_type`, `campaign_brief`, `error_message`) |
| PublishedPost / PostPlatform      | `apps/backend/models.py::PublishedPost` / `PublishedPostPlatform`                  |
| Dashboard step orchestrator       | `apps/product-page/src/pages/Dashboard.jsx` (routes `brief → plan → review`)       |
| Campaign Plan Table UI            | `apps/product-page/src/pages/Dashboard/components/CampaignPlanTable.jsx`           |
| Campaign Review Queue UI          | `apps/product-page/src/pages/Dashboard/components/CampaignReviewQueue.jsx`         |
| Strategic Blueprint UI (insights) | `apps/product-page/src/pages/Dashboard/components/StrategicBlueprint.jsx`          |
| Scheduled list / calendar UI      | `apps/product-page/src/pages/Scheduled.jsx`                                        |
| Published feed UI                 | `apps/product-page/src/pages/Posts.jsx`                                            |

### 15.15 Route table — `/schedule*` endpoints

For reference, after the dedupe cleanup in §15 v2, the live routes are:

| Method | Path                                | Handler                                          | Purpose                                   |
| ------ | ----------------------------------- | ------------------------------------------------ | ----------------------------------------- |
| POST   | `/schedule`                         | `content.py::schedule_post`                      | CREATE a single scheduled post (single-post flow) |
| GET    | `/scheduled`                        | `publishing.py::get_scheduled_posts`             | LIST scheduled posts (+ awaiting_review if flag) |
| POST   | `/scheduled/{id}/approve`           | `publishing.py::approve_scheduled_post`          | NEW — approve a reviewed row              |
| POST   | `/scheduled/{id}/regenerate`        | `publishing.py::regenerate_scheduled_post`       | NEW — re-run pre-gen for an awaiting row  |
| DELETE | `/scheduled/{id}`                   | `publishing.py::delete_scheduled_post`           | CANCEL / reject a scheduled or review row |
| PUT    | `/scheduled/{id}`                   | `publishing.py::update_scheduled_post`           | EDIT a scheduled post                      |
| GET/POST | `/scheduler/process`              | `content.py::process_scheduler`                  | Heartbeat tick from AWS Lambda            |

> **Legacy note:** `content.py` previously had its own duplicate
> `GET /scheduled`, `DELETE /scheduled/{id}`, `PUT /scheduled/{id}` endpoints
> that pre-dated `publishing.py`. Because FastAPI matches the first-registered
> route, those legacy copies were silently winning route resolution and
> ignoring the new `include_awaiting_review` flag. They were removed in
> the v2 refactor; do not re-add them.

---

## Appendix E — Full Critic Agent Prompt (verbatim)

> Built by `_critic_agent()`. Variables: `{user_context}`, `{campaign_brief}`,
> `{final_payload}`, `{BANNED_PHRASES_LIST_STR}`.

```
You are the Senior Strategic Critic for the following user profile:
{user_context}

ORIGINAL BRIEF: "{campaign_brief}"
GENERATED PAYLOAD: {final_payload}

### PIPELYT STYLE RULES (treat as ground truth)
- ALL platforms use PLAIN TEXT. Bold Unicode (𝗔𝗕𝗖) is FORBIDDEN everywhere.
  Flag its PRESENCE as a critical error. Do NOT flag its absence.
- Twitter/X: ≤ 240 chars, 0-1 hashtag, no emojis.
- LinkedIn: ≤ 1500 chars, 3-5 hashtags, arrow (↳) bullets OK.
- Facebook: ≤ 1200 chars, 1-3 hashtags.
- Instagram: ≤ 2200 chars, 8-12 hashtags.
- CTAs must be reply-bait or save-bait. NEVER "share this", "tag a friend",
  "follow us", "don't miss out".
- Banned corporate phrases: {BANNED_PHRASES_LIST_STR}
- First 5 words of every post must be a pattern interrupt.

VERIFICATION CHECKLIST:
1. Does every variant open with a strong hook (not a setup)?
2. Is any banned corporate phrase still present?
3. Are hashtag counts within platform caps?
4. Are CTAs reply-bait or save-bait?
5. Is content factually aligned with the original brief?
6. Are the 3 variants structurally different (contrarian vs question vs proof-stack)?
7. Are the visuals distinct in layout?

Return STRICTLY in this JSON format:
{
    "is_valid": true | false,
    "critique": "Overall strategic assessment...",
    "adjustments": "Specific actionable fixes if any (otherwise empty string)"
}

Set is_valid=false ONLY if a real growth-blocking issue exists.
Do NOT raise false alarms about Bold Unicode being missing — that's intentional.
```
