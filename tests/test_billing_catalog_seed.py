"""ensure_billing_catalog seeds the Phase 3.1 flags for a fresh plan, and never for an existing one.

Backfilling a new feature flag onto a plan an existing tenant already pays for would silently
widen what they are paying for -- the approved rule is that a new flag starts OFF on an existing
plan and an operator turns it on by hand in the packages editor. DEFAULT_PLANS now lists five new
flags (payslips, ads_counting, remote_control, pdf_reports, kiosk_lock) for Starter/Business, so
the backfill loop had to be restricted to the four flags it has always carried, or every plan
already in a database would have quietly been granted all five on the next boot.

Pins:
  * a FRESH install (no plan rows yet) seeds every Phase 3.1 flag and the new ad-slot limits
  * an EXISTING Starter row missing the five new keys stays without them after a re-run
  * the original drift repair still works: an existing Business row missing emergency_alert
    (the gap the migration's seed left) gets it backfilled
  * a flag an operator explicitly set is never touched, even if DEFAULT_PLANS disagrees
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-catalog-seed-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-catalog-seed")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from backend import database, models  # noqa: E402
from backend.billing import DEFAULT_PLANS, ensure_billing_catalog, plan_features  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def run() -> None:
    # --- a fresh install: no plan rows exist yet ---------------------------------------------
    db = database.SessionLocal()
    ensure_billing_catalog(db)

    free = db.query(models.Plan).filter(models.Plan.slug == "free").one()
    starter = db.query(models.Plan).filter(models.Plan.slug == "starter").one()
    business = db.query(models.Plan).filter(models.Plan.slug == "business").one()

    check(plan_features(free).get("ads_counting") is True, f"a fresh Free plan should have ads_counting: {plan_features(free)}")
    for flag in ("payslips", "ads_counting", "remote_control", "pdf_reports", "kiosk_lock"):
        check(plan_features(starter).get(flag) is True, f"a fresh Starter plan should have {flag}: {plan_features(starter)}")
        check(plan_features(business).get(flag) is True, f"a fresh Business plan should have {flag}: {plan_features(business)}")
    check(free.max_ad_slots == 5, f"a fresh Free plan should allow 5 ad slots, got {free.max_ad_slots}")
    check(starter.max_ad_slots == 20, f"a fresh Starter plan should allow 20 ad slots, got {starter.max_ad_slots}")
    check(business.max_ad_slots == 0, f"a fresh Business plan should be unlimited (0), got {business.max_ad_slots}")
    db.close()

    # --- an existing plan, as if seeded before Phase 3.1, must not gain the new flags ---------
    db = database.SessionLocal()
    old_starter = models.Plan(
        name="Starter", slug="old-starter", monthly_price_paise=99_900, yearly_price_paise=999_000,
        max_screens=10, max_clients=10, max_storage_bytes=25 * 1024 ** 3,
        feature_flags_json=json.dumps({"scheduling": True, "transitions": True}),
        is_active=True,
    )
    db.add(old_starter)
    db.commit()
    old_starter_id = old_starter.id
    db.close()

    # Point DEFAULT_PLANS at this row for the duration of the check by matching its slug --
    # ensure_billing_catalog only reconciles the slugs it knows about, so compare behaviour
    # against the real "starter" slug's backfill instead by renaming it to match.
    db = database.SessionLocal()
    row = db.query(models.Plan).filter(models.Plan.id == old_starter_id).one()
    row.slug = "starter-existing-probe"
    db.commit()
    db.close()

    # Patch DEFAULT_PLANS' slug for this one call so ensure_billing_catalog treats the probe
    # row as "the" starter plan without touching the real one already seeded above.
    patched = tuple(
        {**entry, "slug": "starter-existing-probe"} if entry["slug"] == "starter" else entry
        for entry in DEFAULT_PLANS
    )
    import backend.billing as billing_module
    original = billing_module.DEFAULT_PLANS
    billing_module.DEFAULT_PLANS = patched
    try:
        db = database.SessionLocal()
        ensure_billing_catalog(db)
        db.close()
    finally:
        billing_module.DEFAULT_PLANS = original

    db = database.SessionLocal()
    probed = db.query(models.Plan).filter(models.Plan.id == old_starter_id).one()
    flags = plan_features(probed)
    for flag in ("payslips", "ads_counting", "remote_control", "pdf_reports", "kiosk_lock"):
        check(flag not in flags, f"an existing plan should not gain {flag} from a backfill: {flags}")
    check(flags.get("scheduling") is True and flags.get("transitions") is True,
          f"the plan's own existing flags should survive untouched: {flags}")
    db.close()

    # --- the original drift repair (the four legacy flags) still works ------------------------
    db = database.SessionLocal()
    drifted_business = db.query(models.Plan).filter(models.Plan.slug == "business").one()
    # Simulate the migration-seeded gap: emergency_alert missing, and an operator's own
    # explicit choice to turn transitions off, which must never be reasserted.
    drifted_business.feature_flags_json = json.dumps({
        "scheduling": True, "transitions": False, "priority_support": True,
        "ads_counting": True, "payslips": True, "remote_control": True,
        "pdf_reports": True, "kiosk_lock": True,
    })
    db.commit()
    db.close()

    db = database.SessionLocal()
    ensure_billing_catalog(db)
    db.close()

    db = database.SessionLocal()
    repaired = db.query(models.Plan).filter(models.Plan.slug == "business").one()
    flags = plan_features(repaired)
    check(flags.get("emergency_alert") is True, f"the legacy drift repair should still backfill emergency_alert: {flags}")
    check(flags.get("transitions") is False, f"an operator's explicit choice must never be reasserted: {flags}")
    db.close()


if __name__ == "__main__":
    try:
        run()
    finally:
        database.engine.dispose()
        _db_file.unlink(missing_ok=True)
    if FAILURES:
        print("FAIL")
        for failure in FAILURES:
            print("  -", failure)
        sys.exit(1)
    print("OK - new feature flags seed a fresh plan and never backfill onto an existing one")
