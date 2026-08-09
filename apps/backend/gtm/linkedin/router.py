"""GTM LinkedIn Agent — HTTP surface (connect / challenge / disconnect / pause /
product-assignment). Mounted under the existing `/nexus/*` namespace (the
external API contract is unchanged); all business logic lives in
`gtm/linkedin/auth.py`.

Registered by main.py ONLY when `NEXUS_LINKEDIN_ENABLED` is true.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import create_access_token
from core.config import ALGORITHM, SCHEDULER_SECRET, SECRET_KEY
from core.database import get_db
from models import User
from nexus._phase6_common import extract_workspace_id
from nexus.deps import block_user_writes, get_nexus_user, require_current_workspace

from gtm.linkedin import auth, cloud_login, scheduler
from gtm.linkedin.models import GtmLinkedInAccount, GtmLinkedInLoginSession

logger = logging.getLogger("pipelyt.gtm_linkedin.router")

# Router-level block_user_writes is method-aware: read-only Users are blocked
# from POST/PUT/DELETE; GETs stay open (same pattern as nexus/routers/connectors).
router = APIRouter(
    prefix="/nexus/linkedin-agent",
    tags=["GTM — LinkedIn Agent"],
    dependencies=[Depends(block_user_writes)],
)


# ── Schemas ───────────────────────────────────────────────────────────────────
class ConnectIn(BaseModel):
    linkedin_email: str
    password: str
    account_type: str = "personal"  # personal | sales_navigator
    brand_ids: Optional[List[int]] = None


class SessionIn(BaseModel):
    """Session captured in the customer's OWN browser (Chrome extension / cookie
    import). `cookies` is the raw list from chrome.cookies.getAll (or Playwright
    cookies) — the backend normalizes + validates it (must contain `li_at`)."""
    linkedin_email: str
    cookies: List[dict]
    account_type: str = "personal"
    brand_ids: Optional[List[int]] = None
    display_name: Optional[str] = None


class ConnectTokenIn(BaseModel):
    brand_id: Optional[int] = None  # bind the captured session to this brand


class SessionTokenIn(BaseModel):
    """Same as SessionIn but auth is a short-lived connect token (Bearer header),
    not the app session — used by the Chrome extension (cross-origin)."""
    linkedin_email: str
    cookies: List[dict]
    account_type: str = "personal"
    display_name: Optional[str] = None


class CloudConnectIn(BaseModel):
    """Start an interactive cloud login (Phase 2). No password — the user logs in
    inside the hosted browser; we capture the resulting profile."""
    linkedin_email: str
    account_type: str = "personal"
    brand_id: Optional[int] = None
    display_name: Optional[str] = None


_CONNECT_TOKEN_TTL_MIN = 30
_CONNECT_SCOPE = "li_connect"


class ChallengeIn(BaseModel):
    code: str


class PauseIn(BaseModel):
    paused: bool


class BrandsIn(BaseModel):
    brand_ids: List[int]


class EnrollIn(BaseModel):
    account_id: int
    workflow_mode: str = "standard"  # standard | combined (connect+InMail) | hybrid


def _account_out(db: Session, acct: GtmLinkedInAccount) -> dict:
    inmail_status = getattr(acct, "linkedin_inmail_credits_status", None) or "ok"
    inmail_flagged = getattr(acct, "linkedin_inmail_credits_flagged_at", None)
    return {
        "id": acct.id,
        "linkedin_email": acct.linkedin_email,
        "linkedin_display_name": acct.linkedin_display_name,
        "account_type": acct.linkedin_account_type,
        "session_status": acct.linkedin_session_status,
        "reauth_required": bool(acct.linkedin_reauth_required),
        "is_paused": bool(acct.is_paused),
        "cooldown_until": acct.cooldown_until.isoformat() if acct.cooldown_until else None,
        "last_successful_login": acct.last_successful_login.isoformat() if acct.last_successful_login else None,
        "brand_ids": auth.list_account_brands(db, acct.id),
        # InMail credit health — the UI shows a "credits exhausted" banner
        # when status='exhausted'.
        "inmail_credits_status": inmail_status,
        "inmail_credits_flagged_at": inmail_flagged.isoformat() if inmail_flagged else None,
        "inmail_credits_message": (
            "LinkedIn InMail credits exhausted for this account. Add credits "
            "or upgrade your Premium plan to continue sending InMails."
            if inmail_status == "exhausted" else None
        ),
    }


def _get_owned(db: Session, ws_id: int, account_id: int) -> GtmLinkedInAccount:
    acct = (
        db.query(GtmLinkedInAccount)
        .filter(GtmLinkedInAccount.id == account_id, GtmLinkedInAccount.workspace_id == ws_id)
        .first()
    )
    if acct is None:
        raise HTTPException(status_code=404, detail="LinkedIn account not found")
    return acct


# ── Endpoints ───────────────────────────────────────────────────────────────
@router.get("/accounts")
def list_accounts(
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    ws_id = extract_workspace_id(workspace) or 0
    rows = (
        db.query(GtmLinkedInAccount)
        .filter(GtmLinkedInAccount.workspace_id == ws_id)
        .order_by(GtmLinkedInAccount.id.asc())
        .all()
    )
    return {"accounts": [_account_out(db, a) for a in rows]}


@router.post("/connect")
def connect(
    body: ConnectIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Connect a LinkedIn account. Enqueues a one-time headless-login job (the
    password is encrypted into the job payload, never stored). The worker
    completes login + cookie capture; poll GET /accounts for `session_status`."""
    ws_id = extract_workspace_id(workspace) or 0
    try:
        acct = auth.connect_account(
            db, workspace_id=ws_id, user_id=getattr(user, "id", None),
            linkedin_email=body.linkedin_email, password=body.password,
            account_type=body.account_type, brand_ids=body.brand_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "account": _account_out(db, acct)}


