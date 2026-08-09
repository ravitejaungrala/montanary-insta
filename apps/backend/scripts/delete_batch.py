import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

emails_to_delete = [
    "salman.s@neuzenai.com",
    "salman.sk@neuzenai.com",
    "test@test.com",
    "raviteja.u@neuzenai.com",
    "salmanshaik@gmail.com",
    "test@neuzenai.com"
]

def batch_delete():
    if not DATABASE_URL:
        print("Error: DATABASE_URL not found")
        return

    try:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as connection:
            for email in emails_to_delete:
                print(f"\n--- Processing: {email} ---")
                user_res = connection.execute(text("SELECT id FROM users WHERE email = :email"), {"email": email})
                user = user_res.fetchone()
                
                if not user:
                    print(f"Skipping: {email} (Not found)")
                    continue
                
                uid = user[0]
                
                # Delete sequence
                connection.execute(text("DELETE FROM user_analytics_snapshots WHERE user_id = :uid"), {"uid": uid})
                connection.execute(text("DELETE FROM scheduled_posts WHERE user_id = :uid"), {"uid": uid})
                connection.execute(text("DELETE FROM drafts WHERE user_id = :uid"), {"uid": uid})
                connection.execute(text("DELETE FROM published_post_platforms WHERE published_post_id IN (SELECT id FROM published_posts WHERE user_id = :uid)"), {"uid": uid})
                connection.execute(text("DELETE FROM published_posts WHERE user_id = :uid"), {"uid": uid})
                connection.execute(text("DELETE FROM social_accounts WHERE user_id = :uid"), {"uid": uid})
                connection.execute(text("DELETE FROM user_otps WHERE email = :email"), {"email": email})
                connection.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": uid})
                
                print(f"DONE: Removed {email}")
            
            connection.commit()
            print("\nBatch deletion complete.")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    batch_delete()
