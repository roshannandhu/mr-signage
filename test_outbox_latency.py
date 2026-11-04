import time
import json
from backend.database import SessionLocal
from backend.models import OutboxEvent
from backend.outbox import queue_outbox_event, process_outbox_batch

db = SessionLocal()

print("=== BENCHMARKING OUTBOX EVENT PROCESSING LATENCY ===")
latencies_ms = []

for i in range(20):
    t0 = time.perf_counter()
    event = queue_outbox_event(
        db,
        channel="org:1",
        event_type="screen.remote_unlock",
        payload={"command_id": f"bench_{i}", "action": "unlock"},
    )
    db.commit()
    t_queued = time.perf_counter()
    
    # Process batch
    processed_count = process_outbox_batch(db, batch_size=10)
    t_processed = time.perf_counter()
    
    queue_ms = (t_queued - t0) * 1000.0
    process_ms = (t_processed - t_queued) * 1000.0
    total_ms = (t_processed - t0) * 1000.0
    latencies_ms.append(total_ms)

latencies_ms.sort()
outbox_perf = {
    "iterations": 20,
    "p50_ms": round(latencies_ms[int(0.50 * len(latencies_ms))], 2),
    "p95_ms": round(latencies_ms[int(0.95 * len(latencies_ms))], 2),
    "p99_ms": round(latencies_ms[int(0.99 * len(latencies_ms))], 2),
    "avg_ms": round(sum(latencies_ms) / len(latencies_ms), 2),
    "min_ms": round(min(latencies_ms), 2),
    "max_ms": round(max(latencies_ms), 2),
}
print(f"Outbox Processing Latency: p50={outbox_perf['p50_ms']}ms | p95={outbox_perf['p95_ms']}ms | avg={outbox_perf['avg_ms']}ms")

with open("evidence/outbox_latency_benchmarks.json", "w") as f:
    json.dump(outbox_perf, f, indent=2)

db.close()
