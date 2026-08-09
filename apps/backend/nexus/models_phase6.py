"""Phase 6 SQLAlchemy models — Voice, Demo Bookings, Analytics, Credits, Settings, etc.

Cross-phase FK columns are bare BigInteger (no SQL FK constraint) because the
target tables are owned by sibling agents and may not be merged yet. Comments
mark each FK target so a future merge pass can add the ForeignKey clause.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from core.database import Base


# ─── Voice calls ──────────────────────────────────────────────────────────────


class VoiceCall(Base):
    __tablename__ = "nexus_voice_calls"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, nullable=False, index=True)  # TODO: FK to nexus_workspaces.id wired at merge
    user_id = Column(BigInteger, nullable=True)  # TODO: FK to users.id wired at merge
    lead_id = Column(BigInteger, nullable=True, index=True)  # TODO: FK to nexus_global_leads.id wired at merge
    campaign_id = Column(BigInteger, nullable=True, index=True)  # TODO: FK to nexus_campaigns.id wired at merge
    product_id = Column(BigInteger, nullable=True)  # TODO: FK to nexus_products.id wired at merge

    to_number = Column(String, nullable=False)
    from_number = Column(String, default="")
    provider = Column(String, default="twilio")
    twilio_call_sid = Column(String, unique=True, nullable=True, index=True)

    status = Column(String, default="queued", index=True)
    # 'queued','initiated','ringing','in-progress','completed','busy','no-answer','failed','canceled'
    outcome = Column(String, default="unknown", index=True)
    # 'interested','not_interested','question','callback_requested','no_response','unknown'

    last_speech_result = Column(Text, default="")
    error = Column(Text, default="")

    started_at = Column(DateTime, default=datetime.utcnow)
    answered_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    duration_sec = Column(Integer, default=0)

    transcript = Column(Text, default="")
    recording_url = Column(String, default="")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─── Demo bookings ────────────────────────────────────────────────────────────


class DemoBooking(Base):
    __tablename__ = "nexus_demo_bookings"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, nullable=False, index=True)  # TODO: FK to nexus_workspaces.id wired at merge
    lead_id = Column(BigInteger, nullable=True, index=True)  # TODO: FK to nexus_global_leads.id wired at merge
    campaign_id = Column(BigInteger, nullable=True, index=True)  # TODO: FK to nexus_campaigns.id wired at merge
    product_id = Column(BigInteger, nullable=True)  # TODO: FK to nexus_products.id wired at merge

    source = Column(String, default="ms_bookings", index=True)  # 'ms_bookings' | 'calendly'
    booking_id_external = Column(String, default="", index=True)
    ms_booking_business_id = Column(String, default="")

    scheduled_at = Column(DateTime, nullable=True, index=True)
    end_time = Column(DateTime, nullable=True)
    duration_min = Column(Integer, default=30)

    status = Column(String, default="scheduled", index=True)
    # 'pending_rep_confirm','scheduled','confirmed','completed','cancelled','no_show'

    attendee_email = Column(String, default="")
    attendee_name = Column(String, default="")

    rep_name = Column(String, default="")
    rep_email = Column(String, default="")
    rep_phone = Column(String, default="")

    confirm_token = Column(String, unique=True, nullable=True, index=True)
    confirm_token_expires_at = Column(DateTime, nullable=True)
    confirmed_at = Column(DateTime, nullable=True)

    reminder_24h_sent = Column(Boolean, default=False)
    reminder_1h_sent = Column(Boolean, default=False)

    meta = Column(JSONB, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DemoBriefing(Base):
    __tablename__ = "nexus_demo_briefings"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, nullable=True, index=True)
    demo_booking_id = Column(BigInteger, nullable=False, index=True)  # FK to nexus_demo_bookings.id
    lead_id = Column(BigInteger, nullable=True)  # TODO: FK to nexus_global_leads.id wired at merge

    briefing_md = Column(Text, default="")
    status = Column(String, default="generating")  # 'generating','ready','failed'

    about_person = Column(Text, default="")
    about_company = Column(Text, default="")
    why_this_product = Column(Text, default="")
    talking_points = Column(JSONB, nullable=True)
    questions_to_ask = Column(JSONB, nullable=True)
    likely_objections = Column(JSONB, nullable=True)
    raw_research = Column(JSONB, nullable=True)
    refine_history = Column(JSONB, nullable=True)

    generated_at = Column(DateTime, nullable=True)
    regenerated_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MeetingAgenda(Base):
    """Prospect-shareable meeting agenda for a booked demo — generated from the
    lead + company + product context (RAG-grounded). One row per booking.
    Distinct from DemoBriefing (internal rep intel)."""

    __tablename__ = "nexus_meeting_agendas"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, nullable=True, index=True)
    demo_booking_id = Column(BigInteger, nullable=False, index=True)  # FK to nexus_demo_bookings.id
    lead_id = Column(BigInteger, nullable=True)
    product_id = Column(BigInteger, nullable=True)

    agenda_markdown = Column(Text, default="")
    agenda_html = Column(Text, default="")   # rendered for the invite (Phase 2)
    model = Column(String(80), nullable=True)
    source = Column(String(16), default="auto")  # 'auto' | 'manual'
    # Grounding provenance for THIS agenda: {"grounded": bool, "kb_chunks": int,
    # "queries": [...]}. The prospect-delivery gate reads this — an agenda
    # generated with zero retrieved chunks is model-prior text, not grounded,
    # and stays rep-facing.
    grounding = Column(JSONB, nullable=True)

    generated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─── Performance + conversion ─────────────────────────────────────────────────


class PerformanceInsight(Base):
    __tablename__ = "nexus_performance_insights"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(BigInteger, nullable=True)
    campaign_id = Column(BigInteger, nullable=True, index=True)  # TODO: FK to nexus_campaigns.id wired at merge

    status = Column(String, default="running")  # 'running','ready','failed'
    period_start = Column(Date, nullable=True)
    period_end = Column(Date, nullable=True)

    metrics = Column(JSONB, nullable=True)
    icp_data = Column(JSONB, nullable=True)
    outreach_data = Column(JSONB, nullable=True)
    signal_data = Column(JSONB, nullable=True)
    campaign_data = Column(JSONB, nullable=True)

    icp_insights = Column(JSONB, nullable=True)
    outreach_insights = Column(JSONB, nullable=True)
    signal_insights = Column(JSONB, nullable=True)
    campaign_insights = Column(JSONB, nullable=True)

    summary = Column(Text, default="")
    error = Column(Text, default="")

    generated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatSession(Base):
    """Persistent AI-copilot conversation. One row per chat thread.

    Messages are stored separately in `nexus_chat_messages` so we can
    fetch / append efficiently without rewriting a giant JSON blob.
    """
    __tablename__ = "nexus_chat_sessions"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    title = Column(String, default="")
    last_message_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatMessage(Base):
    __tablename__ = "nexus_chat_messages"

    id = Column(BigInteger, primary_key=True, index=True)
    session_id = Column(BigInteger, nullable=False, index=True)
    role = Column(String, nullable=False)  # 'user' | 'assistant' | 'tool'
    content = Column(Text, default="")
    tool_name = Column(String, nullable=True)
    tool_args = Column(JSONB, nullable=True)
    tool_result = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ConversionSnapshot(Base):
    __tablename__ = "nexus_conversion_snapshots"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(BigInteger, nullable=True)
    lead_id = Column(BigInteger, nullable=True, index=True)
    campaign_id = Column(BigInteger, nullable=True)
    product_id = Column(BigInteger, nullable=True)

    snapshot_date = Column(Date, nullable=True, index=True)
    metrics = Column(JSONB, nullable=True)
    lead = Column(JSONB, nullable=True)
    outreach = Column(JSONB, nullable=True)
    signals = Column(JSONB, nullable=True)
    campaign_icp = Column(JSONB, nullable=True)
    product_name = Column(String, default="")

    first_contacted_at = Column(DateTime, nullable=True)
    converted_at = Column(DateTime, default=datetime.utcnow)
    days_to_convert = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_nexus_conv_snap_user_converted", "user_id", "converted_at"),
    )


# ─── Credits + tokens ─────────────────────────────────────────────────────────


class CreditLog(Base):
    __tablename__ = "nexus_credit_logs"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, nullable=False, index=True)

    action_type = Column(String, nullable=False)  # 'people_match','bulk_people_match','voice_call','ai_email',...
    credits_used = Column(Integer, default=0)
    successful_matches = Column(Integer, default=0)
    total_requested = Column(Integer, default=0)

    ref_id = Column(BigInteger, nullable=True)
    ref_type = Column(String, default="")

    campaign_id = Column(BigInteger, nullable=True)  # TODO: FK to nexus_campaigns.id wired at merge

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_nexus_credit_log_ws_created", "workspace_id", "created_at"),
    )


class UserCredits(Base):
    """Per-user (plan-owner) credit balance — the single source of truth.

    One row per FUNDING user (the plan owner), shared across all their
    workspaces. Mutated atomically (row-locked, one transaction) on exactly
    three paths: consume (each reveal), purchase (top-up), and the lazy monthly
    reset. Mirrors Apollo — the monthly plan allotment AND purchased top-ups
    reset each billing cycle (no rollover).
    """

    __tablename__ = "nexus_user_credits"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(BigInteger, nullable=False, unique=True, index=True)  # FK users.id (plan owner)

    plan = Column(String, default="free")               # snapshot at last allotment
    plan_allotment = Column(Integer, default=0)         # this cycle's grant (-1 = unlimited)
    purchased_this_cycle = Column(Integer, default=0)   # top-ups this cycle (also reset)
    consumed_this_cycle = Column(Integer, default=0)    # credits spent this cycle
    enterprise_custom_allotment = Column(Integer, nullable=True)  # admin override; NULL = use map

    cycle_anchor = Column(DateTime, default=datetime.utcnow)   # subscription_end_date else created_at
    last_reset_at = Column(DateTime, default=datetime.utcnow)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CreditGrant(Base):
    """Idempotency ledger for one-time credit PURCHASES. One row per Stripe
    checkout session that granted credits — UNIQUE(stripe_session_id) guarantees
    a session can only add credits once, even if the webhook and the success-page
    confirm both fire."""

    __tablename__ = "nexus_credit_grants"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    stripe_session_id = Column(String, nullable=False, unique=True, index=True)
    credits = Column(Integer, default=0)
    amount_cents = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class TokenUsage(Base):
    __tablename__ = "nexus_token_usage"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(BigInteger, nullable=True)
    campaign_id = Column(BigInteger, nullable=True)

    model = Column(String, default="")
    operation = Column(String, default="")
    # 'product_analysis','email_generation','reply_classification','suggested_reply','polish_pass','lead_generation'

    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    tokens_used = Column(Integer, default=0)  # legacy alias

    used_at = Column(DateTime, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_nexus_token_usage_ws_created", "workspace_id", "created_at"),
    )


# ─── Winning examples ─────────────────────────────────────────────────────────


class WinningExample(Base):
    __tablename__ = "nexus_winning_examples"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, nullable=False, index=True)
    outreach_id = Column(BigInteger, nullable=True)
    lead_id = Column(BigInteger, nullable=True)
    campaign_id = Column(BigInteger, nullable=True)

    example_type = Column(String, default="p1")  # 'subject','p1','p2','p3'
    text = Column(Text, default="")

    email_subject = Column(Text, default="")
    email_body_plain = Column(Text, default="")

    personalization_depth = Column(String, default="")
    prompt_version = Column(String, default="")
    intent = Column(String, default="")  # 'INTERESTED','DEMO_SCHEDULED'

    lead_segment = Column(JSONB, nullable=True)

    reply_rate = Column(Float, default=0.0)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ─── Outreach ─────────────────────────────────────────────────────────────────


class Outreach(Base):
    __tablename__ = "nexus_outreach"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, nullable=False, index=True)
    lead_id = Column(BigInteger, nullable=False, index=True)  # TODO: FK to nexus_global_leads.id wired at merge
    campaign_id = Column(BigInteger, nullable=False, index=True)  # TODO: FK to nexus_campaigns.id wired at merge

    channel = Column(String, default="email")  # 'email','voice','linkedin'
    subject = Column(String, default="")
    to_email = Column(String, default="", index=True)
    email_html = Column(Text, default="")
    email_sent_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)

    status = Column(String, default="pending")
    # 'pending','sent','opened','clicked','replied','demo_scheduled','bounced','unsubscribed'
    response_status = Column(String, default="")

    resend_message_id = Column(String, default="")
    thread_message_ids = Column(JSONB, nullable=True)
    gmail_thread_id = Column(String, default="")

    opened_at = Column(DateTime, nullable=True)
    clicked_at = Column(DateTime, nullable=True)
    reply_text = Column(Text, default="")

    meta = Column(JSONB, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("lead_id", "campaign_id", name="uq_nexus_outreach_lead_campaign"),
        Index("ix_nexus_outreach_campaign", "campaign_id"),
    )


# ─── Settings ─────────────────────────────────────────────────────────────────


class Settings(Base):
    __tablename__ = "nexus_settings"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, unique=True, nullable=False, index=True)

    settings = Column(JSONB, nullable=True)
    # Loose JSON bag: resend_api_key, hunter_api_key, apollo_api_key,
    # mjml_app_id, mjml_secret_key, pexels_api_key, booking_link,
    # sender_name, sender_email, inbound_email, backend_url,
    # rep_name, rep_email, rep_phone,
    # ms_booking_business_id, ms_graph_subscription_id,
    # ms_subscription_expires_at, calendly_webhook_signing_key

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
