"""Local test harness for `browser.do_send_message`.

Runs the message-sending flow locally against a real profile URL, using the
same S3-restored profile the Fargate worker uses. Runs the browser HEADED so
you can watch each step; verbose logs go to stdout so you can debug selector
misses without leaving the terminal.

Prerequisites
-------------
1. `.env` (or shell env) with:
   - DATABASE_URL      Postgres URL for the app DB (needed for account row +
                       device profile lookup)
   - LINKEDIN_COOKIE_KEY  Fernet key used to decrypt the S3 profile
   - GTM_LINKEDIN_PROFILE_BUCKET  S3 bucket where profiles are stored
   - AWS_REGION + AWS creds (env / ~/.aws) with GetObject on that bucket
2. `pip install -r apps/backend/requirements.txt`
3. `python -m playwright install chromium`

Usage
-----
Send to a specific profile URL:
    cd apps/backend
    python scripts/test_send_message_local.py \\
        --account 24 \\
        --profile "https://www.linkedin.com/in/naveen-mamidala-4849b526b/" \\
        --body "Hi Naveen, testing local flow"

Send to every 1st-degree lead in a campaign (dry run — no DB writes, no state
advance; each attempt is independent):
    python scripts/test_send_message_local.py --account 24 --campaign 136

Flags
-----
--headless             run without opening a visible window (default: headed)
--dry-run              stop before the final Send click (still logs every step)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

# Ensure apps/backend is importable when invoked from anywhere.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# Verbose logging BEFORE any project import so every module inherits it.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
# Make our modules extra chatty.
for name in ("pipelyt.gtm_linkedin.browser",
             "pipelyt.gtm_linkedin.profile_store",
             "pipelyt.gtm_linkedin.worker"):
    logging.getLogger(name).setLevel(logging.INFO)

import contextlib  # noqa: E402
from sqlalchemy import text  # noqa: E402
from core.database import SessionLocal  # noqa: E402
from gtm.linkedin import browser as li_browser  # noqa: E402
from gtm.linkedin.models import (  # noqa: E402
    GtmLinkedInAccount,
    GtmLinkedInAccountSettings,
    GtmLinkedInBrowserProfile,
)

log = logging.getLogger("test_send_message_local")


@contextlib.contextmanager
def _local_profile_session(local_dir: str, device):
    """Launch a persistent Chromium context using a LOCAL user_data_dir on disk
    (NOT the S3 profile).  Purpose: local tests can't reuse the Fargate-captured
    S3 profile because LinkedIn ties sessions to IP+fingerprint — a Fargate
    profile opened from your laptop redirects to /login. This helper lets you
    log in ONCE manually in the headed browser; every subsequent test reuses
    the same `local_dir` and stays logged in."""
    from playwright.sync_api import sync_playwright

    os.makedirs(local_dir, exist_ok=True)
    log.info("local-profile: user_data_dir=%s", local_dir)

    ua = getattr(device, "user_agent", None) or (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    vp = str(getattr(device, "viewport", "1366x768") or "1366x768")
    try:
        w, h = (int(x) for x in vp.lower().split("x"))
    except Exception:
        w, h = 1366, 768
    locale = getattr(device, "browser_locale", None) or "en-US"
    tz = getattr(device, "browser_timezone", None) or "Asia/Kolkata"

    pw = sync_playwright().start()
    context = None
    try:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=local_dir,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=ua,
            viewport={"width": w, "height": h},
            locale=locale,
            timezone_id=tz,
        )
        page = context.pages[0] if context.pages else context.new_page()
        yield page
    finally:
        with contextlib.suppress(Exception):
            if context:
                context.close()
        with contextlib.suppress(Exception):
            pw.stop()


def _prompt_manual_login(page, timeout_s: int = 300) -> bool:
    """Open LinkedIn feed. If the session is invalid, poll until the user has
    logged in manually in the visible window. Returns True once session is
    detected valid, False on timeout.

    IMPORTANT: after the initial navigation we check page.url ONLY (no goto,
    no reload) — otherwise every poll wipes what the user is typing in the
    login form."""
    log.info("local-profile: opening LinkedIn feed to check session …")
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
    _LOGIN_MARKERS = ("/login", "/checkpoint", "/uas/login", "authwall")

    def _url_looks_logged_in() -> bool:
        # Zero-navigation check: read the URL Chromium is already on. The user
        # navigates by submitting the form; we only observe.
        url = ""
        with contextlib.suppress(Exception):
            url = (page.url or "").lower()
        return "linkedin.com" in url and not any(m in url for m in _LOGIN_MARKERS)

    if _url_looks_logged_in():
        log.info("local-profile: already logged in ✓ (url=%s)", page.url)
        return True

    log.warning("local-profile: NOT LOGGED IN — a Chromium window is open.")
    log.warning("local-profile: → Sign into LinkedIn IN THAT WINDOW now.")
    log.warning("local-profile: → I'll poll the URL every 5s (no reload) for up to %ds.",
                timeout_s)
    waited = 0
    while waited < timeout_s:
        time.sleep(5)
        waited += 5
        if _url_looks_logged_in():
            log.info("local-profile: login detected ✓ (after %ds, url=%s)", waited, page.url)
            return True
        log.info("local-profile: still waiting … (%ds elapsed, url=%s)", waited, page.url)
    log.error("local-profile: timed out after %ds waiting for manual login", timeout_s)
    return False


def _load_account_context(db, account_id: int):
    """Fetch account, per-account settings, and device profile.  Fail fast if any
    is missing — the flow can't run without them."""
    acct = db.query(GtmLinkedInAccount).filter_by(id=account_id).first()
    if not acct:
        raise SystemExit(f"account {account_id} not found in gtm_linkedin_accounts")
    settings = db.query(GtmLinkedInAccountSettings).filter_by(linkedin_account_id=account_id).first()
    if not settings:
        raise SystemExit(f"no gtm_linkedin_account_settings row for account {account_id}")
    device = db.query(GtmLinkedInBrowserProfile).filter_by(linkedin_account_id=account_id).first()
    if not device:
        raise SystemExit(f"no gtm_linkedin_browser_profiles row for account {account_id}")
    log.info(
        "account %s: email=%s session=%s device: ua=%s viewport=%s tz=%s",
        acct.id, acct.linkedin_email, acct.linkedin_session_status,
        (device.user_agent or "")[:60], device.viewport, device.browser_timezone,
    )
    return acct, settings, device


