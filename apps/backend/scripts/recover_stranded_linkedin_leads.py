"""recover_stranded_linkedin_leads.py — find and re-arm LinkedIn leads that
are ACTIVE but have no due-key, so nothing will ever dispatch them again.

WHY THIS EXISTS
---------------
`scheduler.dispatch_due` clears `next_action_at` when it enqueues a job, and
the due query filters on `next_action_at IS NOT NULL`. So the pair

    linkedin_sequence_status = 'active'  AND  next_action_at IS NULL

means "a job was dispatched and the worker never rescheduled the lead" — the
lead is stranded: invisible (it still reads as active) and unreachable (no
query will pick it up).

Before the defer-on-failure fix, several worker paths returned without
rescheduling:
  - any raised exception (since commit b9c5cbb the Fargate worker DELETES the
    SQS message on failure instead of redelivering, so nothing retried)
  - account_not_active / cooldown / daily_cap_reached / session_expired
  - account_locked (another worker held the per-account lock)
  - inmail_skipped_no_credit

Those paths now defer or stop explicitly. This script cleans up the leads that
were stranded before the fix landed.

A short window is EXPECTED and healthy: a lead sits in this state between
dispatch and the worker finishing its job. `--min-age-minutes` (default 60)
skips those so we never re-arm a job that is legitimately in flight — which
would double-send.

USAGE
-----
    cd apps/backend
    python scripts/recover_stranded_linkedin_leads.py            # dry run
    python scripts/recover_stranded_linkedin_leads.py --apply
    python scripts/recover_stranded_linkedin_leads.py --apply --min-age-minutes 30
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

# Re-arm staggered over this many minutes so a large recovery doesn't dump every
# lead into one dispatch tick (which the per-account caps would reject anyway).
_SPREAD_MINUTES = 240


FIND_SQL = """
    SELECT s.id, s.workspace_id, s.lead_id, s.campaign_id,
           s.linkedin_current_step, s.linkedin_current_branch,
           s.updated_at, s.defer_reason,
           (SELECT j.error_message
              FROM gtm_linkedin_jobs j
             WHERE j.lead_id = s.lead_id AND j.campaign_id = s.campaign_id
             ORDER BY j.id DESC LIMIT 1) AS last_error
      FROM gtm_linkedin_lead_state s
     WHERE s.linkedin_sequence_status = 'active'
       AND s.next_action_at IS NULL
       AND s.updated_at < NOW() - (:age || ' minutes')::interval
     ORDER BY s.updated_at ASC
"""

# Stagger via id so repeated runs are stable, and keep every slot in the future.
REARM_SQL = """
    UPDATE gtm_linkedin_lead_state
       SET next_action_at = NOW() + ((id %% :spread) || ' minutes')::interval,
           defer_reason   = 'recovered_stranded',
           updated_at     = NOW()
     WHERE linkedin_sequence_status = 'active'
       AND next_action_at IS NULL
       AND updated_at < NOW() - (:age || ' minutes')::interval
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="Actually re-arm. Without it, this only reports.")
    ap.add_argument("--min-age-minutes", type=int, default=60,
                    help="Ignore leads stranded for less than this (default 60). "
                         "Guards against re-arming a job that is still running.")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        print("=" * 72)
        print("Stranded LinkedIn leads (active, but no next_action_at)")
        print("=" * 72)
        print(f"Minimum age : {args.min_age_minutes} min")
        print(f"Mode        : {'APPLY' if args.apply else 'DRY RUN'}")
        print()

        try:
            rows = db.execute(text(FIND_SQL), {"age": args.min_age_minutes}).mappings().all()
        except Exception as e:
            print(f"Query failed (tables missing / pre-migration?): {e}")
            return 1

        if not rows:
            print("No stranded leads. Nothing to do.")
            return 0

        print(f"Found {len(rows)} stranded lead(s):")
        print()
        by_step: dict = {}
        for r in rows[:40]:
            print(f"  lead={r['lead_id']:<8} campaign={r['campaign_id']:<6} "
                  f"step={str(r['linkedin_current_step'] or '?'):<22} "
                  f"stuck_since={r['updated_at']}")
            if r["last_error"]:
                print(f"      last error: {str(r['last_error'])[:110]}")
        if len(rows) > 40:
            print(f"  … and {len(rows) - 40} more")
        print()

        for r in rows:
            k = r["linkedin_current_step"] or "(none)"
            by_step[k] = by_step.get(k, 0) + 1
        print("By step:")
        for k, v in sorted(by_step.items(), key=lambda kv: -kv[1]):
            print(f"  {v:>5}  {k}")
        print()

        if not args.apply:
            print("DRY RUN — re-run with --apply to re-arm these leads.")
            return 0

        res = db.execute(text(REARM_SQL),
                         {"age": args.min_age_minutes, "spread": _SPREAD_MINUTES})
        db.commit()
        print(f"Re-armed {res.rowcount} lead(s), staggered over {_SPREAD_MINUTES} min.")
        print("They will be picked up by the next scheduler tick (subject to")
        print("working hours and daily caps, as normal).")
        return 0
    except Exception as e:
        db.rollback()
        print(f"FAILED — rolled back: {e}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
