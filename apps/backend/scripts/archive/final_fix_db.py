from sqlalchemy import text
from core.database import engine

def run_fix():
    sql_commands = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS product_url VARCHAR;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS business_url VARCHAR;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS business_dna JSONB;"
    ]
    
    with engine.connect() as conn:
        print("Checking and fixing users table schema...")
        for cmd in sql_commands:
            try:
                conn.execute(text(cmd))
                print(f"Executed: {cmd}")
            except Exception as e:
                print(f"Skipped/Error: {cmd} -> {e}")
        conn.commit()
    print("Database schema synchronization complete! 🚀")

if __name__ == "__main__":
    run_fix()
