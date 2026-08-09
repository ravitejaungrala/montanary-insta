"""Pydantic v2 schemas for Nexus Phase 4."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------


class SequenceStep(BaseModel):
    step: int
    delay_days: int = 0
    subject_template: str
    body_template: str


class SequenceCreate(BaseModel):
    name: str
    campaign_id: Optional[int] = None
    steps: List[SequenceStep]


class SequenceUpdate(BaseModel):
    name: Optional[str] = None
    campaign_id: Optional[int] = None
    steps: Optional[List[SequenceStep]] = None


class SequenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    campaign_id: Optional[int] = None
    name: str
    steps: Any
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# LeadSequence
# ---------------------------------------------------------------------------


class LeadSequenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    campaign_id: Optional[int] = None
    lead_id: Optional[int] = None
    sequence_id: int
    current_step: int
    next_action_at: Optional[datetime] = None
    status: str
    last_error: Optional[str] = None
    enrolled_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    halt_reason: Optional[str] = None


class EnrollRequest(BaseModel):
    """Single or bulk lead enrollment. Pass `lead_ids` for bulk, `lead_id`
    for single — both shapes are accepted to keep client code simple."""
    lead_id: Optional[int] = None
    lead_ids: Optional[List[int]] = None
    campaign_id: Optional[int] = None
    start_at: Optional[datetime] = None  # default: immediately


class EnrollResult(BaseModel):
    enrolled: int = 0
    skipped_already_active: int = 0
    skipped_suppressed: int = 0
    lead_sequence_ids: List[int] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Touchpoint
# ---------------------------------------------------------------------------


class TouchpointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_sequence_id: int
    step: int
    subject: Optional[str] = None
    body: Optional[str] = None
    sent_at: Optional[datetime] = None
    resend_message_id: Optional[str] = None
    status: str
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Personalization cache
# ---------------------------------------------------------------------------


class PersonalizationCacheOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lead_id: int
    sequence_id: int
    step: int
    subject: Optional[str] = None
    body: Optional[str] = None
    model_used: Optional[str] = None
    created_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Send log
# ---------------------------------------------------------------------------


class SendLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    conversation_account_id: Optional[int] = None
    lead_id: Optional[int] = None
    sent_at: Optional[datetime] = None
    recipient: Optional[str] = None
    status: Optional[str] = None


# ---------------------------------------------------------------------------
# Conversation accounts
# ---------------------------------------------------------------------------


class ConversationAccountCreate(BaseModel):
    email_address: EmailStr
    daily_send_limit: int = 20
    provider: str = "resend"
    refresh_token: Optional[str] = None


class ConversationAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    email_address: str
    daily_send_limit: int
    daily_send_count: int
    last_reset_at: Optional[datetime] = None
    provider: str
    status: str


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------


class SuppressionAdd(BaseModel):
    email: EmailStr
    reason: Optional[str] = None


class SuppressionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    email_lower: str
    reason: Optional[str] = None
    added_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Automations
# ---------------------------------------------------------------------------


class AutomationCreate(BaseModel):
    name: str
    campaign_id: Optional[int] = None
    schedule_type: str = "daily"
    tz: str = "UTC"
    target_leads: int = 100
    next_run_at: Optional[datetime] = None


class AutomationUpdate(BaseModel):
    name: Optional[str] = None
    campaign_id: Optional[int] = None
    schedule_type: Optional[str] = None
    tz: Optional[str] = None
    target_leads: Optional[int] = None
    next_run_at: Optional[datetime] = None
    status: Optional[str] = None


class AutomationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    campaign_id: Optional[int] = None
    name: str
    schedule_type: str
    tz: str
    target_leads: int
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    status: str
    run_count: int = 0
    leads_generated: int = 0
    last_run_status: Optional[str] = None
    last_run_summary: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Tick result schemas
# ---------------------------------------------------------------------------


class TickResult(BaseModel):
    count: int = 0
    sent: int = 0
    skipped: int = 0
    errors: int = 0
    details: List[Dict[str, Any]] = Field(default_factory=list)


class AutomationTickResult(BaseModel):
    count: int = 0
    triggered: int = 0
    errors: int = 0
    details: List[Dict[str, Any]] = Field(default_factory=list)


class ResetResult(BaseModel):
    reset_count: int = 0
