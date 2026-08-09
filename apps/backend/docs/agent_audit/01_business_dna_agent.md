# Agent Audit Report — Business DNA Agent

**Report #1 of the multi-agent audit series**
**Generated:** 2026-07-08
**Auditor:** Claude (post-instrumentation review)

---

## 1. Purpose

Extracts a comprehensive **Business Knowledge Base** from a URL the user provides
when they set up a new brand. This is the FOUNDATION every downstream agent
uses — REFINER reads it, RESEARCHER references it, COPYWRITER writes in its
voice, art directors visualize its aesthetic.

Produces:

- Brand color palette (primary, secondary, accent, background hex codes)
- Company / product name + tagline
- Logo URL (verified reachable)
- Detailed overview (~10k chars of structured knowledge)
- **Category classification** (`saas_product` / `software_service` /
  `physical_product` / `hardware_service`) — drives industry playbook selection
  downstream

---

## 2. When it fires

**Once per brand connect.** Not per campaign.

- Endpoint: `POST /profile/dna-extract` (see [routers/profile.py:258](../routers/profile.py))
- Trigger: user pastes a URL into the "Connect brand" flow
- Runs via `run_in_threadpool()` so the FastAPI event loop stays free during
  the ~10-30s extraction

**Not run again** unless the user re-clicks "Re-extract DNA" or connects a
second brand.

---

## 3. Model used

| Layer | Model | Purpose |
|---|---|---|
| Structured extraction | **`gemini-flash-lite-latest`** | Reads compiled markdown + screenshot, produces DNA JSON |

`gemini-flash-lite-latest` is a Google alias that currently resolves to
**Gemini 3.1 Flash-Lite** (per Google docs 2026-07). Pricing:
- Input: **$0.25 / 1M tokens**
- Output: **$1.50 / 1M tokens**

Config: `temperature=0.4`, `response_mime_type="application/json"`.

No image-generation model in this flow — the screenshot is fed IN to Gemini
as a `Part.from_bytes` (mime=`image/png`).

---

## 4. Data sources (before Gemini sees anything)

Three parallel fetches — all via `concurrent.futures.ThreadPoolExecutor`:

| Source | Purpose | Timeout |
|---|---|---|
| **Jina AI (`r.jina.ai/`)** — homepage | Markdown-rendered SPA content | ~10s |
| **Microlink** | Metadata: title, description, logo URL, screenshot URL | ~10s |
| **Jina Fast (`r.jina.ai/g/`)** — sub-pages | Markdown of every discovered nav link | ~10s each, parallel |

The compiled markdown from all pages is truncated to **80,000 chars** before
Gemini sees it (performance guardrail).

**Empty-URL guard** at both the router boundary AND service boundary — an
empty URL routes to `https://r.jina.ai/` which returns Jina's own homepage as
"content", polluting the user's brand DNA. Explicit `raise ValueError` before
any fetch.

---

## 5. Retry mechanism ✅

**Shipped 2026-07-08.** Two-layer resilience: 3-retry on the primary model,
then automatic fallback to a secondary model with its own retry.

```
1. TRY: gemini-flash-lite-latest (primary)
    └─ 3 retries with 5s / 10s / 20s exponential backoff on transient errors
    └─ Fail fast on spend cap / auth / bad input

2. PRIMARY EXHAUSTED → TRY: gemini-2.5-flash-lite (fallback)
    └─ Independent stable version — survives -latest alias hot-swaps
    └─ Same 3-retry policy on the fallback model

3. BOTH FAIL → re-raise → outer except returns error-shaped dict
```

**Fallback model:** `gemini-2.5-flash-lite` (older stable tier, $0.10/$0.40 per
1M — 60% cheaper than the primary). Priced independently in the cost ledger.

Log signals:
- Success: no retry logs
- Primary transient recover: `[retry] Gemini/BUSINESS_DNA/gemini-flash-lite-latest: TRANSIENT error attempt N/4 — sleeping ...`
- Primary → fallback: `[DNA] primary model 'gemini-flash-lite-latest' exhausted retries — falling back to 'gemini-2.5-flash-lite'`
- Fallback wins: `[DNA] fallback model 'gemini-2.5-flash-lite' succeeded after primary failed`
- Both fail: `[DNA] BOTH models failed. primary=... fallback=...`

**Estimated failure rate: <5%** (down from ~30% before this fix).

---

## 6. Fallback layers (post-Gemini)

Multi-layer defence-in-depth. Even when Gemini fails, the user still gets a
partially-populated DNA record so they can hand-edit it in Profile:

### Layer 1 — JSON parse fallback

Gemini's `response_mime_type="application/json"` should return strict JSON, but
occasionally emits literal newlines inside long `overview` strings that break
`json.loads`. Two-stage recovery in `_parse_dna_json`:

1. Try strict `json.loads`
2. On failure, use **regex extraction** to recover as many top-level fields as
   possible (`company_name`, `tagline`, `logo_url`, `category`, etc.)

```
logger.info(f"Regex fallback recovered fields: {list(partial.keys())}")
```

