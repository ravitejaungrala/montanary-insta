from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.security import OAuth2PasswordBearer
from fastapi.responses import RedirectResponse
import os
import requests
import base64
import hashlib
import secrets
import json
from core.database import get_db
from sqlalchemy.orm import Session
from models import User, PendingOAuthSync
from routers.auth import get_current_user
from services.plan_service import check_feature
from urllib.parse import urlencode
from core import config
from datetime import datetime, timedelta
from typing import Optional
import logging

logger = logging.getLogger("pipelyt.canva")

router = APIRouter(prefix="/auth/canva")

# Helper to get/refresh user's Canva token
def get_valid_canva_token(user: User, db: Session):
    # 1. Check if token exists
    if not user.canva_access_token or not user.canva_refresh_token:
        return None

    # 2. Check if expired (with 5 min buffer)
    now = datetime.utcnow()
    if user.canva_token_expires_at and now < (user.canva_token_expires_at - timedelta(minutes=5)):
        return user.canva_access_token

    # 3. Refresh token
    logger.info(f"Canva token expired for user {user.email}. Refreshing...")
    token_url = "https://api.canva.com/rest/v1/oauth/token"
    client_id = config.CANVA_CLIENT_ID
    client_secret = config.CANVA_CLIENT_SECRET
    
    auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": user.canva_refresh_token
    }
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    response = requests.post(token_url, data=payload, headers=headers)
    if response.status_code != 200:
        logger.error(f"Failed to refresh Canva token: {response.text}")
        return None
    
    tokens = response.json()
    new_access = tokens.get("access_token")
    new_refresh = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in", 14400) # Default 4 hours
    
    user.canva_access_token = new_access
    if new_refresh:
        user.canva_refresh_token = new_refresh
    user.canva_token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
    
    db.commit()
    return new_access

def create_canva_design(access_token: str):
    design_url = "https://api.canva.com/rest/v1/designs"
    design_payload = {
        "design_type": { "type": "custom", "width": 1080, "height": 1080 },
        "asset_id": None, 
        "title": "Pipelyt Design"
    }
    design_headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    design_res = requests.post(design_url, json=design_payload, headers=design_headers)
    if design_res.status_code not in [200, 201]:
        return None, design_res.text

    design_data = design_res.json()
    design_id = design_data.get("design", {}).get("id")
    edit_url = design_data.get("design", {}).get("urls", {}).get("edit_url")
    return design_id, edit_url

# PKCE state is persisted in the `pending_oauth_syncs` table (same bypass used
# for Twitter) so the authorize -> callback flow works across Lambda containers.
# Stale entries are pruned on read by `_load_canva_pkce`.
CANVA_PKCE_TTL_MINUTES = 15

def generate_pkce():
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().replace('=', '')
    return code_verifier, code_challenge

def _save_canva_pkce(db: Session, state: str, code_verifier: str):
    """Persist the PKCE verifier for a Canva OAuth state so the callback can retrieve it."""
    # Namespace the key with a prefix to avoid collisions with Twitter's rows.
    db.add(PendingOAuthSync(state_or_token=f"canva:{state}", secret_or_verifier=code_verifier))
    db.commit()

def _load_canva_pkce(db: Session, state: str) -> Optional[str]:
    """Retrieve (and consume) the PKCE verifier for a Canva OAuth state."""
    row = db.query(PendingOAuthSync).filter(
        PendingOAuthSync.state_or_token == f"canva:{state}"
    ).first()
    if not row:
        return None

    # Reject entries that have outlived the TTL.
    if row.created_at and (datetime.utcnow() - row.created_at) > timedelta(minutes=CANVA_PKCE_TTL_MINUTES):
        db.delete(row)
        db.commit()
        return None

    verifier = row.secret_or_verifier
    # One-shot use: remove so a replayed code cannot be exchanged twice.
    db.delete(row)
    db.commit()
    return verifier

