import os
from sqlalchemy import create_engine, MetaData, Table, update
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# AWS RDS PostgreSQL URI
DATABASE_URL = os.getenv("DATABASE_URL")

def fix_onboarding_status():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not found in .env")
        return

    print(f"Connecting to RDS: {DATABASE_URL.split('@')[-1]}...")
    engine = create_engine(DATABASE_URL)
    metadata = MetaData()
    users = Table('users', metadata, autoload_with=engine)

    with engine.begin() as conn:
        # Mark users who already have company details or are the main seed user as onboarded
        stmt = (
            update(users)
            .where(
                (users.c.email == 'contact@neuzenai.com') | 
                (users.c.company_name != None)
            )
            .values(onboarded=True)
        )
        result = conn.execute(stmt)
        print(f"SUCCESS: Updated {result.rowcount} users to onboarded status.")

if __name__ == "__main__":
    fix_onboarding_status()
