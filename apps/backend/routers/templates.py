"""User-defined template CRUD + preview rendering.

A "template" here = one of our built-in V2 layouts (T1–T5) customized with
user overrides (colors, fonts, positions, accent words). Stored in the
`user_templates` table; rendered via services/image_templates.compose()
with the overrides applied.

Endpoints:
    GET    /templates                      — list caller's templates (incl. admin's if member)
    POST   /templates                      — create
    PUT    /templates/{id}                 — update
    DELETE /templates/{id}                 — delete (404 if not owner)
    POST   /templates/{id}/default         — mark as the user's default
    POST   /templates/preview              — render a preview WITHOUT saving
    POST   /templates/{id}/preview         — render a preview of a saved template

At agent-post time, `ai_service._generate_visuals_v2` fetches the user's
templates (via `get_user_templates_for_generation()` below) and renders
them instead of the hardcoded T1–T5 defaults when any exist.
"""
from __future__ import annotations

import base64
import logging
from io import BytesIO
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from models import User, UserTemplate
from routers.auth import get_current_user
from services.image_templates import (
    compose, TEMPLATE_IDS, DEFAULT_CANVAS,
)
from services.team_service import get_plan_owner, is_team_member

logger = logging.getLogger("pipelyt.templates")
router = APIRouter()

# Sample payload for the /preview endpoint — keeps previews self-contained
# so the editor can render without needing a real campaign brief.
_SAMPLE_COPY = {
    "product_name": "Pipelyt",
    "product_tagline": "social media management platform",
    "headline": "Scale Every Channel from One Workspace",
    "subheading": "AI-crafted posts, unified scheduling, and instant cross-platform analytics.",
    "cta": "Start Free",
    "highlight_words": ["Scale", "One"],
    "attribution": "— The Pipelyt Team",
    "insight_label": "INSIGHT",
    "insight_number": "01",
}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class TemplateOverrides(BaseModel):
    """All override fields are optional — unset keys fall through to the base
    layout's defaults. See services/image_templates.py::apply_overrides for
    the full processing logic."""
    primary_color: Optional[str] = None               # hex e.g. "#FF5722"
    headline_color: Optional[str] = None              # hex — defaults to black
    subheading_color: Optional[str] = None            # hex — defaults to black
    tagline_color: Optional[str] = None               # hex — defaults to black
    headline_font: Optional[str] = None               # "display" | "sans_bold"
    subheading_font: Optional[str] = None             # "sans_reg" | "sans_bold"
    cta_style: Optional[str] = None                   # "solid" | "outline" | "white_pill"
    cta_position: Optional[str] = None                # "bottom-right" | "bottom-left" | "under-sub"
    accent_words: Optional[list[str]] = None          # words highlighted in primary in headline
    sub_accent_words: Optional[list[str]] = None      # words highlighted in primary in subheading
    bg_corner_radius: Optional[int] = None            # 0–64 px
    # Per-element pixel positions as percentages 0–100 of canvas side.
    # Shape: {"logo": {"x": 5, "y": 5}, "headline": {"x": 5, "y": 18, "w": 90}, ...}
    positions: Optional[dict] = None


class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    base_template_id: str = Field(..., description="T1 | T2 | T3 | T4 | T5")
    overrides: Optional[TemplateOverrides] = None
    is_default: bool = False


class TemplateUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    base_template_id: Optional[str] = None
    overrides: Optional[TemplateOverrides] = None
    is_default: Optional[bool] = None


class TemplateOut(BaseModel):
    id: int
    name: str
    base_template_id: str
    overrides: dict
    is_default: bool

    class Config:
        from_attributes = True


class TemplatePreviewRequest(BaseModel):
    base_template_id: str
    overrides: Optional[TemplateOverrides] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _owner_user_id(db: Session, user: User) -> int:
    """Templates are OWNED at the plan-owner level so a team admin can create
    templates once and members automatically see them (matches the DNA
    inheritance model). Members still can't edit — enforced below."""
    return int(get_plan_owner(db, user).id)


def _validate_base_template(base_id: str) -> None:
    if base_id not in TEMPLATE_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"base_template_id must be one of {TEMPLATE_IDS}",
        )


