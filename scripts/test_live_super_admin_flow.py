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

    print("\n--- STEP 1: Super Admin (admin@olrac.com) Authenticates ---")
    admin_token = create_access_token({"sub": "admin@olrac.com"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Verify Super Admin can list all tenants
    res_tenants = client.get("/api/admin/tenants", headers=admin_headers)
    assert res_tenants.status_code == 200
    print(f"Super Admin verified: Loaded {len(res_tenants.json())} tenants across platform.")

    print("\n--- STEP 2: New Business Client Registers (Starts as Pending) ---")
    new_org = models.Organization(name=f"New Retail Chain {u}", slug=f"retail-chain-{u}", status="pending_approval")
    db.add(new_org)
    db.flush()

    client_user = models.User(
        organization_id=new_org.id,
        username=f"retail_owner_{u}@test.com",
        email=f"retail_owner_{u}@test.com",
        role="owner",
        is_active=True,
        hashed_password="dummy"
    )
    db.add(client_user)
    db.commit()

    client_token = create_access_token({"sub": client_user.username})
    client_headers = {"Authorization": f"Bearer {client_token}"}

    # Verify client is pending and blocked from admin portal
    res_me = client.get("/api/auth/me", headers=client_headers)
    assert res_me.status_code == 200
    assert res_me.json()["organization_status"] == "pending_approval"
    print(f"Client account verified: Role '{res_me.json()['role']}', Status '{res_me.json()['organization_status']}'")

    res_blocked = client.get("/api/admin/tenants", headers=client_headers)
    assert res_blocked.status_code == 403
    print("Client correctly blocked with 403 Forbidden from /api/admin/*")

    print("\n--- STEP 3: Super Admin Approves the New Workspace ---")
    res_approve = client.post(f"/api/admin/tenants/{new_org.id}/approve", json={"max_screens": 25, "max_ad_slots": 50}, headers=admin_headers)
    assert res_approve.status_code == 200
    assert res_approve.json()["status"] == "active"
    print(f"Workspace approved: Status '{res_approve.json()['status']}', Max Screens: {res_approve.json()['max_screens']}")

    print("\n--- STEP 4: Super Admin Promotes Client to Platform Super Admin ---")
    res_promote = client.patch(f"/api/admin/users/{client_user.id}/role", json={"role": "super_admin"}, headers=admin_headers)
    assert res_promote.status_code == 200
    assert res_promote.json()["role"] == "super_admin"
    print(f"User promoted: Role is now '{res_promote.json()['role']}'")

    # Verify promoted user now has admin console privileges
    res_admin_access = client.get("/api/admin/tenants", headers=client_headers)
    assert res_admin_access.status_code == 200
    print("Promoted user successfully accessed /api/admin/tenants!")

    print("\n--- STEP 5: Super Admin Demotes User Back to Owner ---")
    res_demote = client.patch(f"/api/admin/users/{client_user.id}/role", json={"role": "owner"}, headers=admin_headers)
    assert res_demote.status_code == 200
    assert res_demote.json()["role"] == "owner"
    print(f"User demoted: Role is back to '{res_demote.json()['role']}'")

    res_blocked_again = client.get("/api/admin/tenants", headers=client_headers)
    assert res_blocked_again.status_code == 403
    print("Demoted user is immediately restricted with 403 Forbidden.")

    print("\n=========================================================================")
    print("LIVE PROOF: Super Admin Console, Approvals & Role Hierarchy 100% PASS!")
    print("=========================================================================")

    # Cleanup
    db.delete(client_user)
    db.delete(new_org)
    db.commit()
    db.close()

if __name__ == "__main__":
    from backend.database import SQLALCHEMY_DATABASE_URL
    from backend.script_guard import confirm_destructive

    confirm_destructive("CREATE test tenants, users and a super admin", SQLALCHEMY_DATABASE_URL)
    main()
