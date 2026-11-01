"""Create or remove the throwaway accounts the admin Playwright suite signs in with.

    python scripts/e2e_seed.py create
    python scripts/e2e_seed.py cleanup

Local databases only, through the shared script guard (backend/script_guard.py); this
script takes no --yes, so a hosted database is always refused. The URL comes from
E2E_DATABASE_URL, then DATABASE_URL, then the local dev default.
"""
import argparse
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LOCAL_DEFAULT = "postgresql://olrac:olrac_password@localhost:5432/olrac_signage"
SLUG_PREFIX = "e2e-smoke-"
ADMIN_USERNAME = "e2e-admin"
TENANT_NAME = "E2E Smoke Tenant"


def database_url() -> str:
    from backend.script_guard import confirm_destructive

    url = os.environ.get("E2E_DATABASE_URL") or os.environ.get("DATABASE_URL") or LOCAL_DEFAULT
    confirm_destructive("create and delete the e2e smoke-test accounts", url)
    return url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("create", "cleanup"))
    args = parser.parse_args()

    os.environ["DATABASE_URL"] = database_url()
    os.environ.setdefault("SECRET_KEY", "local-dev-secret")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

    from backend import database, models
    from backend.routers.auth import get_password_hash

    db = database.SessionLocal()
    try:
        orgs = db.query(models.Organization).filter(models.Organization.slug.like(f"{SLUG_PREFIX}%")).all()
        org_ids = [org.id for org in orgs]
        user_ids = [user.id for user in db.query(models.User).filter(models.User.organization_id.in_(org_ids))]
        db.query(models.AdminAuditEvent).filter(
            models.AdminAuditEvent.actor_user_id.in_(user_ids) | models.AdminAuditEvent.target_org_id.in_(org_ids)
        ).delete(synchronize_session=False)
        for model in (models.Alert, models.Subscription, models.Screen, models.Client):
            db.query(model).filter(model.organization_id.in_(org_ids)).delete(synchronize_session=False)
        db.query(models.User).filter(models.User.id.in_(user_ids)).delete(synchronize_session=False)
        db.query(models.Organization).filter(models.Organization.id.in_(org_ids)).delete(synchronize_session=False)
        db.commit()
        if args.action == "cleanup":
            print(f"e2e seed: removed {len(org_ids)} workspaces")
            return

        password = os.environ.get("E2E_ADMIN_PASSWORD", "e2e-smoke-pass")
        house = models.Organization(name="E2E Smoke House", slug=f"{SLUG_PREFIX}house-{uuid.uuid4().hex[:6]}", status="active")
        tenant = models.Organization(name=TENANT_NAME, slug=f"{SLUG_PREFIX}tenant-{uuid.uuid4().hex[:6]}", status="active")
        db.add_all([house, tenant])
        db.flush()
        db.add_all([
            models.User(organization_id=house.id, username=ADMIN_USERNAME, email="e2e-admin@x.t", full_name="E2E Admin",
                        hashed_password=get_password_hash(password), role="super_admin", is_active=True),
            models.User(organization_id=tenant.id, username="e2e-owner", email="e2e-owner@x.t",
                        hashed_password=get_password_hash(password), role="owner", is_active=True),
        ])
        db.commit()
        print(f"e2e seed: created {ADMIN_USERNAME} and {TENANT_NAME}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
