"""GTM LinkedIn Agent — account lifecycle + session + product-assignment service.

This is the DB-level service layer (no browser). The actual headless Playwright
login / cookie capture runs in the Phase-2 worker (`gtm/linkedin/worker.py`),
which calls `store_session` / `mark_session_expired` here.

Security: the LinkedIn password is NEVER persisted on the account row. On
connect it is Fernet-encrypted into the one-time `linkedin_login` job payload,
used transiently by the worker, then discarded with the job.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from gtm.linkedin import crypto
from gtm.linkedin.events import record_event
from gtm.linkedin.logs import setup as _setup_logs

_setup_logs()
from gtm.linkedin.models import (
    GtmLinkedInAccount,
    GtmLinkedInAccountBrand,
    GtmLinkedInAccountSettings,
    GtmLinkedInBrowserProfile,
    GtmLinkedInJob,
    GtmLinkedInLeadState,
)

logger = logging.getLogger("pipelyt.gtm_linkedin.auth")


def _utcnow() -> datetime:
    return datetime.utcnow()


# ── Job enqueue ───────────────────────────────────────────────────────────────
def enqueue_job(
    db: Session,
    *,
    workspace_id: int,
    account_id: int,
    job_type: str,
    payload: Optional[Dict[str, Any]] = None,
    lead_id: Optional[int] = None,
    campaign_id: Optional[int] = None,
) -> GtmLinkedInJob:
    """Create a durable `gtm_linkedin_jobs` row, then publish it to SQS so the
    worker Lambda runs it (MessageGroupId=account_id → per-account serial). The
    DB row is the source of truth; SQS publish is best-effort and a no-op locally
    (no queue configured) where jobs run inline."""
    job = GtmLinkedInJob(
        workspace_id=workspace_id,
        linkedin_account_id=account_id,
        lead_id=lead_id,
        campaign_id=campaign_id,
        job_type=job_type,
        status="pending",
        scheduled_at=_utcnow(),
        payload_json=payload or {},
    )
    db.add(job)
    db.commit()
    logger.info("enqueue: created job %s type=%s account=%s lead=%s campaign=%s",
                job.id, job_type, account_id, lead_id, campaign_id)
    from gtm.linkedin.queue import publish_job
    publish_job(job_id=job.id, account_id=account_id)
    return job


# ── Connect / re-auth ───────────────────────────────────────────────────────
def connect_account(
    db: Session,
    *,
    workspace_id: int,
    user_id: Optional[int],
    linkedin_email: str,
    password: str,
    account_type: str = "personal",
    brand_ids: Optional[List[int]] = None,
) -> GtmLinkedInAccount:
    """Upsert a LinkedIn account (status='pending'), ensure its settings +
    browser-profile rows, assign sender brands, and enqueue the one-time
    `linkedin_login` job carrying the ENCRYPTED password. The worker performs
    the headless login and calls `store_session`."""
    email = (linkedin_email or "").strip().lower()
    if not email or not password:
        raise ValueError("linkedin_email and password are required")

    acct = (
        db.query(GtmLinkedInAccount)
        .filter(
            GtmLinkedInAccount.workspace_id == workspace_id,
            GtmLinkedInAccount.linkedin_email == email,
        )
        .first()
    )
    if acct is None:
        acct = GtmLinkedInAccount(
            workspace_id=workspace_id,
            user_id=user_id,
            linkedin_email=email,
            linkedin_account_type=account_type,
            linkedin_session_status="pending",
        )
        db.add(acct)
        db.flush()  # assign acct.id
    else:
        acct.user_id = user_id
        acct.linkedin_account_type = account_type
        acct.linkedin_session_status = "pending"
        acct.linkedin_reauth_required = False
        acct.updated_at = _utcnow()

    # Ensure the 1:1 side tables exist (idempotent).
    if not db.query(GtmLinkedInAccountSettings).filter_by(linkedin_account_id=acct.id).first():
        db.add(GtmLinkedInAccountSettings(linkedin_account_id=acct.id))
    if not db.query(GtmLinkedInBrowserProfile).filter_by(linkedin_account_id=acct.id).first():
        db.add(GtmLinkedInBrowserProfile(linkedin_account_id=acct.id))
    db.commit()

    if brand_ids is not None:
        set_account_brands(db, acct.id, brand_ids)

    # Encrypt the password into the login job payload — never store it on the
    # account row; the job (and its payload) is deleted after processing.
    enqueue_job(
        db,
        workspace_id=workspace_id,
        account_id=acct.id,
        job_type="linkedin_login",
        payload={"enc_password": crypto.encrypt(password)},
    )
    record_event(
        db, workspace_id=workspace_id, linkedin_account_id=acct.id,
        event_type="login_requested",
    )
    return acct


# ── Session capture (customer's own browser — extension / cookie import) ───────
_SAMESITE = {"no_restriction": "None", "none": "None", "lax": "Lax", "strict": "Strict"}


def normalize_cookies(raw: Any) -> str:
    """Convert cookies captured in the CUSTOMER's browser into a Playwright-ready
    JSON string for `context.add_cookies()`. Accepts either:
      - Chrome-extension objects (chrome.cookies.getAll → has `expirationDate`,
        sameSite 'no_restriction'/'lax'/'strict'), or
      - already-Playwright cookies (has `expires`, sameSite 'None'/'Lax'/'Strict').
    Requires the LinkedIn auth cookie `li_at` to be present (else the session is
    useless). Raises ValueError on empty / non-LinkedIn / missing-li_at input."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    out: List[Dict[str, Any]] = []
    has_li_at = False
    for c in raw or []:
        name, value, domain = c.get("name"), c.get("value"), c.get("domain")
        if not name or value is None or not domain:
            continue
        if "linkedin.com" not in domain:  # only LinkedIn cookies
            continue
        ck: Dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": c.get("path") or "/",
            "secure": bool(c.get("secure", True)),
            "httpOnly": bool(c.get("httpOnly", False)),
        }
        exp = c.get("expires", c.get("expirationDate"))
        if exp:
            ck["expires"] = float(exp)
        ck["sameSite"] = _SAMESITE.get(str(c.get("sameSite") or "").lower(), "Lax")
        out.append(ck)
        if name == "li_at":
            has_li_at = True
    if not has_li_at:
        raise ValueError("missing the LinkedIn session cookie 'li_at' — is the user logged in?")
    return json.dumps(out)


