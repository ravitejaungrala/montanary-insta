"""One-off diagnostic for the duplicate-Initial-Email bug.

Run from apps/backend with the venv active:
    python scripts/diag_duplicate_initial_emails.py

Reports:
  A) How many global_leads have more than one nexus_lead_sequences row.
  B) How many step-0 email touchpoints exist per affected lead, with the
     enclosing lead_sequence_id + campaign_id + sent_at, so we can tell
     whether the dups are:
       - one-time legacy backfill (all from the same incident date), or
       - actively recurring (fresh sends within last 24h), or
       - cross-campaign (different campaign_ids per send).
  C) The pool of leads currently due (next_action_at <= NOW) at step 0
     in MORE THAN ONE lead_sequence — these are the ones the next
     sequencer tick will double-send if we don't fix the guard.

Read-only. No writes, no commits.
"""
from __future__ import annotations

import os
import sys

# Make `core`, `nexus` etc. importable when this script is run from
# apps/backend.
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(HERE)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from core.database import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402


WORKSPACE_ID = int(os.environ.get("DIAG_WORKSPACE_ID", "5"))


def main() -> None:
    db = SessionLocal()
    try:
        print(f"=== workspace_id = {WORKSPACE_ID} ===\n")

        # ── A. Leads with multiple lead_sequences ────────────────────────
        print("A. Leads with >1 nexus_lead_sequences row")
        rows = db.execute(
            text(
                """
                SELECT ls.lead_id,
                       COUNT(*) AS n_seq,
                       ARRAY_AGG(DISTINCT ls.campaign_id ORDER BY ls.campaign_id) AS campaigns,
                       ARRAY_AGG(DISTINCT ls.sequence_id ORDER BY ls.sequence_id) AS sequences,
                       ARRAY_AGG(DISTINCT ls.status) AS statuses,
                       ARRAY_AGG(DISTINCT ls.current_step) AS steps
                  FROM nexus_lead_sequences ls
                 WHERE ls.workspace_id = :w
                 GROUP BY ls.lead_id
                HAVING COUNT(*) > 1
                 ORDER BY n_seq DESC, ls.lead_id
                 LIMIT 50
                """
            ),
            {"w": WORKSPACE_ID},
        ).fetchall()
        print(f"   total affected leads: {len(rows)}")
        for r in rows[:20]:
            print(
                f"   lead={r[0]:>5}  n={r[1]}  "
                f"campaigns={r[2]}  sequences={r[3]}  "
                f"statuses={r[4]}  steps={r[5]}"
            )

        # ── B. Step-0 sends per lead (the smoking gun) ───────────────────
        print()
        print("B. Step-0 email touchpoints per lead (only leads with >1)")
        rows2 = db.execute(
            text(
                """
                SELECT tp.lead_id,
                       COUNT(*) AS n_step0_sends,
                       ARRAY_AGG(DISTINCT tp.lead_sequence_id ORDER BY tp.lead_sequence_id) AS ls_ids,
                       ARRAY_AGG(DISTINCT tp.campaign_id ORDER BY tp.campaign_id) AS campaigns,
                       MIN(tp.sent_at) AS first_sent,
                       MAX(tp.sent_at) AS last_sent
                  FROM nexus_touchpoints tp
                 WHERE tp.workspace_id = :w
                   AND tp.step = 0
                   AND tp.channel = 'email'
                   AND tp.status = 'sent'
                 GROUP BY tp.lead_id
                HAVING COUNT(*) > 1
                 ORDER BY n_step0_sends DESC
                 LIMIT 50
                """
            ),
            {"w": WORKSPACE_ID},
        ).fetchall()
        print(f"   total affected leads: {len(rows2)}")
        for r in rows2[:20]:
            print(
                f"   lead={r[0]:>5}  n={r[1]}  "
                f"ls_ids={r[2]}  campaigns={r[3]}  "
                f"first={r[4]}  last={r[5]}"
            )

        # ── C. Leads currently DUE to fire AGAIN at step 0 ───────────────
        print()
        print("C. Leads that next tick will double-send (DUE at step 0 in >1 sequence)")
        rows3 = db.execute(
            text(
                """
                WITH due AS (
                    SELECT ls.lead_id, ls.id AS ls_id, ls.campaign_id,
                           ls.sequence_id, ls.next_action_at
                      FROM nexus_lead_sequences ls
                     WHERE ls.workspace_id = :w
                       AND ls.status IN ('active','processing')
                       AND ls.current_step = 0
                       AND ls.next_action_at IS NOT NULL
                       AND ls.next_action_at <= NOW()
                )
                SELECT lead_id,
                       COUNT(*) AS n_due,
                       ARRAY_AGG(ls_id ORDER BY ls_id) AS ls_ids,
                       ARRAY_AGG(campaign_id ORDER BY ls_id) AS campaigns,
                       MIN(next_action_at) AS earliest_due
                  FROM due
                 GROUP BY lead_id
                HAVING COUNT(*) > 1
                 ORDER BY n_due DESC
                """
            ),
            {"w": WORKSPACE_ID},
        ).fetchall()
        print(f"   total leads exposed: {len(rows3)}")
        for r in rows3[:20]:
            print(
                f"   lead={r[0]:>5}  due_n={r[1]}  ls_ids={r[2]}  "
                f"campaigns={r[3]}  earliest_due={r[4]}"
            )

        # ── D. Sanity: campaigns + products on a sample affected lead ────
        if rows2:
            sample_lead = rows2[0][0]
            print()
            print(f"D. Sample lead {sample_lead}: which campaigns/products own its sequences?")
            rows4 = db.execute(
                text(
                    """
                    SELECT ls.id AS ls_id, ls.campaign_id, ls.sequence_id,
                           ls.status, ls.current_step, ls.next_action_at,
                           c.name AS campaign_name, p.id AS product_id,
                           p.name AS product_name, p.source_url
                      FROM nexus_lead_sequences ls
                      JOIN nexus_campaigns c ON c.id = ls.campaign_id
                      LEFT JOIN nexus_products p ON p.id = c.product_id
                     WHERE ls.workspace_id = :w
                       AND ls.lead_id = :lid
                     ORDER BY ls.id
                    """
                ),
                {"w": WORKSPACE_ID, "lid": sample_lead},
            ).fetchall()
            for r in rows4:
                print(
                    f"   ls={r[0]}  camp={r[1]} ({r[6]!r})  seq={r[2]}  "
                    f"status={r[3]}  step={r[4]}  next={r[5]}  "
                    f"product={r[7]} ({r[8]!r}) url={r[9]!r}"
                )
    finally:
        db.close()


if __name__ == "__main__":
    main()
