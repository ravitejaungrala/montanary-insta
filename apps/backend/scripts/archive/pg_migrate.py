import sys
import os
from sqlalchemy import create_engine, text
from core.database import engine

def migrate():
    print("Starting Postgres Migration... 🚀")
    with engine.connect() as conn:
        # Check current columns
        res = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'users'"))
        columns = [row[0] for row in res.fetchall()]
        print(f"Existing columns: {columns}")

        to_add = [
            ("full_name", "TEXT"),
            ("username", "TEXT"),
            ("profile_picture_url", "TEXT"),
            ("timezone", "TEXT DEFAULT 'UTC'"),
            ("company_name", "TEXT"),
            ("company_description", "TEXT"),
            ("product_details", "TEXT"),
            ("onboarded", "BOOLEAN DEFAULT FALSE"),
            ("business_url", "TEXT"),
            ("business_dna", "JSONB"),
            ("pricing_plan", "TEXT DEFAULT 'free'")
        ]

        for col_name, col_type in to_add:
            if col_name not in columns:
                print(f"Adding column: {col_name} ({col_type})")
                try:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"))
                    conn.commit()
                    print(f"Successfully added {col_name} ✅")
                except Exception as e:
                    print(f"Error adding {col_name}: {e}")
            else:
                print(f"Column {col_name} already exists. Skipping.")

    print("Migration finished! 🎉")

if __name__ == "__main__":
    migrate()