def attach_session(
    db: Session,
    *,
    workspace_id: int,
    user_id: Optional[int],
    linkedin_email: str,
    cookies: Any,
    account_type: str = "personal",
    brand_ids: Optional[List[int]] = None,
    display_name: Optional[str] = None,
) -> GtmLinkedInAccount:
    """Attach a LinkedIn session captured on the customer's OWN browser (Chrome
    extension / cookie import) — NO headless login, NO password. Upserts the
    account, then stores the encrypted cookies and marks it active. This is the
    scalable, self-service onboarding path (sidesteps the cloud login / 2FA /
    CAPTCHA / AWS-IP problem — the human already logged in)."""
    email = (linkedin_email or "").strip().lower()
    if not email:
        raise ValueError("linkedin_email is required")
    cookies_json = normalize_cookies(cookies)  # validates + converts (raises on bad input)

    acct = (
        db.query(GtmLinkedInAccount)
        .filter(
            GtmLinkedInAccount.workspace_id == workspace_id,
            GtmLinkedInAccount.linkedin_email == email,
        )
        .first()
    )
    if acct is None:
        acct = GtmLinkedInAccount(
            workspace_id=workspace_id,
            user_id=user_id,
            linkedin_email=email,
            linkedin_account_type=account_type,
            linkedin_session_status="pending",
        )
        db.add(acct)
        db.flush()  # assign acct.id
    else:
        acct.user_id = user_id
        acct.linkedin_account_type = account_type
        acct.linkedin_reauth_required = False
        acct.updated_at = _utcnow()
    if display_name:
        acct.linkedin_display_name = display_name[:255]

    # Ensure the 1:1 side tables exist (idempotent).
    if not db.query(GtmLinkedInAccountSettings).filter_by(linkedin_account_id=acct.id).first():
        db.add(GtmLinkedInAccountSettings(linkedin_account_id=acct.id))
    if not db.query(GtmLinkedInBrowserProfile).filter_by(linkedin_account_id=acct.id).first():
        db.add(GtmLinkedInBrowserProfile(linkedin_account_id=acct.id))
    db.commit()

    if brand_ids is not None:
        set_account_brands(db, acct.id, brand_ids)

    # Store the cookies (encrypted) + mark active + resume any paused leads.
    store_session(db, account_id=acct.id, cookies_json=cookies_json)
    record_event(
        db, workspace_id=workspace_id, linkedin_account_id=acct.id,
        event_type="session_attached", metadata={"source": "browser_capture"},
    )
    logger.info("attach_session: stored captured session for account %s (%s)", acct.id, email)
    return acct