@router.post("/session")
def attach_session(
    body: SessionIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Attach a LinkedIn session captured in the customer's OWN browser (Chrome
    extension / cookie import) — no password, no cloud login. Upserts the account
    and stores the encrypted cookies, marking it active immediately. This is the
    self-service onboarding path that sidesteps cloud-login 2FA/CAPTCHA/IP issues:
    the customer already logged in as a human on their real IP."""
    ws_id = extract_workspace_id(workspace) or 0
    try:
        acct = auth.attach_session(
            db, workspace_id=ws_id, user_id=getattr(user, "id", None),
            linkedin_email=body.linkedin_email, cookies=body.cookies,
            account_type=body.account_type, brand_ids=body.brand_ids,
            display_name=body.display_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "account": _account_out(db, acct)}


@router.post("/connect-token")
def issue_connect_token(
    body: Optional[ConnectTokenIn] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Issue a SHORT-LIVED connect token for the Chrome extension. The user is
    authenticated here via the normal app session; the token (a signed JWT, 30 min)
    encodes their workspace/user/brand so the extension can call /session/token
    cross-origin WITHOUT the app session cookie. The customer copies this token
    into the extension once."""
    ws_id = extract_workspace_id(workspace) or 0
    token = create_access_token(
        {
            "scope": _CONNECT_SCOPE,
            "ws": ws_id,
            "uid": getattr(user, "id", None),
            "brand": (body.brand_id if body else None),
        },
        expires_delta=timedelta(minutes=_CONNECT_TOKEN_TTL_MIN),
    )
    return {"token": token, "expires_in_seconds": _CONNECT_TOKEN_TTL_MIN * 60}


@router.post("/session/token")
def attach_session_via_token(
    body: SessionTokenIn,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Chrome-extension endpoint: attach a captured LinkedIn session, authenticated
    by the connect token (Authorization: Bearer <token>) instead of the app session.
    Verifies the token (scope + signature + expiry), then stores the cookies exactly
    like /session."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing connect token")
    raw = authorization.split(" ", 1)[1].strip()
    try:
        claims = jwt.decode(raw, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="invalid or expired connect token")
    if claims.get("scope") != _CONNECT_SCOPE:
        raise HTTPException(status_code=401, detail="wrong token scope")
    ws_id = int(claims.get("ws") or 0)
    if not ws_id:
        raise HTTPException(status_code=401, detail="connect token missing workspace")
    brand = claims.get("brand")
    try:
        acct = auth.attach_session(
            db, workspace_id=ws_id, user_id=claims.get("uid"),
            linkedin_email=body.linkedin_email, cookies=body.cookies,
            account_type=body.account_type,
            brand_ids=[int(brand)] if brand is not None else None,
            display_name=body.display_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "account": _account_out(db, acct)}


