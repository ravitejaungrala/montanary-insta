"""
POST /nexus/analyze — scrape + LLM extract + persist a NexusProduct, then
auto-create a NexusCampaign and trigger a synchronous discovery pass.

Flow:
  1. Validate workspace ownership (nexus.deps.require_current_workspace).
  2. trial_guard.
  3. Scrape the URL via playwright_scraper.fetch_html (60s budget).
  4. Hand the scraped text to gemini.analyze_product.
  5. Persist a NexusProduct + NexusProductAsset[url] row.
  6. Optionally embed user-supplied knowledge_base text.
  7. Create a NexusCampaign (status='active') seeded with the extracted ICP.
  8. Run services.discover_for_campaign synchronously, capped at 30s.
     - On timeout: campaign still exists; leads can be re-discovered later
       via /nexus/leads/autonomous or /nexus/leads/accurate.


The legacy NEXUS flow used Mongo + setImmediate to trigger discovery
asynchronously. Lambda has no background worker model, so we block the
HTTP response until the discovery pass either completes or times out.
"""

from __future__ import annotations

import json as _json

import asyncio
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.database import get_db
from models import User
from nexus.deps import (  # type: ignore[import-not-found]
    get_nexus_user,
    require_current_workspace,
    trial_guard,
)
from nexus import models_phase2
from nexus.models_phase3 import NexusCampaign, NexusCampaignLaunch
from nexus.schemas_phase2 import (
    AnalyzeCampaignSummary,
    AnalyzeDiscoverySummary,
    AnalyzeRequest,
    AnalyzeResponse,
)
from nexus.services import embedding as embedding_service
from nexus.services import gemini
from nexus.services import playwright_scraper
from nexus.services.discover_for_campaign import discover_for_campaign

logger = logging.getLogger("pipelyt.nexus.analyze")

router = APIRouter(prefix="/nexus/analyze", tags=["Nexus — Analyze"])

# 2026-05-29 — Removed `DISCOVERY_TIMEOUT_SEC` ceiling entirely.
# Background:
#   - The 30s value was set when /analyze ran behind API Gateway
#     (which has a 29s ceiling). We've since moved to Lambda Function URL
#     (no API Gateway), so the only real ceiling is Lambda's own
#     function timeout (set in IaC — currently 14 min).
#   - Discovery now runs to NATURAL completion — Apollo exhausted,
#     20-lead target hit, or adaptive yield-stop fires. No artificial
#     wall-clock cap.
#
# If you need a defensive ceiling later, set it via the Lambda
# function timeout — not in code. Code-side asyncio.wait_for is gone.

# Per-run discovery target locked at 20 (2026-05-29). User-visible
# guarantee — every Launch tries to land 20 leads in the new campaign.
# Apollo's product-level pre-dedup + adaptive yield stop ensure we
# never burn credits chasing the 20th lead when only 5 fresh ones
# exist for the ICP. Will be made configurable from the UI later
# (planned slider on the targeting page).
# 2026-06-04: reverted to 20 (the production target) now that the signal-agent
# gate is validated — every run aims to land 20 leads.
DISCOVERY_MAX_LEADS = 20
# Score floor for leads to be enrolled. 40 was filtering out everything
# that wasn't a near-perfect ICP match. User wants leads to actually
# appear in GTM Journey regardless of strength — drop the floor.
# The icp_score is still computed and stored on each lead so quality
# ranking is preserved; we just don't reject sub-40 ones outright.
DISCOVERY_MIN_ICP_SCORE = 0


class _ContentResult:
    """Scrape-result stand-in for the no-URL (paste/upload) path. Carries the
    user's raw content as `.text` so the SAME analyze_product() call + the
    asset/Pinecone-index code work unchanged. No truncation — the full content
    flows through (the model's own context window is the only ceiling)."""

    def __init__(self, text):
        self.text = text or ""
        self.char_count = len(self.text)
        self.title = ""
        self.meta_description = ""
        self.h1s = []
        self.h2s = []


