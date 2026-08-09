import os
import asyncio
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

def check_database():
    if not DATABASE_URL:
        print("Error: DATABASE_URL not found in .env")
        return

    try:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as connection:
            print(f"--- Connected to Database: {DATABASE_URL.split('@')[-1]} ---")
            
            # Check All Tables
            print("\n[Database Overview]")
            table_res = connection.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"))
            tables = [r[0] for r in table_res.fetchall()]
            print(f"Total Tables in 'public' schema: {len(tables)}")
            print(f"Tables: {', '.join(tables)}")

            if 'users' in tables:
                print("\n[Users Table Check]")
                count_res = connection.execute(text("SELECT COUNT(*) FROM users"))
                print(f"Total users: {count_res.scalar()}")
                
                # List 5 users with email only to prevent schema errors
                user_res = connection.execute(text("SELECT email FROM users LIMIT 10"))
                for row in user_res:
                    print(f"- {row[0]}")
            else:
                print("\n'users' table NOT FOUND in public schema.")

            # Check some stats
            print("\n[Data Stats]")
            tables = ['users', 'user_otps', 'social_connections', 'post_history', 'scheduled_posts']
            for table in tables:
                try:
                    count_result = connection.execute(text(f"SELECT COUNT(*) FROM {table}"))
                    count = count_result.scalar()
                    print(f"- {table}: {count} records")
                except Exception as e:
                    print(f"- {table}: Error checking table ({e})")

    except Exception as e:
        import traceback
        print(f"Error connecting to database: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    check_database()
