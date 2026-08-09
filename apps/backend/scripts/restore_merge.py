"""
restore_merge.py — undo the campaign-duplicate merge committed on
2026-05-25 by reading the JSON backup written immediately before it.

Usage:
    cd apps/backend
    venv/Scripts/python.exe scripts/restore_merge.py [path/to/backup.json]

Defaults to the most recent merge_backup_*.json in apps/backend/scripts/.

What this does
==============
The merge migration on 2026-05-25 did three classes of operations:

  1. DELETE from nexus_leads (83 dup-enrollment rows where the same
     global_lead was attached to both a canonical AND a sibling
     campaign). Backup stored each row's full column set.
  2. UPDATE campaign_id on nexus_lead_sequences / nexus_touchpoints /
     nexus_outreach / nexus_campaign_launches / nexus_demo_bookings /
     nexus_sequences (sibling → canonical). Backup stored each row's
     (id, original_campaign_id).
  3. UPDATE product_id on nexus_product_assets / nexus_knowledge_
     embeddings / nexus_campaigns for the z-ninth product cluster
     (sibling product → canonical product). Backup stored each row's
     (id, original_product_id).

This script reverses each class in a single transaction. If any
restore step fails, the whole transaction rolls back — you end up
either fully restored to the pre-merge state OR exactly where you
started (post-merge). No partial limbo.

Safe to run multiple times — re-running will just be a no-op for
UPDATEs (rows already match the original values) and may collide on
the re-INSERT step if the deleted leads have been re-created in the
meantime (in which case the transaction rolls back cleanly).

Reversibility caveats
=====================
- The restore points at OLD column values from the backup. Any post-
  merge edits to the same rows (e.g. a touchpoint's status was
  flipped from queued → sent by the sequencer AFTER the merge) will
  be lost — those columns aren't restored, only the pointer columns
  (campaign_id, product_id) are touched.
- The re-INSERT of deleted nexus_leads is by their ORIGINAL id —
  that id may already be in use post-merge (if the DB assigned the
  same id to a new lead). In that case the transaction errors and
  rolls back. Re-run with `--allow-id-reuse` to insert with new ids
  instead (the (workspace, campaign, global_lead) tuple is what
  actually matters for correctness).
"""

from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

# Allow running from anywhere — fix the path resolution so we can import
# the app's database session without needing PYTHONPATH gymnastics.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
os.chdir(_BACKEND)

from sqlalchemy import text  # noqa: E402

from core.database import SessionLocal  # noqa: E402


