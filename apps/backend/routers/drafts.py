from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List
import json
from core.database import get_db
from models import Draft, PublishedPost, User
from routers.auth import block_user_writes, get_current_user
from services.team_service import is_team_member, get_team_scope_user_ids
import schemas

router = APIRouter(dependencies=[Depends(block_user_writes)])  # Users blocked from writes

@router.post("/drafts", response_model=schemas.Draft)
async def create_draft(draft_in: schemas.DraftCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Members: resolve DNA tag against their assigned list (any allowed,
    # falls back to primary if the payload's id isn't one of theirs).
    dna_product_id = draft_in.dna_product_id
    if is_team_member(current_user):
        from services.team_service import resolve_member_publish_dna
        dna_product_id = resolve_member_publish_dna(current_user, dna_product_id)

    draft = Draft(
        content=draft_in.content,
        image_url=draft_in.image_url,
        thumbnail_url=draft_in.thumbnail_url,
        slide_thumbnail_urls=draft_in.slide_thumbnail_urls,
        media_type=draft_in.media_type,
        targets=json.dumps(draft_in.targets),
        user_id=current_user.id,
        dna_product_id=dna_product_id,
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)
    try:
        draft.targets = json.loads(draft.targets) if isinstance(draft.targets, str) else draft.targets
    except:
        draft.targets = {}
    return draft

@router.get("/drafts", response_model=List[schemas.Draft])
async def get_drafts(
    # Brand-filter params (same set as /posts/published). Admin-only;
    # when present, drafts across the filtered team scope are returned
    # instead of the admin's own. Members always see only their own.
    member_user_ids: str = None,
    dna_product_ids: str = None,
    filter_company: str = None,
    filter_country: str = None,
    filter_state: str = None,
    filter_city: str = None,
    filter_pin_code: str = None,
    user_type: str = None,   # 'team' | 'franchise' | None (both)
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from services.team_service import resolve_filtered_visible_ids
    visible_ids = resolve_filtered_visible_ids(
        db, current_user,
        member_user_ids=member_user_ids, dna_product_ids=dna_product_ids,
        filter_company=filter_company, filter_country=filter_country,
        filter_state=filter_state, filter_city=filter_city,
        filter_pin_code=filter_pin_code,
        user_type=user_type,
    )
    query = db.query(Draft).filter(Draft.user_id.in_(visible_ids))
    brand_ids = []
    if dna_product_ids:
        brand_ids = [x.strip() for x in dna_product_ids.split(",") if x.strip()]
    if brand_ids:
        query = query.filter(Draft.dna_product_id.in_(brand_ids))
    drafts = query.order_by(Draft.updated_at.desc()).all()
    for d in drafts:
        try:
            d.targets = json.loads(d.targets) if isinstance(d.targets, str) else d.targets
        except Exception:
            d.targets = {}
    return drafts

@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    draft = db.query(Draft).filter(Draft.id == draft_id, Draft.user_id == current_user.id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    try:
        draft.targets = json.loads(draft.targets) if isinstance(draft.targets, str) else draft.targets
    except:
        draft.targets = {}
    return draft

@router.put("/drafts/{draft_id}", response_model=schemas.Draft)
async def update_draft(draft_id: int, draft_in: schemas.DraftUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    draft = db.query(Draft).filter(Draft.id == draft_id, Draft.user_id == current_user.id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    
    if draft_in.content is not None:
        draft.content = draft_in.content
    if draft_in.image_url is not None:
        draft.image_url = draft_in.image_url
    if draft_in.thumbnail_url is not None:
        draft.thumbnail_url = draft_in.thumbnail_url
    if draft_in.targets is not None:
        draft.targets = json.dumps(draft_in.targets)
    
    draft.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(draft)
    try:
        draft.targets = json.loads(draft.targets) if draft.targets else {}
    except:
        draft.targets = {}
    return draft

@router.delete("/drafts/{draft_id}")
async def delete_draft(draft_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Admins can delete team-members' drafts; members only their own.
    visible_ids = get_team_scope_user_ids(db, current_user)
    draft = db.query(Draft).filter(
        Draft.id == draft_id, Draft.user_id.in_(visible_ids)
    ).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    db.delete(draft)
    db.commit()
    return {"status": "success"}

@router.post("/drafts/{draft_id}/publish")
async def publish_draft(draft_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    draft = db.query(Draft).filter(Draft.id == draft_id, Draft.user_id == current_user.id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    
    try:
        targets = json.loads(draft.targets)
    except:
        targets = {}
        
    if not targets:
        raise HTTPException(status_code=400, detail="No target accounts selected in this draft. Edit it to select platforms.")

    # Use the canonical publisher so draft-published posts are persisted in
    # PublishedPost / PublishedPostPlatform exactly like normal publishes.
    from routers.content import process_publishing_internal

    # Fall back to URL-extension sniffing for legacy drafts saved before
    # media_type was being persisted. Without this, a carousel draft with
    # media_type=NULL but image_url ending in .pdf would publish as a
    # text-only post on LinkedIn (no PDF carousel attached).
    import re
    inferred_media_type = draft.media_type
    if not inferred_media_type and draft.image_url:
        if re.search(r"\.(pdf|docx?|pptx?)(\?|$)", draft.image_url, re.IGNORECASE):
            inferred_media_type = "document"

    results = await process_publishing_internal(
        db,
        current_user.id,
        draft.content,
        draft.image_url,
        targets,
        media_type=inferred_media_type,
        dna_product_id=draft.dna_product_id,
        thumbnail_url=draft.thumbnail_url,
    )

    # Keep draft intact (existing behavior) so users can republish/reuse it.
    return {"status": "success", "results": results}
