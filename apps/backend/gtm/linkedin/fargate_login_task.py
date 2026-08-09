"""Entrypoint run INSIDE the Fargate login container (Phase 2).

Wrapper `entrypoint.sh` starts a virtual display (Xvfb), a VNC server (x11vmc),
and noVNC (websockify) so the user can SEE and drive the browser, then execs this
script. Here we:
  1. publish the live-view URL (public IP + noVNC path) onto the login-session row
     so the web app can open it, then
  2. run the interactive login (`cloud_login.run_login_session`, headful onto the
     Xvfb display) which saves the profile + marks the account active on success.

Env (set by ECS runtask override + entrypoint.sh):
  LI_ACCOUNT_ID   (required)  the account being connected
  LI_SESSION_ID   (required)  the gtm_linkedin_login_sessions row
  LI_PUBLIC_IP    (optional)  task public IP, discovered by entrypoint.sh
  LI_NOVNC_PORT   (optional)  noVNC port (default 6080)
  LI_LOGIN_TIMEOUT_S (optional, default 600)
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("pipelyt.gtm_linkedin.fargate_login_task")


def _viewer_url() -> str | None:
    ip = (os.getenv("LI_PUBLIC_IP") or "").strip()
    if not ip:
        return None
    port = (os.getenv("LI_NOVNC_PORT") or "6080").strip()
    # autoconnect + fit the remote screen to the iframe.
    return f"http://{ip}:{port}/vnc.html?autoconnect=1&resize=remote"


def main() -> None:
    from core.database import SessionLocal
    from gtm.linkedin import cloud_login
    from gtm.linkedin.models import GtmLinkedInLoginSession

    account_id = int(os.environ["LI_ACCOUNT_ID"])
    session_id = int(os.environ["LI_SESSION_ID"]) if os.environ.get("LI_SESSION_ID") else None
    timeout_s = int(os.environ.get("LI_LOGIN_TIMEOUT_S", "600"))

    db = SessionLocal()
    try:
        # Publish the live-view URL so the web app can open the browser.
        viewer = _viewer_url()
        if session_id and viewer:
            sess = db.query(GtmLinkedInLoginSession).get(session_id)
            if sess:
                sess.viewer_url = viewer
                db.commit()
                logger.info("fargate_login_task: viewer_url=%s (session %s)", viewer, session_id)

        # Phase 3 will pass the account's dedicated residential proxy here.
        res = cloud_login.run_login_session(
            db, account_id=account_id, session_id=session_id,
            headless=False, proxy=None, timeout_s=timeout_s,
        )
        logger.info("fargate_login_task: result=%s", res)
        print("fargate_login_task result:", res)
    finally:
        db.close()


if __name__ == "__main__":
    main()
