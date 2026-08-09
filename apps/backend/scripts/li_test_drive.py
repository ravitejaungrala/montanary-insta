"""Local manual test-drive for the GTM LinkedIn Agent — runs the worker jobs
INLINE (no SQS/EventBridge needed). Walks: connect → 2FA → send connection
request → check acceptance, printing the resulting events + state at each step.

Run from apps/backend:
    python scripts/li_test_drive.py            # interactive test
    python scripts/li_test_drive.py --cleanup  # delete this test's throwaway rows

Prerequisites:
    pip install -r requirements.txt
    python -m playwright install chromium
    .env has  LINKEDIN_COOKIE_KEY=<fernet key>
    .env has  GTM_LINKEDIN_HEADLESS=false      # so you can WATCH the browser

⚠️  Use a THROWAWAY LinkedIn account — automation can get accounts restricted.

All rows are created under a throwaway workspace (default 990001) so nothing real
is touched; `--cleanup` removes them.
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)

from dotenv import load_dotenv

load_dotenv(os.path.join(_BACKEND, ".env"))

from sqlalchemy import text  # noqa: E402
from core.database import SessionLocal  # noqa: E402
from gtm.linkedin import auth, browser, worker  # noqa: E402
from gtm.linkedin.models import (  # noqa: E402
    GtmLinkedInAccount,
    GtmLinkedInAccountSettings,
    GtmLinkedInBrowserProfile,
    GtmLinkedInJob,
    GtmLinkedInLeadState,
)

WS = int(os.getenv("LI_TEST_WORKSPACE", "990001"))
LEAD_ID = 990002
CAMPAIGN_ID = 990003


def _latest_job(db, account_id, job_type):
    return (
        db.query(GtmLinkedInJob)
        .filter_by(linkedin_account_id=account_id, job_type=job_type)
        .order_by(GtmLinkedInJob.id.desc())
        .first()
    )


def _events(db, account_id):
    return [
        r[0]
        for r in db.execute(
            text("SELECT event_type FROM gtm_linkedin_events WHERE linkedin_account_id=:a ORDER BY id"),
            {"a": account_id},
        ).fetchall()
    ]


def _get_or_create_account(db, email):
    """Create (or reuse) the throwaway account row + its 1:1 settings/profile rows.
    No password — we capture the session by MANUAL login below."""
    acct = db.query(GtmLinkedInAccount).filter_by(workspace_id=WS, linkedin_email=email).first()
    if acct is None:
        acct = GtmLinkedInAccount(
            workspace_id=WS, linkedin_email=email,
            linkedin_account_type="personal", linkedin_session_status="pending",
        )
        db.add(acct)
        db.flush()
    if not db.query(GtmLinkedInAccountSettings).filter_by(linkedin_account_id=acct.id).first():
        db.add(GtmLinkedInAccountSettings(linkedin_account_id=acct.id))
    if not db.query(GtmLinkedInBrowserProfile).filter_by(linkedin_account_id=acct.id).first():
        db.add(GtmLinkedInBrowserProfile(linkedin_account_id=acct.id))
    db.commit()
    return acct


def manual_login(db, acct):
    """Open a VISIBLE browser, let the user log in to LinkedIn by hand (captcha /
    2FA included), then capture + store the encrypted session cookies. Far more
    reliable than scripted credential login, which LinkedIn bot-blocks."""
    prof = db.query(GtmLinkedInBrowserProfile).filter_by(linkedin_account_id=acct.id).first()
    with browser.browser_session(None, prof, headless=False) as page:
        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=60000)
        print("\n   >>> A browser window is open. Log in to LinkedIn MANUALLY there")
        print("   >>> (finish any captcha / 'verify it's you' / 2FA).")
        print("   >>> When your LinkedIn FEED is fully loaded, switch back here.")
        input("   >>> Press Enter once you are logged in… ")
        if browser.session_is_valid(page):
            auth.store_session(db, account_id=acct.id, cookies_json=browser.export_cookies(page))
            return True
        return False


def cleanup():
    db = SessionLocal()
    ids = [r[0] for r in db.execute(text("SELECT id FROM gtm_linkedin_accounts WHERE workspace_id=:w"), {"w": WS}).fetchall()]
    db.execute(text("DELETE FROM gtm_linkedin_events WHERE workspace_id=:w"), {"w": WS})
    db.execute(text("DELETE FROM gtm_linkedin_jobs WHERE workspace_id=:w"), {"w": WS})
    db.execute(text("DELETE FROM gtm_linkedin_lead_state WHERE workspace_id=:w"), {"w": WS})
    if ids:
        for t in ("gtm_linkedin_account_settings", "gtm_linkedin_browser_profiles", "gtm_linkedin_account_brands"):
            db.execute(text(f"DELETE FROM {t} WHERE linkedin_account_id = ANY(:i)"), {"i": ids})
    db.execute(text("DELETE FROM gtm_linkedin_accounts WHERE workspace_id=:w"), {"w": WS})
    db.commit()
    print(f"[cleanup] removed throwaway rows for workspace {WS}")
    db.close()


def main():
    if not (os.getenv("LINKEDIN_COOKIE_KEY") or "").strip():
        print("ERROR: LINKEDIN_COOKIE_KEY is not set in .env — cannot encrypt the login. Aborting.")
        return
    if os.getenv("GTM_LINKEDIN_HEADLESS", "").strip().lower() != "false":
        print("NOTE: GTM_LINKEDIN_HEADLESS is not 'false' — the browser will run HIDDEN. "
              "Set GTM_LINKEDIN_HEADLESS=false in .env to watch it.\n")

    db = SessionLocal()
    email = input("LinkedIn email (the account you'll log in as): ").strip()
    if not email:
        print("No email entered — aborting.")
        db.close()
        return

    # ── 1) Connect via MANUAL login (capture session cookies) ───────────────
    print("\n[1/4] Opening a browser for manual login…")
    acct = _get_or_create_account(db, email)
    if not manual_login(db, acct):
        print("\nStill not logged in — re-run and complete the login in the browser. Stopping.")
        db.close()
        return
    db.refresh(acct)
    print("   ✓ logged in, cookies captured. session_status:", acct.linkedin_session_status)

    # ── 3) Send a real connection request (browser test) ────────────────────
    target = input("\nTarget LinkedIn profile URL to connect to: ").strip()
    note = input("Connection note (blank = default): ").strip() or \
        "Hi — testing our outreach tooling, please disregard. Happy to connect!"
    st = (
        db.query(GtmLinkedInLeadState)
        .filter_by(lead_id=LEAD_ID, campaign_id=CAMPAIGN_ID)
        .first()
    )
    if st is None:
        st = GtmLinkedInLeadState(
            workspace_id=WS, lead_id=LEAD_ID, campaign_id=CAMPAIGN_ID,
            linkedin_account_id=acct.id, linkedin_profile_url=target,
            linkedin_sequence_status="active", linkedin_current_step="connection_request",
            linkedin_current_branch="pending_acceptance", workflow_mode="standard",
        )
        db.add(st)
    else:
        st.linkedin_profile_url = target
        st.linkedin_account_id = acct.id
        st.linkedin_sequence_status = "active"
        st.linkedin_current_step = "connection_request"
    db.commit()

    print("\n[3/4] Sending connection request (watch the browser)…")
    cj = auth.enqueue_job(
        db, workspace_id=WS, account_id=acct.id, job_type="send_connection_request",
        payload={"note": note}, lead_id=LEAD_ID, campaign_id=CAMPAIGN_ID,
    )
    print("   connection job:", worker.handle_job(db, cj.id))
    db.refresh(st)
    print("   connection_status:", st.linkedin_connection_status, "| next step:", st.linkedin_current_step)

    # ── 4) Check acceptance (run after the target accepts) ──────────────────
    if input("\n[4/4] Run an acceptance check now? (only meaningful after they accept) [y/N]: ").strip().lower() == "y":
        st.linkedin_current_step = "check_acceptance"
        db.commit()
        cj = auth.enqueue_job(
            db, workspace_id=WS, account_id=acct.id, job_type="check_connection_status",
            payload={}, lead_id=LEAD_ID, campaign_id=CAMPAIGN_ID,
        )
        print("   acceptance check:", worker.handle_job(db, cj.id))
        db.refresh(st)
        print("   connection_status:", st.linkedin_connection_status, "| next step:", st.linkedin_current_step)

    print("\nEvents recorded:", _events(db, acct.id))
    print("\nDone. Clean up this throwaway data with:  python scripts/li_test_drive.py --cleanup")
    db.close()


def step():
    """Drive the test lead's CURRENT step once (reuses the stored session — no
    re-login). Run repeatedly to walk the cadence and test branching:
      accept the request, then:  --step (check_acceptance → CONNECTED → message_1)
                                  --step (sends message_1)
                                  --step (check_reply_1) … etc.
      not accepted:               --step ×N (check_acceptance → pending → InMail branch)
    """
    from gtm.linkedin.scheduler import _STEP_JOB, _SEND_JOBS  # noqa
    db = SessionLocal()
    st = db.query(GtmLinkedInLeadState).filter_by(lead_id=LEAD_ID, campaign_id=CAMPAIGN_ID).first()
    if st is None:
        print("No test lead_state — run the connect test first (python scripts/li_test_drive.py).")
        db.close()
        return
    acct = db.query(GtmLinkedInAccount).get(st.linkedin_account_id)
    print(f"Before: step={st.linkedin_current_step} branch={st.linkedin_current_branch} "
          f"status={st.linkedin_sequence_status} conn={st.linkedin_connection_status}")
    cur = st.linkedin_current_step or ""
    jt = _STEP_JOB.get(cur)
    if not jt:
        print(f"No job mapping for step '{cur}' (sequence may be completed/stopped).")
        db.close()
        return
    payload = {}
    if jt == "send_message":
        payload = {"body": input("Message body: ").strip() or "Hi — testing the message branch."}
    elif jt == "send_inmail":
        payload = {"subject": input("InMail subject: ").strip() or "Quick note",
                   "body": input("InMail body: ").strip() or "Hi — testing the InMail branch."}
    elif jt == "send_connection_request":
        payload = {"note": input("Connection note: ").strip() or "Hi — testing."}
    job = auth.enqueue_job(db, workspace_id=WS, account_id=acct.id, job_type=jt,
                           payload=payload, lead_id=LEAD_ID, campaign_id=CAMPAIGN_ID)
    print(f"\nRunning {jt} (step={cur})… watch the browser.")
    print("  result:", worker.handle_job(db, job.id))
    db.refresh(st)
    print(f"After:  step={st.linkedin_current_step} branch={st.linkedin_current_branch} "
          f"status={st.linkedin_sequence_status} conn={st.linkedin_connection_status}")
    print("  events:", _events(db, acct.id))
    db.close()


def test_connect():
    """Run do_connect against a REAL profile locally (visible browser) using an
    active account's captured session — so you can WATCH the invite dialog and
    grab its HTML (li_debug_connect_failed.* in this folder) to fix selectors.

        python scripts/li_test_drive.py --test-connect <profile_url> [account_email]

    Set GTM_LINKEDIN_HEADLESS=false in .env so the browser is visible.
    """
    url = next((a for a in sys.argv[1:] if "linkedin.com/in/" in a or a.startswith("http")), None)
    email = next((a.strip().lower() for a in sys.argv[1:] if "@" in a), None)
    if not url:
        print("usage: python scripts/li_test_drive.py --test-connect <profile_url> [account_email]")
        return
    db = SessionLocal()
    q = db.query(GtmLinkedInAccount).filter(GtmLinkedInAccount.linkedin_session_status == "active")
    if email:
        q = q.filter(GtmLinkedInAccount.linkedin_email == email)
    acct = q.order_by(GtmLinkedInAccount.id.desc()).first()
    if acct is None:
        print("No active account found (capture a session first).")
        db.close()
        return
    s = db.query(GtmLinkedInAccountSettings).filter_by(linkedin_account_id=acct.id).first()
    prof = db.query(GtmLinkedInBrowserProfile).filter_by(linkedin_account_id=acct.id).first()
    cookies = auth.load_cookies(db, acct)
    print(f"Testing connect on {url} with account {acct.id} ({acct.linkedin_email})…")
    with browser.browser_session(cookies, prof) as page:
        if not browser.session_is_valid(page):
            print("  session invalid — recapture with --capture")
            db.close()
            return
        res = browser.do_connect(page, url, "Hi — would love to connect.", s)
        print("  result:", res)
        try:
            input("  Inspect the open browser/dialog, then press Enter to close… ")
        except EOFError:
            pass
    db.close()


def test_inmail():
    """Run do_send_inmail against a REAL profile locally (visible browser) using an
    active account's session — to validate the InMail composer (subject/body/send)
    on a Premium account. Needs InMail credits.

        python scripts/li_test_drive.py --test-inmail <profile_url> [account_email]
    """
    url = next((a for a in sys.argv[1:] if "linkedin.com/in/" in a or a.startswith("http")), None)
    email = next((a.strip().lower() for a in sys.argv[1:] if "@" in a), None)
    if not url:
        print("usage: python scripts/li_test_drive.py --test-inmail <profile_url> [account_email]")
        return
    db = SessionLocal()
    q = db.query(GtmLinkedInAccount).filter(GtmLinkedInAccount.linkedin_session_status == "active")
    if email:
        q = q.filter(GtmLinkedInAccount.linkedin_email == email)
    acct = q.order_by(GtmLinkedInAccount.id.desc()).first()
    if acct is None:
        print("No active account found (capture a session first).")
        db.close()
        return
    s = db.query(GtmLinkedInAccountSettings).filter_by(linkedin_account_id=acct.id).first()
    prof = db.query(GtmLinkedInBrowserProfile).filter_by(linkedin_account_id=acct.id).first()
    cookies = auth.load_cookies(db, acct)
    subject = "Quick question about your marketing measurement"
    body = ("Hi — I came across your profile and the work your team is doing. We help "
            "marketing teams measure which channels actually drive results. Open to a quick look?")
    print(f"Testing InMail on {url} with account {acct.id} ({acct.linkedin_email})…")
    with browser.browser_session(cookies, prof) as page:
        if not browser.session_is_valid(page):
            print("  session invalid — recapture with --capture")
            db.close()
            return
        res = browser.do_send_inmail(page, url, subject, body, s)
        print("  result:", res)
        try:
            input("  Inspect the open browser/composer, then press Enter to close… ")
        except EOFError:
            pass
    db.close()


def capture():
    """Capture a LinkedIn session by logging in LOCALLY (your trusted/home IP —
    no bot-CAPTCHA) and storing the cookies on the account, so the CLOUD worker
    REUSES the session and never does the password login (which gets bot-blocked
    from the datacenter IP). No SQS/job needed — you type the password here.

        python scripts/li_test_drive.py --capture <email>

    The account must already exist (connect it once in the UI so it's mapped to
    the brand). Tip: set GTM_LINKEDIN_HEADLESS=false in .env to watch the browser.
    """
    import getpass

    email = next((a.strip().lower() for a in sys.argv[1:] if "@" in a), None)
    if not email:
        print("usage: python scripts/li_test_drive.py --capture <email>")
        return
    db = SessionLocal()
    acct = (
        db.query(GtmLinkedInAccount)
        .filter(GtmLinkedInAccount.linkedin_email == email)
        .order_by(GtmLinkedInAccount.id.desc())
        .first()
    )
    if acct is None:
        print(f"No account for {email}. Connect it once in the UI first (maps it to the brand).")
        db.close()
        return
    password = getpass.getpass(f"LinkedIn password for {email}: ").strip()
    if not password:
        print("No password entered — aborting.")
        db.close()
        return
    prof = db.query(GtmLinkedInBrowserProfile).filter_by(linkedin_account_id=acct.id).first()
    print(f"Logging in LOCALLY for {email} (account {acct.id})… (GTM_LINKEDIN_HEADLESS=false to watch)")
    res = None
    with browser.browser_session(None, prof) as page:
        res = browser.do_login(page, email, password)
        if res.get("status") == "needs_code":
            code = input("  LinkedIn asked for a code — enter it (or press Enter to skip): ").strip()
            if code:
                res = browser.submit_challenge(page, code)
    if res and res.get("status") == "active" and res.get("cookies"):
        auth.store_session(db, account_id=acct.id, cookies_json=res["cookies"])
        db.refresh(acct)
        print(f"  ✅ session captured + stored. account status: {acct.linkedin_session_status}")
        print("  The CLOUD worker will now REUSE this session for outreach (no cloud login).")
    else:
        print("  ❌ login did not reach 'active':", res)
    db.close()


def run_login():
    """Test the AUTOMATED (headless) email+password login — runs the latest
    pending `linkedin_login` job (created when you click 'Connect LinkedIn' in the
    UI) through the worker, exactly as the cloud worker would. Watch the browser
    to see whether LinkedIn lets it through, asks for 2FA, or shows a captcha.
    Target a specific account:  python scripts/li_test_drive.py --run-login <email>
    Otherwise it runs the LATEST pending login job.
    Set GTM_LINKEDIN_HEADLESS=false in .env to see it."""
    db = SessionLocal()
    # Optional email arg → run THAT account's latest pending login job.
    target_email = next((a.strip().lower() for a in sys.argv[1:] if "@" in a), None)
    q = db.query(GtmLinkedInJob).filter_by(job_type="linkedin_login", status="pending")
    if target_email:
        acct0 = (
            db.query(GtmLinkedInAccount)
            .filter(GtmLinkedInAccount.linkedin_email == target_email)
            .order_by(GtmLinkedInAccount.id.desc())
            .first()
        )
        if acct0 is None:
            print(f"No account found for {target_email}. Click 'Connect LinkedIn' for it in the UI first.")
            db.close()
            return
        q = q.filter(GtmLinkedInJob.linkedin_account_id == acct0.id)
    job = q.order_by(GtmLinkedInJob.id.desc()).first()
    if job is None:
        who = f" for {target_email}" if target_email else ""
        print(f"No pending linkedin_login job{who}. Click 'Connect LinkedIn' in the UI first (enter email+password).")
        db.close()
        return
    acct = db.query(GtmLinkedInAccount).get(job.linkedin_account_id)
    print(f"Running HEADLESS login for {acct.linkedin_email} (job {job.id})… watch the browser.")
    print("  result:", worker.handle_job(db, job.id))
    db.refresh(acct)
    print(f"  → account status: {acct.linkedin_session_status} | has cookies: {bool(acct.encrypted_cookies)}")
    if acct.linkedin_session_status == "challenge":
        code = input("  LinkedIn asked for a code — enter it: ").strip()
        cj = auth.submit_challenge_code(db, workspace_id=acct.workspace_id, account_id=acct.id, code=code)
        print("  challenge result:", worker.handle_job(db, cj.id))
        db.refresh(acct)
        print(f"  → account status: {acct.linkedin_session_status} | has cookies: {bool(acct.encrypted_cookies)}")
    if acct.linkedin_session_status == "active":
        print("  ✅ Headless login WORKED — session captured.")
    else:
        print("  ❌ Headless login did NOT reach 'active'. Check li_debug_login_*.png for what LinkedIn showed.")
    db.close()


def _ensure_profile_columns(db):
    """Idempotently add the Phase-1 profile columns so the local test works even
    if the app-cold-start migration hasn't run against this DB yet."""
    for stmt in (
        "ALTER TABLE gtm_linkedin_accounts ADD COLUMN IF NOT EXISTS profile_s3_key VARCHAR(256)",
        "ALTER TABLE gtm_linkedin_accounts ADD COLUMN IF NOT EXISTS profile_updated_at TIMESTAMPTZ",
    ):
        db.execute(text(stmt))
    db.commit()


