"""SQLAlchemy models for NEXUS Phase 3 — Lead Discovery, Enrichment, ICP, Campaigns.

All tables are prefixed with ``nexus_`` to keep them isolated from PIPELYT's
core tables. ``nexus_workspaces`` and ``nexus_user_profiles`` are owned by
Phase 1 (``nexus.models``). ``nexus_products`` is owned by Phase 2.

Cross-phase foreign keys are intentionally left as bare ``BigInteger`` columns
with a TODO comment — the parent agent wires the FK constraint at merge time.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from core.database import Base


# ---------------------------------------------------------------------------
# nexus_global_leads — provider-agnostic person record, dedupe-by-email
# ---------------------------------------------------------------------------
class NexusGlobalLead(Base):
    __tablename__ = "nexus_global_leads"

    id = Column(BigInteger, primary_key=True, index=True)
    email = Column(String(320), nullable=False, unique=True, index=True)
    first_name = Column(String(120), nullable=True)
    last_name = Column(String(120), nullable=True)
    role = Column(String(160), nullable=True)
    company_domain = Column(String(255), nullable=True, index=True)
    company_name = Column(String(255), nullable=True)
    linkedin_url = Column(String(512), nullable=True)
    # 'new' | 'contacted' | 'replied' | 'unsubscribed' | 'bounced'
    status = Column(String(32), default="new", nullable=False)
    # GTM Journey display priority — operator can hide / deprioritise.
    # 'active' | 'low_priority' | 'hidden'
    priority = Column(String(16), default="active", nullable=False)
    email_verified = Column(Boolean, default=False, nullable=False)
    email_verify_score = Column(Integer, default=0, nullable=False)
    source = Column(String(64), nullable=True)

    # ── 2026-05-29 — Apollo-extracted columns that drive the leads table ──
    # person_city / person_state / person_country build the human-readable
    # "Location" column ("Bengaluru, India") on the leads table.
    # organization_industry drives the "Industry" column.
    # All optional — older rows have NULLs until backfilled or replaced
    # on next discovery. Added via sync_db_schema ALTER TABLE.
    person_city = Column(String(120), nullable=True)
    person_state = Column(String(120), nullable=True)
    person_country = Column(String(120), nullable=True)
    organization_industry = Column(String(160), nullable=True, index=True)

    # ── 2026-06-05 — LinkedIn headline (the one-liner under a person's name).
    # Already returned by Apollo's people response; previously dropped. Used
    # ONLY to personalize outreach openers. Optional; never invented.
    linkedin_headline = Column(String(512), nullable=True)
    # 2026-06-23 — Apollo's stable person id. Canonical dedup/traceability anchor
    # (survives LinkedIn URL changes). Filled at discovery; never overwritten.
    apollo_person_id = Column(String(64), nullable=True, index=True)

    # 2026-06-02 — PRIMARY phone number. Populated by Apollo /people/bulk_match
    # at discovery, or the first number captured from a reply signature. NULL
    # when neither yields one — never invented. Kept as a single scalar for
    # backward-compatible reads (leads table, exports).
    phone = Column(String(40), nullable=True)

    # 2026-07-23 — ALL captured numbers for this lead (scenario 9). A signature
    # can carry more than one line (an office + a mobile, or an Indian and an
    # Australian number), and a later reply can add another — every distinct
    # one is kept here, de-duplicated by canonical form, append-only. `phone`
    # above stays the primary (first) for existing single-number readers.
    phones = Column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )

    # 2026-07-15 — referral provenance. Set when this lead was created
    # because an existing lead's reply named them as an alternate contact
    # (e.g. an OOO reply: "please contact mitch@maap.cc"). `source` is set
    # to 'referral' for these rows (existing column, shared with other
    # provenance values like 'apollo'/'manual_upload'). NULL for
    # normally-sourced leads.
    source_lead_id = Column(BigInteger, nullable=True, index=True)
    source_message_id = Column(BigInteger, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    leads = relationship("NexusLead", back_populates="global_lead", lazy="noload")

    __table_args__ = (
        Index("ix_nexus_global_leads_email_lower", "email"),
        Index("ix_nexus_global_leads_company_domain", "company_domain"),
    )


# ---------------------------------------------------------------------------
# nexus_leads — workspace-scoped attachment of a global lead to a campaign
# ---------------------------------------------------------------------------
class NexusLead(Base):
    __tablename__ = "nexus_leads"

    id = Column(BigInteger, primary_key=True, index=True)
    # FK to nexus_workspaces.id (owned by Phase 1)
    workspace_id = Column(
        BigInteger,
        ForeignKey("nexus_workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # TODO: FK to nexus_campaigns.id wired at merge
    campaign_id = Column(BigInteger, nullable=True, index=True)
    # TODO: FK to nexus_products.id wired at merge (Phase 2 owns nexus_products)
    product_id = Column(BigInteger, nullable=True, index=True)
    global_lead_id = Column(
        BigInteger,
        ForeignKey("nexus_global_leads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable: a BYO-uploaded lead with no "Match Score" column stores NULL
    # (blank in the GTM Journey). Apollo-discovered leads always set a real
    # computed score, so they're never NULL.
    icp_score = Column(Integer, nullable=True)
    signals = Column(JSONB, nullable=True)  # discovery + intent signals
    # ── 2026-07-25 — PER-WORKSPACE lead state (isolation fix) ──────────────
    # Engagement status + captured phone used to live ONLY on the shared
    # nexus_global_leads row, so a person targeted by two workspaces leaked
    # one tenant's status/phone into the other. These per-workspace columns
    # are now authoritative for display; reads COALESCE(nl.*, gl.*) so legacy
    # rows (NULL here) still fall back to the global value until backfilled.
    status = Column(String(32), nullable=True)
    phone = Column(String(40), nullable=True)
    phones = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    global_lead = relationship("NexusGlobalLead", back_populates="leads", lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "campaign_id", "global_lead_id", name="uq_nexus_leads_scope"
        ),
        Index("ix_nexus_leads_workspace_campaign", "workspace_id", "campaign_id"),
    )


# ---------------------------------------------------------------------------
# nexus_lead_enrichment — per-domain enrichment cache with 30-day TTL
# ---------------------------------------------------------------------------
class NexusLeadEnrichment(Base):
    __tablename__ = "nexus_lead_enrichment"

    id = Column(BigInteger, primary_key=True, index=True)
    company_domain = Column(String(255), nullable=False, unique=True, index=True)
    page_title = Column(String(512), nullable=True)
    meta_description = Column(Text, nullable=True)
    headings = Column(JSONB, nullable=True)  # {"h1": [...], "h2": [...]}
    body_snippet = Column(Text, nullable=True)
    tech_stack = Column(JSONB, nullable=True)  # ["nginx", "cloudflare", ...]
    news_headlines = Column(JSONB, nullable=True)  # [{title, link, pubDate}]
    # 2026-07-29 — structured profile of the LEAD'S OWN company, from Apollo's
    # organization-enrich response (a call we already made and mostly discarded):
    # {name, description, industry, headcount, revenue, founded_year, hq,
    #  keywords[], technologies[], linkedin_url, website}. Consumed via
    # services/company_context.py so agenda/briefing/email all read one source.
    company_profile = Column(JSONB, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index("ix_nexus_lead_enrichment_fetched_at", "fetched_at"),
    )


# ---------------------------------------------------------------------------
# nexus_intent_signals — intent events linked to a workspace lead
# ---------------------------------------------------------------------------
class NexusIntentSignal(Base):
    __tablename__ = "nexus_intent_signals"

    id = Column(BigInteger, primary_key=True, index=True)
    lead_id = Column(
        BigInteger,
        ForeignKey("nexus_leads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 'reddit_pain' | 'crunchbase_funding' | 'github_role' | ...
    signal_type = Column(String(64), nullable=False)
    payload = Column(JSONB, nullable=True)
    observed_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# nexus_apollo_lead_cache — query-level Apollo response cache
# ---------------------------------------------------------------------------
class NexusApolloLeadCache(Base):
    __tablename__ = "nexus_apollo_lead_cache"

    id = Column(BigInteger, primary_key=True, index=True)
    query_hash = Column(String(64), nullable=False, unique=True, index=True)
    response = Column(JSONB, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# nexus_scrape_cache — cross-process ScrapeBundle cache (2026-06-11).
# The in-memory scrape cache in playwright_scraper only lives inside ONE
# process; on Lambda the /scrape-preview and /analyze requests can land on
# different containers, so the launch re-scraped a site previewed seconds
# earlier. This table shares the bundle across containers. Read/write is
# best-effort (scraping must never fail because the cache does) and rows
# are pruned past the same 15-min TTL the memory layer uses.
# ---------------------------------------------------------------------------
class NexusScrapeCache(Base):
    __tablename__ = "nexus_scrape_cache"

    id = Column(BigInteger, primary_key=True, index=True)
    url_hash = Column(String(64), nullable=False, unique=True, index=True)
    url = Column(String(2048), nullable=True)
    bundle = Column(JSONB, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# nexus_campaigns — top-level outbound campaign object
# ---------------------------------------------------------------------------
class NexusCampaign(Base):
    __tablename__ = "nexus_campaigns"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(
        BigInteger,
        ForeignKey("nexus_workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # TODO: FK to nexus_products.id wired at merge (Phase 2 owns nexus_products)
    product_id = Column(BigInteger, nullable=True, index=True)
    name = Column(String(255), nullable=False)
    icp = Column(JSONB, nullable=True)
    # 'draft' | 'active' | 'paused' | 'archived'
    status = Column(String(32), default="draft", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    # Apollo page-progression cursor. Each discovery run starts at
    # `last_apollo_page + 1` and walks forward by APOLLO_MAX_PAGES,
    # then writes the new high-water mark back. Default 0 = "no run
    # yet, start at page 1". Column is added by the phase3 ALTER
    # TABLE step; declaring it here so the ORM auto-loads it on
    # SELECT and SQLAlchemy can set it on UPDATE.
    last_apollo_page = Column(Integer, nullable=False, server_default="0")
    # Sister cursor for the broaden-retry path inside search_people
    # (the call that drops q_keywords + person_titles when the strict
    # query yields too few candidates). Without this, every broader
    # retry would replay page 1 forever; with it, the broader pool
    # keeps yielding fresh people across re-runs.
    last_apollo_broader_page = Column(Integer, nullable=False, server_default="0")
    # Running total of credits consumed by this campaign's discovery runs
    # (email reveals + any future paid ops). Bumped inside credits_service.consume
    # so the GTM UI can show per-campaign spend. Added via ALTER TABLE; declared
    # here so the ORM auto-loads it.
    credits_consumed = Column(Integer, nullable=False, server_default="0")


# ---------------------------------------------------------------------------
# nexus_campaign_launches — append-only log of /analyze launches.
#
# /analyze REUSES the existing NexusCampaign row per product (see
# routers/analyze.py "Create or reuse the campaign for this product"
# block), so `NexusCampaign.created_at` is pinned to the very first run
# ever and cannot be used to distinguish per-launch activity. This
# side-table records one row per /analyze call. The outbound-emails
# preview fences touchpoints to "this launch only" by reading
# MAX(launched_at) WHERE campaign_id = :cid — a server-side clock that
# eliminates browser-vs-server skew.
# ---------------------------------------------------------------------------
class NexusCampaignLaunch(Base):
    __tablename__ = "nexus_campaign_launches"

    id = Column(BigInteger, primary_key=True, index=True)
    # Bare BIGINT — mirrors the cross-phase convention used elsewhere in
    # this file (see NexusLead.campaign_id). FK to nexus_campaigns is
    # added at merge time by the parent agent if needed.
    campaign_id = Column(BigInteger, nullable=False, index=True)
    launched_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        # Mirrors the migration SQL exactly (DESC on launched_at) so a
        # fresh dev env built off Base.metadata.create_all matches what
        # production gets from migrations/phase3.py. Postgres' B-tree
        # also satisfies MAX() via backward scan when DESC is omitted,
        # so the index spec doesn't change query plan — it's purely a
        # parity-with-migration-sql concern.
        Index(
            "ix_nexus_campaign_launches_campaign_time",
            "campaign_id",
            text("launched_at DESC"),
        ),
    )


__all__ = [
    "NexusGlobalLead",
    "NexusLead",
    "NexusLeadEnrichment",
    "NexusIntentSignal",
    "NexusApolloLeadCache",
    "NexusCampaign",
    "NexusCampaignLaunch",
]
