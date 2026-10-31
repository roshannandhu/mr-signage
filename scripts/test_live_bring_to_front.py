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

adb = r"C:\Users\Roshan Raj\AppData\Local\Android\Sdk\platform-tools\adb.exe"
client = TestClient(app)

def capture_screenshot(filename):
    out = subprocess.check_output([adb, "exec-out", "screencap", "-p"])
    path = rf"C:\Users\Roshan Raj\.gemini\antigravity-ide\brain\78a9f403-f058-4360-b857-d487f944ee79\{filename}"
    with open(path, "wb") as f:
        f.write(out)
    print(f"Captured screenshot: {filename} ({len(out)} bytes)")
    return path

def main():
    db = SessionLocal()
    user = db.query(models.User).filter(models.User.organization_id == 19).first()
    if not user:
        user = models.User(organization_id=19, username="roshan_owner", email="roshan@test.com", role="owner", is_active=True, hashed_password="dummy")
        db.add(user)
        db.commit()

    token = create_access_token({"sub": user.username})
    headers = {"Authorization": f"Bearer {token}"}
    db.close()

    print("\n--- STEP 1: Sending Tablet to Android Home Screen ---")
    subprocess.check_call([adb, "shell", "input keyevent KEYCODE_HOME"])
    time.sleep(2)
    capture_screenshot("proof_step1_home_screen.png")

    print("\n--- STEP 2: Firing Bring-to-Front API for Screen 1 ---")
    res = client.post("/api/screens/1/bring-to-front", headers=headers)
    print(f"API Response: {res.status_code} {res.json()}")
    assert res.status_code == 200

    print("\n--- STEP 3: Waiting for Tablet to Launch Player ---")
    time.sleep(3)
    capture_screenshot("proof_step3_restored_player.png")

    # Check top focused activity on tablet
    focused = subprocess.check_output([adb, "shell", "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'"]).decode()
    print(f"Current focused app on tablet:\n{focused.strip()}")
    assert "com.olrac.signage" in focused

    print("\n=======================================================")
    print("LIVE PROOF: Bring-to-Front Verified on Physical Tablet!")
    print("=======================================================")

if __name__ == "__main__":
    from backend.database import SQLALCHEMY_DATABASE_URL
    from backend.script_guard import confirm_destructive

    confirm_destructive("CREATE test tenants, users and screens", SQLALCHEMY_DATABASE_URL)
    main()
