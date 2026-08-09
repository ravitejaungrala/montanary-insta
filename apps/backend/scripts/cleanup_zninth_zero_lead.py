"""Delete z-ninth NexusProduct rows that have ZERO leads.

Targets ONLY: workspace_id=5 products whose name OR source_url contains
"z-ninth" (case-insensitive) AND have zero leads under any of their
campaigns.

Run modes:
    python scripts/cleanup_zninth_zero_lead.py            # DRY-RUN (default)
    python scripts/cleanup_zninth_zero_lead.py --execute  # Actually delete

Dry-run prints exactly what WOULD be deleted (products + their child
campaigns + sequences) and does not touch the DB. Always run dry-run
first, eyeball the list, then re-run with --execute.

Safety net:
  - Only products with lead_count == 0 are deleted.
  - Only products whose name or source_url matches z-ninth.
  - Only workspace_id == 5.
  - Wrapped in a single transaction; raises and rolls back on the first
    error so partial deletes never happen.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(HERE)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from core.database import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

WORKSPACE_ID = 5
NAME_PATTERN = "%z-ninth%"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually run the DELETE. Default is dry-run.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # 1. Find candidate z-ninth products with lead_count == 0.
        candidates = db.execute(
            text(
                """
                SELECT p.id,
                       p.name,
                       p.source_url,
                       (
                           SELECT COUNT(DISTINCT nl.global_lead_id)
                             FROM nexus_campaigns c
                             JOIN nexus_leads nl ON nl.campaign_id = c.id
                            WHERE c.product_id = p.id
                       ) AS lead_count,
                       (
                           SELECT COUNT(*)
                             FROM nexus_campaigns c
                            WHERE c.product_id = p.id
                       ) AS campaign_count
                  FROM nexus_products p
                 WHERE p.workspace_id = :w
                   AND (
                        LOWER(p.name) LIKE :pat
                        OR LOWER(COALESCE(p.source_url,'')) LIKE :pat
                       )
                 ORDER BY p.id
                """
            ),
            {"w": WORKSPACE_ID, "pat": NAME_PATTERN},
        ).fetchall()

        print(f"=== Found {len(candidates)} z-ninth products in workspace {WORKSPACE_ID} ===")
        for r in candidates:
            tag = "DELETE" if r[3] == 0 else "KEEP  "
            print(
                f"  [{tag}] product_id={r[0]:>4}  leads={r[3]:>3}  "
                f"campaigns={r[4]:>2}  name={r[1]!r}  url={r[2]!r}"
            )

        target_ids = [r[0] for r in candidates if r[3] == 0]
        if not target_ids:
            print("\nNothing to delete. Exiting.")
            return

        # 2. For the deletion targets, list the campaigns/sequences that
        #    will cascade-delete with them.
        print(f"\n=== Children that will cascade-delete ({len(target_ids)} products) ===")
        camp_rows = db.execute(
            text(
                """
                SELECT c.id, c.product_id, c.name,
                       (SELECT COUNT(*) FROM nexus_leads nl WHERE nl.campaign_id = c.id) AS n_leads,
                       (SELECT COUNT(*) FROM nexus_lead_sequences ls WHERE ls.campaign_id = c.id) AS n_seq,
                       (SELECT COUNT(*) FROM nexus_touchpoints tp WHERE tp.campaign_id = c.id) AS n_tp,
                       (SELECT COUNT(*) FROM nexus_outreach o WHERE o.campaign_id = c.id) AS n_out
                  FROM nexus_campaigns c
                 WHERE c.product_id = ANY(:pids)
                 ORDER BY c.id
                """
            ),
            {"pids": target_ids},
        ).fetchall()
        for r in camp_rows:
            print(
                f"  camp_id={r[0]:>4}  product={r[1]:>4}  name={r[2]!r:30}  "
                f"leads={r[3]} seqs={r[4]} touchpoints={r[5]} outreach={r[6]}"
            )

        # SAFETY GATE — if any campaign has any leads/touchpoints/etc.
        # (it shouldn't, because we filtered on lead_count == 0, but the
        # leads filter only counts via nexus_leads. Touchpoints could exist
        # historically), refuse to proceed without --force.
        risky = [r for r in camp_rows if r[3] or r[5] or r[6]]
        if risky:
            print(
                "\nABORT: at least one campaign under a 0-lead product still "
                "has touchpoints/outreach rows. Refusing to delete. "
                "Investigate before proceeding."
            )
            for r in risky:
                print(f"  RISKY camp_id={r[0]} leads={r[3]} touchpoints={r[5]} outreach={r[6]}")
            sys.exit(2)

        if not args.execute:
            print("\nDRY-RUN. Re-run with --execute to actually delete.")
            return

        # 3. EXECUTE — delete in one transaction. Cascade order matters
        #    because not every FK is ON DELETE CASCADE — delete deepest
        #    children first.
        print("\n=== EXECUTING DELETE ===")
        camp_ids = [r[0] for r in camp_rows]

        if camp_ids:
            # Child rows of campaigns we know about.
            n = db.execute(
                text("DELETE FROM nexus_lead_sequences WHERE campaign_id = ANY(:cids)"),
                {"cids": camp_ids},
            ).rowcount
            print(f"  deleted nexus_lead_sequences: {n}")
            n = db.execute(
                text("DELETE FROM nexus_leads WHERE campaign_id = ANY(:cids)"),
                {"cids": camp_ids},
            ).rowcount
            print(f"  deleted nexus_leads: {n}")
            n = db.execute(
                text("DELETE FROM nexus_touchpoints WHERE campaign_id = ANY(:cids)"),
                {"cids": camp_ids},
            ).rowcount
            print(f"  deleted nexus_touchpoints: {n}")
            n = db.execute(
                text("DELETE FROM nexus_outreach WHERE campaign_id = ANY(:cids)"),
                {"cids": camp_ids},
            ).rowcount
            print(f"  deleted nexus_outreach: {n}")
            n = db.execute(
                text("DELETE FROM nexus_campaigns WHERE id = ANY(:cids)"),
                {"cids": camp_ids},
            ).rowcount
            print(f"  deleted nexus_campaigns: {n}")

        n = db.execute(
            text("DELETE FROM nexus_products WHERE id = ANY(:pids)"),
            {"pids": target_ids},
        ).rowcount
        print(f"  deleted nexus_products: {n}")

        db.commit()
        print("\nCommit successful.")
    except Exception:
        db.rollback()
        print("\nROLLED BACK — no changes committed.")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