@router.get("/")
@router.get("")
async def canva_root(
    code: Optional[str] = None, 
    state: Optional[str] = None,
    token: Optional[str] = None,
    db: Session = Depends(get_db),
    request: Request = None
):
    """
    Handles both the entry point and potential callbacks to the root URL.
    This makes the integration resilient to Redirect URI configuration mismatches.
    """
    # High-reliability parameter detection
    q_code = code or request.query_params.get("code")
    q_state = state or request.query_params.get("state")
    q_token = token or request.query_params.get("token")

    if q_code and q_state:
        logger.info(f"Canva callback parameters detected at root: code={q_code[:5]}..., state={q_state}")
        return await callback(code=q_code, state=q_state, db=db)
    
    # Standard authorization flow
    # Check Header first, then Query Param (for window.open compatibility)
    auth_header = request.headers.get("Authorization")
    actual_token = None
    
    if auth_header and auth_header.startswith("Bearer "):
        actual_token = auth_header.split(" ")[1]
    elif q_token:
        actual_token = q_token

    if not actual_token:
         logger.warning(f"Unauthorized hit to /auth/canva. No token found in headers or query params.")
         raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        user = await get_current_user(actual_token, db)
        results = await authorize(db, user)
        # If it returns a dict with 'url', redirect the browser directly
        if isinstance(results, dict) and "url" in results:
            return RedirectResponse(url=results["url"])
        return results
    except Exception as e:
        logger.error(f"Failed to authenticate Canva root hit: {str(e)}")
        raise HTTPException(status_code=401, detail="Authentication failed")

@router.get("/authorize")
async def authorize(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # 1. Feature Check
    check_feature(current_user, "canva")
    
    # 2. Check if user already has a valid token
    token = get_valid_canva_token(current_user, db)
    if token:
        design_id, edit_url = create_canva_design(token)
        if design_id and edit_url:
            return {"url": f"{config.FRONTEND_URL}/canva-callback?design_id={design_id}&edit_url={edit_url}"}

    # 2. Start new flow
    client_id = config.CANVA_CLIENT_ID
    redirect_uri = config.CANVA_REDIRECT_URI
    
    code_verifier, code_challenge = generate_pkce()
    # Use state to store the user's ID to handle the callback correctly
    state = f"{secrets.token_urlsafe(16)}:{current_user.id}"

    # Persist the verifier in DB so the callback works across Lambda containers.
    _save_canva_pkce(db, state, code_verifier)

    scopes = " ".join([
        "design:content:read", "design:content:write", "design:meta:read",
        "asset:read", "asset:write", "profile:read"
    ])
    
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "code_challenge": code_challenge,
        "code_challenge_method": "s256",
        "state": state
    }
    
    auth_url = f"https://www.canva.com/api/oauth/authorize?{urlencode(params)}"
    return {"url": auth_url}

@router.get("/callback")
async def callback(code: Optional[str] = None, state: Optional[str] = None, 
                   error: Optional[str] = None, error_description: Optional[str] = None,
                   db: Session = Depends(get_db)):
    if error:
        return RedirectResponse(url=f"{config.FRONTEND_URL}/canva-callback?error={error}&details={error_description}")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing authorization code or state")

    code_verifier = _load_canva_pkce(db, state)
    if not code_verifier:
        raise HTTPException(status_code=400, detail="Invalid state or session expired")

    # Extract user_id from state
    try:
        user_id = int(state.split(":")[1])
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=400, detail="User correlation failed")
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="User correlation failed")

    token_url = "https://api.canva.com/rest/v1/oauth/token"
    auth_header = base64.b64encode(f"{config.CANVA_CLIENT_ID}:{config.CANVA_CLIENT_SECRET}".encode()).decode()
    
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.CANVA_REDIRECT_URI,
        "code_verifier": code_verifier
    }
    
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    response = requests.post(token_url, data=payload, headers=headers)
    if response.status_code != 200:
        return {"error": "Failed to exchange token", "details": response.json()}
    
    tokens = response.json()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in", 14400)
    
    # Save to user record
    user.canva_access_token = access_token
    user.canva_refresh_token = refresh_token
    user.canva_token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
    db.commit()
    
    design_id, edit_url = create_canva_design(access_token)
    if not design_id or not edit_url:
        return RedirectResponse(url=f"{config.FRONTEND_URL}/canva-callback?error=design_creation_failed")

    return RedirectResponse(url=f"{config.FRONTEND_URL}/canva-callback?design_id={design_id}&edit_url={edit_url}")

