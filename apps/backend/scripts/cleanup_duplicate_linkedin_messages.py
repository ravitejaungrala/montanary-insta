"""cleanup_duplicate_linkedin_messages.py — collapse duplicate outbound
rows in nexus_linkedin_messages so there is at most one row per
(workspace_id, lead_id, variant).

WHY THIS EXISTS
---------------
Sequencer-side dedup races have allowed multiple outbound rows to be
written for the same (workspace_id, lead_id, variant). The Lead Journey
table's COUNT(*) of LinkedIn touches was therefore inflated. We've
already fixed the read-side to COUNT(DISTINCT variant) — this script
cleans the underlying data so the table is internally consistent
again, and so a follow-up partial UNIQUE INDEX can be added safely
without colliding on existing rows.

KEEP POLICY
-----------
Per duplicate group, we keep the EARLIEST row (smallest `id`). The
earliest row is the original successful generation; later rows are
race-condition retries with identical or near-identical content. This
preserves the genuine `sent_at` and the original Gemini body.

SAFETY
------
- Default mode is DRY-RUN. It prints the delete plan but does NOT
  modify the DB.
- Pass --apply to actually delete.
- Wraps deletes in a single transaction so partial failure rolls back
  cleanly.
- Foreign-key audit: nexus_linkedin_messages.id is NOT referenced by
  any other table (checked 2026-05-29), so deleting a row does not
  orphan rows elsewhere. The reply-tracking tables key off
  linkedin_message_urn / lead_id / sent_at, not the surrogate id.

USAGE
-----
    cd apps/backend
    source venv/Scripts/activate

    # Dry-run (recommended first)
    python scripts/cleanup_duplicate_linkedin_messages.py

    # Apply
    python scripts/cleanup_duplicate_linkedin_messages.py --apply

    # Scope to one workspace
    python scripts/cleanup_duplicate_linkedin_messages.py --workspace 7 --apply
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
        "--apply",
        action="store_true",
        help="Actually delete duplicate rows. Default is dry-run.",
    )
    parser.add_argument(
        "--workspace",
        type=int,
        default=None,
        help="Scope to a single workspace_id (default: all workspaces).",
    )
    parser.add_argument(
        "--show",
        type=int,
        default=20,
        help="Number of delete-plan rows to print (default 20).",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # ── Build the set of row ids to delete ──────────────────────
        # For each (workspace_id, lead_id, COALESCE(variant,'dm'))
        # group that has > 1 outbound row, we keep MIN(id) and mark
        # every other id for deletion.
        ws_clause = ""
        params: dict = {}
        if args.workspace is not None:
            ws_clause = " AND workspace_id = :ws"
            params["ws"] = args.workspace

        plan_sql = f"""
            WITH ranked AS (
                SELECT id,
                       workspace_id,
                       lead_id,
                       COALESCE(NULLIF(variant, ''), 'dm') AS v,
                       ROW_NUMBER() OVER (
                           PARTITION BY workspace_id, lead_id,
                                        COALESCE(NULLIF(variant, ''), 'dm')
                           ORDER BY id
                       ) AS rn
                  FROM nexus_linkedin_messages
                 WHERE direction = 'outbound'{ws_clause}
            )
            SELECT id, workspace_id, lead_id, v
              FROM ranked
             WHERE rn > 1
             ORDER BY workspace_id, lead_id, v, id
        """
        plan_rows = db.execute(text(plan_sql), params).fetchall()
        delete_ids = [int(r[0]) for r in plan_rows]
        total = len(delete_ids)

        print("=" * 70)
        print("nexus_linkedin_messages — duplicate cleanup")
        print("=" * 70)
        if args.workspace is not None:
            print(f"Scope                 : workspace_id = {args.workspace}")
        else:
            print("Scope                 : all workspaces")
        print(f"Mode                  : {'APPLY (will delete)' if args.apply else 'DRY-RUN'}")
        print(f"Rows scheduled to drop: {total}")

        if total == 0:
            print("\nNothing to clean. DB is already deduped.")
            return 0

        print()
        print(f"Sample (first {min(args.show, total)} rows):")
        print("-" * 70)
        print(f"{'id':>10}  {'ws':>4}  {'lead_id':>10}  {'variant':<8}")
        print("-" * 70)
        for r in plan_rows[: args.show]:
            print(f"{int(r[0]):>10}  {int(r[1]):>4}  {int(r[2]):>10}  {str(r[3]):<8}")
        if total > args.show:
            print(f"... ({total - args.show} more)")

        if not args.apply:
            print()
            print("DRY-RUN — no changes made. Re-run with --apply to delete.")
            return 0

        # ── APPLY ───────────────────────────────────────────────────
        print()
        print("Applying deletes…")
        try:
            # Batch the delete so we don't push a giant IN(...) clause.
            BATCH = 500
            deleted = 0
            for i in range(0, total, BATCH):
                chunk = delete_ids[i : i + BATCH]
                db.execute(
                    text(
                        "DELETE FROM nexus_linkedin_messages WHERE id = ANY(:ids)"
                    ),
                    {"ids": chunk},
                )
                deleted += len(chunk)
                print(f"  deleted {deleted}/{total}")
            db.commit()
            print(f"OK. Deleted {deleted} duplicate rows.")
            print()
            print("Next step: run add_linkedin_message_unique_index.py to lock in")
            print("the (workspace_id, lead_id, variant) uniqueness at the DB level.")
            return 0
        except Exception as e:
            db.rollback()
            print(f"FAILED — transaction rolled back: {e}")
            return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
