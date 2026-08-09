import os
import sys
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

def delete_user(email):
    if not DATABASE_URL:
        print("Error: DATABASE_URL not found in .env")
        return

    if not email:
        print("Error: Please provide an email address.")
        return

    try:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as connection:
            # 1. Find User ID
            user_res = connection.execute(text("SELECT id FROM users WHERE email = :email"), {"email": email})
            user = user_res.fetchone()
            
            if not user:
                print(f"User with email '{email}' not found.")
                return
            
            user_id = user[0]
            print(f"Found User ID: {user_id} for {email}")
            
            # Start Deletion Sequence
            print("\nDeleting associated data...")
            
            # Analytics
            res = connection.execute(text("DELETE FROM user_analytics_snapshots WHERE user_id = :uid"), {"uid": user_id})
            print(f"- Deleted {res.rowcount} analytics snapshots")
            
            # Scheduled Posts
            res = connection.execute(text("DELETE FROM scheduled_posts WHERE user_id = :uid"), {"uid": user_id})
            print(f"- Deleted {res.rowcount} scheduled posts")
            
            # Drafts
            res = connection.execute(text("DELETE FROM drafts WHERE user_id = :uid"), {"uid": user_id})
            print(f"- Deleted {res.rowcount} drafts")
            
            # Published Post Platforms (Deep link)
            res = connection.execute(text("""
                DELETE FROM published_post_platforms 
                WHERE published_post_id IN (SELECT id FROM published_posts WHERE user_id = :uid)
            """), {"uid": user_id})
            print(f"- Deleted {res.rowcount} published post platform records")
            
            # Published Posts
            res = connection.execute(text("DELETE FROM published_posts WHERE user_id = :uid"), {"uid": user_id})
            print(f"- Deleted {res.rowcount} published posts")
            
            # Social Accounts
            res = connection.execute(text("DELETE FROM social_accounts WHERE user_id = :uid"), {"uid": user_id})
            print(f"- Deleted {res.rowcount} social accounts")
            
            # User OTPs (by email)
            res = connection.execute(text("DELETE FROM user_otps WHERE email = :email"), {"email": email})
            print(f"- Deleted {res.rowcount} OTP records")
            
            # Finally, the User
            res = connection.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
            print(f"- Deleted User record")
            
            connection.commit()
            print(f"\nSUCCESS: User {email} and all associated data have been permanently removed.")

    except Exception as e:
        print(f"Error during deletion: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python delete_user.py <email>")
    else:
        target_email = sys.argv[1]
        confirm = input(f"Are you SURE you want to permanently delete {target_email}? (y/N): ")
        if confirm.lower() == 'y':
            delete_user(target_email)
        else:
            print("Deletion cancelled.")
