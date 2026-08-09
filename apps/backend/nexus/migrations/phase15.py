"""Phase 15 — per-workspace lead status/phone (workspace-isolation fix).

Engagement `status` and captured `phone`/`phones` used to live ONLY on the
shared `nexus_global_leads` row, so a person targeted by two workspaces leaked
one tenant's state into the other. This adds per-workspace columns on
`nexus_leads` (the workspace junction) and backfills them from the global row.

Purely additive: the global columns are kept as a legacy fallback. The backfill
only fills rows where the per-workspace value is still NULL, so re-running it on
every cold start can NEVER overwrite a value a workspace has since diverged.
"""

from __future__ import annotations


MIGRATIONS = [
    "ALTER TABLE nexus_leads ADD COLUMN IF NOT EXISTS status VARCHAR(32)",
    "ALTER TABLE nexus_leads ADD COLUMN IF NOT EXISTS phone VARCHAR(40)",
    "ALTER TABLE nexus_leads ADD COLUMN IF NOT EXISTS phones JSONB",
    # NULL-only backfill from the shared global row (idempotent).
    """UPDATE nexus_leads nl
          SET status = gl.status
         FROM nexus_global_leads gl
        WHERE nl.global_lead_id = gl.id
          AND nl.status IS NULL
          AND gl.status IS NOT NULL""",
    """UPDATE nexus_leads nl
          SET phone = gl.phone
         FROM nexus_global_leads gl
        WHERE nl.global_lead_id = gl.id
          AND nl.phone IS NULL
          AND gl.phone IS NOT NULL""",
    """UPDATE nexus_leads nl
          SET phones = gl.phones
         FROM nexus_global_leads gl
        WHERE nl.global_lead_id = gl.id
          AND nl.phones IS NULL
          AND gl.phones IS NOT NULL""",
]
