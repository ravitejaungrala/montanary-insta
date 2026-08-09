"""
POST /nexus/refine-summary

Apply a user instruction (or explicit field overrides) to a Nexus
Product's stored summary. Two modes:

  * `edits={...}` — direct PATCH of fields on the product row. Skips the LLM.
  * `instruction="..."` — runs the Llama refine-summary prompt against
    a textual snapshot of the product summary, then writes the result
    back into `value_proposition`.

Mirrors legacy nexus/server/routes/refineSummary.js — kept stateless
and idempotent; safe to retry from the UI.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.database import get_db
from models import User
from nexus.deps import (  # type: ignore[import-not-found]
    get_nexus_user,
    require_current_workspace,
    trial_guard,
)
from nexus import models_phase2
from nexus.schemas_phase2 import RefineSummaryRequest, RefineSummaryResponse
from nexus.services import gemini

logger = logging.getLogger("pipelyt.nexus.refine_summary")

router = APIRouter(prefix="/nexus/refine-summary", tags=["Nexus — Refine Summary"])


_ALLOWED_EDIT_FIELDS = {
    "name",
    "category",
    "value_proposition",
    "key_benefits",
    "pricing_tier",
    "industry_relevance",
    "icp",
}


def _summary_snapshot(product: models_phase2.NexusProduct) -> str:
    """Compose a stable text representation of the current summary so
    the LLM has the same view the user sees in the UI."""
    benefits = product.key_benefits or []
    benefits_block = "\n".join(f"- {b}" for b in benefits) or "(no benefits captured)"
    return (
        f"Name: {product.name or ''}\n"
        f"Category: {product.category or ''}\n"
        f"Pricing tier: {product.pricing_tier or ''}\n"
        f"Industry relevance: {product.industry_relevance or ''}\n"
        f"\nValue proposition:\n{product.value_proposition or ''}\n"
        f"\nKey benefits:\n{benefits_block}\n"
    )


@router.post("", response_model=RefineSummaryResponse)
def refine_summary(
    payload: RefineSummaryRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
    _trial=Depends(trial_guard),
):
    product = (
        db.query(models_phase2.NexusProduct)
        .filter(
            models_phase2.NexusProduct.id == payload.product_id,
            models_phase2.NexusProduct.workspace_id == workspace.id,
        )
        .first()
    )
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found in this workspace.",
        )

    if not payload.instruction and not payload.edits:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either `instruction` (LLM) or `edits` (direct patch).",
        )

    refined_text: str | None = None

    # Direct edits first (cheaper) ─────────────────────────────────────
    if payload.edits:
        for field, value in payload.edits.items():
            if field not in _ALLOWED_EDIT_FIELDS:
                logger.info(
                    "refine_summary skipped disallowed field=%s on product=%s",
                    field,
                    product.id,
                )
                continue
            setattr(product, field, value)

    # LLM refinement ──────────────────────────────────────────────────
    if payload.instruction and payload.instruction.strip():
        try:
            refined_text = gemini.refine_summary(
                current_summary=_summary_snapshot(product),
                instruction=payload.instruction.strip(),
            )
        except Exception as exc:
            logger.exception("LLM refine failed for product %s: %s", product.id, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Refinement failed: {exc}",
            ) from exc

        if refined_text:
            # Store the new summary text in value_proposition — the
            # original short tagline can be re-derived by the UI if
            # needed; this matches the legacy contract where the
            # frontend overwrote the full block.
            product.value_proposition = refined_text.strip()

    db.commit()
    db.refresh(product)

    return RefineSummaryResponse(
        product=product,  # type: ignore[arg-type]
        refined_summary=refined_text,
    )


# ───────────────────────────────────────────────────────────────────────────
# Preview-mode refine — used by the NewCampaign wizard BEFORE any Product
# row exists. Stateless LLM call: takes the current summary text + an
# instruction, returns the refined text. No DB writes, no auth-product
# lookup. Workspace is still required so anonymous callers can't burn
# Gemini tokens.
# ───────────────────────────────────────────────────────────────────────────


@router.post("/preview")
def refine_summary_preview(
    body: Dict[str, Any] = Body(...),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
) -> Dict[str, str]:
    current = str((body or {}).get("current_summary") or "").strip()
    instruction = str((body or {}).get("instruction") or "").strip()
    if not current:
        raise HTTPException(status_code=400, detail="current_summary is required")
    if not instruction:
        raise HTTPException(status_code=400, detail="instruction is required")

    try:
        refined = gemini.refine_summary(current_summary=current, instruction=instruction)
    except Exception as exc:
        logger.exception("preview refine_summary failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Refinement failed: {exc}") from exc
    return {"refined_summary": (refined or "").strip()}
