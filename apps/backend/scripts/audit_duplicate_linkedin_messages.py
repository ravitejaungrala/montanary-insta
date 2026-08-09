"""audit_duplicate_linkedin_messages.py — read-only check for duplicate
outbound rows in nexus_linkedin_messages.

WHY THIS EXISTS
---------------
The sequencer's SELECT-then-INSERT dedup (sequencer.py L1062-L1073) has
no row-level lock and the table has no unique constraint, so concurrent
sequencer passes — or a partial-rollback InMail retry — can write more
than one outbound row per (workspace_id, lead_id, variant). That shows
up in the Lead Journey table as inflated "Touches" counts (e.g. the
LinkedIn icon showing "4" for a lead that only has 1 DM + 1 InMail).

This audit is READ-ONLY. It reports:
  - total duplicate groups (more than 1 row for same key)
  - total extra rows that would be removed by a dedupe
  - sample of affected (workspace_id, lead_id, variant) keys

USAGE
-----
    cd apps/backend
    source venv/Scripts/activate
    python scripts/audit_duplicate_linkedin_messages.py
    python scripts/audit_duplicate_linkedin_messages.py --sample 50

The dedup KEY treats NULL/empty variant as 'dm' for backward-compat
with rows written before the variant column existed (phase5 migration).
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--sample",
        type=int,
        default=20,
        help="Number of duplicate groups to print as samples (default 20)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # ── Totals ──────────────────────────────────────────────────
        totals = db.execute(
            text(
                """
                WITH dup_groups AS (
                    SELECT workspace_id,
                           lead_id,
                           COALESCE(NULLIF(variant, ''), 'dm') AS v,
                           COUNT(*) AS n
                      FROM nexus_linkedin_messages
                     WHERE direction = 'outbound'
                     GROUP BY workspace_id, lead_id, v
                    HAVING COUNT(*) > 1
                )
                SELECT COUNT(*)               AS group_count,
                       COALESCE(SUM(n), 0)    AS rows_in_dup_groups,
                       COALESCE(SUM(n - 1), 0) AS extra_rows_removable
                  FROM dup_groups
                """
            )
        ).first()

        group_count = int(totals[0] or 0)
        rows_in_groups = int(totals[1] or 0)
        removable = int(totals[2] or 0)

        print("=" * 70)
        print("nexus_linkedin_messages — duplicate audit (READ-ONLY)")
        print("=" * 70)
        print(f"Duplicate groups (workspace_id, lead_id, variant) : {group_count}")
        print(f"Total rows inside those groups                    : {rows_in_groups}")
        print(f"Extra rows that COULD be removed by cleanup       : {removable}")

        if group_count == 0:
            print("\nNo duplicates found. DB is clean.")
            return 0

        # ── Sample groups ───────────────────────────────────────────
        print()
        print(f"Sample (up to {args.sample} groups, biggest first):")
        print("-" * 70)
        print(f"{'ws':>4}  {'lead_id':>10}  {'variant':<8}  {'rows':>4}  {'ids (keep | drop...)':<40}")
        print("-" * 70)
        rows = db.execute(
            text(
                """
                SELECT workspace_id,
                       lead_id,
                       COALESCE(NULLIF(variant, ''), 'dm') AS v,
                       COUNT(*) AS n,
                       ARRAY_AGG(id ORDER BY id) AS ids
                  FROM nexus_linkedin_messages
                 WHERE direction = 'outbound'
                 GROUP BY workspace_id, lead_id, v
                HAVING COUNT(*) > 1
                 ORDER BY n DESC, workspace_id, lead_id
                 LIMIT :lim
                """
            ),
            {"lim": args.sample},
        ).fetchall()
        for r in rows:
            ws, lead_id, variant, n, ids = r
            ids = list(ids or [])
            keep = ids[0]
            drop = ids[1:]
            print(
                f"{ws:>4}  {lead_id:>10}  {variant:<8}  {n:>4}  "
                f"{keep} | {','.join(str(x) for x in drop[:6])}"
                + ("..." if len(drop) > 6 else "")
            )

        print()
        print("Next step: run cleanup_duplicate_linkedin_messages.py")
        print("  - default mode is DRY-RUN (shows what it would delete)")
        print("  - pass --apply to actually delete (keeps earliest row per key)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
