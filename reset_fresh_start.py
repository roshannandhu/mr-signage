import os
import shutil
import pathlib
import sys

sys.path.insert(0, os.path.abspath("."))

from sqlalchemy import text
from backend.database import SessionLocal, engine

def fresh_start():
    db = SessionLocal()
    try:
        print("1. Cleaning up tables in Supabase Postgres...")
        
        tables_to_clear = [
            "play_logs",
            "play_log_hourly_rollups",
            "screenshot_logs",
            "alerts",
            "emergency_broadcasts",
            "ad_placement_targets",
            "ad_placements",
            "campaigns",
            "playlist_items",
            "playlists",
            "media_renditions",
            "content",
            "screens",
            "screen_groups",
            "enrollment_tokens",
            "subscriptions",
            "webhook_events",
        ]
        
        for table in tables_to_clear:
            try:
                db.execute(text(f"DELETE FROM {table};"))
                db.commit()
                print(f"   [OK] Cleared table: {table}")
            except Exception as e:
                db.rollback()
                print(f"   - Notice on {table}: {e}")
        
        # Delete non-superadmin users and non-default orgs
        db.execute(text("DELETE FROM users WHERE role != 'super_admin';"))
        db.commit()
        db.execute(text("DELETE FROM organizations WHERE id NOT IN (SELECT organization_id FROM users WHERE role = 'super_admin');"))
        db.commit()
        print("2. Database cleared successfully! Kept Super Admin user.")

        # 3. Clean local uploads folder
        uploads_dir = pathlib.Path("uploads")
        if uploads_dir.exists():
            for item in uploads_dir.iterdir():
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                elif item.is_file():
                    item.unlink(missing_ok=True)
            print("3. Local uploads directory cleaned!")

        print("\nAll screens, uploads, and playlists removed. Ready for a fresh start!")
    except Exception as exc:
        db.rollback()
        print(f"Error during fresh start: {exc}")
    finally:
        db.close()

if __name__ == "__main__":
    from backend.database import SQLALCHEMY_DATABASE_URL
    from backend.script_guard import confirm_destructive

    confirm_destructive("DELETE every tenant's screens, content, playlists, bookings, play logs, users and uploads", SQLALCHEMY_DATABASE_URL)
    fresh_start()
