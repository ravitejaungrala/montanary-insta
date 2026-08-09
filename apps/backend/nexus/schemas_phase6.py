"""Phase 6 Pydantic schemas."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ─── Voice ───────────────────────────────────────────────────────────────────


class VoiceInitiateIn(BaseModel):
    lead_id: int
    to_number: str
    campaign_id: Optional[int] = None
    product_id: Optional[int] = None
    script: Optional[str] = None


class VoiceCallOut(BaseModel):
    id: int
    workspace_id: int
    lead_id: Optional[int]
    twilio_call_sid: Optional[str]
    status: str
    outcome: str
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    duration_sec: Optional[int]
    transcript: Optional[str]
    recording_url: Optional[str]

    class Config:
        from_attributes = True


# ─── Bookings ────────────────────────────────────────────────────────────────


class MSBookingsSetupIn(BaseModel):
    ms_booking_business_id: str
    rep_name: Optional[str] = ""
    rep_email: Optional[str] = ""
    rep_phone: Optional[str] = ""


class DemoBookingOut(BaseModel):
    id: int
    workspace_id: int
    source: str
    booking_id_external: Optional[str]
    scheduled_at: Optional[datetime]
    end_time: Optional[datetime]
    status: str
    attendee_email: Optional[str]
    attendee_name: Optional[str]

    class Config:
        from_attributes = True


# ─── Analytics ───────────────────────────────────────────────────────────────


class TimeseriesPoint(BaseModel):
    date: str
    count: int


class FunnelStage(BaseModel):
    label: str
    count: int
    conv_from_prev: Optional[float] = None


class AnalyticsSummary(BaseModel):
    sent: int = 0
    opens: int = 0
    opens_unique: int = 0
    clicks: int = 0
    replies: int = 0
    replies_breakdown_by_intent: Dict[str, int] = Field(default_factory=dict)
    demo_bookings: int = 0
    intent_signal_rate: float = 0.0
    demo_book_rate: float = 0.0
    bounce_rate: float = 0.0
    unsubscribe_rate: float = 0.0


class AnalyticsResponse(BaseModel):
    summary: AnalyticsSummary
    funnel: List[FunnelStage] = Field(default_factory=list)
    timeseries: List[TimeseriesPoint] = Field(default_factory=list)
    daily_opens: List[TimeseriesPoint] = Field(default_factory=list)
    daily_clicks: List[TimeseriesPoint] = Field(default_factory=list)
    daily_replies: List[TimeseriesPoint] = Field(default_factory=list)


# ─── Performance ────────────────────────────────────────────────────────────


class PerformanceCampaignRow(BaseModel):
    campaign_id: int
    product_name: str = ""
    total_leads: int = 0
    total_sent: int = 0
    replies: int = 0
    reply_rate: float = 0.0
    demos: int = 0
    demo_rate: float = 0.0
    is_dead: bool = False
    leads_by_status: Dict[str, int] = Field(default_factory=dict)


class PerformanceStatsOut(BaseModel):
    campaigns: List[PerformanceCampaignRow] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
    dead_zones: List[PerformanceCampaignRow] = Field(default_factory=list)
    top_campaigns: List[PerformanceCampaignRow] = Field(default_factory=list)
    snapshots_count: int = 0


# ─── Performance Agent (nexus_performance_insights / nexus_winning_examples) ──
# See /implementation.md — ranks outreach patterns (channel, message variant,
# audience segment, timing) by outcome and surfaces them here.


class RankedSliceOut(BaseModel):
    dimension: str
    slice_value: str
    sends: int
    successes: int = 0
    raw_rate: float = 0.0
    posterior_mean: float = 0.0
    confidence: str = "low"  # 'low' | 'ok'


class ActionItemOut(BaseModel):
    title: str = ""   # short imperative — the bolded lead-in
    detail: str = ""  # the supporting "why", one sentence


class RecommendationsOut(BaseModel):
    """Structured, actionable summary — see /implementation-v2.md §4.
    Stored in nexus_performance_insights.campaign_insights (an existing,
    previously-unused JSONB column from the phase6 schema — no migration)."""

    headline: str = ""
    summary: str = ""  # 2-3 sentence expanded narrative, more context than the headline alone
    quick_wins: List[str] = Field(default_factory=list)  # exactly top 3, short punchy phrases — a condensed TL;DR distinct from `actions`
    actions: List[ActionItemOut] = Field(default_factory=list)  # exactly top 5, by convention
    caveat: str = ""


class PerformanceInsightOut(BaseModel):
    id: int
    status: str  # 'running' | 'ready' | 'failed'
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    summary: str = ""
    recommendations: Optional[RecommendationsOut] = None
    data_source: Optional[str] = None  # 'real' | 'simulated' — from metrics.data_source
    outreach_insights: Optional[Dict[str, List[RankedSliceOut]]] = None  # keyed by metric
    error: str = ""
    generated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class InsightListOut(BaseModel):
    insights: List[PerformanceInsightOut] = Field(default_factory=list)


class DeleteInsightOut(BaseModel):
    deleted: bool = True


class WinningExampleOut(BaseModel):
    example_type: str
    lead_segment: Optional[Dict[str, Any]] = None
    reply_rate: float = 0.0
    email_subject: str = ""
    text: str = ""

    class Config:
        from_attributes = True


class WinningExampleListOut(BaseModel):
    examples: List[WinningExampleOut] = Field(default_factory=list)


class GenerateInsightIn(BaseModel):
    campaign_id: Optional[int] = None
    force_source: Optional[str] = None  # 'real' | 'simulated' — QA/testing override


# ─── Journey ────────────────────────────────────────────────────────────────


class JourneyEvent(BaseModel):
    timestamp: Optional[datetime] = None
    kind: str  # 'email_sent','email_opened','reply','demo_booked','voice_call','signal'
    title: str
    detail: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


class JourneyOut(BaseModel):
    lead_id: int
    lead_email: str = ""
    lead_name: str = ""
    events: List[JourneyEvent] = Field(default_factory=list)


# ─── Tracker ────────────────────────────────────────────────────────────────


class TrackerRow(BaseModel):
    campaign_id: int
    product_name: str = ""
    sent: int = 0
    opened: int = 0
    replied: int = 0
    booked: int = 0
    last_activity: Optional[datetime] = None


class TrackerOut(BaseModel):
    campaigns: List[TrackerRow] = Field(default_factory=list)


# ─── Credits ────────────────────────────────────────────────────────────────


class CreditUsageBreakdown(BaseModel):
    people_enrichment: int = 0
    bulk_enrichment: int = 0
    voice_call: int = 0
    ai_email: int = 0


class CreditUsageOut(BaseModel):
    period: str = "last_30_days"
    total_credits_consumed: int = 0
    total_api_calls: int = 0
    breakdown: CreditUsageBreakdown = Field(default_factory=CreditUsageBreakdown)
    leads_with_email: int = 0
    workspace_id: str = ""


class CreditBalanceOut(BaseModel):
    plan: str = "free"
    plan_allotment: int = 0
    purchased_this_cycle: int = 0
    used_this_cycle: int = 0
    available: int = 0           # -1 == unlimited
    unlimited: bool = False
    cycle_reset_at: Optional[str] = None
    days_until_reset: int = 0
    low_credits: bool = False
    out_of_credits: bool = False


# ─── Chat ───────────────────────────────────────────────────────────────────


class ChatMessage(BaseModel):
    role: str  # 'user','assistant','system'
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]


class ChatResponse(BaseModel):
    message: str


# ─── Dedup ─────────────────────────────────────────────────────────────────


class DedupStatsOut(BaseModel):
    total_global_leads: int = 0
    total_outreach: int = 0
    multi_pitched_leads: int = 0


# ─── Settings ──────────────────────────────────────────────────────────────


class SettingsIn(BaseModel):
    settings: Dict[str, Any] = Field(default_factory=dict)


class SettingsOut(BaseModel):
    id: int
    workspace_id: int
    settings: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True
