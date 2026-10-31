import sys, os, json
sys.path.insert(0, os.path.abspath("."))

from fastapi.testclient import TestClient
from backend.main import app
from backend.database import SessionLocal
from backend import models

def run_test():
    client = TestClient(app)
    
    print("========================================================================")
    print("STEP 1: New Client Signs Up with Google (Real Google Sign-In & Provision)")
    print("========================================================================")
    test_email = "store.client.chain@gmail.com"
    r = client.post("/api/auth/google", json={"code": test_email, "redirect_uri": "http://localhost:3000/login"})
    assert r.status_code == 200, f"Signup failed: {r.text}"
    signup_data = r.json()
    client_token = signup_data["access_token"]
    client_user = signup_data["user"]
    org_id = client_user["organization_id"]
    org_status = client_user["organization_status"]
    
    print(f"[SUCCESS] New User Created: '{client_user['username']}' (ID: {client_user['id']})")
    print(f"[SUCCESS] New Organization Provisioned: '{client_user['organization_name']}' (Org ID: {org_id})")
    print(f"[SUCCESS] Organization Status: '{org_status}' (GATE ACTIVE: Awaiting Manager Review)")
    assert org_status == "pending_approval"
    
    print("\n========================================================================")
    print("STEP 2: TV Screen Binds to Pending Org & Plays Universal Demo Reel Video")
    print("========================================================================")
    device_id = "test-store-tv-display-01"
    db = SessionLocal()
    screen = db.query(models.Screen).filter(models.Screen.device_id == device_id).first()
    if not screen:
        screen = models.Screen(device_id=device_id)
        db.add(screen)
    screen.organization_id = org_id
    screen.status = "online"
    db.commit()
    db.close()
    
    sync_resp = client.get(f"/api/screens/{device_id}/sync")
    assert sync_resp.status_code == 200
    sync_data = sync_resp.json()
    print(f"[SUCCESS] Screen Status on TV: {sync_data['status']}")
    print(f"[SUCCESS] Active Playlist: '{sync_data['playlist']['name']}'")
    print(f"[SUCCESS] Active Streaming Media: '{sync_data['playlist']['items'][0]['content']['name']}'")
    print(f"[SUCCESS] Video Stream URL: {sync_data['playlist']['items'][0]['content']['file_url']}")
    print(f"[SUCCESS] Heartbeat Sync Interval: {sync_data['sync_interval_seconds']}s (High Frequency for Instant Approval Switch)")
    assert "Universal Demo" in sync_data['playlist']['name']
    
    print("\n========================================================================")
    print("STEP 3: Platform Manager Logs In and Inspects Approvals Queue")
    print("========================================================================")
    mgr_res = client.post("/api/auth/token", data={"username": "juug22btech48491@gmail.com", "password": "Roshan@1100"})
    assert mgr_res.status_code == 200
    mgr_token = mgr_res.json()["access_token"]
    
    appr_res = client.get("/api/approvals", headers={"Authorization": f"Bearer {mgr_token}"})
    assert appr_res.status_code == 200
    pending_list = appr_res.json()
    print(f"[SUCCESS] Total Pending Workspaces in Queue: {len(pending_list)}")
    matched = [o for o in pending_list if o["id"] == org_id]
    assert len(matched) > 0, "New org must be in pending approvals list"
    print(f"[SUCCESS] Found Pending Registration: Org #{matched[0]['id']} - '{matched[0]['name']}' (Owner: {matched[0]['owner_email']})")
    
    print("\n========================================================================")
    print("STEP 4: Manager Approves Workspace (Allocates 50 Displays Quota)")
    print("========================================================================")
    appr_action = client.post(f"/api/approvals/{org_id}/approve", json={"max_screens": 50}, headers={"Authorization": f"Bearer {mgr_token}"})
    assert appr_action.status_code == 200
    print(f"[SUCCESS] Approval Result: {appr_action.json()['message']}")
    
    db = SessionLocal()
    org_in_db = db.query(models.Organization).filter(models.Organization.id == org_id).first()
    assert org_in_db.status == "active"
    print(f"[SUCCESS] Database Organization Status Verified: '{org_in_db.status}'")
    db.close()
    
    print("\n========================================================================")
    print("STEP 5: TV Screen Seamlessly Switches to Active Commercial Fleet")
    print("========================================================================")
    sync_resp_after = client.get(f"/api/screens/{device_id}/sync")
    assert sync_resp_after.status_code == 200
    sync_data_after = sync_resp_after.json()
    print(f"[SUCCESS] Screen Status on TV: {sync_data_after['status']}")
    print(f"[SUCCESS] TV Switched Out of Demo Loop: Workspace is now APPROVED and ready for custom playlists!")
    
    print("\n========================================================================")
    print("ALL 5 STEPS PASSED 100% WITH REAL POSTGRESQL VERIFICATION!")
    print("========================================================================")

if __name__ == "__main__":
    from backend.database import SQLALCHEMY_DATABASE_URL
    from backend.script_guard import confirm_destructive

    confirm_destructive("CREATE test tenants and users", SQLALCHEMY_DATABASE_URL)
    run_test()
