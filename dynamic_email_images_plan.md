# Dynamic Per-Company Outreach Email — Implementation Plan

**Status:** Proposed (revised 2026-08-04 after design review)
**Base template:** [`apps/backend/nexus/templates/email/email_outreach_template_f1.html`](apps/backend/nexus/templates/email/email_outreach_template_f1.html)
**Scope:** Make both the **content** and the **images** of the outreach email
vary per company, the same way `brand_color` already does.

---

## 0. Decisions taken

| Element | Decision | Consequence |
|---------|----------|-------------|
| **Masthead logo** | **Optional, user-provided only.** Render if supplied, otherwise leave empty. | No logo scraping. No fabrication risk. |
| **Hero banner** | **Extracted from the company/product website.** | Per-product, scraped. No AI generation. |
| **Editorial panel photo** | **Constant for all senders** *(for now)*. | One shared asset. Zero per-company work. |
| **SVG icons (×7)** | **Removed entirely.** | Deleted from the template, not rehosted. |
| **Hosting** | **S3, referenced by `https://` URL.** ✅ Approved | Replaces all `data:` URIs. |

> **⚠️ Reversal worth confirming.** The earlier decision was a *layered* model
> where the hero varied by the **recipient's** industry. "Extracted from
> website" makes the hero a property of the **sender's product**, so every
> prospect in a campaign now sees the *same* hero. Recipient-level image
> personalization is therefore **dropped from v1** and parked in §9. Content
> remains personalized per recipient. If per-recipient imagery was still
> intended, say so — it changes §3 and §4 materially.

**Net effect of these decisions:** v1 needs **no image generation, no Gemini
image spend, no approval queue, and no segment vocabulary.** The plan shrinks
from five phases to three, and the `nexus_brand_assets` table drops from
~25k rows to roughly *one row per product*.

---

## 1. Why the template cannot ship as-is

The base template has **10 `<img>` tags**, all `data:` URIs. Gmail strips
`data:` URIs — it does not render them. Since cold outreach is majority-Gmail,
shipping unchanged means ~10 broken images for most recipients.

Per §0, seven of those (the SVG icons) are now **deleted**, leaving three
raster slots. All three move to hosted S3 URLs.

Three problems must be solved in order:

| # | Problem | Phase |
|---|---------|-------|
| 1 | `data:` URIs don't render; icons unwanted | **Phase 1** — strip icons, host the 3 rasters on S3 |
| 2 | Every string is hardcoded Spenzo | **Phase 2** — parameterize content |
| 3 | Hero is the same for every sender | **Phase 3** — scrape hero per product |

Doing 3 before 1 and 2 produces per-company images nobody can see.

---

## 2. The existing pattern we are copying

`brand_color` is already dynamic per company. Images and content ride the same
rails:

```
users.business_dna (JSONB)
   └─> brand_dna.fetch_brand_colors(db, user_id, source_url)   ← domain-matched
         └─> sequencer._build_sender_ctx_for_product()          ← sequencer.py:1002
               └─> sender_ctx["brand_color"]
                     └─> outreach_template.render_email()       ← outreach_template.py:1114
                           └─> accent color in the HTML
```

Two properties to preserve verbatim:

- **Exact-match or nothing.** `fetch_brand_colors` returns `{}` rather than
  borrowing the parent account's brand — mis-branding is worse than neutral
  defaults ([`brand_dna.py:139`](apps/backend/nexus/services/brand_dna.py)).
  The hero resolver must be equally strict: a hero scraped from the *wrong*
  domain is worse than no hero.
- **Resolved at qualification time, not send time.** `prime_drafts_for_lead`
  ([`sequencer.py:1013`](apps/backend/nexus/services/sequencer.py)) generates
  drafts at enrollment so the send loop stays fast. Asset resolution belongs in
  the same place.

---

## 3. Image architecture

| Slot | Line | Size | Keyed by | Cardinality | Source |
|------|------|------|----------|-------------|--------|
| **Logo** | 46 | 44×44 fixed | `product_id` | ≤1 per product | **User upload only** |
| **Hero** | 63 | 552×235 fluid | `product_id` | 1 per product | **Scraped from website** |
| **Editorial** | 198 | 240×210 fluid | — | **1 globally** | Static asset in S3 |

### 3.1 Logo — optional, user-supplied

No scrape, no generation. Rendered only when the workspace has uploaded one.

