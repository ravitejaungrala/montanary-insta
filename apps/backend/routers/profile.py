import asyncio
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from core.database import get_db
from core.auth import get_password_hash
from routers.auth import get_current_user
import models
import schemas
from typing import List, Optional
import logging
import uuid
import io
from fastapi import File, UploadFile, Form
from core.s3_utils import upload_fileobj_to_s3
from services.doc_processor import extract_text_from_file
from services.plan_service import check_quota
from services.team_service import is_team_member, is_franchisee


def _reject_if_member(user: models.User, action: str = "manage brand DNA") -> None:
    """Team members AND franchisees cannot edit brand DNA — that belongs to
    the Master. Franchisees pay their own subscription but still operate
    under the Master's brand identity."""
    if is_team_member(user) or is_franchisee(user):
        who = "Franchisees" if is_franchisee(user) else "Team members"
        raise HTTPException(
            status_code=403,
            detail=f"{who} cannot {action}. Ask your brand owner.",
        )

logger = logging.getLogger("pipelyt.profile")

router = APIRouter()

@router.get("/profile", response_model=schemas.User)
async def get_profile(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Team members AND franchisees inherit Company Name + business_dna from
    # their upstream Master. The Master owns the brand; both member types
    # post under it. We resolve it dynamically on read (instead of
    # persisting to the downstream user's row) so changes to the Master's
    # DNA propagate immediately and no drift occurs. business_dna is
    # FILTERED to the products the user is assigned, so CampaignBrief /
    # Logo chip / Aspect Ratio chip can render brand names + logos without
    # leaking other brands.
    from services.team_service import is_franchisee, get_member_assigned_dna_ids
    upstream_master_id = (
        current_user.team_owner_id
        or getattr(current_user, "franchise_of_id", None)
    )
    if upstream_master_id:
        admin = (
            db.query(models.User)
            .filter(models.User.id == upstream_master_id)
            .first()
        )
        if admin:
            admin_company = (
                admin.company_name
                or (admin.business_dna or {}).get("company_name")
            )
            if admin_company and not current_user.company_name:
                # Response-only mutation — no db.commit() so the row is
                # untouched when the session closes.
                current_user.company_name = admin_company

            # Team members inherit product_selection from their master —
            # they share the plan so they share the product surface. Only
            # applies to team members (team_owner_id set), NOT franchisees
            # (they run independent operations with their own selection).
            if current_user.team_owner_id and getattr(admin, "product_selection", None):
                current_user.product_selection = admin.product_selection

            # Graft admin DNA (company-level keys) + filter products down to
            # the downstream user's assigned list. If they have none assigned,
            # expose the full admin products dict so they can at least see
            # SOMETHING.
            admin_dna = dict(admin.business_dna or {})
            assigned_ids = set(get_member_assigned_dna_ids(current_user))
            all_products = admin_dna.get("products") or {}
            if assigned_ids:
                admin_dna["products"] = {
                    pid: p for pid, p in all_products.items() if pid in assigned_ids
                }
            current_user.business_dna = admin_dna
    return current_user


@router.get("/profile/effective-dna")
async def get_effective_dna(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Compact DNA info used by the content composer.

    For admins: returns their own business_dna.products keyed dict (the UI
    already consumes `business_dna`, but this endpoint mirrors the shape
    members get so both paths can share render code).

    For members: returns ONLY the product dict they are assigned to, plus
    a boolean `locked=True` so the UI renders a read-only chip instead of
    a dropdown. No other team brands are exposed to the member.
    """
    if is_team_member(current_user):
        from services.team_service import get_member_assigned_dna_ids
        admin = (
            db.query(models.User)
            .filter(models.User.id == current_user.team_owner_id)
            .first()
        )
        products = ((admin.business_dna or {}).get("products") or {}) if admin else {}
        ids = get_member_assigned_dna_ids(current_user)
        assigned = [
            {
                "product_id": pid,
                "product_name": (products.get(pid) or {}).get("product_name")
                    or (products.get(pid) or {}).get("company_name") or pid,
                "logo_url": (products.get(pid) or {}).get("logo_url"),
                "product": products.get(pid),
            }
            for pid in ids
        ]
        # Single-product locked flag retained for legacy clients — true iff
        # the member has exactly one brand assigned.
        single_locked = len(assigned) == 1
        return {
            "locked": single_locked,
            "assigned_product_id": (ids[0] if ids else None),
            "assigned_product": assigned[0]["product"] if assigned else None,
            "assigned_products": assigned,      # new: full list
            "admin_company_name": (admin.company_name if admin else None),
        }
    products = (current_user.business_dna or {}).get("products") or {}
    return {
        "locked": False,
        "products": [
            {
                "product_id": pid,
                "product_name": (p.get("product_name") or p.get("company_name") or pid),
                "logo_url": p.get("logo_url"),
            }
            for pid, p in products.items()
        ],
    }

@router.put("/profile", response_model=schemas.User)
async def update_profile(
    profile_data: schemas.UserProfileUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # Update fields if provided
    if profile_data.full_name is not None:
        current_user.full_name = profile_data.full_name
    if profile_data.username is not None:
        # Check if username is taken
        existing = db.query(models.User).filter(models.User.username == profile_data.username, models.User.id != current_user.id).first()
        if existing:
            raise HTTPException(status_code=400, detail="Username already taken")
        current_user.username = profile_data.username
    if profile_data.profile_picture_url is not None:
        current_user.profile_picture_url = profile_data.profile_picture_url
    if profile_data.timezone is not None:
        current_user.timezone = profile_data.timezone
    if profile_data.company_name is not None:
        current_user.company_name = profile_data.company_name
    if profile_data.company_description is not None:
        current_user.company_description = profile_data.company_description
    if profile_data.product_details is not None:
        current_user.product_details = profile_data.product_details
    if profile_data.product_url is not None:
        current_user.product_url = profile_data.product_url
    if profile_data.business_url is not None:
        # Persist the canonical company URL. Previously this field was silently
        # dropped, so the Account Details "Business URL" input always reverted
        # to the original value after save even though the freshly-extracted
        # DNA was being stored against the new URL.
        current_user.business_url = profile_data.business_url
    if profile_data.business_dna is not None:
        _reject_if_member(current_user)
        # Block deletion of a product that team members are currently
        # assigned to — forces admin to reassign first. Compare product_id
        # sets between the incoming DNA and the set of assignments in the DB.
        new_dna = profile_data.business_dna
        new_product_ids = set()
        if isinstance(new_dna, dict):
            new_product_ids = set((new_dna.get("products") or {}).keys())
        old_dna = current_user.business_dna or {}
        old_product_ids = set((old_dna.get("products") or {}).keys())
        removed_ids = old_product_ids - new_product_ids
        if removed_ids:
            # Block removal if ANY active team member is assigned the product
            # — either via the legacy single column OR the list column.
            from services.team_service import get_member_assigned_dna_ids
            members = (
                db.query(models.User)
                .filter(
                    models.User.team_owner_id == current_user.id,
                    models.User.disabled.is_(False),
                )
                .all()
            )
            conflicts: dict = {}
            for m in members:
                mids = set(get_member_assigned_dna_ids(m))
                for pid in mids & removed_ids:
                    conflicts.setdefault(pid, []).append(m.email)
            if conflicts:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Cannot remove brand(s) currently assigned to team members. Reassign them first.",
                        "conflicts": conflicts,
                    },
                )

        # Quota check (brand count)
        if isinstance(new_dna, dict) and "products" in new_dna:
            check_quota(db, current_user, "brands", additional=0)

        # ─────────────────────────────────────────────────────────────
        # AUTO-STYLE ROUTING: backfill `category` only when it's missing.
        # The DNA extractor sets `category` at first save (legacy 4-value
        # scheme); this categorizer runs when that field is blank so old
        # or programmatically-created DNAs still land in a canonical
        # bucket for style routing. User's own dropdown selection is
        # always respected — we never overwrite a non-blank value.
        # ─────────────────────────────────────────────────────────────
        if isinstance(new_dna, dict):
            incoming_cat = str(new_dna.get("category") or "").strip().lower()
            if not incoming_cat:
                try:
                    from services.business_categorizer import categorize_business_dna
                    cat_key, is_custom = categorize_business_dna(new_dna)
                    new_dna["category"] = cat_key
                    logger.info(
                        f"[profile] user {current_user.email} auto-categorized DNA → "
                        f"{cat_key!r} (custom={is_custom})"
                    )
                except Exception as _cat_exc:
                    # Non-fatal — pipeline uses the default 'auto' path.
                    logger.warning(
                        f"[profile] auto-categorize failed for {current_user.email}: "
                        f"{_cat_exc} — leaving category unset"
                    )

        current_user.business_dna = profile_data.business_dna
    if profile_data.pricing_plan is not None:
        current_user.pricing_plan = profile_data.pricing_plan
    
    # Handle password change
    if profile_data.password:
        current_user.hashed_password = get_password_hash(profile_data.password)
        
    db.commit()
    db.refresh(current_user)
    return current_user

@router.put("/profile/image", response_model=schemas.User)
async def update_profile_image(
    image_data: schemas.UserProfileImageUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    current_user.profile_picture_url = image_data.profile_picture_url
    db.commit()
    db.refresh(current_user)
    return current_user

@router.post("/onboarding/complete", response_model=schemas.User)
async def complete_onboarding(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    # M3 fix — defensive server-side guard. Frontend already blocks Continue
    # until required fields are populated, but a scripted client could POST
    # here directly and reach the dashboard with an empty profile. Full name
    # is captured on Step 1 and is the minimum viable identity.
    if not (current_user.full_name or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Full name is required before completing onboarding.",
        )
    current_user.onboarded = True
    db.commit()
    db.refresh(current_user)
    return current_user


_VALID_PRODUCT_SELECTIONS = {"pipelyt", "gtm", "both"}


@router.put("/profile/product-selection", response_model=schemas.User)
async def update_product_selection(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Set which product surface the user wants — pipelyt / gtm / both.

    Called from onboarding step 4 (first time) and from the Settings page
    (when a user wants to add / remove a surface later). Team members
    inherit their master's selection — they cannot set their own, and the
    endpoint returns 403 for them so the UI can hide the tile.
    """
    if current_user.team_owner_id:
        # Team members share the master's product surface; they don't get
        # their own selection. Silently no-op with a clear 403 so the
        # frontend can hide the picker.
        raise HTTPException(
            status_code=403,
            detail="Team members inherit the admin's product selection.",
        )
    choice = (payload or {}).get("product_selection")
    if choice not in _VALID_PRODUCT_SELECTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"product_selection must be one of {sorted(_VALID_PRODUCT_SELECTIONS)}",
        )
    current_user.product_selection = choice
    db.commit()
    db.refresh(current_user)
    return current_user

@router.post("/profile/extract-dna")
async def extract_dna(
    url_request: dict,
    db: Session = Depends(get_db), # Added db
    current_user: models.User = Depends(get_current_user)
):
    _reject_if_member(current_user, action="extract brand DNA")
    from services.business_dna_service import extract_business_dna
    url = url_request.get("url")
    is_product = url_request.get("is_product", False)

    # Empty / whitespace-only URLs must NOT be sent to the extractor —
    # `https://r.jina.ai/` (empty URL) returns Jina AI's own homepage,
    # which then gets stored as the user's brand DNA. Guard here so no
    # caller (frontend bug, blank field, retry loop) can pollute a
    # user's business_dna with garbage.
    if not isinstance(url, str) or not url.strip():
        raise HTTPException(
            status_code=400,
            detail="A non-empty URL is required to extract Business DNA.",
        )
    url = url.strip()

    # 1. Quota Check
    if is_product:
        check_quota(db, current_user, "brands")
    try:
        from fastapi.concurrency import run_in_threadpool
        dna = await run_in_threadpool(
            extract_business_dna,
            url, 
            is_product=is_product
        )
        return dna
    except Exception as e:
        logger.error(f"DNA Extraction Error: {str(e)}")
        raise HTTPException(status_code=422, detail=str(e))

@router.post("/profile/dna-document")
async def upload_dna_document(
    entity_id: str = Form(...), # 'company' or a product name/ID
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Uploads a document to S3, extracts its text, and saves a reference in the user's Business DNA.
    """
    _reject_if_member(current_user, action="upload brand documents")
    try:
        # 1. Read file content first (to avoid closed file issues after S3 upload)
        file_content = await file.read()
        file_extension = file.filename.split('.')[-1]
        unique_filename = f"dna_docs/{current_user.id}/{entity_id}/{uuid.uuid4()}.{file_extension}"

        # 2. + 3. Run S3 upload and text extraction concurrently — they both
        #    operate on file_content but are independent, so we can overlap the
        #    AWS round-trip with the CPU-bound PyPDF2/python-docx parse. Cuts
        #    wall time roughly in half vs the previous sequential version.
        s3_url, extracted_text = await asyncio.gather(
            run_in_threadpool(
                upload_fileobj_to_s3, io.BytesIO(file_content), unique_filename
            ),
            run_in_threadpool(
                extract_text_from_file, file_content, file.filename
            ),
        )
        if not s3_url:
            raise HTTPException(status_code=500, detail="Failed to upload document to S3")

        # 3. Update JSONB DNA
        dna = current_user.business_dna or {}
        doc_obj = {
            "id": str(uuid.uuid4()),
            "name": file.filename,
            "url": s3_url,
            "text": extracted_text,
            "size": len(file_content)
        }

        if entity_id == 'company':
            if "documents" not in dna: dna["documents"] = []
            dna["documents"].append(doc_obj)
        else:
            if "products" not in dna: dna["products"] = {}
            if entity_id not in dna["products"]:
                dna["products"][entity_id] = {"name": entity_id}
            
            prod_dna = dna["products"][entity_id]
            if "documents" not in prod_dna: prod_dna["documents"] = []
            prod_dna["documents"].append(doc_obj)

        current_user.business_dna = dna
        # MutableDict tracks top-level key mutations, but not nested list
        # appends like dna["documents"].append(...). Force-mark the column
        # dirty so the UPDATE actually includes business_dna.
        flag_modified(current_user, "business_dna")
        db.add(current_user)
        db.commit()
        db.refresh(current_user)

        return {"message": "Document uploaded successfully", "doc": doc_obj}

    except Exception as e:
        logger.error(f"Document Upload Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/profile/dna-document/{entity_id}/{doc_id}")
async def delete_dna_document(
    entity_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Removes a document reference from the Business DNA."""
    _reject_if_member(current_user, action="delete brand documents")
    try:
        dna = current_user.business_dna or {}
        
        if entity_id == 'company':
            if "documents" in dna:
                dna["documents"] = [d for d in dna["documents"] if d["id"] != doc_id]
        else:
            if "products" in dna and entity_id in dna["products"]:
                prod_dna = dna["products"][entity_id]
                if "documents" in prod_dna:
                    prod_dna["documents"] = [d for d in prod_dna["documents"] if d["id"] != doc_id]

        current_user.business_dna = dna
        flag_modified(current_user, "business_dna")
        db.add(current_user)
        db.commit()

        return {"message": "Document removed successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