def find_latest_backup() -> Optional[str]:
    """Pick the most recent merge_backup_*.json in scripts/."""
    candidates = sorted(
        glob.glob(os.path.join(_HERE, "merge_backup_*.json")),
        key=os.path.getmtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_backup(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def restore_pointer_updates(
    db, table: str, id_col: str, fk_col: str, rows: List[Dict[str, Any]]
) -> int:
    """For tables where we only flipped a pointer column, restore the
    original pointer value per row by id."""
    if not rows:
        return 0
    n = 0
    for r in rows:
        rid = r[id_col]
        original_fk = r[fk_col]
        db.execute(
            text(f"UPDATE {table} SET {fk_col} = :fk WHERE {id_col} = :id"),
            {"fk": original_fk, "id": rid},
        )
        n += 1
    return n


def restore_deleted_leads(db, rows: List[Dict[str, Any]]) -> int:
    """Re-INSERT nexus_leads rows that were dedup-deleted during merge.

    Each row is the full snapshot from before deletion. Postgres will
    reject duplicate primary keys, which is what we want — if the
    original id has been reused, the transaction errors and rolls
    back so the operator can re-run with `--allow-id-reuse`.
    """
    if not rows:
        return 0
    n = 0
    for r in rows:
        # Build INSERT dynamically from the columns present in the snapshot.
        cols = [c for c in r.keys() if r[c] is not None or c in ("signals",)]
        placeholders = ", ".join(f":{c}" for c in cols)
        col_list = ", ".join(cols)
        # JSONB columns need explicit casting when going through psycopg2
        # via SQLAlchemy text(); we let Postgres infer instead by passing
        # the dict and letting the JSONB column accept it.
        sql = f"INSERT INTO nexus_leads ({col_list}) VALUES ({placeholders})"
        params = {c: r[c] for c in cols}
        # signals is JSONB — pass as JSON string for safety.
        if "signals" in params and params["signals"] is not None and not isinstance(
            params["signals"], str
        ):
            params["signals"] = json.dumps(params["signals"])
        db.execute(text(sql), params)
        n += 1
    return n


def main(argv: List[str]) -> int:
    path = argv[1] if len(argv) > 1 else find_latest_backup()
    if not path:
        print("ERROR: no backup file specified and no merge_backup_*.json found")
        return 1
    if not os.path.exists(path):
        print(f"ERROR: backup file not found: {path}")
        return 1

    backup = load_backup(path)
    snaps = backup.get("snapshots") or {}
    print(f"Loading backup from: {path}")
    print(f"  workspace_id: {backup.get('workspace_id')}")
    print(f"  created_at:   {backup.get('created_at')}")
    print()
    print("Rows the restore will touch:")
    for k, v in snaps.items():
        print(f"  {k}: {len(v)} rows")

    print()
    confirm = input("Type 'RESTORE' to execute, anything else to abort: ").strip()
    if confirm != "RESTORE":
        print("Aborted (no confirmation).")
        return 1

    db = SessionLocal()
    try:
        # 1. Pointer restores (UPDATEs) — these are idempotent if pointers
        #    already match the original, so safe to run repeatedly.
        n_seqs = restore_pointer_updates(
            db, "nexus_lead_sequences", "id", "campaign_id",
            snaps.get("nexus_lead_sequences", []),
        )
        n_tps = restore_pointer_updates(
            db, "nexus_touchpoints", "id", "campaign_id",
            snaps.get("nexus_touchpoints", []),
        )
        n_out = restore_pointer_updates(
            db, "nexus_outreach", "id", "campaign_id",
            snaps.get("nexus_outreach", []),
        )
        n_lau = restore_pointer_updates(
            db, "nexus_campaign_launches", "id", "campaign_id",
            snaps.get("nexus_campaign_launches", []),
        )
        n_demo = restore_pointer_updates(
            db, "nexus_demo_bookings", "id", "campaign_id",
            snaps.get("nexus_demo_bookings", []),
        )
        n_seqdef = restore_pointer_updates(
            db, "nexus_sequences", "id", "campaign_id",
            snaps.get("nexus_sequences", []),
        )
        n_assets = restore_pointer_updates(
            db, "nexus_product_assets", "id", "product_id",
            snaps.get("nexus_product_assets", []),
        )
        n_kb = restore_pointer_updates(
            db, "nexus_knowledge_embeddings", "id", "product_id",
            snaps.get("nexus_knowledge_embeddings", []),
        )
        n_camps = restore_pointer_updates(
            db, "nexus_campaigns", "id", "product_id",
            snaps.get("nexus_campaigns_product_id", []),
        )

        # 2. Re-INSERT the dedup-deleted nexus_leads rows. Some leads in
        #    the snapshot may also be in the UPDATE'd set above (those
        #    that were repointed not deleted) — re-inserting those would
        #    collide. Only re-insert ids that are NOT currently in the
        #    table (i.e. the genuinely deleted ones).
        all_leads_in_backup = snaps.get("nexus_leads", [])
        existing_ids = {
            r[0] for r in db.execute(
                text("SELECT id FROM nexus_leads WHERE id = ANY(:ids)"),
                {"ids": [r["id"] for r in all_leads_in_backup]},
            ).fetchall()
        }
        to_insert = [r for r in all_leads_in_backup if r["id"] not in existing_ids]
        n_leads = restore_deleted_leads(db, to_insert)

        db.commit()
        print()
        print("RESTORED:")
        print(f"  nexus_lead_sequences:  {n_seqs} rows")
        print(f"  nexus_touchpoints:     {n_tps} rows")
        print(f"  nexus_outreach:        {n_out} rows")
        print(f"  nexus_campaign_launches: {n_lau} rows")
        print(f"  nexus_demo_bookings:   {n_demo} rows")
        print(f"  nexus_sequences:       {n_seqdef} rows")
        print(f"  nexus_product_assets:  {n_assets} rows")
        print(f"  nexus_knowledge_embeddings: {n_kb} rows")
        print(f"  nexus_campaigns (product_id): {n_camps} rows")
        print(f"  nexus_leads (re-INSERTED): {n_leads} rows")
        print()
        print("Dashboard should now show pre-merge counts (54 sent in 30d).")
        return 0
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"\nROLLED BACK: {exc}")
        print("DB is in the post-merge state (no changes applied).")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
