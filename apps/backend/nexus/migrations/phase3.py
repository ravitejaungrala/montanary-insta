"""Phase 3 idempotent DDL migrations.

The parent agent stitches MIGRATIONS from every phase into a single ordered
list invoked at Lambda cold start. Every statement here is safe to re-run.
"""
from __future__ import annotations

MIGRATIONS = [
    # -----------------------------------------------------------------
    # nexus_global_leads
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_global_leads (
        id              BIGSERIAL PRIMARY KEY,
        email           VARCHAR(320) NOT NULL UNIQUE,
        first_name      VARCHAR(120),
        last_name       VARCHAR(120),
        role            VARCHAR(160),
        company_domain  VARCHAR(255),
        company_name    VARCHAR(255),
        status          VARCHAR(32) NOT NULL DEFAULT 'new',
        email_verified  BOOLEAN NOT NULL DEFAULT FALSE,
        email_verify_score INTEGER NOT NULL DEFAULT 0,
        source          VARCHAR(64),
        created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_global_leads_email_lower ON nexus_global_leads (email);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_global_leads_company_domain ON nexus_global_leads (company_domain);",

    # -----------------------------------------------------------------
    # nexus_campaigns (must exist before nexus_leads references it
    # logically; the FK is added at merge time by the parent agent).
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_campaigns (
        id              BIGSERIAL PRIMARY KEY,
        workspace_id    BIGINT NOT NULL,
        product_id      BIGINT,
        name            VARCHAR(255) NOT NULL,
        icp             JSONB,
        status          VARCHAR(32) NOT NULL DEFAULT 'draft',
        created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_campaigns_workspace ON nexus_campaigns (workspace_id);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_campaigns_product ON nexus_campaigns (product_id);",
    # Defensive FK to nexus_workspaces — added only if Phase 1 table exists.
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='nexus_workspaces')
           AND NOT EXISTS (
               SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name='fk_nexus_campaigns_workspace'
           ) THEN
            ALTER TABLE nexus_campaigns
              ADD CONSTRAINT fk_nexus_campaigns_workspace
              FOREIGN KEY (workspace_id) REFERENCES nexus_workspaces(id)
              ON DELETE CASCADE;
        END IF;
    END $$;
    """,

    # -----------------------------------------------------------------
    # nexus_leads
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_leads (
        id              BIGSERIAL PRIMARY KEY,
        workspace_id    BIGINT NOT NULL,
        campaign_id     BIGINT,
        product_id      BIGINT,
        global_lead_id  BIGINT NOT NULL,
        icp_score       INTEGER NOT NULL DEFAULT 0,
        signals         JSONB,
        created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMP NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_nexus_leads_scope UNIQUE (workspace_id, campaign_id, global_lead_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_leads_workspace ON nexus_leads (workspace_id);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_leads_campaign ON nexus_leads (campaign_id);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_leads_global ON nexus_leads (global_lead_id);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_leads_workspace_campaign ON nexus_leads (workspace_id, campaign_id);",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
             WHERE constraint_name='fk_nexus_leads_global_lead'
        ) THEN
            ALTER TABLE nexus_leads
              ADD CONSTRAINT fk_nexus_leads_global_lead
              FOREIGN KEY (global_lead_id) REFERENCES nexus_global_leads(id)
              ON DELETE CASCADE;
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='nexus_workspaces')
           AND NOT EXISTS (
               SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name='fk_nexus_leads_workspace'
           ) THEN
            ALTER TABLE nexus_leads
              ADD CONSTRAINT fk_nexus_leads_workspace
              FOREIGN KEY (workspace_id) REFERENCES nexus_workspaces(id)
              ON DELETE CASCADE;
        END IF;
    END $$;
    """,

    # -----------------------------------------------------------------
    # nexus_lead_enrichment
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_lead_enrichment (
        id                BIGSERIAL PRIMARY KEY,
        company_domain    VARCHAR(255) NOT NULL UNIQUE,
        page_title        VARCHAR(512),
        meta_description  TEXT,
        headings          JSONB,
        body_snippet      TEXT,
        tech_stack        JSONB,
        news_headlines    JSONB,
        fetched_at        TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_lead_enrichment_fetched_at ON nexus_lead_enrichment (fetched_at);",
    # 2026-07-29 — structured profile of the lead's own company (Apollo
    # organization-enrich: headcount, revenue, HQ, keywords, tech stack).
    "ALTER TABLE nexus_lead_enrichment ADD COLUMN IF NOT EXISTS company_profile JSONB;",

    # -----------------------------------------------------------------
    # nexus_intent_signals
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_intent_signals (
        id            BIGSERIAL PRIMARY KEY,
        lead_id       BIGINT NOT NULL,
        signal_type   VARCHAR(64) NOT NULL,
        payload       JSONB,
        observed_at   TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_intent_signals_lead ON nexus_intent_signals (lead_id);",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
             WHERE constraint_name='fk_nexus_intent_signals_lead'
        ) THEN
            ALTER TABLE nexus_intent_signals
              ADD CONSTRAINT fk_nexus_intent_signals_lead
              FOREIGN KEY (lead_id) REFERENCES nexus_leads(id) ON DELETE CASCADE;
        END IF;
    END $$;
    """,

    # -----------------------------------------------------------------
    # nexus_apollo_lead_cache
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_apollo_lead_cache (
        id          BIGSERIAL PRIMARY KEY,
        query_hash  VARCHAR(64) NOT NULL UNIQUE,
        response    JSONB,
        fetched_at  TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """,

    # -----------------------------------------------------------------
    # nexus_scrape_cache — cross-process ScrapeBundle cache (2026-06-11).
    # The in-memory scrape cache only survives within ONE process, so on
    # Lambda the /scrape-preview and /analyze requests can land on
    # DIFFERENT containers and the launch re-scrapes. This table makes
    # the preview's scrape visible to every container. Same 15-min TTL
    # semantics as the in-memory layer (enforced on read; expired rows
    # pruned opportunistically on write). Mirrors nexus_apollo_lead_cache.
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_scrape_cache (
        id          BIGSERIAL PRIMARY KEY,
        url_hash    VARCHAR(64) NOT NULL UNIQUE,
        url         VARCHAR(2048),
        bundle      JSONB,
        fetched_at  TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """,

    # -----------------------------------------------------------------
    # nexus_campaign_launches — append-only log of /analyze launches.
    # Each /analyze call reuses the same campaign row for a product, so
    # campaign.created_at is pinned to the very first run ever. This
    # side-table records the START time of every subsequent run so the
    # outbound-emails preview can fence touchpoints to "this launch
    # only" using a server-side clock (no browser-vs-server skew).
    #
    # Read pattern: MAX(launched_at) WHERE campaign_id = :cid.
    # Write pattern: one INSERT per /analyze call (post-commit).
    #
    # Intentionally narrow shape: no FK to nexus_campaigns (other
    # cross-phase tables follow the same bare-BIGINT convention — see
    # the `# TODO: FK at merge` notes throughout models_phase3.py).
    # ON DELETE handling: if a campaign is deleted the launches stay
    # as orphan history — they're harmless (no JOINs depend on them
    # outside the outbound-emails endpoint, which scopes by campaign_id
    # and gets an empty result for missing campaigns).
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_campaign_launches (
        id            BIGSERIAL PRIMARY KEY,
        campaign_id   BIGINT NOT NULL,
        launched_at   TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """,
    # Composite index — the MAX(launched_at) WHERE campaign_id lookup
    # in campaigns.py.list_campaign_outbound_emails reads `launched_at`
    # DESC for a single campaign. The (campaign_id, launched_at DESC)
    # ordering lets Postgres satisfy that with an index-only scan.
    "CREATE INDEX IF NOT EXISTS ix_nexus_campaign_launches_campaign_time "
    "ON nexus_campaign_launches (campaign_id, launched_at DESC);",

    # Apollo page-progression cursor. Each discovery run reads this,
    # asks Apollo for pages [last+1 .. last+APOLLO_MAX_PAGES], and
    # writes back the new high-water mark. Guarantees re-runs surface
    # FRESH candidates instead of replaying the same page-1 cohort
    # every time (which combined with per-product dedup produced
    # "zero new leads" on every re-run of an existing product).
    # Default 0 means "no run has happened yet → start at page 1".
    # Additive; idempotent on cold start.
    """ALTER TABLE nexus_campaigns
         ADD COLUMN IF NOT EXISTS last_apollo_page INTEGER NOT NULL DEFAULT 0""",

    # Sister cursor for the broaden-on-low-yield retry path. When the
    # strict ICP yields fewer than `min_leads` candidates, search_people
    # falls back to a broader query (drops q_keywords + person_titles).
    # That broader query has its own independent pagination space —
    # without this cursor, every broader retry would replay page 1
    # forever and produce the same exhausted candidate pool. This
    # cursor advances by 1 every time the broader retry fires, so
    # the broader pool keeps yielding fresh people too. Default 0.
    """ALTER TABLE nexus_campaigns
         ADD COLUMN IF NOT EXISTS last_apollo_broader_page INTEGER NOT NULL DEFAULT 0""",

    # ── Per-product campaign number (2026-06-11) ─────────────────────────────
    # A human-friendly sequence number that restarts at 1 FOR EACH PRODUCT
    # (Spenzo → 1,2,3; Acme → 1,2,…), shown in the GTM Journey table. The
    # global `id` PK stays the internal identifier; this is a stable, assigned-
    # once DISPLAY number. Nullable so the column add is instant + safe.
    """ALTER TABLE nexus_campaigns
         ADD COLUMN IF NOT EXISTS product_campaign_number INTEGER""",

    # One-time BACKFILL: number every existing campaign per-product by id order.
    # Gated on `IS NULL` so it only fills rows that don't already have a number
    # (existing campaigns on first run); new campaigns get theirs from analyze.py
    # and are skipped on later startups → idempotent, no churn.
    """UPDATE nexus_campaigns c
          SET product_campaign_number = r.rn
         FROM (SELECT id,
                      ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY id) AS rn
                 FROM nexus_campaigns) r
        WHERE c.id = r.id
          AND c.product_campaign_number IS NULL""",

    # Integrity: no two campaigns of the same product can share a number.
    # Partial index (non-null only) so legacy/unnumbered rows don't collide.
    """CREATE UNIQUE INDEX IF NOT EXISTS ux_nexus_campaigns_product_seq
         ON nexus_campaigns (product_id, product_campaign_number)
       WHERE product_campaign_number IS NOT NULL""",
]


__all__ = ["MIGRATIONS"]
