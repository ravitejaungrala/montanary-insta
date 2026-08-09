"""fix_booking_email.py — one-off repair for a demo booking whose
attendee_email was typo'd, leaving it unable to match any lead and
silently falling through to the resolver's step-4 fallback
(workspace's first product).

Updates the booking's `attendee_email`, fills `lead_id` and
`campaign_id` from the correctly-spelled lead, and clears the stale
briefing row so the next /briefing GET regenerates against the right
product.

USAGE
-----
    cd apps/backend
    source venv/Scripts/activate
    # dry-run (default)
    python scripts/fix_booking_email.py --from cmclaim@libnat.com --to cmclain@libnat.com
    # apply
    python scripts/fix_booking_email.py --from cmclaim@libnat.com --to cmclain@libnat.com --apply
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="src", required=True, help="Email currently on the booking (typo'd)")
    parser.add_argument("--to", dest="dst", required=True, help="Correct lead email to repoint at")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    src = args.src.lower()
    dst = args.dst.lower()

    db = SessionLocal()
    try:
        bookings = db.execute(
            text(
                "SELECT id, workspace_id, attendee_email, lead_id, campaign_id, status "
                "FROM nexus_demo_bookings WHERE LOWER(attendee_email) = :em ORDER BY id DESC"
            ),
            {"em": src},
        ).fetchall()

        if not bookings:
            print(f"No booking found with attendee_email = {src!r}.")
            return 0

        # Resolve the correct lead — same logic as the resolver: most
        # recent nexus_leads attachment for the global lead matching the
        # corrected email.
        lead = db.execute(
            text(
                """SELECT gl.id        AS gl_id,
                          nl.id        AS nl_id,
                          nl.workspace_id,
                          nl.campaign_id,
                          c.name       AS campaign_name,
                          p.name       AS product_name
                     FROM nexus_global_leads gl
                     JOIN nexus_leads nl       ON nl.global_lead_id = gl.id
                     LEFT JOIN nexus_campaigns c ON c.id = nl.campaign_id
                     LEFT JOIN nexus_products  p ON p.id = c.product_id
                    WHERE LOWER(gl.email) = :em
                    ORDER BY nl.id DESC
                    LIMIT 1"""
            ),
            {"em": dst},
        ).first()

        if not lead:
            print(f"No lead found with email = {dst!r}. Cannot repoint.")
            return 1

        print()
        print("=" * 72)
        print("BOOKING EMAIL FIX")
        print("=" * 72)
        print(f"  from email  : {src}")
        print(f"  to email    : {dst}")
        print(f"  target lead : global_lead_id={lead.gl_id}  ws={lead.workspace_id}")
        print(f"               campaign={lead.campaign_name!r} (id={lead.campaign_id})")
        print(f"               product ={lead.product_name!r}")
        print()
        print(f"Affected bookings: {len(bookings)}")
        for bk in bookings:
            print(
                f"  booking id={bk.id}  ws={bk.workspace_id}  status={bk.status}  "
                f"current lead_id={bk.lead_id}  current campaign_id={bk.campaign_id}"
            )

        if not args.apply:
            print()
            print("DRY RUN — re-run with --apply to commit.")
            return 0

        repointed = 0
        cleared = 0
        for bk in bookings:
            # Sanity: don't cross workspaces.
            if bk.workspace_id != lead.workspace_id:
                print(
                    f"  SKIP booking {bk.id}: workspace mismatch "
                    f"(booking ws={bk.workspace_id}, lead ws={lead.workspace_id})"
                )
                continue
            db.execute(
                text(
                    """UPDATE nexus_demo_bookings
                          SET attendee_email = :em,
                              lead_id        = :gl,
                              campaign_id    = :cid,
                              updated_at     = NOW()
                        WHERE id = :bid"""
                ),
                {
                    "em": dst,
                    "gl": int(lead.gl_id),
                    "cid": int(lead.campaign_id) if lead.campaign_id is not None else None,
                    "bid": int(bk.id),
                },
            )
            repointed += 1
            d = db.execute(
                text("DELETE FROM nexus_demo_briefings WHERE demo_booking_id = :bid"),
                {"bid": int(bk.id)},
            )
            cleared += d.rowcount or 0

        db.commit()
        print()
        print("=" * 72)
        print("APPLIED")
        print("=" * 72)
        print(f"  bookings repointed : {repointed}")
        print(f"  briefings cleared  : {cleared}")
        print()
        print("Open the booking and the new briefing will regenerate against the correct product.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