def get_or_create_account(
    db: Session,
    *,
    workspace_id: int,
    user_id: Optional[int],
    linkedin_email: str,
    account_type: str = "personal",
    brand_ids: Optional[List[int]] = None,
    display_name: Optional[str] = None,
) -> GtmLinkedInAccount:
    """Upsert a LinkedIn account row (+ its 1:1 settings/profile rows) WITHOUT a
    session — used by the cloud-login flow, which fills the profile after the user
    logs in inside the hosted browser. No cookies, no password."""
    email = (linkedin_email or "").strip().lower()
    if not email:
        raise ValueError("linkedin_email is required")
    acct = (
        db.query(GtmLinkedInAccount)
        .filter(
            GtmLinkedInAccount.workspace_id == workspace_id,
            GtmLinkedInAccount.linkedin_email == email,
        )
        .first()
    )
    if acct is None:
        acct = GtmLinkedInAccount(
            workspace_id=workspace_id,
            user_id=user_id,
            linkedin_email=email,
            linkedin_account_type=account_type,
            linkedin_session_status="pending",
        )
        db.add(acct)
        db.flush()
    else:
        acct.user_id = user_id
        acct.linkedin_account_type = account_type
    if display_name:
        acct.linkedin_display_name = display_name[:255]
    if not db.query(GtmLinkedInAccountSettings).filter_by(linkedin_account_id=acct.id).first():
        db.add(GtmLinkedInAccountSettings(linkedin_account_id=acct.id))
    if not db.query(GtmLinkedInBrowserProfile).filter_by(linkedin_account_id=acct.id).first():
        db.add(GtmLinkedInBrowserProfile(linkedin_account_id=acct.id))
    db.commit()
    if brand_ids is not None:
        set_account_brands(db, acct.id, brand_ids)
    return acct


def submit_challenge_code(
    db: Session, *, workspace_id: int, account_id: int, code: str
) -> GtmLinkedInJob:
    """Enqueue a `linkedin_login_challenge` job carrying a 2FA/checkpoint code
    the user entered after the login job reported `needs_code`."""
    return enqueue_job(
        db, workspace_id=workspace_id, account_id=account_id,
        job_type="linkedin_login_challenge", payload={"code": (code or "").strip()},
    )


# ── Session lifecycle (called by the Phase-2 worker) ──────────────────────────
def store_session(db: Session, *, account_id: int, cookies_json: str) -> None:
    """Persist the captured session cookies (encrypted) and mark the account
    active. Resumes any leads that were paused for an expired session."""
    acct = db.query(GtmLinkedInAccount).get(account_id)
    if acct is None:
        return
    acct.encrypted_cookies = crypto.encrypt(cookies_json)
    acct.linkedin_session_status = "active"
    acct.linkedin_reauth_required = False
    acct.cooldown_until = None
    acct.last_successful_login = _utcnow()
    acct.last_successful_check = _utcnow()
    acct.updated_at = _utcnow()
    db.commit()
    _resume_account_leads(db, account_id)
    record_event(
        db, workspace_id=acct.workspace_id, linkedin_account_id=account_id,
        event_type="session_active",
    )


def load_cookies(db: Session, account: GtmLinkedInAccount) -> Optional[str]:
    """Decrypt the stored cookie blob → JSON string, or None if absent."""
    if not account.encrypted_cookies:
        return None
    return crypto.decrypt(account.encrypted_cookies)


def mark_session_expired(db: Session, *, account_id: int, reason: str = "session_expired") -> None:
    """Flag the session expired + reauth-required and PAUSE the account's active
    leads (they resume automatically on reconnect via `store_session`)."""
    acct = db.query(GtmLinkedInAccount).get(account_id)
    if acct is None:
        return
    acct.linkedin_session_status = "expired"
    acct.linkedin_reauth_required = True
    acct.updated_at = _utcnow()
    db.commit()
    _pause_account_leads(db, account_id, reason=reason)
    record_event(db, workspace_id=acct.workspace_id, linkedin_account_id=account_id, event_type="session_expired", metadata={"reason": reason})
    record_event(db, workspace_id=acct.workspace_id, linkedin_account_id=account_id, event_type="reauth_required")


def mark_cooldown(db: Session, *, account_id: int, hours: int = 24) -> None:
    """On challenge/checkpoint/rate-limit, rest the account so the scheduler
    skips it until `cooldown_until`."""
    acct = db.query(GtmLinkedInAccount).get(account_id)
    if acct is None:
        return
    acct.cooldown_until = _utcnow() + timedelta(hours=hours)
    acct.linkedin_session_status = "challenge"
    acct.updated_at = _utcnow()
    db.commit()
    record_event(db, workspace_id=acct.workspace_id, linkedin_account_id=account_id, event_type="cooldown_set", metadata={"hours": hours})


def set_paused(db: Session, *, account_id: int, paused: bool) -> None:
    """Manual kill-switch — the scheduler dispatch excludes paused accounts."""
    acct = db.query(GtmLinkedInAccount).get(account_id)
    if acct is None:
        return
    acct.is_paused = bool(paused)
    acct.updated_at = _utcnow()
    db.commit()
    record_event(db, workspace_id=acct.workspace_id, linkedin_account_id=account_id, event_type="account_paused" if paused else "account_resumed")