@router.post("/connect/cloud/start")
def cloud_login_start(
    body: CloudConnectIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Start an interactive cloud login: upsert the account, create a login
    session, and launch the hosted browser (Fargate). The user opens the returned
    live-view URL and logs into LinkedIn there — we never see the password. Poll
    GET /connect/cloud/{session_id} for the viewer URL + status."""
    ws_id = extract_workspace_id(workspace) or 0
    try:
        acct = auth.get_or_create_account(
            db, workspace_id=ws_id, user_id=getattr(user, "id", None),
            linkedin_email=body.linkedin_email, account_type=body.account_type,
            brand_ids=[body.brand_id] if body.brand_id is not None else None,
            display_name=body.display_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    sess = cloud_login.create_login_session(db, workspace_id=ws_id, account_id=acct.id)
    task_arn = cloud_login.launch_fargate_login(account_id=acct.id, session_id=sess.id)
    if task_arn:
        return {"ok": True, "session_id": sess.id, "account_id": acct.id, "status": sess.status, "mode": "fargate"}
    # Local-dev fallback: run the login in a headful browser on the host (no Fargate).
    if (os.getenv("LI_LOGIN_ALLOW_LOCAL") or "").strip().lower() in ("1", "true", "yes"):
        cloud_login.run_login_session_background(account_id=acct.id, session_id=sess.id)
        return {"ok": True, "session_id": sess.id, "account_id": acct.id, "status": sess.status, "mode": "local"}
    sess.status = "failed"
    sess.detail = "cloud login not configured (ECS env missing)"
    db.commit()
    raise HTTPException(status_code=503, detail="cloud login is not configured on this environment")


@router.get("/connect/cloud/{session_id}")
def cloud_login_status(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Poll a cloud-login session — status + the live-view URL to open."""
    ws_id = extract_workspace_id(workspace) or 0
    sess = db.query(GtmLinkedInLoginSession).get(session_id)
    if sess is None or sess.workspace_id != ws_id:
        raise HTTPException(status_code=404, detail="login session not found")
    return {
        "session_id": sess.id,
        "account_id": sess.linkedin_account_id,
        "status": sess.status,          # starting|awaiting_login|logged_in|saved|failed|expired
        "viewer_url": sess.viewer_url,
        "detail": sess.detail,
    }


@router.post("/{account_id}/challenge")
def challenge(
    account_id: int,
    body: ChallengeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    ws_id = extract_workspace_id(workspace) or 0
    _get_owned(db, ws_id, account_id)
    job = auth.submit_challenge_code(db, workspace_id=ws_id, account_id=account_id, code=body.code)
    return {"ok": True, "job_id": job.id}


@router.post("/{account_id}/disconnect")
def disconnect(
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    ws_id = extract_workspace_id(workspace) or 0
    _get_owned(db, ws_id, account_id)
    auth.disconnect(db, account_id=account_id)
    return {"ok": True, "id": account_id}


@router.post("/{account_id}/pause")
def pause(
    account_id: int,
    body: PauseIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    ws_id = extract_workspace_id(workspace) or 0
    _get_owned(db, ws_id, account_id)
    auth.set_paused(db, account_id=account_id, paused=body.paused)
    return {"ok": True, "id": account_id, "is_paused": body.paused}


@router.get("/{account_id}/brands")
def get_brands(
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    ws_id = extract_workspace_id(workspace) or 0
    _get_owned(db, ws_id, account_id)
    return {"brand_ids": auth.list_account_brands(db, account_id)}


@router.put("/{account_id}/brands")
def set_brands(
    account_id: int,
    body: BrandsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    ws_id = extract_workspace_id(workspace) or 0
    _get_owned(db, ws_id, account_id)
    ids = auth.set_account_brands(db, account_id, body.brand_ids)
    return {"ok": True, "brand_ids": ids}


@router.get("/brands/{brand_id}/accounts")
def accounts_for_brand(
    brand_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """LinkedIn accounts a campaign on this sender brand may use (campaign-setup picker)."""
    ws_id = extract_workspace_id(workspace) or 0
    rows = auth.accounts_for_brand(db, ws_id, brand_id)
    return {"accounts": [_account_out(db, a) for a in rows]}


@router.post("/campaigns/{campaign_id}/enroll")
def enroll_campaign(
    campaign_id: int,
    body: EnrollIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_nexus_user),
    workspace=Depends(require_current_workspace),
):
    """Enroll a campaign's leads into the LinkedIn sequence on the chosen
    account (must be one of the campaign product's mapped, active accounts)."""
    ws_id = extract_workspace_id(workspace) or 0
    _get_owned(db, ws_id, body.account_id)
    if body.workflow_mode not in ("combined", "standard", "hybrid"):
        raise HTTPException(status_code=400, detail="workflow_mode must be 'combined', 'standard' or 'hybrid'")
    try:
        out = scheduler.enroll_campaign(
            db, workspace_id=ws_id, campaign_id=campaign_id,
            account_id=body.account_id, workflow_mode=body.workflow_mode,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **out}


# ── Machine-to-machine scheduler hooks (EventBridge → heartbeat) ──────────────
def _verify_secret(x_scheduler_secret: str = Header(None)):
    if not SCHEDULER_SECRET or x_scheduler_secret != SCHEDULER_SECRET:
        raise HTTPException(status_code=403, detail="bad scheduler secret")


@router.post("/scheduler/tick", dependencies=[Depends(_verify_secret)])
def scheduler_tick(db: Session = Depends(get_db)):
    """EventBridge (every ~5 min) → auto-pause sweep + dispatch due LinkedIn jobs
    to SQS. Gated by X-Scheduler-Secret (no user auth)."""
    return scheduler.tick(db)


@router.post("/scheduler/validate-sessions", dependencies=[Depends(_verify_secret)])
def scheduler_validate_sessions(db: Session = Depends(get_db)):
    """Daily EventBridge → enqueue a validate_session job per active account."""
    return scheduler.enqueue_session_validations(db)
