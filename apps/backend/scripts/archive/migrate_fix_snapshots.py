import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not found in .env")
    exit(1)

# Correct the URL if it starts with postgres://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

print(f"Connecting to: {DATABASE_URL.split('@')[-1]}")
engine = create_engine(DATABASE_URL)

def migrate():
    with engine.begin() as conn:
        # Check existing columns for user_analytics_snapshots
        result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'user_analytics_snapshots'"))
        existing_columns = [row[0] for row in result]
        
        if 'platform_breakdown' not in existing_columns:
            print("Adding column: platform_breakdown (JSONB)")
            # Using JSONB for better performance and flexibility in PostgreSQL
            conn.execute(text("ALTER TABLE user_analytics_snapshots ADD COLUMN platform_breakdown JSONB"))
            print("Column added successfully!")
        else:
            print("Column platform_breakdown already exists.")

    print("Migration Complete! ✅")

if __name__ == "__main__":
    try:
        migrate()
    except Exception as e:
        print(f"Migration Failed: {e}")
