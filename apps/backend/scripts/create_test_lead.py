"""Create ONE test lead + lead_sequence for end-to-end outreach testing.

Hard-coded for Govardhan's test:
    Email:    govardhan.y@neuzenai.com
    Name:     Govardhan Reddy
    Company:  NeuzenAI
    Campaign: Spenzo AI (campaign_id=1, workspace_id=5, sequence_id=11)

Behavior:
  1. Inspect current state of any existing lead with this email (idempotent).
  2. Insert nexus_global_leads if not present, else reuse the existing row.
  3. Insert nexus_lead_sequences with status='active' and next_action_at=NOW(),
     ONLY if no live row already exists for this lead+campaign+sequence.
  4. Print the IDs so the scheduler tick can be watched.

All writes wrapped in a single transaction.

Run:
    cd apps/backend
    source venv/Scripts/activate
    PYTHONIOENCODING=utf-8 python scripts/create_test_lead.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


# ---- Test config (edit here if needed) -------------------------------------
TEST_EMAIL        = "govardhan.y@neuzenai.com"
TEST_FIRST_NAME   = "Govardhan"
TEST_LAST_NAME    = "Reddy"
TEST_COMPANY      = "NeuzenAI"
TEST_ROLE         = "Founder"
WORKSPACE_ID      = 5
CAMPAIGN_ID       = 1     # Spenzo AI
SEQUENCE_ID       = 11    # 4-step Spenzo AI sequence
SOURCE_TAG        = "manual_test_outreach_agent"


def main() -> int:
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 1
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(db_url)

    print(f"Connected to: {engine.url.host}/{engine.url.database}\n")

    # ---- Phase 1: inspect existing state (read-only) ----
    with engine.connect() as conn:
        existing_lead = conn.execute(
            text(
                """SELECT id, email, status, created_at
                   FROM nexus_global_leads
                   WHERE LOWER(email) = LOWER(:e)
                   LIMIT 1"""
            ),
            {"e": TEST_EMAIL},
        ).first()

        if existing_lead:
            print(f"Found existing lead: id={existing_lead[0]}  status={existing_lead[2]}  created={existing_lead[3]}")
        else:
            print(f"No existing lead for {TEST_EMAIL} — will create new.")

        if existing_lead:
            live_ls = conn.execute(
                text(
                    """SELECT id, status, current_step, next_action_at
                       FROM nexus_lead_sequences
                       WHERE lead_id = :lid
                         AND campaign_id = :cid
                         AND sequence_id = :sid
                         AND status IN ('active', 'apollo_delegated')
                       ORDER BY id DESC LIMIT 1"""
                ),
                {"lid": existing_lead[0], "cid": CAMPAIGN_ID, "sid": SEQUENCE_ID},
            ).first()
            if live_ls:
                print(
                    f"\nWARN: lead {existing_lead[0]} already has a LIVE sequence row "
                    f"id={live_ls[0]} status={live_ls[1]} step={live_ls[2]} next_action={live_ls[3]}."
                )
                print("If you want a fresh test, pause/delete that row first. Aborting to avoid double-enrollment.")
                return 1

    # ---- Phase 2: transactional upsert + enroll ----
    with engine.begin() as conn:
        print("\n[1/3] Upserting lead in nexus_global_leads ...")
        if existing_lead:
            lead_id = existing_lead[0]
            # Refresh status so it's eligible — re-set to 'new' wipes any prior
            # contacted/replied state from a previous test.
            conn.execute(
                text(
                    """UPDATE nexus_global_leads
                          SET status = 'new',
                              first_name = :fn, last_name = :ln,
                              name = :nm, company = :co, company_name = :co,
                              role = :rl, job_title = :rl,
                              email_verified = true, email_verify_score = 100,
                              source = :src,
                              updated_at = NOW(),
                              last_contacted_at = NULL,
                              attempt_count_total = 0,
                              total_emails_sent = 0
                        WHERE id = :id"""
                ),
                {
                    "id": lead_id,
                    "fn": TEST_FIRST_NAME, "ln": TEST_LAST_NAME,
                    "nm": f"{TEST_FIRST_NAME} {TEST_LAST_NAME}",
                    "co": TEST_COMPANY, "rl": TEST_ROLE,
                    "src": SOURCE_TAG,
                },
            )
            print(f"      reused lead id={lead_id} (reset to status='new')")
        else:
            lead_id = conn.execute(
                text(
                    """INSERT INTO nexus_global_leads (
                           email, first_name, last_name, name,
                           company, company_name, role, job_title,
                           status, email_verified, email_verify_score,
                           source, priority, priority_state,
                           created_at, updated_at
                       ) VALUES (
                           :e, :fn, :ln, :nm,
                           :co, :co, :rl, :rl,
                           'new', true, 100,
                           :src, 'active', 'active',
                           NOW(), NOW()
                       )
                       RETURNING id"""
                ),
                {
                    "e": TEST_EMAIL, "fn": TEST_FIRST_NAME, "ln": TEST_LAST_NAME,
                    "nm": f"{TEST_FIRST_NAME} {TEST_LAST_NAME}",
                    "co": TEST_COMPANY, "rl": TEST_ROLE, "src": SOURCE_TAG,
                },
            ).scalar()
            print(f"      inserted new lead id={lead_id}")

        print("[2/3] Creating nexus_lead_sequences row (status=active, next_action_at=NOW) ...")
        ls_id = conn.execute(
            text(
                """INSERT INTO nexus_lead_sequences (
                       workspace_id, campaign_id, lead_id, sequence_id,
                       current_step, next_action_at, status,
                       enrolled_at, updated_at
                   ) VALUES (
                       :ws, :cid, :lid, :sid,
                       0, NOW(), 'active',
                       NOW(), NOW()
                   )
                   RETURNING id"""
            ),
            {"ws": WORKSPACE_ID, "cid": CAMPAIGN_ID, "lid": lead_id, "sid": SEQUENCE_ID},
        ).scalar()
        print(f"      created lead_sequence id={ls_id}")

        print("[3/3] Committing ...")

    # ---- Phase 3: verify ----
    with engine.connect() as conn:
        print("\nVerification:")
        row = conn.execute(
            text(
                """SELECT ls.id, ls.lead_id, ls.campaign_id, ls.sequence_id,
                          ls.status, ls.current_step, ls.next_action_at,
                          gl.email, gl.first_name, gl.company_name, gl.status AS lead_status
                   FROM nexus_lead_sequences ls
                   JOIN nexus_global_leads gl ON gl.id = ls.lead_id
                   WHERE ls.lead_id = :lid AND ls.campaign_id = :cid
                   ORDER BY ls.id DESC LIMIT 1"""
            ),
            {"lid": lead_id, "cid": CAMPAIGN_ID},
        ).first()
        for k, v in zip(
            ["ls_id", "lead_id", "campaign_id", "sequence_id", "ls_status",
             "current_step", "next_action_at", "email", "first_name",
             "company_name", "lead_status"],
            row,
        ):
            print(f"  {k:<16s}  {v}")

    print("\nDone.")
    print("- Lambda scheduler ticks every minute; next tick should claim this row.")
    print("- Within ~2 min, apollo_sequencer will enroll the contact in Apollo (NEXUS PRODUCT OUTREACH).")
    print("- Email should arrive in govardhan.y@neuzenai.com within ~5 min total.")
    print(f"\nIf you ever need to delete this test enrollment:")
    print(f"  DELETE FROM nexus_lead_sequences WHERE id = {ls_id};")
    return 0


if __name__ == "__main__":
    sys.exit(main())