def disconnect(db: Session, *, account_id: int) -> None:
    """Forget the session: wipe cookies + delete the S3 browser profile +
    mark disconnected. Row is KEPT so history/limits survive a later
    reconnect (mirrors the mailbox disconnect).

    Deleting the S3 profile is critical: reconnecting the same email later
    reuses the SAME account_id (unique on workspace_id+email), which would
    otherwise silently restore the OLD (now stale) profile — wrong IP,
    dead cookies, likely to fail session validation. Cleaner to start
    fresh on reconnect."""
    acct = db.query(GtmLinkedInAccount).get(account_id)
    if acct is None:
        return
    acct.encrypted_cookies = None
    acct.linkedin_session_status = "disconnected"
    acct.linkedin_reauth_required = False
    # Clear the profile pointer too so load_profile won't try to restore
    # a key we just deleted.
    acct.profile_s3_key = None
    acct.profile_updated_at = None
    acct.updated_at = _utcnow()
    db.commit()
    # Best-effort S3 cleanup — a failure here doesn't undo the disconnect.
    try:
        from gtm.linkedin import profile_store
        profile_store.delete_profile(account_id)
        logger.info("gtm_linkedin: disconnect account %s — S3 profile deleted", account_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "gtm_linkedin: disconnect account %s — S3 profile delete failed "
            "(kept row disconnected anyway)", account_id, exc_info=True,
        )
    record_event(db, workspace_id=acct.workspace_id, linkedin_account_id=account_id, event_type="account_disconnected")


def _pause_account_leads(db: Session, account_id: int, *, reason: str) -> None:
    db.query(GtmLinkedInLeadState).filter(
        GtmLinkedInLeadState.linkedin_account_id == account_id,
        GtmLinkedInLeadState.linkedin_sequence_status == "active",
    ).update(
        {"linkedin_sequence_status": "paused", "paused_reason": reason, "updated_at": _utcnow()},
        synchronize_session=False,
    )
    db.commit()


def _resume_account_leads(db: Session, account_id: int) -> None:
    db.query(GtmLinkedInLeadState).filter(
        GtmLinkedInLeadState.linkedin_account_id == account_id,
        GtmLinkedInLeadState.linkedin_sequence_status == "paused",
        GtmLinkedInLeadState.paused_reason == "session_expired",
    ).update(
        {"linkedin_sequence_status": "active", "paused_reason": None, "updated_at": _utcnow()},
        synchronize_session=False,
    )
    db.commit()


# ── Product assignment (M:N) ──────────────────────────────────────────────────
def set_account_brands(db: Session, account_id: int, brand_ids: List[int]) -> List[int]:
    """Replace the account's brand assignments with `brand_ids`. Only brands that
    belong to the account's OWN workspace are accepted — foreign brand ids are
    dropped so an account can't be mapped across tenants."""
    want = {int(b) for b in (brand_ids or [])}
    if want:
        acct = db.query(GtmLinkedInAccount).get(account_id)
        ws_id = getattr(acct, "workspace_id", None)
        if ws_id is None:
            want = set()
        else:
            valid = {
                int(r[0])
                for r in db.execute(
                    text("SELECT id FROM nexus_sender_brands WHERE workspace_id = :w AND id = ANY(:ids)"),
                    {"w": ws_id, "ids": list(want)},
                ).fetchall()
            }
            want = want & valid
    rows = db.query(GtmLinkedInAccountBrand).filter_by(linkedin_account_id=account_id).all()
    have = {r.brand_id for r in rows}
    for r in rows:
        if r.brand_id not in want:
            db.delete(r)
    for bid in want - have:
        db.add(GtmLinkedInAccountBrand(linkedin_account_id=account_id, brand_id=bid))
    db.commit()
    return sorted(want)


def list_account_brands(db: Session, account_id: int) -> List[int]:
    return [
        r.brand_id
        for r in db.query(GtmLinkedInAccountBrand).filter_by(linkedin_account_id=account_id).all()
    ]


def accounts_for_brand(db: Session, workspace_id: int, brand_id: int) -> List[GtmLinkedInAccount]:
    """LinkedIn accounts a campaign on this sender brand may use (campaign-setup
    picker). Only active, non-paused accounts are eligible."""
    return (
        db.query(GtmLinkedInAccount)
        .join(GtmLinkedInAccountBrand, GtmLinkedInAccountBrand.linkedin_account_id == GtmLinkedInAccount.id)
        .filter(
            GtmLinkedInAccount.workspace_id == workspace_id,
            GtmLinkedInAccountBrand.brand_id == brand_id,
            GtmLinkedInAccount.linkedin_session_status == "active",
            GtmLinkedInAccount.is_paused.is_(False),
        )
        .all()
    )