def _resolve_targets(db, account_id: int, campaign_id: int | None, profile_url: str | None):
    """Return a list of (profile_url, display_name, ls_id) tuples to message.

    `ls_id` is the gtm_linkedin_lead_state.id when known (needed to look up the
    stored per-lead message body); None for a bare --profile URL."""
    if profile_url:
        return [(profile_url, None, None)]
    if not campaign_id:
        raise SystemExit("must pass --profile OR --campaign")
    rows = db.execute(
        text(
            "SELECT ls.id, ls.linkedin_profile_url, ls.last_seen_name "
            "FROM gtm_linkedin_lead_state ls "
            "WHERE ls.campaign_id = :cid AND ls.linkedin_account_id = :aid "
            "  AND ls.linkedin_connection_status = 'accepted' "
            "  AND ls.linkedin_profile_url IS NOT NULL "
            "ORDER BY ls.id"
        ),
        {"cid": campaign_id, "aid": account_id},
    ).fetchall()
    log.info("resolved %d accepted 1st-degree lead(s) for campaign %s", len(rows), campaign_id)
    return [(r[1], r[2], r[0]) for r in rows]


def _stored_body_for(db, ls_id: int) -> tuple[str, str]:
    """Return (body, source) — the SAME copy the worker's scheduler.
    _content_payload would produce for this lead's current send_message step.
    Source is 'stored'|'generated'|'error' so the log tells you where it came from."""
    from gtm.linkedin import scheduler as li_scheduler
    from gtm.linkedin.models import GtmLinkedInLeadState

    state = db.query(GtmLinkedInLeadState).filter_by(id=ls_id).first()
    if not state:
        return ("", "error: no lead_state")
    try:
        payload = li_scheduler._content_payload(db, state, "send_message")
        body = (payload or {}).get("body") or ""
        # Cheap heuristic: if a stored inmail step exists, that's what the payload used.
        stored = li_scheduler._stored_inmail(
            db, state.workspace_id, state.lead_id,
            li_scheduler._INMAIL_STEP_IDX.get(state.linkedin_current_step or "", 1),
        )
        source = "stored" if stored else "generated"
        return (body, source)
    except Exception as e:  # noqa: BLE001
        log.exception("stored-body lookup failed for ls=%s", ls_id)
        return ("", f"error: {e}")