- Storage: `nexus_brand_assets` row with `source='uploaded'`.
- Upload surface: the existing brand/Connectors settings page (new field).
- Validation on upload: PNG/JPG/SVG, ≤200 KB, decodes via PIL `verify()`,
  min 64×64.
- **Slot is fixed at 44×44 and does not scale on mobile.** A wide wordmark will
  squash. Enforce an aspect ratio between 1:1.5 and 1.5:1 at upload time and
  reject with a clear message, or letterbox onto a square canvas server-side.
- Absent → the `<td>` is omitted entirely and the text wordmark (`company_name`)
  centres on its own. **No placeholder box, no broken-image icon.**

### 3.2 Hero — scraped from the product website

The source is the product's own `source_url`, which onboarding already crawls.
Resolution ladder, first valid wins:

1. `<meta property="og:image">` — the site's own chosen share image, usually
   wide and marketing-approved. Best default.
2. `<meta name="twitter:image">`
3. Largest `<img>` in the page hero/banner region (first `<section>`/`<header>`)
   above a size floor.
4. A first-party screenshot of the landing page via
   [`playwright_scraper.py`](apps/backend/nexus/services/playwright_scraper.py)
   *(already a Playwright dependency — no new infrastructure)*.
5. **Give up → omit the hero row entirely.**

Fetch through `playwright_scraper`'s existing URL-keyed cache
(`get_cached_bundle` / `cache_bundle`) so this piggybacks on the onboarding
crawl rather than adding a fetch.

**Validation gate — reject rather than ship something wrong:**
- min 600 px wide (it renders at 552 px; anything smaller looks soft)
- aspect ratio between 1.6:1 and 3.2:1 — **the slot is 2.35:1**, so tall images
  must be rejected or centre-cropped, never squashed
- ≤400 KB after re-encode; downscale to 1104 px wide (2× retina) and re-encode
  as JPEG q82
- decodes via PIL `verify()`; correct magic bytes
- **served from the product's own domain or its CDN** — never a third-party
  image the page happened to embed
- not a known placeholder/spinner hash

Rejected → `status='rejected'`, reason in `last_error`, hero row omitted.
Provide a **manual override upload** — for sites with no usable `og:image` this
is the only reliable path, and it is cheaper than any clever fallback.

### 3.3 Editorial photo — one global constant

A single vetted image uploaded once to `nexus/static/editorial_default.jpg`.
Not per-company, not in `nexus_brand_assets`, no resolver.

Two consequences to accept knowingly:
- The image is generic stock-style content shown under *every* customer's
  brand. If two customers in the same market both use it, it is visibly shared.
- The dark editorial panel's copy ("WHY IT MATTERS…") is still Spenzo-specific
  and **does** need parameterizing in Phase 2 — only the *photo* is constant.

Marked "for now" — revisit once real send data exists.

### 3.4 Icons — deleted

All seven `data:image/svg+xml` icons are removed, along with their rounded
container chips (the 34×34 and 42×42 wrapper `<table>`s at lines 71–73, 82–84,
93–95, 118–120, 135–137, 154–157, 172–174). Removing the `<img>` but leaving
the chip yields an empty coloured box, which looks broken.

The stat cards and capability cards then lead with their number/title — which
is stronger on a blocked-images first view anyway, since nothing in those cards
depends on an image at all.

---

## 4. Storage

### 4.1 `nexus_brand_assets`

Do **not** stuff these into `nexus_products.icp` JSONB — that blob is already
overloaded (brand overrides, `drafts_ready` flags, campaign state) and is
read-modify-written from several paths; concurrent asset writes would clobber
campaign state.

```sql
CREATE TABLE nexus_brand_assets (
    id            BIGSERIAL PRIMARY KEY,
    workspace_id  BIGINT NOT NULL REFERENCES nexus_workspaces(id) ON DELETE CASCADE,
    product_id    BIGINT NOT NULL REFERENCES nexus_products(id)   ON DELETE CASCADE,

    asset_kind    VARCHAR(32)  NOT NULL,   -- 'logo' | 'hero'
    s3_key        TEXT,
    url           TEXT,                     -- public S3 URL (get_s3_url)
    width         INTEGER,
    height        INTEGER,
    bytes         INTEGER,
    content_type  VARCHAR(64),

    source        VARCHAR(24)  NOT NULL,   -- 'uploaded' | 'scraped'
    source_detail JSONB,                   -- origin URL, ladder step that won
    status        VARCHAR(16)  NOT NULL DEFAULT 'pending',
                                           -- pending|ready|failed|rejected
    last_error    TEXT,

    created_at    TIMESTAMP NOT NULL DEFAULT now(),
    refreshed_at  TIMESTAMP,

    UNIQUE (product_id, asset_kind)
);
```

