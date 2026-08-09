"""Enroll a campaign's leads into the LinkedIn sequence (there's no UI yet).

Talks straight to the DB via scheduler.enroll_campaign — no HTTP/auth needed.
The account must already be CONNECTED to the campaign's brand (Connectors page),
or enroll_campaign refuses ("account is not mapped to this campaign's brand").

After enrolling, the DEPLOYED scheduler tick (pinger, every minute) picks the
leads up automatically → SQS → worker sends. You only run this ONCE per campaign.

Run from apps/backend:
    python scripts/li_enroll.py <campaign_id> <account_email_or_id> [combined|standard|hybrid]

Examples:
    python scripts/li_enroll.py 82 yerasi823@gmail.com
    python scripts/li_enroll.py 82 20 combined
"""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_BACKEND, ".env"))

from core.database import SessionLocal  # noqa: E402
from gtm.linkedin import scheduler  # noqa: E402
from gtm.linkedin.models import GtmLinkedInAccount  # noqa: E402


def main():
    if len(sys.argv) < 3:
        print("usage: python scripts/li_enroll.py <campaign_id> <account_email_or_id> [combined|standard|hybrid]")
        return
    campaign_id = int(sys.argv[1])
    acct_arg = sys.argv[2].strip()
    mode = sys.argv[3] if len(sys.argv) > 3 else "combined"

    db = SessionLocal()
    if acct_arg.isdigit():
        acct = db.query(GtmLinkedInAccount).get(int(acct_arg))
    else:
        acct = (
            db.query(GtmLinkedInAccount)
            .filter(GtmLinkedInAccount.linkedin_email == acct_arg.lower())
            .order_by(GtmLinkedInAccount.id.desc())
            .first()
        )
    if acct is None:
        print(f"No LinkedIn account found for '{acct_arg}'. Connect it first on the Connectors page.")
        db.close()
        return
    if acct.linkedin_session_status != "active":
        print(f"⚠️  account {acct.id} ({acct.linkedin_email}) status is '{acct.linkedin_session_status}', not 'active'. "
              "Enroll still works, but nothing sends until the session is active.")

    print(f"Enrolling campaign {campaign_id} on account {acct.id} ({acct.linkedin_email}), mode={mode}…")
    try:
        out = scheduler.enroll_campaign(
            db, workspace_id=acct.workspace_id, campaign_id=campaign_id,
            account_id=acct.id, workflow_mode=mode,
        )
        print("  result:", out)
        print("  → the deployed scheduler tick will start sending these within ~1 minute.")
    except ValueError as e:
        print("  ENROLL REFUSED:", e)
    db.close()


if __name__ == "__main__":
    main()
