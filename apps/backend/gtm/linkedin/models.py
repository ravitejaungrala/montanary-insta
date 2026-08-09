"""GTM LinkedIn Agent — ORM models for the gtm_linkedin_* tables.

Uses the shared `core.database.Base` so these participate in the same
SQLAlchemy metadata as the rest of the backend. Cross-subsystem references
(nexus_workspaces, nexus_products, nexus_global_leads, nexus_campaigns) are
declared as bare BigInteger columns — the actual DDL + indexes live in
`gtm/linkedin/migrations.py` (the source of truth). Keep the two in lockstep so
SELECTs through these models load every column.

Naming: tables are `gtm_linkedin_*`; LinkedIn-specific columns are
`linkedin_*` (per the approved plan).
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from core.database import Base


class GtmLinkedInAccount(Base):
    """A LinkedIn login connected ONCE as an independent resource. Assigned to
    sender brands many-to-many via GtmLinkedInAccountBrand."""

    __tablename__ = "gtm_linkedin_accounts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "linkedin_email", name="uq_gtm_li_acct_ws_email"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, index=True, nullable=False)  # FK nexus_workspaces.id (merge-time)
    user_id = Column(BigInteger)  # FK users.id (merge-time)
    linkedin_email = Column(String(255), nullable=False)
    linkedin_display_name = Column(String(255))
    linkedin_account_type = Column(String(32), server_default="personal")  # personal | sales_navigator
    encrypted_cookies = Column(Text)  # Fernet blob of context.cookies() JSON
    # Full persistent browser profile (user_data_dir) stored encrypted in S3 —
    # reused on every run so the account stays "already logged in". See profile_store.py.
    profile_s3_key = Column(String(256))
    profile_updated_at = Column(DateTime(timezone=True))
    linkedin_session_status = Column(String(24), server_default="pending")  # pending|active|challenge|expired|disconnected
    linkedin_reauth_required = Column(Boolean, server_default="false")
    # InMail credit health. Flipped to 'exhausted' the first time a send
    # returns no_credit (the browser saw an upsell instead of a composer).
    # UI reads this to show a "Credits exhausted — upgrade or top up" banner.
    # Reset back to 'ok' on any successful InMail send.
    linkedin_inmail_credits_status = Column(String(16), server_default="ok")  # ok|exhausted
    linkedin_inmail_credits_flagged_at = Column(DateTime(timezone=True))
    is_paused = Column(Boolean, server_default="false")  # MUST-HAVE manual kill-switch
    cooldown_until = Column(DateTime(timezone=True))     # MUST-HAVE challenge/rate-limit rest
    # Per-account worker execution lock: one LinkedIn account = one active worker
    # at a time (browser sessions per account MUST be serial). TTL-bounded so a
    # crashed worker can't hold it forever.
    execution_locked_until = Column(DateTime(timezone=True))
    execution_locked_by = Column(String(64))
    last_successful_login = Column(DateTime(timezone=True))
    last_successful_check = Column(DateTime(timezone=True))
    last_successful_action_at = Column(DateTime(timezone=True))  # account-health signal
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class GtmLinkedInAccountBrand(Base):
    """M:N — which sender BRANDS this LinkedIn account may send for. Brand-keyed
    (not product-keyed) to MATCH the email engine, where mailboxes bind to
    `brand_id`: products that share a website collapse to one `nexus_sender_brands`
    card, so email + LinkedIn pick senders the same way."""

    __tablename__ = "gtm_linkedin_account_brands"
    __table_args__ = (
        UniqueConstraint("linkedin_account_id", "brand_id", name="uq_gtm_li_acct_brand"),
        Index("ix_gtm_li_acct_brand_brand", "brand_id"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    linkedin_account_id = Column(BigInteger, index=True, nullable=False)  # FK gtm_linkedin_accounts.id
    brand_id = Column(BigInteger, nullable=False)  # FK nexus_sender_brands.id (merge-time)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GtmLinkedInAccountSettings(Base):
    """Anti-detection knobs + live per-type daily counters (one row per account)."""

    __tablename__ = "gtm_linkedin_account_settings"

    id = Column(BigInteger, primary_key=True, index=True)
    linkedin_account_id = Column(BigInteger, unique=True, nullable=False)  # FK gtm_linkedin_accounts.id
    daily_connection_limit = Column(Integer, server_default="20")
    daily_message_limit = Column(Integer, server_default="20")
    daily_inmail_limit = Column(Integer, server_default="20")
    working_start_time = Column(String(8), server_default="07:00")  # account-local HH:MM
    working_end_time = Column(String(8), server_default="22:00")
    timezone = Column(String(64), server_default="Asia/Kolkata")
    random_delay_min_seconds = Column(Integer, server_default="3")
    random_delay_max_seconds = Column(Integer, server_default="12")
    daily_connection_count = Column(Integer, server_default="0")
    daily_message_count = Column(Integer, server_default="0")
    daily_inmail_count = Column(Integer, server_default="0")
    last_reset_at = Column(DateTime(timezone=True))


class GtmLinkedInBrowserProfile(Base):
    """Consistent device fingerprint per account (MUST-HAVE) — reused by every
    Playwright BrowserContext so the fingerprint doesn't drift."""

    __tablename__ = "gtm_linkedin_browser_profiles"

    id = Column(BigInteger, primary_key=True, index=True)
    linkedin_account_id = Column(BigInteger, unique=True, nullable=False)  # FK gtm_linkedin_accounts.id
    user_agent = Column(String(512))
    viewport = Column(String(16), server_default="1366x768")
    browser_timezone = Column(String(64), server_default="Asia/Kolkata")
    browser_locale = Column(String(16), server_default="en-US")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GtmLinkedInLeadState(Base):
    """Current position of one lead in the LinkedIn sequence (fast reads)."""

    __tablename__ = "gtm_linkedin_lead_state"
    __table_args__ = (
        UniqueConstraint("lead_id", "campaign_id", name="uq_gtm_li_state_lead_campaign"),
        Index("ix_gtm_li_state_due", "linkedin_sequence_status", "next_action_at"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, index=True, nullable=False)
    lead_id = Column(BigInteger, nullable=False)       # nexus_global_leads.id
    campaign_id = Column(BigInteger, nullable=False)   # nexus_campaigns.id
    linkedin_account_id = Column(BigInteger)           # FK gtm_linkedin_accounts.id
    linkedin_profile_url = Column(Text)
    linkedin_member_id = Column(String(128))           # stable id captured on 1st profile visit
    # ── Identity resilience (URLs change / redirect) ──
    linkedin_public_identifier = Column(String(128))   # URL slug, e.g. 'john-smith-tech' — captured at enroll
    last_seen_profile_url = Column(Text)               # final URL after any redirect (self-heals stale URLs)
    last_seen_name = Column(String(255))               # display name read off the profile on the last visit
    last_verified_at = Column(DateTime(timezone=True)) # when we last confirmed the profile live
    linkedin_connection_status = Column(String(16), server_default="none")    # none|pending|accepted|declined
    linkedin_state_confidence = Column(Integer)        # 0-100 confidence of the last detect_profile_state read
    linkedin_message_reply_status = Column(String(16), server_default="none") # none|replied
    linkedin_inmail_status = Column(String(24), server_default="none")        # none|sent|skipped_no_credit
    linkedin_sequence_status = Column(String(16), server_default="active")    # active|paused|stopped|completed|failed
    linkedin_current_step = Column(String(32))         # connection_request|message_1|inmail_1|...
    linkedin_current_branch = Column(String(24))       # pending_acceptance|accepted|inmail
    workflow_mode = Column(String(16), server_default="standard")  # standard|hybrid
    next_action_at = Column(DateTime(timezone=True))   # randomized slot (scheduler due-key)
    locked_until = Column(DateTime(timezone=True))     # concurrency guard
    linkedin_connection_sent_at = Column(DateTime(timezone=True))
    linkedin_connection_accepted_at = Column(DateTime(timezone=True))
    linkedin_message_sent_at = Column(DateTime(timezone=True))
    linkedin_inmail_sent_at = Column(DateTime(timezone=True))
    last_message_at = Column(DateTime(timezone=True))
    last_reply_at = Column(DateTime(timezone=True))
    paused_reason = Column(String(48))                 # session_expired|...
    # Ordered list of REMAINING touches for this lead — the per-lead cadence that
    # replaces the module-level pipeline constants. See gtm/linkedin/plan.py for
    # the entry shape. NULL on legacy rows (engine backfills on first use).
    plan = Column(JSONB)
    # Deferral bookkeeping: a failed/skipped job reschedules the current step with
    # backoff rather than leaving the lead active with next_action_at = NULL.
    deferral_count = Column(Integer, server_default="0")
    last_deferred_at = Column(DateTime(timezone=True))
    defer_reason = Column(String(64))
    # Conversation mode: automated replies sent on this thread, and human handoff.
    conversation_turns = Column(Integer, server_default="0")
    flagged_reason = Column(String(64))
    flagged_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class GtmLinkedInEvent(Base):
    """Immutable append-only audit / analytics log. Every metric is a COUNT
    over this table; also the idempotency anchor + state-rebuild source."""

    __tablename__ = "gtm_linkedin_events"
    __table_args__ = (
        Index("ix_gtm_li_events_ws_type_time", "workspace_id", "event_type", "event_time"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, nullable=False)
    lead_id = Column(BigInteger)
    campaign_id = Column(BigInteger)
    linkedin_account_id = Column(BigInteger)
    event_type = Column(String(48), nullable=False)    # connection_sent|connection_accepted|...
    event_time = Column(DateTime(timezone=True), server_default=func.now())
    event_metadata = Column("metadata", JSONB)         # py attr renamed; DB column stays `metadata`


class GtmLinkedInJob(Base):
    """Durable RDS mirror of the SQS work queue (retries/DLQ queryable)."""

    __tablename__ = "gtm_linkedin_jobs"
    __table_args__ = (
        Index("ix_gtm_li_jobs_status", "status", "scheduled_at"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, nullable=False)
    lead_id = Column(BigInteger)
    campaign_id = Column(BigInteger)
    linkedin_account_id = Column(BigInteger)
    job_type = Column(String(40), nullable=False)      # send_connection_request|send_message|...
    status = Column(String(16), server_default="pending")  # pending|running|completed|failed|cancelled|dead
    scheduled_at = Column(DateTime(timezone=True))
    started_at = Column(DateTime(timezone=True))
    locked_at = Column(DateTime(timezone=True))        # set when a worker claims it (stuck-job recovery)
    completed_at = Column(DateTime(timezone=True))
    retry_count = Column(Integer, server_default="0")
    error_message = Column(Text)
    payload_json = Column(JSONB)                        # content body, note, subject, (login pwd encrypted)
    execution_token = Column(UUID(as_uuid=False))       # MUST-HAVE idempotency
    is_executed = Column(Boolean, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GtmLinkedInLoginSession(Base):
    """One interactive cloud-login attempt (Phase 2). A browser runs on a
    persistent host (Fargate) that the user drives via a live view to log into
    LinkedIn; on success the full profile is saved (profile_store) and the account
    marked active. This row lets the web app poll progress + fetch the viewer URL."""

    __tablename__ = "gtm_linkedin_login_sessions"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, nullable=False)
    linkedin_account_id = Column(BigInteger, nullable=False)
    # starting | awaiting_login | logged_in | saved | failed | expired
    status = Column(String(24), server_default="starting")
    viewer_url = Column(Text)   # noVNC/live-view URL the user opens (set by the browser host)
    detail = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True))