@router.post("", response_model=AnalyzeResponse, status_code=status.HTTP_201_CREATED)
async def analyze_product(
    payload: AnalyzeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
    _trial=Depends(trial_guard),
):
    """Scrape a URL, extract an ICP, and kick off lead discovery.

    NOTE: this handler is `async def` because Mangum (FastAPI→Lambda
    adapter) runs FastAPI inside an already-running event loop. Calling
    `asyncio.run()` from inside that loop raises
    `RuntimeError: cannot be called from a running event loop` and the
    request 500s. Using `await` instead avoids that entirely. In dev under
    plain uvicorn either pattern works; the async form is the one Mangum
    accepts.
    """

    if payload.workspace_id != workspace.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="workspace_id does not match the active workspace.",
        )

    # 2026-05-29 — Top-level /analyze wall-clock. Individual stages
    # (scraping, product_desc, leads_discov, email_gen) emit their own
    # TIMING lines; this one captures the union.
    import time as _t
    _analyze_started = _t.monotonic()

    def _fmt_mmss(sec: float) -> str:
        sec = max(0.0, float(sec))
        m = int(sec // 60)
        return f"{m:02d}:{sec - m * 60:05.2f}"

    # ── 0. Entry mode: URL (scrape) vs CONTENT (paste/upload, no website) ──
    # When `content` is provided the user has no URL — skip scraping entirely
    # and feed their raw content straight into the SAME Gemini extraction. No
    # char limit. The frontend skips the product-description page for this path.
    content_mode = bool((payload.content or "").strip())
    if content_mode:
        # No website — feed the raw pasted/uploaded content straight into the
        # SAME Gemini extraction. No scrape, no char limit.
        url_str = None
        scrape_result = _ContentResult(payload.content)
        bundle_for_gemini = scrape_result
        logger.info(
            "analyze: CONTENT mode (no URL) — %d chars of pasted/uploaded content",
            scrape_result.char_count,
        )
    else:
        url_str = str(payload.url)

        # ── 1. Scrape (homepage + signal-rich subpages) ──────────────────
        # fetch_bundle gives Gemini a STRUCTURED view of the whole site
        # (homepage + /services + /industries + …); homepage-only on subpage
        # discovery failure. Reuses the warm /scrape-preview cache (mem + the
        # cross-process nexus_scrape_cache via db=) when available.
        try:
            scrape_bundle = playwright_scraper.get_cached_bundle(url_str, db=db)
            if scrape_bundle is not None:
                logger.info(
                    "[TIMING] Scrape cache HIT — reusing the /scrape-preview "
                    "bundle for %s (no re-scrape)", url_str,
                )
            else:
                scrape_bundle = await playwright_scraper.fetch_bundle(
                    url_str, retries=3
                )
                playwright_scraper.cache_bundle(url_str, scrape_bundle, db=db)
        except Exception as exc:
            logger.exception("Scrape failed for %s: %s", url_str, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Failed to scrape product URL: {exc}",
            ) from exc

        scrape_result = scrape_bundle.homepage
        if not scrape_result or not scrape_result.text:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The page returned no extractable text. Try a different URL.",
            )
        bundle_for_gemini = scrape_bundle
        logger.info(
            "scrape bundle for /analyze url=%s homepage_chars=%d subpages=%d "
            "(sources=%s)",
            url_str, scrape_result.char_count,
            len(scrape_bundle.subpages),
            scrape_bundle.sources,
        )

    # ── 2. LLM extract ───────────────────────────────────────────────
    # 2026-05-29: pass the full ScrapeBundle (homepage + subpages) so
    # the Gemini prompt can ground claims against /services /industries
    # /customers etc. — not just the homepage. ALSO pass `entity_type`
    # so analyze_product picks the matching type-specific prompt
    # (product / service / gcc).
    try:
        extraction: Dict[str, Any] = gemini.analyze_product(
            url=url_str or "",
            scrape_result=bundle_for_gemini,
            description=payload.product_description,
            entity_type=(payload.entity_type or "").strip().lower() or None,
        )
    except Exception as exc:
        logger.exception("Gemini analyze failed for %s: %s", url_str, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI extraction failed: {exc}",
        ) from exc

    # ── 3. Persist Product + Asset ───────────────────────────────────
    # Lock the user's Product/Service choice into icp.entity_type so the
    # frontend can split GTM Journey filter rows. The user's explicit
    # choice (sent on the payload) always wins over Gemini's auto-detection;
    # falls back to 'product' for back-compat with any legacy caller.
    icp_dict: Dict[str, Any] = dict(extraction.get("icp") or {})
    requested_entity = (payload.entity_type or "").strip().lower()
    # 2026-05-28 — added "gcc" as 3rd valid entity_type (Global Capability
    # Center, e.g. Walmart Global Tech India). See `_ANALYZE_SYSTEM` in
    # nexus/services/gemini.py for the three-type classification rubric.
    if requested_entity in ("product", "service", "gcc"):
        icp_dict["entity_type"] = requested_entity
    elif "entity_type" not in icp_dict:
        icp_dict["entity_type"] = "product"

    # The representative outbound email is signed by. Merged into icp['brand']
    # (rather than replacing it) so the other brand overrides that live there —
    # company_name, cta_url, colors — survive a re-analyze. Blank input never
    # clears an existing rep: a user who leaves the field empty on a re-run
    # should not silently strip the signature off a live campaign.
    _rep_name = (payload.rep_name or "").strip()
    _rep_title = (payload.rep_title or "").strip()
    if _rep_name or _rep_title:
        _brand = dict(icp_dict.get("brand") or {})
        if _rep_name:
            _brand["rep_name"] = _rep_name[:120]
        if _rep_title:
            _brand["rep_title"] = _rep_title[:120]
        icp_dict["brand"] = _brand


    # 2026-07-31 — `product_description` is now PERSISTED (it used to be
    # dropped here). The original objection was staleness; it does not apply,
    # because the blob is written in the SAME upsert as value_proposition /
    # key_benefits / icp below, so it is exactly as fresh as the fields the
    # sequencer already trusts. Gemini's version carries the capability prose
    # ("what we do", customer profile) that cannot be reconstructed from an
    # 80-char value proposition plus a list of noun phrases.
    # sequencer._merge_product_description still synthesizes a description for
    # NULL rows, so legacy products and the pasted-content path are unchanged.

    # ── 3a. Apply user-edited targeting filters (2026-05-28) ─────────
    # The wizard's "Who are you targeting?" page can override any of the
    # 7 canonical filter fields. Apply them on top of Gemini's extracted
    # values — when the user explicitly removes a chip and adds another,
    # THE USER WINS. Each field is set as a plain list (not the {values,
    # confidence, evidence} triple shape Gemini emits) — _icp_to_apollo_body
    # accepts both. `None` means "no user edit, fall through to Gemini's
    # extracted value"; empty list `[]` means "user cleared this filter
    # intentionally — send nothing to Apollo for this dimension."
    _USER_OVERRIDE_KEYS = (
        "person_titles",
        "person_locations",
        # person_states briefly existed (2026-06-02) as a separate ICP
        # field; reverted same day since Apollo has only one location
        # field. State granularity is now part of person_locations
        # values themselves (e.g. "Texas, United States").
        # person_seniorities removed 2026-06-02, person_departments removed
        # 2026-06-08 — titles already imply both; sending them over-narrowed.
        "organization_industries",
        "buyer_technologies",
    )
    for key in _USER_OVERRIDE_KEYS:
        user_val = getattr(payload, key, None)
        if user_val is not None:  # None = no edit; [] = explicit clear
            icp_dict[key] = list(user_val)
    if payload.revenue_range is not None:
        icp_dict["revenue_range"] = payload.revenue_range

    # Per-run QUALIFIED-lead target — user-chosen on the targeting step via
    # the "Number of leads" stepper (payload.lead_count), else the default 20.
    # Clamped to >= 1; NO max cap per product direction (the Apollo per-run
    # credit budget + Lambda function timeout bound real spend/time). This is
    # the count of leads that pass ALL gates and get attached, not raw matches.
    target_lead_count = DISCOVERY_MAX_LEADS
    if payload.lead_count is not None:
        try:
            target_lead_count = max(1, int(payload.lead_count))
        except (TypeError, ValueError):
            target_lead_count = DISCOVERY_MAX_LEADS

    # Upsert by (workspace_id, source_url) — re-running /analyze on the
    # same URL must refresh the existing product instead of creating a
    # duplicate. Each prior run was leaving a fresh row behind, which is
    # why GTM Journey saw 6 z-ninth pills. The new wizard choice
    # (entity_type, refined ICP, etc.) wins; we keep the product id and
    # all leads/campaigns already attached to it.
    #
    # 2026-05-23 fix: compare URLs via the canonical `url_norm` key on
    # BOTH sides of the equality. Previously the comparison was raw
    # equality (`source_url == url_str`) which meant `https://Z-Ninth.com/`
    # vs `https://z-ninth.com` silently created duplicates. Stored values
    # are NOT modified — we only normalize at compare time, so existing
    # rows keep their original URL string.
    # Product name: an explicit user-provided name wins (no-URL path); else the
    # model-extracted name from the content/scrape.
    _prod_name = (payload.product_name or "").strip() or extraction.get("name")

    if content_mode:
        # No URL to dedupe on → each paste/upload mints a fresh product.
        existing = None
    else:
        # Dedupe by DOMAIN NAME (registrable SLD), not the full URL — so
        # spenzo.io / spenzo.ai / spenzo.com all reuse ONE product (same rule
        # the Connectors brand cards use). brand_key is Python (SLD extraction),
        # so we compare in Python over the workspace's products. Match within
        # the same entity_type so a product never collides with a same-named
        # service. Lowest id wins → keeps the original row + its leads/campaigns.
        from nexus.services.url_norm import brand_key as _bkey
        from sqlalchemy import text as _sql_text

        input_key = _bkey(url_str)
        input_et = (icp_dict.get("entity_type") or "product")
        existing = None
        if input_key:
            rows = db.execute(
                _sql_text(
                    """SELECT id, COALESCE(source_url, ''),
                              COALESCE(NULLIF(icp->>'entity_type', ''), 'product') AS et
                         FROM nexus_products
                        WHERE workspace_id = :w AND COALESCE(status, '') <> 'archived'
                        ORDER BY id ASC"""
                ),
                {"w": workspace.id},
            ).fetchall()
            for rid, surl, et in rows:
                if _bkey(surl) == input_key and (et or "product") == input_et:
                    existing = (
                        db.query(models_phase2.NexusProduct)
                        .filter(models_phase2.NexusProduct.id == rid)
                        .first()
                    )
                    break
    if existing is not None:
        existing.name = _prod_name or existing.name
        existing.category = extraction.get("category") or existing.category
        existing.value_proposition = (
            extraction.get("value_proposition") or existing.value_proposition
        )
        existing.key_benefits = extraction.get("key_benefits") or existing.key_benefits or []
        existing.pricing_tier = extraction.get("pricing_tier") or existing.pricing_tier
        existing.industry_relevance = (
            extraction.get("industry_relevance") or existing.industry_relevance
        )
        existing.product_description = (
            extraction.get("product_description") or existing.product_description
        )
        # `icp` is REPLACED wholesale, and the fresh dict comes from the model
        # extraction — which never contains `brand`. Carrying the old brand
        # forward keeps operator-set values (the representative, CTA URL, brand
        # colors) from being silently wiped every time a product is re-analyzed.
        # Anything supplied on THIS request still wins, since it was merged into
        # icp_dict['brand'] above.
        _prior_brand = dict((existing.icp or {}).get("brand") or {})
        if _prior_brand:
            _prior_brand.update(icp_dict.get("brand") or {})
            icp_dict["brand"] = _prior_brand
        existing.icp = icp_dict
        existing.status = "ready"
        product = existing
        db.flush()
    else:
        product = models_phase2.NexusProduct(
            workspace_id=workspace.id,
            user_id=user.id,
            name=_prod_name,
            category=extraction.get("category"),
            value_proposition=extraction.get("value_proposition"),
            key_benefits=extraction.get("key_benefits") or [],
            pricing_tier=extraction.get("pricing_tier"),
            industry_relevance=extraction.get("industry_relevance"),
            product_description=extraction.get("product_description") or {},
            icp=icp_dict,
            source_url=(None if content_mode else url_str),
            status="ready",
        )
        db.add(product)
        db.flush()  # populates product.id without committing the txn

    # 2026-07-28: on RE-analyze, purge the product's prior URL-scraped assets +
    # their Pinecone vectors before indexing fresh. Each run used to append a new
    # 'url' asset with a new asset_id, so vectors never overwrote — one product
    # accumulated 11 near-identical homepage copies, bloating retrieval. Only
    # asset_type='url' is replaced; user-UPLOADED file assets are kept.
    if existing is not None and not content_mode:
        try:
            from nexus.services import pinecone_kb as _pkb

            old_assets = (
                db.query(models_phase2.NexusProductAsset)
                .filter(
                    models_phase2.NexusProductAsset.product_id == product.id,
                    models_phase2.NexusProductAsset.asset_type == "url",
                )
                .all()
            )
            for oa in old_assets:
                try:
                    _pkb.delete_asset(workspace_id=product.workspace_id, asset_id=oa.id)
                except Exception:  # noqa: BLE001
                    pass
                db.delete(oa)
            db.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("re-analyze KB purge skipped for product %s: %s", product.id, exc)

    asset = models_phase2.NexusProductAsset(
        product_id=product.id,
        workspace_id=product.workspace_id,
        asset_type=("text" if content_mode else "url"),
        source=("pasted-content" if content_mode else url_str),
        char_count=scrape_result.char_count,
        status="processing",   # indexing happens below
        chunks_indexed=0,
    )
    db.add(asset)
    db.commit()
    db.refresh(product)
    db.refresh(asset)

    # ── 4. Auto-index scraped URL content + product description ──────
    #
    # Migration 2026-05-26: the scraped URL text is now ALWAYS indexed
    # into Pinecone (it was previously optional and only triggered when
    # the caller passed `knowledge_base` text). This guarantees the Reply
    # Agent can semantically search the website without depending on the
    # legacy `raw_text` DB column (which will be dropped by
    # scripts/drop_old_kb_tables.py).
    #
    # We bundle three text sources into one indexable blob:
    #   1. PRODUCT NAME + VALUE PROPOSITION  (the Gemini-distilled summary)
    #   2. Scraped URL text                  (full homepage / landing page)
    #   3. Optional knowledge_base paragraph (legacy pasted text)
    #
    # All three end up in the SAME Pinecone namespace (ws-{workspace_id})
    # under the same product_id metadata, so retrieval looks across them
    # together by cosine similarity.
    def _compose_indexable_text() -> str:
        parts: list[str] = []
        name = (product.name or "").strip()
        vp = (product.value_proposition or "").strip() if hasattr(product, "value_proposition") else ""
        if name and vp:
            parts.append(f"PRODUCT: {name}\n{vp}")
        elif vp:
            parts.append(vp)
        elif name:
            parts.append(f"PRODUCT: {name}")
        scraped = (scrape_result.text or "").strip()
        if scraped:
            parts.append(scraped[:50000])  # cap a runaway scrape
        # 2026-07-28: also index the signal-rich SUBPAGES fetch_bundle already
        # fetched (features / pricing / integrations / customers / …). Previously
        # ONLY the homepage was embedded, so multi-page sites lost their depth in
        # the KB — grounding (reply RAG, meeting agendas, briefings) then had just
        # the landing page to work from. Now the whole crawled bundle is indexed.
        if not content_mode:
            try:
                for sp in (scrape_bundle.subpages or [])[:6]:
                    sp_text = (getattr(sp, "text", "") or "").strip()
                    if sp_text:
                        parts.append(sp_text[:20000])
            except Exception:  # noqa: BLE001
                pass
        kb = (payload.knowledge_base or "").strip() if payload.knowledge_base else ""
        if kb:
            parts.append(kb)
        return "\n\n".join(parts)

    indexable_text = _compose_indexable_text()
    if indexable_text:
        try:
            chunks_indexed = embedding_service.embed_chunks_and_save(
                db,
                product_id=product.id,
                asset_id=asset.id,
                text=indexable_text,
                workspace_id=product.workspace_id,
                source_name=(url_str or "pasted-content"),
                asset_type=("text" if content_mode else "url"),
            )
            asset.status = "indexed"
            asset.chunks_indexed = int(chunks_indexed)
            asset.last_error = None
            from datetime import datetime as _dt
            asset.indexed_at = _dt.utcnow()
            db.commit()
            logger.info(
                "Indexed scraped URL + product description for product %d "
                "(workspace=%d, %d chunks)",
                product.id, product.workspace_id, chunks_indexed,
            )
        except Exception as exc:
            logger.warning(
                "Pinecone indexing failed for product %d — KB will rely on "
                "user-uploaded files only. Reason: %s",
                product.id, exc,
            )
            asset.status = "failed"
            asset.last_error = str(exc)[:1000]
            try:
                db.commit()
            except Exception:
                db.rollback()
    else:
        # Nothing scraped + no description — odd but possible (very thin
        # landing page). Leave asset row in 'processing'; the user can
        # later upload a PDF or paste text via the KB tab.
        logger.info(
            "Product %d created with no indexable text (scrape empty + no description) — "
            "KB can be populated later via /nexus/kb/upload",
            product.id,
        )

    # ── 5. Create a NEW campaign for THIS run ────────────────────────
    # Model C (2026-06-09, per product direction): EVERY /analyze run mints a
    # brand-new campaign with its own UNIQUE id. We do NOT reuse a prior
    # campaign for the product — each run is an independently-numbered,
    # independently-tracked outreach effort.
    #
    # Lead overlap is still prevented at the PRODUCT level:
    # _filter_against_product_db (discovery_apollo) drops any candidate already
    # attached to ANY campaign of this product, so a new run discovers FRESH
    # leads and never re-enrolls someone already reached under a prior run.
    #
    # Apollo page cursor: a new campaign starts at last_apollo_page=0, which
    # restarts discovery at page 1. To find FRESH leads across re-runs we seed
    # the new campaign from a prior run's furthest-walked page — BUT only from
    # prior runs whose targeting matches THIS run's, because a page number is
    # meaningful only within a FIXED Apollo query. Seeding a new, narrower ICP
    # from a broader prior run's deep cursor lands past the end of the smaller
    # result set and returns ~0 leads (the "1-2 leads after I tightened the
    # filters" bug). So we fingerprint the effective Apollo filter set and only
    # inherit the cursor from same-product campaigns with a matching
    # fingerprint; a changed ICP restarts at page 1. Product-level dedup
    # (_filter_against_product_db) still prevents re-enrolling anyone already
    # reached, so a restart never double-messages.
    #
    # IMPORTANT: campaign.icp is what lead discovery reads
    # (discover_for_campaign._resolve_icp prefers campaign.icp over
    # product.icp). It MUST be `icp_dict` — the Gemini extraction with the
    # user's targeting-page overrides applied (see §3a above).
    from nexus.services.discovery_apollo import icp_filter_fingerprint

    campaign_name = (
        (product.name or extraction.get("name") or "New campaign").strip()[:200]
        or "New campaign"
    )
    _fp = icp_filter_fingerprint(icp_dict)
    _seed_strict = 0
    _seed_broader = 0
    _prior_runs = (
        db.query(
            NexusCampaign.last_apollo_page,
            NexusCampaign.last_apollo_broader_page,
            NexusCampaign.icp,
        )
        .filter(
            NexusCampaign.workspace_id == workspace.id,
            NexusCampaign.product_id == product.id,
        )
        .all()
    )
    for _p_strict, _p_broader, _p_icp in _prior_runs:
        if icp_filter_fingerprint(_p_icp or {}) != _fp:
            continue  # different targeting — its cursor doesn't apply here.
        _seed_strict = max(_seed_strict, int(_p_strict or 0))
        _seed_broader = max(_seed_broader, int(_p_broader or 0))
    campaign = NexusCampaign(
        workspace_id=workspace.id,
        product_id=product.id,
        name=campaign_name,
        icp=icp_dict,
        status="active",
        last_apollo_page=_seed_strict,
        last_apollo_broader_page=_seed_broader,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    # Per-product campaign number (2026-06-11): a stable display number that
    # restarts at 1 FOR EACH PRODUCT (Spenzo → 1,2,3). The global `id` stays the
    # internal PK; this is the human-friendly label shown in the GTM Journey
    # table. Assigned via MAX+1 in raw SQL (works even if the column was just
    # added) and wrapped defensively — a missing column or a rare concurrent-run
    # collision never breaks a launch; the name just falls back to the global id.
    _seq_no = None
    try:
        _seq_no = db.execute(
            _sql_text(
                """UPDATE nexus_campaigns
                      SET product_campaign_number = COALESCE(
                          (SELECT MAX(product_campaign_number)
                             FROM nexus_campaigns
                            WHERE product_id = :pid AND id <> :cid), 0) + 1
                    WHERE id = :cid
                      AND product_campaign_number IS NULL
                  RETURNING product_campaign_number"""
            ),
            {"pid": product.id, "cid": campaign.id},
        ).scalar()
        db.commit()
    except Exception:  # noqa: BLE001 — column missing (pre-migration) or unique race
        db.rollback()
        _seq_no = None

    # Name shows the per-product number when available ("Spenzo #1"), else
    # falls back to the global id (pre-migration / collision).
    _disp = _seq_no if _seq_no is not None else campaign.id
    campaign.name = f"{campaign_name} #{_disp}"[:255]
    db.commit()
    db.refresh(campaign)

    # Record this launch as a discrete event. Under Model C each campaign IS
    # one run (campaign.created_at now reflects THIS run), so this side-table
    # is largely redundant — but we keep it for back-compat with the
    # outbound-emails preview's MAX(launched_at) fence. Best-effort: if the
    # insert fails (e.g. table absent in a stale env), we log and continue.
    try:
        db.add(NexusCampaignLaunch(campaign_id=campaign.id))
        db.commit()
    except Exception:  # noqa: BLE001
        logger.warning(
            "analyze: failed to record campaign launch for %s",
            campaign.id,
            exc_info=True,
        )
        db.rollback()

    # ── 6. Synchronous discovery — no wall-clock cap ──────────────────
    # When the timeout fires (or any error happens), we flag the campaign
    # via `campaign.icp.discovery_pending = true` so the background
    # sweep in `process_pending_discoveries` can pick it up and retry.
    # Without this flag, the campaign would sit empty forever and the
    # zero-lead-campaign filter (campaigns.py:list_campaigns) would hide
    # it from the GTM Journey — operator never sees it broke.
    discovery_summary = AnalyzeDiscoverySummary(status="skipped")
    try:
        # 2026-05-29 — No more `asyncio.wait_for(..., timeout=...)`.
        # The previous 600s wrap was a defensive ceiling that mirrored
        # the (now-removed) API-Gateway 29s ceiling. With Lambda Function
        # URL (no API Gateway), the only real ceiling is Lambda's own
        # function timeout, which is set in IaC. Discovery now runs to
        # natural completion — Apollo exhausted, 20-lead target hit,
        # or yield-stop fires.
        result = await discover_for_campaign(
            db,
            campaign,
            max_leads=target_lead_count,
            min_icp_score=DISCOVERY_MIN_ICP_SCORE,
        )
        discovery_summary = AnalyzeDiscoverySummary(
            leads_attached=int(result.get("leads_attached", 0)),
            by_source=dict(result.get("by_source") or {}),
            errors=list(result.get("errors") or []),
            status="completed",
            requested=int(result.get("requested") or target_lead_count),
            reason=result.get("reason"),
            duplicates=int(result.get("duplicates") or 0),
        )
        # If discovery succeeded but landed zero leads, still flag for
        # retry — Apollo's q_keywords self-heal occasionally races, and
        # a second pass from a sweep usually gets results.
        # 2026-06-11: ONLY when the campaign truly has nothing to show.
        # A zero-QUALIFIED run can still have produced "Already in
        # campaign" marker rows (the known-people re-vet); flagging those
        # runs kept the UI on the "Finding your leads…" loader forever
        # (status stays 'discovering') and queued a pointless background
        # re-run of an already-mined pool.
        if discovery_summary.leads_attached == 0:
            from nexus.models_phase3 import NexusLead
            _has_rows = (
                db.query(NexusLead.id)
                .filter(NexusLead.campaign_id == campaign.id)
                .first()
                is not None
            )
            if not _has_rows:
                _flag_campaign_for_discovery_retry(db, campaign, reason="zero_leads")
    except Exception as exc:  # noqa: BLE001
        logger.exception("analyze: discovery failed for campaign %s: %s", campaign.id, exc)
        discovery_summary = AnalyzeDiscoverySummary(
            status="skipped",
            errors=[str(exc)[:240]],
        )
        _flag_campaign_for_discovery_retry(db, campaign, reason=f"error:{str(exc)[:60]}")

    # ── Enroll + generate emails as a BackgroundTask, AFTER the response ──
    # B-mode scored leads INLINE during discovery WITHOUT enrolling/sending
    # (auto_start_outreach=False), so no emails went out before the user saw
    # the table. Now that we're about to return (table renders), kick the
    # enroll+outreach task: it enrolls the Accepted leads, and the sequencer
    # tick then generates + sends emails in the BACKGROUND — emails happen
    # AFTER the leads are shown, never during discovery. No-op when gate off.
    try:
        from nexus.services.intent_agent import gate_enabled
        if gate_enabled():
            from nexus.services.intent_sweep import enroll_and_outreach_bg
            background_tasks.add_task(enroll_and_outreach_bg, campaign_id=campaign.id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "analyze: failed to schedule enroll+outreach for campaign %s",
            campaign.id, exc_info=True,
        )

    # Inline the leads in the /analyze response so the wizard renders the
    # LeadsTable from this payload — no polling round-trip needed.
    # On timeout / failure this stays an empty list and the UI shows an
    # empty state; the discovery_sweep cron picks the campaign up later.
    leads_payload: List[Dict[str, Any]] = []
    if discovery_summary.status == "completed" and discovery_summary.leads_attached > 0:
        try:
            from nexus.routers.campaign_leads import (
                latest_launch_at,
                load_leads_for_campaign,
            )
            # B-mode: the run REVEALED more than target_lead_count (some leads
            # were rejected by the intent gate), and the table shows the
            # qualified (Accepted) subset. So load generously — all of this
            # run's rows — and let the frontend filter to non-rejected; a tight
            # `limit=target` could truncate accepted leads behind rejected ones.
            leads_payload = load_leads_for_campaign(
                db, campaign_id=campaign.id, workspace_id=workspace.id,
                limit=max(int(result.get("revealed") or target_lead_count), 100),
                offset=0,
                # Run-fence (2026-06-04): the campaign is reused across runs and
                # never cleared, so fence to THIS run's launch — the success
                # screen shows only leads found in this run. We deliberately do
                # NOT re-apply the strict ICP text filter (icp=None): these leads
                # were already ICP-filtered at discovery + vetted by Agent #10,
                # and re-filtering wrongly hid accepted leads (5 found -> 3 shown).
                icp=None,
                since=latest_launch_at(db, campaign_id=campaign.id),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "analyze: failed to inline leads for campaign %s: %s",
                campaign.id, exc,
            )

    logger.info(
        "[TIMING] /analyze TOTAL took %s — campaign %s finished with %d leads "
        "(discovery status: %s). Email generation continues in the background.",
        _fmt_mmss(_t.monotonic() - _analyze_started),
        campaign.id,
        int(discovery_summary.leads_attached or 0),
        discovery_summary.status,
    )
    return AnalyzeResponse(
        product=product,  # type: ignore[arg-type]
        asset=asset,  # type: ignore[arg-type]
        extraction=extraction,
        campaign=AnalyzeCampaignSummary(
            id=campaign.id,
            name=campaign.name,
            # Per-product number (restarts at 1 per product); _disp already
            # resolves to _seq_no or the global id as a legacy fallback.
            campaign_number=_disp,
            # Clean product/company name (no "#N" suffix).
            product_name=campaign_name,
            status=campaign.status,
            created_at=campaign.created_at.isoformat() if campaign.created_at else None,
        ),
        discovery=discovery_summary,
        leads=leads_payload,
    )


# ─── Discovery retry flag (read by sequencer's background sweep) ─────────────


def _flag_campaign_for_discovery_retry(
    db: Session, campaign: "NexusCampaign", reason: str
) -> None:
    """Persist a flag on `campaign.icp.discovery_pending` so the background
    sweep in the sequencer tick picks up the campaign and retries discovery.

    Why this exists: the previous Lambda model "kicks off discovery, returns
    instantly, leads stream in" was a lie — the request handler exits and
    nothing runs in background. Now we flag the campaign explicitly and a
    proper background job (`process_pending_discoveries` in
    `nexus/services/discovery_sweep.py`) picks it up on the next scheduler
    tick. This is what makes the user's "guaranteed there will be leads"
    promise actually hold even when the synchronous pass times out.
    """
    try:
        icp = dict(campaign.icp or {})
        icp["discovery_pending"] = True
        icp["discovery_pending_reason"] = (reason or "")[:240]
        from datetime import datetime, timezone
        icp["discovery_pending_at"] = datetime.now(timezone.utc).isoformat()
        campaign.icp = icp
        db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "analyze: failed to flag campaign %s for discovery retry: %s",
            getattr(campaign, "id", None),
            exc,
        )
        db.rollback()
