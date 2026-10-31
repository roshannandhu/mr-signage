import os
import time
import sys

from fastapi.testclient import TestClient
from backend.main import app

# Build the schema explicitly. backend.main used to do this as an import side effect,
# so importing the app silently wrote to whatever DATABASE_URL pointed at; it now runs
# in the lifespan, which a bare TestClient(app) never starts. Each isolated script owns
# its own database anyway, so creating it here is the honest version of what was
# happening implicitly before.
from backend import database as _bootstrap_db, models as _bootstrap_models
_bootstrap_models.Base.metadata.create_all(bind=_bootstrap_db.engine)
from backend.database import SessionLocal
from backend.models import Content, MediaRendition, Organization, Plan, User, utcnow

def setup_db():
    db = SessionLocal()
    org = db.query(Organization).filter(Organization.slug == "test-org-pipeline").first()
    if not org:
        plan = db.query(Plan).first()
        org = Organization(name="Test Org Pipeline", slug="test-org-pipeline", plan_id=plan.id if plan else None)
        db.add(org)
        db.commit()
    
    user = db.query(User).filter(User.username == "test-pipeline-user").first()
    if not user:
        user = User(
            organization_id=org.id,
            username="test-pipeline-user",
            hashed_password="fake",
            role="owner"
        )
        db.add(user)
        db.commit()
    return user, db

def run():
    user, db = setup_db()
    client = TestClient(app)
    
    # We need to authenticate, but for testing we can just patch `get_tenant_scope`
    from backend.tenancy import TenantScope
    def override_get_tenant_scope():
        return TenantScope(db=db, user=user)
        
    def override_require_tenant_roles(*roles):
        def requirement():
            return override_get_tenant_scope()
        return requirement
        
    app.dependency_overrides[backend.tenancy.get_tenant_scope] = override_get_tenant_scope
    app.dependency_overrides[backend.tenancy.require_tenant_roles] = override_require_tenant_roles
    
    # Upload 4K HEVC
    with open("test_4k_hevc.mp4", "rb") as f:
        resp = client.post("/api/content/upload", files={"file": ("test_4k_hevc.mp4", f)}, data={"name": "4K Video"})
    assert resp.status_code == 201, resp.text
    content_4k = resp.json()
    assert content_4k["status"] == "processing", "Should return processing immediately"
    
    # Upload Portrait
    with open("test_portrait.mp4", "rb") as f:
        resp = client.post("/api/content/upload", files={"file": ("test_portrait.mp4", f)}, data={"name": "Portrait Video"})
    assert resp.status_code == 201, resp.text
    content_portrait = resp.json()
    assert content_portrait["status"] == "processing", "Should return processing immediately"
    
    print(f"Uploaded 4K ID: {content_4k['id']}")
    print(f"Uploaded Portrait ID: {content_portrait['id']}")
    
    # Wait for processing
    print("Waiting for processing to complete...")
    for _ in range(60):
        c1 = db.query(Content).filter(Content.id == content_4k["id"]).first()
        c2 = db.query(Content).filter(Content.id == content_portrait["id"]).first()
        if c1.status in ("ready", "failed") and c2.status in ("ready", "failed"):
            print("Processing finished!")
            break
        time.sleep(1)
        db.refresh(c1)
        db.refresh(c2)
        
    c1 = db.query(Content).filter(Content.id == content_4k["id"]).first()
    c2 = db.query(Content).filter(Content.id == content_portrait["id"]).first()
    
    print("4K status:", c1.status)
    for rend in c1.renditions:
        print(f" - {rend.resolution}: {rend.width}x{rend.height} ({rend.rotation} deg) codec:{rend.codec}")
        
    print("Portrait status:", c2.status)
    for rend in c2.renditions:
        print(f" - {rend.resolution}: {rend.width}x{rend.height} ({rend.rotation} deg) codec:{rend.codec}")
        
    if c1.status != "ready" or c2.status != "ready":
        print(f"Failed. 4K reason: {c1.failed_reason}, Portrait reason: {c2.failed_reason}")
        
        # Cleanup
        db.delete(c1)
        db.delete(c2)
        db.commit()
        sys.exit(1)
        
    # Test that /sync correctly returns only 'ready' content
    # (Though we can just visually confirm the renditions are correct)
    
    # Cleanup DB test data
    db.delete(c1)
    db.delete(c2)
    db.commit()
    print("Test data cleaned up successfully.")

if __name__ == "__main__":
    from backend.database import SQLALCHEMY_DATABASE_URL
    from backend.script_guard import confirm_destructive

    confirm_destructive("CREATE a test organisation and upload files to the storage bucket", SQLALCHEMY_DATABASE_URL)
    import backend.tenancy
    run()
