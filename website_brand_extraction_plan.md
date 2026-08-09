# Website Brand Extraction — Images + Colours for the f1 Email

**Status:** Proposed
**Date:** 2026-08-04
**Depends on:** [`dynamic_email_images_plan.md`](dynamic_email_images_plan.md) (content
parameterization — **already landed**)
**Target template:** [`outreach_f1.tpl.html`](apps/backend/nexus/templates/email/outreach_f1.tpl.html)
+ [`outreach_template_f1.py`](apps/backend/nexus/services/outreach_template_f1.py)

**Scope:** Given a product's `source_url`, extract its **logo**, **hero image**
and **brand colours** from the live website, and feed them into the f1 email —
which is already fully dynamic for content.

---

## 0. Headline finding — most of this already exists

Before designing anything, the existing machinery:

| Capability | Where | Status |
|---|---|---|
| Colour extraction from a website | [`business_dna_service.extract_business_dna()`](apps/backend/services/business_dna_service.py) — screenshot → **Gemini vision** → 4 hex codes | ✅ Built |
| Logo URL resolution | `_resolve_logo_url(ai_candidate, microlink_candidate, site_url)` | ✅ Built |
| Image URL verification | `_verify_image_url()` — HEAD, falls back to ranged GET; rejects non-`image/*` | ✅ Built |
| og:image + logo + screenshot fetch | `fetch_microlink()` — `api.microlink.io/?url=…&screenshot=true` | ✅ Built |
| Domain/host variant matching | `_host_variants`, `_url_with_host_variants`, `_domain_from_url` | ✅ Built |
| Colours → Nexus email | [`brand_dna.fetch_brand_colors()`](apps/backend/nexus/services/brand_dna.py) | ✅ Built |
| One hex → full email palette, contrast-clamped | [`email_palette.derive_palette()`](apps/backend/nexus/services/email_palette.py) | ✅ Built |
| Conditional image slots in the template | `_logo_block` / `_hero_block` / `_editorial_block` | ✅ Built |
| S3 upload → public URL | [`core/s3_utils.upload_fileobj_to_s3()`](apps/backend/core/s3_utils.py) | ✅ Built |

**This plan is therefore mostly integration, not construction.** The genuinely
new work is §4 (re-hosting + template-fit validation), §5.3 (the dark-mode logo
problem), and §6 (a colour path that works when Business DNA is absent).

> ⚠️ **A correction to the earlier plan.** `dynamic_email_images_plan.md` §3.2
> proposed parsing `og:image` out of `playwright_scraper`'s output. That is not
> possible — `ScrapeResult` carries only `url, text, title, meta_description,
> h1s, h2s, char_count, source`. **It never retains raw HTML**, so there are no
> `<meta>` tags to parse. Microlink (already integrated) is the real source of
> `og:image`, and `playwright_scraper.fetch_html_sync()` is the fallback for
> when we need actual markup.

---

## 1. What the .html actually demands

Every constraint below is read directly off the live template and renderer, not
assumed. **These are the acceptance criteria for any extracted asset.**

### 1.1 Slot specifications

| Slot | Declared | @2x target | Ratio | Sits on (light) | Sits on (dark) | Scales? |
|---|---|---|---|---|---|---|
| **Logo** | 44×44 | **88×88** | **1:1** | `#f4ede6` masthead | `#241f1b` | ❌ **Fixed px** |
| **Hero** | 552×235 | **1104×470** | **2.35:1** | `#fbfaf8` panel | `#1f1b18` | ✅ Fluid |
| **Editorial** | 240×210 | **480×420** | **1.14:1** | `#17120f` | `#17120f` | ✅ Fluid |

Three consequences that drive the whole design:

1. **The hero ratio is 2.35:1 — a cinematic letterbox.** A standard `og:image`
   is 1200×630 (**1.91:1**). It is *not* the right shape. Dropping it in
   unmodified either distorts the image or leaves the card the wrong height.
   **Every hero candidate must be centre-cropped to 2.35:1**, never squashed.