@router.post("/export")
async def export_design(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    data = await request.json()
    design_id = data.get("design_id")
    
    # 1. Feature Check
    check_feature(current_user, "canva")

    if not design_id:
        raise HTTPException(status_code=400, detail="Missing design_id")

    # If it's a JWT from correlation_jwt, decode it
    if design_id.startswith("eyJ") and "." in design_id:
        try:
            parts = design_id.split(".")
            payload_padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_padded).decode())
            design_id = payload.get("design_id", design_id)
        except (ValueError, json.JSONDecodeError, IndexError) as e:
            logger.debug(f"design_id JWT decode skipped: {e}")

    # Get valid token from DB (auto-refresh if needed)
    token = get_valid_canva_token(current_user, db)
    if not token:
        return {"error": "Authentication required. Please click Design with Canva again."}
    
    headers = { "Authorization": f"Bearer {token}", "Content-Type": "application/json" }
    export_url = "https://api.canva.com/rest/v1/exports"
    payload = { "design_id": design_id, "format": { "type": "jpg", "quality": 80 } }
    
    res = requests.post(export_url, json=payload, headers=headers)
    if res.status_code not in [200, 201]:
        return {"error": "Failed to start export", "details": res.text}
    
    job_id = res.json().get("job", {}).get("id")
    
    # Poll for completion
    import time
    for i in range(15):
        time.sleep(2)
        job_res = requests.get(f"{export_url}/{job_id}", headers=headers)
        job_data = job_res.json().get("job", {})
        status = job_data.get("status")

        if status == "success":
            urls = job_data.get("urls", [])
            canva_temp_url = urls[0] if urls else None
            if not canva_temp_url:
                return {"error": "Export succeeded but no URL was returned"}

            # Canva's export-download.canva.com URLs expire after ~24-48 h
            # (the Expires query param). Saving them straight onto a
            # PublishedPost.image_url meant week-old posts showed broken
            # thumbnails on the analytics page. Download the bytes and
            # re-upload to S3 so we hand the frontend a permanent URL.
            try:
                from core.s3_utils import get_s3_client, get_s3_url
                from core.config import S3_BUCKET_NAME
                from io import BytesIO
                import uuid

                img_res = requests.get(canva_temp_url, timeout=30)
                if img_res.status_code != 200:
                    logger.warning(
                        f"Canva URL fetch returned {img_res.status_code} — "
                        f"falling back to the temporary URL (will expire)."
                    )
                    return {"url": canva_temp_url}

                content_type = img_res.headers.get("Content-Type", "image/jpeg")
                ext = "jpg"
                if "png" in content_type.lower():
                    ext = "png"
                elif "webp" in content_type.lower():
                    ext = "webp"

                key = f"canva-exports/{uuid.uuid4().hex}.{ext}"
                s3 = get_s3_client()
                s3.upload_fileobj(
                    BytesIO(img_res.content),
                    S3_BUCKET_NAME,
                    key,
                    ExtraArgs={"ContentType": content_type},
                )
                permanent_url = get_s3_url(key)
                logger.info(
                    f"Canva export persisted to S3: {permanent_url} "
                    f"(canva temp URL was {canva_temp_url[:60]}…)"
                )
                return {"url": permanent_url}
            except Exception as up_err:
                # Best-effort fallback: if S3 fails for any reason, still
                # return the Canva temp URL so the user can publish today.
                # The expiry will hit them later but at least the immediate
                # flow doesn't break.
                logger.exception(f"Canva→S3 re-upload failed: {up_err}")
                return {"url": canva_temp_url}
        if status == "failed":
            return {"error": "Export job failed"}

    return {"error": "Export timed out"}

@router.post("/disconnect")
async def disconnect_canva(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    current_user.canva_access_token = None
    current_user.canva_refresh_token = None
    current_user.canva_token_expires_at = None
    db.commit()
    return {"message": "Canva disconnected successfully"}
