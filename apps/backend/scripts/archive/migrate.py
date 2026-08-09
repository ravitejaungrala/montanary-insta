import sqlite3
import os

db_path = 'pipelyt.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    # Check current columns
    cursor.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in cursor.fetchall()]
    print(f"Columns: {columns}")
    
    # Missing columns
    to_add = [
        ("full_name", "TEXT"),
        ("username", "TEXT"),
        ("profile_picture_url", "TEXT"),
        ("timezone", "TEXT DEFAULT 'UTC'"),
        ("company_name", "TEXT"),
        ("company_description", "TEXT"),
        ("product_details", "TEXT"),
        ("onboarded", "BOOLEAN DEFAULT 0"),
        ("business_url", "TEXT"),
        ("business_dna", "JSON"),
        ("pricing_plan", "TEXT DEFAULT 'free'")
    ]
    
    for col_name, col_type in to_add:
        if col_name not in columns:
            print(f"Adding {col_name}...")
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
    
    conn.commit()
    print("Done! ✅")
except Exception as e:
    print(f"Fail: {e}")
finally:
    conn.close()
