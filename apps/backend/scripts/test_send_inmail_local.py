"""Local test harness for `browser.do_send_inmail`.

Runs the InMail flow locally against a Premium LinkedIn account, using the
same local-persistent-profile pattern as the DM test (log in ONCE manually,
reuse forever).  Verbose per-step logs go to stdout so you can debug
selector misses without leaving the terminal.

Prerequisites
-------------
1. A Premium (or Sales Navigator) LinkedIn account connected in the app.
2. `.env` (or shell env) with:
   - DATABASE_URL      Postgres URL for the app DB
   - LINKEDIN_COOKIE_KEY, GTM_LINKEDIN_PROFILE_BUCKET, AWS creds
     (only needed if you don't pass --local-profile)
3. `pip install -r apps/backend/requirements.txt`
4. `python -m playwright install chromium`

Usage
-----
Single-target InMail with an explicit subject + body:
    cd apps/backend
    python scripts/test_send_inmail_local.py \\
        --account <ID> \\
        --local-profile C:\\path\\to\\li-premium-profile \\
        --profile "https://www.linkedin.com/in/<slug>/" \\
        --subject "Quick question about your Data platform" \\
        --body "Hi X, saw your post about... Would love a quick chat."

Batch every 2nd-degree lead in a campaign that has an InMail step queued:
    python scripts/test_send_inmail_local.py \\
        --account <ID> \\
        --local-profile C:\\path\\to\\li-premium-profile \\
        --campaign <ID>

Flags
-----
--dry-run              Open the profile only, DO NOT click Send.  Useful for
                       verifying the compose-URL bypass fires and the composer
                       opens without burning an InMail credit.
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
for name in ("pipelyt.gtm_linkedin.browser", "pipelyt.gtm_linkedin.profile_store"):
    logging.getLogger(name).setLevel(logging.INFO)

from sqlalchemy import text  # noqa: E402
from core.database import SessionLocal  # noqa: E402
from gtm.linkedin import browser as li_browser  # noqa: E402
from gtm.linkedin.models import (  # noqa: E402
    GtmLinkedInAccount,
    GtmLinkedInAccountSettings,
    GtmLinkedInBrowserProfile,
)

log = logging.getLogger("test_send_inmail_local")


@contextlib.contextmanager
def _local_profile_session(local_dir: str, device):
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
    log.info("local-profile: opening LinkedIn feed to check session …")
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
    _LOGIN_MARKERS = ("/login", "/checkpoint", "/uas/login", "authwall")

    def _url_looks_logged_in() -> bool:
        url = ""
        with contextlib.suppress(Exception):
            url = (page.url or "").lower()
        return "linkedin.com" in url and not any(m in url for m in _LOGIN_MARKERS)

    if _url_looks_logged_in():
        log.info("local-profile: already logged in ✓")
        return True
    log.warning("local-profile: NOT LOGGED IN — sign in inside the Chromium window that just opened")
    waited = 0
    while waited < timeout_s:
        time.sleep(5)
        waited += 5
        if _url_looks_logged_in():
            log.info("local-profile: login detected ✓ (after %ds)", waited)
            return True
        log.info("local-profile: still waiting … (%ds elapsed, url=%s)", waited, page.url)
    return False


def _load_account_context(db, account_id: int):
    acct = db.query(GtmLinkedInAccount).filter_by(id=account_id).first()
    if not acct:
        raise SystemExit(f"account {account_id} not found")
    settings = db.query(GtmLinkedInAccountSettings).filter_by(linkedin_account_id=account_id).first()
    device = db.query(GtmLinkedInBrowserProfile).filter_by(linkedin_account_id=account_id).first()
    if not device:
        log.warning("no browser profile row for account %d — using defaults", account_id)
    log.info(
        "account %s: email=%s type=%s session=%s",
        acct.id, acct.linkedin_email,
        getattr(acct, "linkedin_account_type", "?"),
        acct.linkedin_session_status,
    )
    return acct, settings, device


def _resolve_targets(db, account_id: int, campaign_id, profile_url, ls_id):
    if profile_url:
        return [(None, profile_url)]
    if ls_id:
        row = db.execute(text(
            "SELECT id, linkedin_profile_url FROM gtm_linkedin_lead_state WHERE id = :i"
        ), {"i": ls_id}).fetchone()
        if not row:
            raise SystemExit(f"lead_state id={ls_id} not found")
        return [(row[0], row[1])]
    if not campaign_id:
        raise SystemExit("pass --profile, --ls, or --campaign")
    # Pick leads currently on an inmail step OR pending 1st touch.
    rows = db.execute(text(
        "SELECT id, linkedin_profile_url FROM gtm_linkedin_lead_state "
        "WHERE campaign_id = :c AND linkedin_account_id = :a "
        "  AND linkedin_current_step LIKE 'inmail%' "
        "  AND linkedin_inmail_sent_at IS NULL "  # skip leads we already InMailed

        "  AND linkedin_profile_url IS NOT NULL "
        "ORDER BY id"
    ), {"c": campaign_id, "a": account_id}).fetchall()
    log.info("resolved %d InMail-step lead(s) in campaign %s", len(rows), campaign_id)
    return [(r[0], r[1]) for r in rows]


def _stored_inmail_for(db, ls_id: int) -> tuple[str, str, str]:
    """Return (subject, body, source) — SAME copy the worker's scheduler.
    _content_payload would use for this lead's current inmail step."""
    from gtm.linkedin import scheduler as li_scheduler
    from gtm.linkedin.models import GtmLinkedInLeadState

    state = db.query(GtmLinkedInLeadState).filter_by(id=ls_id).first()
    if not state:
        return ("", "", "error: no lead_state")
    try:
        payload = li_scheduler._content_payload(db, state, "send_inmail")
        subject = (payload or {}).get("subject") or ""
        body = (payload or {}).get("body") or ""
        return (subject, body, "stored" if body else "empty")
    except Exception as e:  # noqa: BLE001
        log.exception("stored-inmail lookup failed for ls=%s", ls_id)
        return ("", "", f"error: {e}")


