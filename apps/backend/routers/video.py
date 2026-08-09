"""Video generation endpoints (Phase 1 — silent motion graphics).

Two endpoints:

  POST /video/generate          fires off a generation; returns immediately
                                with the new video's id + status='pending'.
                                The actual storyboard + render runs in a
                                background thread (FastAPI BackgroundTasks).

  GET  /video/{id}/status       polled by the frontend every couple of
                                seconds. Returns the current row including
                                video_url when status='ready'.

Phase 1 ships ONE template (`brand_promo`) at ONE aspect ratio (9:16) and
ONE duration (30s). The body still accepts those fields so the contract
doesn't change when we add more templates / sizes in later phases.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from core.database import SessionLocal, get_db
from models import GeneratedVideo, User
from routers.auth import get_current_user
from services.video.avatar_image_agent import generate_avatar_image
from services.video.remotion_dispatcher import render_video
from services.video.storyboard_agent import generate_storyboard

logger = logging.getLogger("pipelyt.video")
router = APIRouter()


# ----- Request / response schemas -----

class VideoGenerateRequest(BaseModel):
    campaign_brief: str
    aspect_ratio: str = "9:16"   # only "9:16" supported in Phase 1
    template: str = "brand_promo"
    duration_sec: int = 30
    # Optional override — the dna_product_id selects a brand the user has
    # configured. When omitted we use the user's company-level business_dna.
    dna_product_id: Optional[str] = None


class VideoStatusResponse(BaseModel):
    id: int
    status: str
    video_url: Optional[str] = None
    storyboard_json: Optional[dict] = None
    error_message: Optional[str] = None
    aspect_ratio: str
    duration_sec: int
    template: str
    created_at: str
    finished_at: Optional[str] = None
    render_seconds: Optional[int] = None


def _row_to_status(v: GeneratedVideo) -> VideoStatusResponse:
    return VideoStatusResponse(
        id=v.id,
        status=v.status,
        video_url=v.video_url,
        storyboard_json=v.storyboard_json,
        error_message=v.error_message,
        aspect_ratio=v.aspect_ratio,
        duration_sec=v.duration_sec,
        template=v.template,
        created_at=v.created_at.isoformat() + "Z" if v.created_at else "",
        finished_at=v.finished_at.isoformat() + "Z" if v.finished_at else None,
        render_seconds=v.render_seconds,
    )


# ----- Brand resolver: where do colours / logo / company come from? -----

def _resolve_brand(user: User, dna_product_id: Optional[str]) -> dict:
    """Pull brand fields out of the user's business_dna.

    Order of preference:
      1. If dna_product_id is supplied AND found inside business_dna.products,
         use that product's name / colours / logo.
      2. Fall back to the company-level business_dna (the user's master
         brand assets stored at onboarding).
      3. Final defaults (Pipelyt orange, blank logo) so the call never
         hard-fails — the customer just sees the letter-mark fallback.
    """
    bd = user.business_dna or {}
    products = (bd.get("products") or {}) if isinstance(bd, dict) else {}

    if dna_product_id and dna_product_id in products:
        p = products[dna_product_id] or {}
        return {
            "company_name": p.get("product_name") or p.get("company_name") or user.company_name or "",
            "company_url": p.get("url") or user.business_url or user.product_url or "",
            "tagline": p.get("tagline") or "",
            "logo_url": p.get("logo_url") or "",
            "primary_color": _first_color(p.get("colors")) or _first_color(bd.get("colors")) or "#FF4500",
        }

    return {
        "company_name": bd.get("company_name") or user.company_name or "",
        "company_url": bd.get("url") or user.business_url or user.product_url or "",
        "tagline": bd.get("tagline") or "",
        "logo_url": bd.get("logo_url") or "",
        "primary_color": _first_color(bd.get("colors")) or "#FF4500",
    }


def _first_color(colors) -> Optional[str]:
    """business_dna.colors can be either a list of hex strings or a dict
    with a 'primary' key — handle both.
    """
    if not colors:
        return None
    if isinstance(colors, dict):
        return colors.get("primary") or next(iter(colors.values()), None)
    if isinstance(colors, list) and colors:
        return colors[0]
    return None


# ----- Background worker -----

def _run_pipeline(video_id: int) -> None:
    """End-to-end generation: storyboard agent -> Remotion render -> save URL.

    Owns its own DB session so it can outlive the request that started it.
    Any exception is captured into the row's error_message + status='failed'
    rather than re-raised — the worker isn't observable from the API
    response, only from /video/{id}/status.
    """
    started = time.time()
    db: Session = SessionLocal()
    try:
        v = db.query(GeneratedVideo).filter(GeneratedVideo.id == video_id).first()
        if not v:
            logger.error(f"[worker] no GeneratedVideo row {video_id}")
            return
        user = db.query(User).filter(User.id == v.user_id).first()
        if not user:
            v.status = "failed"
            v.error_message = "User no longer exists"
            v.finished_at = datetime.utcnow()
            db.commit()
            return

        # --- 1. Storyboard agent ---
        v.status = "rendering"  # set early so the UI can show the right step
        db.commit()

        brand = _resolve_brand(user, v.dna_product_id)
        try:
            storyboard = generate_storyboard(
                campaign_brief=v.campaign_brief,
                company_name=brand["company_name"] or "Your Brand",
                company_url=brand["company_url"],
                tagline=brand["tagline"],
                logo_url=brand["logo_url"],
                primary_color=brand["primary_color"],
                aspect_ratio=v.aspect_ratio,
            )
        except Exception as e:
            logger.exception(f"[worker {video_id}] storyboard failed")
            v.status = "failed"
            v.error_message = f"Storyboard generation failed: {e}"
            v.finished_at = datetime.utcnow()
            db.commit()
            return

        v.storyboard_json = storyboard
        db.commit()

        # --- 2. Avatar image generation (v4 character-driven) ---
        # For each pain_avatar / solution_avatar scene, call Gemini 2.5
        # Flash Image with the agent-supplied avatar_prompt. Failures are
        # non-fatal: scenes without an avatar_url fall back to a styled
        # gradient inside PainAvatar / SolutionAvatar. We do this
        # serially so a single avatar failure doesn't crash the whole
        # request — also keeps memory pressure on Lambda predictable.
        for scene in storyboard.get("scenes", []):
            if scene.get("type") in ("pain_avatar", "solution_avatar"):
                seed = (scene.get("avatar_prompt") or "").strip()
                if not seed:
                    continue
                try:
                    url = generate_avatar_image(seed, aspect_ratio=v.aspect_ratio)
                    if url:
                        scene["avatar_url"] = url
                except Exception:
                    logger.exception(f"[worker {video_id}] avatar gen failed for {scene.get('type')}")

        # Persist the storyboard with the populated avatar_urls so the
        # status endpoint shows what actually got rendered. SQLAlchemy
        # JSON columns don't auto-detect in-place mutation — flag it.
        flag_modified(v, "storyboard_json")
        db.commit()

        # --- 3. Remotion Lambda render ---
        try:
            mp4_url = render_video(storyboard=storyboard)
        except Exception as e:
            logger.exception(f"[worker {video_id}] render failed")
            v.status = "failed"
            v.error_message = f"Video render failed: {e}"
            v.finished_at = datetime.utcnow()
            db.commit()
            return

        v.video_url = mp4_url
        v.status = "ready"
        v.finished_at = datetime.utcnow()
        v.render_seconds = int(time.time() - started)
        db.commit()
        logger.info(f"[worker {video_id}] ready in {v.render_seconds}s -> {mp4_url}")

    except Exception:
        logger.exception(f"[worker {video_id}] unexpected failure")
    finally:
        db.close()


# ----- Endpoints -----

@router.post("/video/generate")
async def video_generate(
    body: VideoGenerateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a generation request and kick off the background worker.

    Returns immediately with the new row's id + status='pending' so the
    frontend can start polling /video/{id}/status.

    NOTE: Phase 1 has NO plan-feature gate — every authenticated user
    can generate videos while we test. Once pricing is decided we'll
    add `check_feature(current_user, "video_generation")` here and add
    the matching key to core/pricing.PLAN_CONFIG.
    """
    if not body.campaign_brief.strip():
        raise HTTPException(status_code=400, detail="campaign_brief is empty")

    # Phase 1 only ships brand_promo @ 9:16 @ 30s. Reject anything else
    # explicitly so we don't silently render the wrong thing later when
    # those fields become real.
    if body.template != "brand_promo":
        raise HTTPException(status_code=400, detail=f"template {body.template!r} not supported in Phase 1 (only 'brand_promo')")
    if body.aspect_ratio != "9:16":
        raise HTTPException(status_code=400, detail=f"aspect_ratio {body.aspect_ratio!r} not supported in Phase 1 (only '9:16')")

    v = GeneratedVideo(
        user_id=current_user.id,
        campaign_brief=body.campaign_brief.strip(),
        template=body.template,
        aspect_ratio=body.aspect_ratio,
        duration_sec=body.duration_sec,
        dna_product_id=body.dna_product_id,
        status="pending",
    )
    db.add(v)
    db.commit()
    db.refresh(v)

    # Use a real OS thread (not BackgroundTasks) so the work continues
    # past the response even when the parent request finishes — critical
    # for long-running Remotion renders that can take ~30-90 seconds.
    threading.Thread(target=_run_pipeline, args=(v.id,), daemon=True).start()

    return _row_to_status(v)


@router.get("/video/{video_id}/status")
async def video_status(
    video_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    v = db.query(GeneratedVideo).filter(
        GeneratedVideo.id == video_id,
        GeneratedVideo.user_id == current_user.id,
    ).first()
    if not v:
        raise HTTPException(status_code=404, detail="Video not found")
    return _row_to_status(v)


@router.get("/video/list")
async def video_list(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List the user's recent videos — newest first. Used for a future
    'Video History' panel; harmless to ship now."""
    rows = (
        db.query(GeneratedVideo)
        .filter(GeneratedVideo.user_id == current_user.id)
        .order_by(GeneratedVideo.created_at.desc())
        .limit(50)
        .all()
    )
    return [_row_to_status(r) for r in rows]