def capture_profile():
    """Phase 1 capture: log in manually in a VISIBLE persistent-context browser;
    on exit the FULL profile (cookies + localStorage + IndexedDB) is saved to S3
    (or a local fallback if S3 isn't configured) and the account is flagged to
    REUSE it. No cookie seeding — the worker restores this profile already
    logged in.

        python scripts/li_test_drive.py --capture-profile <email>
    Set GTM_LINKEDIN_HEADLESS=false in .env to watch the browser.
    """
    from datetime import datetime, timezone
    from gtm.linkedin import profile_store

    email = next((a.strip().lower() for a in sys.argv[1:] if "@" in a), None)
    if not email:
        print("usage: python scripts/li_test_drive.py --capture-profile <email>")
        return
    db = SessionLocal()
    _ensure_profile_columns(db)
    acct = _get_or_create_account(db, email)
    prof = db.query(GtmLinkedInBrowserProfile).filter_by(linkedin_account_id=acct.id).first()
    print(f"[capture-profile] Opening a persistent browser for {email} (account {acct.id})…")
    logged_in = False
    with browser.browser_session_persistent(acct.id, prof, headless=False) as page:
        page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=60000)
        print("   >>> Log in to LinkedIn MANUALLY (finish any captcha / 2FA).")
        input("   >>> Press Enter once your FEED is loaded… ")
        logged_in = browser.session_is_valid(page)
    # On context exit, browser_session_persistent saved the profile (S3 / local).
    if logged_in:
        acct.profile_s3_key = profile_store.s3_key(acct.id)
        acct.profile_updated_at = datetime.now(timezone.utc)
        acct.linkedin_session_status = "active"
        acct.linkedin_reauth_required = False
        db.commit()
        print(f"   ✅ profile saved + account flagged to reuse it. key={acct.profile_s3_key}")
        print(f"   Next, verify it round-trips:  python scripts/li_test_drive.py --drive-profile {email}")
    else:
        print("   ❌ not logged in — re-run and finish the login in the browser.")
    db.close()


