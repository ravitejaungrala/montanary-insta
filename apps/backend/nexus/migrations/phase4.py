"""Idempotent Phase 4 DDL.

Executed by parent merger via Nexus migration runner at startup. Each
statement is safe to re-run.
"""

MIGRATIONS = [
    # -----------------------------------------------------------------------
    # nexus_sequences
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_sequences (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT REFERENCES nexus_workspaces(id) ON DELETE CASCADE,
        campaign_id BIGINT,
        name VARCHAR NOT NULL,
        steps JSONB NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_sequences_ws ON nexus_sequences(workspace_id);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_sequences_campaign ON nexus_sequences(campaign_id);",

    # -----------------------------------------------------------------------
    # nexus_lead_sequences
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_lead_sequences (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT REFERENCES nexus_workspaces(id) ON DELETE CASCADE,
        campaign_id BIGINT,
        lead_id BIGINT,
        sequence_id BIGINT REFERENCES nexus_sequences(id) ON DELETE CASCADE,
        current_step INT DEFAULT 0,
        next_action_at TIMESTAMP,
        status VARCHAR DEFAULT 'active',
        last_error TEXT,
        enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_lead_sequences_due ON nexus_lead_sequences(status, next_action_at);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_lead_sequences_ws ON nexus_lead_sequences(workspace_id, status);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_lead_sequences_lead ON nexus_lead_sequences(lead_id);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_lead_sequences_seq ON nexus_lead_sequences(sequence_id);",

    # -----------------------------------------------------------------------
    # nexus_touchpoints
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_touchpoints (
        id BIGSERIAL PRIMARY KEY,
        lead_sequence_id BIGINT REFERENCES nexus_lead_sequences(id) ON DELETE CASCADE,
        step INT NOT NULL,
        subject TEXT,
        body TEXT,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        resend_message_id VARCHAR,
        status VARCHAR DEFAULT 'sent',
        error TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_touchpoints_ls ON nexus_touchpoints(lead_sequence_id);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_touchpoints_status ON nexus_touchpoints(status);",

    # -----------------------------------------------------------------------
    # nexus_personalization_cache
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_personalization_cache (
        lead_id BIGINT NOT NULL,
        sequence_id BIGINT NOT NULL,
        step INT NOT NULL,
        subject TEXT,
        body TEXT,
        model_used VARCHAR,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (lead_id, sequence_id, step)
    );
    """,

    # -----------------------------------------------------------------------
    # nexus_send_logs
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_send_logs (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT REFERENCES nexus_workspaces(id) ON DELETE CASCADE,
        conversation_account_id BIGINT,
        lead_id BIGINT,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        recipient VARCHAR,
        status VARCHAR
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_send_logs_ws ON nexus_send_logs(workspace_id);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_send_logs_sent ON nexus_send_logs(sent_at);",

    # -----------------------------------------------------------------------
    # nexus_conversation_accounts
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_conversation_accounts (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT REFERENCES nexus_workspaces(id) ON DELETE CASCADE,
        email_address VARCHAR UNIQUE NOT NULL,
        daily_send_limit INT DEFAULT 20,
        daily_send_count INT DEFAULT 0,
        last_reset_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        provider VARCHAR DEFAULT 'resend',
        refresh_token TEXT,
        status VARCHAR DEFAULT 'active'
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_conv_accounts_ws ON nexus_conversation_accounts(workspace_id);",

    # -----------------------------------------------------------------------
    # nexus_suppression_list
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_suppression_list (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT REFERENCES nexus_workspaces(id) ON DELETE CASCADE,
        email_lower VARCHAR NOT NULL,
        reason VARCHAR,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_nexus_suppression_ws_email UNIQUE (workspace_id, email_lower)
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_suppression_email ON nexus_suppression_list(email_lower);",

    # -----------------------------------------------------------------------
    # nexus_automations
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nexus_automations (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT REFERENCES nexus_workspaces(id) ON DELETE CASCADE,
        campaign_id BIGINT,
        name VARCHAR NOT NULL,
        schedule_type VARCHAR DEFAULT 'daily',
        tz VARCHAR DEFAULT 'UTC',
        target_leads INT DEFAULT 100,
        next_run_at TIMESTAMP,
        last_run_at TIMESTAMP,
        status VARCHAR DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_automations_due ON nexus_automations(status, next_run_at);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_automations_ws ON nexus_automations(workspace_id);",

    # -----------------------------------------------------------------------
    # Patch — late-added columns. Safe to re-run on every cold start.
    # -----------------------------------------------------------------------
    "ALTER TABLE nexus_lead_sequences ADD COLUMN IF NOT EXISTS locked_until TIMESTAMP;",
    "ALTER TABLE nexus_lead_sequences ADD COLUMN IF NOT EXISTS halt_reason VARCHAR(64);",
    "ALTER TABLE nexus_lead_sequences ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;",
    "CREATE INDEX IF NOT EXISTS ix_nexus_lead_sequences_lock ON nexus_lead_sequences(locked_until);",

    "ALTER TABLE nexus_automations ADD COLUMN IF NOT EXISTS run_count INT DEFAULT 0;",
    "ALTER TABLE nexus_automations ADD COLUMN IF NOT EXISTS leads_generated INT DEFAULT 0;",
    "ALTER TABLE nexus_automations ADD COLUMN IF NOT EXISTS last_run_status VARCHAR(32);",
    "ALTER TABLE nexus_automations ADD COLUMN IF NOT EXISTS last_run_summary TEXT;",

    # nexus_touchpoints — needed by tracking pixel + click redirect
    "ALTER TABLE nexus_touchpoints ADD COLUMN IF NOT EXISTS opens_count INT DEFAULT 0;",
    "ALTER TABLE nexus_touchpoints ADD COLUMN IF NOT EXISTS last_opened_at TIMESTAMP;",
    "ALTER TABLE nexus_touchpoints ADD COLUMN IF NOT EXISTS clicks_count INT DEFAULT 0;",
    "ALTER TABLE nexus_touchpoints ADD COLUMN IF NOT EXISTS last_clicked_at TIMESTAMP;",
]
