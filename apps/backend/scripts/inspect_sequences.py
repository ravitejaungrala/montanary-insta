"""Read-only inspector for nexus_sequences. Prints every row's id, name,
step_count, AND full steps JSONB content. Makes ZERO writes — pure SELECT.

Run:
    cd apps/backend
    source venv/Scripts/activate
    python scripts/inspect_sequences.py
"""

from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text


def main() -> int:
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not found in .env")
        return 1
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

    engine = create_engine(db_url)
    with engine.connect() as conn:
        host = engine.url.host
        db = engine.url.database
        print(f"Connected to: {host}/{db}\n")

        # Summary first
        print("======== SUMMARY ========")
        summary = conn.execute(
            text(
                """SELECT jsonb_array_length(steps) AS step_count, COUNT(*) AS rows
                   FROM nexus_sequences
                   GROUP BY 1 ORDER BY 1"""
            )
        ).fetchall()
        if not summary:
            print("  (table is empty)")
            return 0
        for step_count, row_count in summary:
            print(f"  step_count={step_count}  ->{row_count} row(s)")

        # Row-by-row listing
        print("\n======== ROWS ========")
        rows = conn.execute(
            text(
                """SELECT id, name, workspace_id, campaign_id,
                          jsonb_array_length(steps) AS step_count, steps
                   FROM nexus_sequences
                   ORDER BY id"""
            )
        ).fetchall()
        for r in rows:
            sid, name, wid, cid, step_count, steps = r
            print(
                f"\n-- id={sid} | workspace={wid} | campaign={cid} | "
                f"steps={step_count} | name={name!r} --"
            )
            # arrow replaced with ascii to keep Windows cp1252 happy

            print(json.dumps(steps, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
