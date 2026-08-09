#!/usr/bin/env python3
"""
Read-only check: compare Mongo NEXUS users to PIPELYT Postgres users.
Reports which Mongo emails have matching PIPELYT accounts (= safe to migrate)
and which don't (= would be skipped by the migration script).
"""
from __future__ import annotations
import io, os, sys

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

import pymongo
from pymongo import ReadPreference
from core.database import engine
from sqlalchemy import text

MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb+srv://saisidd07:nexus123@cluster0.xiwxvnk.mongodb.net/?appName=Cluster0",
)
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "spenzo")

client = pymongo.MongoClient(MONGO_URI, read_preference=ReadPreference.SECONDARY_PREFERRED)
mdb = client[MONGO_DB_NAME]

print("=" * 70)
print("  Mongo NEXUS users vs PIPELYT users — match check")
print("=" * 70)

mongo_users = list(mdb["users"].find({}, {"email": 1, "name": 1, "role": 1}))
pg_emails = set()
with engine.connect() as c:
    rows = c.execute(text("SELECT lower(email) FROM users")).fetchall()
    pg_emails = {r[0] for r in rows}

print(f"\n  Mongo users: {len(mongo_users)}")
print(f"  PIPELYT users: {len(pg_emails)}")
print("\n  Mongo email                                  Status")
print("  " + "─" * 68)

matched = 0
unmatched = []
for u in mongo_users:
    email = (u.get("email") or "").lower().strip()
    name = u.get("name", "")
    role = u.get("role", "")
    if not email:
        print(f"  {'(no email)':<45} skipped — no email field")
        continue
    if email in pg_emails:
        print(f"  {email:<45} ✓ MATCHED — would migrate")
        matched += 1
    else:
        print(f"  {email:<45} ✗ NO PIPELYT ACCOUNT")
        unmatched.append({"email": email, "name": name, "role": role})

print("\n" + "─" * 70)
print(f"  Matched:   {matched}")
print(f"  Unmatched: {len(unmatched)}")
print("─" * 70)

if unmatched:
    print("\n  ⚠️  These Mongo users have NO PIPELYT account.")
    print("      If migration runs as-is, ALL their data will be skipped:")
    for u in unmatched:
        print(f"      - {u['email']:<35} ({u['name']}, role={u['role']})")
    print("\n  Fix options:")
    print("    A) Create matching PIPELYT users first (recommended)")
    print("    B) Add an email-remap dict to the migration script that maps")
    print("       these Mongo emails to existing PIPELYT user IDs")
    print()

client.close()
