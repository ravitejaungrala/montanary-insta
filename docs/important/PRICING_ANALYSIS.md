# Pipelyt — AI Cost & Pricing Analysis

_Generated 2026-07-03. Reflects the **actual production `.env` config** (`apps/backend/.env`), not just code defaults._

> **Important correction:** the live `.env` sets `USE_MAGIC_IMAGE_PIPELINE=true` and `USE_CAROUSEL_PIPELINE=true`. That means **image posts and carousels both render on OpenAI `gpt-image-2`, not on a Gemini image model.** The Gemini image agent (v4, `gemini-3.1-flash-image`) is wired up but only runs as a **fallback** when the Magic pipeline fails. See §2 for the routing proof.

---

## 0. Active configuration (from `apps/backend/.env`)

| Env var | Value | Effect |
|---|---|---|
| `USE_MAGIC_IMAGE_PIPELINE` | **`true`** | **Image posts → Magic pipeline (GPT-5 + gpt-image-2).** Overrides Gemini v4. |
| `USE_CAROUSEL_PIPELINE` | **`true`** | Carousel posts → GPT-5 Director + gpt-image-2. |
| `USE_VISUALIST_V2` | `true` | Legacy path (only reached if the above are off). |
| `MAGIC_AGENT2_QUALITY` | **`high`** (prod) | Magic image renders at gpt-image-2 **high** (~$0.211/img). _(Committed `.env` shows `medium`; production runs `high`.)_ |
| `CAROUSEL_QUALITY` | _(unset → `high`)_ | Carousel slides render at gpt-image-2 **high** (~$0.211/img). |
| `USE_IMAGE_AGENT_V4` | _(unset → `true`)_ | Gemini image agent — **fallback only**, overridden by Magic. |

---

## 1. Official API pricing — only the models we actually use

### 1a. Text models

| Model (our alias) | Where used | Input /1M | Output /1M |
|---|---|---|---|
| **Gemini 3.1 Flash-Lite** (`gemini-flash-lite-latest`) | refiner, cultural, researcher, copywriter, critic | $0.25 | $1.50 |
| **GPT-5** (`gpt-5`) | Magic image-prompt agent, Carousel Director | $1.25 | $10.00 |

