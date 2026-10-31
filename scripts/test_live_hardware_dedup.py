import sys
import pathlib
import uuid
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from backend.main import app
from backend.routers.auth import create_access_token
from backend.database import SessionLocal
from backend import models

client = TestClient(app)

def main():
    db = SessionLocal()
    u = uuid.uuid4().hex[:6]
    install_id = f"hw_lenovo_tb8505f_{u}"

    # Step 1: Initial TV registration
    print(f"\n--- STEP 1: TV registers with installation_id '{install_id}' and device_id 'dev_old_{u}' ---")
    res1 = client.post("/api/screens/register", json={
        "device_id": f"dev_old_{u}",
        "installation_id": install_id,
        "device_model": "Lenovo TB-8505F"
    })
    assert res1.status_code == 200
    pair_code = res1.json()["pair_code"]
    print(f"Pairing code generated: {pair_code}")

    # Step 2: Tenant owner pairs screen
    org = models.Organization(name=f"Live Hardware Test Org {u}", slug=f"hw-test-{u}", status="active")
    db.add(org)
    db.flush()

    user = models.User(organization_id=org.id, username=f"hw_user_{u}", email=f"hw_user_{u}@test.com", role="owner", is_active=True, hashed_password="dummy")
    db.add(user)
    db.commit()

    token = create_access_token({"sub": user.username})
    headers = {"Authorization": f"Bearer {token}"}

    print(f"\n--- STEP 2: Tenant owner pairs TV with name 'Lobby Main Display' ---")
    res_pair = client.post("/api/screens/pair", json={"pair_code": pair_code, "name": "Lobby Main Display"}, headers=headers)
    assert res_pair.status_code == 200
    paired_screen = res_pair.json()
    original_id = paired_screen["id"]
    print(f"Paired screen created: ID {original_id}, Name: '{paired_screen['name']}'")

    # Step 3: Simulate app uninstall + reinstall (same installation_id, new random device_id)
    print(f"\n--- STEP 3: TV App reinstalled (mints new device_id 'dev_new_{u}' with SAME installation_id) ---")
    res_reinstall = client.post("/api/screens/register", json={
        "device_id": f"dev_new_{u}",
        "installation_id": install_id,
        "device_model": "Lenovo TB-8505F"
    })
    assert res_reinstall.status_code == 200
    reclaimed = res_reinstall.json()
    print(f"Re-register response: ID {reclaimed['id']}, Status: '{reclaimed['status']}'")

    # Step 4: Verify screen list on dashboard has NO duplicates
    db.expire_all()
    res_list = client.get("/api/screens/", headers=headers)
    assert res_list.status_code == 200
    screens_in_org = res_list.json()
    print(f"\n--- STEP 4: Total screens in workspace: {len(screens_in_org)} ---")
    for s in screens_in_org:
        print(f"  Screen: ID {s['id']}, Name: '{s['name']}', Device: '{s['device_id']}'")

    assert len(screens_in_org) == 1, "Duplicate ghost screen was created!"
    assert screens_in_org[0]["id"] == original_id, "Screen ID did not match original!"
    assert screens_in_org[0]["device_id"] == f"dev_new_{u}", "Device ID was not updated!"

    print("\n=============================================================================")
    print("LIVE PROOF: 1 TV Reinstall Concept & Anti-Duplicate Hardware Binding 100% PASS!")
    print("=============================================================================")

    # Cleanup
    db.query(models.Screen).filter(models.Screen.organization_id == org.id).delete()
    db.delete(user)
    db.delete(org)
    db.commit()
    db.close()

if __name__ == "__main__":
    from backend.database import SQLALCHEMY_DATABASE_URL
    from backend.script_guard import confirm_destructive

    confirm_destructive("CREATE test tenants, users and screens", SQLALCHEMY_DATABASE_URL)
    main()
