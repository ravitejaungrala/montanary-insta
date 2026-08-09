"""dedupe_nexus_leads.py — one-off cleanup for leads enrolled in
multiple campaigns inside the same workspace.

WHY THIS EXISTS
---------------
A single global lead (email) can have several `nexus_leads` rows under
the same workspace, one per campaign. That breaks demo-booking product
attribution: `_resolve_lead_and_workspace` (and the journey query) pick
"most recent attachment by nexus_leads.id" with no awareness of which
campaign actually drove the booking. A lead enrolled in Pipelyt first
and Spenzo second gets a Spenzo briefing even though the demo came from
the Pipelyt outreach.

This script keeps the most-recent `nexus_leads` row per
`(workspace_id, global_lead_id)` and deletes the older ones. It also
repoints any `nexus_demo_bookings.campaign_id` that was attributed to a
now-deleted campaign over to the surviving campaign, and clears the
stale `nexus_demo_briefings` rows for those bookings so the briefing
regenerates against the correct product on next open.

WHAT GETS DELETED / MUTATED (when --apply is set)
-------------------------------------------------
- nexus_leads:               loser rows (older `id`s) for any
                             (workspace, global_lead) tuple with >1 attachment.
- nexus_intent_signals:      auto-cascades via FK ON DELETE CASCADE.
- nexus_demo_bookings:       `campaign_id` updated to the survivor campaign
                             when it currently points at a loser campaign for
                             the same (workspace, lead) pair.
- nexus_demo_briefings:      rows deleted for any booking we just repointed,
                             so the next /briefing read regenerates against
                             the correct product.

WHAT IS UNTOUCHED
-----------------
- nexus_global_leads — the canonical lead identity stays.
- nexus_outreach, nexus_voice_calls, nexus_linkedin_messages,
  nexus_inbound_threads, nexus_touchpoints, nexus_lead_sequences —
  these store global_lead_id in their `lead_id` column, not
  nexus_leads.id, so deleting nexus_leads attachments does not affect
  outreach history.
- nexus_campaigns and nexus_products themselves.

USAGE
-----
    cd apps/backend
    source venv/Scripts/activate              # Windows / Git Bash
    python scripts/dedupe_nexus_leads.py                  # dry run (default)
    python scripts/dedupe_nexus_leads.py --workspace 7    # scope to one ws
    python scripts/dedupe_nexus_leads.py --apply          # actually delete

Dry-run is read-only. With --apply, everything happens inside one
transaction; if anything fails midway, the whole thing rolls back.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from core.database import SessionLocal  # noqa: E402


# Loser-set query: every nexus_leads row that is NOT the highest-id
# attachment for its (workspace_id, global_lead_id) tuple.
LOSERS_SQL = """
    SELECT
        nl.id              AS loser_id,
        nl.workspace_id    AS workspace_id,
        nl.global_lead_id  AS global_lead_id,
        nl.campaign_id     AS loser_campaign_id,
        survivor.id        AS survivor_id,
        survivor.campaign_id AS survivor_campaign_id
    FROM nexus_leads nl
    JOIN LATERAL (
        SELECT s.id, s.campaign_id
          FROM nexus_leads s
         WHERE s.workspace_id   = nl.workspace_id
           AND s.global_lead_id = nl.global_lead_id
         ORDER BY s.id DESC
         LIMIT 1
    ) survivor ON TRUE
    WHERE nl.id <> survivor.id
