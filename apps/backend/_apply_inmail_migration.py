"""One-shot script to apply the InMail columns to nexus_linkedin_messages.

Pure ALTER TABLE ADD COLUMN IF NOT EXISTS — idempotent, no data change.
Safe to run any number of times. Delete this file after running.
"""
import sys
from dotenv import load_dotenv
load_dotenv()

from core.database import engine
from sqlalchemy import text

STATEMENTS = [
    "ALTER TABLE nexus_linkedin_messages ADD COLUMN IF NOT EXISTS variant VARCHAR(16) NOT NULL DEFAULT 'dm'",
    "ALTER TABLE nexus_linkedin_messages ADD COLUMN IF NOT EXISTS subject TEXT",
]

print("Applying InMail migration ...", flush=True)
with engine.begin() as conn:
    for s in STATEMENTS:
        print(f"  - {s[:70]}...", flush=True)
        conn.execute(text(s))
print("Done. Verifying ...", flush=True)

with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'nexus_linkedin_messages' "
        "  AND column_name IN ('variant', 'subject') "
        "ORDER BY column_name"
    )).fetchall()
    found = [r[0] for r in rows]
    print(f"Columns present: {found}")
    if 'variant' in found and 'subject' in found:
        print("SUCCESS - both columns exist.")
        sys.exit(0)
    else:
        print(f"FAIL - missing: {set(['variant','subject']) - set(found)}")
        sys.exit(1)
