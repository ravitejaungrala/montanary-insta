"""Phase 12 — Performance Agent v1: idempotent upsert key for
nexus_winning_examples.

The table (created in phase6) had no uniqueness constraint, so
performance_agent/generator.py's upsert (delete-then-insert per key) would
otherwise accumulate duplicate rows across repeated /nexus/performance/generate
calls for the same (workspace, example_type, lead_segment) combination.

A UNIQUE INDEX (not `ADD CONSTRAINT`) is used because plain PostgreSQL has no
`ADD CONSTRAINT IF NOT EXISTS` for unique constraints — `CREATE UNIQUE INDEX
IF NOT EXISTS` is idempotent and enforces the same guarantee (same pattern as
phase9.py's nexus_perf_slices index, for consistency even though phase9
itself is not registered/live).

jsonb equality here compares the normalized binary representation (key order
doesn't matter), so this is safe as a uniqueness key. Purely additive — no
data migration, no edits to existing rows.
"""

from __future__ import annotations


MIGRATIONS = [
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_nexus_winning_examples_ws_type_segment "
    "ON nexus_winning_examples (workspace_id, example_type, lead_segment)",
]