def drive_profile():
    """Phase 1 verification: RESTORE the account's saved profile (from S3 / local),
    open the browser, and confirm we're ALREADY LOGGED IN — no cookies, no
    re-login. Proves the whole persistent-profile flow end-to-end. Optionally runs
    do_connect if a profile URL is passed.

        python scripts/li_test_drive.py --drive-profile [email] [profile_url]
    """
    email = next((a.strip().lower() for a in sys.argv[1:] if "@" in a), None)
    url = next((a for a in sys.argv[1:] if "linkedin.com/in/" in a or a.startswith("http")), None)
    db = SessionLocal()
    _ensure_profile_columns(db)
    q = db.query(GtmLinkedInAccount).filter(GtmLinkedInAccount.profile_s3_key.isnot(None))
    if email:
        q = q.filter(GtmLinkedInAccount.linkedin_email == email)
    acct = q.order_by(GtmLinkedInAccount.id.desc()).first()
    if acct is None:
        print("No account with a saved profile. Run --capture-profile <email> first.")
        db.close()
        return
    prof = db.query(GtmLinkedInBrowserProfile).filter_by(linkedin_account_id=acct.id).first()
    print(f"[drive-profile] Restoring profile for {acct.linkedin_email} (account {acct.id})…")
    with browser.browser_session_persistent(acct.id, prof, headless=False) as page:
        valid = browser.session_is_valid(page)
        print("   >>> ALREADY LOGGED IN from restored profile:", valid)
        if valid and url:
            s = db.query(GtmLinkedInAccountSettings).filter_by(linkedin_account_id=acct.id).first()
            print("   running do_connect on", url)
            print("   result:", browser.do_connect(page, url, "Hi — would love to connect.", s))
        try:
            input("   Inspect the browser, then press Enter to close… ")
        except EOFError:
            pass
    db.close()


