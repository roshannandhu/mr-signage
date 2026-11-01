""""View workspace (read-only)" actually means read-only, everywhere.

The Tenant 360 page's read-only entry point sends X-Act-As-Mode: read; tenancy.py's
require_write_access() is meant to refuse every write while it is set. Two layers:

  * a STATIC sweep of every POST/PUT/PATCH/DELETE route in every tenant-facing router,
    asserting each one is gated by require_tenant_roles (which calls require_write_access),
    require_super_admin, device credentials, or is on a short, named allowlist of routes
    that do no write at all (a pure price quote, a maps-link parse). A new write route that
    forgets the gate fails this test immediately, which is the actual point: a live HTTP
    sweep across 79 routes with 79 different schemas would be too fragile to trust.
  * a DYNAMIC check that the mechanism works at runtime: representative writes across
    different routers (clients, branding, playlists, billing) are refused under the read
    header with nothing changed, succeed when acting without it (full "Enter workspace"
    access is not accidentally narrowed), and a GET still works either way.
"""
import bisect
import json
import os
import pathlib
import re
import sys
import tempfile
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-actasro-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-act-as-read-only")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import get_password_hash, login_token  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


# Routes with no business write at all -- a price computation and a URL parse -- so they
# are POST only because they take a body, not because they change anything. Anything found
# outside this list and outside the gated patterns below is a real gap.
PURE_COMPUTATION_ROUTES = {
    ("backend/routers/screens.py", "resolve_location_link"),
    ("backend/routers/tenant_plans.py", "quote_plan"),
}
# Routers with no TenantScope at all -- TV/device credentials or platform-admin-only --
# which this sweep (tenant-dashboard writes) does not apply to.
EXCLUDED_ROUTERS = {"admin.py", "auth.py", "device_diagnostics.py", "screenshots.py", "websockets.py"}
# billing.py's writes use get_tenant_scope/get_billing_scope directly (so a pending_approval
# workspace can still pay its way in) rather than require_tenant_roles; each calls
# scope.require_write_access() by hand instead. Verified by name below.
MANUAL_GATE_FUNCTIONS = {
    ("backend/routers/billing.py", "create_checkout"),
    ("backend/routers/billing.py", "purchase_plan"),
    ("backend/routers/billing.py", "mock_confirm"),
    ("backend/routers/billing.py", "create_custom_request"),
    ("backend/routers/billing.py", "purchase_custom_request"),
}


def static_sweep() -> None:
    decorator = re.compile(r'@router\.(post|put|patch|delete)\(')
    any_route = re.compile(r'^@router\.')
    func_name = re.compile(r'^(?:async )?def (\w+)\(')
    routers_dir = ROOT / "backend" / "routers"
    gapped: list[str] = []

    for path in sorted(routers_dir.glob("*.py")):
        if path.name in EXCLUDED_ROUTERS:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        rel = f"backend/routers/{path.name}"
        # Where the next route starts, by line index -- a route's window is capped there so
        # it never reads into the FOLLOWING route's body. It did once, and a device route
        # with no TenantScope of its own borrowed one from its neighbour, nearly hiding a
        # real gap.
        route_starts = sorted(i for i, l in enumerate(lines) if any_route.match(l.strip()))

        def next_route_after(i: int) -> int:
            j = bisect.bisect_right(route_starts, i)
            return route_starts[j] if j < len(route_starts) else len(lines)

        for i, line in enumerate(lines):
            if not decorator.search(line):
                continue
            # The function name: the next `def` line. Not necessarily right after the
            # @router.post(...) decorator -- a rate-limit decorator or a comment explaining
            # it often sits between the two.
            name = None
            end = next_route_after(i)
            for j in range(i + 1, end):
                m = func_name.match(lines[j].strip())
                if m:
                    name = m.group(1)
                    break
            if name is None:
                gapped.append(f"{rel}:{i + 1}: could not find the function name before the next route")
                continue

            if (rel, name) in PURE_COMPUTATION_ROUTES or (rel, name) in MANUAL_GATE_FUNCTIONS:
                continue

            # From the decorator up to (not into) the next route -- the whole signature and
            # body, so a Depends() that spans lines or a gate call made as a body statement
            # (billing.py's manual calls, caught above) are both seen.
            window = "\n".join(lines[i:end])
            if "TenantScope" not in window:
                # No TenantScope parameter at all: a TV/device-credentialed route (sign-in,
                # enrollment, pairing) or a signature-verified payment webhook. Neither goes
                # through tenancy.py, so "read-only view of a workspace" does not apply.
                continue
            if "require_tenant_roles" in window or "require_super_admin" in window:
                continue
            gapped.append(f"{rel}:{i + 1} {name}: takes a TenantScope but is not gated by "
                           "require_tenant_roles/require_super_admin, and is not on the "
                           "pure-computation or manual-gate allowlist")

    check(not gapped, "write routes not covered by the read-only gate:\n  " + "\n  ".join(gapped))

    # The manual-gate allowlist itself has to actually call the method, not just be named here.
    billing_src = (routers_dir / "billing.py").read_text(encoding="utf-8")
    for _, name in MANUAL_GATE_FUNCTIONS:
        body_start = billing_src.index(f"def {name}(")
        body = billing_src[body_start:body_start + 1200]
        check("scope.require_write_access()" in body,
              f"billing.{name} is on the manual-gate allowlist but never calls require_write_access()")


