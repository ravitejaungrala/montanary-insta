"""Test the NEW browser-session capture path locally BEFORE deploying.

This exercises exactly what `POST /nexus/linkedin-agent/session` does
(auth.attach_session → normalize_cookies → store_session), then opens a local
browser with the stored session to confirm it's valid — the same thing the
worker does in the cloud.

HOW TO USE
  1. Log into linkedin.com in your browser.
  2. Export your linkedin.com cookies as JSON with a cookie-export extension
     (e.g. "Cookie-Editor" → Export → JSON, or "EditThisCookie"). Save to a file.
  3. From apps/backend, run:
       python scripts/li_attach_test.py <cookies.json> <linkedin_email> [workspace_id]

     Example:
       python scripts/li_attach_test.py C:/tmp/li_cookies.json contact@neuzenai.com

NOTE: using an email that already exists (e.g. contact@neuzenai.com = account 22)
re-attaches that account via the new path — safe, just refreshes its cookies.
"""
import json
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_BACKEND, ".env"))

from core.database import SessionLocal  # noqa: E402
from gtm.linkedin import auth, browser  # noqa: E402
from gtm.linkedin.models import GtmLinkedInAccount, GtmLinkedInBrowserProfile  # noqa: E402


def main():
    if len(sys.argv) < 3:
        print("usage: python scripts/li_attach_test.py <cookies.json> <linkedin_email> [workspace_id]")
        return
    cookies_path, email = sys.argv[1], sys.argv[2].strip().lower()
    ws = int(sys.argv[3]) if len(sys.argv) > 3 else None

    try:
        with open(cookies_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"Could not read cookies file: {e}")
        return
    print(f"Loaded {len(raw) if isinstance(raw, list) else '?'} cookies from {cookies_path}")

    db = SessionLocal()
    if ws is None:
        acct0 = db.query(GtmLinkedInAccount).order_by(GtmLinkedInAccount.id.desc()).first()
        ws = acct0.workspace_id if acct0 else None
    if ws is None:
        print("No workspace found — pass workspace_id as the 3rd argument.")
        db.close()
        return

    # 1) The exact backend path the endpoint runs.
    try:
        acct = auth.attach_session(
            db, workspace_id=ws, user_id=None, linkedin_email=email, cookies=raw,
        )
        print(f"✅ attach_session OK → account {acct.id}, status={acct.linkedin_session_status}")
    except Exception as e:  # noqa: BLE001
        print(f"❌ attach_session FAILED: {e}")
        db.close()
        return

    # 2) Prove the stored session is actually valid (what the worker checks).
    prof = db.query(GtmLinkedInBrowserProfile).filter_by(linkedin_account_id=acct.id).first()
    cookies_json = auth.load_cookies(db, acct)
    print("Opening a local browser with the stored session…")
    with browser.browser_session(cookies_json, prof) as page:
        ok = browser.session_is_valid(page)
        print(f"   session_is_valid: {ok}  ({'logged in ✅' if ok else 'NOT logged in ❌'})")
        try:
            input("   Inspect the browser, then press Enter to close… ")
        except EOFError:
            pass
    db.close()


if __name__ == "__main__":
    main()