### Layer 2 — Field-level fallbacks

| Field | Fallback logic |
|---|---|
| `product_name` (when `is_product=True`) | Domain part of URL (`.split('.')[0].capitalize()`) |
| `logo_url` | Resolution cascade — see Layer 3 |
| `tagline` | `f"{company_name}: Elevating your digital presence."` |
| `category` | Whitelisted against 4 valid slugs; empty string if unrecognised |

### Layer 3 — Logo URL cascade

Because LLMs happily hallucinate `${domain}/logo.png` URLs that 404, the logo
goes through a 3-stage verification cascade in `_resolve_logo_url`:

1. **AI candidate** — HEAD-check the logo URL Gemini returned
2. **Microlink candidate** — HEAD-check what Microlink scraped
3. **Google Favicon fallback** — `https://www.google.com/s2/favicons?domain={host}&sz=256`
   (near-universally available)

Also tries multiple host variants (`www.brand.com`, `brand.com`) to survive
CDN redirects.

### Layer 4 — Full extraction failure

If any exception escapes the try block, returns a minimal error dict:

```python
return {
    "error": str(e),
    "company_name": url,       # so the UI can display *something*
    "overview": f"Failed to extract for {url}: {e}"
}
```

The user's brand DNA never ends up in a fully-blank state — always at least
the URL and an error message they can retry from.

---

## 7. Cost tracking

### ❌ NOT INSTRUMENTED in the current cost_ledger

The Business DNA agent runs OUTSIDE the `/generate-content` request lifecycle,
so it doesn't participate in the per-request `CostLedger`. Its Gemini spend is
INVISIBLE to `agent_time_cost_ledger.csv`.

**Estimated cost per extract:**
- Input tokens: ~40,000-80,000 (compiled markdown + screenshot ~1600 image tokens)
- Output tokens: ~5,000-10,000 (structured DNA JSON with detailed overview)
- Per call: **~$0.03-0.05**

For a workspace with 10 brands connected: **~$0.30-0.50 total DNA spend**.
Not a hot cost driver — but currently untracked.

### ⚠️ Recommendation

Add optional standalone logging to a separate CSV (e.g. `brand_setup_costs.csv`)
or extend `cost_ledger.py` with a fire-and-forget mode for non-request-scoped
calls. Low priority given the small $ impact.

---

## 8. Failure modes seen in the wild

| Failure | Root cause | Current handling |
|---|---|---|
| Empty overview | Gemini returned no parseable JSON | Regex fallback recovers partial fields |
| Wrong category | Website ambiguous (SaaS + services mix) | Whitelist coerces to empty; user picks manually in Profile |
| Missing tagline | AI returned empty or `"..."` | Auto-generated placeholder |
| Missing logo | AI returned 404 URL / Microlink failed | Google Favicon fallback (always works) |
| Full extraction fail | Jina rate limit + timeout | Error dict returned, user sees banner + can retry |
| **Polluted DNA (Jina homepage)** | Empty URL routed to `r.jina.ai/` | Fixed — guards at router + service boundary |

---

## 9. Instrumentation gaps

| Gap | Impact | Fix effort |
|---|---|---|
| No 429/timeout retry on Gemini call | Manual retry required from UI | Low — wrap with `call_with_retry` |
| No cost recording | DNA spend invisible in ledgers | Medium — need non-request-scoped ledger |
| No detailed timing per stage | Can't distinguish Jina slowness vs. Gemini slowness | Low — add `[TIMING]` breadcrumbs |
| No per-brand audit trail | Can't see who re-extracted DNA and when | Medium — new DB column or event log |

---

## 10. Summary matrix

| Property | Value |
|---|---|
| **Agent name** | Business DNA Extractor |
| **Purpose** | Build knowledge base + brand palette + logo + category from URL |
| **Runs when** | Brand connect / re-extract |
| **Model** | `gemini-flash-lite-latest` → Gemini 3.1 Flash-Lite ($0.25 in / $1.50 out per 1M) |
| **External data sources** | Jina AI (homepage + sub-pages) + Microlink (metadata + screenshot) |
| **Retry** | ✅ 3-retry with 5s/10s/20s backoff on primary (shipped 2026-07-08) |
| **Fallback model** | ✅ `gemini-2.5-flash-lite` with its own 3-retry (shipped 2026-07-08) |
| **Fallback (parse)** | Regex extraction of top-level fields |
| **Fallback (fields)** | Domain-name product name, favicon logo, generic tagline, empty category |
| **Fallback (full-fail)** | Error-shaped dict with URL + error message |
| **Cost tracking** | ❌ Not in per-request ledger (~$0.03-0.05 per extract, invisible) |
| **Wall-clock** | ~10-30s typical (mostly waiting on Jina + Microlink) |
| **Est. cost per brand** | ~$0.04 |

---

## Next report: REFINER agent

The REFINER is the first campaign-time Gemini call. It refines the user's raw
brief using the DNA overview + product context. Different retry / fallback
profile because it's inside the `/generate-content` request lifecycle.