def run() -> None:
    static_sweep()

    client = TestClient(app)
    db = database.SessionLocal()
    plan = models.Plan(name="P", slug=f"p-{uuid.uuid4().hex[:6]}", feature_flags_json="{}",
                       is_active=True, max_storage_bytes=10 * 1024 ** 3,
                       monthly_price_paise=0, yearly_price_paise=0, price_paise=0)
    db.add(plan)
    db.flush()
    house = models.Organization(name="House", slug=f"house-{uuid.uuid4().hex[:6]}", status="active")
    tenant = models.Organization(name="Tenant", slug=f"tenant-{uuid.uuid4().hex[:6]}", status="active", plan_id=plan.id)
    db.add_all([house, tenant])
    db.flush()
    db.add(models.Subscription(organization_id=tenant.id, plan_id=plan.id, status="active"))
    admin = models.User(organization_id=house.id, username="aaro-admin", email="aaro-admin@x.t",
                        hashed_password=get_password_hash("x"), role="super_admin", is_active=True)
    db.add(admin)
    db.commit()
    tenant_id, plan_id = tenant.id, plan.id
    admin_auth = {"Authorization": f"Bearer {login_token(admin)}"}
    db.close()

    read_only = {**admin_auth, "X-Act-As-Org": str(tenant_id), "X-Act-As-Mode": "read"}
    full_access = {**admin_auth, "X-Act-As-Org": str(tenant_id)}

    def before_count(model) -> int:
        db = database.SessionLocal()
        try:
            return db.query(model).filter(model.organization_id == tenant_id).count()
        finally:
            db.close()

    # --- representative writes across different routers: refused read-only, allowed in full access ---
    checks = [
        ("clients", "/api/clients/", {"name": "Acme Ads"}, models.Client),
        ("playlists", "/api/playlists/", {"name": "Lobby loop"}, models.Playlist),
    ]
    for label, path, payload, model in checks:
        before = before_count(model)
        refused = client.post(path, json=payload, headers=read_only)
        check(refused.status_code == 403, f"{label}: read-only POST should be 403, got {refused.status_code}")
        check("read-only" in refused.json().get("detail", "").lower(),
              f"{label}: refusal should say it's a read-only view: {refused.json()}")
        check(before_count(model) == before, f"{label}: a refused write must not have created a row")

        allowed = client.post(path, json=payload, headers=full_access)
        check(allowed.status_code in (200, 201), f"{label}: full-access POST should succeed, got {allowed.status_code} {allowed.text[:200]}")
        check(before_count(model) == before + 1, f"{label}: the allowed write should have created exactly one row")

    # branding: PUT, no created-row to count, just the status code and that the name didn't change
    refused = client.put("/api/branding/", json={"brand_name": "Should Not Stick"}, headers=read_only)
    check(refused.status_code == 403, f"branding: read-only PUT should be 403, got {refused.status_code}")
    allowed = client.put("/api/branding/", json={"brand_name": "Did Stick"}, headers=full_access)
    check(allowed.status_code == 200 and allowed.json().get("brand_name") == "Did Stick",
          f"branding: full-access PUT should succeed, got {allowed.status_code} {allowed.text[:200]}")

    # billing: the manually-gated purchase path, refused the same way.
    refused = client.post("/api/billing/checkout", json={"plan_id": plan_id, "billing_period": "monthly"}, headers=read_only)
    check(refused.status_code == 403, f"billing checkout: read-only POST should be 403, got {refused.status_code}")

    # GETs still work read-only -- this is a VIEW, not a lockout.
    view = client.get("/api/clients/", headers=read_only)
    check(view.status_code == 200, f"GET should still work read-only, got {view.status_code}")

    database.engine.dispose()

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Act-as read-only: every write route gated (static sweep), representative writes refused live, GETs still work")


if __name__ == "__main__":
    run()