> `gemini-flash-lite-latest` is a rolling alias that **currently points to `gemini-3.1-flash-lite`** (released May 7, 2026; $0.25 in / $1.50 out per Google's model card).

### 1b. Image models

| Model | Where used | Quality | ≈ per 1024² image |
|---|---|---|---|
| **`gpt-image-2`** | **image posts (Magic)** | **high** (prod) | **$0.211** |
| **`gpt-image-2`** | **carousel slides** | **high** (default) | **$0.211** |
| `gemini-3.1-flash-image` | image posts — **fallback only** | — | $0.067 |
| `gemini-2.5-flash-image` (Nano Banana) | fallback of the fallback | — | $0.039 |

> The Gemini image models are **cheaper** ($0.039–$0.067) but are **not used** in the current config — the `.env` routes every image through `gpt-image-2`. Switching `USE_MAGIC_IMAGE_PIPELINE=false` would move image posts onto Gemini and cut their image cost ~3× (see §5).

`gpt-image-2` per-image (1024²): **low ≈ $0.006 · medium ≈ $0.053 · high ≈ $0.211**.

Google Search **grounding** (researcher + cultural): free for first 1,500 req/day, then $0.035 each.

Sources:
- [Gemini API pricing (official)](https://ai.google.dev/gemini-api/docs/pricing)
- [OpenAI API pricing (official)](https://developers.openai.com/api/docs/pricing)
- [GPT Image 2 pricing breakdown](https://wavespeed.ai/blog/posts/gpt-image-2-pricing-2026/)

---

## 2. Which model each agent runs (with routing proof)

**Routing precedence** in `ai_service.py:3618-3869` (first match wins):
1. `text` / `video` → skip visuals
2. `document` + `USE_CAROUSEL_PIPELINE` → **Carousel pipeline** (gpt-image-2 high)
3. **`elif use_magic:` (`USE_MAGIC_IMAGE_PIPELINE=true`) → Magic pipeline (GPT-5 + gpt-image-2 medium)** ← image posts land here
4. `elif use_v4:` → Gemini image agent — **only reached if Magic is off or fails**

| Agent | Function | Model | Type | File |
|---|---|---|---|---|
| Refiner | `_refine_brief_agent` | Gemini 3.1 Flash-Lite | text | `ai_service.py:577` |
| Cultural calendar | `_get_cultural_calendar` | Gemini 3.1 Flash-Lite + Search | text (grounded, day-cached) | `ai_service.py:716` |
| Researcher | `_research_agent` | Gemini 3.1 Flash-Lite + Search | text (grounded) | `ai_service.py:1316` |
| Copywriter | `_content_agent` | Gemini 3.1 Flash-Lite | text | `ai_service.py:1675` |
| Critic | `_critic_agent` | Gemini 3.1 Flash-Lite | text (image posts only) | `ai_service.py:1962` |
| **Magic prompt (image)** | `_call_agent1_magic_prompt` | **`gpt-5`** | text, **once per variant (×3)** | `magic_image_pipeline.py:51` |
| **Magic render (image)** | `_call_agent2_render` | **`gpt-image-2` high** | image, **×3 variants** | `magic_image_pipeline.py:66-67` |
| **Carousel Director** | `run_carousel_director` | **`gpt-5`** | text/reasoning, picks 2–6 slides | `carousel_director.py:63` |
| **Carousel render** | `_render_slide` | **`gpt-image-2` high** | image, **1 per slide** | `carousel_pipeline.py:63-64` |
| _Gemini art director_ | `_art_director_agent` | _Gemini 3.1 Flash-Lite_ | _text — v4 fallback only_ | `image_agent_v4.py:993` |
| _Gemini image gen_ | `generate_image_variants_v4` | _`gemini-3.1-flash-image`_ | _image — fallback only_ | `image_agent_v4.py:47` |

Magic generates **3 variants** — `viral_reach`, `high_interaction`, `follower_growth` (`magic_image_pipeline.py:95`), +1 optional `festival_variant`.

---

## 3. Cost to produce ONE post

Text-token figures are **estimates** from real prompt sizes (no token logging in code). Image figures are exact list prices. GPT-5 estimates assume low/medium reasoning effort. See §6.

### 3a. IMAGE post — Magic pipeline (GPT-5 + gpt-image-2 high) — deep dive

Pipeline: text agents (Gemini) → **3× GPT-5 magic prompt** → **3× gpt-image-2 high** → critic.

| # | Agent | Model | Calls | Est. cost |
|---|---|---|---|---|
| 1 | Refiner | Gemini 3.1 Flash-Lite | 1 | $0.0018 |
| 2 | Cultural (day-cached) | Gemini 3.1 Flash-Lite | ~0 | ~$0 |
| 3 | Researcher | Gemini 3.1 Flash-Lite | 1 | $0.0028 (+ grounding $0.035\*) |
| 4 | Copywriter | Gemini 3.1 Flash-Lite | 1 | $0.0043 |
| 5 | Critic | Gemini 3.1 Flash-Lite | 1 | $0.0014 |
| 6 | **Magic prompt (GPT-5)** | `gpt-5` | **3** | **≈ $0.060** |
| 7 | **Image render (gpt-image-2 high)** | `gpt-image-2` | **3** | **3 × $0.211 = $0.633** |
| | **TOTAL** | | | **≈ $0.70** (up to $0.74 w/ grounding) |

\*Grounding free under 1,500 req/day; $0.035/post at scale.

**Image-post cost drivers, ranked:**
1. **3× gpt-image-2 high — $0.633** (~90%). This is the whole ballgame.
2. **3× GPT-5 prompt writers — ~$0.060** (~9%). _GPT-5 output at $10/1M._
3. Gemini text agents combined — **~$0.010** (~1%).

**Levers:**
- **Drop image quality to `medium`** (`MAGIC_AGENT2_QUALITY=medium`, $0.053/img) → image line $0.633 → **$0.159**, post ≈ **$0.23**. Biggest single lever.
- **Turn off Magic** (`USE_MAGIC_IMAGE_PIPELINE=false`) → image posts fall to Gemini: 3× `gemini-3.1-flash-image` ($0.201) → post ≈ **$0.21**, or **Nano Banana** (`gemini-2.5-flash-image`, $0.039) → **~$0.13**.
- Drop magic to **2 variants** → saves ~$0.23/post (one GPT-5 + one high image).

### 3b. TEXT-only post

Visuals skipped; critic skipped for text.

| Agent | Model | Cost |
|---|---|---|
| Refiner | Gemini 3.1 Flash-Lite | $0.0018 |
| Cultural (cached) | Gemini 3.1 Flash-Lite | ~$0 |
| Researcher | Gemini 3.1 Flash-Lite | $0.0028 (+ $0.035 grounding) |
| Copywriter | Gemini 3.1 Flash-Lite | $0.0043 |
| **TOTAL** | | **≈ $0.009** (+ grounding if billed) |

Essentially free (~1¢ of tokens).

### 3c. CAROUSEL post — GPT-5 Director + gpt-image-2 **high**

Text (Gemini) → **GPT-5 Director** → **N × gpt-image-2 high**. Critic skipped for documents.

| Agent | Model | Calls | Cost |
|---|---|---|---|
| Text pipeline (refiner+researcher+copywriter) | Gemini 3.1 Flash-Lite | 3 | ~$0.009 (+ grounding $0.035) |
| **Carousel Director** | `gpt-5` | 1 | ≈ $0.05 |
| **Slide render** | `gpt-image-2` **high** | N | N × $0.211 |

| Slides (N) | Image cost | **Carousel total** |
|---|---|---|
| 2 | $0.422 | **≈ $0.48** |
| 3 (typical) | $0.633 | **≈ $0.69** |
| 4 | $0.844 | **≈ $0.90** |
| 6 (max) | $1.266 | **≈ $1.33** |

> Carousels are the most expensive post by far — a 3-slide carousel (~$0.69) costs **~3× an image post** and **~75× a text post**, entirely because slides render at `gpt-image-2` **high**. Setting `CAROUSEL_QUALITY=medium` (~$0.053/img) would cut a 3-slide carousel to **~$0.21**.

### 3d. Per-post cost summary (current `.env`)

| Post type | Image path (actual) | AI cost / post |
|---|---|---|
| **Text** | — | **~$0.009** |
| **Image** | Magic: GPT-5 ×3 + gpt-image-2 **high** ×3 | **~$0.70** (up to $0.74 w/ grounding) |
| **Carousel** (3 slides) | GPT-5 director + gpt-image-2 high ×3 | **~$0.69** |
| **Carousel** (6 slides) | GPT-5 director + gpt-image-2 high ×6 | **~$1.33** |

> With Magic at **high**, an image post (~$0.70) now costs about the **same as a 3-slide carousel** — both are dominated by 3 `gpt-image-2` high renders. _Levers:_ image quality → medium ≈ **$0.23**, or Gemini image path ≈ **$0.13–0.21**.

---

## 4. Our current plans (what we sell)

Prices from `Onboarding.jsx`; quotas from `core/pricing.py`.

| Plan | Monthly | Annual (/mo) | AI posts/mo | AI images/mo | Brands (DNA) | Channels | Team seats | Key features |
|---|---|---|---|---|---|---|---|---|
| **Free** | $0 | $0 | 5 | 15 | 1 | 1 | 0 | Content generation only — no scheduling/Canva/analytics/teams/video |
| **Starter** | **$49** | $39 ($468/yr) | 60 | 180 | 2 | 3 | 0 | "Solopreneurs." Email support. Flags all off |
| **Growth** ⭐ | **$99** | $79 ($948/yr) | 150 | 450 | 5 | 5 | 0 | "Most popular." Scheduling ✓ Canva ✓ Analytics ✓. Priority support |
| **Agency** | **$249** | $199 ($2,388/yr) | 300 | 900 | 10 | Unlimited | 15 (+5 franchise) | Growth **+ Teams ✓ Franchise ✓ Video ✓**. Dedicated CSM |
| **Enterprise** | Custom | Custom | Unlimited | Unlimited | Unlimited | Unlimited | Unlimited | SSO + SOC 2, custom volumes, private model routing, solutions engineer |

Feature flags (`core/pricing.py`): Free & Starter all-off; Growth = scheduling+Canva+analytics on; Agency & Enterprise = all on. Aliases: `basic`→Starter, `pro`→Growth.

---

## 5. Margin check (worst case — user spends whole image quota)

Assumes each quota "image" = one rendered image variant.

Every image (Magic variant **and** carousel slide) now renders at `gpt-image-2` **high** = **$0.211/img**.

| Plan | Price/mo | Image quota | Image COGS @ $0.211/img | Margin |
|---|---|---|---|---|
| Starter | $49 | 180 | $37.98 | **22%** ⚠️ |
| Growth | $99 | 450 | $94.95 | **4%** ⚠️ |
| Agency | $249 | 900 | $189.90 | **24%** ⚠️ |

_(GPT-5 prompt/director tokens add ~$0.02–0.05 per render on top — a further few dollars per full quota, pushing Growth negative in the worst case.)_

**Takeaways:**
- At **high** quality, margins are **thin-to-critical** if users exhaust their image quota — **Growth is essentially break-even (4%)** and can go negative once GPT-5 tokens are added. ⚠️
- The image quota (180/450/900) is priced as if images were cheap; at $0.211 each they aren't.
- **Biggest levers, in order:** (1) drop image render quality to **medium** (~4× cheaper, $0.211 → $0.053); (2) move image posts off Magic onto **`gemini-3.1-flash-image`** ($0.067) or **Nano Banana** ($0.039) — 3–5× cheaper than gpt-image-2 high; (3) reduce variants from 3 → 2; (4) enable Batch API (‑50%) where latency allows.

---

## 6. Methodology & assumptions

- **Config reflects production** — Magic + Carousel pipelines both ON; **both render at `gpt-image-2` high** per the deployed config (the committed `.env` shows magic=`medium`, but production runs `high`).
- **Image/render costs are exact** vendor list prices; `gpt-image-2` high = $0.211, medium = $0.053 per 1024².
- **Text-token costs are estimates** from real prompt sizes (no token logging). Even a 2× error moves a post total <2% (images dominate).
- **GPT-5 estimated at $1.25/$10** with low reasoning for magic prompts (~$0.02/call) and medium reasoning for the carousel director (~$0.05). Reasoning tokens bill as output.
- **`gemini-flash-lite-latest` = Gemini 3.1 Flash-Lite** ($0.25/$1.50), confirmed by Google's model card.
- **Grounding** shown separately: $0 under free tier, else $0.035/grounded request; cultural calendar is day-cached (~$0/post amortized).
- **Image Check** (`gemini-2.5-flash`) is off in production; **Critic** runs for image posts, skipped for text/carousel.
- **Variant counts:** Magic = 3 variants (`STANDARD_VARIANTS`), Carousel = 2–6 slides (director-chosen, typical 3). Gemini v4 (fallback) = 3 variants.

---

_File:line references are against the working tree on branch `dev`, 2026-07-03._
