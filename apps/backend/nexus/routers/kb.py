"""
/nexus/kb — Knowledge-base ingestion for a Nexus Product.

Migration 2026-05-26 (Pinecone + S3):
    - Raw files now stored in S3 at:
        s3://<S3_BUCKET_NAME>/nexus-kb/ws-{ws}/prod-{pid}/asset-{aid}/{filename}
    - Vector embeddings now stored in Pinecone (namespace `ws-{workspace_id}`,
      filter on `product_id` for strict per-product isolation).
    - DB row (`nexus_product_assets`) is now a lightweight metadata record:
      status / s3_key / s3_url / chunks_indexed / last_error / indexed_at.
    - Heavy work (extract → chunk → embed → upsert) runs in FastAPI
      BackgroundTasks so the upload endpoint returns in ~1 second.
    - Status polled via GET /nexus/kb/asset/{id}/status.

Endpoints (all return immediately with status='processing' — UI polls):
  * POST /nexus/kb/upload          multipart file (pdf/docx/pptx/xlsx/csv/txt/md)
  * POST /nexus/kb/url             scrape an HTTPS URL and index the text
  * POST /nexus/kb/text            raw pasted text
  * GET  /nexus/kb/{product_id}    list assets attached to a product
  * GET  /nexus/kb/asset/{id}/status   poll indexing status for one asset
  * DELETE /nexus/kb/asset/{id}    remove the asset (S3 + Pinecone + DB)
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from core.database import SessionLocal, get_db
from core.s3_utils import get_s3_client, get_s3_url
from core.config import S3_BUCKET_NAME

# Optional dedicated bucket for NEXUS knowledge-base files (kept separate
# from the image-upload bucket so KB files can have different lifecycle
# policies, versioning, or cross-account access). Falls back to the
# generic S3_BUCKET_NAME when unset so existing deployments keep working.
import os as _os
NEXUS_KB_S3_BUCKET = (_os.getenv("NEXUS_KB_S3_BUCKET") or S3_BUCKET_NAME or "").strip()
from models import User
from nexus.deps import (  # type: ignore[import-not-found]
    get_nexus_user,
    require_current_workspace,
    trial_guard,
)
from nexus import models_phase2
from nexus.schemas_phase2 import (
    KBAssetStatusOut,
    KBMultiUploadItem,
    KBMultiUploadResponse,
    KBTextRequest,
    KBUploadResponse,
    KBUrlRequest,
    ProductAssetOut,
)
from nexus.services import embedding as embedding_service
from nexus.services import pinecone_kb
from nexus.services import playwright_scraper

# Reuse PIPELYT's existing PDF/DOCX/PPTX/XLSX/CSV/TXT extractor.
try:
    from services.doc_processor import extract_text_from_file  # type: ignore
except ImportError:  # pragma: no cover
    def extract_text_from_file(content: bytes, filename: str) -> str:  # type: ignore[misc]
        try:
            return content.decode("utf-8", errors="ignore")
        except Exception:
            return ""

logger = logging.getLogger("pipelyt.nexus.kb")

router = APIRouter(prefix="/nexus/kb", tags=["Nexus — Knowledge Base"])


# Extensions accepted by the upload endpoint. Expanded to include pptx +
# xlsx (Office formats) — the new doc_processor handles all of these via
# python-pptx and openpyxl.
_ALLOWED_EXTS = {"pdf", "docx", "pptx", "xlsx", "csv", "txt", "md"}

# Per-file size cap. 25MB covers most pricing decks, FAQ PDFs, case
# studies. Heavier docs are usually noise (raw scans, design files).
MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB

# Multi-file upload safety limits. Lambda timeout is 14 min — we use
# only ~1-2 min even on a 20-file batch because:
#   - Extract + S3 upload is synchronous in the request thread (fast)
#   - The slow work (chunk + embed + Pinecone) runs in BackgroundTasks
#     which start AFTER the response is returned
# These caps prevent a runaway 100-file dump from queuing too many
# parallel background tasks in one Lambda container.
MAX_FILES_PER_REQUEST = 20
MAX_TOTAL_BYTES_PER_REQUEST = 250 * 1024 * 1024  # 250 MB across all files

_S3_KEY_PREFIX = "nexus-kb"


def _sanitize_filename(name: str) -> str:
    """Strip path traversal + most special chars to make a safe S3 key
    segment. Preserves extension."""
    base = (name or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    # Replace anything that isn't alnum, dash, underscore, or dot with _
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)[:200] or "upload.bin"


def _slugify(name: str, fallback: str) -> str:
    """Convert a workspace / product display name to an S3-key-safe slug:
        "NeuZenAI"      -> "neuzenai"
        "Spenzo AI"     -> "spenzo-ai"
        "z-ninth!"      -> "z-ninth"
        ""              -> fallback (e.g. "ws-5" or "prod-1")
    Lowercase, spaces -> dashes, non-alnum stripped, trimmed.
    """
    s = (name or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9._-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-._")
    return s[:80] or fallback


def _build_s3_key(
    *,
    workspace_id: int,
    product_id: int,
    asset_id: int,
    filename: str,
    workspace_name: str = "",
    product_name: str = "",
) -> str:
    """Human-readable S3 key layout, grouped by workspace + product names.

    Layout:
        nexus-kb/{workspace-slug}/{product-slug}/asset-{id}-{filename}

    Examples:
        nexus-kb/neuzenai/spenzo-ai/asset-42-pricing.pdf
        nexus-kb/neuzenai/pipelyt/asset-55-faq.docx
        nexus-kb/neuzenai/z-ninth/asset-77-scrape.md

    Why this shape:
      - Folder per workspace makes per-tenant cleanup trivial
        (`aws s3 rm s3://bucket/nexus-kb/neuzenai/ --recursive`).
      - Folder per product means an operator browsing the bucket can
        instantly see what each product knows.
      - `asset-{id}-` prefix on the file makes the asset row id
        traceable from S3 alone, and prevents collisions when the
        same filename is uploaded twice.
      - Slugs fall back to id-based segments if names are empty, so
        the layout is never broken by missing data.
    """
    ws_slug = _slugify(workspace_name, fallback=f"ws-{int(workspace_id)}")
    prod_slug = _slugify(product_name, fallback=f"prod-{int(product_id)}")
    return (
        f"{_S3_KEY_PREFIX}/{ws_slug}/{prod_slug}"
        f"/asset-{int(asset_id)}-{_sanitize_filename(filename)}"
    )


def _lookup_workspace_product_names(
    db: Session, workspace_id: int, product_id: int
) -> tuple[str, str]:
    """Fetch the display names for a workspace + product in one query.

    Used by background tasks (where the request-scoped `workspace` /
    `product` ORM objects are unavailable) so the S3 key still uses
    human-readable slugs instead of ID-only fallbacks.

    Returns `("", "")` if either lookup fails — `_slugify` will fall
    back to id-based segments.
    """
    from sqlalchemy import text as _sql_text
    ws_name = ""
    prod_name = ""
    try:
        row = db.execute(
            _sql_text("SELECT name FROM nexus_workspaces WHERE id = :id"),
            {"id": int(workspace_id)},
        ).first()
        if row and row[0]:
            ws_name = str(row[0])
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    try:
        row = db.execute(
            _sql_text("SELECT name FROM nexus_products WHERE id = :id"),
            {"id": int(product_id)},
        ).first()
        if row and row[0]:
            prod_name = str(row[0])
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    return ws_name, prod_name


def _load_owned_product(
    db: Session, product_id: int, workspace_id: int
) -> models_phase2.NexusProduct:
    product = (
        db.query(models_phase2.NexusProduct)
        .filter(
            models_phase2.NexusProduct.id == product_id,
            models_phase2.NexusProduct.workspace_id == workspace_id,
        )
        .first()
    )
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found in this workspace.",
        )
    return product


def _upload_bytes_to_s3(
    data: bytes, key: str, content_type: str = "application/octet-stream"
) -> Optional[str]:
    """Stream-upload bytes to the dedicated NEXUS_KB_S3_BUCKET (falls back
    to S3_BUCKET_NAME if unset).

    Sets ContentType so a future presigned download URL serves the right
    MIME to the browser. Returns the public URL or None on failure —
    callers treat None as a soft failure (indexing can still proceed
    from the in-memory text).
    """
    bucket = NEXUS_KB_S3_BUCKET
    if not bucket:
        logger.warning(
            "Cannot upload knowledge file to S3 — no bucket configured. "
            "Set NEXUS_KB_S3_BUCKET or S3_BUCKET_NAME in the Lambda env."
        )
        return None
    s3 = get_s3_client()
    if not s3:
        logger.warning("Cannot upload knowledge file to S3 — boto3 client could not be initialised.")
        return None
    try:
        s3.upload_fileobj(
            io.BytesIO(data),
            bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        # Build the URL ourselves — get_s3_url() is hard-coded to the
        # legacy S3_BUCKET_NAME and would point at the wrong bucket.
        region = (_os.getenv("AWS_REGION") or "us-east-1").strip()
        url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
        logger.info("Uploaded knowledge file to s3://%s/%s", bucket, key)
        return url
    except Exception as exc:
        logger.warning(
            "S3 upload failed for bucket='%s' key='%s': %s", bucket, key, exc,
        )
        return None


# ─── Background indexing task ─────────────────────────────────────────────


def _mark_asset_failed(db: Session, asset_id: int, reason: str) -> None:
    """Helper: flip an asset row to status='failed' with a human-readable
    last_error. Used by both the file-upload and URL-scrape background
    tasks. Never raises — failures here just mean the UI won't show the
    'retry' button until the user re-uploads.
    """
    try:
        row = db.query(models_phase2.NexusProductAsset).filter(
            models_phase2.NexusProductAsset.id == asset_id
        ).first()
        if row is None:
            logger.warning("Cannot mark asset %d as failed — DB row missing", asset_id)
            return
        row.status = "failed"
        row.last_error = reason[:1000]
        db.commit()
    except Exception as exc:
        logger.error(
            "Could not mark asset %d as failed in DB (reason was: %s) — DB error: %s",
            asset_id, reason[:200], exc,
        )
        try:
            db.rollback()
        except Exception:
            pass


def _run_full_indexing_job(
    *,
    asset_id: int,
    workspace_id: int,
    product_id: int,
    workspace_name: str,
    product_name: str,
    file_bytes: bytes,
    filename: str,
    asset_type: str,
) -> None:
    """End-to-end background indexer for file uploads (Option A — full async).

    Runs in FastAPI BackgroundTasks AFTER the upload endpoint has already
    returned a 202 to the user. This means the user can navigate around
    the app freely while indexing happens.

    Steps (in order):
        1. Flip status: 'queued' → 'processing'
        2. Upload raw bytes to S3 (durable backup)
        3. Extract text via doc_processor
        4. Chunk text
        5. Embed each chunk via Gemini (3072 dim)
        6. Upsert vectors to Pinecone (namespace=ws-{workspace_id})
        7. Flip status: 'processing' → 'indexed' + chunks_indexed / indexed_at

    Any failure flips status='failed' with a plain-text last_error so the
    UI can show a retry button + tooltip explaining what went wrong.

    Uses its own DB session (the request-scoped one is closed by the time
    background tasks run).
    """
    db = SessionLocal()
    try:
        # ── 1. queued → processing ────────────────────────────────────
        try:
            row = db.query(models_phase2.NexusProductAsset).filter(
                models_phase2.NexusProductAsset.id == asset_id
            ).first()
            if row is None:
                logger.error(
                    "Indexing aborted — asset %d not found in DB (was it deleted between upload and BG task?)",
                    asset_id,
                )
                return
            row.status = "processing"
            row.last_error = None
            db.commit()
        except Exception as exc:
            logger.error(
                "Could not mark asset %d as 'processing' — aborting indexing. DB error: %s",
                asset_id, exc,
            )
            try:
                db.rollback()
            except Exception:
                pass
            return

        # ── 2 + 3. S3 upload + text extraction (in parallel) ─────────
        # These two are independent (S3 is network I/O, extraction is
        # CPU-bound on pure-Python libs). Running them concurrently in
        # threads saves ~0.5-2s per file.
        s3_key = _build_s3_key(
            workspace_id=workspace_id,
            product_id=product_id,
            asset_id=asset_id,
            filename=filename,
            workspace_name=workspace_name,
            product_name=product_name,
        )
        content_type = _CONTENT_TYPES_BY_EXT.get(
            asset_type, "application/octet-stream"
        )

        s3_url: Optional[str] = None
        text: str = ""
        extract_exc: Optional[Exception] = None

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="kb_io") as ex:
            s3_future = ex.submit(_upload_bytes_to_s3, file_bytes, s3_key, content_type)
            extract_future = ex.submit(extract_text_from_file, file_bytes, filename)

            try:
                s3_url = s3_future.result()
            except Exception as exc:
                # S3 failure is non-fatal — we can still index from
                # in-memory bytes. Just no durable copy of the file.
                logger.warning(
                    "Asset %d: S3 upload raised an exception, continuing without durable copy: %s",
                    asset_id, exc,
                )

            try:
                text = extract_future.result() or ""
            except Exception as exc:
                extract_exc = exc

        # Persist the S3 location if upload succeeded.
        if s3_url:
            try:
                row.s3_key = s3_key
                row.s3_url = s3_url
                db.commit()
            except Exception:
                db.rollback()
        else:
            logger.warning(
                "Asset %d: S3 upload failed, indexing will proceed from in-memory bytes",
                asset_id,
            )

        # Now check extraction result — fatal if it failed or empty.
        if extract_exc is not None:
            reason = f"Text extraction crashed for '{filename}': {extract_exc}"
            logger.exception("Asset %d: %s", asset_id, reason)
            _mark_asset_failed(db, asset_id, reason)
            return

        if not text or not text.strip():
            reason = (
                f"No readable text could be extracted from '{filename}'. "
                "If this is a scanned PDF, OCR is required (not yet supported)."
            )
            logger.warning("Asset %d: %s", asset_id, reason)
            _mark_asset_failed(db, asset_id, reason)
            return

        # Update char_count now that we know it.
        try:
            row.char_count = len(text)
            db.commit()
        except Exception:
            db.rollback()

        # ── 4. Chunk ──────────────────────────────────────────────────
        try:
            chunks = embedding_service.chunk_text(text)
            chunk_strs = [c.text for c in chunks]
        except Exception as exc:
            reason = f"Chunking failed for '{filename}': {exc}"
            logger.exception("Asset %d: %s", asset_id, reason)
            _mark_asset_failed(db, asset_id, reason)
            return

        if not chunk_strs:
            reason = (
                f"Document '{filename}' produced zero chunks after normalisation — "
                "the extracted text was empty or only whitespace."
            )
            logger.warning("Asset %d: %s", asset_id, reason)
            _mark_asset_failed(db, asset_id, reason)
            return

        # ── 5 + 6. Embed via Gemini + upsert to Pinecone ──────────────
        try:
            written = pinecone_kb.embed_and_upsert(
                workspace_id=workspace_id,
                product_id=product_id,
                asset_id=asset_id,
                chunks=chunk_strs,
                source_name=filename,
                asset_type=asset_type,
            )
        except Exception as exc:
            reason = f"Embedding or Pinecone upsert failed for '{filename}': {exc}"
            logger.exception("Asset %d: %s", asset_id, reason)
            _mark_asset_failed(db, asset_id, reason)
            return

        # ── 7. processing → indexed ───────────────────────────────────
        try:
            row.status = "indexed"
            row.chunks_indexed = int(written)
            row.indexed_at = datetime.utcnow()
            row.last_error = None
            db.commit()
            logger.info(
                "Knowledge base updated: asset %d indexed %d chunks (workspace=%d product=%d, source='%s')",
                asset_id, written, workspace_id, product_id, filename,
            )
        except Exception as exc:
            logger.error(
                "Asset %d: indexing succeeded in Pinecone but DB status update failed: %s",
                asset_id, exc,
            )
            try:
                db.rollback()
            except Exception:
                pass
            _mark_asset_failed(
                db, asset_id,
                f"Indexing completed but DB status update failed: {exc}",
            )
    finally:
        db.close()


# Number of files to process concurrently inside one upload batch.
# Each file's pipeline (S3 + extract + chunk + embed + Pinecone) spends
# ~80% of its time on network I/O, so threading dominates. 3 workers ×
# 5 embedding workers per file = 15 max concurrent Gemini calls,
# comfortably below Tier 1's ~16/sec ceiling. Bump to 5 once we're on
# a higher Gemini tier.
_BATCH_FILE_WORKERS = 3


def _run_batch_indexing_job(work_items: List[Dict[str, Any]]) -> None:
    """Single background task that processes many files in parallel.

    Compared to scheduling N separate BackgroundTasks (which Starlette
    runs sequentially in one Lambda invocation), this uses a thread pool
    so multiple files index simultaneously. For a 5-file batch where each
    file takes ~10s sequentially, total time drops from ~50s to ~15-20s.

    Each work item is a dict with the kwargs for `_run_full_indexing_job`.
    Per-file failures are caught + logged here; one bad file doesn't
    affect the others (the failing file's row gets marked status='failed'
    inside `_run_full_indexing_job` itself).

    Runs in FastAPI BackgroundTasks AFTER the upload response was sent.
    """
    if not work_items:
        return

    worker_count = min(_BATCH_FILE_WORKERS, len(work_items))
    logger.info(
        "Starting batch indexing: %d file(s) in parallel with %d worker(s)",
        len(work_items), worker_count,
    )

    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="kb_idx") as ex:
        futures = [ex.submit(_run_full_indexing_job, **item) for item in work_items]
        # Wait for all to finish. Each task has its own try/except inside
        # so a raised exception here is unexpected — log it but keep going.
        for fut, item in zip(futures, work_items):
            try:
                fut.result()
            except Exception as exc:
                logger.error(
                    "Batch worker raised unexpected exception for asset %s ('%s'): %s",
                    item.get("asset_id"), item.get("filename"), exc,
                )

    logger.info("Batch indexing finished: %d file(s) processed", len(work_items))


def _run_indexing_job(
    *,
    asset_id: int,
    workspace_id: int,
    product_id: int,
    raw_text: str,
    source_name: str,
    asset_type: str,
) -> None:
    """Text-only indexing background task (chunk → embed → upsert).

    Used by the /text and /url upload paths where there's no raw file
    to upload to S3 (the URL path uploads the scraped markdown
    separately before calling this). For file uploads see
    `_run_full_indexing_job` which also handles S3 upload + extraction.
    """
    db = SessionLocal()
    try:
        try:
            chunks = embedding_service.chunk_text(raw_text)
            chunk_strs = [c.text for c in chunks]
            if not chunk_strs:
                raise ValueError(
                    "Document produced zero chunks after normalisation — "
                    "the extracted text was empty or only whitespace."
                )

            written = pinecone_kb.embed_and_upsert(
                workspace_id=workspace_id,
                product_id=product_id,
                asset_id=asset_id,
                chunks=chunk_strs,
                source_name=source_name,
                asset_type=asset_type,
            )

            row = db.query(models_phase2.NexusProductAsset).filter(
                models_phase2.NexusProductAsset.id == asset_id
            ).first()
            if row is not None:
                row.status = "indexed"
                row.chunks_indexed = int(written)
                row.indexed_at = datetime.utcnow()
                row.last_error = None
                db.commit()
            logger.info(
                "Knowledge base updated: asset %d indexed %d chunks (workspace=%d product=%d)",
                asset_id, written, workspace_id, product_id,
            )
        except Exception as exc:
            logger.exception(
                "Indexing failed for asset %d (workspace=%d product=%d): %s",
                asset_id, workspace_id, product_id, exc,
            )
            try:
                db.rollback()
            except Exception:
                pass
            _mark_asset_failed(db, asset_id, str(exc))
    finally:
        db.close()


def _run_url_scrape_then_index(
    *,
    asset_id: int,
    workspace_id: int,
    product_id: int,
    url: str,
) -> None:
    """Background task for /url uploads. Scrapes first (slow — up to 45s
    on Playwright fallback), uploads the raw scraped HTML/markdown to S3
    (using human-readable workspace/product slugs), then runs the standard
    index path.
    """
    db = SessionLocal()
    text: str = ""
    try:
        try:
            logger.info(
                "Scraping URL for asset %d (workspace=%d product=%d): %s",
                asset_id, workspace_id, product_id, url,
            )
            # Scrape (Jina → Playwright → httpx chain)
            scrape = asyncio.run(
                playwright_scraper.fetch_html(url, retries=2, total_timeout=45.0)
            )
            text = (scrape.text or "").strip()
            if not text:
                raise ValueError(
                    f"Scrape returned no readable text for {url}. The page may "
                    "be JavaScript-only or behind a login wall."
                )

            # Look up names so the S3 path is human-readable. Safe inside
            # the background task — this DB session is fresh (the
            # request-scoped one is already closed).
            ws_name, prod_name = _lookup_workspace_product_names(db, workspace_id, product_id)

            # Persist the scraped content to S3 so we can re-chunk later
            # without re-scraping (Playwright fetches can be flaky).
            s3_key = _build_s3_key(
                workspace_id=workspace_id,
                product_id=product_id,
                asset_id=asset_id,
                filename="scrape.md",
                workspace_name=ws_name,
                product_name=prod_name,
            )
            s3_url = _upload_bytes_to_s3(
                text.encode("utf-8"),
                s3_key,
                "text/markdown; charset=utf-8",
            )

            row = db.query(models_phase2.NexusProductAsset).filter(
                models_phase2.NexusProductAsset.id == asset_id
            ).first()
            if row is not None:
                row.s3_key = s3_key
                row.s3_url = s3_url or row.s3_url
                row.char_count = len(text)
                db.commit()
        except Exception as exc:
            logger.exception(
                "URL scrape failed for asset %d (url=%s): %s",
                asset_id, url, exc,
            )
            row = db.query(models_phase2.NexusProductAsset).filter(
                models_phase2.NexusProductAsset.id == asset_id
            ).first()
            if row is not None:
                row.status = "failed"
                row.last_error = f"Scrape failed: {exc}"[:1000]
                try:
                    db.commit()
                except Exception as commit_exc:
                    logger.error(
                        "Could not mark asset %d as failed after scrape error: %s",
                        asset_id, commit_exc,
                    )
                    db.rollback()
            return
    finally:
        db.close()

    # Hand off to the standard indexing job
    _run_indexing_job(
        asset_id=asset_id,
        workspace_id=workspace_id,
        product_id=product_id,
        raw_text=text,
        source_name=url,
        asset_type="url",
    )


# ─── File upload ────────────────────────────────────────────────────────────


# Content-type map used when uploading the raw bytes to S3 (so a
# future presigned download URL serves the right MIME type to browsers).
_CONTENT_TYPES_BY_EXT = {
    "pdf":  "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv":  "text/csv",
    "txt":  "text/plain; charset=utf-8",
    "md":   "text/markdown; charset=utf-8",
}


async def _process_one_uploaded_file(
    *,
    file: UploadFile,
    product: models_phase2.NexusProduct,
    workspace,
    db: Session,
) -> tuple[KBMultiUploadItem, Optional[Dict[str, Any]]]:
    """Validate + queue a single file from a multi-file batch.

    Option A (full-async) — the request thread does the absolute minimum:
      1. Validate extension + size
      2. Read raw bytes (already in memory by this point)
      3. Insert a lightweight DB row with status='queued'
      4. Return both:
           - the KBMultiUploadItem to put in the HTTP response, AND
           - the work-item dict to hand to the batched parallel BG task
             (None if the file was rejected — nothing to schedule).

    All slow work (S3 upload, text extraction, chunking, embedding,
    Pinecone upsert) runs LATER in `_run_batch_indexing_job` AFTER the
    response is sent. The user can navigate anywhere in the app while
    indexing happens.

    Never raises — one bad file in a batch shouldn't break the rest.
    """
    filename = file.filename or "upload.bin"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # ── Validation: extension ────────────────────────────────────────
    if ext not in _ALLOWED_EXTS:
        msg = (
            f"Unsupported file type '.{ext}'. "
            f"Allowed types: {', '.join(sorted(_ALLOWED_EXTS))}."
        )
        logger.warning("Rejecting upload '%s': %s", filename, msg)
        return KBMultiUploadItem(filename=filename, accepted=False, error=msg), None

    # ── Read bytes + size check ──────────────────────────────────────
    try:
        body = await file.read()
    except Exception as exc:
        msg = f"Could not read uploaded file: {exc}"
        logger.warning("Rejecting upload '%s': %s", filename, msg)
        return KBMultiUploadItem(filename=filename, accepted=False, error=msg), None

    if not body:
        msg = "File is empty (0 bytes)."
        logger.warning("Rejecting upload '%s': %s", filename, msg)
        return KBMultiUploadItem(filename=filename, accepted=False, error=msg), None

    if len(body) > MAX_FILE_BYTES:
        msg = (
            f"File too large ({len(body) // (1024 * 1024)} MB). "
            f"Maximum allowed is {MAX_FILE_BYTES // (1024 * 1024)} MB."
        )
        logger.warning("Rejecting upload '%s': %s", filename, msg)
        return KBMultiUploadItem(filename=filename, accepted=False, error=msg), None

    asset_type = ext

    # ── Insert lightweight DB row (status='queued') ──────────────────
    # All heavy work happens later in `_run_batch_indexing_job` (which
    # internally calls `_run_full_indexing_job` per file in parallel).
    try:
        asset = models_phase2.NexusProductAsset(
            product_id=product.id,
            workspace_id=workspace.id,
            asset_type=asset_type,
            source=filename,
            char_count=0,           # filled in by background task after extraction
            status="queued",
            chunks_indexed=0,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
    except Exception as exc:
        msg = f"Could not create asset record in DB: {exc}"
        logger.error("Rejecting upload '%s': %s", filename, msg)
        try:
            db.rollback()
        except Exception:
            pass
        return KBMultiUploadItem(filename=filename, accepted=False, error=msg), None

    work_item: Dict[str, Any] = {
        "asset_id":       asset.id,
        "workspace_id":   workspace.id,
        "product_id":     product.id,
        "workspace_name": getattr(workspace, "name", "") or "",
        "product_name":   product.name or "",
        "file_bytes":     body,
        "filename":       filename,
        "asset_type":     asset_type,
    }

    logger.info(
        "Queued upload '%s' as asset %d (workspace=%d product=%d, %d bytes, type=%s)",
        filename, asset.id, workspace.id, product.id, len(body), asset_type,
    )
    item = KBMultiUploadItem(
        filename=filename,
        accepted=True,
        asset=ProductAssetOut.model_validate(asset),
        chars_extracted=0,   # extraction happens in background
    )
    return item, work_item


@router.post(
    "/upload",
    response_model=KBMultiUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_kb_files(
    background_tasks: BackgroundTasks,
    product_id: int = Form(...),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
    _trial=Depends(trial_guard),
) -> KBMultiUploadResponse:
    """Upload one or many knowledge documents in a single request.

    Supports any mix of supported formats (pdf / docx / pptx / xlsx /
    csv / txt / md) — drag-and-drop UX like Claude / Perplexity / Gemini.

    Behaviour:
      - Each file goes through its own validation + S3 upload + indexing
        pipeline. One bad file doesn't abort the others (partial success).
      - Heavy work (chunk + embed + Pinecone upsert) runs in
        BackgroundTasks AFTER the response is returned. The endpoint
        responds in ~1-2 s even for 20 files.
      - The UI polls `GET /nexus/kb/asset/{id}/status` for each accepted
        asset until status='indexed' (or 'failed').

    Batch limits:
      - Max {MAX_FILES_PER_REQUEST} files per request
      - Max {MAX_FILE_BYTES // 1024 // 1024} MB per file
      - Max {MAX_TOTAL_BYTES_PER_REQUEST // 1024 // 1024} MB total per request

    Response: 202 Accepted with a KBMultiUploadResponse containing one
    item per file. Per-item `accepted=True` means the file is queued;
    `accepted=False` with `error` filled means it was rejected up-front.
    """
    # Outer validation — total file count + total batch size.
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files were attached to the upload request.",
        )
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Too many files in one upload ({len(files)}). "
                f"Maximum is {MAX_FILES_PER_REQUEST} per request — please split into batches."
            ),
        )

    product = _load_owned_product(db, product_id, workspace.id)

    logger.info(
        "KB upload batch starting: workspace=%d product=%d ('%s'), %d file(s) attached",
        workspace.id, product.id, product.name, len(files),
    )

    # Process each file independently. We validate + create the DB row
    # synchronously (fast), and collect a work-item dict for each accepted
    # file. After the loop, ALL work items are passed to one parallel
    # background task — files index concurrently in threads.
    items: List[KBMultiUploadItem] = []
    work_items: List[Dict[str, Any]] = []
    running_total_bytes = 0
    for file in files:
        # Soft guardrail using accumulated raw bytes from previously
        # accepted files in this batch. Prevents one user from queuing
        # a 500MB batch in a single Lambda invocation.
        if running_total_bytes >= MAX_TOTAL_BYTES_PER_REQUEST:
            items.append(KBMultiUploadItem(
                filename=file.filename or "unknown",
                accepted=False,
                error=(
                    f"Batch size limit reached "
                    f"({MAX_TOTAL_BYTES_PER_REQUEST // 1024 // 1024} MB total). "
                    "This file was skipped — please upload it in a separate batch."
                ),
            ))
            continue

        item, work_item = await _process_one_uploaded_file(
            file=file,
            product=product,
            workspace=workspace,
            db=db,
        )
        items.append(item)
        if work_item is not None:
            work_items.append(work_item)
            # Track approximate batch size from the actual file bytes.
            running_total_bytes += len(work_item["file_bytes"])

    # Schedule ONE batch background task that processes all accepted
    # files in parallel (using a thread pool). This is much faster than
    # scheduling N separate FastAPI BackgroundTasks, which would run
    # sequentially in the same Lambda invocation.
    if work_items:
        background_tasks.add_task(_run_batch_indexing_job, work_items)

    accepted = sum(1 for i in items if i.accepted)
    rejected = len(items) - accepted

    logger.info(
        "KB upload batch finished: workspace=%d product=%d, accepted=%d, rejected=%d, total=%d "
        "(parallel indexing scheduled for %d file(s))",
        workspace.id, product.id, accepted, rejected, len(items), len(work_items),
    )

    return KBMultiUploadResponse(
        items=items,
        accepted_count=accepted,
        rejected_count=rejected,
        total_received=len(items),
    )


# ─── URL upload ─────────────────────────────────────────────────────────────


@router.post("/url", response_model=KBUploadResponse, status_code=status.HTTP_202_ACCEPTED)
def upload_kb_url(
    payload: KBUrlRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
    _trial=Depends(trial_guard),
):
    """Index a URL's content. Scraping happens entirely in the background
    task (it can take up to 45 s with the Playwright fallback). Returns
    immediately with status='processing'.
    """
    product = _load_owned_product(db, payload.product_id, workspace.id)

    url_str = str(payload.url)

    asset = models_phase2.NexusProductAsset(
        product_id=product.id,
        workspace_id=workspace.id,
        asset_type="url",
        source=url_str,
        char_count=0,
        status="processing",
        chunks_indexed=0,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    background_tasks.add_task(
        _run_url_scrape_then_index,
        asset_id=asset.id,
        workspace_id=workspace.id,
        product_id=product.id,
        url=url_str,
    )

    return KBUploadResponse(
        asset=asset,  # type: ignore[arg-type]
        chunks_indexed=0,
        chars_extracted=0,
    )


# ─── Raw pasted text ────────────────────────────────────────────────────────


@router.post("/text", response_model=KBUploadResponse, status_code=status.HTTP_202_ACCEPTED)
def upload_kb_text(
    payload: KBTextRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
    _trial=Depends(trial_guard),
):
    """Index pasted text. Stores a .txt blob in S3 for durability, then
    runs the standard indexing path in the background.
    """
    product = _load_owned_product(db, payload.product_id, workspace.id)

    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="`text` must not be empty.",
        )

    title = payload.title or "pasted-text"

    asset = models_phase2.NexusProductAsset(
        product_id=product.id,
        workspace_id=workspace.id,
        asset_type="text",
        source=title,
        char_count=len(text),
        status="processing",
        chunks_indexed=0,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    # Persist to S3 as a .txt for durability + future re-indexing. Use the
    # human-readable workspace+product slug layout.
    s3_key = _build_s3_key(
        workspace_id=workspace.id,
        product_id=product.id,
        asset_id=asset.id,
        filename=f"{_sanitize_filename(title)}.txt",
        workspace_name=getattr(workspace, "name", "") or "",
        product_name=product.name or "",
    )
    s3_url = _upload_bytes_to_s3(text.encode("utf-8"), s3_key, "text/plain; charset=utf-8")
    if s3_url:
        asset.s3_key = s3_key
        asset.s3_url = s3_url
        db.commit()

    background_tasks.add_task(
        _run_indexing_job,
        asset_id=asset.id,
        workspace_id=workspace.id,
        product_id=product.id,
        raw_text=text,
        source_name=title,
        asset_type="text",
    )

    return KBUploadResponse(
        asset=asset,  # type: ignore[arg-type]
        chunks_indexed=0,
        chars_extracted=len(text),
    )


# ─── Status polling + listing ───────────────────────────────────────────────


@router.get("/{product_id}", response_model=List[ProductAssetOut])
def list_kb_assets(
    product_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """List every KB asset attached to a product. Returns processing,
    indexed, and failed rows so the UI can render status badges."""
    product = _load_owned_product(db, product_id, workspace.id)
    rows = (
        db.query(models_phase2.NexusProductAsset)
        .filter(models_phase2.NexusProductAsset.product_id == product.id)
        .order_by(models_phase2.NexusProductAsset.created_at.desc())
        .all()
    )
    return rows


@router.get("/asset/{asset_id}/status", response_model=KBAssetStatusOut)
def get_kb_asset_status(
    asset_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
) -> KBAssetStatusOut:
    """Poll endpoint the UI hits after upload to know when indexing
    completes. Returns the minimal fields needed for the toast/banner.

    Workspace-scoped — an asset belonging to a different workspace
    returns 404 even if the caller knows the id.
    """
    asset = (
        db.query(models_phase2.NexusProductAsset)
        .filter(
            models_phase2.NexusProductAsset.id == asset_id,
            models_phase2.NexusProductAsset.workspace_id == workspace.id,
        )
        .first()
    )
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base asset not found in this workspace.",
        )
    return KBAssetStatusOut.model_validate(asset)


# ─── Delete ─────────────────────────────────────────────────────────────────


@router.delete("/asset/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_kb_asset(
    asset_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Hard-delete an asset: removes Pinecone vectors, S3 object, and DB row.
    Best-effort on the S3 + Pinecone deletes — DB row always removed."""
    asset = (
        db.query(models_phase2.NexusProductAsset)
        .filter(
            models_phase2.NexusProductAsset.id == asset_id,
            models_phase2.NexusProductAsset.workspace_id == workspace.id,
        )
        .first()
    )
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base asset not found in this workspace.",
        )

    # 1. Remove Pinecone vectors (best-effort — never blocks the DB delete).
    try:
        pinecone_kb.delete_asset(workspace_id=workspace.id, asset_id=asset.id)
    except Exception as exc:
        logger.warning(
            "Could not remove Pinecone vectors for asset %d (workspace=%d): %s",
            asset_id, workspace.id, exc,
        )

    # 2. Remove S3 object (best-effort — orphan files cost pennies, not worth aborting).
    if asset.s3_key:
        try:
            s3 = get_s3_client()
            if s3 and NEXUS_KB_S3_BUCKET:
                s3.delete_object(Bucket=NEXUS_KB_S3_BUCKET, Key=asset.s3_key)
                logger.info(
                    "Removed S3 object s3://%s/%s for asset %d",
                    NEXUS_KB_S3_BUCKET, asset.s3_key, asset_id,
                )
            else:
                logger.warning(
                    "Skipping S3 delete for asset %d — no S3 client or bucket configured",
                    asset_id,
                )
        except Exception as exc:
            logger.warning(
                "S3 delete failed for asset %d (bucket=%s key=%s): %s",
                asset_id, NEXUS_KB_S3_BUCKET, asset.s3_key, exc,
            )

    # 3. Remove DB row (authoritative — once this commits, the asset is gone
    # from the operator's view regardless of S3/Pinecone state).
    db.delete(asset)
    db.commit()
    logger.info("Deleted knowledge base asset %d (workspace=%d)", asset_id, workspace.id)
    return None
