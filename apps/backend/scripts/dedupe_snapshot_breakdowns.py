"""dedupe_snapshot_breakdowns.py — one-off cleanup for stale duplicate
entries in `UserAnalyticsSnapshot.platform_breakdown`.

WHY THIS EXISTS
---------------
A re-connect can leave two `SocialAccount` rows for the same logical
platform page (same `account_id` URN, different DB primary keys).
While both rows are live, the analytics sync writes a breakdown entry
for each — `linkedin_5: 444` AND `linkedin_8: 444` — which doubles the
follower count for that page in every snapshot taken during that
window. After the duplicate is eventually cleaned up, the breakdown
keeps the inflated entries forever, producing the visible "452 → 904 →
452" cliff on the Performance Trends chart.

This script walks every snapshot, groups breakdown entries by
(platform, native account_id), keeps the largest value of each group
(matching the runtime read-side fix), and rewrites both
`platform_breakdown` and `total_followers` so the data on disk no
longer needs the runtime clamp.

USAGE
-----
    cd apps/backend
    source venv/Scripts/activate    # Windows / Git Bash
    python scripts/dedupe_snapshot_breakdowns.py --dry-run
    python scripts/dedupe_snapshot_breakdowns.py            # apply

The script is idempotent — running it twice is harmless. Snapshots
that are already clean (no duplicate native ids) are left untouched.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Tuple

# Make the project's modules importable when running from any directory.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from core.database import SessionLocal  # noqa: E402
from models import SocialAccount, UserAnalyticsSnapshot  # noqa: E402


def _build_native_lookup(db: Session) -> Dict[int, Tuple[str, str]]:
    """Map every SocialAccount.id → (platform, native account_id) for the
    currently live rows. Snapshots that reference deleted db_ids will
    fall through to a synthetic native key so they still contribute
    once (without merging with live duplicates)."""
    lookup: Dict[int, Tuple[str, str]] = {}
    for a in db.query(SocialAccount).all():
        lookup[int(a.id)] = (str(a.platform or "").lower(), str(a.account_id or ""))
    return lookup


def _dedupe_breakdown(
    breakdown: dict,
    native_lookup: Dict[int, Tuple[str, str]],
) -> Tuple[dict, int]:
    """Group entries by (platform, native_account_id), keep the MAX of
    each group. Returns the cleaned breakdown plus the recomputed
    follower total."""
    if not isinstance(breakdown, dict):
        return {}, 0

    # First pass: assign each breakdown entry to a (platform, native_id)
    # group. Stale entries whose db_id no longer exists in
    # SocialAccount get a synthetic native key so they don't merge with
    # live accounts on the same platform — they still contribute once.
    groups: Dict[Tuple[str, str], list] = {}
    for k, v in breakdown.items():
        try:
            parts = str(k).rsplit("_", 1)
            if len(parts) != 2:
                continue
            key_plat = parts[0].lower()
            key_acc = int(parts[1])
            val = int(v or 0)
        except Exception:
            continue
        native = native_lookup.get(key_acc, (key_plat, f"_legacy_{key_acc}"))
        groups.setdefault(native, []).append((k, val))

    # Second pass: pick the canonical entry per group (max value). We
    # keep the ORIGINAL key with the max value so existing analytics
    # code that does keyed lookups (`breakdown[f"{plat}_{db_id}"]`)
    # still finds the right row.
    cleaned: Dict[str, int] = {}
    for entries in groups.values():
        # max by value, ties broken by lexicographic key (stable).
        best_k, best_v = max(entries, key=lambda kv: (kv[1], kv[0]))
        cleaned[best_k] = best_v

    total = sum(cleaned.values())
    return cleaned, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing to the database.",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="Optional: only clean snapshots for this user_id.",
    )
    args = parser.parse_args()

    db: Session = SessionLocal()
    try:
        native_lookup = _build_native_lookup(db)
        print(f"Loaded native-id lookup for {len(native_lookup)} live SocialAccount rows.")

        q = db.query(UserAnalyticsSnapshot)
        if args.user_id is not None:
            q = q.filter(UserAnalyticsSnapshot.user_id == args.user_id)

        snapshots = q.order_by(UserAnalyticsSnapshot.snapshot_date.asc()).all()
        print(f"Found {len(snapshots)} snapshot(s) to inspect.")

        changed = 0
        for snap in snapshots:
            old_breakdown = snap.platform_breakdown or {}
            old_total = int(snap.total_followers or 0)

            new_breakdown, new_total = _dedupe_breakdown(old_breakdown, native_lookup)

            removed_keys = set(old_breakdown.keys()) - set(new_breakdown.keys())
            if not removed_keys and new_total == old_total:
                continue  # Already clean.

            changed += 1
            tag = "[DRY-RUN]" if args.dry_run else "[FIX]"
            print(
                f"{tag} snap id={snap.id} user_id={snap.user_id} "
                f"date={snap.snapshot_date.isoformat() if snap.snapshot_date else '?'} "
                f"total: {old_total} → {new_total} "
                f"({len(old_breakdown)} → {len(new_breakdown)} entries; "
                f"dropped: {sorted(removed_keys)[:5]}{'…' if len(removed_keys) > 5 else ''})"
            )

            if not args.dry_run:
                snap.platform_breakdown = new_breakdown
                snap.total_followers = new_total

        if args.dry_run:
            print(f"\nDry-run complete. {changed} snapshot(s) WOULD be updated.")
        else:
            db.commit()
            print(f"\nDone. {changed} snapshot(s) updated.")
        return 0
    except Exception as e:
        db.rollback()
        print(f"ERROR: {e!r}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