No `segment_key` — cardinality is now one row per product per kind. The
`UNIQUE` constraint is the concurrency guard: simultaneous enrollments race to
`INSERT … ON CONFLICT DO NOTHING`; the loser reads the winner's row.

**Uploaded always beats scraped.** When a user uploads a hero, the row flips to
`source='uploaded'` and re-scrapes must not overwrite it.

### 4.2 Hosting

Reuse [`core/s3_utils.py`](apps/backend/core/s3_utils.py) —
`upload_fileobj_to_s3()` → public URL via `get_s3_url()`, exactly as
`image_agent_v4` does today.

Key namespace:
```
nexus/brand/{workspace_id}/{product_id}/logo_{hash}.png
nexus/brand/{workspace_id}/{product_id}/hero_{hash}.jpg
nexus/static/editorial_default.jpg
```

`{hash}` is content-addressed so a re-scrape that yields identical bytes reuses
the object and never invalidates a cached image in flight.

**Transport: remote `https://` URLs, not CID attachments.** `resend_client.send_email()`
([`resend_client.py:31`](apps/backend/nexus/services/resend_client.py)) has **no
`attachments` parameter** — CID would require extending it, and inline
attachments inflate every message and can hurt deliverability.

Because images are blocked-by-default in many clients, **the email must read
completely without them**: real `alt` text on all three, no text baked into any
image, and no layout that collapses when they fail.

---

## 5. Content architecture — every hardcoded string

This is the larger half of the work. Full inventory of the base template:

| Line | Current value | Becomes | Source |
|------|---------------|---------|--------|
| 9 | `<title>` "Make marketing decisions…" | headline | `gemini` |
| 31 | Preheader "AI-powered marketing mix modeling…" | preheader | `gemini` (new field) |
| 46 | logo `<img>` | conditional | `images.logo_url` |
| 47 | `SPENZO` wordmark | company name | `sender.company_name` |
| 50 | `spenzo.ai` link | company URL | `sender.company_url` |
| 51 | `POWERED BY NEUZENAI` | platform attribution | config flag — see §5.3 |
| 54 | `#ff4500` rule | brand | `sender.brand_color` |
| 57 | `Hi Nry,` | greeting | `lead.first_name` |
| 58 | Headline | headline | `gemini` |
| 59 | Intro paragraph | body | `gemini.intro_body` |
| 63 | hero `<img>` | conditional | `images.hero_url` |
| 69–101 | Stat strip `3.2x` / `$10M+` / `92%` | proof points | **see §5.2 — highest risk** |
| 109 | `WHAT SPENZO DOES` | eyebrow | `f"WHAT {company_name} DOES"` |
| 110–111 | "Four things, end to end" + subhead | section copy | `gemini` or static-neutral |
| 114–184 | 4 capability cards (title + body) | benefits | `nexus_products.key_benefits` (JSONB array) |
| 193–195 | Editorial panel copy | section copy | `gemini` or product value prop |
| 198 | editorial `<img>` | constant | global S3 asset |
| 206–208 | CTA heading + subcopy | CTA copy | `gemini` or static-neutral |
| 209, 213 | `spenzo.ai/book`, `BOOK A QUICK CALL` | CTA | `sender.cta_url`, `sender.cta_label` |
| 225–227 | `B. Subba Rami Reddy` / `AI Delivery Head` / `spenzo.ai` | signature | `sender.rep_name`, `rep_title`, `company_url` |
| 235 | Footer `SPENZO` | company name | `sender.company_name` |
| 237 | `© 2026 Spenzo by NeuzenAI · 702 S Denton Tap Rd…` | footer | `company_name` + **workspace postal address** |

### 5.1 The colour palette problem

The template uses **six** related colours — `#ff4500`, `#b8330a`, `#c2410c`,
`#d93a02`, `#b03209` (an orange family) plus `#0f4c5c` (a teal counter-accent)
— and tinted card backgrounds `#fff1ea` / `#ffddd0` / `#e9f2f4` / `#cbdfe4`.