"""


def _scope_filter(workspace_id: int | None) -> tuple[str, dict]:
    """Add an optional workspace filter to LOSERS_SQL."""
    if workspace_id is None:
        return "", {}
    return " AND nl.workspace_id = :ws", {"ws": int(workspace_id)}


def audit(db, workspace_id: int | None) -> dict:
    extra, params = _scope_filter(workspace_id)
    rows = db.execute(text(LOSERS_SQL + extra), params).fetchall()

    # Group by (workspace, global_lead) for the human-readable report.
    grouped: dict[tuple[int, int], list[dict]] = defaultdict(list)
    survivor_by_lead: dict[tuple[int, int], dict] = {}
    for r in rows:
        key = (int(r.workspace_id), int(r.global_lead_id))
        grouped[key].append(
            {
                "loser_id": int(r.loser_id),
                "loser_campaign_id": (
                    int(r.loser_campaign_id) if r.loser_campaign_id is not None else None
                ),
            }
        )
        survivor_by_lead[key] = {
            "survivor_id": int(r.survivor_id),
            "survivor_campaign_id": (
                int(r.survivor_campaign_id) if r.survivor_campaign_id is not None else None
            ),
        }

    # How many demo bookings would get their campaign_id repointed?
    bookings_to_repoint = 0
    briefings_to_clear = 0
    if grouped:
        per_lead_losers = {
            key: [d["loser_campaign_id"] for d in vs if d["loser_campaign_id"] is not None]
            for key, vs in grouped.items()
        }
        for (ws, gl_id), loser_camps in per_lead_losers.items():
            if not loser_camps:
                continue
            survivor_camp = survivor_by_lead[(ws, gl_id)]["survivor_campaign_id"]
            cnt = db.execute(
                text(
                    """
                    SELECT COUNT(*) FROM nexus_demo_bookings
                     WHERE workspace_id = :w
                       AND lead_id = :gl
                       AND campaign_id = ANY(:lc)
                       AND (:sc IS NULL OR campaign_id <> :sc)
                    """
                ),
                {"w": ws, "gl": gl_id, "lc": loser_camps, "sc": survivor_camp},
            ).scalar()
            bookings_to_repoint += int(cnt or 0)

            cnt2 = db.execute(
                text(
                    """
                    SELECT COUNT(*) FROM nexus_demo_briefings db
                      JOIN nexus_demo_bookings b ON b.id = db.demo_booking_id
                     WHERE b.workspace_id = :w
                       AND b.lead_id = :gl
                       AND b.campaign_id = ANY(:lc)
                       AND (:sc IS NULL OR b.campaign_id <> :sc)
                    """
                ),
                {"w": ws, "gl": gl_id, "lc": loser_camps, "sc": survivor_camp},
            ).scalar()
            briefings_to_clear += int(cnt2 or 0)

    return {
        "grouped": grouped,
        "survivors": survivor_by_lead,
        "loser_rows_total": sum(len(v) for v in grouped.values()),
        "affected_leads": len(grouped),
        "bookings_to_repoint": bookings_to_repoint,
        "briefings_to_clear": briefings_to_clear,
    }


def print_report(report: dict, sample: int = 20) -> None:
    grouped = report["grouped"]
    print()
    print("=" * 72)
    print("DUPLICATE nexus_leads AUDIT")
    print("=" * 72)
    print(f"  affected (workspace, global_lead) tuples : {report['affected_leads']}")
    print(f"  total loser nexus_leads rows             : {report['loser_rows_total']}")
    print(f"  demo bookings to repoint                 : {report['bookings_to_repoint']}")
    print(f"  demo briefings to clear (regenerate)     : {report['briefings_to_clear']}")
    print()
    if not grouped:
        print("  No duplicates found. Nothing to do.")
        return

    print(f"Sample (first {min(sample, len(grouped))} of {len(grouped)}):")
    print()
    print(
        f"  {'workspace':>9}  {'global_lead':>11}  {'survivor':>20}  losers"
    )
    print("  " + "-" * 70)
    for (ws, gl_id), losers in list(grouped.items())[:sample]:
        surv = report["survivors"][(ws, gl_id)]
        survivor_disp = f"id={surv['survivor_id']}/c={surv['survivor_campaign_id']}"
        loser_disp = ", ".join(
            f"id={d['loser_id']}/c={d['loser_campaign_id']}" for d in losers
        )
        print(f"  {ws:>9}  {gl_id:>11}  {survivor_disp:>20}  {loser_disp}")


def apply_dedup(db, workspace_id: int | None) -> dict:
    """Run the destructive cleanup inside a single transaction."""
    extra, params = _scope_filter(workspace_id)

    # Pull the full loser set up front so we can repoint bookings BEFORE the
    # cascade nukes anything we still need to read.
    loser_rows = db.execute(text(LOSERS_SQL + extra), params).fetchall()
    if not loser_rows:
        print("Nothing to do.")
        return {"deleted_leads": 0, "repointed_bookings": 0, "cleared_briefings": 0}

    # Group losers per (ws, global_lead) so we can compute the survivor
    # campaign for each affected booking.
    losers_by_lead: dict[tuple[int, int], dict] = defaultdict(
        lambda: {"loser_campaigns": [], "survivor_campaign": None, "loser_ids": []}
    )
    for r in loser_rows:
        key = (int(r.workspace_id), int(r.global_lead_id))
        bucket = losers_by_lead[key]
        bucket["loser_ids"].append(int(r.loser_id))
        if r.loser_campaign_id is not None:
            bucket["loser_campaigns"].append(int(r.loser_campaign_id))
        if r.survivor_campaign_id is not None:
            bucket["survivor_campaign"] = int(r.survivor_campaign_id)

    repointed = 0
    cleared = 0

    # 1) For each affected lead, clear stale demo briefings then repoint
    #    the bookings to the survivor campaign. Briefings are cleared
    #    FIRST because they reference the booking; clearing them after
    #    UPDATE would still work but doing it before keeps the intent
    #    crisp.
    for (ws, gl_id), bucket in losers_by_lead.items():
        loser_camps = bucket["loser_campaigns"]
        if not loser_camps:
            continue
        survivor_camp = bucket["survivor_campaign"]

        # Clear briefings for bookings that will be repointed.
        del_res = db.execute(
            text(
                """
                DELETE FROM nexus_demo_briefings
                 WHERE demo_booking_id IN (
                    SELECT id FROM nexus_demo_bookings
                     WHERE workspace_id = :w
                       AND lead_id = :gl
                       AND campaign_id = ANY(:lc)
                       AND (:sc IS NULL OR campaign_id <> :sc)
                 )
                """
            ),
            {"w": ws, "gl": gl_id, "lc": loser_camps, "sc": survivor_camp},
        )
        cleared += del_res.rowcount or 0

        # Repoint the bookings themselves.
        upd_res = db.execute(
            text(
                """
                UPDATE nexus_demo_bookings
                   SET campaign_id = :sc,
                       updated_at = NOW()
                 WHERE workspace_id = :w
                   AND lead_id = :gl
                   AND campaign_id = ANY(:lc)
                   AND (:sc IS NULL OR campaign_id <> :sc)
                """
            ),
            {"w": ws, "gl": gl_id, "lc": loser_camps, "sc": survivor_camp},
        )
        repointed += upd_res.rowcount or 0

    # 2) Delete the loser nexus_leads rows. nexus_intent_signals cascade-deletes.
    all_loser_ids = [r.loser_id for r in loser_rows]
    deleted = 0
    # Delete in chunks to keep parameter counts sane for very large prod sets.
    CHUNK = 1000
    for i in range(0, len(all_loser_ids), CHUNK):
        chunk = all_loser_ids[i : i + CHUNK]
        res = db.execute(
            text("DELETE FROM nexus_leads WHERE id = ANY(:ids)"),
            {"ids": [int(x) for x in chunk]},
        )
        deleted += res.rowcount or 0

    db.commit()
    return {
        "deleted_leads": deleted,
        "repointed_bookings": repointed,
        "cleared_briefings": cleared,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete loser rows and repoint bookings. Default is dry-run.",
    )
    parser.add_argument(
        "--workspace",
        type=int,
        default=None,
        help="Optional: scope the cleanup to a single workspace_id.",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=20,
        help="How many sample groups to print in the dry-run report.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        report = audit(db, args.workspace)
        print_report(report, sample=args.sample)

        if not args.apply:
            print()
            print("DRY RUN — no changes written. Re-run with --apply to commit.")
            return 0

        if report["loser_rows_total"] == 0:
            return 0

        print()
        print("Applying changes…")
        result = apply_dedup(db, args.workspace)
        print()
        print("=" * 72)
        print("APPLIED")
        print("=" * 72)
        print(f"  deleted nexus_leads rows : {result['deleted_leads']}")
        print(f"  repointed demo bookings  : {result['repointed_bookings']}")
        print(f"  cleared demo briefings   : {result['cleared_briefings']}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
