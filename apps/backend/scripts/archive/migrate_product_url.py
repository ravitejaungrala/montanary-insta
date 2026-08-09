from sqlalchemy import text
from core.database import engine

def migrate():
    with engine.connect() as conn:
        print("Checking for missing columns in users table...")
        
        # Add product_url
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN product_url VARCHAR;"))
            print("Added 'product_url' column.")
        except Exception as e:
            print(f"'product_url' might already exist or error: {e}")
            
        conn.commit()
        print("Migration complete! 🚀")

if __name__ == "__main__":
    migrate()