`brand_color` supplies **one** hex. Substituting it everywhere flattens the
design; substituting it in one place leaves five Spenzo oranges behind.

Needs a small palette derivation utility:

```python
def derive_palette(brand_hex: str, accent_hex: str | None) -> dict:
    """-> {primary, primary_dark, primary_deep, tint_bg, tint_border,
           counter, counter_tint, counter_border}"""
```

Implement via HSL manipulation (shift lightness for shades, desaturate + raise
lightness for tints). The counter-accent comes from `accent_color` when
Business DNA provides one, else a desaturated complement.

**Must be contrast-checked** — several of these colours carry small text
(11–13 px) on tinted backgrounds. Assert ≥4.5:1 for body text and clamp
lightness if a brand colour would fail. A pale-yellow brand would otherwise
render unreadable labels.

### 5.2 The stat strip is the highest-risk element

`3.2x AVG. ROAS`, `$10M+ SPEND OPTIMIZED`, `92% MODEL ACCURACY` are **specific
factual performance claims about Spenzo**.

Shipping these under another customer's brand would be publishing fabricated
performance data on their behalf. `outreach_template.py` already carries a
comment about hardcoded Spenzo values leaking into every campaign once
([`outreach_template.py:1075`](apps/backend/nexus/services/outreach_template.py));
this is that bug with legal consequences attached.

Three options, in order of preference:

1. **Drop the strip** unless the workspace has explicitly entered its own
   proof points. Safest, and the email still works — it has a hero, four
   capability cards, an editorial panel and a CTA.
2. **User-entered proof points** — three `{value, label, note}` triples in
   product settings, rendered only when all three exist.
3. ~~Generate from Business DNA~~ — **rejected.** An LLM must never invent
   performance statistics.

**Do not let Gemini write this block.** It is the one place in the template
where a hallucination becomes a factual claim about a real company's results.

### 5.3 Smaller decisions

- **`POWERED BY NEUZENAI`** — white-label question. Default on, per-workspace
  flag to hide. Trivial to implement, needs a product call.
- **Capability cards need exactly four.** `key_benefits` is a free-length JSONB
  array. `<4` → drop to a single full-width card or a 2-up row; `>4` → take the
  first four. Never render an empty card.
- **Footer postal address** is currently Spenzo's Coppell TX address. **CAN-SPAM
  requires a valid physical postal address in every commercial email** — this
  must come from the workspace, and sending another company's address is a
  compliance failure, not a cosmetic one. Treat as a **blocking** field: if a
  workspace has no address on file, block the send rather than fall back.

  ⚠️ **No such field exists today.** Verified: `NexusWorkspace`
  ([`models.py:108`](apps/backend/nexus/models.py)) has no address column, and
  the only address-shaped columns anywhere in the Nexus models are
  `person_city` on the lead and `email_address` on a mailbox. This is therefore
  a **new column + new settings-page field + backfill for existing
  workspaces**, and every current workspace starts non-compliant. It is the
  single largest hidden dependency in Phase 2 — scope it explicitly rather than
  discovering it at rollout.

---

## 6. Integration points

### 6.1 New module `nexus/services/brand_assets.py`

Mirrors `brand_dna.py`'s contract: never raises, returns `{}` when unsure.

```python
def resolve_product_assets(db, product) -> dict:
    """{'logo_url', 'hero_url', 'editorial_url'} for a product.
    Cached; returns {} per-key for anything unavailable."""

def scrape_hero_for_product(db, product) -> dict:
    """Run the §3.2 ladder + validation, upload to S3, upsert the row.
    Idempotent. Never overwrites source='uploaded'."""

def store_uploaded_asset(db, product, kind, file_obj) -> dict:
    """User upload path for logo / hero override."""
```

### 6.2 Template rendering

The template is currently a **static HTML file**, while `render_email` builds
HTML from an f-string in Python
([`outreach_template.py:1119`](apps/backend/nexus/services/outreach_template.py)).
Two options:

| Approach | Verdict |
|----------|---------|
| Port the file into an f-string in `render_email` | ✗ 250 lines of table markup with `{}` in every `style` attribute — f-string brace-escaping would be a maintenance trap. |
| **Load the file, substitute placeholders** | ✓ **Chosen.** Keeps the design editable by whoever produced it. Use `string.Template` (`$var`) — not `str.format`, which collides with CSS braces. |

