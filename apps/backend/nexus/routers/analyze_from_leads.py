"""
POST /nexus/analyze/from-leads — "Bring-Your-Own-Leads" pipeline.

Mirrors the shape of /nexus/analyze BUT:
  • Accepts a user-supplied `leads: List[ManualLead]` array — every row must
    carry the 4 mandatory fields (name, role, company, email). The endpoint
    refuses the WHOLE request (no partial inserts) if any row is missing
    a field or has an invalid email.
  • Skips ICP suggestion + Apollo discovery entirely. The product still gets
    scraped + Gemini-summarised + indexed exactly like /analyze so the
    sequencer's email/LinkedIn generators have the same product context to
    work with.
  • Inserts every supplied lead via the existing helpers in
    `lead_discovery` (`_upsert_global_lead` → `_attach_workspace_lead` →
    `_enroll_in_sequence`) so the sequencer picks them up on the next tick
    just like Apollo-sourced leads — same status flow, same touchpoints
    (email + LinkedIn DM + LinkedIn InMail).

Why a NEW endpoint instead of extending /analyze:
  • Keeps the existing /analyze code path byte-identical — no risk of
    introducing a regression in the discovery flow that the operator team
    relies on.
  • The validation contract here is "all-or-nothing" on leads which is
    structurally different from /analyze's "best-effort discovery".
  • Authentication, workspace scoping, and trial-guard logic are reused
    via dependency injection so we don't duplicate auth code.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator
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
    AnalyzeResponse,
)
from nexus.services import embedding as embedding_service
from nexus.services import gemini
from nexus.services import playwright_scraper

logger = logging.getLogger("pipelyt.nexus.analyze_from_leads")

router = APIRouter(prefix="/nexus/analyze", tags=["Nexus — Analyze (BYO-Leads)"])


# ── Self-check endpoint ────────────────────────────────────────────────────
# Hit GET /nexus/analyze/from-leads/preflight on the deployed env to
# verify both prerequisites in one shot:
#   1. nexus_global_leads.source column exists (required by ON CONFLICT)
#   2. The caller's workspace has at least one sequence (required for
#      the auto-enrolled lead_sequences to actually result in emails)
#
# Returns 200 with a `ready: true` payload if everything's fine, otherwise
# 200 with `ready: false` and a list of `issues` you can act on. Never
# 5xxs — this is a diagnostic, not an enforcement gate.


@router.get("/from-leads/preflight")
def from_leads_preflight(
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Verify the deploy is ready to handle a manual-leads upload."""
    from sqlalchemy import text as _t

    issues: List[str] = []

    # 1. nexus_global_leads.source column.
    try:
        col_row = db.execute(
            _t(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'nexus_global_leads'
                  AND column_name = 'source'
                LIMIT 1
                """
            )
        ).first()
        source_column_present = col_row is not None
        if not source_column_present:
            issues.append(
                "nexus_global_leads.source column is MISSING. Run the phase3 "
                "migration (or ALTER TABLE nexus_global_leads ADD COLUMN "
                "source VARCHAR(64)) before uploading leads."
            )
    except Exception as exc:  # noqa: BLE001
        source_column_present = None
        issues.append(f"Failed to inspect schema: {str(exc)[:200]}")

    # 2. Workspace has at least one sequence.
    try:
        seq_count_row = db.execute(
            _t(
                "SELECT COUNT(*) FROM nexus_sequences WHERE workspace_id = :w"
            ),
            {"w": workspace.id},
        ).scalar()
        sequence_count = int(seq_count_row or 0)
        # NOTE: this isn't a hard blocker any more — the endpoint will
        # auto-create a default sequence if none exists. The check
        # remains useful as a heads-up: an Ops dashboard can show
        # "workspace X has 0 sequences" so you know auto-creation will
        # fire on the next upload.
    except Exception as exc:  # noqa: BLE001
        sequence_count = None
        issues.append(f"Failed to count sequences: {str(exc)[:200]}")

    return {
        "ready": len(issues) == 0,
        "workspace_id": workspace.id,
        "source_column_present": source_column_present,
        "sequence_count": sequence_count,
        "auto_creates_sequence_if_missing": True,
        "issues": issues,
    }


# ── Request/response schemas ────────────────────────────────────────────────

# Simple but reasonably-strict email pattern. Matches the same shape the
# Apollo enrichment pipeline expects so leads inserted here are
# indistinguishable downstream.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


class ManualLead(BaseModel):
    """One row from the uploaded CSV/XLSX/pasted data.

    Mandatory: name, role, company, email.
    Optional:  linkedin_url (LinkedIn DM + InMail generators key on this;
               without it those touchpoints stay unavailable for the lead).
    """

    name: str
    role: str
    company: str
    email: str
    linkedin_url: Optional[str] = None
    # Optional user-supplied columns (BYO). All blank/absent → None so the
    # Journey shows a blank cell rather than a fabricated value.
    #   match_score → nexus_leads.icp_score (0-100). None ⇒ blank pill.
    #   location    → nexus_global_leads.person_city (shown verbatim).
    #   phone       → nexus_global_leads.phone (Contact column, click-to-dial).
    match_score: Optional[int] = None
    location: Optional[str] = None
    phone: Optional[str] = None

    @field_validator("name", "role", "company", "email", mode="before")
    @classmethod
    def _strip_and_require(cls, v: Any) -> Any:  # type: ignore[no-untyped-def]
        if v is None:
            return v
        s = str(v).strip()
        return s

    @field_validator("location", "phone", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:  # type: ignore[no-untyped-def]
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    @field_validator("match_score", mode="before")
    @classmethod
    def _parse_score(cls, v: Any) -> Any:  # type: ignore[no-untyped-def]
        # Accept "", None, or a numeric string/number. Blank ⇒ None (unscored).
        # Non-numeric ⇒ None (never fabricate a score). Clamp to 0-100.
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        try:
            n = int(round(float(s)))
        except (TypeError, ValueError):
            return None
        return max(0, min(100, n))


class AnalyzeFromLeadsRequest(BaseModel):
    workspace_id: int
    url: HttpUrl
    entity_type: Optional[str] = None  # 'product' | 'service'
    product_description: Optional[str] = None
    knowledge_base: Optional[str] = None
    leads: List[ManualLead] = Field(default_factory=list)
    # Representative that signs outbound email for this product — same capture
    # as the New Campaign wizard. BYO-lead runs send the identical cadence, so
    # skipping it here would leave uploaded leads getting "<Product> Team".
    # Persisted to product.icp['brand'], which the sender context already reads.
    rep_name: Optional[str] = None
    rep_title: Optional[str] = None


class _LeadRowError(BaseModel):
    """One per-row validation error returned in the 422 response body."""

    model_config = ConfigDict(extra="allow")

    row: int
    field: str
    reason: str


# ── Validation helpers ──────────────────────────────────────────────────────


def _validate_leads(leads: List[ManualLead]) -> List[_LeadRowError]:
    """All-or-nothing validation. Returns a list of every offending row;
    if the list is non-empty the caller must 422 the entire request so we
    never create a partial product/campaign with half the leads silently
    dropped."""
    errors: List[_LeadRowError] = []
    if not leads:
        errors.append(_LeadRowError(row=0, field="leads", reason="No leads provided."))
        return errors

    for idx, lead in enumerate(leads):
        # Row index is 1-based for human-friendly error messages — matches
        # how spreadsheets show row numbers.
        row = idx + 1
        if not lead.name:
            errors.append(_LeadRowError(row=row, field="name", reason="Name is required."))
        if not lead.role:
            errors.append(_LeadRowError(row=row, field="role", reason="Role is required."))
        if not lead.company:
            errors.append(_LeadRowError(row=row, field="company", reason="Company is required."))
        if not lead.email:
            errors.append(_LeadRowError(row=row, field="email", reason="Email is required."))
        elif not _EMAIL_RE.match(lead.email):
            errors.append(
                _LeadRowError(row=row, field="email", reason=f"Invalid email format: {lead.email!r}.")
            )
    return errors


def _split_name(full_name: str) -> tuple[str, str]:
    """Split a single 'name' column into (first, last). Matches the legacy
    Apollo path which always populates first_name + last_name on
    nexus_global_leads, so downstream code that personalises emails can
    look up either column without a None check."""
    n = (full_name or "").strip()
    if not n:
        return "", ""
    parts = n.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _company_domain_from_email(email: str) -> str:
    """Best-effort fallback when the user didn't paste a company URL —
    derives a company domain from the email so the Journey lead card's
    'Company · domain.com' line still has something to show. Stripped of
    common consumer providers so 'gmail.com' doesn't masquerade as a
    company domain."""
    s = (email or "").strip().lower()
    if "@" not in s:
        return ""
    domain = s.split("@", 1)[1]
    CONSUMER = {
        "gmail.com",
        "yahoo.com",
        "outlook.com",
        "hotmail.com",
        "icloud.com",
        "proton.me",
        "protonmail.com",
        "live.com",
        "aol.com",
    }
    if domain in CONSUMER:
        return ""
    return domain


def _normalize_linkedin(url: Optional[str]) -> Optional[str]:
    """Light normalization — accept bare handles ('john-doe') by prefixing
    the canonical URL, otherwise pass through. Returns None for blank/
    invalid input so the lead row is created without a LinkedIn link
    instead of carrying garbage."""
    if not url:
        return None
    s = str(url).strip()
    if not s:
        return None
    if s.startswith("http://") or s.startswith("https://"):
        return s.rstrip("/")
    # Bare handle (e.g. "john-doe") or `linkedin.com/in/john-doe`.
    if s.startswith("linkedin.com/") or s.startswith("www.linkedin.com/"):
        return f"https://{s}".rstrip("/")
    if "/" not in s and " " not in s:
        return f"https://www.linkedin.com/in/{s}".rstrip("/")
    return None


# ── Endpoint ────────────────────────────────────────────────────────────────


@router.post(
    "/from-leads",
    response_model=AnalyzeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def analyze_from_leads(
    payload: AnalyzeFromLeadsRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
    _trial=Depends(trial_guard),
):
    """Create a campaign from a user-supplied lead list.

    Same response shape as /nexus/analyze so the wizard can route to GTM
    Journey unchanged. `discovery.status` is reported as 'skipped' with
    `leads_attached` equal to the count of supplied leads — the operator
    sees the right number on the success screen.
    """

    if payload.workspace_id != workspace.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="workspace_id does not match the active workspace.",
        )

    # ── 0. Validate leads up-front (all-or-nothing) ─────────────────────
    lead_errors = _validate_leads(payload.leads)
    if lead_errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": (
                    f"{len(lead_errors)} validation error"
                    f"{'s' if len(lead_errors) != 1 else ''} — fix the file and re-upload."
                ),
                "errors": [e.model_dump() for e in lead_errors],
            },
        )

    url_str = str(payload.url)

    # ── 1. Scrape ────────────────────────────────────────────────────────
    try:
        scrape_result = await playwright_scraper.fetch_html(
            url_str, retries=3, total_timeout=60.0
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Scrape failed for %s: %s", url_str, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to scrape product URL: {exc}",
        ) from exc

    if not scrape_result.text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The page returned no extractable text. Try a different URL.",
        )

    # ── 2. LLM extract ───────────────────────────────────────────────────
    try:
        extraction: Dict[str, Any] = gemini.analyze_product(
            url=url_str,
            scraped_text=scrape_result.text,
            description=payload.product_description,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Gemini analyze failed for %s: %s", url_str, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI extraction failed: {exc}",
        ) from exc

    # ── 3. Persist Product + Asset (upsert by URL, same as /analyze) ────
    icp_dict: Dict[str, Any] = dict(extraction.get("icp") or {})
    requested_entity = (payload.entity_type or "").strip().lower()
    if requested_entity in ("product", "service"):
        icp_dict["entity_type"] = requested_entity
    elif "entity_type" not in icp_dict:
        icp_dict["entity_type"] = "product"
    # Mark the product as BYO-leads sourced so the campaign card / journey
    # tag can surface this distinction without an extra column.
    icp_dict["bring_your_own_leads"] = True

    # Representative that signs outbound email. Merged into icp['brand'] so the
    # other overrides living there (CTA URL, brand colors) survive; blank input
    # never clears an existing rep. Mirrors the /analyze handler exactly.
    _rep_name = (payload.rep_name or "").strip()
    _rep_title = (payload.rep_title or "").strip()
    if _rep_name or _rep_title:
        _brand = dict(icp_dict.get("brand") or {})
        if _rep_name:
            _brand["rep_name"] = _rep_name[:120]
        if _rep_title:
            _brand["rep_title"] = _rep_title[:120]
        icp_dict["brand"] = _brand


    # NOTE: product_description is NOT persisted — the email generator builds
    # the grounded 3-section context fresh at send time from the product's
    # current stored fields (see sequencer._load_product).

    # Dedupe by DOMAIN NAME (registrable SLD) + entity_type — same rule as
    # /analyze and the Connectors brand cards — so spenzo.io / spenzo.ai reuse
    # ONE product. brand_key is Python, so compare over the workspace's products.
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
        existing.name = extraction.get("name") or existing.name
        existing.category = extraction.get("category") or existing.category
        existing.value_proposition = (
            extraction.get("value_proposition") or existing.value_proposition
        )
        existing.key_benefits = extraction.get("key_benefits") or existing.key_benefits or []
        existing.pricing_tier = extraction.get("pricing_tier") or existing.pricing_tier
        existing.industry_relevance = (
            extraction.get("industry_relevance") or existing.industry_relevance
        )
        # `icp` is replaced wholesale and the fresh dict comes from the model
        # extraction, which never contains `brand`. Carry the old brand forward
        # so a re-upload doesn't silently wipe the representative (or the CTA
        # URL / brand colors). Anything sent on THIS request already won above.
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
            name=extraction.get("name"),
            category=extraction.get("category"),
            value_proposition=extraction.get("value_proposition"),
            key_benefits=extraction.get("key_benefits") or [],
            pricing_tier=extraction.get("pricing_tier"),
            industry_relevance=extraction.get("industry_relevance"),
            icp=icp_dict,
            source_url=url_str,
            status="ready",
        )
        db.add(product)
        db.flush()

    asset = models_phase2.NexusProductAsset(
        product_id=product.id,
        workspace_id=product.workspace_id,
        asset_type="url",
        source=url_str,
        char_count=scrape_result.char_count,
        status="processing",
        chunks_indexed=0,
    )
    db.add(asset)
    db.commit()
    db.refresh(product)
    db.refresh(asset)

    # ── 4. Auto-index scraped URL + product description + KB text ──────
    def _compose_indexable_text() -> str:
        parts: List[str] = []
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
            parts.append(scraped[:50000])
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
                source_name=url_str,
                asset_type="url",
            )
            asset.status = "indexed"
            asset.chunks_indexed = int(chunks_indexed)
            asset.last_error = None
            asset.indexed_at = datetime.utcnow()
            db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Pinecone indexing failed for product %d (BYO-leads): %s",
                product.id,
                exc,
            )
            asset.status = "failed"
            asset.last_error = str(exc)[:1000]
            try:
                db.commit()
            except Exception:
                db.rollback()

    # ── 5. Create the campaign (a FRESH one per upload) ─────────────────
    # Each BYO upload creates its OWN campaign — same "one campaign = one run"
    # model the discovery flow (analyze.py) uses. Previously this REUSED the
    # product's oldest campaign, so every upload appeared under that first
    # campaign's OLD number and OLD created-date in the GTM Journey (the exact
    # bug reported: Spenzo showed campaign #1, dated a month ago, for leads
    # uploaded today). A new campaign gives this batch its own number + date.
    campaign_name = (
        (product.name or extraction.get("name") or "BYO-Leads Campaign").strip()[:200]
        or "BYO-Leads Campaign"
    )
    campaign_icp = dict(extraction.get("icp") or {})
    campaign_icp["bring_your_own_leads"] = True
    # Carry the representative onto the CAMPAIGN. The campaign is what the user
    # just named a rep for, and it is the level the sender context reads first —
    # so the person typed here signs this campaign's mail regardless of which
    # product row the campaign happens to hang off.
    if icp_dict.get("brand"):
        campaign_icp["brand"] = dict(icp_dict["brand"])
    campaign = NexusCampaign(
        workspace_id=workspace.id,
        product_id=product.id,
        name=campaign_name,
        icp=campaign_icp,
        status="active",
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    # Per-product campaign number (restarts at 1 per product, e.g. Spenzo →
    # 1,2,3) — the human-friendly label the GTM Journey shows. MAX+1 in raw
    # SQL; best-effort so a missing column / concurrent-run race never breaks
    # the upload (mirrors analyze.py's assignment for the discovery flow).
    try:
        db.execute(
            _sql_text(
                """UPDATE nexus_campaigns
                      SET product_campaign_number = COALESCE(
                          (SELECT MAX(product_campaign_number)
                             FROM nexus_campaigns
                            WHERE product_id = :pid AND id <> :cid), 0) + 1
                    WHERE id = :cid
                      AND product_campaign_number IS NULL"""
            ),
            {"pid": product.id, "cid": campaign.id},
        )
        db.commit()
    except Exception:  # noqa: BLE001 — column missing (pre-migration) or unique race
        db.rollback()

    try:
        db.add(NexusCampaignLaunch(campaign_id=campaign.id))
        db.commit()
    except Exception:  # noqa: BLE001
        logger.warning(
            "analyze_from_leads: failed to record campaign launch for %s",
            campaign.id,
            exc_info=True,
        )
        db.rollback()

    # ── 6. Bulk-insert the user-supplied leads + enroll in sequence ─────
    #
    # Strategy: every operation runs in 3 batched SQL statements (one per
    # table) wrapped in a single transaction. This keeps the launch
    # request under the API Gateway / Lambda 29s timeout even for ~500
    # leads, where the prior per-lead path (4 commits × 100 leads =
    # ~10s of fsync overhead) was hitting the wall.
    #
    # Trade-off vs. the per-lead helpers: no preview placeholder is written
    # here. The next sequencer tick (~1 min later) generates the real
    # touchpoint row when it picks up each new lead_sequence — same end
    # state, just no preview row for the first minute.
    #
    # Identical end state to the Apollo flow:
    #   • Row in nexus_global_leads (deduped by email)
    #   • Row in nexus_leads (deduped by workspace+campaign+global_lead)
    #   • Row in nexus_lead_sequences with status='active', next_action_at=now
    #     → sequencer picks it up on its next tick and generates email +
    #       LinkedIn DM + InMail automatically.
    from nexus.models_phase4 import NexusSequence  # type: ignore[import-not-found]
    import json as _json

    attached = 0
    failed_rows: List[Dict[str, Any]] = []

    # Pre-compute per-lead payloads. Validation already screened out
    # blank fields, so we trust the inputs are well-formed here.
    now = datetime.utcnow()
    upload_signal_json = {
        "source": "manual_upload",
        "uploaded_at": now.isoformat(),
    }
    prepared: List[Dict[str, Any]] = []
    for idx, lead in enumerate(payload.leads):
        first, last = _split_name(lead.name)
        prepared.append({
            "row": idx + 1,
            "email": lead.email.strip().lower(),
            "first_name": first,
            "last_name": last,
            "role": lead.role.strip(),
            "company_name": lead.company.strip(),
            "company_domain": _company_domain_from_email(lead.email),
            "source": "manual_upload",
            "linkedin_url": _normalize_linkedin(lead.linkedin_url),
            # Optional user-supplied columns (None when the CSV omitted them).
            # location → person_city (Journey renders it verbatim), phone →
            # Contact column, match_score → nexus_leads.icp_score (None = blank).
            "person_city": lead.location,
            "phone": lead.phone,
            "match_score": lead.match_score,
            "display_name": lead.name.strip(),
            "display_role": lead.role.strip(),
            "display_company": lead.company.strip(),
        })

    try:
        # ---- 6a. Bulk-upsert global leads (email is UNIQUE) ----------
        # COALESCE(...) in the SET clause preserves any human-curated
        # values already on the row (matches `_upsert_global_lead`'s
        # "only fill missing fields" semantics, just in batch SQL).
        gl_sql = _sql_text("""
            INSERT INTO nexus_global_leads
                (email, first_name, last_name, role, company_domain,
                 company_name, source, linkedin_url, person_city, phone,
                 status, created_at, updated_at)
            VALUES
                (:email, :first_name, :last_name, :role, :company_domain,
                 :company_name, :source, :linkedin_url, :person_city, :phone,
                 'new', :now, :now)
            ON CONFLICT (email) DO UPDATE
            SET first_name     = COALESCE(NULLIF(nexus_global_leads.first_name, ''), EXCLUDED.first_name),
                last_name      = COALESCE(NULLIF(nexus_global_leads.last_name, ''), EXCLUDED.last_name),
                role           = COALESCE(NULLIF(nexus_global_leads.role, ''), EXCLUDED.role),
                company_domain = COALESCE(NULLIF(nexus_global_leads.company_domain, ''), EXCLUDED.company_domain),
                company_name   = COALESCE(NULLIF(nexus_global_leads.company_name, ''), EXCLUDED.company_name),
                source         = COALESCE(NULLIF(nexus_global_leads.source, ''), EXCLUDED.source),
                linkedin_url   = COALESCE(NULLIF(nexus_global_leads.linkedin_url, ''), EXCLUDED.linkedin_url),
                -- Only fill Location/Contact when the row didn't already have one
                -- (matches the "preserve human-curated values" upsert semantics).
                person_city    = COALESCE(NULLIF(nexus_global_leads.person_city, ''), EXCLUDED.person_city),
                phone          = COALESCE(NULLIF(nexus_global_leads.phone, ''), EXCLUDED.phone),
                updated_at     = EXCLUDED.updated_at
            RETURNING id, email
        """)
        gl_id_by_email: Dict[str, int] = {}
        for r in prepared:
            res = db.execute(gl_sql, {**r, "now": now}).first()
            if res is not None:
                gl_id_by_email[res[1]] = int(res[0])
        # Note: we still loop here, but each iteration is a single fast
        # statement with NO commit in between (commit happens once at
        # the end). On RDS this is ~5–10ms per row instead of the
        # ~80–120ms the prior per-lead helpers cost (4 commits each).
        # For true batch RETURNING we'd need `executemany` with a
        # PostgreSQL VALUES list — saving for a future pass if needed.

        # BYO (customer-uploaded) leads are the customer's OWN explicit choice,
        # so they must NOT be re-qualified by the ICP intent agent (Agent #10) —
        # gating them silently DROPPED leads the user deliberately uploaded
        # (score < cutoff → never enrolled → no outreach). Always enroll uploaded
        # leads inline. The global intent gate still governs the Apollo DISCOVERY
        # path (discover_for_campaign); this override is scoped to BYO uploads.
        gate_on = False

        # ---- 6b. Bulk-upsert workspace leads (one per global_lead) ---
        nl_sql = _sql_text("""
            INSERT INTO nexus_leads
                (workspace_id, campaign_id, product_id, global_lead_id,
                 icp_score, signals, created_at, updated_at)
            VALUES
                (:ws, :cid, :pid, :gid, :icp_score, CAST(:signals AS JSONB), :now, :now)
            ON CONFLICT (workspace_id, campaign_id, global_lead_id) DO UPDATE
            SET product_id = COALESCE(nexus_leads.product_id, EXCLUDED.product_id),
                -- Uploaded match score is the customer's explicit input, so it
                -- WINS when provided; a blank (NULL) upload keeps any existing
                -- score rather than wiping it.
                icp_score  = COALESCE(EXCLUDED.icp_score, nexus_leads.icp_score),
                -- BYO re-upload is the customer's explicit choice, so drop any
                -- stale ICP verdict ('intent') left by a PRIOR gated run — else a
                -- previously-'rejected' lead stays hidden from the GTM Journey
                -- (which filters out rejected) even though it's now enrolled.
                signals    = (COALESCE(nexus_leads.signals, '{}'::jsonb) || EXCLUDED.signals) - 'intent',
                -- Re-uploaded lead = THIS run's lead. Every run fence (the
                -- New-Run UI's latest_run filter, auto-enroll) keys on
                -- created_at vs MAX(launched_at), and this flow REUSES the
                -- campaign + records a new launch per upload — keeping the
                -- old created_at would report the lead as attached while the
                -- UI hides it (same bug as the campaign-move fence-out).
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at
        """)
        for r in prepared:
            gid = gl_id_by_email.get(r["email"])
            if gid is None:
                failed_rows.append({"row": r["row"], "reason": "Global lead upsert returned no id."})
                continue
            sig = {
                **upload_signal_json,
                "display_name": r["display_name"],
                "display_role": r["display_role"],
                "display_company": r["display_company"],
            }
            if gate_on:
                # Mark pending so the intent_sweep scores it; enrolled only if accepted.
                sig["intent"] = {"status": "pending", "attempts": 0}
            db.execute(nl_sql, {
                "ws": workspace.id,
                "cid": campaign.id,
                "pid": product.id,
                "gid": gid,
                "icp_score": r.get("match_score"),  # None ⇒ blank (unscored)
                "signals": _json.dumps(sig),
                "now": now,
            })

        # ---- 6c. Look up the campaign's primary sequence ------------
        # First check (workspace, campaign), then fall back to any
        # workspace sequence. If NEITHER exists — common for a brand-new
        # workspace whose first action is a manual upload (no prior
        # Apollo run has ever created a default sequence) — auto-create
        # one with the same 4-step cadence Apollo uses.
        # Without this, the leads would land in the DB but never get
        # enrolled, and the sequencer would never email them. The user
        # explicitly asked: "no lead missed."
        seq = (
            db.query(NexusSequence)
            .filter(
                NexusSequence.workspace_id == workspace.id,
                NexusSequence.campaign_id == campaign.id,
            )
            .order_by(NexusSequence.id.asc())
            .first()
        ) or (
            db.query(NexusSequence)
            .filter(
                NexusSequence.workspace_id == workspace.id,
                NexusSequence.campaign_id.is_(None),
            )
            .order_by(NexusSequence.id.asc())
            .first()
        )
        if seq is None:
            # Reuse the same helper Apollo's discover_for_campaign uses
            # so the cadence + step structure stays consistent across
            # both code paths. _ensure_default_sequence is idempotent.
            from nexus.services.discover_for_campaign import (  # type: ignore[import-not-found]
                _ensure_default_sequence,
            )
            seq = _ensure_default_sequence(
                db,
                workspace_id=workspace.id,
                campaign_id=campaign.id,
                name=campaign_name,
            )
            logger.info(
                "analyze_from_leads: auto-created default sequence (id=%s) "
                "for workspace=%s campaign=%s — workspace had none.",
                seq.id, workspace.id, campaign.id,
            )

        # ---- 6d. Bulk-insert lead_sequences (skip if already enrolled) -
        # No UNIQUE constraint on nexus_lead_sequences, so we manually
        # guard against duplicates via a NOT EXISTS subquery. Same
        # idempotency contract as _enroll_in_sequence.
        if gate_on:
            # GATED: leads were stamped 'pending' in 6b. Do NOT enroll here —
            # flag the campaign so the intent_sweep scores them and enrolls
            # ONLY the Agent #10-accepted ones (same gate as discovery). The
            # sequence ensured in 6c is what the sweep enrolls accepted leads into.
            attached = sum(
                1 for r in prepared if gl_id_by_email.get(r["email"]) is not None
            )
            db.execute(
                _sql_text(
                    "UPDATE nexus_campaigns SET icp = COALESCE(icp, '{}'::jsonb) "
                    "|| jsonb_build_object('intent_pending', true) WHERE id = :cid"
                ),
                {"cid": campaign.id},
            )
        elif seq is not None:
            # NOT EXISTS is the fast path; ON CONFLICT makes it RACE-PROOF
            # against the uq_lead_seq unique constraint (workspace_id, lead_id,
            # campaign_id) when two schedulers enroll the same lead at once.
            #
            # 2026-08-05 BUG-FIX: the guard keyed on `sequence_id` while the
            # constraint keys on `campaign_id`. Campaigns without their own
            # sequence share the workspace default (campaign_id IS NULL), so a
            # lead already enrolled via ANY earlier campaign matched the guard
            # and the insert was silently skipped — while the separate status
            # UPDATE still flipped the lead to 'queued'. The UI therefore showed
            # a queued lead that could never be emailed. Guard now mirrors the
            # constraint exactly, so re-uploading an address into a NEW campaign
            # enrolls it, and re-uploading into the SAME campaign still no-ops.
            ls_sql = _sql_text("""
                INSERT INTO nexus_lead_sequences
                    (workspace_id, campaign_id, sequence_id, lead_id,
                     current_step, status, next_action_at, enrolled_at)
                SELECT :ws, :cid, :sid, :gid, 0, 'active', :now, :now
                WHERE NOT EXISTS (
                    SELECT 1 FROM nexus_lead_sequences
                    WHERE workspace_id = :ws
                      AND campaign_id  = :cid
                      AND lead_id      = :gid
                )
                ON CONFLICT (workspace_id, lead_id, campaign_id) DO NOTHING
            """)
            # Bump global lead status 'new' → 'queued' so the GTM Journey
            # badge reflects an email is pending. Idempotent (the WHERE
            # leaves contacted/replied/etc. rows alone).
            status_sql = _sql_text("""
                UPDATE nexus_global_leads
                   SET status = 'queued'
                 WHERE id = :gid
                   AND (status IS NULL OR status = '' OR status = 'new')
            """)
            for r in prepared:
                gid = gl_id_by_email.get(r["email"])
                if gid is None:
                    continue
                db.execute(ls_sql, {
                    "ws": workspace.id,
                    "cid": campaign.id,
                    "sid": seq.id,
                    "gid": gid,
                    "now": now,
                })
                db.execute(status_sql, {"gid": gid})
                # Per-workspace status mirror (authoritative for display).
                db.execute(_sql_text(
                    "UPDATE nexus_leads SET status = 'queued', updated_at = :now "
                    "WHERE global_lead_id = :gid AND workspace_id = :ws "
                    "  AND (status IS NULL OR status = '' OR status = 'new')"
                ), {"gid": gid, "ws": workspace.id, "now": now})
                attached += 1
        else:
            # No sequence configured in the workspace yet — log loudly
            # so we don't silently ship leads that never get an email.
            # The lead rows still exist in the DB, so the operator can
            # configure a sequence later and re-enroll via a script.
            logger.warning(
                "analyze_from_leads: workspace %s has NO sequence — "
                "%d leads inserted but not enrolled. Configure a sequence "
                "and call /sequences/enroll-all to backfill.",
                workspace.id, len(prepared),
            )
            attached = len(prepared)  # leads still added; sequence missing

        # Single commit for all 4 batched SQL passes.
        db.commit()
        # Gated upload: kick Agent #10 scoring now (mirrors /analyze) so the
        # pending leads are scored promptly; only accepted ones get enrolled.
        if gate_on:
            try:
                from nexus.services.intent_sweep import run_intent_sweep_bg
                background_tasks.add_task(run_intent_sweep_bg, campaign_id=campaign.id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "analyze_from_leads: failed to schedule sweep for campaign %s",
                    campaign.id,
                )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception(
            "analyze_from_leads: bulk enrollment failed for ws=%s, campaign=%s, n=%d",
            workspace.id, campaign.id, len(prepared),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Failed to insert leads — campaign was created but enrollment did not complete. Contact support.",
                "errors": [str(exc)[:240]],
            },
        ) from exc

    # If we couldn't attach any leads at all, the campaign has nothing to
    # do — surface that as a 500 so the wizard shows an error instead of
    # silently routing the user to an empty journey list.
    if attached == 0:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "message": "Failed to insert any leads. Try again or contact support.",
                "errors": failed_rows,
            },
        )

    # ── 7. Inline LinkedIn drafts pass — Lambda-safe behaviour ───────────
    # We do NOT run a full LinkedIn draft pass inline. ~3s/lead Gemini
    # calls × 100 leads = ~5 min, which blows past API Gateway's 29s
    # ceiling and the response would be cut off mid-write.
    #
    # The scheduler (apps/backend/nexus/routers/scheduler.py) invokes
    # BOTH process_due_sequences (emails) AND process_linkedin_drafts
    # (DM + InMail) on every tick (~1 min cadence). With:
    #   • DEFAULT_MAX_ROWS_PER_TICK = 25 (emails)
    #   • LINKEDIN_DRAFTS_PER_TICK  = 25 (LinkedIn)
    # 100 manual-upload leads finish both passes inside ~4 ticks ≈ 4 min
    # AFTER the launch response returns.
    #
    # Coverage guarantees (verified by audit + the company-dedup fix in
    # sequencer.py): the candidate queries pick up EVERY manual_upload
    # lead that doesn't already have an outbound row in the corresponding
    # table, with no source-based skip. So every lead in this upload WILL
    # land an email + DM + InMail; the only variable is how soon.
    #
    # The log line below is the durable trace operators search for to
    # confirm an upload's enrollment count matches what the wizard said.
    logger.info(
        "analyze_from_leads: enrolled %d manual_upload leads for ws=%s campaign=%s. "
        "Email + LinkedIn DM + InMail generation handled by background scheduler — "
        "expect all touchpoints within ~%d minutes.",
        attached, workspace.id, campaign.id,
        max(1, (attached + 24) // 25),  # ticks needed to drain at 25/tick
    )

    discovery_summary = AnalyzeDiscoverySummary(
        status="completed",
        leads_attached=attached,
        by_source={"manual_upload": attached},
        errors=[r["reason"] for r in failed_rows],
    )

    return AnalyzeResponse(
        product=product,  # type: ignore[arg-type]
        asset=asset,  # type: ignore[arg-type]
        extraction=extraction,
        campaign=AnalyzeCampaignSummary(
            id=campaign.id,
            name=campaign.name,
            status=campaign.status,
        ),
        discovery=discovery_summary,
    )
