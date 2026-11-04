import concurrent.futures
import time
import requests

API_BASE = "http://127.0.0.1:8000/api"

# Get owner token
owner_res = requests.post(f"{API_BASE}/auth/token", data={"username": "owner@olrac.com", "password": "olrac1234"}).json()
owner_token = owner_res["access_token"]

HEADERS = {"Authorization": f"Bearer {owner_token}"}

def update_pin(pin: str):
    t0 = time.perf_counter()
    res = requests.put(f"{API_BASE}/screens/lock-pin", headers=HEADERS, json={"lock_pin": pin})
    return res.status_code, res.json(), (time.perf_counter() - t0) * 1000.0

PINS = ["1111", "2222", "3333", "4444", "5555", "6666", "7777", "8888", "9999", "1234"]

print("=== CONCURRENCY AUDIT: 10 CONCURRENT PIN UPDATES ===")
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
    futures = [executor.submit(update_pin, p) for p in PINS]
    results = [f.result() for f in futures]

status_codes = [r[0] for r in results]
latencies = [r[2] for r in results]
print(f"Status codes: {status_codes}")
print(f"Latencies (ms): min={min(latencies):.2f}, avg={sum(latencies)/len(latencies):.2f}, max={max(latencies):.2f}")

# Check final state in DB
from backend.database import SessionLocal
from backend.models import Organization, OutboxEvent
from backend.pin_crypto import decrypt_pin

db = SessionLocal()
org = db.query(Organization).get(1)
print(f"Final Organization Credentials Version: {org.credentials_version}")
print(f"Final Decrypted PIN: {decrypt_pin(org.lock_pin)}")

# Check outbox events for channel org:1
events = db.query(OutboxEvent).filter_by(channel="org:1", event_type="credentials.changed").order_by(OutboxEvent.id.desc()).limit(10).all()
print(f"Recent Outbox 'credentials.changed' events: {len(events)}")
for ev in events[:3]:
    print(f"  Event ID {ev.id}: status={ev.status}, version={ev.payload.get('version')}, directive={ev.payload.get('directive')}")

db.close()
