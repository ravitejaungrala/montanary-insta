"""Pinecone-backed Knowledge Base for the NEXUS Reply Agent.

Replaces the previous Postgres (`nexus_knowledge_embeddings`) retrieval
path with a serverless Pinecone index. Strict tenant isolation:

    - Hard storage isolation: every workspace gets its own Pinecone
      NAMESPACE (`ws-{workspace_id}`). Vectors in one namespace are
      physically separated from another — a query to namespace A
      cannot see B's vectors regardless of filters.

    - In-workspace product isolation: vectors are tagged with
      `product_id` in metadata. Every query MUST pass
      `filter={"product_id": {"$eq": product_id}}` so a workspace
      running multiple products (Spenzo + Pipelyt + z-ninth) never
      retrieves the wrong product's chunks.

Why Pinecone (vs pgvector / JSONB cosine in Postgres):
    - 3072-dim embeddings (gemini-embedding-001) don't fit pgvector's
      2000-dim hard limit. We'd have to fall back to JSONB cosine in
      Python, which is slow at scale.
    - Pinecone serverless on AWS us-east-1 is co-located with the
      Lambda + S3, so query latency is < 50ms typical.
    - Free namespace partitioning gives multi-tenant isolation without
      paying per-index minimum costs.

Why we skipped LangChain:
    - The `langchain-pinecone` wrapper pulls ~80MB of transitive deps
      which bloats the Lambda container image and slows cold start.
    - The raw `pinecone` SDK is ~5MB. We reuse our existing
      `gemini.embed_batch` for the embedding calls.

Public API (all functions are sync — Pinecone SDK is sync-only as of v5):
    embed_and_upsert(workspace_id, product_id, asset_id, chunks, ...) -> int
    query(workspace_id, product_id, query_text, top_k, min_score)    -> list[ChunkHit]
    delete_asset(workspace_id, asset_id)                              -> int
    is_configured()                                                   -> bool
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

logger = logging.getLogger("pipelyt.nexus.pinecone_kb")


# ─── Configuration (hardcoded constants — see note below) ─────────────────
#
# Only the API key + (optionally) the index name come from env. Cloud,
# region, and dimension are HARDCODED on purpose: a mismatch between any
# of them would silently corrupt retrieval (Pinecone indexes are immutable
# after create — you can't resize a dim, you can't move a region).

# Index name is env-overridable so staging and prod can use different
# indexes (e.g. `nexus-kb-staging` vs `nexus-kb`).
INDEX_NAME: str = (os.getenv("PINECONE_INDEX_NAME") or "nexus-kb").strip() or "nexus-kb"

# Hardcoded — change requires a code review + index recreation.
PINECONE_CLOUD: str = "aws"
PINECONE_REGION: str = "us-east-1"

# Embedding dimension MUST match `gemini.EMBED_DIMS`. Both are hardcoded
# to 3072. Changing either requires:
#   1. Update both constants
#   2. Drop the existing Pinecone index
#   3. Re-create at the new dim (auto on next upload)
#   4. Re-upload all KB content (vectors at the old dim are unreadable)
_EMBED_DIMS: int = 3072

# Upsert in batches to stay well below Pinecone's 2MB request payload
# limit. 100 vectors * 3072 floats * 4 bytes ≈ 1.2MB, leaving room for
# metadata.
_UPSERT_BATCH_SIZE: int = 100

# When embedding a single inbound query, retry once on transient failure.
_QUERY_EMBED_RETRIES: int = 1

# Pinecone caps each vector's metadata at 40KB. We store the chunk text
# in metadata so retrieval doesn't need a second DB fetch — truncate
# defensively at 30KB to leave room for the other metadata fields.
_MAX_METADATA_TEXT_BYTES: int = 30_000


# ─── Pydantic models ──────────────────────────────────────────────────────


class ChunkHit(BaseModel):
    """One retrieval result returned to callers.

    Stable shape so the reply agent + future API endpoints don't have
    to parse raw Pinecone response dicts.
    """

    score: float = Field(..., description="Cosine similarity score (0..1)")
    text: str = Field(..., description="Chunk content stored in metadata")
    product_id: int = Field(0, description="Owning product id")
    asset_id: int = Field(0, description="Owning nexus_product_assets.id")
    chunk_index: int = Field(0, description="Position of this chunk inside the asset")
    source_name: str = Field("", description="Original filename or URL")
    asset_type: str = Field("", description="pdf | docx | pptx | xlsx | csv | txt | url | text")


# ─── Lazy module-level client (warm-container cache) ───────────────────────

_pc = None         # pinecone.Pinecone instance
_index = None      # pinecone.Index handle
_index_ready: bool = False


def is_configured() -> bool:
    """Return True iff PINECONE_API_KEY is present in env.

    Callers should short-circuit when Pinecone isn't configured (e.g.
    local dev without a Pinecone account) and fall back to ungrounded
    generation rather than crash.
    """
    return bool((os.getenv("PINECONE_API_KEY") or "").strip())


def _get_index():
    """Lazy-init: import pinecone, build the client, ensure the index
    exists, return an Index handle. Cached at module scope so warm Lambda
    invocations reuse the same connection.

    Raises RuntimeError if PINECONE_API_KEY is missing — callers should
    have checked is_configured() first.
    """
    global _pc, _index, _index_ready
    if _index is not None and _index_ready:
        return _index

    api_key = (os.getenv("PINECONE_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "Pinecone API key is missing. Set PINECONE_API_KEY in the Lambda env vars."
        )

    # Lazy import keeps non-KB code paths free of the cold-start cost.
    from pinecone import Pinecone, ServerlessSpec  # type: ignore

    if _pc is None:
        _pc = Pinecone(api_key=api_key)
        logger.info("Pinecone client initialised (index target: %s)", INDEX_NAME)

    # Create the index if it doesn't exist yet. Idempotent — list_indexes
    # is one cheap HTTP call.
    try:
        existing_indexes = {i.name for i in _pc.list_indexes()}
    except Exception as exc:
        logger.error("Could not list Pinecone indexes: %s", exc)
        raise

    if INDEX_NAME not in existing_indexes:
        logger.info(
            "Creating serverless Pinecone index '%s' (dim=%d, metric=cosine, cloud=%s, region=%s)",
            INDEX_NAME, _EMBED_DIMS, PINECONE_CLOUD, PINECONE_REGION,
        )
        try:
            _pc.create_index(
                name=INDEX_NAME,
                dimension=_EMBED_DIMS,
                metric="cosine",
                spec=ServerlessSpec(cloud=PINECONE_CLOUD, region=PINECONE_REGION),
            )
        except Exception as exc:
            logger.error("Failed to create Pinecone index '%s': %s", INDEX_NAME, exc)
            raise
        # Wait until the new index is ready — serverless provisions in 5-15s.
        for attempt in range(30):
            try:
                desc = _pc.describe_index(INDEX_NAME)
                if getattr(desc, "status", {}).get("ready"):
                    logger.info("Pinecone index '%s' is ready (took ~%ds)", INDEX_NAME, attempt)
                    break
            except Exception as exc:
                logger.debug("Index '%s' not ready yet (attempt %d): %s", INDEX_NAME, attempt, exc)
            time.sleep(1.0)
        else:
            logger.warning(
                "Pinecone index '%s' did not report ready after 30s; proceeding anyway",
                INDEX_NAME,
            )

    _index = _pc.Index(INDEX_NAME)
    _index_ready = True
    return _index


# ─── Helpers ───────────────────────────────────────────────────────────────


def _ns(workspace_id: int) -> str:
    """Namespace name for a workspace. Kept as a function so the convention
    is documented once — any future rename (e.g. adding a staging prefix)
    is a one-line change here.
    """
    return f"ws-{int(workspace_id)}"


def _vec_id(asset_id: int, chunk_index: int) -> str:
    """Deterministic vector ID. Re-uploading the same asset overwrites
    its existing vectors (no orphans), and `delete_asset` can target by
    asset_id without a metadata scan.
    """
    return f"asset-{int(asset_id)}-chunk-{int(chunk_index)}"


def _truncate_metadata_text(text: str) -> str:
    """Truncate chunk text to fit Pinecone's per-vector metadata limit
    (40KB). Uses 30KB ceiling to leave headroom for the other metadata
    fields. Never raises — returns "" if input is empty.
    """
    if not text:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= _MAX_METADATA_TEXT_BYTES:
        return text
    return encoded[:_MAX_METADATA_TEXT_BYTES].decode("utf-8", errors="ignore")


# ─── Public API ────────────────────────────────────────────────────────────


def embed_and_upsert(
    *,
    workspace_id: int,
    product_id: int,
    asset_id: int,
    chunks: Sequence[str],
    source_name: str = "",
    asset_type: str = "",
) -> int:
    """Embed each chunk and upsert it to Pinecone. Returns the number of
    vectors successfully written.

    Idempotent on (asset_id, chunk_index): re-uploading the same asset
    overwrites the previous vectors so duplicate calls never create
    duplicates.

    Args:
        workspace_id: tenant boundary — becomes the Pinecone namespace.
        product_id:   in-workspace product boundary — stored as metadata.
        asset_id:     nexus_product_assets.id — used in the deterministic
                      vector ID + metadata for delete-by-asset.
        chunks:       ordered list of chunk text strings (from chunk_text).
        source_name:  original filename or URL (used by the reply agent
                      to cite the source in generated replies).
        asset_type:   'pdf' / 'docx' / 'pptx' / 'xlsx' / 'csv' / 'txt' /
                      'url' / 'text'.

    Raises:
        RuntimeError if PINECONE_API_KEY is not configured.
        Exception on Pinecone upsert failure (caller decides how to react;
        in kb.py the background task marks the asset row as 'failed').
    """
    if not chunks:
        logger.info(
            "Nothing to upsert for asset %d (workspace=%d product=%d): chunk list is empty",
            asset_id, workspace_id, product_id,
        )
        return 0

    if not is_configured():
        raise RuntimeError(
            "Cannot index knowledge base — PINECONE_API_KEY is not set in the environment."
        )

    # Embed all chunks. gemini.embed_batch is sequential but rate-limit
    # tolerant. A 50-chunk PDF takes ~5 seconds on a warm Lambda.
    from nexus.services import gemini  # lazy import — avoid import cycle

    try:
        vectors = gemini.embed_batch(list(chunks), input_type="passage")
    except Exception as exc:
        logger.error(
            "Gemini embedding failed for asset %d (workspace=%d product=%d): %s",
            asset_id, workspace_id, product_id, exc,
        )
        raise

    if len(vectors) != len(chunks):
        logger.warning(
            "Embedding count mismatch for asset %d: requested %d chunks, got %d vectors back",
            asset_id, len(chunks), len(vectors),
        )

    # Build the upsert payload. Drop any chunks whose embedding came back
    # as an all-zero fallback (e.g. Gemini was down) — a noise vector
    # pollutes retrieval far more than a missing chunk.
    records: List[Dict[str, Any]] = []
    skipped_zero_vector = 0
    skipped_empty_chunk = 0
    skipped_dim_mismatch = 0

    for chunk_idx, (chunk_text, vec) in enumerate(zip(chunks, vectors)):
        if not chunk_text or not chunk_text.strip():
            skipped_empty_chunk += 1
            continue
        if not vec or all(v == 0 for v in vec):
            skipped_zero_vector += 1
            continue
        if len(vec) != _EMBED_DIMS:
            logger.warning(
                "Skipping chunk %d of asset %d — embedding dim %d does not match index dim %d",
                chunk_idx, asset_id, len(vec), _EMBED_DIMS,
            )
            skipped_dim_mismatch += 1
            continue
        records.append({
            "id": _vec_id(asset_id, chunk_idx),
            "values": vec,
            "metadata": {
                "product_id":  int(product_id),
                "asset_id":    int(asset_id),
                "chunk_index": int(chunk_idx),
                "text":        _truncate_metadata_text(chunk_text),
                "source_name": (source_name or "")[:200],
                "asset_type":  (asset_type or "")[:32],
            },
        })

    if skipped_zero_vector:
        logger.warning(
            "Skipped %d chunks with zero-vector embeddings on asset %d (likely Gemini outage)",
            skipped_zero_vector, asset_id,
        )
    if skipped_empty_chunk:
        logger.info(
            "Skipped %d empty chunks on asset %d", skipped_empty_chunk, asset_id,
        )

    if not records:
        logger.warning(
            "No usable vectors to upsert for asset %d after filtering (workspace=%d product=%d)",
            asset_id, workspace_id, product_id,
        )
        return 0

    try:
        index = _get_index()
    except Exception as exc:
        logger.error("Could not get Pinecone index handle: %s", exc)
        raise

    namespace = _ns(workspace_id)
    total_written = 0
    for batch_start in range(0, len(records), _UPSERT_BATCH_SIZE):
        batch = records[batch_start : batch_start + _UPSERT_BATCH_SIZE]
        try:
            index.upsert(vectors=batch, namespace=namespace)
        except Exception as exc:
            logger.error(
                "Pinecone upsert failed (workspace=%d asset=%d batch_start=%d size=%d): %s",
                workspace_id, asset_id, batch_start, len(batch), exc,
            )
            # Surface to caller so the asset row can be marked 'failed'.
            raise
        total_written += len(batch)

    logger.info(
        "Indexed %d vectors for asset %d in namespace '%s' (product=%d, source='%s')",
        total_written, asset_id, namespace, product_id, source_name,
    )
    return total_written


def query(
    *,
    workspace_id: int,
    product_id: int,
    query_text: str,
    top_k: int = 5,
    min_score: float = 0.5,
) -> List[ChunkHit]:
    """Semantic search the workspace's KB for chunks relevant to a query.

    Strict isolation:
        - Namespace `ws-{workspace_id}` (storage-level partition).
        - Metadata filter `product_id == :product_id` (in-workspace).

    Returns up to `top_k` chunks ranked by cosine similarity. Chunks
    below `min_score` (0.5 ≈ "semantically related") are filtered out
    so the reply prompt doesn't get padded with weak matches.

    Failure modes (all return [] — never raises):
        - PINECONE_API_KEY not configured
        - Empty query text
        - Gemini embedding failure (e.g. quota / outage)
        - Pinecone index unreachable
        - Zero matches in this namespace
    """
    if not query_text or not query_text.strip():
        return []
    if not is_configured():
        logger.debug("Pinecone not configured; skipping retrieval (returning no matches)")
        return []

    # Embed the query. `input_type='query'` uses RETRIEVAL_QUERY task type,
    # which is calibrated differently from passage embeddings for cosine
    # accuracy.
    from nexus.services import gemini  # lazy import

    last_embed_exception: Optional[Exception] = None
    qvec: List[float] = []
    for attempt in range(_QUERY_EMBED_RETRIES + 1):
        try:
            qvec = gemini.embed_text(query_text, input_type="query")
            break
        except Exception as exc:
            last_embed_exception = exc
            logger.warning(
                "Gemini query embedding attempt %d failed: %s",
                attempt + 1, exc,
            )
            if attempt < _QUERY_EMBED_RETRIES:
                time.sleep(0.5)

    if not qvec or all(v == 0 for v in qvec):
        logger.warning(
            "Could not embed inbound query — Gemini returned a zero or empty vector (last error: %s)",
            last_embed_exception,
        )
        return []
    if len(qvec) != _EMBED_DIMS:
        logger.error(
            "Query embedding dim %d does not match Pinecone index dim %d — retrieval skipped",
            len(qvec), _EMBED_DIMS,
        )
        return []

    try:
        index = _get_index()
    except Exception as exc:
        logger.warning("Pinecone index unavailable; returning no matches. Reason: %s", exc)
        return []

    namespace = _ns(workspace_id)
    try:
        response = index.query(
            namespace=namespace,
            vector=qvec,
            top_k=int(top_k),
            include_metadata=True,
            filter={"product_id": {"$eq": int(product_id)}},
        )
    except Exception as exc:
        logger.warning(
            "Pinecone query failed (namespace=%s product=%d): %s",
            namespace, product_id, exc,
        )
        return []

    # Pinecone v5 returns a Match object with attribute access; older
    # surfaces return raw dicts. Handle both shapes.
    raw_matches = (
        getattr(response, "matches", None)
        or (response.get("matches") if isinstance(response, dict) else None)
        or []
    )

    hits: List[ChunkHit] = []
    for match in raw_matches:
        score = float(getattr(match, "score", None) or match.get("score", 0.0))
        if score < float(min_score):
            continue
        metadata = getattr(match, "metadata", None) or match.get("metadata", {}) or {}
        chunk_text = (metadata.get("text") or "").strip()
        if not chunk_text:
            continue
        try:
            hits.append(ChunkHit(
                score=round(score, 4),
                text=chunk_text,
                product_id=int(metadata.get("product_id") or 0),
                asset_id=int(metadata.get("asset_id") or 0),
                chunk_index=int(metadata.get("chunk_index") or 0),
                source_name=str(metadata.get("source_name") or ""),
                asset_type=str(metadata.get("asset_type") or ""),
            ))
        except Exception as exc:  # malformed metadata — skip the row
            logger.debug("Skipping malformed Pinecone match: %s", exc)
            continue

    top_score = hits[0].score if hits else 0.0
    logger.info(
        "Retrieved %d chunk(s) for workspace=%d product=%d (top score %.4f)",
        len(hits), workspace_id, product_id, top_score,
    )
    return hits


def delete_asset(*, workspace_id: int, asset_id: int) -> int:
    """Remove every vector belonging to one asset from its workspace's
    namespace.

    Called when a user deletes a knowledge file from the UI. Returns 1
    on success or 0 on failure / unconfigured — Pinecone's delete-by-filter
    doesn't return an exact deleted count, so we report a boolean-ish.

    Never raises — failures here shouldn't block the DB row delete.
    """
    if not is_configured():
        logger.debug("Skipping Pinecone delete; PINECONE_API_KEY not configured")
        return 0

    try:
        index = _get_index()
    except Exception as exc:
        logger.warning(
            "Cannot delete vectors for asset %d (workspace=%d) — Pinecone index unavailable: %s",
            asset_id, workspace_id, exc,
        )
        return 0

    namespace = _ns(workspace_id)
    try:
        index.delete(
            filter={"asset_id": {"$eq": int(asset_id)}},
            namespace=namespace,
        )
        logger.info(
            "Deleted vectors for asset %d in namespace '%s'", asset_id, namespace,
        )
        return 1
    except Exception as exc:
        logger.warning(
            "Pinecone delete failed for asset %d in namespace '%s': %s",
            asset_id, namespace, exc,
        )
        return 0
