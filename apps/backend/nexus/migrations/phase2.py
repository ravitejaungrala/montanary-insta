"""
Phase 2 migrations — Product Analysis layer.

Idempotent CREATE TABLE / CREATE INDEX statements that the nexus
migration runner executes at Lambda cold start (see
`nexus/migrations/__init__.py`). Safe to re-run.

DO NOT add ALTER TABLE statements that drop or rename columns here —
add a new migration module instead. Live data must survive every
deploy.
"""

MIGRATIONS = [
    # -- nexus_products --------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_products (
        id                  BIGSERIAL PRIMARY KEY,
        workspace_id        BIGINT NOT NULL REFERENCES nexus_workspaces(id) ON DELETE CASCADE,
        user_id             BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name                VARCHAR(255),
        category            VARCHAR(120),
        value_proposition   TEXT,
        key_benefits        JSONB DEFAULT '[]'::jsonb,
        pricing_tier        VARCHAR(40),
        industry_relevance  VARCHAR(255),
        icp                 JSONB DEFAULT '{}'::jsonb,
        source_url          VARCHAR(2048),
        status              VARCHAR(32) NOT NULL DEFAULT 'ready',
        created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at          TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_products_workspace_id ON nexus_products(workspace_id);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_products_user_id      ON nexus_products(user_id);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_products_source_url   ON nexus_products(source_url);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_products_status       ON nexus_products(status);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_products_workspace_status ON nexus_products(workspace_id, status);",

    # -- nexus_product_assets --------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_product_assets (
        id          BIGSERIAL PRIMARY KEY,
        product_id  BIGINT NOT NULL REFERENCES nexus_products(id) ON DELETE CASCADE,
        asset_type  VARCHAR(16) NOT NULL DEFAULT 'text',
        source      VARCHAR(1024),
        raw_text    TEXT,
        char_count  INTEGER NOT NULL DEFAULT 0,
        created_at  TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_product_assets_product_id ON nexus_product_assets(product_id);",

    # -- nexus_knowledge_embeddings --------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_knowledge_embeddings (
        id                BIGSERIAL PRIMARY KEY,
        product_id        BIGINT NOT NULL REFERENCES nexus_products(id) ON DELETE CASCADE,
        product_asset_id  BIGINT REFERENCES nexus_product_assets(id) ON DELETE CASCADE,
        chunk_index       INTEGER NOT NULL DEFAULT 0,
        chunk_text        TEXT NOT NULL,
        embedding         JSONB,
        created_at        TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_kb_emb_product_id ON nexus_knowledge_embeddings(product_id);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_kb_emb_asset_id   ON nexus_knowledge_embeddings(product_asset_id);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_kb_emb_product_chunk ON nexus_knowledge_embeddings(product_id, chunk_index);",
]
