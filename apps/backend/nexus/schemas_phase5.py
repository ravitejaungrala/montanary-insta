"""Pydantic schemas for NEXUS Phase 5 (Inbox + Intent + Reply)."""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field


# ---- Intent classification ----

IntentLiteral = Literal[
    "DEMO_SCHEDULED",
    "INTERESTED",
    "QUESTION",
    "NOT_NOW",
    "NOT_INTERESTED",
    "LEFT_COMPANY",
    "OUT_OF_OFFICE",
    "UNSUBSCRIBE",
]


class IntentClassification(BaseModel):
    intent: IntentLiteral
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: Optional[str] = None


# ---- Inbox list / detail ----

class InboundMessageSummary(BaseModel):
    id: int
    thread_id: int
    direction: str
    from_email: Optional[str] = None
    to_email: Optional[str] = None
    subject: Optional[str] = None
    body_preview: Optional[str] = None
    received_at: Optional[datetime] = None
    intent: Optional[str] = None
    intent_confidence: Optional[float] = None
    suggested_reply_preview: Optional[str] = None
    is_read: bool

    class Config:
        from_attributes = True


class InboundMessageFull(BaseModel):
    id: int
    thread_id: int
    direction: str
    from_email: Optional[str] = None
    to_email: Optional[str] = None
    subject: Optional[str] = None
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    message_id_header: Optional[str] = None
    in_reply_to_header: Optional[str] = None
    received_at: Optional[datetime] = None
    intent: Optional[str] = None
    intent_confidence: Optional[float] = None
    intent_reasoning: Optional[str] = None
    suggested_reply: Optional[str] = None
    is_read: bool

    class Config:
        from_attributes = True


class ThreadView(BaseModel):
    id: int
    workspace_id: int
    lead_id: Optional[int] = None
    lead_sequence_id: Optional[int] = None
    subject: Optional[str] = None
    status: str
    message_count: int
    last_message_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    messages: List[InboundMessageFull] = []

    class Config:
        from_attributes = True


class InboxListResponse(BaseModel):
    items: List[InboundMessageSummary]
    total: int


# ---- Reply send ----

class SendReplyRequest(BaseModel):
    body_text: str = Field(min_length=1)
    body_html: Optional[str] = None
    subject_override: Optional[str] = None


class SendReplyResponse(BaseModel):
    message_id: int
    sent: bool
    provider_message_id: Optional[str] = None
    detail: Optional[str] = None


class MarkReadResponse(BaseModel):
    message_id: int
    is_read: bool


# ---- Inbound webhook ingestion ----

class ResendInboundPayload(BaseModel):
    """Normalized shape pulled from Resend's inbound webhook envelope.
    Resend wraps the email in `{ "type": "email.inbound", "data": {...} }`;
    we accept either the envelope or just the `data` object."""

    from_email: Optional[str] = None
    to_email: Optional[str] = None
    subject: Optional[str] = None
    text: Optional[str] = None
    html: Optional[str] = None
    message_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: Optional[str] = None
    headers: Optional[dict] = None


# ---- Unmatched reply admin ----

class UnmatchedReplyOut(BaseModel):
    id: int
    workspace_id: Optional[int] = None
    from_email: Optional[str] = None
    subject: Optional[str] = None
    body_preview: Optional[str] = None
    received_at: Optional[datetime] = None
    processed: bool

    class Config:
        from_attributes = True
