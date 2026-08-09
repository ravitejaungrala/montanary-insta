"""Safe cleanup: drop legacy KB tables after the Pinecone migration is live.

Why this exists
---------------
The 2026-05-26 Pinecone migration moved knowledge-base storage out of
Postgres:
  - Raw text → S3 (per workspace + product + asset)
  - Chunked vectors → Pinecone (namespace `ws-{workspace_id}`)

The legacy Postgres tables / columns are now dead weight on RDS:
  1. `nexus_knowledge_embeddings`     — vector chunks (full table)
  2. `nexus_product_assets.raw_text`  — the 200KB plaintext column

This script removes both. Run only AFTER verifying the new path works
end-to-end for at least one real lead reply.

Safety guarantees
-----------------
1. READ-ONLY preview first. Shows row counts + column existence before
   touching anything.
2. Backup table created BEFORE drop:
       nexus_knowledge_embeddings_backup_<YYYYMMDD_HHMMSS>
   Held for at least 30 days, then you can drop manually.
3. Transaction-wrapped (BEGIN / COMMIT / ROLLBACK on any error).
4. Idempotent — running twice is safe (uses IF EXISTS guards).

Usage
-----
    cd apps/backend
    source venv/Scripts/activate
    PYTHONIOENCODING=utf-8 python scripts/drop_old_kb_tables.py --dry-run
    PYTHONIOENCODING=utf-8 python scripts/drop_old_kb_tables.py --yes

Restoring (only if needed):
    -- The backup table has the original chunks. Recreate the table
    -- from it and re-attach FK constraints by hand if you ever need
    -- to roll back. Vectors are in JSONB so they're still readable.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


KB_TABLE = "nexus_knowledge_embeddings"
ASSETS_TABLE = "nexus_product_assets"
RAW_TEXT_COLUMN = "raw_text"


def _engine():
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set in .env")
        sys.exit(1)
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(db_url)


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute(
        text("SELECT to_regclass(:n) IS NOT NULL"),
        {"n": f"public.{name}"},
    ).scalar())


def _column_exists(conn, table: str, column: str) -> bool:
    return bool(conn.execute(
        text(
            """SELECT 1 FROM information_schema.columns
               WHERE table_name = :t AND column_name = :c"""
        ),
        {"t": table, "c": column},
    ).scalar())


def _row_count(conn, table: str) -> int:
    try:
        return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
    except Exception:
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview only — no writes.")
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation.")
    args = parser.parse_args()

    engine = _engine()
    backup_table = (
        "nexus_knowledge_embeddings_backup_"
        + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    )

    # ── Phase 1: read-only preview ──
    with engine.connect() as conn:
        print(f"Connected to: {engine.url.host}/{engine.url.database}\n")

        kb_exists = _table_exists(conn, KB_TABLE)
        assets_exists = _table_exists(conn, ASSETS_TABLE)
        raw_text_exists = (
            _column_exists(conn, ASSETS_TABLE, RAW_TEXT_COLUMN)
            if assets_exists else False
        )
        kb_rows = _row_count(conn, KB_TABLE) if kb_exists else 0

        print("======== CURRENT STATE ========")
        print(f"  {KB_TABLE:<35s} exists={kb_exists}  rows={kb_rows}")
        print(f"  {ASSETS_TABLE+'.'+RAW_TEXT_COLUMN:<35s} exists={raw_text_exists}")
        print()

        if not kb_exists and not raw_text_exists:
            print("Nothing to drop. Both objects already absent. Exiting.")
            return 0

        # Sanity check: warn if there's still data in the KB table — that
        # might mean the Pinecone migration didn't finish.
        if kb_rows > 0:
            print(
                f"WARNING: {KB_TABLE} still has {kb_rows} rows. The Pinecone\n"
                "migration script does NOT delete from this table — it only\n"
                "re-indexes into Pinecone. If you proceed, the backup table\n"
                "below preserves these rows. Re-read the migration plan if\n"
                "unsure.\n"
            )

        print("======== PLAN ========")
        print(f"  1. CREATE TABLE {backup_table} AS SELECT * FROM {KB_TABLE};")
        print(f"  2. DROP TABLE IF EXISTS {KB_TABLE};")
        print(f"  3. ALTER TABLE {ASSETS_TABLE} DROP COLUMN IF EXISTS {RAW_TEXT_COLUMN};")
        print(f"  4. VERIFY both objects gone. ROLLBACK on any verify failure.")
        print()

        if args.dry_run:
            print("--dry-run set — exiting without changes.")
            return 0

        if not args.yes:
            ans = input("Proceed with cleanup? [yes/N]: ").strip().lower()
            if ans not in ("y", "yes"):
                print("Aborted by user.")
                return 1

    # ── Phase 2: transactional drop ──
    with engine.begin() as conn:
        if kb_exists:
            print(f"\n[1/4] Backing up {KB_TABLE} -> {backup_table} ...")
            conn.execute(text(f"CREATE TABLE {backup_table} AS SELECT * FROM {KB_TABLE}"))
            backup_count = _row_count(conn, backup_table)
            print(f"      backed up {backup_count} row(s)")

            print(f"[2/4] Dropping table {KB_TABLE} ...")
            # CASCADE removes any FK references (the relationship in
            # nexus_product_assets is one-way; CASCADE cleans up any
            # orphan indices safely).
            conn.execute(text(f"DROP TABLE IF EXISTS {KB_TABLE} CASCADE"))
        else:
            print(f"\n[1-2/4] {KB_TABLE} already absent — skipping backup + drop.")

        if raw_text_exists:
            print(f"[3/4] Dropping column {ASSETS_TABLE}.{RAW_TEXT_COLUMN} ...")
            conn.execute(text(
                f"ALTER TABLE {ASSETS_TABLE} DROP COLUMN IF EXISTS {RAW_TEXT_COLUMN}"
            ))
        else:
            print(f"[3/4] {ASSETS_TABLE}.{RAW_TEXT_COLUMN} already absent — skipping.")

        # Verify in the SAME transaction. If anything failed, raising
        # here triggers an automatic rollback of all changes above.
        print("[4/4] Verifying ...")
        still_kb = _table_exists(conn, KB_TABLE)
        still_col = _column_exists(conn, ASSETS_TABLE, RAW_TEXT_COLUMN) if _table_exists(conn, ASSETS_TABLE) else False
        if still_kb:
            raise RuntimeError(f"verify failed: {KB_TABLE} still exists after DROP")
        if still_col:
            raise RuntimeError(f"verify failed: {ASSETS_TABLE}.{RAW_TEXT_COLUMN} still exists after DROP COLUMN")
        print(f"      both objects confirmed gone")
        print("[5/4] Committing transaction ...")

    # ── Phase 3: post-commit confirmation ──
    with engine.connect() as conn:
        kb_after = _table_exists(conn, KB_TABLE)
        col_after = _column_exists(conn, ASSETS_TABLE, RAW_TEXT_COLUMN) if _table_exists(conn, ASSETS_TABLE) else False
        backup_after = _table_exists(conn, backup_table) if kb_exists else False
        print()
        print("======== AFTER ========")
        print(f"  {KB_TABLE:<40s} exists={kb_after}  (expected False)")
        print(f"  {ASSETS_TABLE+'.'+RAW_TEXT_COLUMN:<40s} exists={col_after}  (expected False)")
        if kb_exists:
            print(f"  {backup_table:<40s} exists={backup_after}  (preserved for 30 days)")

        if kb_after or col_after:
            print("\nUNEXPECTED: drops did not stick. Investigate.")
            return 1

    print("\n✓ Done. Old KB tables removed. Pinecone now owns retrieval.")
    if kb_exists:
        print("  Backup table preserved — drop manually after 30 days:")
        print(f"    DROP TABLE {backup_table};")
    return 0


if __name__ == "__main__":
    sys.exit(main())
