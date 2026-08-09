"""debug_booking_briefing.py — print everything we know about a single
demo booking + what the briefing resolver will return for it.

Use when a regenerated briefing keeps coming back with the wrong product.

USAGE
-----
    cd apps/backend
    source venv/Scripts/activate
    python scripts/debug_booking_briefing.py --email cmclain@libnat.com
    python scripts/debug_booking_briefing.py --booking-id 42
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
    parser.add_argument("--email")
    parser.add_argument("--booking-id", type=int)
    args = parser.parse_args()

    if not (args.email or args.booking_id):
        print("Pass --email or --booking-id")
        return 1

    db = SessionLocal()
    try:
        if args.booking_id:
            bookings = db.execute(
                text("SELECT * FROM nexus_demo_bookings WHERE id = :id"),
                {"id": args.booking_id},
            ).fetchall()
        else:
            bookings = db.execute(
                text(
                    "SELECT * FROM nexus_demo_bookings "
                    "WHERE LOWER(attendee_email) = :em ORDER BY id DESC"
                ),
                {"em": (args.email or "").lower()},
            ).fetchall()

        if not bookings:
            print("No booking found.")
            return 0

        for bk in bookings:
            m = bk._mapping
            print()
            print("=" * 80)
            print(f"BOOKING id={m['id']}  workspace={m['workspace_id']}")
            print("=" * 80)
            print(f"  attendee_email    : {m.get('attendee_email')}")
            print(f"  attendee_name     : {m.get('attendee_name')}")
            print(f"  status            : {m.get('status')}")
            print(f"  scheduled_at      : {m.get('scheduled_at')}")
            print(f"  lead_id (b)       : {m.get('lead_id')}")
            print(f"  campaign_id (b)   : {m.get('campaign_id')}")

            # What product is the booking's campaign_id pointing at?
            if m.get("campaign_id"):
                r = db.execute(
                    text(
                        """SELECT c.id, c.name AS campaign, p.id AS pid, p.name AS product
                             FROM nexus_campaigns c
                             LEFT JOIN nexus_products p ON p.id = c.product_id
                            WHERE c.id = :cid"""
                    ),
                    {"cid": int(m["campaign_id"])},
                ).first()
                if r:
                    print(
                        f"  --> booking.campaign={r.campaign!r} (id={r.id})  "
                        f"product={r.product!r} (id={r.pid})   <- step-1 of resolver"
                    )
                else:
                    print(f"  --> booking.campaign_id={m['campaign_id']} NOT FOUND in nexus_campaigns")

            # What does the lead's current nexus_leads say?
            print()
            print("  --- lead lookup ---")
            gl_id = m.get("lead_id")
            if not gl_id and m.get("attendee_email"):
                r = db.execute(
                    text("SELECT id FROM nexus_global_leads WHERE LOWER(email) = :em LIMIT 1"),
                    {"em": (m["attendee_email"] or "").lower()},
                ).first()
                if r:
                    gl_id = int(r.id)
                    print(f"  booking.lead_id is NULL — email matches global_lead id={gl_id}")
                else:
                    print(f"  booking.lead_id is NULL — email matches NO global_lead")
                    continue

            if gl_id:
                nl_rows = db.execute(
                    text(
                        """SELECT nl.id, nl.workspace_id, nl.campaign_id,
                                  c.name AS campaign, p.id AS pid, p.name AS product
                             FROM nexus_leads nl
                             LEFT JOIN nexus_campaigns c ON c.id = nl.campaign_id
                             LEFT JOIN nexus_products  p ON p.id = c.product_id
                            WHERE nl.global_lead_id = :gl
                            ORDER BY nl.id DESC"""
                    ),
                    {"gl": int(gl_id)},
                ).fetchall()
                if not nl_rows:
                    print(f"  no nexus_leads row exists for global_lead {gl_id}")
                else:
                    for nl in nl_rows:
                        print(
                            f"  nexus_leads id={nl.id}  ws={nl.workspace_id}  "
                            f"campaign_id={nl.campaign_id}  campaign={nl.campaign!r}  "
                            f"product={nl.product!r} (id={nl.pid})"
                        )

            # Existing briefing row?
            print()
            print("  --- existing briefing ---")
            br = db.execute(
                text("SELECT id, status, generated_at, regenerated_at FROM nexus_demo_briefings WHERE demo_booking_id = :id"),
                {"id": int(m["id"])},
            ).first()
            if br:
                print(f"  briefing id={br.id}  status={br.status}  "
                      f"generated={br.generated_at}  regenerated={br.regenerated_at}")
            else:
                print("  no briefing row (will be generated on next GET)")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())