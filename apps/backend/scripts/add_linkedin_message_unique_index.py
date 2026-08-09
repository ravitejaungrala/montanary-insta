"""add_linkedin_message_unique_index.py — install a partial UNIQUE
INDEX on nexus_linkedin_messages that prevents duplicate outbound
rows per (workspace_id, lead_id, variant).

WHY THIS EXISTS
---------------
The sequencer's SELECT-then-INSERT dedup is race-prone. We're already
catching IntegrityError on the application side; this index is the
DB-level backstop that makes the race fail loudly and atomically.

We do NOT add this to the Phase 5 auto-run _DDL block. Reason: if
duplicates remain in the table at startup, CREATE UNIQUE INDEX fails
and the whole app refuses to boot. This is a one-shot, manually-run
migration. Run cleanup_duplicate_linkedin_messages.py with --apply
FIRST, then run this.

INDEX SHAPE
-----------
    CREATE UNIQUE INDEX uq_li_msg_outbound_one_per_variant
        ON nexus_linkedin_messages (workspace_id, lead_id,
                                    COALESCE(NULLIF(variant, ''), 'dm'))
        WHERE direction = 'outbound';

- Partial (direction = 'outbound'): we deliberately allow duplicate
  inbound rows — multiple inbound replies from the same lead on the
  same variant are normal.
- COALESCE so legacy NULL/empty variants from before phase5's variant
  column bucket into 'dm'.

USAGE
-----
    cd apps/backend
    source venv/Scripts/activate
    python scripts/add_linkedin_message_unique_index.py

    # Check first without creating (validates that there are no
    # duplicates that would make CREATE INDEX fail):
    python scripts/add_linkedin_message_unique_index.py --check

If the check finds remaining duplicates the script exits non-zero and
points you back at the cleanup script.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from core.database import SessionLocal  # noqa: E402


INDEX_NAME = "uq_li_msg_outbound_one_per_variant"

CREATE_INDEX_SQL = f"""
    CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME}
        ON nexus_linkedin_messages (
            workspace_id,
            lead_id,
            COALESCE(NULLIF(variant, ''), 'dm')
        )
        WHERE direction = 'outbound'
"""


def _count_duplicates(db) -> int:
    row = db.execute(
        text(
            """
            SELECT COUNT(*)
              FROM (
                    SELECT 1
                      FROM nexus_linkedin_messages
                     WHERE direction = 'outbound'
                     GROUP BY workspace_id, lead_id,
                              COALESCE(NULLIF(variant, ''), 'dm')
                    HAVING COUNT(*) > 1
                   ) d
            """
        )
    ).first()
    return int(row[0] or 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check that the index can be created (no DDL run).",
    )
    args = parser.parse_args()

    # SUPERSEDED — do not run. This index keys on (workspace_id, lead_id,
    # variant) WITHOUT `step`, but the InMail cadence stores FOUR outbound rows
    # per lead (variant='inmail', step 0..3). Creating it would make rows 1-3
    # violate uniqueness; the writer inserts all four in one nested transaction
    # and swallows IntegrityError as a "race", so every lead would silently end
    # up with ZERO InMail rows.
    #
    # phase5.py now creates the correct index (uq_li_msg_outbound_variant_step,
    # which includes `step`) and drops this one on every cold start.
    print("REFUSING TO RUN — this index shape is superseded and would break the")
    print("4-step InMail cadence (it omits `step`).")
    print()
    print("The correct index is created automatically by the migration runner:")
    print("  nexus/migrations/phase5.py -> uq_li_msg_outbound_variant_step")
    print("  (workspace_id, lead_id, variant, step) WHERE direction = 'outbound'")
    print()
    print("Nothing to do. This script is kept only so an operator following an")
    print("old runbook gets this message instead of a broken cadence.")
    return 1

    db = SessionLocal()
    try:
        print("=" * 70)
        print(f"nexus_linkedin_messages — install partial UNIQUE INDEX")
        print("=" * 70)
        print(f"Index name : {INDEX_NAME}")
        print()

        dup_groups = _count_duplicates(db)
        print(f"Remaining duplicate groups: {dup_groups}")
        if dup_groups > 0:
            print()
            print("REFUSING to create the index — duplicates remain.")
            print("Run cleanup_duplicate_linkedin_messages.py --apply first.")
            return 1

        if args.check:
            print()
            print("Check passed. Re-run without --check to actually create.")
            return 0

        print()
        print("Creating index…")
        try:
            db.execute(text(CREATE_INDEX_SQL))
            db.commit()
            print(f"OK. Index '{INDEX_NAME}' is in place.")
            return 0
        except Exception as e:
            db.rollback()
            print(f"FAILED — transaction rolled back: {e}")
            return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
