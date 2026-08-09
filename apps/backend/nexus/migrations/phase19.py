"""Phase 19 — per-product email brand assets (logo + hero) and f1 section copy.

`nexus_brand_assets` holds images extracted from a product's own website,
re-hosted in S3. Re-hosting rather than hotlinking is deliberate: the origin
URLs (Microlink CDN, the customer's CDN) expire, block referer-less requests,
and rate-limit — and a sent email is archived forever, so a hero that 404s
later makes the mail look broken permanently.

UNIQUE (product_id, asset_kind) is the concurrency guard: simultaneous
enrollments race to INSERT ... ON CONFLICT DO NOTHING and the loser reads the
winner's row instead of extracting a duplicate.

`nexus_products.f1_sections` caches the template's section copy (benefits
heading, editorial panel, CTA wording). That copy is a property of the
PRODUCT, not the lead — it does not change between prospects — so generating
it per draft would be waste and would let the same campaign drift in wording
between recipients.

Purely additive. See website_brand_extraction_plan.md.
"""

from __future__ import annotations


MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS nexus_brand_assets (
        id            BIGSERIAL PRIMARY KEY,
        workspace_id  BIGINT,
        product_id    BIGINT NOT NULL,
        asset_kind    VARCHAR(32) NOT NULL,
        s3_key        TEXT,
        url           TEXT,
        width         INTEGER,
        height        INTEGER,
        bytes         INTEGER,
        content_type  VARCHAR(64),
        source        VARCHAR(24),
        source_detail JSONB,
        status        VARCHAR(16) NOT NULL DEFAULT 'pending',
        last_error    TEXT,
        created_at    TIMESTAMP NOT NULL DEFAULT now(),
        refreshed_at  TIMESTAMP
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_nexus_brand_assets_kind
        ON nexus_brand_assets (product_id, asset_kind)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_nexus_brand_assets_lookup
        ON nexus_brand_assets (product_id, asset_kind, status)
    """,
    "ALTER TABLE nexus_products ADD COLUMN IF NOT EXISTS f1_sections JSONB",
]
