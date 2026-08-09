"""Pydantic schemas for NEXUS Phase 3 — Lead Discovery, Enrichment, Campaigns."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---------------------------------------------------------------------------
# ICP — embedded JSON shape carried by campaigns and discovery requests
# ---------------------------------------------------------------------------
class ICPProfile(BaseModel):
    industry: Optional[List[str]] = None
    roles: Optional[List[str]] = None
    seniority: Optional[List[str]] = None  # e.g. ["VP", "Director", "Head"]
    company_size: Optional[List[str]] = None  # e.g. ["1-10", "11-50", "51-200"]
    geographies: Optional[List[str]] = None
    pain_points: Optional[List[str]] = None
    tech_stack: Optional[List[str]] = None
    keywords: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Global / workspace leads
# ---------------------------------------------------------------------------
class GlobalLeadBase(BaseModel):
    email: EmailStr
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role: Optional[str] = None
    company_domain: Optional[str] = None
    company_name: Optional[str] = None
    source: Optional[str] = None
    linkedin_url: Optional[str] = None


class GlobalLeadOut(GlobalLeadBase):
    id: int
    status: str
    email_verified: bool
    email_verify_score: int
    created_at: datetime

    class Config:
        from_attributes = True


class LeadBase(BaseModel):
    campaign_id: Optional[int] = None
    product_id: Optional[int] = None
    icp_score: int = 0
    signals: Optional[Dict[str, Any]] = None


class LeadCreate(LeadBase):
    # Allow either an existing global_lead_id or a fresh GlobalLead payload.
    global_lead_id: Optional[int] = None
    global_lead: Optional[GlobalLeadBase] = None

    @field_validator("global_lead")
    @classmethod
    def _need_one_global(cls, v, info):
        if v is None and not info.data.get("global_lead_id"):
            raise ValueError("Provide either global_lead_id or global_lead payload")
        return v


class LeadBulkCreate(BaseModel):
    campaign_id: Optional[int] = None
    product_id: Optional[int] = None
    leads: List[LeadCreate]


class LeadUpdate(BaseModel):
    campaign_id: Optional[int] = None
    icp_score: Optional[int] = None
    signals: Optional[Dict[str, Any]] = None
    status: Optional[
        Literal["new", "contacted", "replied", "unsubscribed", "bounced"]
    ] = None


class LeadOut(BaseModel):
    id: int
    workspace_id: int
    campaign_id: Optional[int]
    product_id: Optional[int]
    global_lead_id: int
    icp_score: int
    signals: Optional[Dict[str, Any]] = None
    created_at: datetime
    global_lead: Optional[GlobalLeadOut] = None

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Discovery (autonomous + accurate)
# ---------------------------------------------------------------------------
DiscoverySource = Literal[
    "apollo", "hunter", "linkedin", "reddit", "github", "website"
]


class AutonomousDiscoveryIn(BaseModel):
    campaign_id: Optional[int] = None
    product_id: Optional[int] = None
    icp: ICPProfile
    target_domains: Optional[List[str]] = None
    max_leads: int = Field(50, ge=1, le=500)
    min_icp_score: int = Field(40, ge=0, le=100)
    sources: Optional[List[DiscoverySource]] = None  # default: all six


class AccurateDiscoveryIn(BaseModel):
    source: DiscoverySource
    campaign_id: Optional[int] = None
    product_id: Optional[int] = None
    icp: ICPProfile
    target_domain: Optional[str] = None
    max_leads: int = Field(25, ge=1, le=200)
    min_icp_score: int = Field(60, ge=0, le=100)


class DiscoveryProgressEvent(BaseModel):
    """SSE event payload."""

    type: Literal[
        "started", "source_started", "source_progress", "source_done", "lead", "done", "error"
    ]
    source: Optional[DiscoverySource] = None
    message: Optional[str] = None
    count: Optional[int] = None
    lead: Optional[GlobalLeadOut] = None
    icp_score: Optional[int] = None


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------
class LeadEnrichmentOut(BaseModel):
    company_domain: str
    page_title: Optional[str] = None
    meta_description: Optional[str] = None
    headings: Optional[Dict[str, Any]] = None
    body_snippet: Optional[str] = None
    tech_stack: Optional[List[str]] = None
    news_headlines: Optional[List[Dict[str, Any]]] = None
    fetched_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------
class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    product_id: Optional[int] = None
    icp: Optional[ICPProfile] = None
    status: Literal["draft", "active", "paused", "archived"] = "draft"


class CampaignUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    product_id: Optional[int] = None
    icp: Optional[ICPProfile] = None
    status: Optional[Literal["draft", "active", "paused", "archived"]] = None


class CampaignOut(BaseModel):
    id: int
    workspace_id: int
    product_id: Optional[int] = None
    name: str
    icp: Optional[Dict[str, Any]] = None
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


__all__ = [
    "ICPProfile",
    "GlobalLeadBase",
    "GlobalLeadOut",
    "LeadBase",
    "LeadCreate",
    "LeadBulkCreate",
    "LeadUpdate",
    "LeadOut",
    "AutonomousDiscoveryIn",
    "AccurateDiscoveryIn",
    "DiscoveryProgressEvent",
    "LeadEnrichmentOut",
    "CampaignCreate",
    "CampaignUpdate",
    "CampaignOut",
]
