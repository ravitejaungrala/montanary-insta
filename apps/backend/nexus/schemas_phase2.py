"""
Phase 2 — Product Analysis Pydantic shapes.

Request/response models for /nexus/analyze, /nexus/refine-summary,
/nexus/kb and /nexus/products. ICP and key_benefits are kept as loose
JSON objects so we can extend the Qwen prompt schema without breaking
the API.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------


class AnalyzeRequest(BaseModel):
    # URL is now OPTIONAL (2026-06-12). Users without a website can instead
    # send `content` (pasted text, or text extracted from an uploaded
    # .pdf/.docx/.pptx via /nexus/kb/extract). Exactly one of url/content is
    # required — enforced by the validator below.
    url: Optional[HttpUrl] = None
    # Raw product content for the no-URL path. Fed straight to the same
    # analyze_product() Gemini call that the scrape path uses (no truncation /
    # char limit — the model's own context window is the only ceiling).
    content: Optional[str] = None
    # Optional explicit product/company name for the no-URL path. When blank
    # the model extracts the name from `content`. When set, it wins.
    product_name: Optional[str] = None
    workspace_id: int
    # Optional user-provided description that biases the Qwen extraction
    product_description: Optional[str] = None
    # Optional raw KB text that will be chunked + embedded async
    knowledge_base: Optional[str] = None
    # 'product' | 'service' — set by the wizard's Product/Service chooser.
    # Stored inside product.icp so we don't need a DB schema change.
    entity_type: Optional[str] = None

    # ── Representative (2026-07-30) ──────────────────────────────────────
    # The real person outbound email is signed by. Without these, every email
    # closes "<Product> Team", which reads as bulk mail and gives the prospect
    # nobody to reply to. Captured on the New Campaign wizard, stored in
    # product.icp['brand'] — the key `sequencer._build_sender_ctx_for_product`
    # already reads — so no schema change and no send-path change.
    # Name + role only, deliberately no email: the signature carries no address,
    # so it can never contradict the mailbox the mail actually leaves from.
    rep_name: Optional[str] = None
    rep_title: Optional[str] = None

    # ── Canonical Apollo-filter fields edited by the user on the targeting
    #    wizard step (2026-05-28). One name per concept, used identically
    #    here, in the React state, in the ICP dict, and in
    #    discovery_apollo._icp_to_apollo_body(). Only TWO have a different
    #    Apollo wire name (documented at the Apollo boundary):
    #      organization_industries  -> organization_industry_tag_ids
    #      buyer_technologies       -> currently_using_any_of_technology_uids
    #
    #    All optional — when omitted the analyze handler falls back to the
    #    Gemini-extracted values. When present, user-edits WIN over Gemini.
    person_titles: Optional[List[str]] = None
    person_locations: Optional[List[str]] = None
    # person_states briefly existed (2026-06-02); reverted same day since
    # Apollo has only one location field. State granularity now travels
    # inside person_locations values themselves.
    # person_seniorities removed 2026-06-02, person_departments removed
    # 2026-06-08 — titles already imply both. Fields intentionally absent;
    # Pydantic Extra.forbid is NOT set so a stale frontend still sending
    # them is silently ignored (frontend was updated to stop sending them).
    organization_industries: Optional[List[str]] = None
    # One of: a legacy band-label string (Gemini autofill); an explicit
    # {min, max} dollar object (custom value or merged adjacent bands); or a
    # LIST of {min, max} objects when the user picks NON-ADJACENT bands — each
    # is run as a separate Apollo search and the leads union into one campaign
    # (see discover_for_campaign + discovery_apollo.revenue_ranges_from_icp).
    revenue_range: Optional[Union[str, Dict[str, Any], List[Dict[str, Any]]]] = None
    buyer_technologies: Optional[List[str]] = None

    # How many QUALIFIED leads to target for this run (user-chosen on the
    # targeting step). "Qualified" = leads that survive all gates + get
    # attached, NOT raw Apollo matches. Optional; when omitted the analyze
    # handler falls back to DISCOVERY_MAX_LEADS (20). Clamped server-side to
    # a minimum of 1 (no max cap — the Apollo per-run credit budget + Lambda
    # timeout bound real spend/time).
    lead_count: Optional[int] = None

    @model_validator(mode="after")
    def _require_url_or_content(self):
        """Exactly one entry path: a website URL (scrape) OR pasted/uploaded
        content. Reject a request with neither."""
        has_url = self.url is not None
        has_content = bool((self.content or "").strip())
        if not has_url and not has_content:
            raise ValueError("Provide either a website `url` or `content`.")
        return self


class ICPShape(BaseModel):
    """Loose ICP shape — every field is optional so partial Qwen
    responses don't 422 the user. Frontends should treat missing keys
    as empty lists."""

    model_config = ConfigDict(extra="allow")

    industries: List[str] = Field(default_factory=list)
    roles: List[str] = Field(default_factory=list)
    company_sizes: List[str] = Field(default_factory=list)
    pain_points: List[str] = Field(default_factory=list)
    buying_triggers: List[str] = Field(default_factory=list)
    negative_signals: List[str] = Field(default_factory=list)
    tech_stack_hints: List[str] = Field(default_factory=list)
    geography_hints: List[str] = Field(default_factory=list)


class ProductSummaryShape(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: Optional[str] = None
    category: Optional[str] = None
    value_proposition: Optional[str] = None
    key_benefits: List[str] = Field(default_factory=list)
    pricing_tier: Optional[str] = None
    industry_relevance: Optional[str] = None


# ---------------------------------------------------------------------------
# Product CRUD
# ---------------------------------------------------------------------------


class ProductBase(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    value_proposition: Optional[str] = None
    key_benefits: List[str] = Field(default_factory=list)
    pricing_tier: Optional[str] = None
    industry_relevance: Optional[str] = None
    icp: Optional[Dict[str, Any]] = None
    source_url: Optional[str] = None


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    """Every field optional — PATCH semantics."""

    name: Optional[str] = None
    category: Optional[str] = None
    value_proposition: Optional[str] = None
    key_benefits: Optional[List[str]] = None
    pricing_tier: Optional[str] = None
    industry_relevance: Optional[str] = None
    icp: Optional[Dict[str, Any]] = None
    source_url: Optional[str] = None
    status: Optional[str] = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    user_id: int
    name: Optional[str] = None
    category: Optional[str] = None
    value_proposition: Optional[str] = None
    key_benefits: List[str] = Field(default_factory=list)
    pricing_tier: Optional[str] = None
    industry_relevance: Optional[str] = None
    icp: Dict[str, Any] = Field(default_factory=dict)
    source_url: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime
    # Number of leads currently attached to this product (via its campaigns).
    # Populated only by GET /nexus/products list; falls back to 0 elsewhere.
    lead_count: int = 0
    # 'product' | 'service' — derived from icp.entity_type. Legacy rows
    # with no icp.entity_type fall back to 'product'. Surfaced so the
    # frontend can split GTM Journey into Products / Services rows.
    entity_type: str = "product"


# ---------------------------------------------------------------------------
# Refine summary
# ---------------------------------------------------------------------------


class RefineSummaryRequest(BaseModel):
    product_id: int
    # Either freeform instruction to the LLM …
    instruction: Optional[str] = None
    # … or explicit field overrides applied directly to the row.
    edits: Optional[Dict[str, Any]] = None


class RefineSummaryResponse(BaseModel):
    product: ProductOut
    # Present when `instruction` was used — the new text from the LLM
    refined_summary: Optional[str] = None


# ---------------------------------------------------------------------------
# KB upload + assets
# ---------------------------------------------------------------------------


class KBUrlRequest(BaseModel):
    product_id: int
    url: HttpUrl


class KBTextRequest(BaseModel):
    product_id: int
    text: str
    title: Optional[str] = None


class ProductAssetOut(BaseModel):
    """List-view shape for a knowledge-base asset.

    Reflects the post-2026-05-26 Pinecone migration: raw text lives in
    S3 (`s3_url`), vectors live in Pinecone (`chunks_indexed`), and
    `status` reflects the background-task progress so the UI can show
    a per-row indexing badge.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    workspace_id: Optional[int] = None
    asset_type: str
    source: Optional[str] = None
    char_count: int
    # ── Pinecone-migration fields ─────────────────────────────────────
    s3_key: Optional[str] = None
    s3_url: Optional[str] = None
    status: str = Field(
        "indexed",
        description="processing | indexed | failed",
    )
    chunks_indexed: int = 0
    last_error: Optional[str] = None
    indexed_at: Optional[datetime] = None
    created_at: datetime