**Jinja2 is NOT currently a backend dependency** — verified absent from both
`venv/Lib/site-packages` and `requirements*.txt`. So either add it, or use
`string.Template` with **pre-rendered block strings** for the conditional
sections (logo / hero / stat strip / editorial photo), assembling each block in
Python before substitution. Given there are only ~5 conditional regions, the
no-new-dependency route is recommended — it also matches how `render_email`
already builds conditional regions today via inline `if` expressions.

⚠️ **`string.Template` requires escaping every literal `$` in the template.**
The stat strip contains `$10M+`, which must become `$$10M+`. If the stat strip
is dropped per §5.2 this is moot, but a pre-substitution check should assert no
unescaped `$` survives.

Template file is read once and cached at module level — not re-read per send.

Template file is read once and cached at module level — not re-read per send.

### 6.3 Wiring

**`sequencer._build_sender_ctx_for_product`** ([`sequencer.py:952`](apps/backend/nexus/services/sequencer.py))
— extend the existing brand-colour `try/except`:

```python
try:
    from nexus.services.brand_assets import resolve_product_assets
    ctx["images"] = resolve_product_assets(db, prod) or {}
except Exception:
    log.exception("nexus.sequencer: brand asset fetch failed (non-fatal)")
    ctx["images"] = {}
```

**`render_email`** — read `sender_dict.get("images", {})`, emit each `<img>`
only when its URL is present, exactly as the existing `show_real_result` /
`show_cta` conditionals work.

No change needed in `prime_drafts_for_lead`: with the hero now per-product
rather than per-recipient, `_build_sender_ctx_for_product` covers it.

---

## 7. Failure ladder

Every slot degrades independently. The email always sends.

```
logo:      uploaded → omit the <td>, wordmark centres alone
hero:      uploaded → scraped → omit the row
editorial: global constant → omit the photo cell, copy goes full-width
stats:     user proof points → omit the whole strip
benefits:  key_benefits[0:4] → fewer cards → omit the section
colors:    derived palette → neutral slate (existing behaviour)
address:   workspace address → BLOCK THE SEND (compliance, not cosmetic)
```

**Rule: a missing image never blocks a send and never produces a broken-image
icon.** Enforce with a test asserting that across every present/absent
combination, the rendered HTML contains zero `<img>` tags whose `src` is empty,
`None`, or a `*_IMAGE_SRC` placeholder.

---

## 7a. Implementation status (2026-08-04)

**Landed:**

| File | What |
|------|------|
| [`templates/email/outreach_f1.tpl.html`](apps/backend/nexus/templates/email/outreach_f1.tpl.html) | Parameterized template. All 7 SVG icons + their chips removed. Every Spenzo string tokenized. |
| [`services/email_palette.py`](apps/backend/nexus/services/email_palette.py) | `derive_palette()` — one brand hex → the 9 `$c_*` values, each WCAG-clamped against its real surface. |
| [`services/outreach_template_f1.py`](apps/backend/nexus/services/outreach_template_f1.py) | `render_email_f1()` — same return shape as `render_email`. All conditional blocks. |
| [`scripts/test_outreach_f1_render.py`](apps/backend/scripts/test_outreach_f1_render.py) | Render matrix — **all checks passing**. |

Verified by the test run:
- Zero unsubstituted placeholders and zero empty/`data:` `img src` across every
  image-present/absent combination.
- Each of the 3 image slots is independently optional; all-absent renders 0
  `<img>` tags and still reads completely.
- Stat strip appears only at ≥3 proof points (0/1/2 → dropped).
- `key_benefits` of length 0/1/2/3/4/7 → 0/1/2/3/4/4 cards.
- Contrast holds for hostile brand colours (`#ffff00`, `#ffffff`, `#7fffd4`,
  `#000000`, and `None`): white-on-button ≥4.5:1, soft-on-dark ≥4.5:1,
  stat-on-white ≥3:1.
- **Cross-tenant check** — rendering as "Zyntegrate" yields 0 Spenzo strings,
  0 hardcoded-orange hexes, and no Coppell address.

**Not yet done:**
- Real image files + S3 upload (blocked — see §11 Q6/Q7).
- Proof-point **extraction** from the sender's own site (currently the renderer
  consumes `sender["proof_points"]`; nothing populates it yet).
