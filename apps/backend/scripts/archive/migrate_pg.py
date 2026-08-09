import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not found in .env")
    exit(1)

# Correct the URL if it starts with postgres:// (SQLAlchemy 1.4+ requires postgresql://)
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

print(f"Connecting to: {DATABASE_URL.split('@')[-1]}") # Print host only for security

engine = create_engine(DATABASE_URL)

def migrate():
    # 1. Standard column additions
    columns_to_add = [
        ("full_name", "VARCHAR"),
        ("username", "VARCHAR"),
        ("profile_picture_url", "VARCHAR"),
        ("timezone", "VARCHAR DEFAULT 'UTC'"),
        ("company_name", "VARCHAR"),
        ("company_description", "TEXT"),
        ("product_details", "TEXT"),
        ("onboarded", "BOOLEAN DEFAULT FALSE"),
        ("business_url", "VARCHAR"),
        ("business_dna", "JSONB")
    ]

    with engine.begin() as conn:
        # Check existing columns for users
        result = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'users'"))
        existing_columns = [row[0] for row in result]
        
        for col_name, col_type in columns_to_add:
            if col_name not in existing_columns:
                print(f"Adding column: {col_name} ({col_type})")
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"))
        
        # 2. Convert INT to BIGINT for overflow protection
        tables_to_fix = [
            "users", "social_accounts", "published_posts", 
            "published_post_platforms", "drafts", "scheduled_posts", 
            "user_analytics_snapshots"
        ]
        
        print("Converting ID columns to BIGINT...")
        for table in tables_to_fix:
            try:
                # Alter primary key id
                conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN id TYPE BIGINT"))
                print(f"Converted {table}.id to BIGINT")
                
                # Alter foreign keys if they exist in this table
                if table != "users":
                    # Check if user_id exists
                    result = conn.execute(text(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}' AND column_name = 'user_id'"))
                    if result.first():
                        conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN user_id TYPE BIGINT"))
                        print(f"Converted {table}.user_id to BIGINT")
                
                if table == "published_post_platforms":
                    conn.execute(text(f"ALTER TABLE published_post_platforms ALTER COLUMN published_post_id TYPE BIGINT"))
                    print(f"Converted published_post_platforms.published_post_id to BIGINT")
            except Exception as e:
                print(f"Note on {table} conversion: {e}")

        # 3. Constraints
        print("Checking username constraint...")
        try:
            conn.execute(text("ALTER TABLE users ADD CONSTRAINT unique_username UNIQUE (username)"))
        except Exception as e:
            print(f"Constraint note: {e}")

    print("PostgreSQL Migration Complete! ✅")

if __name__ == "__main__":
    try:
        migrate()
    except Exception as e:
        print(f"Migration Failed: {e}")
