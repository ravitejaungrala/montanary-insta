"""GTM LinkedIn Agent schema — additive, idempotent DDL.

Source of truth for the gtm_linkedin_* tables (ORM mirror: gtm/linkedin/models.py).
Registered in `nexus/migrations/__init__.py` (the central migration runner) and
applied on cold start regardless of NEXUS_LINKEDIN_ENABLED — empty tables are
harmless; only the runtime behavior is gated.

Conventions (per nexus/migrations/__init__.py): every statement idempotent,
additive only, never DROP. Cross-subsystem references (nexus_workspaces,
nexus_products, nexus_global_leads, nexus_campaigns) are bare BIGINT columns —
no hard FK constraints, matching the rest of the schema.
"""

MIGRATIONS = [
    # 1) gtm_linkedin_accounts
    """
    CREATE TABLE IF NOT EXISTS gtm_linkedin_accounts (
      id BIGSERIAL PRIMARY KEY,
      workspace_id BIGINT NOT NULL,
      user_id BIGINT,
      linkedin_email VARCHAR(255) NOT NULL,
      linkedin_display_name VARCHAR(255),
      linkedin_account_type VARCHAR(32) DEFAULT 'personal',
      encrypted_cookies TEXT,
      linkedin_session_status VARCHAR(24) DEFAULT 'pending',
      linkedin_reauth_required BOOLEAN DEFAULT false,
      is_paused BOOLEAN DEFAULT false,
      cooldown_until TIMESTAMPTZ,
      last_successful_login TIMESTAMPTZ,
      last_successful_check TIMESTAMPTZ,
      last_successful_action_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ DEFAULT now(),
      updated_at TIMESTAMPTZ DEFAULT now(),
      CONSTRAINT uq_gtm_li_acct_ws_email UNIQUE (workspace_id, linkedin_email)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_gtm_li_acct_ws ON gtm_linkedin_accounts (workspace_id)",

    # 1b) gtm_linkedin_account_brands (M:N account ↔ sender brand)
    # Brand-keyed (not product-keyed) so LinkedIn picks senders the same way as
    # the email engine (mailboxes bind to nexus_sender_brands.id). The legacy
    # *_account_products table is dropped — it never held data (feature dark).
    "DROP TABLE IF EXISTS gtm_linkedin_account_products",
    """
    CREATE TABLE IF NOT EXISTS gtm_linkedin_account_brands (
      id BIGSERIAL PRIMARY KEY,
      linkedin_account_id BIGINT NOT NULL REFERENCES gtm_linkedin_accounts(id) ON DELETE CASCADE,
      brand_id BIGINT NOT NULL,
      created_at TIMESTAMPTZ DEFAULT now(),
      CONSTRAINT uq_gtm_li_acct_brand UNIQUE (linkedin_account_id, brand_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_gtm_li_acct_brand_brand ON gtm_linkedin_account_brands (brand_id)",

    # 2) gtm_linkedin_account_settings
    """
    CREATE TABLE IF NOT EXISTS gtm_linkedin_account_settings (
      id BIGSERIAL PRIMARY KEY,
      linkedin_account_id BIGINT NOT NULL UNIQUE REFERENCES gtm_linkedin_accounts(id) ON DELETE CASCADE,
      daily_connection_limit INT DEFAULT 20,
      daily_message_limit INT DEFAULT 20,
      daily_inmail_limit INT DEFAULT 20,
      working_start_time VARCHAR(8) DEFAULT '07:00',
      working_end_time VARCHAR(8) DEFAULT '22:00',
      timezone VARCHAR(64) DEFAULT 'Asia/Kolkata',
      random_delay_min_seconds INT DEFAULT 3,
      random_delay_max_seconds INT DEFAULT 12,
      daily_connection_count INT DEFAULT 0,
      daily_message_count INT DEFAULT 0,
      daily_inmail_count INT DEFAULT 0,
      last_reset_at TIMESTAMPTZ
    )
    """,

    # 3) gtm_linkedin_browser_profiles
    """
    CREATE TABLE IF NOT EXISTS gtm_linkedin_browser_profiles (
      id BIGSERIAL PRIMARY KEY,
      linkedin_account_id BIGINT NOT NULL UNIQUE REFERENCES gtm_linkedin_accounts(id) ON DELETE CASCADE,
      user_agent VARCHAR(512),
      viewport VARCHAR(16) DEFAULT '1366x768',
      browser_timezone VARCHAR(64) DEFAULT 'Asia/Kolkata',
      browser_locale VARCHAR(16) DEFAULT 'en-US',
      created_at TIMESTAMPTZ DEFAULT now()
    )
    """,

    # 4) gtm_linkedin_lead_state
    """
    CREATE TABLE IF NOT EXISTS gtm_linkedin_lead_state (
      id BIGSERIAL PRIMARY KEY,
      workspace_id BIGINT NOT NULL,
      lead_id BIGINT NOT NULL,
      campaign_id BIGINT NOT NULL,
      linkedin_account_id BIGINT,
      linkedin_profile_url TEXT,
      linkedin_member_id VARCHAR(128),
      linkedin_public_identifier VARCHAR(128),
      last_seen_profile_url TEXT,
      last_seen_name VARCHAR(255),
      last_verified_at TIMESTAMPTZ,
      linkedin_connection_status VARCHAR(16) DEFAULT 'none',
      linkedin_message_reply_status VARCHAR(16) DEFAULT 'none',
      linkedin_inmail_status VARCHAR(24) DEFAULT 'none',
      linkedin_sequence_status VARCHAR(16) DEFAULT 'active',
      linkedin_current_step VARCHAR(32),
      linkedin_current_branch VARCHAR(24),
      workflow_mode VARCHAR(16) DEFAULT 'standard',
      next_action_at TIMESTAMPTZ,
      locked_until TIMESTAMPTZ,
      linkedin_connection_sent_at TIMESTAMPTZ,
      linkedin_connection_accepted_at TIMESTAMPTZ,
      linkedin_message_sent_at TIMESTAMPTZ,
      linkedin_inmail_sent_at TIMESTAMPTZ,
      last_message_at TIMESTAMPTZ,
      last_reply_at TIMESTAMPTZ,
      paused_reason VARCHAR(48),
      created_at TIMESTAMPTZ DEFAULT now(),
      updated_at TIMESTAMPTZ DEFAULT now(),
      CONSTRAINT uq_gtm_li_state_lead_campaign UNIQUE (lead_id, campaign_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_gtm_li_state_due ON gtm_linkedin_lead_state (linkedin_sequence_status, next_action_at)",
    "CREATE INDEX IF NOT EXISTS ix_gtm_li_state_ws ON gtm_linkedin_lead_state (workspace_id)",
    # Identity-resilience columns (additive for DBs created before this change).
    "ALTER TABLE gtm_linkedin_lead_state ADD COLUMN IF NOT EXISTS linkedin_public_identifier VARCHAR(128)",
    "ALTER TABLE gtm_linkedin_lead_state ADD COLUMN IF NOT EXISTS last_seen_profile_url TEXT",
    "ALTER TABLE gtm_linkedin_lead_state ADD COLUMN IF NOT EXISTS last_seen_name VARCHAR(255)",
    "ALTER TABLE gtm_linkedin_lead_state ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ",
    "ALTER TABLE gtm_linkedin_lead_state ADD COLUMN IF NOT EXISTS linkedin_state_confidence INTEGER",
    # ── Dynamic sequence (plan-as-data) ──────────────────────────────────────
    # `plan` is the per-lead ordered list of remaining touches. It replaces the
    # module-level _ACCEPTED_PIPELINE / _INMAIL_PIPELINE constants, so a lead's
    # cadence can be rewritten in response to a reply. NULL = legacy lead not yet
    # backfilled; the engine falls back to building one from its current step.
    "ALTER TABLE gtm_linkedin_lead_state ADD COLUMN IF NOT EXISTS plan JSONB",
    # Deferral bookkeeping. A job that fails / is skipped reschedules the CURRENT
    # step with backoff instead of leaving the lead active with a NULL
    # next_action_at (which no query would ever pick up again).
    "ALTER TABLE gtm_linkedin_lead_state ADD COLUMN IF NOT EXISTS deferral_count INTEGER DEFAULT 0",
    "ALTER TABLE gtm_linkedin_lead_state ADD COLUMN IF NOT EXISTS last_deferred_at TIMESTAMPTZ",
    "ALTER TABLE gtm_linkedin_lead_state ADD COLUMN IF NOT EXISTS defer_reason VARCHAR(64)",
    # Conversation mode: how many automated replies we've sent on this thread,
    # and why (if at all) the lead was handed to a human.
    "ALTER TABLE gtm_linkedin_lead_state ADD COLUMN IF NOT EXISTS conversation_turns INTEGER DEFAULT 0",
    "ALTER TABLE gtm_linkedin_lead_state ADD COLUMN IF NOT EXISTS flagged_reason VARCHAR(64)",
    "ALTER TABLE gtm_linkedin_lead_state ADD COLUMN IF NOT EXISTS flagged_at TIMESTAMPTZ",
    # Health probe for the stall class of bug: an ACTIVE lead with no due-key is
    # unreachable by dispatch_due. Partial index keeps the sweep query cheap.
    """CREATE INDEX IF NOT EXISTS ix_gtm_li_state_stranded
         ON gtm_linkedin_lead_state (workspace_id)
         WHERE linkedin_sequence_status = 'active' AND next_action_at IS NULL""",
    # Per-account worker execution lock (one account = one worker).
    "ALTER TABLE gtm_linkedin_accounts ADD COLUMN IF NOT EXISTS execution_locked_until TIMESTAMPTZ",
    "ALTER TABLE gtm_linkedin_accounts ADD COLUMN IF NOT EXISTS execution_locked_by VARCHAR(64)",
    # Full persistent browser profile stored encrypted in S3 (profile_store.py).
    "ALTER TABLE gtm_linkedin_accounts ADD COLUMN IF NOT EXISTS profile_s3_key VARCHAR(256)",
    "ALTER TABLE gtm_linkedin_accounts ADD COLUMN IF NOT EXISTS profile_updated_at TIMESTAMPTZ",
    # InMail credits health. Flipped to 'exhausted' when a send hits the
    # LinkedIn upsell — the UI reads this to show a top-up banner.
    "ALTER TABLE gtm_linkedin_accounts ADD COLUMN IF NOT EXISTS linkedin_inmail_credits_status VARCHAR(16) DEFAULT 'ok'",
    "ALTER TABLE gtm_linkedin_accounts ADD COLUMN IF NOT EXISTS linkedin_inmail_credits_flagged_at TIMESTAMPTZ",

    # 5) gtm_linkedin_events (immutable audit / analytics)
    """
    CREATE TABLE IF NOT EXISTS gtm_linkedin_events (
      id BIGSERIAL PRIMARY KEY,
      workspace_id BIGINT NOT NULL,
      lead_id BIGINT,
      campaign_id BIGINT,
      linkedin_account_id BIGINT,
      event_type VARCHAR(48) NOT NULL,
      event_time TIMESTAMPTZ DEFAULT now(),
      metadata JSONB
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_gtm_li_events_ws_type_time ON gtm_linkedin_events (workspace_id, event_type, event_time)",
    # Idempotency anchor: at most one accepted/replied detection per lead.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_gtm_li_events_dedup ON gtm_linkedin_events (lead_id, event_type)
      WHERE event_type IN ('connection_accepted', 'reply_received')
    """,

    # 6) gtm_linkedin_jobs (durable SQS mirror)
    """
    CREATE TABLE IF NOT EXISTS gtm_linkedin_jobs (
      id BIGSERIAL PRIMARY KEY,
      workspace_id BIGINT NOT NULL,
      lead_id BIGINT,
      campaign_id BIGINT,
      linkedin_account_id BIGINT,
      job_type VARCHAR(40) NOT NULL,
      status VARCHAR(16) DEFAULT 'pending',
      scheduled_at TIMESTAMPTZ,
      started_at TIMESTAMPTZ,
      locked_at TIMESTAMPTZ,
      completed_at TIMESTAMPTZ,
      retry_count INT DEFAULT 0,
      error_message TEXT,
      payload_json JSONB,
      execution_token UUID,
      is_executed BOOLEAN DEFAULT false,
      created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_gtm_li_jobs_status ON gtm_linkedin_jobs (status, scheduled_at)",
    # ── Phase 2: interactive cloud-login sessions ────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS gtm_linkedin_login_sessions (
      id BIGSERIAL PRIMARY KEY,
      workspace_id BIGINT NOT NULL,
      linkedin_account_id BIGINT NOT NULL REFERENCES gtm_linkedin_accounts(id) ON DELETE CASCADE,
      status VARCHAR(24) DEFAULT 'starting',
      viewer_url TEXT,
      detail TEXT,
      created_at TIMESTAMPTZ DEFAULT now(),
      updated_at TIMESTAMPTZ DEFAULT now(),
      expires_at TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_gtm_li_login_sessions_acct ON gtm_linkedin_login_sessions (linkedin_account_id)",
]
