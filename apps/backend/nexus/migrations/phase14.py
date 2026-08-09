"""Phase 14 — allow a blank (unscored) match score on BYO-leads.

Drops the NOT NULL constraint on `nexus_leads.icp_score` so a user-uploaded
lead that did NOT supply a "Match Score" column stores NULL (rendered as a
blank/em-dash in the GTM Journey) instead of the old hardcoded 100. Apollo-
discovered leads always compute a real score, so they are unaffected.

`DROP NOT NULL` is idempotent (dropping it on an already-nullable column is a
no-op), matching the additive-only migration policy — no data is dropped.
"""

from __future__ import annotations


MIGRATIONS = [
    "ALTER TABLE nexus_leads ALTER COLUMN icp_score DROP NOT NULL",
]