- Hero scrape ladder + `nexus_brand_assets` (Phase 3).
- **Sequencer wiring** — deliberately not switched. `render_email` still serves
  live sends; `render_email_f1` is additive and inert until wired.

---

## 8. Phases

### Phase 1 — Make the template sendable *(prerequisite)*
- **Delete all 7 SVG icons and their container chips** (§3.4).
- Source the editorial photo; upload to `nexus/static/`.
- Recover or replace the hero + logo images — the base file still carries
  `LOGO_IMAGE_SRC` / `HERO_IMAGE_SRC` / `TEAM_IMAGE_SRC` placeholders, because
  the original base64 could not be faithfully reproduced from the paste and was
  **not** approximated.
- Swap every remaining `data:`/placeholder `src` for an `https://` S3 URL.
- **Render test across Gmail web, Gmail iOS/Android, Outlook desktop, Apple
  Mail — images ON and OFF.** This gate decides whether the design survives
  contact with real clients at all.
- *Exit:* one static branded email renders correctly in Gmail.

### Phase 2 — Parameterize content *(the bulk of the work)*
- Template loader + placeholder substitution (§6.2).
- Wire every row of the §5 inventory.
- `derive_palette()` + contrast assertions (§5.1).
- Resolve the stat strip decision (§5.2) and the workspace address (§5.3).
- Extend `generate_template_content` with the new fields (headline, preheader,
  section copy) — **excluding** the stat strip.
- *Exit:* Zyntegrate and Spenzo campaigns render correctly from the same file.

### Phase 3 — Per-product images
- `nexus_brand_assets` migration + `brand_assets.py`.
- Logo upload surface; hero upload override.
- Hero scrape ladder + validation; backfill for existing products.
- *Exit:* each product shows its own hero; failures degrade cleanly.

---

## 9. Deferred

- **Per-recipient hero imagery** (the dropped layered model — see §0). If
  revisited, do it as deterministic PIL compositing over the scraped hero
  reusing [`services/image_templates.py`](apps/backend/services/image_templates.py),
  **not** per-lead generation.
- **Per-company editorial photo** — constant "for now" per §0.
- **AI-generated imagery** — entirely out of scope for v1. The
  [`image_agent_v4.py`](apps/backend/services/image_agent_v4.py) pipeline stays
  available if this is revived.

---

## 10. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| **Stat strip ships another company's numbers** | **Critical** | §5.2 — drop by default; never LLM-generated. |
| **Footer lacks a valid postal address** | **Critical** | CAN-SPAM. Block the send; do not fall back. |
| **Image-heavy email trips spam filters** | **High** | Text:image ratio, real alt text, no image-only content. Measure deliverability before/after — an image-rich email in spam is worse than the current plain one. A/B before full rollout. |
| Scraped hero is off-brand or wrong | High | Same-domain check, size/ratio gates, manual override, reject over guess. |
| Brand colour makes small text unreadable | Medium | Contrast assertions with lightness clamping (§5.1). |
| Logo squashed in the fixed 44×44 slot | Medium | Aspect gate at upload, or letterbox onto a square canvas. |
| S3 objects not publicly readable | Medium | Assert reachability at write time; health check in the render test. |
| Hero image hotlink-blocked by origin CDN | Low | We re-host in S3 rather than hotlinking — mitigated by design. |

---

## 11. Open questions

1. **Was dropping per-recipient hero personalization intended?** (§0) Blocks
   the §3 shape.
2. **Stat strip — drop, or add user-entered proof points?** Blocks Phase 2.
3. **Is `POWERED BY NEUZENAI` white-labelled per workspace?** Blocks Phase 2.
4. ~~Where does the workspace postal address live today?~~ **Answered: nowhere.**
   New column + settings field + backfill required. Blocks Phase 2; compliance
   gate. See §5.3.
5. ~~Is Jinja2 already a backend dependency?~~ **Answered: no.** Use
   `string.Template` + pre-rendered blocks, or add the dependency. See §6.2.
6. **Does the S3 bucket serve public objects today?** `get_s3_url()` builds a
   public-style URL but the bucket policy needs confirming — if objects are
   private, Phase 1 needs CloudFront or non-expiring presigned URLs.
7. **Source images** — the three rasters are still placeholders; where do the
   real logo/hero/editorial files live?
8. **Deliverability appetite** — has this list been warmed on plain text?
   Switching a warmed sender to heavy HTML is itself a reputation event,
   independent of the image work.
