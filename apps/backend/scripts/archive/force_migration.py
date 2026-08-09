from sqlalchemy import text
from core.database import engine

def migrate():
    with engine.connect() as conn:
        print("FORCING PRODUCT_URL MIGRATION...")
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN product_url VARCHAR;"))
            print("Successfully added product_url!")
        except Exception as e:
            print(f"Error or already exists: {e}")
        conn.commit()
    print("DONE!")

if __name__ == "__main__":
    migrate()