def _ensure_login_session_table(db):
    """Idempotently create the Phase-2 login-session table for local testing."""
    db.execute(text(
        """
        CREATE TABLE IF NOT EXISTS gtm_linkedin_login_sessions (
          id BIGSERIAL PRIMARY KEY,
          workspace_id BIGINT NOT NULL,
          linkedin_account_id BIGINT NOT NULL REFERENCES gtm_linkedin_accounts(id) ON DELETE CASCADE,
          status VARCHAR(24) DEFAULT 'starting',
          viewer_url TEXT,
          detail TEXT,
          created_at TIMESTAMPTZ DEFAULT now(),
          updated_at TIMESTAMPTZ DEFAULT now(),
          expires_at TIMESTAMPTZ
        )
        """
    ))
    db.commit()


def cloud_login_cmd():
    """Phase 2 core: run one interactive cloud-login LOCALLY (headful) — exactly
    what the Fargate host will run, minus the live-view wrapper. A browser opens;
    log in to LinkedIn; on success the full profile is saved and the account is
    marked active. Proves the login→capture→reuse flow.

        python scripts/li_test_drive.py --cloud-login <email>
    Set GTM_LINKEDIN_HEADLESS=false in .env to watch the browser.
    """
    from gtm.linkedin import cloud_login

    email = next((a.strip().lower() for a in sys.argv[1:] if "@" in a), None)
    if not email:
        print("usage: python scripts/li_test_drive.py --cloud-login <email>")
        return
    db = SessionLocal()
    _ensure_profile_columns(db)
    _ensure_login_session_table(db)
    acct = _get_or_create_account(db, email)
    sess = cloud_login.create_login_session(db, workspace_id=WS, account_id=acct.id)
    print(f"[cloud-login] session {sess.id} — a browser will open; log in to LinkedIn there "
          f"(finish any captcha / 2FA). Waiting up to 10 min…")
    res = cloud_login.run_login_session(
        db, account_id=acct.id, session_id=sess.id, headless=False, timeout_s=600, poll_s=3
    )
    print("   result:", res)
    db.refresh(acct)
    print("   account status:", acct.linkedin_session_status, "| profile_s3_key:", acct.profile_s3_key)
    db.close()


