"""
Idempotent Phase-5 migration. Mirrors the `sync_db_schema()` style used in
Pipelyt's `apps/backend/main.py` — every statement is safe to re-run on
Lambda cold start because we guard with `IF NOT EXISTS`.

Call `run_phase5_migration(engine)` once at startup AFTER Phase 1 tables
exist. If pgvector is available we additionally create cosine-similarity
indexes used by `services.rag_reply`; if not we silently skip and the RAG
service falls back to JSONB cosine in Python.
"""

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger("nexus.migrations.phase5")


# Plain DDL — Postgres dialect. We do NOT add FK constraints here for the
# cross-phase columns; those are wired in a final consolidation migration
# once all phases land in the same metadata.
_DDL_STATEMENTS = [
    # --- nexus_inbound_leads ----------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_inbound_leads (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL,
        email VARCHAR(320) NOT NULL,
        first_name VARCHAR(120),
        last_name VARCHAR(120),
        source VARCHAR(64),
        created_at TIMESTAMPTZ DEFAULT NOW(),
        CONSTRAINT uq_inbound_lead_ws_email UNIQUE (workspace_id, email)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_inbound_leads_ws ON nexus_inbound_leads (workspace_id)",
    "CREATE INDEX IF NOT EXISTS ix_nexus_inbound_leads_email ON nexus_inbound_leads (email)",

    # --- nexus_inbound_threads --------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_inbound_threads (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL,
        lead_id BIGINT,
        lead_sequence_id BIGINT,
        gmail_thread_id VARCHAR(255),
        resend_thread_id VARCHAR(255),
        subject VARCHAR(998),
        last_message_at TIMESTAMPTZ,
        message_count INT NOT NULL DEFAULT 0,
        status VARCHAR(16) NOT NULL DEFAULT 'open',
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_threads_ws ON nexus_inbound_threads (workspace_id)",
    "CREATE INDEX IF NOT EXISTS ix_nexus_threads_gmail ON nexus_inbound_threads (gmail_thread_id)",
    "CREATE INDEX IF NOT EXISTS ix_nexus_threads_resend ON nexus_inbound_threads (resend_thread_id)",
    "CREATE INDEX IF NOT EXISTS ix_nexus_threads_lead ON nexus_inbound_threads (lead_id)",
    "CREATE INDEX IF NOT EXISTS ix_nexus_threads_seq ON nexus_inbound_threads (lead_sequence_id)",
    "CREATE INDEX IF NOT EXISTS ix_nexus_threads_last_msg ON nexus_inbound_threads (last_message_at)",

    # --- nexus_inbound_messages -------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_inbound_messages (
        id BIGSERIAL PRIMARY KEY,
        thread_id BIGINT NOT NULL,
        direction VARCHAR(16) NOT NULL,
        from_email VARCHAR(320),
        to_email VARCHAR(320),
        subject VARCHAR(998),
        body_text TEXT,
        body_html TEXT,
        message_id_header VARCHAR(998),
        in_reply_to_header VARCHAR(998),
        received_at TIMESTAMPTZ DEFAULT NOW(),
        intent VARCHAR(32),
        intent_confidence DOUBLE PRECISION,
        intent_reasoning TEXT,
        suggested_reply TEXT,
        is_read BOOLEAN NOT NULL DEFAULT FALSE
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_inbound_msg_thread_received ON nexus_inbound_messages (thread_id, received_at)",
    "CREATE INDEX IF NOT EXISTS ix_inbound_msg_from ON nexus_inbound_messages (from_email)",
    "CREATE INDEX IF NOT EXISTS ix_inbound_msg_msg_id ON nexus_inbound_messages (message_id_header)",
    "CREATE INDEX IF NOT EXISTS ix_inbound_msg_inreply ON nexus_inbound_messages (in_reply_to_header)",
    "CREATE INDEX IF NOT EXISTS ix_inbound_msg_intent ON nexus_inbound_messages (intent)",
    "CREATE INDEX IF NOT EXISTS ix_inbound_msg_unread ON nexus_inbound_messages (is_read)",

    # --- nexus_inbox -------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_inbox (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL,
        thread_id BIGINT NOT NULL,
        message_id BIGINT NOT NULL,
        is_unread BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        CONSTRAINT uq_inbox_ws_msg UNIQUE (workspace_id, message_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_inbox_ws_unread ON nexus_inbox (workspace_id, is_unread)",

    # --- nexus_unmatched_replies ------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_unmatched_replies (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT,
        from_email VARCHAR(320),
        subject VARCHAR(998),
        body_text TEXT,
        body_html TEXT,
        message_id_header VARCHAR(998),
        in_reply_to_header VARCHAR(998),
        raw_payload TEXT,
        received_at TIMESTAMPTZ DEFAULT NOW(),
        processed BOOLEAN NOT NULL DEFAULT FALSE
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_unmatched_processed ON nexus_unmatched_replies (processed)",
    "CREATE INDEX IF NOT EXISTS ix_unmatched_from ON nexus_unmatched_replies (from_email)",

    # --- nexus_linkedin_messages ------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_linkedin_messages (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL,
        lead_id BIGINT,
        lead_sequence_id BIGINT,
        direction VARCHAR(16) NOT NULL,
        body TEXT,
        linkedin_message_urn VARCHAR(255),
        sent_at TIMESTAMPTZ DEFAULT NOW(),
        intent VARCHAR(32),
        intent_confidence DOUBLE PRECISION
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_li_msg_ws_lead ON nexus_linkedin_messages (workspace_id, lead_id)",
    "CREATE INDEX IF NOT EXISTS ix_li_msg_urn ON nexus_linkedin_messages (linkedin_message_urn)",

    # InMail support — additive columns on the existing
    # `nexus_linkedin_messages` table so the workspace can store both
    # short DMs (variant='dm', subject NULL) and longer InMail messages
    # (variant='inmail', subject populated) without a parallel table.
    # Both columns are NULL/default for existing rows, so no backfill is
    # required and no existing query touches these fields.
    """ALTER TABLE nexus_linkedin_messages
         ADD COLUMN IF NOT EXISTS variant VARCHAR(16) NOT NULL DEFAULT 'dm'""",
    """ALTER TABLE nexus_linkedin_messages
         ADD COLUMN IF NOT EXISTS subject TEXT""",
    # InMail cadence step: 0=initial, 1=follow-up 1, 2=follow-up 2, 3=closing.
    # DMs (connection notes) stay at 0. Default 0 → existing rows unaffected.
    """ALTER TABLE nexus_linkedin_messages
         ADD COLUMN IF NOT EXISTS step SMALLINT NOT NULL DEFAULT 0""",
    # The InMail cadence stores 4 outbound rows per lead, so the outbound
    # uniqueness key must include `step`: one row per (ws, lead, variant,
    # step). Replaces the older one-per-variant index.
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_li_msg_outbound_variant_step
         ON nexus_linkedin_messages
         (workspace_id, lead_id, COALESCE(NULLIF(variant::text, ''), 'dm'), step)
         WHERE direction = 'outbound'""",
    "DROP INDEX IF EXISTS uq_li_msg_outbound_one_per_variant",
]


# Optional pgvector cosine index — only fires if the extension and
# Phase 4's nexus_knowledge_embeddings table both exist.
_PGVECTOR_DDL = [
    """
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')
           AND EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_name = 'nexus_knowledge_embeddings') THEN
            EXECUTE 'CREATE INDEX IF NOT EXISTS ix_nke_embedding_cosine
                     ON nexus_knowledge_embeddings
                     USING ivfflat (embedding vector_cosine_ops)
                     WITH (lists = 100)';
        END IF;
    END
    $$;
    """,
]


def run_phase5_migration(engine: Engine, attempt_pgvector: bool = True) -> None:
    """Apply Phase 5 schema. Idempotent — safe to call on every cold start.

    `attempt_pgvector` is best-effort: failure to create the vector index is
    not fatal (the RAG service falls back to JSONB cosine in Python).
    """
    with engine.begin() as conn:
        for stmt in _DDL_STATEMENTS:
            try:
                conn.execute(text(stmt))
            except SQLAlchemyError as exc:
                logger.warning("phase5 DDL failed (continuing): %s — %s", stmt.split("\n", 1)[0].strip(), exc)

    if attempt_pgvector:
        try:
            with engine.begin() as conn:
                for stmt in _PGVECTOR_DDL:
                    conn.execute(text(stmt))
        except SQLAlchemyError as exc:
            logger.info("phase5 pgvector index skipped: %s", exc)


def downgrade(engine: Engine) -> None:
    """Drop all Phase 5 tables. NOT called automatically — manual ops only."""
    tables = [
        "nexus_linkedin_messages",
        "nexus_processed_gmail_messages",
        "nexus_unmatched_replies",
        "nexus_inbox",
        "nexus_inbound_messages",
        "nexus_inbound_threads",
        "nexus_inbound_leads",
    ]
    with engine.begin() as conn:
        for t in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))


# Adapter for nexus.migrations.ALL_PHASES — the parent runner expects a
# MIGRATIONS list of SQL strings. Combine the core DDL with the optional
# pgvector index DDL (the DO $$ ... $$ block silently skips if the
# extension is absent, so it is safe to include unconditionally).
MIGRATIONS = list(_DDL_STATEMENTS) + list(_PGVECTOR_DDL)

__all__ = ["run_phase5_migration", "downgrade", "MIGRATIONS"]
