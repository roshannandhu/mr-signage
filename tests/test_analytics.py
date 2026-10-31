"""Analytics rollup accuracy and performance: python tests/test_analytics.py

Seeds 100k+ play events, aggregates them, and checks both the API latency and that the
hourly upsert ADDS rather than replaces when late offline logs arrive for an hour that
was already rolled up.
"""

import asyncio
import os
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Own throwaway database, created BEFORE backend is imported.
#
# backend/database.py builds the engine at import time from DATABASE_URL, and
# backend/.env points that at the live olrac_signage database. An earlier version of this
# file imported SessionLocal with no isolation and seeded 100,100 play_logs plus a whole
# organisation straight into production. Every other script in tests/ sets up its own
# database first; this one must too.
import psycopg2  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402

TEST_DB = "olrac_test_analytics"
try:
    _admin = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
    _admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    _admin.cursor().execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    _admin.cursor().execute(f"CREATE DATABASE {TEST_DB} OWNER olrac")
    _admin.close()
except Exception:
    pass

os.environ["DATABASE_URL"] = f"postgresql://olrac:olrac_password@localhost:5432/{TEST_DB}"
os.environ["SECRET_KEY"] = "analytics-test-secret-key-not-for-production"
os.environ["INITIAL_ADMIN_USERNAME"] = "analytics-owner"
os.environ["INITIAL_ADMIN_PASSWORD"] = "analytics-password-123"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock"
os.environ["PAYMENT_PROVIDER"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402

# Build the schema explicitly. backend.main used to do this as an import side effect,
# so importing the app silently wrote to whatever DATABASE_URL pointed at; it now runs
# in the lifespan, which a bare TestClient(app) never starts. Each isolated script owns
# its own database anyway, so creating it here is the honest version of what was
# happening implicitly before.
from backend import database as _bootstrap_db, models as _bootstrap_models
_bootstrap_models.Base.metadata.create_all(bind=_bootstrap_db.engine)
from backend.database import SessionLocal  # noqa: E402
from backend.models import (  # noqa: E402
    User, Organization, Screen, Campaign, PlayLog, PlayLogHourlyRollup,
)
from backend.worker import aggregate_play_logs  # noqa: E402
from tests._test_plans import grant_plan  # noqa: E402

client = TestClient(app)


def run():
    db = SessionLocal()
    
    # 1. Setup throwaway org & campaign
    org = db.query(Organization).filter_by(slug="perf-org").first()
    if not org:
        org = Organization(name="Perf Org", slug="perf-org")
        db.add(org)
        db.flush()
        grant_plan(db, org, ads_counting=True)  # the stats call below is sold behind this flag
        db.commit()
    elif org.plan_id is None:
        grant_plan(db, org, ads_counting=True)
        db.commit()
    
    from backend.routers.auth import get_password_hash
    # Create an owner
    owner = db.query(User).filter_by(username="perf_owner").first()
    if not owner:
        owner = User(username="perf_owner", hashed_password=get_password_hash("pwd"), role="owner", organization_id=org.id)
        db.add(owner)
    else:
        owner.hashed_password = get_password_hash("pwd")
    db.commit()
    
    # Create Campaign
    campaign = db.query(Campaign).filter_by(name="Perf Campaign", organization_id=org.id).first()
    if not campaign:
        campaign = Campaign(name="Perf Campaign", organization_id=org.id)
        db.add(campaign)
        db.commit()
    
    # Create Screen
    screen = db.query(Screen).filter_by(name="Perf Screen", organization_id=org.id).first()
    if not screen:
        screen = Screen(name="Perf Screen", organization_id=org.id)
        db.add(screen)
        db.commit()
    
    # Clear existing play logs for this campaign
    db.query(PlayLog).filter(PlayLog.campaign_id == campaign.id).delete()
    db.query(PlayLogHourlyRollup).filter(PlayLogHourlyRollup.campaign_id == campaign.id).delete()
    db.commit()
    
    # 2. Seed 100k+ events using fast bulk inserts
    events = []
    base_time = datetime.now(timezone.utc) - timedelta(days=2)
    
    # We'll do chunks to not blow up memory
    for chunk in range(10):
        chunk_events = []
        for i in range(10000):
            event_time = base_time + timedelta(seconds=i)
            chunk_events.append(PlayLog(
                event_id=str(uuid.uuid4()),
                screen_id=screen.id,
                organization_id=org.id,
                media_id=None,
                campaign_id=campaign.id,
                device_started_at=event_time,
                device_finished_at=event_time + timedelta(seconds=10),
                corrected_started_at=event_time,
                corrected_finished_at=event_time + timedelta(seconds=10),
                duration_ms=10000,
                status="completed",
                aggregated=False
            ))
        db.bulk_save_objects(chunk_events)
        db.commit()
        events.extend(chunk_events)
        
    total_raw_count = db.query(PlayLog).filter(PlayLog.campaign_id == campaign.id).count()
    assert total_raw_count == 100000
    
    # 3. Run aggregation and time it
    start_time = time.time()
    # run async func synchronously
    asyncio.run(aggregate_play_logs({}))
    end_time = time.time()
    
    agg_time = end_time - start_time
    print(f"Aggregation took {agg_time:.2f} seconds")
    assert agg_time < 10.0, f"Aggregation took too long: {agg_time}s"
    
    # 4. Check API stats
    # Get token
    r_login = client.post("/api/auth/token", data={"username": "perf_owner", "password": "pwd"})
    token = r_login.json()["access_token"]
    
    api_start = time.time()
    r_stats = client.get(f"/api/analytics/campaigns/{campaign.id}/stats", headers={"Authorization": f"Bearer {token}"})
    api_end = time.time()
    api_time = api_end - api_start
    print(f"API took {api_time:.2f} seconds")
    assert api_time < 1.0, f"API took too long: {api_time}s"
    
    stats = r_stats.json()
    print("API Response:", stats)
    assert r_stats.status_code == 200, f"Expected 200, got {r_stats.status_code}. Response: {stats}"
    assert stats["lifetime"]["total_plays"] == 100000
    assert stats["lifetime"]["success_percent"] == 100.0

    # 5. Check billing correctness (Upsert ADD)
    # Add 100 more events for an hour that was ALREADY aggregated
    more_events = []
    for i in range(100):
        more_events.append(PlayLog(
            event_id=str(uuid.uuid4()),
            screen_id=screen.id,
            organization_id=org.id,
            media_id=None,
            campaign_id=campaign.id,
            device_started_at=base_time,
            device_finished_at=base_time + timedelta(seconds=10),
            corrected_started_at=base_time,
            corrected_finished_at=base_time + timedelta(seconds=10),
            duration_ms=10000,
            status="completed",
            aggregated=False
        ))
    db.bulk_save_objects(more_events)
    db.commit()
    
    asyncio.run(aggregate_play_logs({}))
    
    r_stats2 = client.get(f"/api/analytics/campaigns/{campaign.id}/stats", headers={"Authorization": f"Bearer {token}"})
    stats2 = r_stats2.json()
    assert stats2["lifetime"]["total_plays"] == 100100, (
        f"late offline logs did not ADD to the already-aggregated hour: {stats2['lifetime']}"
    )

    db.close()
    print("Analytics rollup check passed: 100000 -> 100100 after late arrivals")


# No test_* wrapper on purpose — conftest.py collects this file as a subprocess so it owns
# its database engine. A wrapper would also be imported into the shared pytest process and
# run a second time against whatever DATABASE_URL that process holds.
if __name__ == "__main__":
    run()
