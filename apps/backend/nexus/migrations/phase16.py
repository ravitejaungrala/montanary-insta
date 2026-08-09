"""Phase 16 — prospect-facing meeting agendas (nexus_meeting_agendas).

One agenda per booked demo, generated from the lead + company + product context
(with RAG grounding over the product KB). Distinct from nexus_demo_briefings
(internal rep intel) — this artifact is prospect-shareable.

Purely additive.
"""

from __future__ import annotations


MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS nexus_meeting_agendas (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT,
        demo_booking_id BIGINT NOT NULL,
        lead_id BIGINT,
        product_id BIGINT,
        agenda_markdown TEXT DEFAULT '',
        agenda_html TEXT DEFAULT '',
        model VARCHAR(80),
        source VARCHAR(16) DEFAULT 'auto',
        generated_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    )
    """,
    # One agenda per booking — the orchestrator upserts on this.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_nexus_meeting_agendas_booking "
    "ON nexus_meeting_agendas (demo_booking_id)",
    # 2026-07-29 — grounding provenance. Records whether THIS agenda actually
    # retrieved product-KB chunks (and how many), so the prospect-delivery gate
    # can key off real retrieval instead of "the product has an indexed asset
    # somewhere". An agenda with grounded=false must never reach a prospect.
    "ALTER TABLE nexus_meeting_agendas ADD COLUMN IF NOT EXISTS grounding JSONB",
]
