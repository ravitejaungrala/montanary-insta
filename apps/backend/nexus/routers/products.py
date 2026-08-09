"""
/nexus/products — Product CRUD scoped to the active Nexus workspace.

Endpoints:
  GET    /nexus/products                 list workspace products (with lead_count + entity_type)
  POST   /nexus/products                 manual create (no scrape)
  GET    /nexus/products/{id}            single product
  GET    /nexus/products/{id}/leads      leads enrolled in any campaign for this product
  PATCH  /nexus/products/{id}            partial update
  DELETE /nexus/products/{id}            cascade delete (assets + embeddings)

The richer auto-create flow lives at POST /nexus/analyze. This router
is for direct manipulation by the UI once a product exists.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from core.database import get_db
from models import User
from nexus.deps import (  # type: ignore[import-not-found]
    block_user_writes,
    check_limit,
    get_nexus_user,
    increment_usage,
    require_current_workspace,
    trial_guard,
)
from nexus import models_phase2
from nexus.models_phase3 import NexusCampaign, NexusLead
from nexus.schemas_phase2 import ProductCreate, ProductOut, ProductUpdate

logger = logging.getLogger("pipelyt.nexus.products")

router = APIRouter(
    prefix="/nexus/products",
    tags=["Nexus — Products"],
    dependencies=[Depends(block_user_writes)],  # read-only Users blocked from writes
)


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


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("", response_model=List[ProductOut])
def list_products(
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    rows = (
        db.query(models_phase2.NexusProduct)
        .filter(models_phase2.NexusProduct.workspace_id == workspace.id)
        # Skip archived (soft-deleted duplicate products). They stay in
        # the DB but never surface in the picker, the GTM Journey product
        # filter pills, analytics, or anywhere downstream.
        .filter(models_phase2.NexusProduct.status != "archived")
        .order_by(models_phase2.NexusProduct.updated_at.desc())
        .all()
    )
    if not rows:
        return []

    # Lead-count rule: match what GET /products/{id}/leads actually
    # returns when the pill is clicked. The old rule counted every
    # nexus_leads row (duplicate enrollments + hidden leads included),
    # which made the pill say "130" for Spenzo AI when only 1 lead
    # showed up after clicking. We now count DISTINCT global_lead_ids
    # AND exclude leads whose global_lead.priority_state = 'hidden'.
    product_ids = [p.id for p in rows]
    counts: dict = {}
    try:
        from sqlalchemy import text as _text

        count_rows = db.execute(
            _text(
                """
                SELECT c.product_id, COUNT(DISTINCT gl.id) AS n
                  FROM nexus_campaigns c
                  JOIN nexus_leads nl       ON nl.campaign_id = c.id
                  JOIN nexus_global_leads gl ON gl.id = nl.global_lead_id
                 WHERE c.workspace_id = :w
                   AND c.product_id = ANY(:pids)
                   AND (gl.priority_state IS NULL OR gl.priority_state <> 'hidden')
                 GROUP BY c.product_id
                """
            ),
            {"w": workspace.id, "pids": product_ids},
        ).fetchall()
        counts = {int(r[0]): int(r[1]) for r in count_rows}
    except Exception:
        logger.warning(
            "list_products: distinct-active count failed, falling back to old count"
        )
        # Conservative fallback so the endpoint still returns SOMETHING.
        counts = dict(
            db.query(NexusCampaign.product_id, func.count(NexusLead.id))
            .join(NexusLead, NexusLead.campaign_id == NexusCampaign.id)
            .filter(
                NexusCampaign.workspace_id == workspace.id,
                NexusCampaign.product_id.in_(product_ids),
            )
            .group_by(NexusCampaign.product_id)
            .all()
        )

    # Collapse legacy duplicates: any products that share a normalised
    # source_url get folded into a single canonical row. The canonical
    # row is the most-recently-updated sibling (which was already the
    # query's natural order) so the user sees the latest name/icp. The
    # surfaced `lead_count` is the SUM across siblings — matches what
    # the sibling-aware /products/{id}/leads endpoint returns when the
    # canonical id is clicked. Products with NULL/empty source_url stay
    # in their own group keyed by id (no accidental merging).
    def _url_key(p) -> str:
        url = (p.source_url or "").strip().lower().rstrip("/")
        return url if url else f"__id__::{p.id}"

    groups: dict[str, dict] = {}
    for p in rows:
        key = _url_key(p)
        g = groups.get(key)
        n = int(counts.get(p.id, 0))
        if g is None:
            groups[key] = {"canonical": p, "lead_count": n}
        else:
            g["lead_count"] += n
            # `rows` is ordered by updated_at DESC so the FIRST product
            # we see for a key is already the freshest — keep canonical.
            # (No swap needed.)

    out: List[ProductOut] = []
    for g in groups.values():
        p = g["canonical"]
        icp_dict = p.icp if isinstance(p.icp, dict) else {}
        et = (icp_dict.get("entity_type") or "product").strip().lower()
        if et not in ("product", "service"):
            et = "product"
        # model_copy(update=...) re-runs validation so the derived fields
        # are guaranteed to appear in the serialized JSON (post-validate
        # attribute assignment is mutable but is fragile around Pydantic
        # v2 serializers — copy-with-update is the safe path).
        out.append(
            ProductOut.model_validate(p).model_copy(
                update={
                    "lead_count": int(g["lead_count"]),
                    "entity_type": et,
                }
            )
        )
    return out


# ---------------------------------------------------------------------------
# Create (no scrape — for manual ICP authoring)
# ---------------------------------------------------------------------------


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
    _trial=Depends(trial_guard),
):
    # Dedupe by DOMAIN NAME (registrable SLD) + entity_type — same rule as
    # /analyze and /analyze-from-leads — so pipelyt.ai / pipelyt.io / pipelyt.com
    # reuse ONE product instead of creating a TLD-variant duplicate. Reusing
    # doesn't count against the plan limit (no new row is created).
    from nexus.services.url_norm import brand_key as _bkey
    from sqlalchemy import text as _sql_text

    icp_dict = payload.icp or {}
    input_key = _bkey(payload.source_url)
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
        # Reuse the existing brand product — apply the fields the user supplied
        # without blanking existing values with empties.
        existing.name = payload.name or existing.name
        existing.category = payload.category or existing.category
        existing.value_proposition = payload.value_proposition or existing.value_proposition
        existing.key_benefits = payload.key_benefits or existing.key_benefits or []
        existing.pricing_tier = payload.pricing_tier or existing.pricing_tier
        existing.industry_relevance = payload.industry_relevance or existing.industry_relevance
        if icp_dict:
            existing.icp = icp_dict
        existing.status = "ready"
        db.commit()
        db.refresh(existing)
        return existing

    check_limit(db, workspace, "products")

    product = models_phase2.NexusProduct(
        workspace_id=workspace.id,
        user_id=user.id,
        name=payload.name,
        category=payload.category,
        value_proposition=payload.value_proposition,
        key_benefits=payload.key_benefits or [],
        pricing_tier=payload.pricing_tier,
        industry_relevance=payload.industry_relevance,
        icp=payload.icp or {},
        source_url=payload.source_url,
        status="ready",
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    increment_usage(db, workspace, "products", 1)
    return product


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


@router.get("/{product_id}", response_model=ProductOut)
def get_product(
    product_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    product = _load_owned_product(db, product_id, workspace.id)
    icp_dict = product.icp if isinstance(product.icp, dict) else {}
    et = (icp_dict.get("entity_type") or "product").strip().lower()
    if et not in ("product", "service"):
        et = "product"
    return ProductOut.model_validate(product).model_copy(update={"entity_type": et})


# ---------------------------------------------------------------------------
# Leads for this product (resolves product -> campaigns -> leads)
# ---------------------------------------------------------------------------


@router.get("/{product_id}/leads")
def list_leads_for_product(
    product_id: int,
    view: str = "active",
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Return leads enrolled in any campaign whose product_id matches.

    `view` param ("active" | "hidden", default "active") mirrors the
    /nexus/journey/leads contract so the GTM Journey Active/Hidden toggle
    works whether or not a product pill is selected. Without this param
    the toggle was a no-op when filtering by product.

    Sibling-aware: if the selected product shares a `source_url` with
    other products in the same workspace (legacy duplicates left over
    from when /analyze inserted a new row per re-run), this endpoint
    unions the lead sets across all siblings. From the operator's
    perspective there's one "z-ninth" workspace concept; the UI pill
    should surface every lead that came in for that URL regardless of
    which legacy product row owns the campaign.

    Response shape matches GET /nexus/journey/leads so NexusJourney.jsx
    can use the same LeadCard rendering pipeline.
    """
    # 404 if the product doesn't belong to this workspace.
    canonical = _load_owned_product(db, product_id, workspace.id)

    # Lazy-import journey helpers — avoids a circular import at module load.
    from nexus.routers.journey import (
        _TERMINAL_STATUSES,
        _collect_attempt_stats,
        _lead_row_to_dict,
        _status_rank,
        _with_journey_derived,
    )

    # Resolve sibling product ids — same workspace + same source_url
    # (case-insensitive, trailing-slash-tolerant). Empty source_url means
    # "no siblings" and only the selected product is queried.
    sibling_ids: List[int] = [canonical.id]
    if canonical.source_url:
        # Normalise both sides so http://example.com and https://example.com/
        # match. Postgres LOWER + TRIM('/') keeps the test SQL-side.
        rows = db.execute(
            text(
                """
                SELECT id FROM nexus_products
                 WHERE workspace_id = :w
                   AND source_url IS NOT NULL
                   AND TRIM(BOTH '/' FROM LOWER(source_url))
                       = TRIM(BOTH '/' FROM LOWER(:url))
                """
            ),
            {"w": workspace.id, "url": canonical.source_url},
        ).fetchall()
        sibling_ids = [int(r[0]) for r in rows] or [canonical.id]

    # Campaigns under ANY of those sibling products in this workspace.
    campaign_ids: List[int] = [
        r[0]
        for r in db.execute(
            text(
                "SELECT id FROM nexus_campaigns "
                "WHERE workspace_id = :w AND product_id = ANY(:ps)"
            ),
            {"w": workspace.id, "ps": sibling_ids},
        ).fetchall()
    ]
    if not campaign_ids:
        return {"leads": []}

    # Distinct global_lead_ids enrolled in any of those campaigns.
    lead_ids: List[int] = [
        r[0]
        for r in db.execute(
            text(
                "SELECT DISTINCT global_lead_id FROM nexus_leads "
                "WHERE campaign_id = ANY(:cids)"
            ),
            {"cids": campaign_ids},
        ).fetchall()
    ]
    if not lead_ids:
        return {"leads": []}

    # Fetch the global lead rows, filtering by the requested view.
    # `view='active'` → leads NOT in hidden bucket.
    # `view='hidden'` → leads in hidden bucket only.
    # Mirrors /nexus/journey/leads exactly.
    view_norm = (view or "active").strip().lower()
    if view_norm == "hidden":
        where_priority = "priority_state = 'hidden'"
    else:
        where_priority = "(priority_state IS NULL OR priority_state <> 'hidden')"
    rows = db.execute(
        text(
            f"SELECT * FROM nexus_global_leads "
            f"WHERE id = ANY(:lids) "
            f"  AND {where_priority} "
            f"LIMIT 1000"
        ),
        {"lids": lead_ids},
    ).fetchall()

    derived_map = _collect_attempt_stats(
        db, campaign_ids, [r._mapping["id"] for r in rows]
    )

    # Per-lead enrolled_at attribution — pick the LATEST nexus_leads.created_at
    # per global_lead so a lead re-enrolled today (after first being
    # discovered weeks ago) shows up at the top of the list, not buried
    # with the old discoveries. Without this the products endpoint used
    # the global lead's ORIGINAL created_at, which sorted re-enrolled
    # leads by their first-ever discovery date — they correctly displayed
    # "28m ago" via last_attempt_at on the card, but the sort placed them
    # with multi-day-old leads. Mirrors journey.py:454-470.
    enrolled_by_lead: Dict[int, Any] = {}
    rows_lead_ids = [int(r._mapping["id"]) for r in rows]
    if rows_lead_ids:
        try:
            for ar in db.execute(
                text(
                    """
                    SELECT DISTINCT ON (nl.global_lead_id)
                           nl.global_lead_id, nl.created_at AS enrolled_at
                      FROM nexus_leads nl
                     WHERE nl.global_lead_id = ANY(:lids)
                       AND nl.campaign_id = ANY(:cids)
                     ORDER BY nl.global_lead_id, nl.id DESC
                    """
                ),
                {"lids": rows_lead_ids, "cids": campaign_ids},
            ).fetchall():
                enrolled_by_lead[int(ar[0])] = ar[1]
        except Exception:  # noqa: BLE001
            # Best-effort: if the attribution query fails (rare — same
            # table the upstream query just succeeded against), fall
            # through and let response items use the global created_at
            # default below. Sort order may revert to the old "by global
            # discovery time" semantics for that request only.
            try:
                db.rollback()
            except Exception:
                pass

    response: List[Dict[str, Any]] = []
    for r in rows:
        lead = _lead_row_to_dict(r)
        merged = _with_journey_derived(lead, derived_map.get(lead["id"]))
        is_terminal = (merged.get("status") or "") in _TERMINAL_STATUSES
        response.append(
            {
                "_id": merged["id"],
                "email": merged.get("email"),
                "name": merged.get("name") or "",
                "company": merged.get("company") or "",
                "company_domain": merged.get("company_domain") or "",
                "job_title": merged.get("job_title") or "",
                "linkedin_url": merged.get("linkedin_url") or "",
                "status": merged.get("status") or "new",
                "priority_state": merged.get("priority_state") or "active",
                "hidden_reason": merged.get("hidden_reason") or "",
                "hidden_at": merged.get("hidden_at"),
                # Prefer the per-campaign enrollment time (latest
                # nexus_leads.created_at via the DISTINCT ON query
                # above) over the global lead's original discovery
                # date — matches what /nexus/journey/leads returns.
                "enrolled_at": enrolled_by_lead.get(int(merged["id"])) or merged.get("createdAt"),
                "attempt_count_total": merged.get("attempt_count_total") or 0,
                "channel_attempts": merged.get("channel_attempts")
                or {"email": 0, "linkedin": 0, "voice": 0},
                "last_attempt_at": merged.get("last_attempt_at"),
                "last_attempt_channel": merged.get("last_attempt_channel"),
                "total_emails_sent": merged.get("total_emails_sent") or 0,
                "eligible_for_auto_hide": (
                    not is_terminal
                    and merged.get("priority_state") != "hidden"
                    and (merged.get("attempt_count_total") or 0) >= 3
                ),
                # All leads on this endpoint belong to the canonical
                # product (or one of its source_url siblings); use the
                # canonical row's name for the lead-card tag.
                "product_id":   canonical.id,
                "product_name": canonical.name or "",
                "product_entity_type": (
                    (canonical.icp or {}).get("entity_type") if isinstance(canonical.icp, dict) else None
                ) or "product",
            }
        )

    _epoch = datetime(1970, 1, 1)
    response.sort(
        key=lambda a: (
            -(a.get("enrolled_at") or _epoch).timestamp() if a.get("enrolled_at") else 0,
            -_status_rank(a.get("status")),
            -(a.get("attempt_count_total") or 0),
        )
    )
    return {"leads": response}


# ---------------------------------------------------------------------------
# Patch
# ---------------------------------------------------------------------------


@router.patch("/{product_id}", response_model=ProductOut)
def update_product(
    product_id: int,
    payload: ProductUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
    _trial=Depends(trial_guard),
):
    product = _load_owned_product(db, product_id, workspace.id)

    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(product, key, value)
    db.commit()
    db.refresh(product)
    return product


# ---------------------------------------------------------------------------
# Delete (cascade — SQLAlchemy ON DELETE handles assets + embeddings)
# ---------------------------------------------------------------------------


@router.delete("/{product_id}", status_code=status.HTTP_200_OK)
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    product = _load_owned_product(db, product_id, workspace.id)

    db.delete(product)
    db.commit()
    increment_usage(db, workspace, "products", -1)
    return {"ok": True, "deleted": product_id}