class KBUploadResponse(BaseModel):
    """Returned from POST /nexus/kb/upload | /url | /text.

    `chunks_indexed` is 0 immediately because the heavy work (chunk +
    embed + Pinecone upsert) runs in a background task. Poll
    GET /nexus/kb/asset/{id}/status until `status='indexed'` then read
    the final chunk count.
    """

    asset: ProductAssetOut
    chunks_indexed: int = Field(
        0,
        description="0 at upload time — indexing runs asynchronously. Poll the status endpoint.",
    )
    chars_extracted: int


class KBAssetStatusOut(BaseModel):
    """Minimal status-poll shape used by the UI between upload and the
    'Knowledge base updated' toast.
    """

    id: int
    status: str = Field(..., description="processing | indexed | failed")
    chunks_indexed: int = 0
    char_count: int = 0
    last_error: Optional[str] = None
    indexed_at: Optional[datetime] = None
    s3_url: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Multi-file upload (Claude / Perplexity / Gemini style — drag many files)
# ---------------------------------------------------------------------------


class KBMultiUploadItem(BaseModel):
    """One file's result inside a multi-file upload batch.

    `accepted=True` means the file passed validation, was saved to S3, has
    a DB row, and is queued for background indexing. The UI should poll
    /asset/{asset_id}/status until indexing completes.

    `accepted=False` means the file failed up-front validation (wrong
    extension, too large, empty, unreadable). The `error` field tells the
    user why — no DB row, no S3 object, no Pinecone work was done.
    """

    filename: str
    accepted: bool
    asset: Optional[ProductAssetOut] = Field(
        None,
        description="Set only when accepted=True (the queued asset row).",
    )
    chars_extracted: int = 0
    error: Optional[str] = Field(
        None,
        description="Set only when accepted=False (human-readable rejection reason).",
    )


