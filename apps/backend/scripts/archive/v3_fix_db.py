import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

def fix():
    load_dotenv()
    db_url = os.getenv('DATABASE_URL')
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
        
    engine = create_engine(db_url)
    cols_to_add = ["product_url", "business_url", "business_dna"]
    
    with engine.connect() as conn:
        print("Checking users table columns...")
        for col in cols_to_add:
            try:
                # Direct SQL for PostgreSQL
                conn.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {'JSONB' if col == 'business_dna' else 'VARCHAR'}"))
                print(f"Verified/Added: {col}")
            except Exception as e:
                print(f"Error with {col}: {e}")
        conn.commit()
    print("MIGRATION COMPLETE!")

if __name__ == "__main__":
    fix()