def run_worker():
    """Local stand-in for the cloud SQS worker: loops forever, dispatching due
    leads (scheduler.tick) and running EVERY pending job through worker.handle_job
    against live LinkedIn. Use this to test the DEPLOYED app end-to-end — Connect /
    Enroll on the deployed UI write jobs to the shared DB; this loop executes them.

        python scripts/li_test_drive.py --run-worker

    Ctrl+C to stop. Set GTM_LINKEDIN_HEADLESS=false in .env to watch the browser.
    NOTE: cadence gaps are real (acceptance check is +2 days) — to fast-forward a
    specific lead in testing, set its gtm_linkedin_lead_state.next_action_at = now()."""
    import time
    from gtm.linkedin import scheduler

    db = SessionLocal()
    print("Local GTM LinkedIn worker loop — Ctrl+C to stop.")
    print("  (dispatches due leads + runs pending jobs against live LinkedIn)")
    try:
        while True:
            try:
                t = scheduler.tick(db)
                if t.get("enqueued") or t.get("auto_paused") or t.get("capped"):
                    print(f"  tick: {t}")
            except Exception as e:  # noqa: BLE001
                print("  tick error:", e)
                db.rollback()
            jobs = (
                db.query(GtmLinkedInJob)
                .filter(GtmLinkedInJob.status == "pending")
                .order_by(GtmLinkedInJob.id.asc())
                .all()
            )
            for j in jobs:
                jid, jtype = j.id, j.job_type
                try:
                    res = worker.handle_job(db, jid)
                    print(f"  job {jid} [{jtype}] -> {res}")
                except Exception as e:  # noqa: BLE001
                    print(f"  job {jid} [{jtype}] FAILED (will retry): {e}")
                    db.rollback()
            time.sleep(15)
    except KeyboardInterrupt:
        print("\n  stopped.")
    finally:
        db.close()


if __name__ == "__main__":
    if "--cleanup" in sys.argv:
        cleanup()
    elif "--step" in sys.argv:
        step()
    elif "--cloud-login" in sys.argv:
        cloud_login_cmd()
    elif "--capture-profile" in sys.argv:
        capture_profile()
    elif "--drive-profile" in sys.argv:
        drive_profile()
    elif "--capture" in sys.argv:
        capture()
    elif "--test-connect" in sys.argv:
        test_connect()
    elif "--test-inmail" in sys.argv:
        test_inmail()
    elif "--run-login" in sys.argv:
        run_login()
    elif "--run-worker" in sys.argv:
        run_worker()
    else:
        main()