class KBMultiUploadResponse(BaseModel):
    """Returned from POST /nexus/kb/upload for any number of files (1..N).

    Partial-success behaviour: if 8 of 10 files are accepted and 2 fail
    validation, the response is 207 Multi-Status (or 202 if all accepted).
    The frontend renders one toast per file — green for accepted, red for
    rejected.
    """

    items: List[KBMultiUploadItem]
    accepted_count: int
    rejected_count: int
    total_received: int


# ---------------------------------------------------------------------------
# Analyze response
# ---------------------------------------------------------------------------


class AnalyzeCampaignSummary(BaseModel):
    """Minimal view of the campaign that /nexus/analyze auto-creates."""

    id: int
    name: str
    status: str
    # Per-PRODUCT campaign number (restarts at 1 for each product) — what the UI
    # should show, NOT the global `id`. Falls back to the global id for legacy
    # rows that were never numbered.
    campaign_number: Optional[int] = None
    # Clean product/company name (without the "#N" suffix that `name` carries).
    product_name: Optional[str] = None
    # Campaign creation timestamp. Under Model C (new campaign per run) this
    # is THIS run's creation time. ISO string; None only for legacy rows.
    created_at: Optional[str] = None


class AnalyzeDiscoverySummary(BaseModel):
    """Result of the synchronous discovery pass triggered by /nexus/analyze."""

    leads_attached: int = 0
    by_source: Dict[str, int] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)
    # 'completed' | 'timed_out' | 'skipped'
    status: str = "skipped"
    # How many QUALIFIED leads the user requested for this run.
    requested: int = 0
    # WHY we may have landed fewer than requested, so the UI can show a clear
    # message: 'ok' | 'credits' | 'no_matches' | 'partial'.
    reason: Optional[str] = None
    # "Already in campaign" marker rows this run surfaced (people Agent #10
    # approved who were already contacted in an earlier campaign of this
    # product). Lets the UI avoid a misleading "0 leads found" toast.
    duplicates: int = 0


class AnalyzeResponse(BaseModel):
    product: ProductOut
    asset: Optional[ProductAssetOut] = None
    # Echo the raw LLM extraction so the UI can show the full Qwen output
    # before the user starts editing.
    extraction: Dict[str, Any] = Field(default_factory=dict)
    # Populated when analyze.py auto-creates a campaign + runs discovery.
    campaign: Optional[AnalyzeCampaignSummary] = None
    discovery: Optional[AnalyzeDiscoverySummary] = None
    # Inline leads payload — same shape as GET /nexus/campaigns/{id}/leads.
    # Lets the wizard render the LeadsTable immediately from the /analyze
    # response without a separate poll. Empty list when discovery skipped
    # or timed out.
    leads: List[Dict[str, Any]] = Field(default_factory=list)
