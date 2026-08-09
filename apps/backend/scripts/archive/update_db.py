from sqlalchemy import text
from core.database import engine

def update_schema():
    with engine.connect() as conn:
        print("Checking for missing columns in scheduled_posts...")
        
        # Check if columns exist (ignoring errors if they do)
        try:
            conn.execute(text("ALTER TABLE scheduled_posts ADD COLUMN post_type VARCHAR DEFAULT 'standard';"))
            print("Added 'post_type' column.")
        except Exception as e:
            print(f"'post_type' might already exist: {e}")
            
        try:
            conn.execute(text("ALTER TABLE scheduled_posts ADD COLUMN campaign_brief VARCHAR;"))
            print("Added 'campaign_brief' column.")
        except Exception as e:
            print(f"'campaign_brief' might already exist: {e}")
            
        conn.commit()
        print("Schema update complete! 🚀")

if __name__ == "__main__":
    update_schema()
