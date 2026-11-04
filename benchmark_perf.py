import time
import statistics
import json
import uuid
import requests
from backend.database import SessionLocal
from backend.models import Screen
from backend.routers.auth import get_password_hash

API_BASE = "http://127.0.0.1:8000/api"

# Authenticate tokens
admin_res = requests.post(f"{API_BASE}/auth/token", data={"username": "admin@olrac.com", "password": "olrac1234"}).json()
admin_token = admin_res["access_token"]
owner_res = requests.post(f"{API_BASE}/auth/token", data={"username": "owner@olrac.com", "password": "olrac1234"}).json()
owner_token = owner_res["access_token"]

# Setup screen for device auth
db = SessionLocal()
device_id = "bench_tv_perf_01"
raw_secret = "secret_" + uuid.uuid4().hex[:12]
screen = db.query(Screen).filter_by(device_id=device_id).first()
if not screen:
    screen = Screen(
        organization_id=1,
        name="Benchmark TV",
        device_id=device_id,
        device_secret_hash=get_password_hash(raw_secret),
        status="online",
    )
    db.add(screen)
else:
    screen.device_secret_hash = get_password_hash(raw_secret)
db.commit()
db.refresh(screen)
screen_db_id = screen.id
db.close()

auth_res = requests.post(f"{API_BASE}/screens/auth", json={"device_id": device_id, "device_secret": raw_secret}).json()
device_token = auth_res["access_token"]

ENDPOINTS = [
    {
        "name": "GET /api/screens/lock-pin (Tenant PIN lookup)",
        "method": "GET",
        "url": f"{API_BASE}/screens/lock-pin",
        "headers": {"Authorization": f"Bearer {owner_token}"},
        "json": None,
    },
    {
        "name": "PUT /api/screens/lock-pin (Tenant PIN update)",
        "method": "PUT",
        "url": f"{API_BASE}/screens/lock-pin",
        "headers": {"Authorization": f"Bearer {owner_token}"},
        "json": {"lock_pin": "5678"},
    },
    {
        "name": "PUT /api/screens/lock-enabled (Toggle Workspace Lock)",
        "method": "PUT",
        "url": f"{API_BASE}/screens/lock-enabled",
        "headers": {"Authorization": f"Bearer {owner_token}"},
        "json": {"lock_enabled": True},
    },
    {
        "name": "GET /api/screens/{device_id}/sync (Device Sync HTTP Fallback)",
        "method": "GET",
        "url": f"{API_BASE}/screens/{device_id}/sync",
        "headers": {"Authorization": f"Bearer {device_token}"},
        "json": None,
    },
    {
        "name": "POST /api/screens/{id}/remote-unlock (Remote Unlock Command Dispatch)",
        "method": "POST",
        "url": f"{API_BASE}/screens/{screen_db_id}/remote-unlock",
        "headers": {"Authorization": f"Bearer {owner_token}"},
        "json": {"reason": "Automated Benchmark Test"},
    },
    {
        "name": "POST /api/admin/security/challenge-response (Dynamic Challenge HMAC Gen)",
        "method": "POST",
        "url": f"{API_BASE}/admin/security/challenge-response",
        "headers": {"Authorization": f"Bearer {admin_token}"},
        "json": {
            "device_id": device_id,
            "nonce": "A1B2C3",
            "admin_password": "olrac1234",
            "reason": "HMAC benchmark test",
        },
    },
]

results = {}
ITERATIONS = 50

print(f"=== BENCHMARKING API PERFORMANCE ({ITERATIONS} requests per endpoint) ===")

for ep in ENDPOINTS:
    times_ms = []
    status_codes = []
    
    # Warmup
    requests.request(ep["method"], ep["url"], headers=ep["headers"], json=ep["json"])
    
    for _ in range(ITERATIONS):
        t0 = time.perf_counter()
        res = requests.request(ep["method"], ep["url"], headers=ep["headers"], json=ep["json"])
        t1 = time.perf_counter()
        times_ms.append((t1 - t0) * 1000.0)
        status_codes.append(res.status_code)
        
    times_ms.sort()
    p50 = times_ms[int(0.50 * len(times_ms))]
    p95 = times_ms[int(0.95 * len(times_ms))]
    p99 = times_ms[int(0.99 * len(times_ms))]
    avg = statistics.mean(times_ms)
    max_t = max(times_ms)
    min_t = min(times_ms)
    
    results[ep["name"]] = {
        "p50_ms": round(p50, 2),
        "p95_ms": round(p95, 2),
        "p99_ms": round(p99, 2),
        "avg_ms": round(avg, 2),
        "min_ms": round(min_t, 2),
        "max_ms": round(max_t, 2),
        "success_rate": round(sum(1 for s in status_codes if 200 <= s < 300) / len(status_codes) * 100, 1),
    }
    print(f"  {ep['name']}:")
    print(f"     p50={p50:.2f}ms | p95={p95:.2f}ms | p99={p99:.2f}ms | avg={avg:.2f}ms | success={results[ep['name']]['success_rate']}%")

with open("evidence/api_performance_benchmarks.json", "w") as f:
    json.dump(results, f, indent=2)

print("\nBenchmark saved to evidence/api_performance_benchmarks.json")
