"""One-shot backfill: render slide-1 PNG for every existing PDF carousel
row and populate `thumbnail_url` on Draft and PublishedPost.

Why this exists: carousels posted before the `thumbnail_url` column was
added show as the ugly "PDF + carousel_<uuid>.pdf" tile on the Published
feed and Drafts page. This script downloads each PDF from S3, renders
page 1 to PNG via PyMuPDF, uploads it as a separate S3 object, and writes
the URL onto the row.

Idempotent: skips any row that already has a thumbnail_url. Safe to
re-run. Best-effort: rows whose PDFs fail to download/render are logged
and skipped — the rest still get fixed.

Run:
    cd apps/backend
    python -m scripts.backfill_carousel_thumbnails
"""
from __future__ import annotations

import logging
import sys
import time
import uuid
from io import BytesIO

# Bootstrap so this script can run from `apps/backend` directly
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

import fitz  # PyMuPDF
import requests
from sqlalchemy.orm import Session

from core.database import SessionLocal
from core.s3_utils import S3_BUCKET_NAME, get_s3_client, get_s3_url
from models import Draft, PublishedPost

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill")


def _is_pdf_carousel(row) -> bool:
    """Pick out rows that look like carousel PDFs (media_type=document OR
    image_url ending in .pdf in our carousel S3 path)."""
    if not row.image_url:
        return False
    url = row.image_url.lower()
    if (row.media_type or "").lower() == "document":
        return True
    return ".pdf" in url and "/ai_gen/carousel/" in url


def _render_page1_to_png(pdf_bytes: bytes, target_width: int = 1024) -> bytes:
    """Render PDF page 1 to a PNG. Scales to ~target_width while preserving
    aspect ratio. Returns PNG bytes."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        if doc.page_count == 0:
            raise RuntimeError("PDF has no pages")
        page = doc.load_page(0)
        zoom = target_width / page.rect.width
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()


def _render_all_pages_to_png(pdf_bytes: bytes, target_width: int = 1024) -> list[bytes]:
    """Render ALL pages of a PDF to PNG bytes (in order). Used for full
    carousel modal navigation."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out: list[bytes] = []
    try:
        for i in range(doc.page_count):
            page = doc.load_page(i)
            zoom = target_width / page.rect.width
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            out.append(pix.tobytes("png"))
        return out
    finally:
        doc.close()


def _upload_png(png_bytes: bytes) -> str:
    s3 = get_s3_client()
    if not s3 or not S3_BUCKET_NAME:
        raise RuntimeError("S3 not configured — check AWS env vars")
    key = f"ai_gen/carousel/slide_1_backfill_{uuid.uuid4().hex}.png"
    s3.upload_fileobj(
        BytesIO(png_bytes),
        S3_BUCKET_NAME,
        key,
        ExtraArgs={"ContentType": "image/png"},
    )
    return get_s3_url(key)


def _backfill_row(row, db: Session) -> str:
    """Returns one of: 'fixed', 'skipped', 'error'.

    Renders ALL pages of the PDF, uploads each as a separate PNG to S3,
    stores slide 1's URL on `thumbnail_url` (the card cover) AND the full
    ordered list of slide URLs on `slide_thumbnail_urls` (modal nav)."""
    has_thumb = bool(getattr(row, "thumbnail_url", None))
    has_slides = bool(getattr(row, "slide_thumbnail_urls", None))
    if has_thumb and has_slides:
        return "skipped"
    if not _is_pdf_carousel(row):
        return "skipped"
    try:
        resp = requests.get(row.image_url, timeout=60)
        resp.raise_for_status()
        pdf_bytes = resp.content
        page_pngs = _render_all_pages_to_png(pdf_bytes, target_width=1024)
        slide_urls = []
        for png in page_pngs:
            slide_urls.append(_upload_png(png))
        # thumbnail_url = slide 1 (kept for backwards-compat with cards
        # that still read this field). slide_thumbnail_urls = full list.
        row.thumbnail_url = slide_urls[0]
        if hasattr(row, "slide_thumbnail_urls"):
            row.slide_thumbnail_urls = __import__("json").dumps(slide_urls)
        db.commit()
        logger.info(
            "%s id=%s -> %d slides backfilled (pdf=%s bytes)",
            type(row).__name__, row.id, len(slide_urls), len(pdf_bytes),
        )
        return "fixed"
    except Exception as exc:
        db.rollback()
        logger.warning("%s id=%s failed: %s", type(row).__name__, row.id, exc)
        return "error"


def main() -> None:
    db: Session = SessionLocal()
    started = time.monotonic()
    try:
        for Model in (Draft, PublishedPost):
            # Pick up rows missing EITHER thumbnail_url OR (for Draft only)
            # slide_thumbnail_urls. Previously-backfilled rows that only
            # got slide 1 are re-queued so they pick up all slides.
            from sqlalchemy import or_
            q = db.query(Model).filter(Model.image_url.isnot(None))
            if hasattr(Model, "slide_thumbnail_urls"):
                q = q.filter(or_(
                    Model.thumbnail_url.is_(None),
                    Model.slide_thumbnail_urls.is_(None),
                ))
            else:
                q = q.filter(Model.thumbnail_url.is_(None))
            rows = q.all()
            logger.info("=== %s: %d candidate rows (no thumbnail_url) ===", Model.__name__, len(rows))
            fixed = errored = skipped = 0
            for r in rows:
                outcome = _backfill_row(r, db)
                if outcome == "fixed":
                    fixed += 1
                elif outcome == "error":
                    errored += 1
                else:
                    skipped += 1
            logger.info(
                "%s done: fixed=%d skipped=%d errored=%d",
                Model.__name__, fixed, skipped, errored,
            )
    finally:
        db.close()
    logger.info("Backfill complete in %.1fs", time.monotonic() - started)


if __name__ == "__main__":
    main()
