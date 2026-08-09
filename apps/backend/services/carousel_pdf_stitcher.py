"""Carousel PDF stitcher.

Takes N PNG image bytes (one per carousel slide) and composes them into a
single PDF whose pages are sized to match the chosen carousel aspect.

LinkedIn renders the resulting PDF as a swipeable carousel in-feed. Each
PDF page = one slide.

reportlab is already in apps/backend/requirements.txt - no new dep.

Key choices:
  - Page dimensions match the slide image exactly (no whitespace borders).
  - Each PNG is rendered at the full page size so the image IS the slide.
  - PDF version stays at reportlab's default; LinkedIn accepts it.
  - We do NOT re-render the headline text in PDF text-layer because the
    image already contains the rendered headline. Re-rendering would
    cause visible duplication.

Usage:
    pdf_bytes = build_carousel_pdf(slide_pngs, pdf_title="My Carousel")
"""
from __future__ import annotations

import logging
from io import BytesIO

from reportlab.lib.pagesizes import landscape   # noqa: F401  (kept for callers)
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

logger = logging.getLogger("pipelyt.carousel_pdf")

# Slide dimensions in PDF points (1 pt = 1/72 inch). gpt-image-2 returns
# 1024x1024 PNGs at quality=high for 1:1 aspect; we map directly to PDF
# page size so the image fills the page edge-to-edge.
ASPECT_TO_PDF_POINTS = {
    "1:1":  (1024.0, 1024.0),
    "4:5":  (1024.0, 1280.0),
    "16:9": (1792.0, 1024.0),
    "9:16": (1024.0, 1792.0),
}
DEFAULT_PAGE_SIZE = (1024.0, 1024.0)   # 1:1


def build_carousel_pdf(
    slide_pngs: list[bytes],
    *,
    pdf_title: str = "",
    aspect_ratio: str = "1:1",
) -> bytes:
    """Build a multi-page PDF from a list of slide PNGs. Returns PDF bytes.

    Each page = one slide. Page size matches the chosen aspect.
    """
    if not slide_pngs:
        raise ValueError("build_carousel_pdf needs at least 1 slide PNG")

    page_w, page_h = ASPECT_TO_PDF_POINTS.get(aspect_ratio, DEFAULT_PAGE_SIZE)

    buf = BytesIO()
    canvas = Canvas(buf, pagesize=(page_w, page_h))

    # PDF /Title metadata - LinkedIn surfaces this under the carousel in
    # some places. Keep it readable in PDF reader sidebars.
    if pdf_title:
        canvas.setTitle(pdf_title)
    canvas.setCreator("Pipelyt - AI Carousel Pipeline")
    canvas.setAuthor("Pipelyt")

    for i, png_bytes in enumerate(slide_pngs, 1):
        img = ImageReader(BytesIO(png_bytes))
        # Edge-to-edge - the image IS the slide. No padding so the
        # rendered text on the image stays at the model's intended scale.
        canvas.drawImage(
            img,
            x=0,
            y=0,
            width=page_w,
            height=page_h,
            preserveAspectRatio=False,
            mask="auto",
        )
        canvas.showPage()

    canvas.save()
    pdf_bytes = buf.getvalue()
    logger.info(
        f"[carousel_pdf] built {len(slide_pngs)} slides into {len(pdf_bytes):,} bytes "
        f"page={int(page_w)}x{int(page_h)} aspect={aspect_ratio} title={pdf_title!r}"
    )
    return pdf_bytes
