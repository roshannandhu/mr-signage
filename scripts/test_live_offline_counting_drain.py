import sys
import pathlib
import time
import subprocess
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from backend.main import app
from backend.routers.auth import create_access_token
from backend.database import SessionLocal
from backend import models
from backend.worker import aggregate_play_logs_sync

adb = r"C:\Users\Roshan Raj\AppData\Local\Android\Sdk\platform-tools\adb.exe"
client = TestClient(app)

def get_tablet_sqlite_count():
    out = subprocess.check_output([
        adb, "shell",
        "run-as com.olrac.signage sqlite3 databases/signage_database 'SELECT count(*) FROM play_events;'"
    ])
    return int(out.decode().strip() or "0")

def main():
    db = SessionLocal()
    user = db.query(models.User).filter(models.User.organization_id == 19).first()
    token = create_access_token({"sub": user.username})
    headers = {"Authorization": f"Bearer {token}"}

    # Step 1: Baseline count
    aggregate_play_logs_sync(db)
    res_before = client.get("/api/analytics/media/28", headers=headers)
    assert res_before.status_code == 200
    baseline_count = res_before.json().get("today", {}).get("completed_plays", 0)
    print(f"\n--- STEP 1: Baseline Backend Analytics Completed Plays for Media 28: {baseline_count} ---")

    # Step 2: Cut network to physical tablet
    print("\n--- STEP 2: Disconnecting Tablet Network (Simulating Internet Outage) ---")
    subprocess.call([adb, "reverse", "--remove-all"])
    time.sleep(1)

    # Step 3: Let tablet loop offline for 32 seconds (~3 full plays)
    print("\n--- STEP 3: Letting Tablet Play Offline for 32 Seconds ---")
    for i in range(32, 0, -8):
        print(f"  Playing offline... {i}s remaining")
        time.sleep(8)

    # Step 4: Check SQLite on tablet
    offline_buffered = get_tablet_sqlite_count()
    print(f"\n--- STEP 4: Tablet Room SQLite Local Buffer: {offline_buffered} play events stored offline! ---")
    assert offline_buffered >= 2, f"Expected at least 2 buffered plays, got {offline_buffered}"

    # Step 5: Restore network
    print("\n--- STEP 5: Restoring Tablet Network Connection ---")
    subprocess.check_call([adb, "reverse", "tcp:8000", "tcp:8000"])
    subprocess.check_call([adb, "reverse", "tcp:8010", "tcp:8000"])
    
    print("\n--- STEP 6: Waiting for Automatic ProofOfPlayReporter Drain ---")
    time.sleep(6)

    # Step 7: Check SQLite buffer drained
    post_drain_sqlite = get_tablet_sqlite_count()
    print(f"Tablet Room SQLite Local Buffer after drain: {post_drain_sqlite} (Cleaned up!)")
    assert post_drain_sqlite == 0

    # Step 8: Run Rollup Aggregator & Verify
    aggregate_play_logs_sync(db)
    res_after = client.get("/api/analytics/media/28", headers=headers)
    assert res_after.status_code == 200
    new_count = res_after.json().get("today", {}).get("completed_plays", 0)
    print(f"\n--- STEP 8: New Certified Completed Plays on Backend: {new_count} (Incremented by {new_count - baseline_count}) ---")
    assert new_count >= baseline_count + offline_buffered

    print("\n=========================================================================")
    print("LIVE PROOF: Offline Play Buffering & HTTP Batch Drain Verified on Tablet!")
    print("=========================================================================")
    db.close()

if __name__ == "__main__":
    from backend.database import SQLALCHEMY_DATABASE_URL
    from backend.script_guard import confirm_destructive

    confirm_destructive("CREATE test tenants, users, screens and play logs", SQLALCHEMY_DATABASE_URL)
    main()
