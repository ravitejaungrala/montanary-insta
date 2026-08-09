"""Nexus Phase 4 SQLAlchemy models.

Sequence engine, personalization, send infrastructure, suppression,
and automation scheduling.

Cross-phase foreign keys (campaign_id, lead_id) are declared as bare
BigInteger columns. The parent merger wires real ForeignKey constraints
at merge time once Phase 3 tables (nexus_campaigns, nexus_global_leads)
are available.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from core.database import Base


class NexusSequence(Base):
    __tablename__ = "nexus_sequences"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id = Column(
        BigInteger,
        ForeignKey("nexus_workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # TODO: FK to nexus_campaigns.id (Phase 3) wired at merge
    campaign_id = Column(BigInteger, nullable=True, index=True)
    name = Column(String, nullable=False)
    # steps shape: [{step: 0, delay_days: 0, subject_template: "...", body_template: "..."}]
    steps = Column(JSON, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class NexusLeadSequence(Base):
    __tablename__ = "nexus_lead_sequences"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id = Column(
        BigInteger,
        ForeignKey("nexus_workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # TODO: FK to nexus_campaigns.id (Phase 3) wired at merge
    campaign_id = Column(BigInteger, nullable=True, index=True)
    # TODO: FK to nexus_global_leads.id (Phase 3) wired at merge
    lead_id = Column(BigInteger, nullable=True, index=True)
    sequence_id = Column(
        BigInteger,
        ForeignKey("nexus_sequences.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    current_step = Column(Integer, default=0)
    next_action_at = Column(DateTime, nullable=True)
    # active | paused | replied | dead | completed | halted
    status = Column(String, default="active")
    last_error = Column(Text, nullable=True)
    # 2026-06-04 — Sticky sending mailbox for this lead. Set on the FIRST
    # send (via rotation across the workspace's connected mailboxes) and
    # reused for every follow-up / close-up so the whole thread comes from
    # ONE address (deliverability + replies stay on the same mailbox).
    conversation_account_id = Column(BigInteger, nullable=True, index=True)
    enrolled_at = Column(DateTime, server_default=func.now())
    # Fixed cadence, computed once at enrollment (email_flow_plan_3.md §3a):
    # {"0": iso_str, "1": iso_str, ...}, keyed by step order as a string.
    # next_action_at is sourced from this after a send rather than being
    # recomputed as now + delay_days, so a late send no longer compounds
    # delay into every later step.
    step_schedule = Column(JSON, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    # Concurrent-tick guard: a tick claims a row by setting locked_until to
    # now+5min; another tick refuses to pick up a row whose lock has not yet
    # expired. Cleared after the row is processed (success or fail).
    locked_until = Column(DateTime, nullable=True)
    # Set by the inbox / bounce / unsub flows so a halted row is never re-enrolled
    # without an explicit operator action. Distinguishes from 'dead' (config error).
    halt_reason = Column(String, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    # 2026-07-15 — gates the FIRST send for a referral lead created from a
    # reply that named a different point of contact (see sequencer.py
    # referral-to-lead flow). Not a general-purpose flag.
    needs_review = Column(Boolean, default=False, nullable=True)
    needs_review_reason = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_nexus_lead_sequences_due", "status", "next_action_at"),
        Index("ix_nexus_lead_sequences_ws", "workspace_id", "status"),
        Index("ix_nexus_lead_sequences_lock", "locked_until"),
    )


class NexusSenderBrand(Base):
    """A sender 'card' on the Connectors page — a website + entity type the
    user wants to send for. Keyed by the NORMALIZED url (same dedup New Run
    uses for products), so all duplicate product rows for one site collapse
    to a single card. Mailboxes attach to a brand; a campaign matches by its
    product's normalized URL → brand → that brand's mailboxes.
    """

    __tablename__ = "nexus_sender_brands"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id = Column(BigInteger, nullable=False, index=True)
    name = Column(String, nullable=True)           # display name (from URL/scrape)
    url = Column(Text, nullable=True)              # raw URL the user entered
    url_norm = Column(String, nullable=True, index=True)  # normalized match key
    entity_type = Column(String, default="product")  # product | service | gcc
    booking_url = Column(Text, nullable=True)      # per-brand demo booking link (MS Bookings/Google/Calendly)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("workspace_id", "url_norm", "entity_type", name="uq_sender_brand"),
        Index("ix_nexus_sender_brands_ws", "workspace_id"),
    )


class NexusLeadEmail(Base):
    """The AI-written outreach emails for ONE lead's cadence.

    Generated ONCE (on the lead's first send) — one row per cadence step
    (0=initial, 1=follow-up 1, 2=follow-up 2, 3=close-up) — then the
    sequencer reads the row for the step that's due, renders it through the
    HTML template, and sends it. This replaces re-calling Gemini on every
    tick and keeps the four emails consistent + auditable.
    """

    __tablename__ = "nexus_lead_emails"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id = Column(BigInteger, nullable=False, index=True)
    campaign_id = Column(BigInteger, nullable=True, index=True)
    lead_id = Column(BigInteger, nullable=True, index=True)
    lead_sequence_id = Column(
        BigInteger,
        ForeignKey("nexus_lead_sequences.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step = Column(Integer, nullable=False)  # 0=initial,1=fu1,2=fu2,3=closing
    kind = Column(String, nullable=True)    # initial | followup_1 | followup_2 | closing
    subject = Column(Text, nullable=True)
    body = Column(Text, nullable=True)
    # Proof/case-study line — only populated for the initial email (step 0).
    real_result = Column(Text, nullable=True)
    # 2026-06-05 — per-lead personalized opener (1-2 sentences referencing the
    # lead's company/role, grounded in real scraped facts). Rendered as the
    # first line of the rich initial email. Only set on step 0.
    opener = Column(Text, nullable=True)
    # Which role variant was used for the rich render (audit / stability).
    variant_key = Column(String(80), nullable=True)
    status = Column(String, default="pending")  # pending | sent | skipped
    sent_at = Column(DateTime, nullable=True)
    provider_message_id = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    # 2026-07-15 — reply-aware regeneration provenance. The most recent
    # nexus_inbound_messages.id this step's subject/body was generated from.
    # Compared against the thread's latest inbound message (not treated as a
    # one-time flag) so a step can be regenerated again if a newer reply
    # arrives before it sends. NULL = never regenerated (still the original
    # enrollment-time draft).
    regenerated_from_message_id = Column(BigInteger, nullable=True)
    regenerated_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("lead_sequence_id", "step", name="uq_lead_email_seq_step"),
        Index("ix_nexus_lead_emails_seq_step", "lead_sequence_id", "step"),
    )


class NexusTouchpoint(Base):
    __tablename__ = "nexus_touchpoints"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    lead_sequence_id = Column(
        BigInteger,
        ForeignKey("nexus_lead_sequences.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step = Column(Integer, nullable=False)
    subject = Column(Text, nullable=True)
    body = Column(Text, nullable=True)
    sent_at = Column(DateTime, server_default=func.now())
    resend_message_id = Column(String, nullable=True)
    # sent | failed | bounced | opened | clicked
    status = Column(String, default="sent")
    error = Column(Text, nullable=True)
    # Columns added by later migrations but missing from this declarative
    # model. Without them the constructor rejects these kwargs even though
    # the DB has the columns. Sequencer needs to set workspace_id+lead_id+
    # campaign_id explicitly so analytics + journey queries can find rows.
    workspace_id = Column(BigInteger, nullable=True, index=True)
    lead_id = Column(BigInteger, nullable=True, index=True)
    campaign_id = Column(BigInteger, nullable=True, index=True)
    channel = Column(String, nullable=True)
    opens_count = Column(Integer, nullable=True, default=0)
    last_opened_at = Column(DateTime, nullable=True)
    clicks_count = Column(Integer, nullable=True, default=0)
    last_clicked_at = Column(DateTime, nullable=True)
    body_snapshot = Column(Text, nullable=True)
    error_msg = Column(Text, nullable=True)
    user_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class NexusPersonalizationCache(Base):
    __tablename__ = "nexus_personalization_cache"

    # Composite PK: (lead_id, sequence_id, step)
    lead_id = Column(BigInteger, nullable=False)
    sequence_id = Column(BigInteger, nullable=False)
    step = Column(Integer, nullable=False)
    subject = Column(Text, nullable=True)
    body = Column(Text, nullable=True)
    model_used = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        PrimaryKeyConstraint(
            "lead_id", "sequence_id", "step", name="pk_nexus_personalization_cache"
        ),
    )


class NexusSendLog(Base):
    __tablename__ = "nexus_send_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id = Column(
        BigInteger,
        ForeignKey("nexus_workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # TODO: FK to nexus_conversation_accounts.id wired at merge (same phase, but
    # left bare so cross-phase merge stays uniform).
    conversation_account_id = Column(BigInteger, nullable=True, index=True)
    # TODO: FK to nexus_global_leads.id (Phase 3) wired at merge
    lead_id = Column(BigInteger, nullable=True, index=True)
    sent_at = Column(DateTime, server_default=func.now())
    recipient = Column(String, nullable=True)
    status = Column(String, nullable=True)


class NexusConversationAccount(Base):
    __tablename__ = "nexus_conversation_accounts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id = Column(
        BigInteger,
        ForeignKey("nexus_workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email_address = Column(String, unique=True, nullable=False)
    daily_send_limit = Column(Integer, default=20)
    daily_send_count = Column(Integer, default=0)
    last_reset_at = Column(DateTime, server_default=func.now())
    # resend | gmail | outlook
    provider = Column(String, default="resend")
    refresh_token = Column(Text, nullable=True)
    status = Column(String, default="active")
    # ── OAuth mailbox connector fields (2026-06-04) ──────────────────────
    # Populated when a user connects their own Outlook/Gmail mailbox via
    # OAuth (provider='outlook'|'gmail'). `access_token` is short-lived and
    # refreshed from `refresh_token` on demand; `token_expires_at` lets the
    # send path skip a refresh when the cached token is still valid.
    # `tenant_id` is the Microsoft tenant the mailbox belongs to (external
    # customers each have their own). `display_name` is the human label
    # shown in the Connectors UI.
    access_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime, nullable=True)
    tenant_id = Column(String, nullable=True)
    display_name = Column(String, nullable=True)
    # 2026-06-04 — The sender BRAND CARD this mailbox belongs to (keyed by
    # normalized website URL + entity type, mirroring how New Run dedups a
    # product). Rotation matches the campaign's product URL → brand → these
    # mailboxes. Supersedes product_id for matching.
    brand_id = Column(BigInteger, nullable=True, index=True)


class NexusSuppressionList(Base):
    __tablename__ = "nexus_suppression_list"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id = Column(
        BigInteger,
        ForeignKey("nexus_workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email_lower = Column(String, nullable=False)
    reason = Column(String, nullable=True)
    added_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "email_lower", name="uq_nexus_suppression_ws_email"
        ),
    )


class NexusAutomation(Base):
    __tablename__ = "nexus_automations"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id = Column(
        BigInteger,
        ForeignKey("nexus_workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # TODO: FK to nexus_campaigns.id (Phase 3) wired at merge
    campaign_id = Column(BigInteger, nullable=True, index=True)
    name = Column(String, nullable=False)
    # daily | one_time
    schedule_type = Column(String, default="daily")
    tz = Column(String, default="UTC")
    target_leads = Column(Integer, default=100)
    next_run_at = Column(DateTime, nullable=True, index=True)
    last_run_at = Column(DateTime, nullable=True)
    status = Column(String, default="active")
    # Run accounting — populated by automation_runner each tick
    run_count = Column(Integer, default=0)
    leads_generated = Column(Integer, default=0)
    last_run_status = Column(String, nullable=True)  # ok | failed | skipped
    last_run_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