def _validate_hex(value: Optional[str], field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.startswith("#") or len(value) not in (4, 7):
        raise HTTPException(status_code=400, detail=f"{field} must be a hex color like #FF5722")


def _load_bundled_preview_assets() -> tuple[bytes, bytes]:
    """Load the bundled conference photo + favicon for the /preview endpoint.
    Keeps previews offline-capable and deterministic."""
    here = Path(__file__).resolve().parent.parent
    bg_path = here / "scripts" / "visualist_harness" / "out" / "img_3.png"
    logo_path = here.parent / "product-page" / "public" / "favicon.png"
    if not bg_path.exists() or not logo_path.exists():
        raise HTTPException(status_code=500, detail="Preview assets missing on server")
    return bg_path.read_bytes(), logo_path.read_bytes()


def _render_preview(base_id: str, overrides: Optional[dict]) -> str:
    """Render one template with the given overrides + sample copy and
    return a base64 data-URL the editor can <img src=...> directly."""
    _validate_base_template(base_id)
    bg_bytes, logo_bytes = _load_bundled_preview_assets()
    primary = (overrides or {}).get("primary_color") or "#FF5722"
    jpeg = compose(
        bg_bytes, _SAMPLE_COPY, logo_bytes, primary, base_id,
        DEFAULT_CANVAS, overrides=overrides or {},
    )
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def _serialize(t: UserTemplate) -> TemplateOut:
    return TemplateOut(
        id=int(t.id),
        name=t.name,
        base_template_id=t.base_template_id,
        overrides=dict(t.overrides or {}),
        is_default=bool(t.is_default),
    )


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------
@router.get("/templates", response_model=list[TemplateOut])
def list_templates(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List templates owned by the caller's plan owner (admin). Members
    inherit the admin's templates, same pattern as business_dna."""
    owner_id = _owner_user_id(db, current_user)
    rows = (
        db.query(UserTemplate)
        .filter(UserTemplate.user_id == owner_id)
        .order_by(UserTemplate.is_default.desc(), UserTemplate.created_at.desc())
        .all()
    )
    return [_serialize(t) for t in rows]


@router.post("/templates", response_model=TemplateOut, status_code=201)
def create_template(
    body: TemplateCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Only the plan owner (admin) may create templates. Members inherit
    via GET but cannot edit — prevents a member from polluting the admin's
    library."""
    if is_team_member(current_user):
        raise HTTPException(status_code=403, detail="Only the admin can create templates")
    _validate_base_template(body.base_template_id)
    overrides_dict = body.overrides.model_dump(exclude_none=True) if body.overrides else {}
    _validate_hex(overrides_dict.get("primary_color"), "primary_color")
    _validate_hex(overrides_dict.get("headline_color"), "headline_color")
    _validate_hex(overrides_dict.get("subheading_color"), "subheading_color")
    _validate_hex(overrides_dict.get("tagline_color"), "tagline_color")

    if body.is_default:
        # At most one default per user — clear any previous default.
        db.query(UserTemplate).filter(
            UserTemplate.user_id == current_user.id,
            UserTemplate.is_default == True,  # noqa: E712
        ).update({UserTemplate.is_default: False})

    t = UserTemplate(
        user_id=current_user.id,
        name=body.name.strip(),
        base_template_id=body.base_template_id,
        overrides=overrides_dict or None,
        is_default=bool(body.is_default),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _serialize(t)


@router.put("/templates/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: int,
    body: TemplateUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if is_team_member(current_user):
        raise HTTPException(status_code=403, detail="Only the admin can edit templates")
    t = db.query(UserTemplate).filter(
        UserTemplate.id == template_id,
        UserTemplate.user_id == current_user.id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    if body.name is not None:
        t.name = body.name.strip()
    if body.base_template_id is not None:
        _validate_base_template(body.base_template_id)
        t.base_template_id = body.base_template_id
    if body.overrides is not None:
        overrides_dict = body.overrides.model_dump(exclude_none=True)
        _validate_hex(overrides_dict.get("primary_color"), "primary_color")
        _validate_hex(overrides_dict.get("headline_color"), "headline_color")
        _validate_hex(overrides_dict.get("subheading_color"), "subheading_color")
        _validate_hex(overrides_dict.get("tagline_color"), "tagline_color")
        t.overrides = overrides_dict or None
    if body.is_default is not None:
        if body.is_default:
            db.query(UserTemplate).filter(
                UserTemplate.user_id == current_user.id,
                UserTemplate.id != t.id,
                UserTemplate.is_default == True,  # noqa: E712
            ).update({UserTemplate.is_default: False})
        t.is_default = bool(body.is_default)
    db.commit()
    db.refresh(t)
    return _serialize(t)


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(
    template_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if is_team_member(current_user):
        raise HTTPException(status_code=403, detail="Only the admin can delete templates")
    t = db.query(UserTemplate).filter(
        UserTemplate.id == template_id,
        UserTemplate.user_id == current_user.id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    db.delete(t)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Preview endpoints
# ---------------------------------------------------------------------------
@router.post("/templates/preview")
def preview_unsaved(
    body: TemplatePreviewRequest,
    current_user: User = Depends(get_current_user),
):
    """Render an unsaved draft — used by the editor's live preview pane."""
    overrides = body.overrides.model_dump(exclude_none=True) if body.overrides else {}
    return {"image": _render_preview(body.base_template_id, overrides)}


@router.post("/templates/{template_id}/preview")
def preview_saved(
    template_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    owner_id = _owner_user_id(db, current_user)
    t = db.query(UserTemplate).filter(
        UserTemplate.id == template_id,
        UserTemplate.user_id == owner_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"image": _render_preview(t.base_template_id, dict(t.overrides or {}))}


# ---------------------------------------------------------------------------
# Helper for the ai_service / _generate_visuals_v2 consumer
# ---------------------------------------------------------------------------
def get_user_templates_for_generation(db: Session, user: User) -> list[UserTemplate]:
    """Return templates that should drive the user's next agent post. Empty
    list → caller should fall back to the default 5 built-in templates."""
    owner = get_plan_owner(db, user)
    return (
        db.query(UserTemplate)
        .filter(UserTemplate.user_id == owner.id)
        .order_by(UserTemplate.is_default.desc(), UserTemplate.created_at.asc())
        .all()
    )
