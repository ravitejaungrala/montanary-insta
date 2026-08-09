import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("DATABASE_URL not found in .env")
    exit(1)

engine = create_engine(DATABASE_URL)

def update_db():
    with engine.connect() as conn:
        print("Checking and updating 'users' table for Canva integration...")
        try:
            # PostgreSQL syntax: ADD COLUMN IF NOT EXISTS works in modern PG
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS canva_access_token TEXT;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS canva_refresh_token TEXT;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS canva_token_expires_at TIMESTAMP;"))
            conn.commit()
            print("Users table updated successfully for Canva integration.")
        except Exception as e:
            print(f"Error updating users table: {e}")
            conn.rollback()

if __name__ == "__main__":
    update_db()
