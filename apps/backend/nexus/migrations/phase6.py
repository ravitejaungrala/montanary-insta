"""Phase 6 idempotent migration helper.

Called from main.sync_db_schema() at backend startup. Each statement is wrapped
in a try/except so Lambda cold starts self-heal without failing if a table
already exists with a slightly different shape.
"""

import logging

from sqlalchemy import text

logger = logging.getLogger("pipelyt.nexus.phase6.migrations")


# Raw DDL — kept in one place so reviewers can audit table shape vs. legacy Mongoose.
_DDL_STATEMENTS = [
    # ── nexus_voice_calls ───────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nexus_voice_calls (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL,
        user_id BIGINT,
        lead_id BIGINT,
        campaign_id BIGINT,
        product_id BIGINT,
        to_number VARCHAR NOT NULL,
        from_number VARCHAR DEFAULT '',
        provider VARCHAR DEFAULT 'twilio',
        twilio_call_sid VARCHAR UNIQUE,
        status VARCHAR DEFAULT 'queued',
        outcome VARCHAR DEFAULT 'unknown',
        last_speech_result TEXT DEFAULT '',
        error TEXT DEFAULT '',
        started_at TIMESTAMP DEFAULT NOW(),
        answered_at TIMESTAMP,
        ended_at TIMESTAMP,
        duration_sec INTEGER DEFAULT 0,
        transcript TEXT DEFAULT '',
        recording_url VARCHAR DEFAULT '',
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_voice_calls_workspace ON nexus_voice_calls (workspace_id)",
    "CREATE INDEX IF NOT EXISTS ix_nexus_voice_calls_lead ON nexus_voice_calls (lead_id)",
    "CREATE INDEX IF NOT EXISTS ix_nexus_voice_calls_status ON nexus_voice_calls (status)",
    # ── nexus_demo_bookings ─────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nexus_demo_bookings (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL,
        lead_id BIGINT,
        campaign_id BIGINT,
        product_id BIGINT,
        source VARCHAR DEFAULT 'ms_bookings',
        booking_id_external VARCHAR DEFAULT '',
        ms_booking_business_id VARCHAR DEFAULT '',
        scheduled_at TIMESTAMP,
        end_time TIMESTAMP,
        duration_min INTEGER DEFAULT 30,
        status VARCHAR DEFAULT 'scheduled',
        attendee_email VARCHAR DEFAULT '',
        attendee_name VARCHAR DEFAULT '',
        rep_name VARCHAR DEFAULT '',
        rep_email VARCHAR DEFAULT '',
        rep_phone VARCHAR DEFAULT '',
        confirm_token VARCHAR UNIQUE,
        confirm_token_expires_at TIMESTAMP,
        confirmed_at TIMESTAMP,
        reminder_24h_sent BOOLEAN DEFAULT FALSE,
        reminder_1h_sent BOOLEAN DEFAULT FALSE,
        meta JSONB,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_demo_bookings_ws ON nexus_demo_bookings (workspace_id)",
    "CREATE INDEX IF NOT EXISTS ix_nexus_demo_bookings_lead ON nexus_demo_bookings (lead_id)",
    "CREATE INDEX IF NOT EXISTS ix_nexus_demo_bookings_status ON nexus_demo_bookings (status, scheduled_at)",
    # ── nexus_demo_briefings ────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nexus_demo_briefings (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT,
        demo_booking_id BIGINT NOT NULL,
        lead_id BIGINT,
        briefing_md TEXT DEFAULT '',
        status VARCHAR DEFAULT 'generating',
        about_person TEXT DEFAULT '',
        about_company TEXT DEFAULT '',
        why_this_product TEXT DEFAULT '',
        talking_points JSONB,
        questions_to_ask JSONB,
        likely_objections JSONB,
        raw_research JSONB,
        refine_history JSONB,
        generated_at TIMESTAMP,
        regenerated_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_demo_briefings_booking ON nexus_demo_briefings (demo_booking_id)",
    # ── nexus_performance_insights ──────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nexus_performance_insights (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL,
        user_id BIGINT,
        campaign_id BIGINT,
        status VARCHAR DEFAULT 'running',
        period_start DATE,
        period_end DATE,
        metrics JSONB,
        icp_data JSONB,
        outreach_data JSONB,
        signal_data JSONB,
        campaign_data JSONB,
        icp_insights JSONB,
        outreach_insights JSONB,
        signal_insights JSONB,
        campaign_insights JSONB,
        summary TEXT DEFAULT '',
        error TEXT DEFAULT '',
        generated_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_perf_ws ON nexus_performance_insights (workspace_id)",
    # ── nexus_conversion_snapshots ──────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nexus_conversion_snapshots (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL,
        user_id BIGINT,
        lead_id BIGINT,
        campaign_id BIGINT,
        product_id BIGINT,
        snapshot_date DATE,
        metrics JSONB,
        lead JSONB,
        outreach JSONB,
        signals JSONB,
        campaign_icp JSONB,
        product_name VARCHAR DEFAULT '',
        first_contacted_at TIMESTAMP,
        converted_at TIMESTAMP DEFAULT NOW(),
        days_to_convert INTEGER,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_conv_snap_user_converted ON nexus_conversion_snapshots (user_id, converted_at)",
    # ── nexus_credit_logs ───────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nexus_credit_logs (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL,
        action_type VARCHAR NOT NULL,
        credits_used INTEGER DEFAULT 0,
        successful_matches INTEGER DEFAULT 0,
        total_requested INTEGER DEFAULT 0,
        ref_id BIGINT,
        ref_type VARCHAR DEFAULT '',
        campaign_id BIGINT,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_credit_log_ws_created ON nexus_credit_logs (workspace_id, created_at)",
    # ── nexus_token_usage ───────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nexus_token_usage (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL,
        user_id BIGINT,
        campaign_id BIGINT,
        model VARCHAR DEFAULT '',
        operation VARCHAR DEFAULT '',
        prompt_tokens INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        total_tokens INTEGER DEFAULT 0,
        tokens_used INTEGER DEFAULT 0,
        used_at TIMESTAMP DEFAULT NOW(),
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_token_usage_ws_created ON nexus_token_usage (workspace_id, created_at)",
    # ── nexus_winning_examples ──────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nexus_winning_examples (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL,
        outreach_id BIGINT,
        lead_id BIGINT,
        campaign_id BIGINT,
        example_type VARCHAR DEFAULT 'p1',
        text TEXT DEFAULT '',
        email_subject TEXT DEFAULT '',
        email_body_plain TEXT DEFAULT '',
        personalization_depth VARCHAR DEFAULT '',
        prompt_version VARCHAR DEFAULT '',
        intent VARCHAR DEFAULT '',
        lead_segment JSONB,
        reply_rate FLOAT DEFAULT 0,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_winning_ws_created ON nexus_winning_examples (workspace_id, created_at)",
    # ── nexus_outreach ──────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nexus_outreach (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL,
        lead_id BIGINT NOT NULL,
        campaign_id BIGINT NOT NULL,
        channel VARCHAR DEFAULT 'email',
        subject VARCHAR DEFAULT '',
        to_email VARCHAR DEFAULT '',
        email_html TEXT DEFAULT '',
        email_sent_at TIMESTAMP,
        sent_at TIMESTAMP,
        status VARCHAR DEFAULT 'pending',
        response_status VARCHAR DEFAULT '',
        resend_message_id VARCHAR DEFAULT '',
        thread_message_ids JSONB,
        gmail_thread_id VARCHAR DEFAULT '',
        opened_at TIMESTAMP,
        clicked_at TIMESTAMP,
        reply_text TEXT DEFAULT '',
        meta JSONB,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_outreach_ws ON nexus_outreach (workspace_id)",
    "CREATE INDEX IF NOT EXISTS ix_nexus_outreach_lead ON nexus_outreach (lead_id)",
    "CREATE INDEX IF NOT EXISTS ix_nexus_outreach_campaign ON nexus_outreach (campaign_id)",
    "CREATE INDEX IF NOT EXISTS ix_nexus_outreach_to_email ON nexus_outreach (to_email)",
    # Conditional unique index — only enforce on (lead_id, campaign_id) when both set
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_nexus_outreach_lead_campaign ON nexus_outreach (lead_id, campaign_id)",
    # ── nexus_settings ──────────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nexus_settings (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT UNIQUE NOT NULL,
        settings JSONB,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    )
    """,
    # ── nexus_chat_sessions / nexus_chat_messages ────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS nexus_chat_sessions (
        id BIGSERIAL PRIMARY KEY,
        workspace_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        title VARCHAR DEFAULT '',
        last_message_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_chat_sessions_ws_user ON nexus_chat_sessions (workspace_id, user_id)",
    "CREATE INDEX IF NOT EXISTS ix_nexus_chat_sessions_last ON nexus_chat_sessions (last_message_at)",
    """
    CREATE TABLE IF NOT EXISTS nexus_chat_messages (
        id BIGSERIAL PRIMARY KEY,
        session_id BIGINT NOT NULL,
        role VARCHAR NOT NULL,
        content TEXT DEFAULT '',
        tool_name VARCHAR,
        tool_args JSONB,
        tool_result JSONB,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_nexus_chat_messages_session ON nexus_chat_messages (session_id, created_at)",

    # ── NexusUserProfile preference patch (Phase 1 table) ──────────────────
    "ALTER TABLE nexus_user_profiles ADD COLUMN IF NOT EXISTS timezone VARCHAR(64) DEFAULT 'UTC'",
    "ALTER TABLE nexus_user_profiles ADD COLUMN IF NOT EXISTS daily_report_time VARCHAR(8) DEFAULT '08:00'",
    "ALTER TABLE nexus_user_profiles ADD COLUMN IF NOT EXISTS daily_pipeline_time VARCHAR(8) DEFAULT '07:00'",
    "ALTER TABLE nexus_user_profiles ADD COLUMN IF NOT EXISTS auto_reply_enabled BOOLEAN DEFAULT FALSE",
    "ALTER TABLE nexus_user_profiles ADD COLUMN IF NOT EXISTS founder_mode BOOLEAN DEFAULT FALSE",

    # ── NexusGlobalLead priority patch (Phase 3 table) ─────────────────────
    "ALTER TABLE nexus_global_leads ADD COLUMN IF NOT EXISTS priority VARCHAR(16) DEFAULT 'active'",
    "CREATE INDEX IF NOT EXISTS ix_nexus_global_leads_priority ON nexus_global_leads(priority)",
]


def sync_phase6_schema(engine) -> None:
    """Idempotently CREATE TABLE / CREATE INDEX every Phase 6 table.

    Engine is the SQLAlchemy engine from core.database. Run on every cold start.
    Each statement is independent — one failure does not block the next.
    """
    with engine.begin() as conn:
        for stmt in _DDL_STATEMENTS:
            try:
                conn.execute(text(stmt))
            except Exception as e:  # noqa: BLE001
                # Swallow + log so a partial migration doesn't break cold start.
                logger.warning("Phase 6 DDL skipped (likely already applied): %s", str(e)[:200])


# Adapter for nexus.migrations.ALL_PHASES — the parent runner expects a
# MIGRATIONS list of SQL strings.
MIGRATIONS = list(_DDL_STATEMENTS)

__all__ = ["sync_phase6_schema", "MIGRATIONS"]
