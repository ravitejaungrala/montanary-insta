"""realign_demo_bookings.py — second-pass cleanup for DemoBookings
whose campaign_id no longer matches their lead's current nexus_leads
attachment.

WHY THIS EXISTS
---------------
dedupe_nexus_leads.py collapses duplicate nexus_leads rows and repoints
any DemoBooking that pointed at a now-deleted (loser) campaign. It does
NOT handle bookings whose campaign_id was set incorrectly in the first
place — e.g. an MS Bookings sync that picked the wrong campaign at
ingest time, leaving the booking attributed to a campaign that the
lead is no longer (or was never) in.

Symptom: lead row shows product X, demo briefing shows product Y.
Root: booking.campaign_id points at Y's campaign, lead's only
nexus_leads row points at X's campaign.

This script finds those mismatches and (with --apply) repoints the
booking to the lead's current campaign + clears the stale briefing so
the next /briefing GET regenerates against the correct product.

USAGE
-----
    cd apps/backend
    source venv/Scripts/activate
    python scripts/realign_demo_bookings.py                       # dry run
    python scripts/realign_demo_bookings.py --workspace 5         # scoped
    python scripts/realign_demo_bookings.py --workspace 5 --apply # commit
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


# Drift query: bookings whose campaign_id disagrees with the lead's
# current nexus_leads attachment. Joined two ways so we can show
# operator both the wrong and the right product.
DRIFT_SQL = """
    SELECT
        b.id              AS booking_id,
        b.workspace_id    AS workspace_id,
        b.lead_id         AS global_lead_id,
        b.attendee_email  AS attendee_email,
        b.attendee_name   AS attendee_name,
        b.campaign_id     AS wrong_campaign_id,
        wp.name           AS wrong_product,
        nl.campaign_id    AS right_campaign_id,
        rp.name           AS right_product
      FROM nexus_demo_bookings b
      JOIN nexus_leads nl
        ON nl.workspace_id = b.workspace_id
       AND nl.global_lead_id = b.lead_id
      LEFT JOIN nexus_campaigns wc ON wc.id = b.campaign_id
      LEFT JOIN nexus_products  wp ON wp.id = wc.product_id
      LEFT JOIN nexus_campaigns rc ON rc.id = nl.campaign_id
      LEFT JOIN nexus_products  rp ON rp.id = rc.product_id
     WHERE b.lead_id IS NOT NULL
       AND nl.campaign_id IS NOT NULL
       AND (b.campaign_id IS NULL OR b.campaign_id <> nl.campaign_id)
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--workspace", type=int, default=None)
    parser.add_argument("--sample", type=int, default=30)
    args = parser.parse_args()

    extra = ""
    params: dict = {}
    if args.workspace is not None:
        extra = " AND b.workspace_id = :ws"
        params["ws"] = int(args.workspace)

    db = SessionLocal()
    try:
        rows = db.execute(text(DRIFT_SQL + extra), params).fetchall()

        print()
        print("=" * 80)
        print("BOOKING ↔ LEAD CAMPAIGN DRIFT — READ ONLY" if not args.apply else
              "BOOKING ↔ LEAD CAMPAIGN DRIFT — APPLYING")
        print("=" * 80)
        print(f"  drifted bookings : {len(rows)}")
        print()

        if not rows:
            print("  No drift. Briefings will resolve to the right product.")
            return 0

        print(f"Sample (first {min(args.sample, len(rows))} of {len(rows)}):")
        print()
        print(
            f"  {'bk_id':>6}  {'lead':>5}  {'email':40}  {'wrong':14} -> {'right':14}"
        )
        print("  " + "-" * 96)
        for r in rows[: args.sample]:
            email = (r.attendee_email or "")[:40]
            wrong = (r.wrong_product or f"c={r.wrong_campaign_id}")[:14]
            right = (r.right_product or f"c={r.right_campaign_id}")[:14]
            print(
                f"  {int(r.booking_id):>6}  {int(r.global_lead_id):>5}  "
                f"{email:40}  {wrong:14} -> {right:14}"
            )

        if not args.apply:
            print()
            print("DRY RUN — no changes. Re-run with --apply to fix.")
            return 0

        # Apply: update booking.campaign_id to the lead's current campaign
        # and drop the stale briefing so the next read regenerates.
        booking_ids = [int(r.booking_id) for r in rows]

        del_res = db.execute(
            text("DELETE FROM nexus_demo_briefings WHERE demo_booking_id = ANY(:ids)"),
            {"ids": booking_ids},
        )
        cleared = del_res.rowcount or 0

        repointed = 0
        for r in rows:
            upd = db.execute(
                text(
                    """
                    UPDATE nexus_demo_bookings
                       SET campaign_id = :cid, updated_at = NOW()
                     WHERE id = :bid
                    """
                ),
                {"cid": int(r.right_campaign_id), "bid": int(r.booking_id)},
            )
            repointed += upd.rowcount or 0

        db.commit()

        print()
        print("=" * 80)
        print("APPLIED")
        print("=" * 80)
        print(f"  repointed bookings    : {repointed}")
        print(f"  cleared briefings     : {cleared}")
        print()
        print("Open the affected bookings in the UI — the briefing will regenerate")
        print("against the correct product on next /briefing GET.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
