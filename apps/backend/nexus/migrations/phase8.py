"""Phase 8 — legacy-parity aliases.

Adds compatibility surfaces that match the legacy model/table names so
the Mongo->Postgres migration can target the legacy names without
renaming.

Specifically:
  - `nexus_invitations` VIEW pointing at `nexus_workspace_invites`
    (legacy MongoDB had a model called `Invitation`).
"""

from __future__ import annotations

MIGRATIONS = [
    # Legacy "Invitation" model maps 1:1 to nexus_workspace_invites; we
    # expose a view under the legacy name so any importer that asks for
    # `nexus_invitations` resolves cleanly. Views are idempotent via
    # CREATE OR REPLACE.
    """CREATE OR REPLACE VIEW nexus_invitations AS
       SELECT id,
              workspace_id,
              invited_by_user_id,
              email_lower,
              role,
              token,
              status,
              expires_at,
              created_at,
              accepted_at
       FROM nexus_workspace_invites;""",
]
