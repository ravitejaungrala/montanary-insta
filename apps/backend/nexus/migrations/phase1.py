"""Phase 1 — workspaces + user profiles.

Idempotent migrations for the auth/workspace foundation. Runs every
cold start while NEXUS_ENABLED=true. Side-table strategy: PIPELYT's
`users` row is not modified; NEXUS-specific user data lives in
`nexus_user_profiles`.
"""

MIGRATIONS = [
    # nexus_workspaces — multi-tenant container for sales-engagement data.
    """
    CREATE TABLE IF NOT EXISTS nexus_workspaces (
        id BIGSERIAL PRIMARY KEY,
        name VARCHAR NOT NULL,
        owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        is_trial VARCHAR DEFAULT 'true',
        trial_starts_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        trial_ends_at TIMESTAMP,
        plan VARCHAR DEFAULT 'starter',
        status VARCHAR DEFAULT 'active',
        limits JSONB,
        usage JSONB,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_workspaces_owner_status ON nexus_workspaces(owner_id, status);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_workspaces_created_at ON nexus_workspaces(created_at);",

    # nexus_user_profiles — 1:1 with users; NEXUS-specific user fields.
    """
    CREATE TABLE IF NOT EXISTS nexus_user_profiles (
        user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        role VARCHAR DEFAULT 'admin',
        default_workspace_id BIGINT REFERENCES nexus_workspaces(id) ON DELETE SET NULL,
        trial_starts_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        trial_ends_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    # nexus_workspace_invites — pending invitations.
    """
    CREATE TABLE IF NOT EXISTS nexus_workspace_invites (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL REFERENCES nexus_workspaces(id) ON DELETE CASCADE,
        invited_by_user_id BIGINT NOT NULL,
        email_lower VARCHAR NOT NULL,
        role VARCHAR DEFAULT 'member',
        token VARCHAR UNIQUE NOT NULL,
        status VARCHAR DEFAULT 'pending',
        expires_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        accepted_at TIMESTAMP
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_workspace_invites_ws ON nexus_workspace_invites(workspace_id);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_workspace_invites_email ON nexus_workspace_invites(email_lower);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_workspace_invites_token ON nexus_workspace_invites(token);",
]
