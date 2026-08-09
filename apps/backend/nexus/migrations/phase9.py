"""Phase 9 idempotent migration — Performance Agent.

Adds three NEW tables (purely additive, no edits to existing schema):

  - nexus_perf_slices       Bayesian Beta posteriors per
                            (workspace, dimension, slice_value).
  - nexus_perf_suggestions  Suggestions surfaced to the UI, with
                            approve / reject / dismissed state.
  - nexus_perf_settings     Per-workspace toggle for auto-mode,
                            half-life, min-sample-size knobs.

All statements are CREATE ... IF NOT EXISTS and safe to re-run.
"""

from __future__ import annotations


MIGRATIONS = [
    # ── nexus_perf_slices ──────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nexus_perf_slices (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL,
        dimension VARCHAR(40) NOT NULL,
        slice_value VARCHAR(160) NOT NULL,
        alpha DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        beta DOUBLE PRECISION NOT NULL DEFAULT 19.0,
        raw_sends INTEGER NOT NULL DEFAULT 0,
        raw_replies INTEGER NOT NULL DEFAULT 0,
        last_recomputed_at TIMESTAMP DEFAULT NOW(),
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_nexus_perf_slices_ws_dim_val ON nexus_perf_slices (workspace_id, dimension, slice_value)",
    "CREATE INDEX IF NOT EXISTS ix_nexus_perf_slices_ws_dim ON nexus_perf_slices (workspace_id, dimension)",

    # ── nexus_perf_suggestions ─────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nexus_perf_suggestions (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL,
        kind VARCHAR(40) NOT NULL,
        target VARCHAR(160) DEFAULT '',
        title TEXT DEFAULT '',
        rationale TEXT DEFAULT '',
        evidence JSONB,
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        feedback_note TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT NOW(),
        decided_at TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_perf_sugg_ws_status ON nexus_perf_suggestions (workspace_id, status, created_at DESC)",

    # ── nexus_perf_settings ────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nexus_perf_settings (
        workspace_id BIGINT PRIMARY KEY,
        auto_mode_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        half_life_days INTEGER NOT NULL DEFAULT 60,
        min_sample_size INTEGER NOT NULL DEFAULT 10,
        prior_alpha DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        prior_beta DOUBLE PRECISION NOT NULL DEFAULT 19.0,
        last_run_at TIMESTAMP,
        last_run_status VARCHAR(20) DEFAULT '',
        last_run_error TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    )
    """,
]
