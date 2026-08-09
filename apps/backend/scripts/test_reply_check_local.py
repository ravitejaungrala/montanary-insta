"""Local test harness for `browser.detect_reply`.

Runs the reply-detection flow locally against real LinkedIn profiles you've
already messaged. Same architecture as `test_send_message_local.py`:
persistent local Chromium profile you log into once, then reuse.

Prerequisites
-------------
Same as the send-message test — DATABASE_URL, LINKEDIN_COOKIE_KEY,
GTM_LINKEDIN_PROFILE_BUCKET, Playwright, plus the local profile dir
you already logged into.

Usage
-----
Check reply on ONE profile:
    python scripts/test_reply_check_local.py \\
        --account 24 \\
        --local-profile C:\\Users\\Govardhan\\li-test-profile \\
        --profile "https://www.linkedin.com/in/naveen-mamidala-4849b526b/"

Check every lead in a campaign that we've already messaged:
    python scripts/test_reply_check_local.py \\
        --account 24 \\
        --campaign 136 \\
        --local-profile C:\\Users\\Govardhan\\li-test-profile

Check ONE lead by lead-state id (targets the current thread state exactly):
    python scripts/test_reply_check_local.py \\
        --account 24 \\
        --ls 89 \\
        --local-profile C:\\Users\\Govardhan\\li-test-profile

Interpreting output
-------------------
Per lead the script prints one of:
    ✅ REPLIED     — the last message in the thread is FROM the prospect
                     (in prod this stops the sequence + records reply_received)
    ⚪ NO REPLY    — the last message is still OURS
                     (in prod the sequence advances to the next send step)
    ⚠️  UNREADABLE  — thread couldn't be opened / no bubbles rendered
                     (in prod SQS retries; eventually DLQs — never mis-flagged as
                     "no reply", protecting the lead from over-messaging)

How to actually PROVE reply detection works
-------------------------------------------
Reply-detection can only report what's in the thread. To positively test:
  1. Use a SECOND LinkedIn account (or a friend's) that's connected to your
     test account.
  2. From that second account, reply to one of the messages you just sent.
  3. Run this script targeting that lead → should print ✅ REPLIED.
Steps 1-2 give you a KNOWN-replied thread; without them the script will just
tell you what's in the thread (which is fine — every ⚪ NO REPLY confirms
the flow works too, it's just less exciting to look at).
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
import time

# Ensure apps/backend is importable when invoked from anywhere.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
# Make our modules extra chatty.
for name in ("pipelyt.gtm_linkedin.browser",
             "pipelyt.gtm_linkedin.profile_store"):
    logging.getLogger(name).setLevel(logging.INFO)

from sqlalchemy import text  # noqa: E402
from core.database import SessionLocal  # noqa: E402
from gtm.linkedin import browser as li_browser  # noqa: E402
from gtm.linkedin.models import (  # noqa: E402
    GtmLinkedInAccount,
    GtmLinkedInAccountSettings,
    GtmLinkedInBrowserProfile,
)

log = logging.getLogger("test_reply_check_local")


# ── Local persistent profile helper (identical to send-message test) ─────────
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
    """URL-only poll (no reload) so a manual login form stays intact."""
    log.info("local-profile: opening LinkedIn feed to check session …")
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
    _LOGIN_MARKERS = ("/login", "/checkpoint", "/uas/login", "authwall")

    def _url_looks_logged_in() -> bool:
        url = ""
        with contextlib.suppress(Exception):
            url = (page.url or "").lower()
        return "linkedin.com" in url and not any(m in url for m in _LOGIN_MARKERS)

    if _url_looks_logged_in():
        log.info("local-profile: already logged in ✓ (url=%s)", page.url)
        return True

    log.warning("local-profile: NOT LOGGED IN — sign into LinkedIn in the Chromium window.")
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
    log.info("account %s: email=%s session=%s", acct.id, acct.linkedin_email, acct.linkedin_session_status)
    return acct, settings, device


def _resolve_targets(db, account_id: int, campaign_id, profile_url, ls_id, ls_ids):
    """Return [(ls_id, profile_url, member_id)]. Priority:
       --profile > --ls > --ls-ids > --campaign (accepted leads regardless of sent flag)."""
    if profile_url:
        return [(None, profile_url, None)]
    if ls_id:
        row = db.execute(text(
            "SELECT id, linkedin_profile_url, linkedin_member_id "
            "FROM gtm_linkedin_lead_state WHERE id = :i"
        ), {"i": ls_id}).fetchone()
        if not row:
            raise SystemExit(f"lead_state id={ls_id} not found")
        return [(row[0], row[1], row[2])]
    if ls_ids:
        # Explicit list of lead_state ids (comma-separated). Order preserved.
        ids = [int(x.strip()) for x in ls_ids.split(",") if x.strip()]
        rows = db.execute(text(
            "SELECT id, linkedin_profile_url, linkedin_member_id "
            "FROM gtm_linkedin_lead_state WHERE id = ANY(:ids) ORDER BY id"
        ), {"ids": ids}).fetchall()
        found = {r[0] for r in rows}
        missing = [i for i in ids if i not in found]
        if missing:
            log.warning("test: --ls-ids missing rows for: %s", missing)
        log.info("resolved %d lead(s) from --ls-ids", len(rows))
        return [(r[0], r[1], r[2]) for r in rows]
    if not campaign_id:
        raise SystemExit("pass --profile, --ls, --ls-ids, or --campaign")
    # Campaign mode: every accepted 1st-degree lead with a profile URL.
    # (Doesn't filter on linkedin_message_sent_at — local test sends don't
    # write that flag, so requiring it would exclude the leads we care about.)
    rows = db.execute(text(
        "SELECT id, linkedin_profile_url, linkedin_member_id "
        "FROM gtm_linkedin_lead_state "
        "WHERE campaign_id = :c AND linkedin_account_id = :a "
        "  AND linkedin_connection_status = 'accepted' "
        "  AND linkedin_profile_url IS NOT NULL "
        "ORDER BY id"
    ), {"c": campaign_id, "a": account_id}).fetchall()
    log.info("resolved %d accepted lead(s) in campaign %s", len(rows), campaign_id)
    return [(r[0], r[1], r[2]) for r in rows]


def main() -> int:
    p = argparse.ArgumentParser(description="Local test of browser.detect_reply")
    p.add_argument("--account", type=int, required=True, help="gtm_linkedin_accounts.id")
    p.add_argument("--profile", type=str, help="LinkedIn profile URL (single target)")
    p.add_argument("--ls", type=int, help="gtm_linkedin_lead_state.id (single target)")
    p.add_argument("--ls-ids", type=str, help="Comma-separated lead_state ids (e.g. '89,90,91')")
    p.add_argument("--campaign", type=int, help="Check every accepted lead in this campaign")
    p.add_argument("--local-profile", type=str, required=True,
                   help="Local Chromium user_data_dir (same one used for send-message test)")
    p.add_argument("--login-timeout", type=int, default=300)
    p.add_argument("--keep-open", type=int, default=10,
                   help="Seconds to keep the browser open after each check (default 10)")
    args = p.parse_args()

    for env in ("DATABASE_URL",):
        if not os.getenv(env):
            log.warning("env %s is not set — DB queries will fail", env)

    db = SessionLocal()
    try:
        acct, settings, device = _load_account_context(db, args.account)
        targets = _resolve_targets(db, args.account, args.campaign, args.profile, args.ls, args.ls_ids)
        if not targets:
            log.error("no targets — nothing to do (leads with linkedin_message_sent_at set are eligible)")
            return 1
        for i, (ls_id, url, mid) in enumerate(targets, 1):
            log.info("─" * 60)
            log.info("target %d/%d — ls=%s url=%s member_id=%s", i, len(targets), ls_id, url, mid)
            log.info("─" * 60)

        with _local_profile_session(args.local_profile, device) as page:
            if not _prompt_manual_login(page, timeout_s=args.login_timeout):
                log.error("manual login didn't complete — aborting")
                return 2

            replied_count = 0
            not_replied_count = 0
            unreadable_count = 0
            for i, (ls_id, url, mid) in enumerate(targets, 1):
                log.info("═" * 60)
                log.info("[%d/%d] checking reply for ls=%s %s", i, len(targets), ls_id, url)
                log.info("═" * 60)
                try:
                    t0 = time.monotonic()
                    # Post-merge API: read_reply_thread returns the rich dict
                    # (replied, last_body, last_self_body, prospect_msgs,
                    # total_msgs, read_ok). Normalise it into the same shape
                    # the rest of this script prints so no other lines change.
                    read = li_browser.read_reply_thread(
                        page, url, settings,
                        self_name=(acct.linkedin_display_name or None),
                    )
                    result = {
                        "replied":     read.get("replied", False),
                        "reply_body":  read.get("last_body") if read.get("replied") else None,
                        "sender_name": None,  # read_reply_thread returns hrefs, not names
                        "thread_len":  read.get("total_msgs", 0),
                    }
                    elapsed = time.monotonic() - t0
                    if result["replied"]:
                        log.info("[%d/%d] ✅ REPLIED — %s (%.1fs) → prod would STOP the sequence",
                                 i, len(targets), url, elapsed)
                        log.info("     sender: %s", result.get("sender_name"))
                        log.info("     reply body (%d chars):", len(result.get("reply_body") or ""))
                        for line in (result.get("reply_body") or "").splitlines():
                            log.info("       > %s", line)
                        log.info("     ↳ would be fed to follow-up generator as conversation context")
                        replied_count += 1
                    else:
                        log.info("[%d/%d] ⚪ NO REPLY — %s (%.1fs) → prod would advance to next send",
                                 i, len(targets), url, elapsed)
                        log.info("     thread_len=%d last_sender=%s (still us)",
                                 result.get("thread_len"), result.get("sender_name"))
                        not_replied_count += 1
                except Exception as e:  # noqa: BLE001
                    log.error("[%d/%d] ⚠️  UNREADABLE — %s — %s → prod would RETRY (never mis-flag)",
                              i, len(targets), url, e)
                    unreadable_count += 1
                if args.keep_open > 0 and i < len(targets):
                    log.info("waiting %ds before next lead so you can inspect the thread …",
                             args.keep_open)
                    time.sleep(args.keep_open)

            log.info("═" * 60)
            log.info("SUMMARY — replied=%d no_reply=%d unreadable=%d total=%d",
                     replied_count, not_replied_count, unreadable_count, len(targets))
            log.info("═" * 60)
            return 0 if unreadable_count == 0 else 3
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