def main() -> int:
    p = argparse.ArgumentParser(description="Local test of browser.do_send_message")
    p.add_argument("--account", type=int, required=True, help="gtm_linkedin_accounts.id")
    p.add_argument("--profile", type=str, default=None,
                   help="LinkedIn profile URL to message (single target)")
    p.add_argument("--campaign", type=int, default=None,
                   help="Message every accepted lead in this campaign (batch)")
    p.add_argument("--body", type=str, default=None,
                   help="Override the message body. If OMITTED, uses the SAME per-lead "
                        "stored copy the worker would (nexus_linkedin_messages, else "
                        "generated). Only applies when using --campaign; for --profile "
                        "you must pass --body since there's no lead_state to look up.")
    p.add_argument("--headless", action="store_true",
                   help="Run browser headless (default: visible for debugging)")
    p.add_argument("--dry-run", action="store_true",
                   help="Log every step but stop before final Send click")
    p.add_argument("--local-profile", type=str, default=None,
                   help="Use a LOCAL Chromium user_data_dir (path) instead of the S3 "
                        "profile. First run: log in manually in the browser window. "
                        "Subsequent runs reuse the same dir. Recommended for local "
                        "testing because the Fargate S3 profile fails on your laptop's IP.")
    p.add_argument("--login-timeout", type=int, default=300,
                   help="Seconds to wait for manual login when using --local-profile "
                        "(default 300).")
    args = p.parse_args()

    # Sanity-check required env — S3 restore silently no-ops without them.
    for env in ("DATABASE_URL", "LINKEDIN_COOKIE_KEY", "GTM_LINKEDIN_PROFILE_BUCKET"):
        if not os.getenv(env):
            log.warning("env %s is not set — profile restore may fail", env)

    # Local runs default to headed unless --headless is passed.  browser_session_persistent
    # reads GTM_LINKEDIN_HEADLESS, so set it here rather than plumbing another arg.
    os.environ["GTM_LINKEDIN_HEADLESS"] = "true" if args.headless else "false"

    db = SessionLocal()
    try:
        acct, settings, device = _load_account_context(db, args.account)
        targets = _resolve_targets(db, args.account, args.campaign, args.profile)
        if not targets:
            log.error("no targets — nothing to do")
            return 1
        for i, (url, name, ls_id) in enumerate(targets, 1):
            log.info("─" * 60)
            log.info("target %d/%d — %s (name hint=%r ls=%s)", i, len(targets), url, name, ls_id)
            log.info("─" * 60)

        # Two paths:
        #  (a) --local-profile <dir>  → local Chromium user_data_dir, manual
        #      login on first run.  Recommended for laptop testing.
        #  (b) default               → restore the S3 profile the Fargate task
        #      uses.  Almost always fails locally because LinkedIn ties sessions
        #      to IP+fingerprint (the S3 profile was captured on Fargate's IP).
        if args.local_profile:
            session_cm = _local_profile_session(args.local_profile, device)
        else:
            session_cm = li_browser.browser_session_persistent(
                args.account, device, headless=args.headless, save_on_exit=False,
            )

        with session_cm as page:
            # Session check.  With --local-profile, prompt the user to log in
            # manually (poll for up to --login-timeout seconds); with the S3
            # path, fail immediately since we can't recover.
            if args.local_profile:
                if not _prompt_manual_login(page, timeout_s=args.login_timeout):
                    log.error("local-profile: manual login didn't complete in time — aborting")
                    return 2
            else:
                if not li_browser.session_is_valid(page):
                    log.error("session INVALID — LinkedIn redirected to login/checkpoint. "
                              "S3 profiles captured on Fargate almost never work on a laptop IP. "
                              "Use --local-profile <dir> instead (log in once, reuse forever).")
                    return 2
                log.info("session VALID — proceeding")

            # Reopen a DB session — the outer one was closed after target resolution.
            body_db = SessionLocal()
            successes, failures = 0, 0
            try:
                for i, (url, _name, ls_id) in enumerate(targets, 1):
                    log.info("═" * 60)
                    log.info("[%d/%d] sending to %s (ls=%s)", i, len(targets), url, ls_id)
                    log.info("═" * 60)

                    # Pick the body per-lead:
                    #   1. --body override wins (mostly for --profile URL testing).
                    #   2. Otherwise use the SAME stored copy the worker would.
                    #   3. --profile without --body → error (there's no lead to look up).
                    if args.body:
                        body = args.body
                        body_src = "cli-override"
                    elif ls_id is None:
                        log.error("no --body given and no lead_state to look up "
                                  "(happens when you pass --profile without --body)")
                        failures += 1
                        continue
                    else:
                        body, body_src = _stored_body_for(body_db, ls_id)
                        if not body:
                            log.error("no body available for ls=%s — %s", ls_id, body_src)
                            failures += 1
                            continue
                    log.info("[%d/%d] body_source=%s body=%r",
                             i, len(targets), body_src, body[:120] + ("…" if len(body) > 120 else ""))

                    if args.dry_run:
                        log.info("--dry-run: opening profile only, NOT sending")
                        li_browser.open_profile(page, url, settings)
                        state = li_browser.read_profile_state(page, url, settings)
                        log.info("dry-run: profile state=%s signals=%s",
                                 state.get("state"), state.get("signals"))
                        continue

                    result = li_browser.do_send_message(page, url, body, settings)
                    if result.get("ok"):
                        log.info("[%d/%d] ✅ SENT to %s", i, len(targets), url)
                        successes += 1
                    else:
                        log.error("[%d/%d] ❌ FAILED to %s — %s",
                                  i, len(targets), url, result.get("detail"))
                        failures += 1
            finally:
                body_db.close()

            log.info("═" * 60)
            log.info("SUMMARY — sent=%d failed=%d total=%d", successes, failures, len(targets))
            log.info("═" * 60)
            return 0 if failures == 0 else 3
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
