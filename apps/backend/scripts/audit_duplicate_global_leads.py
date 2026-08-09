"""audit_duplicate_global_leads.py — read-only check for duplicate
rows in nexus_global_leads.

WHY THIS EXISTS
---------------
dedupe_nexus_leads.py cleans up duplicate per-workspace attachments
(`nexus_leads`). It does NOT touch the global identity table
(`nexus_global_leads`). If the same email was inserted as two separate
global_lead rows (race condition on intake, two different ingestion
paths, or pre-uniqueness-constraint legacy data), the journey UI will
still appear to have "two Clint McLains" even after dedupe_nexus_leads
has run — because each global_lead has its own nexus_leads attachment
and the dedup script considers them distinct.

This is a READ-ONLY audit. It reports duplicates by lowercased email,
shows the affected global_lead ids, and counts how many nexus_leads,
nexus_demo_bookings, and nexus_outreach rows each duplicate carries —
so you can decide whether a follow-up merge script is worth writing.

USAGE
-----
    cd apps/backend
    source venv/Scripts/activate
    python scripts/audit_duplicate_global_leads.py
    python scripts/audit_duplicate_global_leads.py --sample 50
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
    parser.add_argument("--sample", type=int, default=20)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        groups = db.execute(
            text(
                """
                SELECT LOWER(email) AS em, COUNT(*) AS n, ARRAY_AGG(id ORDER BY id) AS ids
                  FROM nexus_global_leads
                 WHERE email IS NOT NULL AND email <> ''
                 GROUP BY LOWER(email)
                HAVING COUNT(*) > 1
                 ORDER BY n DESC, em
                """
            )
        ).fetchall()

        print()
        print("=" * 72)
        print("DUPLICATE nexus_global_leads (by lowercased email) — READ ONLY")
        print("=" * 72)
        print(f"  emails with >1 global_lead row : {len(groups)}")
        print(
            f"  excess global_lead rows total  : {sum(int(g.n) - 1 for g in groups)}"
        )
        print()

        if not groups:
            print("  None. nexus_global_leads is clean.")
            return 0

        print(f"Sample (first {min(args.sample, len(groups))} of {len(groups)}):")
        print()
        print(f"  {'email':40}  {'#rows':>5}  {'leads/bookings/outreach per id'}")
        print("  " + "-" * 90)

        for g in groups[: args.sample]:
            ids = list(g.ids)
            counts = []
            for gid in ids:
                nl = db.execute(
                    text("SELECT COUNT(*) FROM nexus_leads WHERE global_lead_id = :g"),
                    {"g": int(gid)},
                ).scalar() or 0
                nb = db.execute(
                    text("SELECT COUNT(*) FROM nexus_demo_bookings WHERE lead_id = :g"),
                    {"g": int(gid)},
                ).scalar() or 0
                no = db.execute(
                    text("SELECT COUNT(*) FROM nexus_outreach WHERE lead_id = :g"),
                    {"g": int(gid)},
                ).scalar() or 0
                counts.append(f"{gid}:{nl}/{nb}/{no}")
            email_disp = (g.em or "")[:40]
            print(f"  {email_disp:40}  {int(g.n):>5}  {', '.join(counts)}")

        print()
        print(
            "Per-id legend: <global_lead_id>:<nexus_leads count>/<demo_bookings>/<outreach>"
        )
        print(
            "If you see e.g. '105:0/0/0, 312:2/1/5' that's a stale empty row + an active row —"
        )
        print(
            "the empty one is safe to drop. If both have data, a merge script is needed."
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