def main() -> int:
    p = argparse.ArgumentParser(description="Local test of browser.do_send_inmail")
    p.add_argument("--account", type=int, required=True, help="gtm_linkedin_accounts.id")
    p.add_argument("--profile", type=str, help="LinkedIn profile URL (single target)")
    p.add_argument("--ls", type=int, help="gtm_linkedin_lead_state.id (single target)")
    p.add_argument("--campaign", type=int, help="Batch — every InMail-step lead in this campaign")
    p.add_argument("--subject", type=str, default=None,
                   help="Override subject. Omit to use the stored/generated one.")
    p.add_argument("--body", type=str, default=None,
                   help="Override body. Omit to use the stored/generated one.")
    p.add_argument("--local-profile", type=str, required=True,
                   help="Local Chromium user_data_dir (log in once, reuse forever)")
    p.add_argument("--dry-run", action="store_true",
                   help="Open profile only, DO NOT send (no credit burned)")
    p.add_argument("--login-timeout", type=int, default=300)
    p.add_argument("--keep-open", type=int, default=15,
                   help="Seconds to keep browser open after each target (default 15)")
    args = p.parse_args()

    for env in ("DATABASE_URL",):
        if not os.getenv(env):
            log.warning("env %s is not set — DB queries will fail", env)

    db = SessionLocal()
    try:
        acct, settings, device = _load_account_context(db, args.account)
        targets = _resolve_targets(db, args.account, args.campaign, args.profile, args.ls)
        if not targets:
            log.error("no targets — nothing to do")
            return 1

        for i, (ls_id, url) in enumerate(targets, 1):
            log.info("─" * 60)
            log.info("target %d/%d — ls=%s url=%s", i, len(targets), ls_id, url)
            log.info("─" * 60)

        with _local_profile_session(args.local_profile, device) as page:
            if not _prompt_manual_login(page, timeout_s=args.login_timeout):
                log.error("manual login didn't complete — aborting")
                return 2

            successes, no_credits, failures = 0, 0, 0
            body_db = SessionLocal()
            try:
                for i, (ls_id, url) in enumerate(targets, 1):
                    log.info("═" * 60)
                    log.info("[%d/%d] target ls=%s url=%s", i, len(targets), ls_id, url)
                    log.info("═" * 60)

                    # Resolve subject + body per target.
                    if args.subject and args.body:
                        subject = args.subject
                        body = args.body
                        src = "cli-override"
                    elif ls_id is None:
                        log.error("no --subject/--body given and no lead_state — pass both flags")
                        failures += 1
                        continue
                    else:
                        stored_subject, stored_body, src = _stored_inmail_for(body_db, ls_id)
                        subject = args.subject or stored_subject
                        body = args.body or stored_body
                        if not body:
                            log.error("no body available for ls=%s — %s", ls_id, src)
                            failures += 1
                            continue

                    log.info("[%d/%d] source=%s  subject=%r  body_preview=%r…",
                             i, len(targets), src, subject[:80], body[:80])

                    if args.dry_run:
                        log.info("--dry-run: opening profile only, NOT sending")
                        li_browser.open_profile(page, url, settings)
                        state = li_browser.read_profile_state(page, url, settings)
                        log.info("dry-run: profile state=%s signals=%s",
                                 state.get("state"), state.get("signals"))
                        if args.keep_open > 0:
                            log.info("keeping browser open %ds so you can inspect", args.keep_open)
                            time.sleep(args.keep_open)
                        continue

                    t0 = time.monotonic()
                    result = li_browser.do_send_inmail(page, url, subject, body, settings)
                    elapsed = time.monotonic() - t0

                    if result.get("ok"):
                        log.info("[%d/%d] ✅ SENT to %s (%.1fs) — verified=%s",
                                 i, len(targets), url, elapsed, result.get("verified"))
                        successes += 1
                    elif result.get("no_credit"):
                        log.warning("[%d/%d] ⚠️  SKIPPED %s (%.1fs) — %s",
                                    i, len(targets), url, elapsed, result.get("detail"))
                        no_credits += 1
                    else:
                        log.error("[%d/%d] ❌ FAILED %s (%.1fs) — %s",
                                  i, len(targets), url, elapsed, result.get("detail"))
                        failures += 1

                    if args.keep_open > 0 and i < len(targets):
                        log.info("waiting %ds before next target so you can inspect …",
                                 args.keep_open)
                        time.sleep(args.keep_open)
            finally:
                body_db.close()

            log.info("═" * 60)
            log.info("SUMMARY — sent=%d skipped(no_credit)=%d failed=%d total=%d",
                     successes, no_credits, failures, len(targets))
            log.info("═" * 60)
            return 0 if failures == 0 else 3
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
