"""Phase 7 — GTM Journey columns.

Adds columns that the legacy NEXUS GTM Journey UI expects on
`nexus_global_leads` and `nexus_touchpoints`. The Postgres column names
match the Mongo field names 1:1 so a future Mongo->Postgres migration
can copy values without renaming.

Additive only — `IF NOT EXISTS` everywhere.
"""

from __future__ import annotations

MIGRATIONS = [
    # ── nexus_global_leads: GTM Journey fields ─────────────────────────────
    # Display fields the legacy UI reads directly (`lead.name`, `lead.company`,
    # `lead.job_title` etc.). Existing columns first_name/last_name/role/
    # company_name are kept; these are denormalised mirrors for fast reads.
    "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS name VARCHAR(255);",
    "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS company VARCHAR(255);",
    "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS job_title VARCHAR(160);",
    "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS linkedin_url VARCHAR(512);",
    "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS email_verify_status VARCHAR(64);",
    "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS last_contacted_at TIMESTAMP;",

    # Priority bucketing — legacy uses `priority_state`. The existing
    # `priority` column already exists with the same semantics; we add
    # `priority_state` as a separate column so the response shape matches
    # the legacy 1:1 (and the migration tool can copy across unchanged).
    "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS priority_state VARCHAR(16) DEFAULT 'active';",
    "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS hidden_reason VARCHAR(180);",
    "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS hidden_at TIMESTAMP;",

    # Attempt accounting — used by auto-hide rules + Outreach Flow stage
    # detection in the right panel.
    "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS attempt_count_total INTEGER DEFAULT 0;",
    # NOTE: SQLAlchemy's text() parses `:name` as bind parameters, so the
    # JSON literal `{"email":0,...}` blows up with "value required for bind
    # parameter '0'". Use jsonb_build_object() to avoid any colons.
    "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS channel_attempts JSONB DEFAULT jsonb_build_object('email', 0, 'linkedin', 0, 'voice', 0);",
    "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMP;",
    "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS last_attempt_channel VARCHAR(16);",
    "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS total_emails_sent INTEGER DEFAULT 0;",

    # Indexes that the journey queries use heavily.
    "CREATE INDEX IF NOT EXISTS ix_nexus_global_leads_priority_state ON nexus_global_leads (priority_state);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_global_leads_hidden_at ON nexus_global_leads (hidden_at);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_global_leads_last_attempt_at ON nexus_global_leads (last_attempt_at);",

    # ── nexus_touchpoints: legacy fields needed by detail timeline ─────────
    # Today nexus_touchpoints only stores lead_sequence_id; the legacy
    # journey response references touchpoint.lead_id, .campaign_id,
    # .channel, .body_snapshot, .error_msg directly. Adding them
    # denormalised lets the detail query be a simple `SELECT ... WHERE
    # lead_id = :id` instead of a 3-table join (and matches the Mongo
    # Touchpoint shape).
    "ALTER TABLE nexus_touchpoints ADD COLUMN IF NOT EXISTS workspace_id BIGINT;",
    "ALTER TABLE nexus_touchpoints ADD COLUMN IF NOT EXISTS lead_id BIGINT;",
    "ALTER TABLE nexus_touchpoints ADD COLUMN IF NOT EXISTS campaign_id BIGINT;",
    "ALTER TABLE nexus_touchpoints ADD COLUMN IF NOT EXISTS channel VARCHAR(16) DEFAULT 'email';",
    "ALTER TABLE nexus_touchpoints ADD COLUMN IF NOT EXISTS body_snapshot TEXT;",
    "ALTER TABLE nexus_touchpoints ADD COLUMN IF NOT EXISTS error_msg TEXT;",
    "ALTER TABLE nexus_touchpoints ADD COLUMN IF NOT EXISTS user_id BIGINT;",
    "ALTER TABLE nexus_touchpoints ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();",

    "CREATE INDEX IF NOT EXISTS ix_nexus_touchpoints_lead_id ON nexus_touchpoints (lead_id);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_touchpoints_campaign_id ON nexus_touchpoints (campaign_id);",
    "CREATE INDEX IF NOT EXISTS ix_nexus_touchpoints_channel ON nexus_touchpoints (channel);",

    # ── nexus_demo_bookings: legacy references briefing_id directly ────────
    # Legacy DemoBooking.briefing_id points to the briefing row; in
    # Postgres we resolve the other way (briefing has demo_booking_id),
    # but to keep parity for the migration tool we add an optional
    # back-pointer.
    "ALTER TABLE nexus_demo_bookings ADD COLUMN IF NOT EXISTS briefing_id BIGINT;",
]
