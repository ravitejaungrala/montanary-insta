"""
NEXUS Phase 5 — Inbox + Intent Classification + RAG-grounded Reply models.

These ORM classes use the shared `core.database.Base` so they participate in
the same SQLAlchemy metadata as the rest of the Pipelyt backend, but they
live under the `nexus_` table-name prefix to keep the NEXUS migration
isolated from Pipelyt's own tables.

Cross-phase FKs (LeadSequence from Phase 4, GlobalLead from Phase 3,
NexusWorkspace from Phase 1) are intentionally declared as bare BigInteger
columns; the actual ForeignKey constraints are wired at merge time once the
target tables exist in the same metadata. See the `# TODO: FK ...` comments.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from core.database import Base


class InboundLead(Base):
    """A lead that arrived via an inbound channel (reply to outbound, manual
    capture, form fill). Scoped per workspace; email is unique inside a
    workspace so we don't fan-out duplicate threads for the same person."""

    __tablename__ = "nexus_inbound_leads"
    __table_args__ = (
        UniqueConstraint("workspace_id", "email", name="uq_inbound_lead_ws_email"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, index=True, nullable=False)  # TODO: FK to nexus_workspaces.id wired at merge
    email = Column(String(320), index=True, nullable=False)
    first_name = Column(String(120))
    last_name = Column(String(120))
    source = Column(String(64))  # 'ms_graph', 'manual', etc.
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class InboundThread(Base):
    """A conversation thread. Identifiers from both Gmail and Resend are
    stored so we can match either inbound channel back to the same thread."""

    __tablename__ = "nexus_inbound_threads"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, index=True, nullable=False)  # TODO: FK to nexus_workspaces.id wired at merge
    lead_id = Column(BigInteger, index=True, nullable=True)  # TODO: FK to nexus_global_leads.id wired at merge (Phase 3)
    lead_sequence_id = Column(BigInteger, index=True, nullable=True)  # TODO: FK to nexus_lead_sequences.id wired at merge (Phase 4)
    gmail_thread_id = Column(String(255), index=True)
    resend_thread_id = Column(String(255), index=True)
    subject = Column(String(998))  # RFC 2822 subject line cap
    last_message_at = Column(DateTime(timezone=True), index=True)
    message_count = Column(Integer, default=0, nullable=False)
    status = Column(String(16), default="open", nullable=False)  # 'open' | 'closed'
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class InboundMessage(Base):
    """One message inside a thread. `direction` distinguishes inbound replies
    from outbound sends; intent / confidence / suggested_reply are populated
    asynchronously after classification + RAG generation."""

    __tablename__ = "nexus_inbound_messages"
    __table_args__ = (
        Index("ix_inbound_msg_thread_received", "thread_id", "received_at"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    thread_id = Column(BigInteger, index=True, nullable=False)  # TODO: FK to nexus_inbound_threads.id wired at merge
    direction = Column(String(16), nullable=False)  # 'inbound' | 'outbound'
    from_email = Column(String(320), index=True)
    to_email = Column(String(320))
    subject = Column(String(998))
    body_text = Column(Text)
    body_html = Column(Text)
    message_id_header = Column(String(998), index=True)
    in_reply_to_header = Column(String(998), index=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    intent = Column(String(32), index=True)
    intent_confidence = Column(Float)
    intent_reasoning = Column(Text)
    suggested_reply = Column(Text)
    is_read = Column(Boolean, default=False, nullable=False, index=True)


class Inbox(Base):
    """Per-workspace unread queue. We keep this as a real table (not a VIEW)
    because Lambda cold-starts can't `CREATE VIEW IF NOT EXISTS` portably and
    we want the same write path on Postgres + local SQLite-ish dev."""

    __tablename__ = "nexus_inbox"
    __table_args__ = (
        UniqueConstraint("workspace_id", "message_id", name="uq_inbox_ws_msg"),
        Index("ix_inbox_ws_unread", "workspace_id", "is_unread"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, index=True, nullable=False)  # TODO: FK to nexus_workspaces.id wired at merge
    thread_id = Column(BigInteger, index=True, nullable=False)  # TODO: FK to nexus_inbound_threads.id wired at merge
    message_id = Column(BigInteger, index=True, nullable=False)  # TODO: FK to nexus_inbound_messages.id wired at merge
    is_unread = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UnmatchedReply(Base):
    """Inbound reply we couldn't link to an existing thread/lead. Surfaced in
    the UI so users can manually associate (or discard)."""

    __tablename__ = "nexus_unmatched_replies"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, index=True, nullable=True)  # TODO: FK to nexus_workspaces.id wired at merge
    from_email = Column(String(320), index=True)
    subject = Column(String(998))
    body_text = Column(Text)
    body_html = Column(Text)
    message_id_header = Column(String(998))
    in_reply_to_header = Column(String(998))
    raw_payload = Column(Text)  # JSON-encoded original webhook payload for debugging
    received_at = Column(DateTime(timezone=True), server_default=func.now())
    processed = Column(Boolean, default=False, nullable=False, index=True)


class LinkedInMessage(Base):
    """NEXUS-side LinkedIn DM record. Separate from Pipelyt's outbound
    LinkedIn POST publishing — this is the messaging channel only."""

    __tablename__ = "nexus_linkedin_messages"
    __table_args__ = (
        Index("ix_li_msg_ws_lead", "workspace_id", "lead_id"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(BigInteger, index=True, nullable=False)  # TODO: FK to nexus_workspaces.id wired at merge
    lead_id = Column(BigInteger, index=True)  # TODO: FK to nexus_global_leads.id wired at merge (Phase 3)
    lead_sequence_id = Column(BigInteger, index=True)  # TODO: FK to nexus_lead_sequences.id wired at merge (Phase 4)
    direction = Column(String(16), nullable=False)  # 'inbound' | 'outbound'
    body = Column(Text)
    linkedin_message_urn = Column(String(255), index=True)
    sent_at = Column(DateTime(timezone=True), server_default=func.now())
    intent = Column(String(32))
    intent_confidence = Column(Float)
    # InMail support — variant distinguishes a short DM ('dm', default)
    # from a longer Sales-Nav InMail ('inmail') that carries a subject.
    # Both columns are populated by phase5's ALTER TABLE step; declaring
    # them here lets the ORM auto-load them so any SELECT through
    # db.query(LinkedInMessage) returns the values from the DB.
    variant = Column(String(16), nullable=False, server_default="dm")
    subject = Column(Text)
    # Cadence step for InMail: 0=initial, 1=follow-up 1, 2=follow-up 2,
    # 3=closing. DMs (connection notes) stay at step 0. Added by phase5's
    # ALTER TABLE step; declared here so the ORM auto-loads it.
    step = Column(Integer, nullable=False, server_default="0")
