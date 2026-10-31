"""One place to give a test workspace a plan with specific feature flags.

Every route now gated by require_feature/ensure_feature (Phase 3.1's "hide, don't lock")
refuses a workspace with no plan at all -- which is what a bare `models.Organization(...)`
in a test gets, since plan_id defaults to None. Before this, the gate did not exist, so
these organisations never needed an opinion about it. Rather than repeating the same
plan-creation boilerplate in every file that now needs one feature turned on, it lives here.
"""
import json


def grant_plan(db, organization, **flags: bool) -> None:
    """Give `organization` a plan with exactly these flags on (everything else off).

    Creates one plan row per unique flag set, reused by slug across calls/files, so a test
    run does not pile up a fresh plan table row for every assertion.
    """
    from backend import models

    slug = "test-plan-" + "-".join(sorted(k for k, v in flags.items() if v)) or "test-plan-bare"
    plan = db.query(models.Plan).filter(models.Plan.slug == slug).first()
    if not plan:
        plan = models.Plan(
            name="Test Plan", slug=slug,
            feature_flags_json=json.dumps(flags),
            is_active=True, max_storage_bytes=10 * 1024 ** 3,
        )
        db.add(plan)
        db.flush()
    organization.plan_id = plan.id
