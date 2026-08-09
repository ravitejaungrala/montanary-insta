import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("DATABASE_URL not found in .env")
    exit(1)

engine = create_engine(DATABASE_URL)

def migrate():
    with engine.connect() as conn:
        print("Creating 'user_analytics_snapshots' table...")
        try:
            # SQL for creating the snapshots table if it doesn't exist
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS user_analytics_snapshots (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id),
                    snapshot_date TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
                    total_followers INTEGER DEFAULT 0,
                    total_engagement INTEGER DEFAULT 0,
                    total_reach INTEGER DEFAULT 0
                );
            """))
            conn.commit()
            print("Table 'user_analytics_snapshots' created successfully.")
        except Exception as e:
            print(f"Error creating table: {e}")
            conn.rollback()

if __name__ == "__main__":
    migrate()
