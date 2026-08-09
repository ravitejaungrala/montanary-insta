"""
Chunk + (lazily) embed knowledge-base documents.

Post-2026-05-26 (Pinecone migration) the embedding + persistence
responsibility moved to `nexus.services.pinecone_kb`. This module
now exposes only the chunker (`chunk_text`) and a thin shim
(`embed_chunks_and_save`) that routes to Pinecone for any legacy
caller that still needs it.

Pipeline today:
    raw text (PDF / DOCX / PPTX / XLSX / URL / pasted)
        -> services.doc_processor.extract_text_from_file
        -> chunk_text() — ~1000-char windows on paragraph boundaries
        -> nexus.services.pinecone_kb.embed_and_upsert
            (which internally calls gemini.embed_batch for the
             3072-dim Gemini embeddings, then upserts to the
             workspace's Pinecone namespace)

The chunker splits on paragraphs (\\n\\n), packs until the soft limit,
and never splits mid-sentence unless a single paragraph exceeds the
limit.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List

from sqlalchemy.orm import Session

from nexus import models_phase2  # type: ignore

logger = logging.getLogger("pipelyt.nexus.embedding")

# Chunking constants (intentionally NOT env-tunable).
#
# Raised from 500/50 (the pre-Pinecone defaults) to 1000/200 because
# gemini-embedding-001 @ 3072 dim preserves more meaning per chunk —
# we can index larger windows without diluting retrieval precision.
# Keeping them as code constants prevents accidental drift between
# environments (e.g. dev indexing at 1000c while prod indexes at 500c
# would produce wildly different retrieval quality for the same query).
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_CHUNK_OVERLAP = 200


@dataclass
class Chunk:
    index: int
    text: str


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> List[Chunk]:
    """Split `text` into roughly `chunk_size`-character chunks on
    paragraph boundaries. If a single paragraph is larger than
    `chunk_size`, fall back to fixed-size slicing with `overlap`."""

    norm = _normalize(text)
    if not norm:
        return []

    paragraphs = [p.strip() for p in norm.split("\n\n") if p.strip()]
    chunks: List[str] = []
    current = ""

    for p in paragraphs:
        if len(p) > chunk_size:
            # Flush whatever we were building
            if current:
                chunks.append(current.strip())
                current = ""
            # Window the long paragraph with overlap
            i = 0
            step = max(chunk_size - overlap, 1)
            while i < len(p):
                chunks.append(p[i : i + chunk_size].strip())
                i += step
            continue

        if not current:
            current = p
            continue

        if len(current) + len(p) + 2 <= chunk_size:
            current = f"{current}\n\n{p}"
        else:
            chunks.append(current.strip())
            current = p

    if current:
        chunks.append(current.strip())

    return [Chunk(index=i, text=c) for i, c in enumerate(chunks) if c]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def embed_chunks_and_save(
    db: Session,
    *,
    product_id: int,
    asset_id: int,
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    workspace_id: int | None = None,
    source_name: str = "",
    asset_type: str = "",
) -> int:
    """DEPRECATED — kept as a thin shim for any legacy caller.

    The 2026-05-26 Pinecone migration moved storage out of the
    `nexus_knowledge_embeddings` table and into Pinecone. New code should
    call `nexus.services.pinecone_kb.embed_and_upsert` directly with
    `workspace_id` and the chunk list — see `routers/kb.py`.

    This shim now routes through Pinecone IF `workspace_id` is provided,
    otherwise it returns 0 (logs a warning) — we no longer write to the
    old Postgres table at all.
    """
    if workspace_id is None:
        logger.warning(
            "embed_chunks_and_save called WITHOUT workspace_id — Pinecone path "
            "requires it. asset_id=%s product_id=%s. Returning 0 (no-op).",
            asset_id, product_id,
        )
        return 0

    chunks = chunk_text(text, chunk_size=chunk_size)
    chunk_strs = [c.text for c in chunks]
    if not chunk_strs:
        return 0

    # Lazy import to keep the legacy import surface small.
    from nexus.services import pinecone_kb
    return pinecone_kb.embed_and_upsert(
        workspace_id=workspace_id,
        product_id=product_id,
        asset_id=asset_id,
        chunks=chunk_strs,
        source_name=source_name,
        asset_type=asset_type,
    )
