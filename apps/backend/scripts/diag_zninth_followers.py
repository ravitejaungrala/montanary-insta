"""One-off diagnostic: find where the 25,610 follower count on the
info@z-ninth.com Performance Trends chart is coming from.

Run from apps/backend with the venv activated:
    python scripts/diag_zninth_followers.py
"""
import os
import sys
from pathlib import Path

# Make `core` / `models` importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timedelta

from sqlalchemy import or_

from core.database import SessionLocal
from models import User, SocialAccount, UserAnalyticsSnapshot


TARGET_EMAIL = "Info@z-ninth.com"


def main() -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == TARGET_EMAIL).first()
        if not user:
            print(f"No exact-match user for {TARGET_EMAIL}; searching loose matches…")
            candidates = (
                db.query(User)
                  .filter(User.email.ilike("%z-ninth%") | User.email.ilike("%zninth%"))
                  .all()
            )
            print(f"  found {len(candidates)} candidate(s):")
            for c in candidates:
                print(f"    id={c.id} email={c.email} team_owner_id={c.team_owner_id}")
            if not candidates:
                # Last resort: list every user so we can spot the right one
                everyone = db.query(User.id, User.email).order_by(User.id.desc()).limit(40).all()
                print("  most recent 40 users in DB:")
                for u in everyone:
                    print(f"    id={u.id} email={u.email}")
                return
            user = candidates[0]
            print(f"Using {user.email} (id={user.id})")
        print(f"User id={user.id}  email={user.email}  team_owner_id={user.team_owner_id}")

        # Mirror the analytics handler's "team scope" so we look at the
        # same accounts the chart sees.
        if user.team_owner_id:
            visible_ids = [int(user.id)]
        else:
            member_ids = [
                int(m.id)
                for m in db.query(User.id).filter(User.team_owner_id == user.id).all()
            ]
            visible_ids = [int(user.id), *member_ids]
        print(f"Team scope visible_ids = {visible_ids}")

        accounts = (
            db.query(SocialAccount)
              .filter(or_(
                  SocialAccount.user_id.in_(visible_ids),
                  SocialAccount.assigned_to_user_id.in_(visible_ids),
              ))
              .all()
        )
        print()
        print("=== Live SocialAccount rows in scope ===")
        live_total = 0
        live_initial_total = 0
        for a in accounts:
            live_total += int(a.follower_count or 0)
            live_initial_total += int(a.initial_follower_count or 0)
            print(
                f"  acct id={a.id:<5} platform={a.platform:<10} name={a.name!r:<40} "
                f"user_id={a.user_id} assigned_to={a.assigned_to_user_id} "
                f"follower_count={a.follower_count} initial={a.initial_follower_count} "
                f"initial_connected_at={a.initial_connected_at}"
            )
        print(f"  --> live sum follower_count = {live_total}")
        print(f"  --> live sum initial_follower_count = {live_initial_total}")

        # Reproduce the exact prev_f computation from /analytics/summary so
        # we can prove where 25,610 comes from. The handler builds a
        # synthetic anchor snapshot whose total_followers = sum of
        # initial_follower_count and snapshot_date = earliest_connect, then
        # adds `new_additions_baseline` for accounts whose initial_connected_at
        # is AFTER prev_snap.snapshot_date — which double-counts every
        # account that wasn't first to connect.
        connect_dates = [a.initial_connected_at for a in accounts if a.initial_connected_at]
        earliest_connect = min(connect_dates) if connect_dates else None
        print()
        print(f"earliest_connect = {earliest_connect}")
        synthetic_total = sum(int(a.initial_follower_count or 0) for a in accounts)
        new_additions_baseline = sum(
            int(a.initial_follower_count or 0)
            for a in accounts
            if a.initial_connected_at and earliest_connect and a.initial_connected_at > earliest_connect
        )
        prev_f_with_bug = synthetic_total + new_additions_baseline
        print(f"synthetic anchor total_followers = {synthetic_total}")
        print(f"new_additions_baseline (initial of accounts connected AFTER earliest_connect) = {new_additions_baseline}")
        print(f"prev_f (with current bug) = {prev_f_with_bug}")
        print(f"chart shows: {prev_f_with_bug} on leading buckets -> matches the 25,610 spike on screenshot? {abs(prev_f_with_bug - 25610) < 100}")
        print(f"after fix (skip new_additions when synthetic anchor) prev_f = {synthetic_total}")

        # All snapshots in the last 7 days for this user.
        since = datetime.utcnow() - timedelta(days=7)
        snaps = (
            db.query(UserAnalyticsSnapshot)
              .filter(
                  UserAnalyticsSnapshot.user_id == user.id,
                  UserAnalyticsSnapshot.snapshot_date >= since,
              )
              .order_by(UserAnalyticsSnapshot.snapshot_date.asc())
              .all()
        )
        print()
        print(f"=== UserAnalyticsSnapshot rows for user {user.id} in last 7d ({len(snaps)} rows) ===")
        for s in snaps:
            print(
                f"  id={s.id:<6} date={s.snapshot_date} "
                f"total_followers={s.total_followers:<8} "
                f"engagement={s.total_engagement} reach={s.total_reach}"
            )
            bd = s.platform_breakdown or {}
            if bd:
                print(f"    platform_breakdown ({len(bd)} keys):")
                for k, v in sorted(bd.items()):
                    print(f"      {k:<30} = {v}")
            else:
                print("    platform_breakdown = (empty)")

        # Spotlight: snapshots whose total >= 20000 (the suspicious ones).
        bad = [s for s in snaps if (s.total_followers or 0) >= 20000]
        print()
        print(f"=== Anomalous snapshots (total_followers >= 20000): {len(bad)} ===")
        for s in bad:
            print(
                f"  id={s.id} date={s.snapshot_date} "
                f"total_followers={s.total_followers} platform_breakdown={s.platform_breakdown}"
            )

    finally:
        db.close()


if __name__ == "__main__":
    main()
