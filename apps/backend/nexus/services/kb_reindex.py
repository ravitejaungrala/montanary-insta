"""Backfill — re-index a product's KB from its website URL: multi-page + deduped.

Existing products were indexed with the HOMEPAGE only, and re-analysis appended a
new homepage asset each run (one product accumulated 11 near-identical copies).
This re-fetches the full crawl bundle (homepage + signal-rich subpages via the
existing `fetch_bundle`), purges the product's prior URL assets + their Pinecone
vectors, and re-embeds the whole bundle under ONE fresh asset.

Idempotent + best-effort: re-running converges to a single deep, deduped asset.
Used by a one-off backfill for products bloated under the old behaviour; the
live `/analyze` path already applies the same subpage-index + purge logic inline.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict

from sqlalchemy.orm import Session

log = logging.getLogger("nexus.services.kb_reindex")


def _run(coro):
    """Run an async coroutine whether or not a loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


def reindex_product_url_kb(db: Session, *, product_id: int, max_subpages: int = 6) -> Dict[str, Any]:
    from nexus import models_phase2
    from nexus.services import playwright_scraper, pinecone_kb
    from nexus.services import embedding as embedding_service

    product = (
        db.query(models_phase2.NexusProduct)
        .filter(models_phase2.NexusProduct.id == product_id)
        .first()
    )
    if not product or not (product.source_url or "").strip():
        return {"ok": False, "reason": "no_product_or_url"}
    url = product.source_url
    ws = product.workspace_id

    # 1. Crawl homepage + subpages.
    try:
        bundle = _run(playwright_scraper.fetch_bundle(url, retries=2))
    except Exception as exc:  # noqa: BLE001
        log.warning("reindex: crawl failed for product %s (%s): %s", product_id, url, exc)
        return {"ok": False, "reason": "crawl_failed"}
    hp = getattr(bundle, "homepage", None)
    hp_text = (getattr(hp, "text", "") or "").strip() if hp else ""
    subpages = [
        (getattr(sp, "text", "") or "").strip()
        for sp in (getattr(bundle, "subpages", []) or [])
    ]
    subpages = [s for s in subpages if s]
    if not hp_text and not subpages:
        return {"ok": False, "reason": "empty_crawl"}

    # 2. Purge the product's prior URL assets + their Pinecone vectors.
    old = (
        db.query(models_phase2.NexusProductAsset)
        .filter(
            models_phase2.NexusProductAsset.product_id == product_id,
            models_phase2.NexusProductAsset.asset_type == "url",
        )
        .all()
    )
    for oa in old:
        try:
            pinecone_kb.delete_asset(workspace_id=ws, asset_id=oa.id)
        except Exception:  # noqa: BLE001
            pass
        db.delete(oa)
    db.flush()

    # 3. Compose homepage + subpages + product summary into one fresh asset.
    parts = []
    name = (product.name or "").strip()
    vp = (product.value_proposition or "").strip()
    if name and vp:
        parts.append("PRODUCT: %s\n%s" % (name, vp))
    elif vp:
        parts.append(vp)
    if hp_text:
        parts.append(hp_text[:50000])
    for s in subpages[:max_subpages]:
        parts.append(s[:20000])
    text = "\n\n".join(parts)

    asset = models_phase2.NexusProductAsset(
        product_id=product_id,
        workspace_id=ws,
        asset_type="url",
        source=url,
        char_count=len(text),
        status="processing",
        chunks_indexed=0,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    # 4. Embed the deep, deduped bundle.
    n = embedding_service.embed_chunks_and_save(
        db,
        product_id=product_id,
        asset_id=asset.id,
        text=text,
        workspace_id=ws,
        source_name=url,
        asset_type="url",
    )
    asset.status = "indexed"
    asset.chunks_indexed = int(n)
    asset.indexed_at = datetime.utcnow()
    db.commit()
    return {
        "ok": True,
        "removed_old_assets": len(old),
        "subpages_indexed": len(subpages[:max_subpages]),
        "chunks": int(n),
        "chars": len(text),
    }
