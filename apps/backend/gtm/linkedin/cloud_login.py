"""GTM LinkedIn Agent — interactive cloud-login capture (Phase 2).

The user logs into LinkedIn **inside a browser we host** (never gives us their
password). We launch a persistent-context Chromium (restoring any existing
profile), open the login page, and poll — non-disruptively — until the session
cookie (`li_at`) appears and we're off the login/checkpoint pages. On success the
FULL browser profile is saved (profile_store, via browser_session_persistent's
save-on-exit) and the account is marked active + flagged to reuse the profile.

This module is the runnable CORE that works two ways with the SAME code:
  • locally / headful — for testing now (see scripts/li_test_drive.py --cloud-login)
  • on a Fargate host wrapped with a live view (noVNC) — the production path.

It never handles or stores the password — LinkedIn authenticates the human in the
browser; we only persist the resulting session profile.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from gtm.linkedin import browser, profile_store
from gtm.linkedin.models import (
    GtmLinkedInAccount,
    GtmLinkedInBrowserProfile,
    GtmLinkedInLoginSession,
)

logger = logging.getLogger("pipelyt.gtm_linkedin.cloud_login")

_LOGIN_URL = "https://www.linkedin.com/login"
_DEFAULT_TIMEOUT_S = 600   # 10 min for the human to finish (password + MFA + captcha)
_DEFAULT_POLL_S = 3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _profile(db: Session, account_id: int) -> Optional[GtmLinkedInBrowserProfile]:
    return db.query(GtmLinkedInBrowserProfile).filter_by(linkedin_account_id=account_id).first()


def create_login_session(db: Session, *, workspace_id: int, account_id: int, ttl_minutes: int = 15) -> GtmLinkedInLoginSession:
    """Create a login-session row the web app can poll (status + viewer_url)."""
    sess = GtmLinkedInLoginSession(
        workspace_id=workspace_id,
        linkedin_account_id=account_id,
        status="starting",
        expires_at=_utcnow() + timedelta(minutes=ttl_minutes),
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


def launch_fargate_login(*, account_id: int, session_id: int) -> Optional[str]:
    """Launch the hosted login browser as a Fargate task (public IP so the user's
    noVNC live-view is reachable). Returns the task ARN, or None if ECS isn't
    configured (env vars below) — the caller then reports 'not configured'.

    Required env: LI_LOGIN_ECS_CLUSTER, LI_LOGIN_ECS_TASKDEF, LI_LOGIN_ECS_SUBNETS
    (comma-sep). Optional: LI_LOGIN_ECS_SECURITY_GROUPS, LI_LOGIN_ECS_CONTAINER.
    """
    cluster = (os.getenv("LI_LOGIN_ECS_CLUSTER") or "").strip()
    taskdef = (os.getenv("LI_LOGIN_ECS_TASKDEF") or "").strip()
    subnets: List[str] = [s.strip() for s in (os.getenv("LI_LOGIN_ECS_SUBNETS") or "").split(",") if s.strip()]
    sgs: List[str] = [s.strip() for s in (os.getenv("LI_LOGIN_ECS_SECURITY_GROUPS") or "").split(",") if s.strip()]
    container = (os.getenv("LI_LOGIN_ECS_CONTAINER") or "linkedin-login").strip()
    if not (cluster and taskdef and subnets):
        logger.warning("launch_fargate_login: ECS not configured (cluster/taskdef/subnets)")
        return None
    try:
        import boto3  # lazy
        ecs = boto3.client("ecs", region_name=os.getenv("AWS_REGION") or "us-east-1")
        net = {"subnets": subnets, "assignPublicIp": "ENABLED"}
        if sgs:
            net["securityGroups"] = sgs
        resp = ecs.run_task(
            cluster=cluster,
            taskDefinition=taskdef,
            launchType="FARGATE",
            count=1,
            networkConfiguration={"awsvpcConfiguration": net},
            overrides={"containerOverrides": [{
                "name": container,
                "environment": [
                    {"name": "LI_ACCOUNT_ID", "value": str(account_id)},
                    {"name": "LI_SESSION_ID", "value": str(session_id)},
                ],
            }]},
        )
        tasks = resp.get("tasks") or []
        arn = tasks[0]["taskArn"] if tasks else None
        logger.info("launch_fargate_login: started task %s for account %s", arn, account_id)
        return arn
    except Exception:  # noqa: BLE001
        logger.exception("launch_fargate_login: failed for account %s", account_id)
        return None


def _set_status(db: Session, sess: Optional[GtmLinkedInLoginSession], status: str, detail: Optional[str] = None) -> None:
    if sess is None:
        return
    sess.status = status
    if detail is not None:
        sess.detail = detail[:2000]
    sess.updated_at = _utcnow()
    db.commit()


def _looks_logged_in(page: Any) -> bool:
    """Non-disruptive check: li_at cookie present AND not sitting on a
    login/checkpoint page. Does NOT navigate (so it won't disturb the user)."""
    try:
        cookies = page.context.cookies()
        if not any(c.get("name") == "li_at" for c in cookies):
            return False
        url = (page.url or "").lower()
        return not any(m in url for m in browser._LOGIN_REDIRECT_MARKERS)
    except Exception:  # noqa: BLE001 — page mid-navigation
        return False


def run_login_session(
    db: Session,
    *,
    account_id: int,
    session_id: Optional[int] = None,
    headless: bool = False,
    proxy: Optional[Dict[str, str]] = None,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
    poll_s: int = _DEFAULT_POLL_S,
) -> Dict[str, Any]:
    """Drive one interactive login: open the login page, wait for the human to
    finish, then persist the profile + mark the account active. Returns a summary
    dict. `proxy` (Phase 3) routes the login browser through the account's IP so
    the session is born on the same IP it will later be used from.
    """
    acct = db.query(GtmLinkedInAccount).get(account_id)
    if acct is None:
        return {"status": "failed", "reason": "account not found"}
    prof = _profile(db, account_id)
    sess = db.query(GtmLinkedInLoginSession).get(session_id) if session_id else None

    _set_status(db, sess, "awaiting_login")
    logged_in = False
    # Restore any existing profile, but SAVE only if the login actually succeeds —
    # a failed/timed-out login must not overwrite a good profile or orphan one.
    # (save_on_exit is a callable evaluated at exit, so it sees the final `logged_in`.)
    with browser.browser_session_persistent(
        account_id, prof, headless=headless, proxy=proxy, save_on_exit=lambda: logged_in
    ) as page:
        try:
            page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:  # noqa: BLE001 — a restored session may redirect straight to /feed
            pass
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if _looks_logged_in(page):
                logged_in = True
                break
            time.sleep(poll_s)

    if logged_in:
        acct.profile_s3_key = profile_store.s3_key(account_id)
        acct.profile_updated_at = _utcnow()
        acct.linkedin_session_status = "active"
        acct.linkedin_reauth_required = False
        acct.last_successful_login = _utcnow()
        db.commit()
        _set_status(db, sess, "saved", "profile captured + account active")
        logger.info("cloud_login: account %s logged in + profile saved", account_id)
        return {"status": "active", "account_id": account_id, "profile_s3_key": acct.profile_s3_key}

    _set_status(db, sess, "failed", "login not completed before timeout")
    logger.warning("cloud_login: account %s login not completed in %ss", account_id, timeout_s)
    return {"status": "failed", "reason": "login not completed in time", "account_id": account_id}


def run_login_session_background(account_id: int, session_id: int) -> None:
    """LOCAL-testing helper: run the interactive login in a background thread with
    its own DB session, so `POST /connect/cloud/start` can drive a real (headful)
    browser on the host WITHOUT Fargate. Gated by the caller (LI_LOGIN_ALLOW_LOCAL).
    The browser opens as a normal window on the host — there is no noVNC iframe
    locally; the web app just polls status until 'saved'."""
    import threading

    def _run() -> None:
        from core.database import SessionLocal
        db = SessionLocal()
        try:
            run_login_session(db, account_id=account_id, session_id=session_id, headless=False)
        except Exception:  # noqa: BLE001
            logger.exception("run_login_session_background: failed for account %s", account_id)
        finally:
            db.close()

    threading.Thread(target=_run, daemon=True, name=f"li-login-{session_id}").start()
