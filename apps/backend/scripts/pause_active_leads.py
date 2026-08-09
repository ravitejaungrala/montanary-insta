"""Pause all currently-active nexus_lead_sequences rows for testing.

Safe one-off:
  1. SELECT the IDs (read-only) — prints them so they can be saved.
  2. UPDATE status='active' -> 'paused' in a transaction.
  3. VERIFY count=0 active. If not, ROLLBACK.
  4. COMMIT.

Run:
    cd apps/backend
    source venv/Scripts/activate
    PYTHONIOENCODING=utf-8 python scripts/pause_active_leads.py

To resume later, copy the printed IDs into:
    UPDATE nexus_lead_sequences SET status='active' WHERE id IN (...);
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


def main() -> int:
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 1
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

    engine = create_engine(db_url)

    # ---- Phase 1: read-only inspection ----
    with engine.connect() as conn:
        print(f"Connected to: {engine.url.host}/{engine.url.database}\n")

        rows = conn.execute(
            text(
                """SELECT id, lead_id, campaign_id, current_step, next_action_at
                   FROM nexus_lead_sequences
                   WHERE status = 'active'
                   ORDER BY id"""
            )
        ).fetchall()

        if not rows:
            print("No active rows. Nothing to pause.")
            return 0

        print(f"Found {len(rows)} active row(s):\n")
        ids = []
        for r in rows:
            rid, lid, cid, step, naa = r
            ids.append(rid)
            print(f"  id={rid:>5}  lead_id={lid:>5}  campaign_id={cid}  step={step}  next_action_at={naa}")

        print(f"\nIDs to resume later (save this list):")
        print(f"  {','.join(str(i) for i in ids)}")

    # ---- Phase 2: transactional UPDATE ----
    with engine.begin() as conn:
        print("\n[1/3] Running UPDATE ...")
        result = conn.execute(
            text(
                """UPDATE nexus_lead_sequences
                      SET status = 'paused'
                    WHERE status = 'active'"""
            )
        )
        print(f"      affected {result.rowcount} row(s)")

        print("[2/3] Verifying ...")
        remaining = conn.execute(
            text("SELECT COUNT(*) FROM nexus_lead_sequences WHERE status = 'active'")
        ).scalar()
        if remaining != 0:
            raise RuntimeError(f"verify failed: {remaining} active row(s) remain")
        print(f"      active count = 0 (expected)")

        print("[3/3] Committing ...")

    # ---- Phase 3: post-commit confirmation ----
    with engine.connect() as conn:
        summary = conn.execute(
            text(
                """SELECT status, COUNT(*)
                   FROM nexus_lead_sequences
                   GROUP BY status
                   ORDER BY status"""
            )
        ).fetchall()
        print("\nAFTER status distribution:")
        for status, cnt in summary:
            print(f"  {status:<20s} {cnt}")

    print("\nDone. To resume, run this SQL when testing is over:")
    print(f"  UPDATE nexus_lead_sequences SET status='active' WHERE id IN ({','.join(str(i) for i in ids)});")
    return 0


if __name__ == "__main__":
    sys.exit(main())
