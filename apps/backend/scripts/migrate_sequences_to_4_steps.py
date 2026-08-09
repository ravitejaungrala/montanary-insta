"""Safe one-off migration: expand 3-step sequences to 4 steps in nexus_sequences.

Why this exists
---------------
Existing in-flight leads were enrolled with the OLD 3-step _DEFAULT_STEPS
(Initial → FU1 → Closing). We've updated discover_for_campaign.py to seed
4 steps (Initial → FU1 → FU2 → Closing) for NEW sequences, but existing
rows in nexus_sequences still have only 3 entries in their `steps` JSONB.

This causes the OutreachFlowPanel to render only 3 email stages for
in-flight leads (no "Follow-up 2" node), and the date-projection logic
to chain incorrectly.

Safety guarantees
-----------------
1. READ-ONLY preview FIRST. The script prints what's in the DB before
   touching anything. You see exactly what's there before you confirm.
2. Snapshot table created BEFORE the update. If anything looks wrong,
   one SQL command restores. (`UPDATE nexus_sequences AS s SET
   steps = b.steps FROM nexus_sequences_backup_<ts> AS b WHERE s.id = b.id;`)
3. Targets ONLY rows with exactly 3 steps. Rows with 4 (already migrated),
   1-2 (unusual), or 5+ (custom) are left untouched and reported for manual review.
4. Steps 0 and 1 are preserved BYTE-FOR-BYTE — we slice them with `steps->0`
   and `steps->1` JSONB ops, not by reconstructing. Whatever fields were
   there (subject_template, body_template, channel, custom keys) stay.
5. Transaction-wrapped (BEGIN/COMMIT or ROLLBACK on any error). If the
   verify-after-update check fails, the script aborts and rolls back.
6. The two NEW rows we append (step 2 = FU2, step 3 = Closing) include the
   Resend-path fallback templates from discover_for_campaign.py so a
   Resend rollback (NEXUS_USE_APOLLO_SEND=false) would still produce
   readable emails. Apollo path ignores these fields — they're inert
   under the current config.

Usage
-----
    cd apps/backend
    source venv/Scripts/activate   # or: source venv/bin/activate
    python scripts/migrate_sequences_to_4_steps.py

The script will print state, ask for confirmation, then proceed. Pass
--dry-run to see the plan without writing anything.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


# ─── New step content (matches _DEFAULT_STEPS[2] and [3] in
#     discover_for_campaign.py exactly so future re-seeds are consistent) ─────

NEW_STEP_2_FU2 = {
    "step": 2,
    "delay_days": 3,
    "subject_template": "One more thought for {company_name}",
    "body_template": (
        "Hi {first_name},\n\n"
        "Different angle — most {company_name}-style teams we talk to are "
        "quietly sitting on the exact problem {product_name} solves. "
        "Curious if that resonates, or if the timing's just off?"
    ),
}

NEW_STEP_3_CLOSING = {
    "step": 3,
    "delay_days": 3,
    "subject_template": "Last note, {first_name}",
    "body_template": (
        "Hi {first_name},\n\n"
        "Last note from me — completely understand if the timing isn't right. "
        "If {product_name} ever becomes relevant for {company_name}, you "
        "know where to find me."
    ),
}


def _engine():
    """Reuse the same env-loading pattern as scripts/check_db.py."""
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not found in .env")
        sys.exit(1)
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(db_url)


def _print_state(conn, label: str) -> dict:
    """Print every row's id, name, step_count. Returns {step_count: row_count}.

    Read-only — used both before (preview) and after (verify) the update.
    """
    print(f"\n──────── {label} ────────")
    rows = conn.execute(
        text(
            """
            SELECT id, name, jsonb_array_length(steps) AS step_count
            FROM nexus_sequences
            ORDER BY id
            """
        )
    ).fetchall()

    if not rows:
        print("  (no rows in nexus_sequences)")
        return {}

    by_count: dict[int, int] = {}
    for r in rows:
        sid, name, step_count = r
        by_count[step_count] = by_count.get(step_count, 0) + 1
        print(f"  id={sid:>4}  steps={step_count}  name={name!r}")

    print(f"\n  Summary: {dict(sorted(by_count.items()))}")
    return by_count


def _show_full_row(conn, row_id: int) -> None:
    """Pretty-print the full steps JSONB for one row — used for inspection
    so the operator can see exactly what's there before confirming."""
    r = conn.execute(
        text("SELECT id, name, steps FROM nexus_sequences WHERE id = :id"),
        {"id": row_id},
    ).first()
    if not r:
        return
    print(f"\n── Full content of row id={r[0]} ({r[1]!r}) ──")
    print(json.dumps(r[2], indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the plan without writing. No backup table, no UPDATE.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt (non-interactive mode).",
    )
    args = parser.parse_args()

    engine = _engine()
    backup_table = (
        "nexus_sequences_backup_"
        + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    )

    with engine.connect() as conn:
        print(f"Connected to: {engine.url.host}/{engine.url.database}")

        # ── PHASE 1: read-only preview ──
        before = _print_state(conn, "BEFORE — current state of nexus_sequences")

        target_count = before.get(3, 0)
        already_ok = before.get(4, 0)
        other_counts = {k: v for k, v in before.items() if k not in (3, 4)}

        print()
        print(f"Rows with exactly 3 steps → will be migrated: {target_count}")
        print(f"Rows already at 4 steps → left untouched: {already_ok}")
        if other_counts:
            print(f"⚠ Rows with unusual step counts → SKIPPED for manual review: {other_counts}")

        if target_count == 0:
            print("\nNothing to do — no 3-step rows exist. Exiting.")
            return 0

        # Show the FIRST 3-step row in full so operator sees the exact shape.
        first_3step_id = conn.execute(
            text(
                """SELECT id FROM nexus_sequences
                    WHERE jsonb_array_length(steps) = 3
                    ORDER BY id LIMIT 1"""
            )
        ).scalar()
        if first_3step_id is not None:
            _show_full_row(conn, first_3step_id)

        print("\n── PLAN ──")
        print(f"  1. CREATE TABLE {backup_table} AS SELECT * FROM nexus_sequences;")
        print(f"  2. UPDATE rows WHERE jsonb_array_length(steps) = 3:")
        print( "       SET steps = jsonb_build_array(")
        print( "         steps->0,                       -- preserved as-is")
        print( "         steps->1,                       -- preserved as-is")
        print(f"         <new step 2 FU2>,               -- delay_days=3")
        print(f"         <new step 3 Closing>            -- delay_days=3")
        print( "       )")
        print(f"  3. VERIFY all targeted rows now have step_count=4. If not → ROLLBACK.")
        print(f"  4. COMMIT.")
        print()
        print("New step 2 content:")
        print(json.dumps(NEW_STEP_2_FU2, indent=2))
        print("\nNew step 3 content:")
        print(json.dumps(NEW_STEP_3_CLOSING, indent=2))

        if args.dry_run:
            print("\n--dry-run set — exiting without changes.")
            return 0

        if not args.yes:
            print()
            ans = input(f"Proceed with migration on {target_count} row(s)? [y/N]: ").strip().lower()
            if ans not in ("y", "yes"):
                print("Aborted by user.")
                return 1

    # ── PHASE 2: do the work inside a transaction ──
    with engine.begin() as conn:  # auto-commit on success, rollback on exception
        print(f"\n[1/4] Creating backup table {backup_table} ...")
        conn.execute(text(f"CREATE TABLE {backup_table} AS SELECT * FROM nexus_sequences"))
        backup_count = conn.execute(
            text(f"SELECT COUNT(*) FROM {backup_table}")
        ).scalar()
        print(f"      backed up {backup_count} row(s)")

        print("[2/4] Running UPDATE ...")
        result = conn.execute(
            text(
                """
                UPDATE nexus_sequences
                SET steps = jsonb_build_array(
                    steps->0,
                    steps->1,
                    CAST(:fu2 AS jsonb),
                    CAST(:closing AS jsonb)
                )
                WHERE jsonb_array_length(steps) = 3
                """
            ),
            {
                "fu2": json.dumps(NEW_STEP_2_FU2),
                "closing": json.dumps(NEW_STEP_3_CLOSING),
            },
        )
        print(f"      UPDATE affected {result.rowcount} row(s)")

        print("[3/4] Verifying post-update state ...")
        still_3 = conn.execute(
            text(
                "SELECT COUNT(*) FROM nexus_sequences WHERE jsonb_array_length(steps) = 3"
            )
        ).scalar()
        if still_3 > 0:
            # Anything still at 3 steps means the UPDATE didn't fully apply.
            # Raise so the transaction rolls back — backup table is also
            # rolled back since it was created in this same transaction.
            raise RuntimeError(
                f"verify failed: {still_3} row(s) still have 3 steps after UPDATE"
            )

        # Confirm at least target_count rows now have 4 steps.
        now_4 = conn.execute(
            text(
                "SELECT COUNT(*) FROM nexus_sequences WHERE jsonb_array_length(steps) = 4"
            )
        ).scalar()
        print(f"      rows now at 4 steps: {now_4}")

        print("[4/4] Committing transaction ...")
        # commit happens on context-manager exit

    # ── PHASE 3: read-only post-verify (own connection so we see committed state) ──
    with engine.connect() as conn:
        _print_state(conn, "AFTER — post-migration state")
        print(f"\n✓ Done. Backup table preserved: {backup_table}")
        print("  To restore (only if needed):")
        print("    UPDATE nexus_sequences AS s")
        print(f"      SET steps = b.steps")
        print(f"      FROM {backup_table} AS b")
        print("      WHERE s.id = b.id;")

    return 0


if __name__ == "__main__":
    sys.exit(main())
