"""One-shot script: correct entity_type on misclassified products.

Why this exists:
    aAROHAN Consulting (and any other GCC-provider that was created
    BEFORE the GCC chooser shipped) was stored with
    `nexus_products.icp.entity_type = 'service'`. The Dashboard's
    Services filter now shows it under Services even though it's a
    GCC provider. This script updates the JSONB so the row reflects
    reality.

Usage:
    cd apps/backend
    python -m scripts.fix_product_entity_type

    # Or to dry-run (print SQL without executing):
    DRY_RUN=1 python -m scripts.fix_product_entity_type

What it does:
    1. Searches nexus_products for rows whose `name` matches any of
       the targets (case-insensitive, substring match)
    2. Updates `icp.entity_type` to the new value
    3. Commits one transaction per row so a single bad row doesn't
       roll back the others

Safe to re-run — UPDATEs are idempotent.
"""
from __future__ import annotations

import os
import sys
import json

# Make the parent package importable when invoked as a module.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from core.database import SessionLocal


# Edit this list to add more misclassified products.
# (name_substring, correct_entity_type)
TARGETS = [
    ("aAROHAN",  "gcc"),
    ("aarohan",  "gcc"),
    ("ANSR",     "gcc"),
    ("Inductus", "gcc"),
]


def main() -> int:
    dry_run = bool(os.getenv("DRY_RUN"))
    db = SessionLocal()
    try:
        for needle, new_etype in TARGETS:
            rows = db.execute(
                text(
                    """
                    SELECT id, name, icp
                      FROM nexus_products
                     WHERE LOWER(name) LIKE :n
                    """
                ),
                {"n": f"%{needle.lower()}%"},
            ).fetchall()

            if not rows:
                print(f"  · no match for '{needle}'")
                continue

            for r in rows:
                pid, name, icp = r[0], r[1], r[2]
                icp_dict = dict(icp) if isinstance(icp, dict) else {}
                current = (icp_dict.get("entity_type") or "").strip().lower()
                if current == new_etype:
                    print(f"  · #{pid} '{name}' already entity_type={new_etype} — skipped")
                    continue

                icp_dict["entity_type"] = new_etype
                if dry_run:
                    print(
                        f"  [DRY] would update #{pid} '{name}': "
                        f"entity_type {current!r} -> {new_etype!r}"
                    )
                    continue

                try:
                    db.execute(
                        text(
                            "UPDATE nexus_products SET icp = :icp WHERE id = :id"
                        ),
                        {"icp": json.dumps(icp_dict), "id": pid},
                    )
                    db.commit()
                    print(
                        f"  ✓ updated #{pid} '{name}': "
                        f"entity_type {current!r} -> {new_etype!r}"
                    )
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    print(f"  ✗ FAILED #{pid} '{name}': {exc}")

    finally:
        db.close()

    if dry_run:
        print("\nDRY_RUN — no changes committed. Drop DRY_RUN=1 to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
