"""Dry-run: count what would be deleted if we hard-removed user 40 (shaiksalmanisha@gmail.com).
DOES NOT DELETE ANYTHING. Reports counts only."""
import os
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import create_engine, text, inspect

TARGET_EMAIL = "shaiksalmanisha@gmail.com"

eng = create_engine(os.environ["DATABASE_URL"])
insp = inspect(eng)

with eng.connect() as c:
    user = c.execute(text(
        "SELECT id, email, full_name, company_name, pricing_plan FROM users WHERE email = :e"
    ), {"e": TARGET_EMAIL}).fetchone()
    if not user:
        print(f"No user found with email {TARGET_EMAIL}")
        raise SystemExit(0)

    uid = user.id
    print(f"USER: id={uid}  email={user.email}  name={user.full_name}  company={user.company_name}  plan={user.pricing_plan}\n")

    # Tables we know reference users.id
    tables_to_check = [
        ("social_accounts", "user_id"),
        ("published_posts", "user_id"),
        ("published_post_platforms", None),  # via published_posts
        ("drafts", "user_id"),
        ("scheduled_posts", "user_id"),
        ("user_analytics_snapshots", "user_id"),
        ("user_otps", "user_id"),
        ("pending_oauth_sync", None),  # depends on schema
        ("reconnect_requests", None),
    ]

    all_tables = set(insp.get_table_names())
    print("Cascading impact:\n")
    total_rows_to_delete = 0
    for tbl, fk in tables_to_check:
        if tbl not in all_tables:
            continue
        cols = [col["name"] for col in insp.get_columns(tbl)]
        if fk and fk in cols:
            n = c.execute(text(f"SELECT COUNT(*) FROM {tbl} WHERE {fk} = :uid"), {"uid": uid}).scalar()
            if n is None: n = 0
            print(f"  {tbl:<35} ({fk:<20}={uid})  → {n} row(s)")
            total_rows_to_delete += n
        else:
            # try common fallback fk names
            for cand in ("user_id", "owner_id", "created_by", "requester_id"):
                if cand in cols:
                    n = c.execute(text(f"SELECT COUNT(*) FROM {tbl} WHERE {cand} = :uid"), {"uid": uid}).scalar() or 0
                    print(f"  {tbl:<35} ({cand:<20}={uid})  → {n} row(s)")
                    total_rows_to_delete += n
                    break
            else:
                print(f"  {tbl:<35} (no known fk to users)  → skipped")

    # Special case: published_post_platforms via parent published_posts
    if "published_posts" in all_tables and "published_post_platforms" in all_tables:
        n = c.execute(text(
            "SELECT COUNT(*) FROM published_post_platforms WHERE published_post_id IN "
            "(SELECT id FROM published_posts WHERE user_id = :uid)"
        ), {"uid": uid}).scalar() or 0
        print(f"  published_post_platforms (via posts)        → {n} row(s)")

    # Team membership: anyone whose team_owner_id = this user
    members_under = c.execute(text(
        "SELECT id, email FROM users WHERE team_owner_id = :uid"
    ), {"uid": uid}).fetchall()
    if members_under:
        print(f"\nWARNING: {len(members_under)} user(s) have this user as team_owner:")
        for m in members_under:
            print(f"     - id={m.id} {m.email}  (would be orphaned)")
    else:
        print(f"\nNo other users have this user as team_owner.")

    print(f"\n--- TOTAL rows that would be deleted: {total_rows_to_delete} (plus the user row itself) ---")
    print("\nNothing has been deleted. Re-run the dedicated delete script after explicit confirmation.")
