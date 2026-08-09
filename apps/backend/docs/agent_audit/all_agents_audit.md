# Pipelyt AI Agents — Consolidated Audit Report

**Structure:** each agent gets 4 sections — purpose, model + parameters, retry, fallback.

---

## Table of Contents

1. [Business DNA Extractor](#1-business-dna-extractor)
2. [REFINER](#2-refiner)
3. [CULTURAL_CALENDAR](#3-cultural_calendar)
4. [RESEARCHER](#4-researcher)
5. [COPYWRITER](#5-copywriter)
6. [CRITIC](#6-critic)
7. [SENTIMENT_ANALYZER (auto-comment analyzer)](#7-sentiment_analyzer)
8. [COMMENT_REPLY_AGENT (auto-comment reply)](#8-comment_reply_agent)
9. [ART_DIRECTOR / Agent 1 (image posts)](#9-art_director--agent-1)
10. [CAROUSEL_DIRECTOR](#10-carousel_director)
11. [IMAGE_GENERATOR / Agent 2 (gpt-image-2)](#11-image_generator--agent-2)

---

# 1. Business DNA Extractor

## Section 1 — Agent name & purpose

**Agent name:** Business DNA Extractor
**Code location:** [`services/business_dna_service.py`](../../services/business_dna_service.py) — `extract_business_dna()`
**Runs when:** User connects a new brand OR clicks "Re-extract DNA" in Profile.
**Frequency:** Once per brand connect (not per campaign).

**Purpose:**
Build the FOUNDATION knowledge base that every downstream campaign-time agent
reads. This is what makes Pipelyt "know" the brand.

Produces per brand:
- **Brand color palette** — 4 hex codes (primary / secondary / accent / background) extracted from the homepage screenshot
- **Company / product name + tagline**
- **Logo URL** — HEAD-verified reachable (with favicon fallback)
- **Detailed overview** — ~5-10k chars of structured knowledge (offerings, value prop, target audience, tone, etc.)
- **Category classification** — one of `saas_product` / `software_service` / `physical_product` / `hardware_service` — drives which industry playbook the image + carousel pipelines load

**Upstream inputs (before Gemini sees anything):**

| Source | Purpose | Fetched in parallel? |
|---|---|---|
| Jina AI (`r.jina.ai/`) | Markdown-rendered homepage content | ✅ |
| Microlink | Title / description / logo URL / homepage screenshot | ✅ |
| Jina Fast (`r.jina.ai/g/`) | Markdown of every discovered nav link (sub-pages) | ✅ (one thread per sub-page) |

Compiled markdown is truncated to **80,000 chars** before Gemini sees it.

## Section 2 — Model name & parameters

### Primary model

| Property | Value |
|---|---|
| **Model name** | `gemini-flash-lite-latest` |
| **Resolves to** | Gemini 3.1 Flash-Lite |
| **Input pricing** | $0.25 per 1M tokens |
| **Output pricing** | $1.50 per 1M tokens |
| **Temperature** | `0.4` |
| **Response MIME type** | `application/json` |
| **Max output tokens** | Not set (uses model default) |
| **Multimodal input** | Yes — accepts screenshot bytes as `Part.from_bytes(mime='image/png')` |

### Fallback model

| Property | Value |
|---|---|
| **Model name** | `gemini-2.5-flash-lite` |
| **Version** | Explicit stable (not an alias) |
| **Input pricing** | **$0.10 per 1M tokens** (60% cheaper than primary) |
| **Output pricing** | **$0.40 per 1M tokens** (73% cheaper than primary) |
| **Temperature** | `0.4` (same as primary) |
| **Response MIME type** | `application/json` (same) |
| **Multimodal input** | Yes (verified — screenshot bytes still accepted) |

### Estimated cost per extract

| Scenario | Input tokens | Output tokens | Cost |
|---|---|---|---|
| Primary succeeds | ~40,000 (markdown + 1,600 image tokens) | ~5,000-10,000 | **~$0.02-0.03** |
| Fallback fires (primary exhausted) | +40,000 more | +5,000-10,000 more | +**~$0.01** overhead |

Not a hot cost driver — typical workspace has ~5-10 brands connected.

## Section 3 — Retry mechanism

### ✅ 3-retry with exponential backoff
Wraps the primary Gemini call with `call_with_retry(fn, label="Gemini/BUSINESS_DNA/gemini-flash-lite-latest")` from [`services/retry_helper.py`](../../services/retry_helper.py).

| Attempt | Delay before | Behavior |
|---|---|---|
| 1 | 0s | Initial call |
| 2 | **5s** | Retry after transient error |
| 3 | **10s** | Retry after transient error |
| 4 | **20s** | Last attempt |
| — | — | If still failing → exit retry loop → fall back to secondary model |

### Retry classification (from `retry_helper._classify_error`)

| Error type | Detected by | Behavior |
|---|---|---|
| **Transient** (worth retrying) | `429 quota`, `429 rate limit`, `500`, `502`, `503`, `504`, timeout, network | Retry with backoff |
| **Spend cap** (fatal) | `"spending cap"`, `"insufficient_quota"`, `"exceeded your current quota"` | Fail fast — no retries wasted |
| **Auth** (fatal) | `401`, `403`, `invalid_api_key`, `permission_denied` | Fail fast |
| **Bad input** (fatal) | `400`, `invalid_request_error`, `missing required parameter` | Fail fast |

### Log signals

**Success on first attempt (normal):**
```
(no retry logs — silent)
```

**Transient error recovered on retry:**
```
[retry] Gemini/BUSINESS_DNA/gemini-flash-lite-latest: TRANSIENT error on attempt 1/4 — sleeping 5.0s then retrying | 429 rate limit
```

**All 4 attempts exhausted (triggers fallback):**
```
[retry] Gemini/BUSINESS_DNA/gemini-flash-lite-latest: TRANSIENT error on attempt 4/4 — retries exhausted, falling back
```

## Section 4 — Fallback

### Multi-layer defence-in-depth

If the primary Gemini call exhausts retries, the DNA extractor cascades through
progressively simpler recovery strategies rather than failing to the user.

### Layer 1 — Fallback model
**Trigger:** Primary `gemini-flash-lite-latest` exhausted all 4 retry attempts (transient error persists).

**Action:** Automatically retry the same prompt with `gemini-2.5-flash-lite` — a
different underlying version (2.5 vs. 3.1) so it survives cases where Google
hot-swaps the `-latest` alias to a broken preview.

The fallback model gets its OWN 3-retry policy — so worst-case 8 total attempts
across two models before final failure.

**Log signals:**
```
[DNA] primary model 'gemini-flash-lite-latest' exhausted retries — falling back to 'gemini-2.5-flash-lite'. Last error: ...
[DNA] fallback model 'gemini-2.5-flash-lite' succeeded after primary failed
```

**Verification:** Both models tested live with JSON mime type — both return valid JSON, so the fallback is functionally equivalent for the DNA use case.

### Layer 2 — JSON parse fallback

**Trigger:** Gemini returned text but `json.loads` throws (usually raw newlines inside long `overview` strings).

**Action:** In `_parse_dna_json` — try strict parse first, on failure use regex to extract as many top-level fields as possible (`company_name`, `tagline`, `logo_url`, `category`, etc.).

**Log signal:**
```
Regex fallback recovered fields: ['company_name', 'tagline', 'category']
```

### Layer 3 — Field-level fallbacks

Applied after Gemini result is parsed (however partially):

| Field | Fallback logic |
|---|---|
| `product_name` (product mode only) | Domain part of URL (`.split('.')[0].capitalize()`) |
| `logo_url` | Cascade → AI candidate (HEAD-checked) → Microlink candidate → **Google Favicon** (`https://www.google.com/s2/favicons?domain={host}&sz=256`) |
| `tagline` | Auto-generated: `f"{company_name}: Elevating your digital presence."` |
| `category` | Whitelist against 4 valid slugs; anything else → empty string (downstream falls back to default SaaS prompt) |

### Layer 4 — Complete failure (both models + all retries fail)

**Trigger:** Both primary and fallback models exhausted all retries.

**Action:** Return an error-shaped dict so the user's DNA never ends up completely blank:

```python
{
    "error": str(e),
    "company_name": url,        # so UI shows something
    "overview": f"Failed to extract for {url}: {e}"
}
```

The frontend shows a "DNA extraction failed" banner + a Retry button.

**Log signal:**
```
[DNA] BOTH models failed. primary=... fallback=...
DNA Extraction Error: <error message>
```

### Failure recovery estimate

Before this work: **~30%** of failed brand connects were transient errors that required manual retry.
After 3-retry + fallback model: **<5%** estimated final failure rate.

### Summary matrix

| Failure mode | Layer that catches it |
|---|---|
| Transient 429 / 5xx / timeout | ✅ Layer 0: retry with backoff |
| Primary model permanently broken (alias hot-swap) | ✅ Layer 1: fallback to `gemini-2.5-flash-lite` |
| Gemini returned malformed JSON | ✅ Layer 2: regex extraction |
| Gemini returned partial fields | ✅ Layer 3: field-level defaults |
| Both models down / spend cap on entire Google project | ✅ Layer 4: error dict, UI banner + retry button |
| Bad URL (empty, invalid) | Caught at router boundary before extraction starts |
| Bad URL (routes to `r.jina.ai/` empty page) | Caught by service-level empty-URL guard |

---

# 2. REFINER

## Section 1 — Agent name & purpose

**Agent name:** REFINER (a.k.a. Refining Strategy Agent)
**Code location:** [`services/ai_service.py:_refine_brief_agent`](../../services/ai_service.py) (function at line 365)
**Runs when:** First stage of every `/generate-content` request — before cultural / research / content / visuals.
**Frequency:** Once per campaign generation (text, image, carousel — all post types).

**Purpose:**

Turn the user's raw campaign brief (which may be as short as `"Post about our new launch"`) into a **structured strategic brief** that downstream agents can execute against without guessing what the user meant.

Also acts as a **content guardrail** — rejects briefs that violate policy (harmful content, generic placeholders, no marketing utility) BEFORE any expensive downstream calls fire.

### Two roles in one call

| Role | What it does |
|---|---|
| **Content guardrail (STEP 0)** | Validates brief → if invalid, raises `BriefRejected` → endpoint returns 422 with a category-specific message |
| **Brief refiner** | Expands the brief into labeled sections (WHAT, WHY, TARGET AUDIENCE, TONE, ASSUMPTIONS MADE, etc.) using Business DNA + uploaded docs to fill gaps |

### Rejection categories (guardrail output)

| Category | Trigger example | User-facing message |
|---|---|---|
| `harmful` | Violence / illegal / sexual content | "We can't generate marketing content for briefs that involve violence, sexual content, or illegal activity." |
| `generic` | `"create a post"`, `"make something"` | "Your brief is too generic. Please describe what you want to promote..." |
| `no_utility` / `off_brand` | No marketable value | "This brief doesn't describe content any business could plausibly market." |
| `invalid` | Empty / unparseable | "Please provide a clearer campaign brief..." |

The refiner writes its own custom rejection message, but the **user always sees the generic per-category message** for consistency. Custom message is only logged for debugging.

### Inputs

| Input | Source |
|---|---|
| `campaign_brief` | The user's raw text from the compose box |
| `user_context` | Business DNA overview + product-specific DNA (when applicable) + uploaded document text |
| Brief quality hint | Auto-computed: `empty` / `lean` (<20 words) / `specific` (<50) / `professional` |

### Output

```json
{
  "valid": true,
  "refined_brief": "<multi-paragraph labeled-sections text block>"
}
```

The `refined_brief` string is passed downstream as the primary source of truth for RESEARCHER, COPYWRITER, ART_DIRECTOR, and CAROUSEL_DIRECTOR.

## Section 2 — Model name & parameters

### Primary model

| Property | Value |
|---|---|
| **Model name** | `gemini-flash-lite-latest` |
| **Resolves to** | Gemini 3.1 Flash-Lite |
| **Input pricing** | $0.25 per 1M tokens |
| **Output pricing** | $1.50 per 1M tokens |
| **Temperature** | `0.7` (higher than DNA — refiner needs some creativity to fill brief gaps) |
| **Top-p** | `0.95` |
| **Top-k** | `40` |
| **Response format** | JSON (parsed by `_call_agent`) |
| **Grounding / web search** | Off (`web_search=False`) — refiner works from brief + DNA only |

### Fallback model
| Property | Value |
|---|---|
| **Model name** | `gemini-2.5-flash-lite` |
| **Version** | Explicit stable (not an alias) |
| **Input pricing** | **$0.10 per 1M tokens** (60% cheaper than primary) |
| **Output pricing** | **$0.40 per 1M tokens** (73% cheaper) |
| **Same config** | Yes — inherits temperature / top_p / top_k / tools from the primary call config |
| **Verified live** | ✅ Tested (text + JSON mime type both work) |

Configuration lives in `_call_agent` at [`services/ai_service.py:30`](../../services/ai_service.py). The fallback is DEFAULT for all callers — no signature change required. Callers can opt out with `fallback_model=None`.

### Estimated cost per call

| Scenario | Input tokens | Output tokens | Cost |
|---|---|---|---|
| Typical | ~5,000-15,000 (brief + DNA + docs) | ~500-1,500 | **~$0.002-0.005** |
| Long DNA / many docs | Up to ~40,000 | Up to ~2,000 | **~$0.013** |

Very cheap. Not a hot cost driver — always <1% of any campaign's total cost.

### Typical wall-clock

- Cold: ~3-5s
- Warm / short brief: ~1-3s

## Section 3 — Retry mechanism

### ✅ 3-retry with exponential backoff (shared with all campaign-time text agents)

Uses `call_with_retry` wrapper inside `_call_agent` at [`services/ai_service.py:56`](../../services/ai_service.py) — same retry policy as CULTURAL, RESEARCHER, COPYWRITER, CRITIC, BULK_COPYWRITER.

| Attempt | Delay before | Behavior |
|---|---|---|
| 1 | 0s | Initial call |
| 2 | **5s** | Retry after transient error |
| 3 | **10s** | Retry after transient error |
| 4 | **20s** | Last attempt |
| — | — | If still failing → propagate exception → orchestrator handles it |

### Retry classification

Same smart classification as Business DNA (see Section 3 of Agent #1):
- **Transient** (429 rate limit, 5xx, timeout, network) → retry
- **Spend cap** (`"spending cap"`, `"insufficient_quota"`) → fail fast
- **Auth** (401, 403) → fail fast
- **Bad input** (400, `invalid_request_error`) → fail fast

### Log signals

**Success:**
```
[DEBUG] AI Service: Calling agent REFINER (model_name='gemini-flash-lite-latest', web_search=False)
[TRACE] refine END   ts=... dur=3.75s
[TIMING] stage=refine dur=3.75s post_type='image'
```

**Transient retry:**
```
[retry] Gemini/REFINER: TRANSIENT error on attempt 1/4 — sleeping 5.0s then retrying | 429 rate limit
```

**Retries exhausted:**
```
[retry] Gemini/REFINER: TRANSIENT error on attempt 4/4 — retries exhausted, falling back
Agent parsing error in REFINER: <error>
```

**Guardrail rejection (not a failure — expected behavior):**
```
[REFINER] rejected brief — category='generic' generic_msg='Your brief is too generic...' refiner_custom='Brief is a placeholder command...'
```
Endpoint returns HTTP 422 with the generic message.

## Section 4 — Fallback

### ✅ Model fallback
If the primary `gemini-flash-lite-latest` exhausts all 4 retry attempts, `_call_agent` automatically retries with `gemini-2.5-flash-lite` — its own 3-retry policy applies. Same prompt, same config (tools / temperature / top_p / top_k) forwarded to the fallback.

**The ledger records the ACTUAL model that served the response** — so when fallback fires, the CSV shows `gemini-2.5-flash-lite` under `REFINER_MODEL` (and the correct $0.10/$0.40 pricing applies).

Worst-case attempts before final failure: **8 total** (4 on primary + 4 on fallback).

**Log signals:**
```
[REFINER] primary model 'gemini-flash-lite-latest' exhausted retries — falling back to 'gemini-2.5-flash-lite'. Last error: ...
[REFINER] fallback model 'gemini-2.5-flash-lite' succeeded after primary failed
```

**Both fail:**
```
[REFINER] BOTH models failed. primary=... fallback=...
```

### Ripple effect — same fallback covers ALL text agents

Because the fallback lives in `_call_agent` (the shared entry point), **every agent that goes through `_call_agent`** now has fallback for free:

| Agent | Slot in ledger | Uses `_call_agent` | Has fallback now? |
|---|---|---|---|
| REFINER | REFINER | ✅ | ✅ shipped |
| CULTURAL_CALENDAR | CULTURAL | ✅ | ✅ shipped |
| RESEARCHER | RESEARCHER | ✅ | ✅ shipped |
| COPYWRITER | COPYWRITER | ✅ | ✅ shipped |
| CRITIC | CRITIC | ✅ | ✅ shipped |
| BULK_COPYWRITER | COPYWRITER | ✅ | ✅ shipped |
| SENTIMENT_ANALYZER | (not per-slot tracked) | ✅ | ✅ shipped |
| COMMENT_REPLY_AGENT | (not per-slot tracked) | ✅ | ✅ shipped |

**5 minutes of work** = all 8 text agents get resilience.

### Layer 1 — JSON parse fallback

When Gemini's response can't be `json.loads`'d:
- Regex-strips ```json / ``` code fences before parsing
- On failure, returns `{"error": "Invalid JSON", "raw": <first N chars>}`

If REFINER returns `{"error": ...}`, the orchestrator treats it as a soft failure — downstream research/content agents receive an empty `refined_brief` and work from the raw user brief instead. **No crash, degraded output.**

### Layer 2 — Content-level fallback (soft degrade)

At [`services/ai_service.py:3606`](../../services/ai_service.py):

```python
refined_brief_raw = refinement.get("refined_brief", full_brief)
```

If `refinement` came back malformed (no `refined_brief` key), the pipeline **uses the raw campaign_brief as-is** for downstream stages. Quality is worse (no structured sections, no gap-filling), but the campaign still generates.

### Layer 3 — Rejection path (not a failure — designed behavior)

`BriefRejected` exception is raised on `valid=false` responses. The `/generate-content` endpoint catches it and returns HTTP 422 with the user-facing category message. This is NOT a fallback — it's the guardrail firing correctly. But it does stop the pipeline before any downstream tokens are spent.

### Summary matrix

| Failure mode | Layer that catches it | Result |
|---|---|---|
| Transient 429 / 5xx / timeout | ✅ 3-retry with backoff on primary | Recovered silently |
| Primary model persistent failure | ✅ Fallback to `gemini-2.5-flash-lite` with own retry | Recovered — CSV shows fallback model |
| Bad JSON in Gemini response | ✅ Regex fence-strip → error dict | Downstream uses raw brief |
| Missing `refined_brief` key | ✅ `.get("refined_brief", full_brief)` | Downstream uses raw brief |
| `valid=false` (guardrail) | Endpoint returns HTTP 422 | User sees rejection message |
| Both models fully down / Google project spend cap | ❌ No further recovery | Error dict → HTTP 500 |

**Estimated final failure rate: <1%** (was ~5% before fallback).

---

# 3. CULTURAL_CALENDAR

## Section 1 — Agent name & purpose

**Agent name:** CULTURAL_CALENDAR
**Code location:** [`services/ai_service.py:_get_cultural_calendar`](../../services/ai_service.py) (around line 725)
**Runs when:** After REFINER, before RESEARCHER — during every `/generate-content` request.
**Frequency:** ~Once per day per running process (cached in-memory by UTC date). All subsequent same-day requests hit the cache — no Gemini call.

**Purpose:**

Fetch **today + tomorrow's major cultural moments** (public holidays, festivals) for India and USA so the researcher and copywriter can acknowledge them in the post if relevant.

Uses Google Search grounding (`web_search=True`) — this is a real live web query, not offline knowledge, so it catches moving-date festivals (Diwali, Eid, Easter, etc.) that vary each year.

### Strict inclusion rules (baked into the prompt)

**India — include only if:**
- Nation-wide gazetted public holiday (Independence Day, Republic Day, Diwali, Holi, Eid, Christmas), OR
- Major nationally-recognised religious festival (Raksha Bandhan, Ganesh Chaturthi, Navratri, Dussehra, Muharram, Guru Nanak Jayanti, etc.)

**USA — include only if:**
- Federal public holiday (MLK Day, Memorial Day, Thanksgiving, etc.), OR
- Top-tier mainstream marketing day (Valentine's, Halloween, Black Friday, Super Bowl Sunday)

**Explicitly excluded:**
- Single-state / single-city holidays
- Regional festivals observed only in one state (e.g. Onam outside Kerala, Pongal outside Tamil Nadu)
- "National X Day" novelty days (National Pizza Day, etc.)

### Cache behavior

```python
_CULTURAL_CACHE: dict = {}
cache_key = today_iso  # e.g. "2026-07-08"
if cache_key in _CULTURAL_CACHE:
    logger.info(f"[CULTURAL] cache hit for {cache_key}")
    return _CULTURAL_CACHE[cache_key]
```

- **In-memory cache** — one entry per UTC date
- Warm process ⇒ **0s cost** (cache hit)
- Cold process / first request of the day ⇒ ~3-5s (Google Search grounding roundtrip)

Log signals:
```
[CULTURAL] cache hit for 2026-07-08                    ← warm path (0 tokens)
[CULTURAL] cached for 2026-07-08 (IN today=0, IN tomorrow=0, US today=0, US tomorrow=0)   ← cold path result
```

Output shape (attached to research + content prompts):
```json
{
  "india_today": [ ... ],
  "india_tomorrow": [ ... ],
  "us_today": [ ... ],
  "us_tomorrow": [ ... ]
}
```

## Section 2 — Model name & parameters

### Primary model

| Property | Value |
|---|---|
| **Model name** | `gemini-flash-lite-latest` |
| **Resolves to** | Gemini 3.1 Flash-Lite |
| **Input pricing** | $0.25 per 1M tokens |
| **Output pricing** | $1.50 per 1M tokens |
| **Temperature** | `0.7` (`_call_agent` default — unmodified) |
| **Top-p / Top-k** | `0.95` / `40` |
| **Web search grounding** | ✅ **ON** — `web_search=True` (Google Search grounding tool) |
| **Response format** | JSON |

**Note:** Gemini does not allow `response_mime_type="application/json"` combined with the google_search tool, so this agent relies on prompt-side discipline + `_call_agent`'s fence-stripping to get clean JSON.

### Fallback model

| Property | Value |
|---|---|
| **Model name** | `gemini-2.5-flash-lite` |
| **Input pricing** | $0.10 per 1M tokens |
| **Output pricing** | $0.40 per 1M tokens |
| **Same web_search / config forwarded** | Yes — inherits everything from primary call |

### Estimated cost per call

| Scenario | Input tokens | Output tokens | Cost |
|---|---|---|---|
| Cold call (first request of day) | ~2,000 (prompt + grounding metadata) | ~200-500 | **~$0.001-0.002** |
| Cache hit (all subsequent same-day requests) | 0 | 0 | **$0** |

**Effective average cost per campaign: <$0.001** — cache dominates the cost profile. The initial daily fetch is amortized across dozens of requests.

## Section 3 — Retry mechanism

### ✅ 3-retry with exponential backoff (inherited from `_call_agent`)

Same 4-attempts-total policy as REFINER:

| Attempt | Delay | Behavior |
|---|---|---|
| 1 | 0s | Initial call |
| 2 | 5s | Transient retry |
| 3 | 10s | Transient retry |
| 4 | 20s | Last attempt |

Retry classification (transient / spend cap / auth / bad input) same as all `_call_agent` callers.

### Log signals

**Cold (fresh Google Search + Gemini call):**
```
[TRACE] cultural BEGIN
[DEBUG] AI Service: Calling agent CULTURAL_CALENDAR (model_name='gemini-flash-lite-latest', web_search=True)
[CULTURAL] cached for 2026-07-08 (IN today=1, IN tomorrow=0, US today=0, US tomorrow=0)
[TRACE] cultural END dur=3.15s
```

**Warm (cache hit — no Gemini call at all):**
```
[TRACE] cultural BEGIN
[CULTURAL] cache hit for 2026-07-08
[TRACE] cultural END dur=0.00s
```

**Transient retry:**
```
[retry] Gemini/CULTURAL_CALENDAR/gemini-flash-lite-latest: TRANSIENT error on attempt 1/4 — sleeping 5.0s
```

## Section 4 — Fallback

### ✅ Model fallback (via shared `_call_agent`)

If `gemini-flash-lite-latest` exhausts all 4 retry attempts, `_call_agent` auto-retries with `gemini-2.5-flash-lite`. The web_search grounding + config carry over intact.

Log signals:
```
[CULTURAL_CALENDAR] primary model 'gemini-flash-lite-latest' exhausted retries — falling back to 'gemini-2.5-flash-lite'
[CULTURAL_CALENDAR] fallback model 'gemini-2.5-flash-lite' succeeded after primary failed
```

### Layer 1 — Cache fallback (silent quality-degrade)

If today's cache miss AND both models fail, `_get_cultural_calendar` returns `{}` (empty dict) — the downstream research and content agents just don't get any festival context. **Campaign still generates** with slightly less cultural awareness. No visible user impact.

### Layer 2 — JSON parse fallback (inherited from `_call_agent`)

Same regex fence-strip + error dict logic as REFINER. Since this agent uses google_search grounding (not response_mime_type), malformed JSON is more common — the fence-strip handles most cases.

### Summary matrix

| Failure mode | Layer that catches it | Result |
|---|---|---|
| Transient 429 / 5xx / timeout | ✅ 3-retry with backoff | Recovered silently |
| Primary model persistent failure | ✅ Fallback to `gemini-2.5-flash-lite` | Recovered |
| Bad JSON (grounding doesn't allow mime type) | ✅ Regex fence-strip → error dict | Empty calendar dict returned |
| Both models fail on cold day | ✅ Empty dict returned | Downstream still generates, no festival context |
| Cache hit (99% of daily requests) | N/A — no Gemini call | 0 cost, 0ms latency |

**Impact of failure: Very low.** The cultural calendar is nice-to-have, not critical. Missing it just means posts won't mention "Happy Diwali" — but they still generate correctly.

---

# 4. RESEARCHER

## Section 1 — Agent name & purpose

**Agent name:** RESEARCHER
**Code location:** [`services/ai_service.py:_research_agent`](../../services/ai_service.py) (around line 1403)
**Runs when:** After CULTURAL_CALENDAR, before COPYWRITER — during every `/generate-content` request.
**Frequency:** Once per campaign, always.

**Purpose:**

Reads the `refined_brief` + Business DNA + cultural calendar and produces **research context** the copywriter needs to write informed posts:

- **Topic news** — recent developments on the campaign subject
- **Company news** — recent moves by the brand
- **Competitor news** — what rivals are doing on the same topic
- **Trending hashtags + keywords** — for social-media reach
- **Grounding sources** — citation URLs the frontend renders in a Sources panel

Uses `google_search` grounding for the primary path — actual live web queries, not offline knowledge. Called "always-grounded" in the log because there's no regex gate deciding whether to search; the LLM picks the search strategy per brief.

## Section 2 — Model name & parameters

### Primary model (grounded call)

| Property | Value |
|---|---|
| **Model name** | `gemini-flash-lite-latest` |
| **Input pricing** | $0.25 per 1M tokens |
| **Output pricing** | $1.50 per 1M tokens |
| **Temperature** | `0.3` (lower than REFINER — research is factual, not creative) |
| **Top-p / Top-k** | `0.95` / `40` |
| **Web search grounding** | ✅ **ON** — `web_search=True` |
| **Response format** | JSON |

### Fallback model (via shared `_call_agent`)

`gemini-2.5-flash-lite` — cheaper, older stable version. Same 3-retry.

### Secondary path — OFFLINE researcher (behavior fallback)

When the grounded call returns an error dict (e.g. quota / grounding failure), a completely different prompt fires:

| Property | Value |
|---|---|
| **Model name** | Same primary `gemini-flash-lite-latest` (with same fallback chain) |
| **Temperature** | `0.7` (higher — the LLM is now inferring rather than searching) |
| **Web search grounding** | ❌ OFF — pure LLM knowledge |
| **Prompt** | `_build_offline_research_prompt(...)` — different instructions telling the model to work from prior knowledge |

The offline path is a graceful degrade — quality is worse (no fresh news, older knowledge) but the pipeline continues.

### Estimated cost per call

| Scenario | Input tokens | Output tokens | Cost |
|---|---|---|---|
| Grounded first-try success | ~5,000-15,000 (brief + DNA + calendar) | ~1,000-3,000 | **~$0.003-0.008** |
| Grounded + offline fallback (both fire) | ~10,000-30,000 total | ~2,000-6,000 total | **~$0.006-0.015** |

Second highest Gemini cost after COPYWRITER, but still <1% of a full image/carousel campaign cost.

## Section 3 — Retry mechanism

### ✅ 3-retry with exponential backoff (inherited from `_call_agent`)

Same policy as REFINER + CULTURAL. Applies **independently** to both the grounded call AND the offline fallback call — potentially 8 total attempts on primary before fallback model even engages, and up to 16 total across both models.

### Log signals

**Grounded success:**
```
[TRACE] research BEGIN
[RESEARCH] always-grounded (intent-driven search strategy)
[DEBUG] AI Service: Calling agent RESEARCHER (model_name='gemini-flash-lite-latest', web_search=True)
[GROUND] RESEARCHER: 6 sources, 4 queries
[TRACE] research END dur=8.53s
```

**Grounded fails, offline fires (common case — grounded JSON malformed):**
```
[RESEARCH] grounded call returned error — falling back to offline (Invalid JSON)
[DEBUG] AI Service: Calling agent RESEARCHER (model_name='gemini-flash-lite-latest', web_search=False)
[TRACE] research END dur=13.19s
```

**Model fallback engaged:**
```
[RESEARCHER] primary model 'gemini-flash-lite-latest' exhausted retries — falling back to 'gemini-2.5-flash-lite'
[RESEARCHER] fallback model 'gemini-2.5-flash-lite' succeeded after primary failed
```

## Section 4 — Fallback

### Two-dimension fallback strategy

RESEARCHER is the most defensively-coded agent because it does the most work (grounded search + JSON parsing + citation extraction):

**Dimension 1 — Behavior fallback (grounded → offline):**
If the grounded call returns an error dict, the same `_call_agent` fires again with a totally different prompt (`_build_offline_research_prompt`) and `web_search=False`. This is the FIRST fallback attempt.

**Dimension 2 — Model fallback (primary → fallback model):**
Inside each `_call_agent` invocation, if the primary model exhausts retries, `gemini-2.5-flash-lite` takes over. This applies to BOTH the grounded call AND the offline call independently.

### Failure cascade

```
1. Grounded call — primary model (attempts 1-4)
   ├─ Success → return with citations ✓
   └─ Failure ↓

2. Grounded call — fallback model (attempts 1-4)
   ├─ Success → return with citations ✓
   └─ Failure ↓

3. Offline call — primary model (attempts 1-4)
   ├─ Success → return without citations (still useful)
   └─ Failure ↓

4. Offline call — fallback model (attempts 1-4)
   ├─ Success → return without citations
   └─ Failure ↓

5. Both fully failed → return error dict → orchestrator uses raw refined_brief as research context (very degraded but non-fatal)
```

**Up to 16 attempts across 4 model-call combinations** before the campaign gives up on research.

### Layer — Prompt-level defence

Both grounded and offline prompts explicitly instruct the model to return `{}` or an error field if it truly can't find anything, rather than hallucinating fake sources. Anti-hallucination is enforced by requiring the researcher to cite what it saw.

### Summary matrix

| Failure mode | Layer that catches it | Result |
|---|---|---|
| Transient 429 on grounded call | ✅ 3-retry on primary → primary succeeds on retry | Recovered silently |
| Grounded call returns malformed JSON | ✅ Offline call fires with different prompt | Sources lost, content preserved |
| Primary model persistent failure | ✅ Fallback to `gemini-2.5-flash-lite` | Recovered |
| Both grounded + offline fail on both models | ✅ Empty research dict → orchestrator continues | Copywriter works from brief only |
| Web search grounding quota exhausted | Falls through to offline path | Sources lost, content preserved |

---

# 5. COPYWRITER

## Section 1 — Agent name & purpose

**Agent name:** COPYWRITER
**Code location:** [`services/ai_service.py:_content_agent`](../../services/ai_service.py) (around line 1440)
**Runs when:** After RESEARCHER, before visuals — during every `/generate-content` request.
**Frequency:** Once per campaign generation.

**Purpose:**

Writes the actual **social media post copy** for every platform selected by the user. This is the highest-value text agent — its output is what users read and publish.

For each of `linkedin`, `twitter`, `instagram`, `facebook` (and optionally `youtube`, `tiktok`), it produces:

- **`viral_reach`** variant — visibility-flavored, wide-net hook
- **`high_interaction`** variant — comment-driven, question-heavy
- **`follower_growth`** variant — authority / depth / expertise
- **`festival_variant`** — ONLY when cultural calendar has a festival alert (else omitted)

That's typically 12-16 unique post drafts per campaign (4 platforms × 3-4 variants).

### Enforced post-processing (server-side)

After the LLM output, `_apply_content_post_processing()` enforces platform hard limits:

| Platform | Hashtag cap | Char cap |
|---|---|---|
| Twitter | 2 | 270 |
| LinkedIn | 5 | 2,800 |
| Facebook | 3 | 2,200 |
| Instagram | 15 | 2,100 |

Bold Unicode (`𝗔𝗕𝗖`) is stripped everywhere — plain-text only.

## Section 2 — Model name & parameters

### Primary model

| Property | Value |
|---|---|
| **Model name** | `gemini-flash-lite-latest` |
| **Input pricing** | $0.25 per 1M tokens |
| **Output pricing** | $1.50 per 1M tokens |
| **Temperature** | `0.7` (creative — copywriter needs voice variation) |
| **Top-p / Top-k** | `0.95` / `40` |
| **Web search grounding** | ❌ OFF (uses RESEARCHER's output; doesn't re-search) |
| **Response format** | JSON |

### Fallback model (via shared `_call_agent`)

`gemini-2.5-flash-lite` — cheaper legacy tier. Same 3-retry.

### Estimated cost per call

Highest-token text agent because the prompt attaches the full refined_brief + research + cultural calendar + DNA + platform-specific style rules:

| Scenario | Input tokens | Output tokens | Cost |
|---|---|---|---|
| Typical (4 platforms × 3 variants) | ~15,000-25,000 | ~1,500-3,000 | **~$0.005-0.010** |
| Long DNA / lots of research | ~30,000 | ~4,000 | **~$0.014** |

**Highest single Gemini text-agent cost per campaign** — but still <1% of any image/carousel campaign total.

## Section 3 — Retry mechanism

### ✅ 3-retry with exponential backoff (inherited from `_call_agent`)

Same policy as REFINER + CULTURAL + RESEARCHER.

### Log signals

**Success:**
```
[TRACE] content BEGIN
[DEBUG] AI Service: Calling agent COPYWRITER (model_name='gemini-flash-lite-latest', web_search=False)
[TRACE] content END dur=5.52s
[TIMING] stage=content dur=5.52s platforms=['linkedin', 'twitter', 'instagram', 'facebook']
```

**Model fallback engaged:**
```
[COPYWRITER] primary model 'gemini-flash-lite-latest' exhausted retries — falling back to 'gemini-2.5-flash-lite'
[COPYWRITER] fallback model 'gemini-2.5-flash-lite' succeeded after primary failed
```

## Section 4 — Fallback

### ✅ Model fallback (via shared `_call_agent`)

Automatic fallback to `gemini-2.5-flash-lite` on primary retry exhaustion. Full inheritance of behavior from `_call_agent`.

### Layer 1 — Post-processing safety net (belt-and-braces)

Even when the copywriter output is malformed or missing platforms, `_apply_content_post_processing()` still runs:

- Strips Unicode bold characters
- Trims hashtags to the per-platform cap
- Truncates to char cap (safety margin below actual API limits)
- Normalizes platform keys to lowercase (`Twitter` → `twitter`)

This means even a partially-broken copywriter response gets sanitized before it reaches the frontend.

### Layer 2 — Content-level fallback (in downstream branches)

If the copywriter returns an error dict, downstream code (visuals pipeline) reads content_dict as-is — empty platforms mean no variants can be rendered for those platforms. The pipeline continues with whichever platforms DID succeed rather than dying.

Concretely in [`services/ai_service.py`](../../services/ai_service.py):
```python
platform_content = (content_dict or {}).get(priority_platform) or {}
```

Fail-open: empty content → skip visuals for that platform, still generate for others.

### Layer 3 — Rule #9 URL fallback

The copywriter is instructed to draw URLs from Business DNA, but sometimes emits raw `${brand_url}` placeholders. `_replace_url_placeholders()` post-processing replaces those with the actual URL as a defense-in-depth fallback.

### Summary matrix

| Failure mode | Layer that catches it | Result |
|---|---|---|
| Transient 429 / 5xx / timeout | ✅ 3-retry with backoff | Recovered silently |
| Primary model persistent failure | ✅ Fallback to `gemini-2.5-flash-lite` | Recovered |
| Bold Unicode leaked through | ✅ Post-processing strips it | Clean plain-text output |
| Hashtag overflow | ✅ Post-processing caps per platform | Compliant with platform limits |
| Char cap overflow | ✅ Post-processing truncates | No API rejection |
| Missing platforms | ✅ Downstream skips visuals for those | Other platforms still generate |
| URL placeholder leaked | ✅ Post-processing replaces from DNA | Correct URL rendered |
| Both models fully fail | Downstream sees empty content_dict | Visuals stage falls back to Image Agent v4 (in image-post flow) |

---

# 6. CRITIC

## Section 1 — Agent name & purpose

**Agent name:** CRITIC
**Code location:** [`services/ai_service.py:_critic_agent`](../../services/ai_service.py) (around line 2026)
**Runs when:** LAST stage of `/generate-content`, AFTER visuals complete. Skipped entirely for `post_type='text'` and `post_type='document'` (carousel).
**Frequency:** Once per image-post campaign; NEVER for text or carousel posts.

**Purpose:**

Post-hoc **QA verification** of the entire generated payload — validates the final content against Pipelyt's style rules before the user sees it. Acts as a soft gatekeeper rather than a hard rejector.

### What it verifies

| Check | Rule |
|---|---|
| Bold Unicode present? | Must be absent (already stripped by copywriter post-processing, but critic double-checks) |
| Twitter length | ≤ 240 chars, 0-1 hashtag, no emojis |
| LinkedIn length | ≤ 1,500 chars, 3-5 hashtags |
| Facebook length | ≤ 1,200 chars, 1-3 hashtags |
| Instagram length | ≤ 2,200 chars, 8-12 hashtags |
| CTA style | Reply-bait or save-bait only; never "share this", "tag a friend", "follow us" |
| Banned corporate phrases | List of ~30 phrases forbidden |
| First-5-word hook | Must be pattern interrupt, not a setup |
| Variant differentiation | 3 variants structurally distinct (contrarian / question / proof-stack) |
| Visual distinctness | Layouts differ |

### Output

```json
{
  "is_valid": true | false,
  "critique": "Overall strategic assessment...",
  "adjustments": "Specific actionable fixes if any (otherwise empty string)"
}
```

`is_valid=false` is rare — the critic only trips on **real growth-blocking issues**, not stylistic quibbles. The `critique` string is shown to the user as an "AI Score" panel in the review UI.

### Skipped scenarios (by design)

- `post_type='text'` → text posts skip critic (no visuals to critique, and no images means less to go wrong)
- `post_type='document'` (carousel) → carousel decks skip critic (director already does deck-level QA in its prompt)
- Image posts with 0 successful variants → critic still runs but has less to grade

Log signal on skip:
```
[TRACE] critic END dur=0.00s
[TIMING] stage=critic dur=0.00s skipped=True
```

## Section 2 — Model name & parameters

### Primary model

| Property | Value |
|---|---|
| **Model name** | `gemini-flash-lite-latest` |
| **Input pricing** | $0.25 per 1M tokens |
| **Output pricing** | $1.50 per 1M tokens |
| **Temperature** | `0.7` (`_call_agent` default) |
| **Top-p / Top-k** | `0.95` / `40` |
| **Web search grounding** | ❌ OFF |
| **Response format** | JSON |

### Fallback model (via shared `_call_agent`)

`gemini-2.5-flash-lite` — same fallback pattern.

### Estimated cost per call

Critic receives the ENTIRE final payload as its prompt input, so token counts are high:

| Scenario | Input tokens | Output tokens | Cost |
|---|---|---|---|
| Typical image post | ~10,000-20,000 (all variants + brief + DNA) | ~100-500 (short verdict + adjustments) | **~$0.003-0.006** |

Small relative to the campaign total (~$0.85 image post), but not negligible.

## Section 3 — Retry mechanism

### ✅ 3-retry with exponential backoff (inherited from `_call_agent`)

Same policy as all other text agents.

### Log signals

**Success:**
```
[TRACE] critic BEGIN
[DEBUG] AI Service: Calling agent CRITIC (model_name='gemini-flash-lite-latest', web_search=False)
[TRACE] critic END dur=2.08s
[TIMING] stage=critic dur=2.08s skipped=False
```

**Skipped by design:**
```
[TRACE] critic BEGIN
Skipping critic — post_type='text'
[TRACE] critic END dur=0.00s
```

**Model fallback engaged:**
```
[CRITIC] primary model 'gemini-flash-lite-latest' exhausted retries — falling back to 'gemini-2.5-flash-lite'
[CRITIC] fallback model 'gemini-2.5-flash-lite' succeeded after primary failed
```

## Section 4 — Fallback

### ✅ Model fallback (via shared `_call_agent`)

Same as all text agents — automatic fallback to `gemini-2.5-flash-lite` on retry exhaustion.

### Layer 1 — Non-blocking failure

If the CRITIC returns an error dict (both models exhausted), the campaign STILL SHIPS. The orchestrator treats the critique as advisory metadata, not a gate. Users see a message like:

```
"AI review skipped — content generated but not verified"
```

They can still publish. This is the KEY design difference vs. REFINER — refiner failure kills the request, critic failure just removes the advisory panel.

### Layer 2 — is_valid soft rejection (behavior fallback)

Even when `is_valid=false`, the critic does NOT block the response. The campaign is still returned with the critique attached. Users decide whether to regenerate or edit.

This is intentional — critic is often over-cautious (flagging missing bold Unicode when it's supposed to be absent, for example). Better to show the critique and let the human decide.

### Summary matrix

| Failure mode | Layer that catches it | Result |
|---|---|---|
| Transient 429 / 5xx / timeout | ✅ 3-retry with backoff | Recovered silently |
| Primary model persistent failure | ✅ Fallback to `gemini-2.5-flash-lite` | Recovered |
| Both models fail | ✅ Campaign still ships without critique | User sees "AI review skipped" |
| `is_valid=false` verdict | Advisory only — campaign still shows to user | User decides |
| Skipped (text/document post types) | By design — no critic call | 0 cost, 0 latency |

---

# 7. SENTIMENT_ANALYZER

## Section 1 — Agent name & purpose

**Agent name:** SENTIMENT_ANALYZER (auto-comment analyzer)
**Code location:** [`services/ai_service.py:analyze_comments_sentiment`](../../services/ai_service.py) (around line 4359)
**Runs when:** User opens the "Comments" panel for a published post (Nexus / analytics flow), OR when the auto-comment cron picks up new comments to classify.
**Frequency:** Once per batch of comments (up to N comments per call). Not per-campaign; per-comment-batch.

**Purpose:**

Classify incoming social media comments into **Positive / Neutral / Negative** so the auto-reply agent knows which ones to reply to, and the analytics dashboard can show sentiment counts.

### The 80/20 classification principle (baked into the prompt)

- **80% weight — Contextual relevance:** How directly does the comment relate to the post's core business offer?
- **20% weight — Linguistic sentiment:** Emotional tone (praise / anger / sarcasm)

### Classification tiers

| Tier | Trigger | Example |
|---|---|---|
| **POSITIVE (success/lead)** | High relevance to post + positive tone | Deep question about the business topic, praise, "how do I sign up?" |
| **NEUTRAL (passive)** | Low relevance / generic engagement | "Interesting", "Cool", "@user check this out" |
| **NEGATIVE (irrelevant/toxic)** | Off-topic / abusive / spam | Toxicity, unrelated content, purely destructive complaints |

### Output shape

```json
{
  "overall_score": 0-100,
  "overall_summary": "Summary of business engagement quality",
  "top_insight": "Actionable 80/20 insight",
  "sentiment_counts": { "positive": 0, "neutral": 0, "negative": 0 },
  "analyzed_comments": [
    {
      "id": "comment_id",
      "sentiment_label": "Positive | Neutral | Negative",
      "sentiment_score": 0-100,
      "reasoning": "80% Relevance + 20% Tone → verdict"
    }
  ]
}
```

## Section 2 — Model name & parameters

### Primary model

| Property | Value |
|---|---|
| **Model name** | `gemini-flash-lite-latest` |
| **Input pricing** | $0.25 per 1M tokens |
| **Output pricing** | $1.50 per 1M tokens |
| **Temperature** | **`0.0`** (deterministic — same input must produce same classification) |
| **Top-p / Top-k** | `0.95` / `40` |
| **Web search grounding** | ❌ OFF |
| **Response format** | JSON |

Temperature 0.0 is the KEY parameter here — sentiment must be consistent so the same comment doesn't flip between positive/negative across runs. All other text agents use temperature 0.3-0.7.

### Fallback model (via shared `_call_agent`)

`gemini-2.5-flash-lite` — cheaper legacy tier. Temperature 0.0 is preserved on the fallback call.

### Estimated cost per call

Depends heavily on batch size:

| Batch size | Input tokens | Output tokens | Cost |
|---|---|---|---|
| 10 comments | ~3,000 | ~1,500 | **~$0.003** |
| 50 comments | ~10,000 | ~5,000 | **~$0.010** |
| 100 comments | ~20,000 | ~10,000 | **~$0.020** |

Very cheap per comment — even a moderator handling 1,000 comments/day costs only ~$0.20.

## Section 3 — Retry mechanism

### ✅ 3-retry with exponential backoff (inherited from `_call_agent`)

Same policy as every other `_call_agent` caller:

| Attempt | Delay |
|---|---|
| 1 | 0s |
| 2 | 5s |
| 3 | 10s |
| 4 | 20s |

### Log signals

**Success:**
```
[DEBUG] AI Service: Calling agent SENTIMENT_ANALYZER (model_name='gemini-flash-lite-latest', web_search=False)
```

**Transient retry:**
```
[retry] Gemini/SENTIMENT_ANALYZER/gemini-flash-lite-latest: TRANSIENT error on attempt 1/4 — sleeping 5.0s
```

**Model fallback engaged:**
```
[SENTIMENT_ANALYZER] primary model 'gemini-flash-lite-latest' exhausted retries — falling back to 'gemini-2.5-flash-lite'
[SENTIMENT_ANALYZER] fallback model 'gemini-2.5-flash-lite' succeeded after primary failed
```

## Section 4 — Fallback

### ✅ Model fallback (via shared `_call_agent`)

Automatic fallback to `gemini-2.5-flash-lite`. Temperature 0.0 preserved.

### Layer 1 — Empty-input short-circuit

If no comments are passed in, returns `{"error": "No comments to analyze"}` without calling Gemini at all. Zero cost, zero latency.

### Layer 2 — JSON parse fallback (inherited from `_call_agent`)

Regex fence-strip + error dict logic. If parsing fails, caller receives `{"error": "Invalid JSON", "raw": "..."}` and can decide whether to retry the whole batch or skip.

### Layer 3 — Per-comment resilience

If Gemini returned SOME comments classified but not all, the analytics dashboard shows what it has AND flags the missing ones — better than dropping the whole batch. The `analyzed_comments` array is treated as best-effort.

### Summary matrix

| Failure mode | Layer that catches it | Result |
|---|---|---|
| No comments to analyze | ✅ Short-circuit before Gemini | Empty result, 0 cost |
| Transient 429 / 5xx / timeout | ✅ 3-retry with backoff | Recovered silently |
| Primary model persistent failure | ✅ Fallback to `gemini-2.5-flash-lite` | Recovered |
| Bad JSON in response | ✅ Regex fence-strip → error dict | Caller can retry batch |
| Both models fail | Caller (Nexus dashboard) shows "sentiment unavailable" | Dashboard degrades gracefully |
| Inconsistent classification across runs | ✅ Temperature 0.0 prevents this | Same input → same output |

---

# 8. COMMENT_REPLY_AGENT

## Section 1 — Agent name & purpose

**Agent name:** COMMENT_REPLY_AGENT (auto-comment reply generator)
**Code location:** [`services/ai_service.py:generate_replies_for_comments`](../../services/ai_service.py) (around line 4427)
**Runs when:** After SENTIMENT_ANALYZER classifies comments, OR when the user clicks "Generate replies" for a batch.
**Frequency:** Once per batch of comments needing replies.

**Purpose:**

Generate **professional, context-aware AI replies** for social media comments — matching the brand's voice from Business DNA and the specific post context.

Uses THREE inputs to shape each reply:

1. **Business DNA** — brand voice / tone / values
2. **Post context** — the original post the comments are responding to
3. **Comment content** — what the user actually said

### Context-aware behavior baked into the prompt

| Post type | Reply strategy |
|---|---|
| **Hiring/Recruitment** | If post has email → direct commenter to send resume there. If DNA has email → use that. |
| **Product/Offer** | Answer from DNA facts. Helpful, not pushy. |
| **Positive/Praise comment** | Gratitude + warmth |
| **Neutral/Question comment** | Answer clearly, invite conversation |
| **Negative/Toxic comment** | Professional, de-escalate, or offer DM. NOT defensive. |

### Anti-hallucination guardrails

- "Never invent facts about the company or product not in the DNA"
- If unsure → suggest support email or website
- Match brand tone from DNA (professional / playful / authoritative)

### Output shape

```json
{
  "replies": [
    {
      "id": "comment_id",
      "generated_reply": "The AI-generated reply text",
      "reasoning": "Brief explanation of why this reply was chosen"
    }
  ]
}
```

## Section 2 — Model name & parameters

### Primary model

| Property | Value |
|---|---|
| **Model name** | `gemini-flash-lite-latest` |
| **Input pricing** | $0.25 per 1M tokens |
| **Output pricing** | $1.50 per 1M tokens |
| **Temperature** | **`0.7`** (creative — need reply variation, not deterministic) |
| **Top-p / Top-k** | `0.95` / `40` |
| **Web search grounding** | ❌ OFF |
| **Response format** | JSON |

Higher temperature than SENTIMENT_ANALYZER (0.7 vs 0.0) — same comment should NOT always get the exact same reply. Reply variation is desirable.

### Fallback model (via shared `_call_agent`)

`gemini-2.5-flash-lite` — cheaper legacy tier. Temperature preserved.

### Estimated cost per call

Higher than SENTIMENT_ANALYZER because reply generation produces more output tokens:

| Batch size | Input tokens | Output tokens | Cost |
|---|---|---|---|
| 10 comments (with DNA + post context) | ~5,000 | ~3,000 | **~$0.006** |
| 50 comments | ~15,000 | ~10,000 | **~$0.019** |
| 100 comments | ~30,000 | ~20,000 | **~$0.038** |

Still cheap. A brand handling 500 auto-replies per day pays ~$0.10-0.15/day.

## Section 3 — Retry mechanism

### ✅ 3-retry with exponential backoff (inherited from `_call_agent`)

Same 4-attempts-total policy as every other text agent.

### Log signals

**Success:**
```
[DEBUG] AI Service: Calling agent COMMENT_REPLY_AGENT (model_name='gemini-flash-lite-latest', web_search=False)
```

**Transient retry:**
```
[retry] Gemini/COMMENT_REPLY_AGENT/gemini-flash-lite-latest: TRANSIENT error on attempt 1/4 — sleeping 5.0s
```

**Model fallback engaged:**
```
[COMMENT_REPLY_AGENT] primary model 'gemini-flash-lite-latest' exhausted retries — falling back to 'gemini-2.5-flash-lite'
[COMMENT_REPLY_AGENT] fallback model 'gemini-2.5-flash-lite' succeeded after primary failed
```

## Section 4 — Fallback

### ✅ Model fallback (via shared `_call_agent`)

Automatic fallback to `gemini-2.5-flash-lite`. Prompt + config preserved.

### Layer 1 — Empty-input short-circuit

If `comments` list is empty, returns `{"replies": []}` immediately without calling Gemini.

### Layer 2 — Empty DNA / post context fallback

If DNA is missing, the prompt uses `"Professional Business"` as a default persona. If post context is missing, uses `"General brand engagement"`. Reply quality is degraded (no brand voice matching) but the agent still produces something usable.

```python
{business_dna or "Professional Business"}
{post_context or "General brand engagement."}
```

### Layer 3 — Human moderation as final gate

Auto-replies are typically shown to the user for **approval before posting** — the user can edit or reject before anything actually publishes to social media. So even if Gemini generates a bad reply, no damage is done to the brand's public presence. **This is the ultimate fallback.**

### Layer 4 — Per-reply resilience

If Gemini returned replies for SOME comments but not others, the caller (Nexus auto-reply queue) posts what it has and leaves the missing ones in the queue for the next run. **Fail-open by design.**

### Summary matrix

| Failure mode | Layer that catches it | Result |
|---|---|---|
| No comments to reply to | ✅ Short-circuit before Gemini | Empty result, 0 cost |
| Missing DNA | ✅ Default "Professional Business" persona | Generic professional replies |
| Missing post context | ✅ Default "General brand engagement" | Generic replies |
| Transient 429 / 5xx / timeout | ✅ 3-retry with backoff | Recovered silently |
| Primary model persistent failure | ✅ Fallback to `gemini-2.5-flash-lite` | Recovered |
| Bad reply generated | ✅ Human moderation step | User edits/rejects before publishing |
| Both models fail | Comments stay in queue for next run | No public damage, just delay |
| Partial batch success | ✅ Per-reply granularity | Post what succeeded, retry the rest |

---

# 9. ART_DIRECTOR / Agent 1

## Section 1 — Agent name & purpose

**Agent name:** ART_DIRECTOR (a.k.a. Magic Agent 1 / Magic Prompt Generator)
**Code location:** [`services/magic_image_pipeline.py:_call_agent1_magic_prompt`](../../services/magic_image_pipeline.py) (around line 1015)
**Runs when:** During image posts — the visuals stage of `/generate-content` when `post_type='image'`.
**Frequency:** Once per variant × 3 variants per campaign = **3 parallel calls** per image post (in ThreadPoolExecutor).

**Purpose:**

Take the campaign brief + business DNA + platform captions + the Physical Product playbook (if category matches) and produce a **single detailed image_prompt string** that gets fed straight to gpt-image-2.

This is the "brain" that decides what the image should look like — model face preservation, product placement, festival palette, offer flyer layout, etc. Every constraint we've been fighting (Mode C bust-beside-model, offer text bloat, model-outfit-copied-from-ref) is enforced HERE via the playbook overrides.

### Inputs

| Input | Purpose |
|---|---|
| System prompt | Fixed template with 2 variants (TEMPLATE A / TEMPLATE B) + variant_type routing rules |
| User prompt | Playbook (if category=physical_product) + variant type + brand name + POST_CONTENT + brand color + aspect ratio + campaign brief |
| Logo bytes (multimodal) | Attached as image so the model can "see" the brand mark |
| `variant_type` | `viral_reach` / `high_interaction` / `follower_growth` / `festival_variant` (drives template selection) |
| `business_category` | `physical_product` / `saas_product` / etc. — decides which playbook injects |

### Playbook overrides applied

For `business_category='physical_product'`, a ~20K-character playbook injects at the top of the user prompt with:

- CRITICAL OVERRIDE #1 — REWRITE THE TEMPLATE, DON'T FILL IT (post caption stripping)
- CRITICAL OVERRIDE #2 — MODE C COMPOSITING VERB (put product from Image 1 on model in Image 2)
- CRITICAL OVERRIDE #3 — TEMPLATE SELECTION FOR MODE C / OFFERS (force TEMPLATE B for all variants)
- CRITICAL OVERRIDE #4 — MODE C FACE IS THE BRAND (face preservation)

Plus 6-combo Mode × Intent matrix, offer flyer anatomy, palette rules, composition patterns, and NEVER-RENDER lists.

### Output

A ~2,000-4,000 character single-string image_prompt that gets passed as `prompt` to gpt-image-2's `images.edit()` or `images.generate()` endpoint.

## Section 2 — Model name & parameters

### Primary model

| Property | Value |
|---|---|
| **Model name** | `gpt-5.1` (env-overridable via `MAGIC_AGENT1_MODEL`) |
| **Family** | GPT-5 series (adaptive reasoning) |
| **Input pricing** | $5.00 per 1M tokens |
| **Output pricing** | $30.00 per 1M tokens |
| **Cached input** | $0.50 per 1M tokens (2026 discount tier) |
| **Max completion tokens** | 8,000 (covers reasoning + output) |
| **Reasoning effort** | `low` (env: `MAGIC_AGENT1_REASONING_EFFORT`) — prompt-writing is not deep reasoning |
| **Temperature** | 0.7 (env: `MAGIC_AGENT1_TEMPERATURE`) — ignored by GPT-5 family (uses default) |
| **Top-p** | 1.0 |
| **Multimodal input** | ✅ Yes — logo image attached as `image_url` block |

### Why GPT-5.1 over GPT-5

Based on OpenAI's Nov 2025 release notes:

| Improvement in 5.1 | Why it matters for Art Director |
|---|---|
| **Adaptive reasoning** | Faster on simple variants (viral_reach with no refs) — only reasons deeply when Mode C playbook activates |
| **Better instruction-following** on constraint-heavy prompts | Our playbook has 4 CRITICAL OVERRIDE blocks + 6 sub-modes — 5.1 obeys them more reliably (fewer "product on bust" bugs, fewer "silver sequin blouse copied" bugs) |
| **Same pricing** as gpt-5 | No cost penalty for the upgrade |
| **Same output token limits** | No max-tokens tuning needed |

### Fallback model

| Property | Value |
|---|---|
| **Model name** | `gpt-5` (env-overridable via `MAGIC_AGENT1_FALLBACK_MODEL`) |
| **Family** | GPT-5 (original, older stable version) |
| **Input pricing** | $5.00 per 1M tokens (identical to primary) |
| **Output pricing** | $30.00 per 1M tokens |
| **Independence rationale** | Different underlying training version — survives 5.1-specific outages or hot-swap issues |
| **Verified live** | ✅ Both models tested — both respond correctly to the same prompt |

### Estimated cost per campaign (3 variants)

| Scenario | Input tokens per variant | Output tokens per variant | Total (3 variants) |
|---|---|---|---|
| Typical (physical_product with playbook) | ~8,500-9,000 | ~1,200-2,400 (incl. reasoning) | **~$0.15-0.25** |
| Simple (SaaS category, no playbook) | ~2,500 | ~800-1,200 | **~$0.08-0.15** |
| Complex (Mode C + OFFER with playbook) | ~9,500 | ~2,400 | **~$0.27** |

Second-highest OpenAI cost after gpt-image-2 renders. Real observed: **$0.13-0.27 per image campaign** on the current playbook.

## Section 3 — Retry mechanism

### ✅ 3-retry with exponential backoff (via `call_with_retry`)

Same retry policy as the Gemini text agents. Wrapped around the OpenAI `chat.completions.create` call:

| Attempt | Delay | Behavior |
|---|---|---|
| 1 | 0s | Initial call |
| 2 | 5s | Retry after transient error |
| 3 | 10s | Retry after transient error |
| 4 | 20s | Last attempt on primary → then switch to fallback model |

### OpenAI-specific retry classification

The `retry_helper._classify_error` recognizes OpenAI-specific error codes:

| Error type | Detected by | Behavior |
|---|---|---|
| **Transient** | `429 rate limit`, `500`, `502`, `503`, `504`, timeout | Retry |
| **Spend cap / quota** | `"insufficient_quota"`, `"exceeded your current quota"` | Fail fast (retry can't help) |
| **Auth** | `401`, `invalid_api_key` | Fail fast |
| **Bad input** | `400`, `invalid_request_error`, `"Missing required parameter"` | Fail fast |

The 502 storms we saw earlier (Cloudflare origin errors on OpenAI's side) are correctly classified as transient — retries recover them.

### Log signals

**Success:**
```
[TRACE] variant=viral_reach agent1 BEGIN
[magic] Agent 1 produced 2860 chars in 12.11s variant=viral_reach model=gpt-5.1 tokens=in:2475/out:912/reasoning:256
[TRACE] variant=viral_reach agent1 END dur=12.14s
```

**Transient retry:**
```
[retry] OpenAI/Agent1/viral_reach/gpt-5.1: TRANSIENT error on attempt 1/4 — sleeping 5.0s
```

**Primary exhausted, fallback engaged:**
```
[magic] Agent 1 primary model 'gpt-5.1' exhausted retries for variant=viral_reach — falling back to 'gpt-5'
[magic] Agent 1 fallback model 'gpt-5' succeeded after primary failed
[magic] Agent 1 produced 2660 chars in 15.20s variant=viral_reach model=gpt-5 ...   ← note model=gpt-5 in log
```

**Both models exhausted:**
```
[magic] Agent 1 BOTH models failed for variant=viral_reach. primary=... fallback=...
```

## Section 4 — Fallback

### ✅ Model fallback (gpt-5.1 → gpt-5)

If `gpt-5.1` exhausts all 4 retry attempts, the same api_kwargs are cloned with the model swapped to `gpt-5` and fired again with a fresh 3-retry policy. Worst-case attempts before final failure: **8 total** across two models.

**The ledger records the ACTUAL model that served the response** — so when fallback fires, the CSV shows `ART_DIRECTOR_MODEL = gpt-5` (not gpt-5.1). Since both are priced identically, cost math is unaffected, but the log trail is honest.

**Log signal on ledger record (fallback path):**
```
[magic] Agent 1 produced 2660 chars in 15.20s variant=viral_reach model=gpt-5 tokens=in:2500/out:1100/reasoning:320
```
The `model=gpt-5` in the log confirms which underlying model actually served.

### Layer 1 — Empty-output guard

If gpt-5.1 burns its entire max_completion_tokens budget on internal reasoning and emits 0 output tokens (rare, seen at `MAGIC_AGENT1_MAX_TOKENS < 4000`), the pipeline raises:

```
RuntimeError: Agent 1 (gpt-5.1) returned an empty image prompt. Bump MAGIC_AGENT1_MAX_TOKENS (current=8000) or lower MAGIC_AGENT1_REASONING_EFFORT (current=low).
```

This is a config error, not a runtime failure — the retry helper would keep retrying an empty response and never recover. The exception surfaces the config knob to bump.

### Layer 2 — Variant-level isolation

Agent 1 fires 3 parallel variants in a ThreadPoolExecutor. If ONE variant fails (both models exhausted), the other 2 still produce their images. The pipeline logs the failed variant and skips it — the campaign returns 2/3 successful variants rather than dying entirely.

Log signal:
```
[magic] variant 'high_interaction' failed (will skip in output): Error code: 429 - ...
```

### Layer 3 — Magic pipeline → Image Agent v4 (whole-pipeline fallback)

If ALL 3 variants fail (all 6 model×retry combinations exhausted), the entire magic pipeline returns 0 variants → orchestrator falls back to **Image Agent v4** (Gemini-based image generation). Quality is worse but the campaign still ships.

Log signal:
```
[magic] pipeline done — produced 0 variants
Magic pipeline produced no variants — retrying with Image Agent v4 (aspect=1:1)
```

### Summary matrix

| Failure mode | Layer that catches it | Result |
|---|---|---|
| Transient 429 / 5xx / timeout on primary | ✅ 3-retry with backoff | Recovered silently |
| Primary model persistent failure (gpt-5.1 outage) | ✅ Fallback to `gpt-5` with own retry | Recovered — CSV shows fallback model |
| Empty output (reasoning budget exhausted) | ✅ RuntimeError with clear config-tuning message | Surfaces to caller; not silent |
| One variant fails, others succeed | ✅ Variant-level isolation | 2/3 variants returned |
| ALL variants fail | ✅ Pipeline-level fallback to Image Agent v4 | Degraded quality, still ships |
| Both models spend cap hit | Falls through all layers → Image Agent v4 | Campaign uses Gemini renderer instead |
| OpenAI Cloudflare 502 storm | ✅ Retry (transient) | Recovered when Cloudflare recovers |

### Fallback also applied to CAROUSEL_DIRECTOR

The same `gpt-5.1` → `gpt-5` fallback pattern is applied to the carousel director at [`services/carousel_director.py:1291`](../../services/carousel_director.py). Same retry policy, same env-overridable model names (`CAROUSEL_DIRECTOR_MODEL` / `CAROUSEL_DIRECTOR_FALLBACK_MODEL`), same log signals with `[carousel_director]` prefix.

See section 10 below for the full CAROUSEL_DIRECTOR audit.

---

# 10. CAROUSEL_DIRECTOR

## Section 1 — Agent name & purpose

**Agent name:** CAROUSEL_DIRECTOR (a.k.a. Carousel Deck Planner)
**Code location:** [`services/carousel_director.py:run_carousel_director`](../../services/carousel_director.py) (around line 1291) + streaming variant `run_carousel_director_streaming` (around line 1518)
**Runs when:** During carousel posts — the visuals stage of `/generate-content` when `post_type='document'`.
**Frequency:** **Once per carousel** (not per-slide). Unlike Magic Agent 1, this is a single deck-level plan, not a per-variant call.

**Purpose:**

Plan the ENTIRE carousel deck as a single JSON structure — figure out how many slides make sense, what each slide's role is (cover / body / cta), decide the visual theme, and write the individual `image_prompt` for every slide.

Downstream, each slide's `image_prompt` gets sent to gpt-image-2 in parallel to render the actual slide PNGs.

### What the director decides

| Decision | Constraint |
|---|---|
| **Slide count** | Between `MIN_SLIDES` (2) and `MAX_SLIDES` (6). Director picks based on how many distinct ideas POST_TEXT actually has. Never pad. |
| **Per-slide role** | `cover` (first) / `body` (middle) / `cta` (last) |
| **Deck-wide design** | Palette, typography, layout style — consistent across slides |
| **Per-slide image_prompt** | Standalone gpt-image-2 instruction for that slide |
| **Per-slide text_zones** | Headline, caption, CTA text placements |
| **PDF title** | `"From Clicks to Outcomes"`, `"Measure What Moves Revenue"`, etc. |

### Playbook integration

Same industry-playbook system as Magic Agent 1. For `business_category='physical_product'`, a ~26K-character playbook injects with:

- CRITICAL OVERRIDE #1 — Text lockdown (no post-caption copying)
- CRITICAL OVERRIDE #2 — Mode C compositing verb (per on-model slide)
- CRITICAL OVERRIDE #3 — Slide-role override (Mode C = every product slide is on-model)
- CRITICAL OVERRIDE #4 — Mode C face is the brand (repeated on every on-model slide's image_prompt)
- SIX-COMBO MATRIX (Mode × Intent) tuned for deck-wide adaptation
- OFFER DECK ANATOMY (spread across cover / body / CTA slides)

### Output shape (structured JSON)

```json
{
  "pdf_title": "From Clicks to Outcomes",
  "deck_design": {
    "palette": ["#0A2540", "#00A5FF", "#FFF9F0"],
    "typography": "Modern sans-serif",
    "layout_style": "editorial minimal"
  },
  "slides": [
    {
      "slide_no": 1,
      "role": "cover",
      "headline": "Bridal jewellery reimagined",
      "layout": "hero-headline-left",
      "background_spec": "warm ivory gradient",
      "text_zones": { ... },
      "hero_visual": "on-model bridal shot",
      "image_prompt": "<detailed gpt-image-2 instruction>"
    },
    { ...more slides... }
  ]
}
```

## Section 2 — Model name & parameters

### Primary model

| Property | Value |
|---|---|
| **Model name** | `gpt-5.1` (env-overridable via `CAROUSEL_DIRECTOR_MODEL`) |
| **Family** | GPT-5 series (adaptive reasoning) |
| **Input pricing** | $5.00 per 1M tokens |
| **Output pricing** | $30.00 per 1M tokens |
| **Cached input** | $0.50 per 1M tokens |
| **Max completion tokens** | 12,000 (higher than Agent 1's 8,000 — director produces multi-slide JSON with all image_prompts inline, needs bigger budget for reasoning + long output) |
| **Reasoning effort** | `medium` (env: `CAROUSEL_DIRECTOR_REASONING`) — higher than Agent 1's `low` because deck planning IS a multi-step reasoning task (decide count → assign roles → theme → per-slide prompts) |
| **Temperature** | 0.7 (env: `CAROUSEL_DIRECTOR_TEMPERATURE`) — ignored by GPT-5 family (uses default) |
| **Top-p** | 1.0 |
| **Response format** | `{"type": "json_object"}` (strict JSON mode) |
| **Multimodal input** | ❌ No — director works from text prompts only (no logo attached at this stage) |

### Why the higher reasoning_effort vs. Agent 1

Deck planning is genuinely multi-step:
1. Read POST_TEXT + brief + DNA + playbook
2. Count distinct ideas → decide slide count within [2, 6]
3. Assign roles per slide (cover / body / cta)
4. Design deck-wide theme (palette / typography)
5. Write standalone image_prompt for each slide referencing the deck theme

Agent 1's job is simpler (one image, one prompt) — hence `reasoning_effort=low`. Director's `medium` reflects the additional planning burden.

### Fallback model

| Property | Value |
|---|---|
| **Model name** | `gpt-5` (env-overridable via `CAROUSEL_DIRECTOR_FALLBACK_MODEL`) |
| **Family** | GPT-5 original (stable predecessor) |
| **Input pricing** | $5.00 per 1M tokens (identical to primary) |
| **Output pricing** | $30.00 per 1M tokens |
| **Independence rationale** | Different underlying training version — survives 5.1-specific outages |

### Estimated cost per carousel

Director is the SECOND-most expensive call in a carousel campaign (after the 5-6 gpt-image-2 slide renders):

| Scenario | Input tokens | Output tokens (incl. reasoning) | Cost |
|---|---|---|---|
| Typical 5-slide deck | ~5,000-6,000 | ~7,000-10,000 | **~$0.24-0.33** |
| Complex 6-slide with playbook | ~6,000-7,000 | ~10,000-12,000 (heavy reasoning) | **~$0.32-0.40** |

Real observed values from recent runs: **~$0.27** for a 5-slide deck.

Compared to Magic Agent 1 which runs 3× parallel at ~$0.13-0.27 total, the director is roughly equivalent in cost per campaign but has different scaling characteristics (single call, longer wall-clock).

### Wall-clock characteristics

- Non-streaming: **~110-145s** (GPT-5's `medium` reasoning burns a lot of thinking tokens)
- Streaming variant: faster time-to-first-slide (allows renderer to start earlier)

Director is on the critical path — nothing else fires until it finishes.

## Section 3 — Retry mechanism

### ✅ 3-retry with exponential backoff (via `call_with_retry`)

Same retry policy as Magic Agent 1. Wrapped around the OpenAI `chat.completions.create` call at [`services/carousel_director.py:1291`](../../services/carousel_director.py):

| Attempt | Delay | Behavior |
|---|---|---|
| 1 | 0s | Initial call |
| 2 | 5s | Retry after transient error |
| 3 | 10s | Retry after transient error |
| 4 | 20s | Last attempt on primary → then switch to fallback |

### OpenAI-specific retry classification

Same as Magic Agent 1:
- **Transient** (429, 5xx, timeout) → retry
- **Spend cap** (`insufficient_quota`) → fail fast
- **Auth / bad input** → fail fast

The 502 Bad Gateway storms from Cloudflare (OpenAI infrastructure) are correctly identified as transient — retries recover them.

### Log signals

**Success:**
```
[TRACE] carousel director BEGIN
[carousel_director] produced 13969 chars in 115.12s model=gpt-5.1 range=[2..6] tokens=in:5088/out:6989/reasoning:3904
[carousel_director] director chose 5 slides (roles: ['cover', 'body', 'body', 'body', 'cta'])
```

**Transient retry:**
```
[retry] OpenAI/CarouselDirector/gpt-5.1: TRANSIENT error on attempt 1/4 — sleeping 5.0s
```

**Primary exhausted, fallback engaged:**
```
[carousel_director] primary model 'gpt-5.1' exhausted retries — falling back to 'gpt-5'
[carousel_director] fallback model 'gpt-5' succeeded after primary failed
```

**Both models fail:**
```
[carousel_director] BOTH models failed. primary=... fallback=...
```

### Streaming variant caveat

There's a second entry point `run_carousel_director_streaming()` at [`services/carousel_director.py:1518`](../../services/carousel_director.py) that consumes the response incrementally so slide renders can start firing before the full JSON is emitted. **The streaming variant does NOT currently use retry+fallback** — a mid-stream failure aborts the whole streaming pass.

This is a deliberate tradeoff — restarting a partially-consumed stream would waste already-received bytes. If the streaming path becomes unstable, the fix would be to detect early stream failures and fall back to the non-streaming path (which does have retry+fallback).

## Section 4 — Fallback

### ✅ Model fallback (gpt-5.1 → gpt-5)

Same pattern as Magic Agent 1. If `gpt-5.1` exhausts all 4 retry attempts, the api_kwargs are cloned with the model swapped to `gpt-5` and fired with a fresh 3-retry policy. Worst-case: **8 attempts** across two models before final failure.

**Ledger records the actual model that served.** So when fallback fires, the CSV's `ART_DIRECTOR_MODEL` column shows `gpt-5` (director shares the `ART_DIRECTOR` slot with Magic Agent 1 since both are art-director roles). Cost math unaffected because both are priced identically.

### Layer 1 — Empty-output guard

If `gpt-5.1` burns its 12,000-token budget on internal reasoning without emitting output:

```
RuntimeError: Carousel Director (gpt-5.1) returned empty output. Bump CAROUSEL_DIRECTOR_MAX_TOKENS (current=12000) or lower CAROUSEL_DIRECTOR_REASONING (current=medium).
```

Surfaces the config knob rather than looping forever on retries. This is more likely for director than Agent 1 because `reasoning_effort=medium` chews more tokens.

### Layer 2 — JSON schema fallback

The director MUST return valid JSON matching the schema. If gpt-5.1 emits malformed JSON:
1. Retry with same model (up to 3 more times via `call_with_retry`)
2. If retries exhausted, fallback to gpt-5
3. If gpt-5 also emits malformed JSON, the JSON parser raises → the visuals stage catches it and the whole carousel generation fails with a clear error

There's no "partial JSON recovery" like the Business DNA regex fallback — a carousel deck needs the full structured plan or nothing.

### Layer 3 — Slide-level isolation (downstream)

Even after director succeeds, individual slide renders can fail. The pipeline handles that at the IMAGE_GENERATOR layer (per-slide gpt-image-2 hedging + retry) — NOT the director's responsibility.

### Layer 4 — No pipeline-level fallback for carousels

Unlike image posts (which fall back from magic pipeline to Image Agent v4 if all variants fail), carousels have **no fallback pipeline**. If the director fails permanently on both models, the entire carousel generation fails with HTTP 500. The frontend shows a "Carousel generation failed" banner + retry button.

This is a design choice — carousels are inherently more structured (multi-slide layout, PDF stitching) and there's no simpler alternative to fall back to. If OpenAI is fully down, carousels can't ship until it recovers.

### Summary matrix

| Failure mode | Layer that catches it | Result |
|---|---|---|
| Transient 429 / 5xx / timeout on primary | ✅ 3-retry with backoff | Recovered silently |
| Primary model persistent failure (gpt-5.1 outage) | ✅ Fallback to `gpt-5` with own retry | Recovered — CSV shows fallback model |
| Empty output (reasoning budget exhausted) | ✅ RuntimeError with clear config-tuning message | Surfaces to caller |
| Malformed JSON | ✅ Retry + fallback model retry | Second model usually produces clean JSON |
| Director succeeded but slide render fails | Handled at IMAGE_GENERATOR layer (per-slide hedging) | Slides render individually |
| Both models fully fail | ❌ Carousel generation fails | HTTP 500 → user sees banner + retry button |
| OpenAI Cloudflare 502 storm | ✅ Retry classifies as transient | Recovered when infra recovers |
| Streaming variant mid-stream failure | ⚠️ No fallback for streaming path | Whole streaming pass aborts (edge case, rarely happens) |

### Cross-agent comparison — Director vs. Magic Agent 1

| Property | Magic Agent 1 (image posts) | Carousel Director |
|---|---|---|
| Call frequency | 3 parallel per campaign | 1 per carousel |
| Model | gpt-5.1 → gpt-5 fallback | gpt-5.1 → gpt-5 fallback (same) |
| Max completion tokens | 8,000 | 12,000 (higher) |
| Reasoning effort | `low` | `medium` (higher) |
| Multimodal input | ✅ Logo attached | ❌ Text only |
| Response format | Plain text prompt | Strict JSON object |
| Typical wall-clock | ~10-25s per variant | ~110-145s single call |
| Playbook injection | Same physical_product playbook | Same (with deck-adapted rules) |
| CSV slot in ledger | ART_DIRECTOR | ART_DIRECTOR (shared) |
| Pipeline-level fallback | ✅ Falls back to Image Agent v4 | ❌ No fallback pipeline |

---

# 11. IMAGE_GENERATOR / Agent 2

## Section 1 — Agent name & purpose

**Agent name:** IMAGE_GENERATOR (a.k.a. Magic Agent 2 / gpt-image-2 renderer / Carousel Slide Renderer)
**Code locations:**
- [`services/magic_image_pipeline.py:_call_agent2_render`](../../services/magic_image_pipeline.py) (around line 1218) — image posts
- [`services/carousel_pipeline.py:_render_slide`](../../services/carousel_pipeline.py) (around line 173) — carousel slides

**Runs when:** During any post that produces images — image posts and carousels.
**Frequency:**
- **Image posts:** 3 parallel calls per campaign (one per variant)
- **Carousels:** 5-6 parallel calls per campaign (one per slide, plus hedge duplicates when slides run slow)

**Purpose:**

Take the `image_prompt` (produced upstream by ART_DIRECTOR or CAROUSEL_DIRECTOR) plus reference images (logo, product refs, model refs, anchor slide) and produce a final rendered PNG.

This is where the CRITICAL OVERRIDE playbook rules actually become pixels — Mode C compositing, model face preservation, offer flyer text rendering, palette adherence all happen here.

### Input references (up to 16 total per call)

| Reference | Purpose | Required? |
|---|---|---|
| Text prompt (`image_prompt`) | Written by upstream art director | ✅ Always |
| Logo image | Rendered top-left in every output | ✅ For branded posts |
| Product reference photo(s) | For Mode B / C — preserve exact product | Optional (up to 4) |
| Model reference photo | For Mode C — preserve model face | Optional (1) |
| Anchor slide PNG | Carousel legacy path — locks style across slides | Optional (carousel only) |

### Two API endpoints

| Endpoint | When | Cost impact |
|---|---|---|
| **`images.edit`** | Any references attached (logo / product / anchor) | Higher — reference images consume image_input tokens |
| **`images.generate`** | No references at all | Cheaper — only text_input tokens |

For our pipeline, `edit` is used almost always (because the brand logo is always attached). `generate` is a rare cheap-path fallback.

## Section 2 — Model name & parameters

### Primary configuration

| Property | Value |
|---|---|
| **Model name** | `gpt-image-2` (env: `MAGIC_AGENT2_MODEL` / `CAROUSEL_IMAGE_MODEL`) |
| **Quality tier** | `high` (env: `MAGIC_AGENT2_QUALITY` / `CAROUSEL_QUALITY`) |
| **Per-image cost** | **$0.211 per 1024×1024 image** |
| **Text input tokens** | $5.00 per 1M |
| **Image input tokens** (reference photos) | $8.00 per 1M |
| **Size** | Derived from `aspect_ratio` param (1:1 → 1024×1024, 16:9 → 1792×1024, etc.) |
| **Background** | `auto` (env: `MAGIC_AGENT2_BACKGROUND`) — could be `transparent` for compositing lane |
| **Response format** | Base64-encoded PNG bytes |
| **Typical latency** | 130-190s per image at quality=high (long-tail can hit 250s+ under load) |

### Fallback configuration

| Property | Value |
|---|---|
| **Model name** | `gpt-image-2` (same model) |
| **Quality tier** | `medium` (env: `MAGIC_AGENT2_FALLBACK_QUALITY` / `CAROUSEL_FALLBACK_QUALITY`) |
| **Per-image cost** | **$0.053 per image (~75% cheaper)** |
| **Same input/reference tokens** | Yes — reference photos still respected |
| **Typical latency** | 30-45s (5-6× faster than high quality) |

### Why quality=medium fallback instead of a different model or provider

| Option considered | Downside |
|---|---|
| gpt-image-1.5 | Older, deprecated tier — quality noticeably worse than 2-medium |
| gpt-image-1-mini | Even older, worse than 1.5 |
| gemini-3.1-flash-image | Different provider — losing OpenAI-side consistency in prompt handling |
| **gpt-image-2 medium** ← chosen | Same model → same prompt handling & compositing behavior; degraded resolution/detail only; much cheaper; much faster; same OpenAI infra so recovers when infra recovers |

### Cost impact by post type

| Post type | Primary path | Fallback path | Savings on fallback |
|---|---|---|---|
| Image post (3 variants) | 3 × $0.211 = **$0.633** | 3 × $0.053 = **$0.159** | **-$0.474 (75%)** |
| Carousel (6 slides) | 6 × $0.211 = **$1.266** | 6 × $0.053 = **$0.318** | **-$0.948 (75%)** |

Fallback path is dramatically cheaper — a stressed OpenAI hour costs users much less when the fallback fires.

## Section 3 — Retry mechanism

### ✅ 3-retry with exponential backoff (via `call_with_retry`)

Wraps BOTH the `images.edit` and `images.generate` API calls with the shared retry policy:

| Attempt | Delay | Behavior |
|---|---|---|
| 1 | 0s | Initial call at primary quality (high) |
| 2 | 5s | Retry after transient error |
| 3 | 10s | Retry after transient error |
| 4 | 20s | Last attempt at primary quality → then switch to fallback quality (medium) with fresh 3-retry |

### OpenAI-specific classification

Same as other OpenAI agents:
- **Transient** (429, 5xx, timeout, **Cloudflare 502 storms**) → retry
- **Spend cap** (`insufficient_quota`) → fail fast — retry can't help when the OpenAI project has hit its monthly billing cap
- **Auth** (401) → fail fast
- **Bad input** (`invalid_value`, e.g. `quality='High'` capitalized) → fail fast

### Additional wall-clock safety: HEDGING (carousel only)

Beyond retry+fallback, the carousel pipeline has a **hedge mechanism** for slow slides:

```
If a slide has been pending > median × 1.25 (or > 180s floor):
    Fire a duplicate request in parallel
    Use whichever finishes first
```

This handles cases where OpenAI's tail latency for gpt-image-2 spikes but individual requests don't error. Hedging is a WALL-CLOCK optimization, not an error recovery — but it complements the retry+fallback system.

Log signals for hedging:
```
[hedge] slide 4 pending 232.7s (threshold=228.1s, median=182.5s). Firing duplicate request.
[hedge] slide 4 HEDGE WIN in 161.6s (original was still pending — saved time, cost $0.211 extra)
```

Hedge duplicates DO also go through the retry+fallback system — each hedge is a fresh `_render_slide` call.

### Log signals

**Success (primary quality):**
```
[TRACE] variant=viral_reach agent2 BEGIN
[magic] Agent 2 rendered 1,177,614 bytes in 168.64s model=gpt-image-2 quality=high size=1024x1024 refs=1
```

**Transient retry at primary quality:**
```
[retry] OpenAI/Agent2/edit/high: TRANSIENT error on attempt 1/4 — sleeping 5.0s
```

**Primary quality exhausted, fallback quality engages:**
```
[magic] Agent 2 primary quality='high' exhausted retries — falling back to quality='medium'. Last error: 502 Bad gateway
[magic] Agent 2 fallback quality='medium' succeeded after primary failed
[magic] Agent 2 rendered 654,321 bytes in 32.14s model=gpt-image-2 quality=medium size=1024x1024 refs=1
```

Note `quality=medium` in the final log — the ledger will record the correct cost tier.

**Both qualities fail (rare — full OpenAI outage):**
```
[magic] Agent 2 BOTH qualities failed. primary=... fallback=...
[magic] variant 'high_interaction' failed (will skip in output): ...
```

## Section 4 — Fallback

### ✅ Quality fallback (high → medium, same model)

If `quality=high` exhausts all 4 retry attempts, the same `_render_slide` / `_call_agent2_render` retries with `quality=medium` and its own 3-retry policy.

**Same model, same prompt, same references — only the quality tier changes.** This gives us:
- Same prompt handling (no adaptation needed)
- Same compositing behavior (Mode C rules still work)
- 75% cost reduction on the fallback path
- 5-6× faster wall-clock on the fallback path

The ledger records the ACTUAL quality tier that served — so cost math is accurate.

### Layer 1 — Variant/slide isolation

If Agent 2 fails permanently for ONE variant/slide (both qualities exhausted after all retries), the pipeline logs the failure and skips it. Other variants/slides continue.

For image posts:
```
[magic] variant 'high_interaction' failed (will skip in output): ...
```
The campaign returns 2/3 or 1/3 variants rather than dying.

For carousels: if a slide fails, the whole PDF stitching fails (need all slides for a coherent deck).

### Layer 2 — Hedge mechanism (carousel wall-clock safety)

Duplicate parallel requests fired when a slide is anomalously slow. Wins go to whichever finishes first. Hedges cost extra ($0.211 per hedge) but rescue campaigns that would otherwise time out on OpenAI's long-tail latency.

### Layer 3 — Pipeline-level fallback (image posts only)

If ALL variants fail (all quality tiers, all retries exhausted, all hedge duplicates too), the magic pipeline returns 0 variants → orchestrator falls back to **Image Agent v4** (Gemini-based renderer).

Log signal:
```
[magic] pipeline done — produced 0 variants
Magic pipeline produced no variants — retrying with Image Agent v4
```

**Note:** with the new quality fallback, this pipeline-level fallback is now much LESS likely to fire — quality=medium alone recovers most failure cases that would previously have degraded to v4.

**Carousels have no pipeline-level fallback** — if the full retry+fallback cascade fails, the carousel generation fails with HTTP 500.

### Cascade summary

```
For each variant/slide:
  1. Try quality=high (attempts 1-4 with 5s/10s/20s backoff)
      └─ Success → return PNG
      └─ Fail ↓

  2. Try quality=medium (attempts 1-4, own retry)
      └─ Success → return PNG at 75% lower cost
      └─ Fail ↓

  3. Variant/slide skipped
      └─ For image posts: other variants still return
      └─ For carousels: whole PDF fails

  4. (Image posts only) If ALL variants failed:
      └─ Whole magic pipeline → Image Agent v4 fallback

Worst-case per variant: 8 API attempts across 2 quality tiers
```

### Summary matrix

| Failure mode | Layer that catches it | Result |
|---|---|---|
| Transient 429 / 5xx / timeout on quality=high | ✅ 3-retry with backoff | Recovered silently at high quality |
| Cloudflare 502 storm from OpenAI infra | ✅ Classified as transient → retry | Recovered when infra recovers |
| Primary quality persistent failure | ✅ Fallback to quality=medium with own retry | Recovered at 75% lower cost, 5× faster |
| Anomalously slow slide (carousel) | ✅ Hedge duplicate fires, wins go to first response | Slightly higher cost, better wall-clock |
| One variant/slide fully fails | ✅ Variant/slide-level isolation | Others continue |
| All variants fail (image post) | ✅ Pipeline fallback to Image Agent v4 | Degraded quality but still ships |
| All slides fail (carousel) | ❌ No pipeline-level fallback | HTTP 500, user sees retry banner |
| Spend cap hit on entire OpenAI account | Fail-fast retry classification → immediate cascade to fallback quality → immediate cascade to v4 | Recovered via Gemini (image posts) or failed (carousels) |
| Bad input (e.g. `quality='High'` cap issue) | Fail-fast retry → propagate | Surfaces bug to fix |

### Cross-agent comparison — All 3 image-related agents

| Property | Magic Agent 1 (Art Director) | Carousel Director | **Agent 2 (Image Generator)** |
|---|---|---|---|
| Provider | OpenAI (gpt-5.1 primary, gpt-5 fallback) | OpenAI (gpt-5.1 → gpt-5) | **OpenAI (gpt-image-2, high → medium quality)** |
| Pricing model | Per-token ($5 in / $30 out per 1M) | Per-token (same) | **Per-image ($0.211 high / $0.053 medium)** |
| Retry | 3-retry with backoff | 3-retry with backoff | **3-retry with backoff (per quality tier)** |
| Fallback mechanism | Different model (gpt-5) | Different model (gpt-5) | **Different quality tier (medium)** |
| Fallback cost delta | Same $ | Same $ | **-75% on fallback path** |
| Hedge mechanism | ❌ No | ❌ No | ✅ **Yes (carousel only)** |
| Pipeline-level fallback | ❌ None | ❌ None | ✅ **Image Agent v4 (image posts only)** |
| Typical wall-clock | 10-25s per variant | 110-145s single call | 130-190s per image (high), 30-45s (medium) |
| Cost slot in CSV | ART_DIRECTOR | ART_DIRECTOR (shared) | **IMAGE_GENERATOR** |
