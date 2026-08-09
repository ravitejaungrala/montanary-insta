"""
Phase 2 — Product Analysis layer models.

These tables are populated by:
  - POST /nexus/analyze         (creates NexusProduct + ProductAsset[url])
  - POST /nexus/kb              (creates ProductAsset[pdf|docx|txt|url] + KnowledgeEmbedding rows)
  - PATCH /nexus/products/{id}  (user-refined edits to product summary)

Mongo legacy reference (read-only — DO NOT IMPORT):
  - nexus/server/models/Product.js
  - nexus/server/models/ProductAsset.js
  - nexus/server/models/KnowledgeEmbedding.js

All FKs point to PIPELYT tables (`users.id`) and Phase 1 nexus tables
(`nexus_workspaces.id`). Vectors are stored as JSONB until pgvector is
provisioned; the API surface is identical when we migrate to a `vector`
column type later.
"""

from datetime import datetime

from sqlalchemy import (
    Column,
    BigInteger,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from core.database import Base


class NexusProduct(Base):
    """One product per workspace (typically per `source_url`). Owns
    ProductAssets (the raw documents/URLs the analysis was derived from)
    and KnowledgeEmbeddings (chunked vectors over those assets)."""

    __tablename__ = "nexus_products"

    id = Column(BigInteger, primary_key=True, index=True)
    workspace_id = Column(
        BigInteger,
        ForeignKey("nexus_workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name = Column(String(255), nullable=True)
    category = Column(String(120), nullable=True)
    value_proposition = Column(Text, nullable=True)
    # JSONB array of short benefit strings extracted by Qwen
    key_benefits = Column(JSONB, nullable=True, default=list)
    pricing_tier = Column(String(40), nullable=True)  # free|freemium|paid|enterprise
    industry_relevance = Column(String(255), nullable=True)

    # JSONB shape (mirrors legacy):
    #   {
    #     "industries":     [...],
    #     "roles":          [...],
    #     "company_sizes":  [...],
    #     "pain_points":    [...],
    #     "buying_triggers":[...],
    #     "negative_signals":[...],
    #     "tech_stack_hints":[...],
    #     "geography_hints":[...]
    #   }
    icp = Column(JSONB, nullable=True, default=dict)

    # Gemini's 3-section grounded description (what it is / what we do + key
    # capabilities / who we serve), as returned by analyze_product. Rewritten
    # on every /analyze run alongside value_proposition and key_benefits, so it
    # can never be staler than they are. NULL on legacy rows and on the
    # pasted-content path -> the sequencer synthesizes one from the columns
    # instead (see sequencer._merge_product_description).
    product_description = Column(JSONB, nullable=True)

    source_url = Column(String(2048), nullable=True, index=True)
    # ready | analyzing | failed | archived
    status = Column(String(32), nullable=False, default="ready", index=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    assets = relationship(
        "NexusProductAsset",
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    embeddings = relationship(
        "NexusKnowledgeEmbedding",
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_nexus_products_workspace_status", "workspace_id", "status"),
    )


class NexusProductAsset(Base):
    """A single source document or URL attached to a Product.

    Migration 2026-05-26 (Pinecone): the heavy `raw_text` column is now
    OPTIONAL — new uploads store the raw file in S3 (see `s3_key`) and
    push chunked vectors to Pinecone (namespace `ws-{workspace_id}`,
    filtered by `product_id`). The DB row is now a lightweight metadata
    record:

        - status   : 'processing' (just uploaded, indexing in background)
                     'indexed'    (Pinecone vectors live)
                     'failed'     (background indexing errored — see last_error)
        - s3_key   : where the raw file lives in S3
        - s3_url   : presigned/public URL for the UI to download/preview
        - chunks_indexed : how many vectors Pinecone has for this asset
        - last_error     : last failure reason (if status='failed')
        - indexed_at     : timestamp of last successful index

    `workspace_id` is denormalised here so the Pinecone namespace can be
    computed without a campaign JOIN on every upload.
    """

    __tablename__ = "nexus_product_assets"

    id = Column(BigInteger, primary_key=True, index=True)
    product_id = Column(
        BigInteger,
        ForeignKey("nexus_products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id = Column(BigInteger, nullable=True, index=True)

    # url | pdf | docx | pptx | xlsx | csv | txt | md | text
    asset_type = Column(String(16), nullable=False, default="text")
    # Either a URL string or the original filename
    source = Column(String(1024), nullable=True)
    # Dropped 2026-05-27 by scripts/drop_old_kb_tables.py — raw text now
    # lives in S3, embeddings live in Pinecone. Column removed from DB.
    char_count = Column(Integer, nullable=False, default=0)

    # New columns (2026-05-26 Pinecone migration):
    s3_key = Column(String(1024), nullable=True)
    s3_url = Column(String(2048), nullable=True)
    status = Column(String(16), nullable=False, default="indexed")
    chunks_indexed = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    indexed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    product = relationship("NexusProduct", back_populates="assets")
    # `embeddings` relationship retained while NexusKnowledgeEmbedding table
    # still exists in DB. Will be removed once drop_old_kb_tables.py runs.
    embeddings = relationship(
        "NexusKnowledgeEmbedding",
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class NexusKnowledgeEmbedding(Base):
    """One chunked vector. `embedding` is stored as JSONB list[float] for
    now — a future migration will move this to a pgvector `vector(1024)`
    column without changing the API."""

    __tablename__ = "nexus_knowledge_embeddings"

    id = Column(BigInteger, primary_key=True, index=True)
    product_id = Column(
        BigInteger,
        ForeignKey("nexus_products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_asset_id = Column(
        BigInteger,
        ForeignKey("nexus_product_assets.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    chunk_index = Column(Integer, nullable=False, default=0)
    chunk_text = Column(Text, nullable=False)
    # list[float] — NIM nv-embedqa-e5-v5 returns 1024-dim vectors
    embedding = Column(JSONB, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    product = relationship("NexusProduct", back_populates="embeddings")
    asset = relationship("NexusProductAsset", back_populates="embeddings")

    __table_args__ = (
        Index(
            "ix_nexus_kb_emb_product_chunk",
            "product_id",
            "chunk_index",
        ),
    )
