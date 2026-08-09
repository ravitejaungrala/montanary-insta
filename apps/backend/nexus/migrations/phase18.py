"""Phase 18 — persisted per-product brand colours.

Email brand colours currently come from ONE source: `users.business_dna`,
matched by domain in `brand_dna.fetch_brand_colors`. That blob is only written
when Pipelyt's onboarding ran `extract_business_dna` for the account, so a
product added by any other route has no colours at all and every email for it
falls back to neutral slate.

This column gives that product a place to keep colours extracted directly from
its OWN website, so the fallback path runs once rather than on every send.

Shape:
    {
      "primary": "#hex",          # -> derive_palette(brand_hex)
      "accent":  "#hex" | null,   # -> derive_palette(accent_hex)
      "source":  "business_dna" | "website_extraction",
      "extracted_at": "2026-08-04T…Z"
    }

A dedicated column rather than a key inside `nexus_products.icp`: `icp` is
read-modify-written from several paths (campaign brand overrides,
drafts_ready flags), so a concurrent colour write would clobber campaign
state.

Purely additive. See website_brand_extraction_plan.md §7.
"""

from __future__ import annotations


MIGRATIONS = [
    "ALTER TABLE nexus_products ADD COLUMN IF NOT EXISTS brand_colors JSONB",
]