2. **The logo slot is fixed at 44×44 and does not scale on mobile.** A wide
   wordmark-style logo (most companies' `og:logo`) will squash badly. Only a
   square-ish mark works.
3. **The logo's presence changes the typography.** `outreach_template_f1.py`
   sets `_LOGO_COST = 58.0`, so rendering a logo shrinks the auto-fitted
   wordmark (552 → 494px available). A marginal-quality logo therefore costs
   headline size — a reason to reject rather than accept a doubtful one.

### 1.2 Colour contract

`derive_palette(brand_hex, accent_hex)` consumes **two** hexes and emits nine
(`c_rule, c_link, c_accent, c_eyebrow, c_deep, c_counter, c_soft, c_cta_bg,
c_cta_border`), each already contrast-clamped against the surface it sits on.

Extraction must therefore deliver **exactly two usable hexes** — no more, no
less. Business DNA produces four (`primary, secondary, accent, background`);
§6.2 defines the mapping.

**Do not add contrast logic here.** `derive_palette` already guarantees
white-on-button ≥4.5:1, soft-on-dark ≥4.5:1 and stat-on-white ≥3:1, verified
against `#ffff00`, `#ffffff`, `#7fffd4` and `#000000` in the existing test.

---

## 2. Gap analysis

| # | Gap | Severity |
|---|---|---|
| G1 | Colours live in `users.business_dna`, populated only if onboarding ran. A product added another way has none. | High |
| G2 | Extracted image URLs point at **third-party origins** (Microlink CDN, the customer's own CDN). Hotlinking in email is fragile — origins expire, block referers, or rate-limit. | High |
| G3 | Nothing crops/normalizes to the template's ratios. | High |
| G4 | `_verify_image_url` proves a URL *is an image*; it proves nothing about **dimensions, ratio, or file size**. | High |
| G5 | A dark-ink transparent-PNG logo becomes invisible on the dark-mode masthead (`#241f1b`). | Medium |
| G6 | No per-product asset cache — re-extraction on every send would be absurd. | High |
| G7 | Microlink is a paid, rate-limited third party with no retry/backoff in `fetch_microlink`. | Medium |

---

## 3. Pipeline

```
product.source_url
      │
      ├── A. FETCH ────────────────────────────────────────────────
      │     fetch_microlink(url)          → {logo, image, screenshot}
      │     extract_business_dna(url)     → {colors:{primary,secondary,accent,background}}
      │     playwright_scraper.fetch_html_sync(url)   ← fallback, raw markup
      │
      ├── B. SELECT ───────────────────────────────────────────────
      │     per-slot candidate ladder (§5)
      │
      ├── C. VALIDATE ─────────────────────────────────────────────
      │     _verify_image_url  +  NEW: decode, dimensions, ratio,
      │     bytes, same-origin, placeholder-hash (§4.1)
      │
      ├── D. NORMALIZE ────────────────────────────────────────────
      │     centre-crop to slot ratio, downscale to @2x,
      │     re-encode (JPEG q82 / PNG for logos)          (§4.2)
      │
      ├── E. RE-HOST ──────────────────────────────────────────────
      │     upload_fileobj_to_s3() → stable public URL     (§4.3)
      │
      └── F. CACHE + SERVE ────────────────────────────────────────
            nexus_brand_assets row → _build_sender_ctx_for_product
                                   → render_email_f1(sender["images"])
```

**Runs once per product**, at onboarding or on demand — never per send, never
per lead.

---

## 4. The genuinely new work

### 4.1 Template-fit validation

`_verify_image_url` is necessary but nowhere near sufficient. Add
`nexus/services/brand_assets.py::validate_for_slot(bytes, slot)`:

| Check | Logo | Hero | Editorial |
|---|---|---|---|
| Decodes (PIL `verify()` + reopen) | ✔ | ✔ | ✔ |
| Min source dimensions | 88×88 | 800×340 | 480×420 |
| Accepted ratio *before* crop | 0.8–1.25 | **1.4–3.2** | 0.9–1.6 |
| Max bytes (post-encode) | 60 KB | 400 KB | 250 KB |
| Served from product domain or its CDN | ✔ | ✔ | n/a |
| Not a known placeholder/spinner hash | ✔ | ✔ | ✔ |
| Not near-uniform (variance floor) | ✔ | ✔ | ✔ |

The **variance floor** catches a failure `_verify_image_url` cannot: a 1×1
tracking pixel, a solid-colour spacer, or a blank CDN placeholder are all
valid `image/*` responses. Reject when stddev across channels is below a small
threshold.

**Ratio gate before crop is deliberate.** A 1.91:1 `og:image` crops to 2.35:1
losing 18% of its height — acceptable. A 1:1 image would lose 40%, which
reliably decapitates people and cuts text. Hence the 1.4 floor.

### 4.2 Normalization

```python
def normalize_for_slot(raw: bytes, slot: str) -> tuple[bytes, str, int, int]:
    """Centre-crop to the slot ratio, downscale to @2x, re-encode.
    Returns (bytes, content_type, width, height)."""
```

- **Crop, never squash.** Target ratios: logo `1.0`, hero `552/235`,
  editorial `240/210`.
- **Bias the hero crop upward** (~40% from the top, not 50%). Marketing
  screenshots and product shots put the subject in the upper half; a true
  centre crop tends to cut headlines.
- Downscale to @2x, then re-encode: **JPEG q82** for photographic slots,
  **PNG** for logos (alpha must survive).
- `EXIF` stripped — reuse the metadata-stripping approach already in
  `image_agent_v4._strip_image_metadata`.

### 4.3 Re-hosting (non-negotiable)

**Never reference an extracted URL directly in an email.** Microlink CDN links
and customer-origin URLs are outside our control: they expire, may block
referer-less requests, and can rate-limit under email-open load. A hero that
404s months after send makes the email look broken forever, and emails are
archived indefinitely.

Re-host every asset:

```
nexus/brand/{workspace_id}/{product_id}/logo_{sha8}.png
nexus/brand/{workspace_id}/{product_id}/hero_{sha8}.jpg
```

Content-addressed `{sha8}` — an unchanged re-extraction reuses the object and
never invalidates a URL already in flight in a sent email.

---

## 5. Per-slot extraction ladders

### 5.1 Logo — optional, square-ish, verified

Per the prior decision the logo is **optional**; the email renders correctly
without one and the wordmark simply centres (test-verified).

1. **User upload** — always wins. `source='uploaded'`, never overwritten.
2. `fetch_microlink(url)["logo"]` — Microlink's own resolution.
3. `extract_business_dna(url)` AI candidate, passed through the existing
   `_resolve_logo_url(ai, microlink, site_url)`.
4. `<link rel="apple-touch-icon">` from `fetch_html_sync` — usually the
   largest clean square available.
5. **Stop. Render no logo.**

Never AI-*generate* a logo. `_verify_image_url` exists precisely because LLMs
guess plausible-but-dead logo URLs; the same instinct would fabricate a mark.

### 5.2 Hero — the primary per-company visual

1. **User upload** — wins.
2. `fetch_microlink(url)["image"]` — the site's own `og:image`, marketing-approved.
3. `fetch_microlink(url)["screenshot"]` — Microlink already requests
   `screenshot=true`, so this costs nothing extra. A homepage screenshot at
   2.35:1 reads as a genuine product shot.
4. `playwright_scraper.fetch_html_sync()` → largest `<img>` in the first
   `<header>`/`<section>` passing §4.1.
5. **Stop. Omit the hero row.**

> Screenshots ranked above scraped `<img>`s deliberately: a screenshot is
> guaranteed on-brand and correctly sized, whereas an arbitrary page image is
> frequently a partner logo, a stock photo, or an icon sprite.

### 5.3 The dark-mode logo problem

The masthead is `#f4ede6` in light mode and **`#241f1b` in dark**. A logo
delivered as a transparent PNG with dark ink — extremely common — is
**invisible in dark mode**. Neither `_verify_image_url` nor a ratio check
catches this; the asset is perfectly valid and simply disappears.

Detect at normalization: composite the logo onto the dark surface and measure
contrast. Three responses, in order:

1. **Opaque logo** → no action; it carries its own background.
2. **Transparent + light-ink** → no action; readable on dark, and the light
   masthead is handled by the light variant.
3. **Transparent + dark-ink** → flatten onto a small rounded white plate
   (2–3px padding) and store *that* as the served asset. Renders correctly on
   both surfaces at the cost of a visible plate in dark mode — which is what
   most brands do in their own dark UIs anyway.

Storing two variants and swapping via `@media (prefers-color-scheme:dark)` is
the "correct" answer but needs a second `<img>` plus a CSS show/hide, and
**Gmail does not support `prefers-color-scheme` reliably** — the fallback would
show both. The plate is the pragmatic choice. Revisit only if a customer
complains.

---

## 6. Colours

### 6.1 Two paths, in priority order

```
1. brand_dna.fetch_brand_colors(db, user_id, source_url)     ← existing, exact-match
2. extract_business_dna(source_url)["colors"]                ← NEW wiring, on-demand
3. {} → derive_palette(None) → neutral slate                 ← existing fallback
```

Path 1 is unchanged and must keep its **exact-domain-match-or-nothing** rule —
it deliberately refuses to borrow the parent account's brand, because
mis-branding is worse than neutral defaults.

Path 2 closes **G1**: a product whose owner has no Business DNA gets colours
extracted directly from its own site. Persist the result so it runs once.

### 6.2 Four extracted colours → two template inputs

`extract_business_dna` returns `{primary, secondary, accent, background}`,
chosen by Gemini vision from a screenshot with the instruction *"primary(color
used for buttons and links)"*. That is exactly the semantic the email's
`c_link` needs, so:

| Extracted | → | `derive_palette` arg | Rationale |
|---|---|---|---|
| `primary` | → | `brand_hex` | Same semantic: buttons and links. |
| `accent` | → | `accent_hex` | Becomes `c_counter`, the second accent. |
| `secondary` | → | *discarded* | It is a **text** colour; feeding it in would fight the template's own type colours. |
| `background` | → | *discarded* | The template owns its surfaces, in both light and dark. |

**Validation before use:**
- Must match `^#[0-9a-fA-F]{6}$` — reuse `brand_dna._is_hex`.
- **Reject near-white and near-black primaries.** Vision models frequently
  return `#ffffff` or `#000000` from a minimal site. `derive_palette` *will*
  clamp them into legibility, but the result is a grey email with no brand
  signal — worse than the honest neutral fallback.
- Reject when `primary` and `accent` are near-identical (ΔE too small); pass
  `accent_hex=None` and let `derive_palette` synthesize its counter-accent.

### 6.3 Do not re-solve contrast

`derive_palette` already handles it, with tests covering hostile inputs.
Extraction's only job is to hand over two plausible brand hexes.

---

## 7. Storage

Reuse the `nexus_brand_assets` table from the prior plan, with extraction
provenance:

```sql
asset_kind    VARCHAR(32)   -- 'logo' | 'hero'
source        VARCHAR(24)   -- 'uploaded' | 'microlink' | 'og_image'
                            -- | 'screenshot' | 'scraped_img'
source_detail JSONB         -- {origin_url, ladder_step, crop_box,
                            --  original_dims, dark_ink_plated: bool}
status        VARCHAR(16)   -- pending | ready | failed | rejected
last_error    TEXT          -- WHY a candidate was rejected
UNIQUE (product_id, asset_kind)
```

Colours persist alongside, so path 2 runs once:

```sql
ALTER TABLE nexus_products
  ADD COLUMN brand_colors JSONB;   -- {primary, accent, source, extracted_at}
```

`brand_colors` is a **new dedicated column, not `icp`** — `icp` is already
read-modify-written from several paths and concurrent writes would clobber
campaign state.

**Rejections must be stored, not just logged.** `last_error` is what lets
support answer "why is there no logo for this customer?" without re-running
the pipeline.

---

## 8. Integration

### 8.1 New module

```python
# nexus/services/brand_assets.py — mirrors brand_dna.py's contract:
#   never raises, returns {} when unsure.

def resolve_product_assets(db, product) -> dict:
    """{'logo_url','hero_url','editorial_url'} — cached read. Send path."""

def extract_assets_for_product(db, product, force=False) -> dict:
    """Full A→F pipeline. Idempotent. Never overwrites source='uploaded'."""

def resolve_brand_colors(db, product) -> dict:
    """{'brand_color','accent_color'} via §6.1's two paths."""

def store_uploaded_asset(db, product, kind, file_obj) -> dict:
    """Manual override — highest precedence."""
```

### 8.2 Wiring — one place

[`sequencer._build_sender_ctx_for_product`](apps/backend/nexus/services/sequencer.py)
already has the brand-colour `try/except`. Extend it:

```python
try:
    from nexus.services.brand_assets import (
        resolve_brand_colors, resolve_product_assets,
    )
    colors = resolve_brand_colors(db, prod) or {}
    ctx["brand_color"] = colors.get("brand_color") or ctx.get("brand_color")
    ctx["accent_color"] = colors.get("accent_color") or ctx.get("accent_color")
    ctx["images"] = resolve_product_assets(db, prod) or {}
except Exception:
    log.exception("nexus.sequencer: brand asset resolve failed (non-fatal)")
    ctx.setdefault("images", {})
```

`render_email_f1` already reads `sender["images"]` and `sender["brand_color"]`
and handles every absent combination — **no renderer change is needed.**

### 8.3 Where extraction is triggered

- **Product onboarding** — background task after `source_url` is set.
- **Manual "Refresh brand" action** in product settings.
- **Never lazily inside a send.** A cold-start Microlink call plus a Gemini
  vision call inside the send loop would stall the tick.

---

## 9. Failure ladder

Nothing here can block a send.

```
logo:    upload → microlink → DNA/AI → apple-touch-icon → no logo
                                                   └─ wordmark reclaims 58px
hero:    upload → og:image → screenshot → page <img> → omit hero row
colours: business_dna (exact match) → extract_business_dna → neutral slate
```

Already proven by the existing suite (186 checks): every image-present/absent
combination renders with zero empty `src`, zero placeholder leakage, and the
layout closes up cleanly. **Extraction inherits that safety for free** — its
only obligation is to return `{}` rather than a bad asset.

---

## 10. Phases

### Phase A — Colours *(smallest, highest value)*
- `resolve_brand_colors()` with §6.1's two paths and §6.2's mapping/validation.
- `nexus_products.brand_colors` column + persistence.
- Wire into `_build_sender_ctx_for_product`.
- *Exit:* a product with no Business DNA still emails in its own brand colours.

### Phase B — Validation + normalization *(no extraction yet)*
- `validate_for_slot()` and `normalize_for_slot()` against fixture images.
- Dark-ink logo detection and plating (§5.3).
- *Exit:* given bytes, we can prove they fit a slot and emit a correct crop.

### Phase C — Extraction + re-hosting
- `nexus_brand_assets` migration; `extract_assets_for_product()` ladders.
- S3 re-host; onboarding trigger; backfill script.
- *Exit:* each product shows its own logo and hero, re-hosted and correctly cropped.

### Phase D — Controls
- Manual upload override + "Refresh brand" action.
- Rejection reasons surfaced in product settings.
- *Exit:* a customer can fix a bad extraction without engineering.

---

## 11. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Wrong company's logo/hero extracted** | **High** | Same-origin check; exact domain match; `_verify_image_url`; reject over guess. A wrong logo is worse than none. |
| Hotlinked asset dies after send | High | Re-host everything (§4.3); never reference origin URLs. |
| Hero crop decapitates the subject | Medium | 1.4 ratio floor before crop; upward-biased crop; manual override. |
| Dark-ink logo invisible in dark mode | Medium | Detection + plating (§5.3). |
| Vision returns `#ffffff`/`#000000` | Medium | Reject near-white/near-black primaries; fall to neutral (§6.2). |
| Microlink quota / outage | Medium | Extraction is off the send path; cache aggressively; degrade to no-image. Add backoff — `fetch_microlink` currently has none. |
| Gemini vision cost per product | Low | One call per product, cached. Meter via `cost_ledger`. |
| Extraction stalls onboarding | Low | Background task; never synchronous. |

---

## 12. Open questions

1. **Does the S3 bucket serve public objects today?** `get_s3_url()` builds a
   public-style URL but the bucket policy is unconfirmed. Blocks Phase C.
2. **Microlink plan limits** — what is the current quota, and is it shared with
   onboarding? Determines whether backfill needs throttling.
3. **Should `extract_business_dna` be refactored, or called as-is?** It lives in
   `services/` (Pipelyt) while consumers are in `nexus/services/`. Calling
   across is consistent with how `brand_dna` already reads `users.business_dna`,
   but it does couple Nexus to the Pipelyt service layer.
4. **Screenshot as hero — acceptable?** A homepage screenshot is authentic and
   correctly on-brand, but reads as a screenshot rather than an editorial
   image. Product call on whether that suits cold outreach.
5. **Refresh cadence** — do assets ever re-extract, or only on manual refresh?
   Sites rebrand; a stale logo is a slow-burn embarrassment.
6. **Editorial photo** — still the single global constant per the prior
   decision. Confirm it stays out of extraction.
