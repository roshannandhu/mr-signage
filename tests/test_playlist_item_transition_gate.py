"""An item's own transition override is sold behind the "transitions" flag too.

update_playlist_transitions gated the playlist-wide default, but an item's override rides on
the same ordinary PUT /items/{id} write as duration and scheduling, and that route checked
nothing -- a workspace on a plan without "transitions" could still give one item its own
transition through the item editor, bypassing the one place the default was ever refused.

Pins:
  * a workspace without the flag gets 403 setting an item's transition, and the item is unchanged
  * clearing an item back to "inherit the default" (transition: null) is not the paid feature
  * duration/schedule writes with no transition field are unaffected
  * a workspace WITH the flag can still set one
  * a row that already carries a transition from before a downgrade still accepts an
    unrelated resave that leaves the transition unchanged -- only a genuinely NEW
    transition is gated, so losing the flag does not break editing an old item's duration
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-transition-gate-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-transition-gate")
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


def workspace(has_transitions: bool):
    db = database.SessionLocal()
    plan = models.Plan(
        name="Plan", slug=f"p-{uuid.uuid4().hex[:8]}",
        feature_flags_json=json.dumps({"transitions": has_transitions}),
        is_active=True, max_storage_bytes=10 * 1024 ** 3,
    )
    db.add(plan)
    db.flush()
    org = models.Organization(name="Venue", slug=f"v-{uuid.uuid4().hex[:8]}", status="active", plan_id=plan.id)
    db.add(org)
    db.flush()
    owner = models.User(organization_id=org.id, username=f"owner-{uuid.uuid4().hex[:6]}", email=None,
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add(owner)
    db.flush()
    content = models.Content(organization_id=org.id, type="image", file_url="/uploads/1/a.jpg", name="Ad")
    playlist = models.Playlist(organization_id=org.id, name="Loop")
    db.add_all([content, playlist])
    db.flush()
    item = models.PlaylistItem(playlist_id=playlist.id, content_id=content.id, duration=10, order=0)
    db.add(item)
    db.commit()
    token = login_token(owner)
    ids = {"playlist": playlist.id, "item": item.id, "plan": plan.id}
    db.close()
    return token, ids


def set_plan_feature(plan_id: int, key: str, value: bool) -> None:
    """What an admin turning a flag off for a plan does to the row -- not the route, since
    this only needs the resulting state, not a proof the admin endpoint writes it."""
    db = database.SessionLocal()
    plan = db.query(models.Plan).filter(models.Plan.id == plan_id).one()
    flags = json.loads(plan.feature_flags_json or "{}")
    flags[key] = value
    plan.feature_flags_json = json.dumps(flags)
    db.commit()
    db.close()


def run() -> None:
    client = TestClient(app)

    # --- a plan without the flag: refused, and the item is untouched ----------------------------
    token, ids = workspace(has_transitions=False)
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.put(f"/api/playlists/{ids['playlist']}/items/{ids['item']}",
                      json={"transition": "slide_left"}, headers=headers)
    check(resp.status_code == 403, f"setting a transition without the flag should be 403, got {resp.status_code}: {resp.text}")

    db = database.SessionLocal()
    stored = db.query(models.PlaylistItem).filter(models.PlaylistItem.id == ids["item"]).one()
    check(stored.transition is None, f"a refused write should not have touched the item, got {stored.transition!r}")
    db.close()

    # --- clearing back to "inherit" is not the paid feature --------------------------------------
    resp = client.put(f"/api/playlists/{ids['playlist']}/items/{ids['item']}",
                      json={"transition": None}, headers=headers)
    check(resp.status_code == 200, f"clearing an item's transition to inherit should be allowed, got {resp.status_code}: {resp.text}")

    # --- duration/schedule with no transition field is unaffected --------------------------------
    resp = client.put(f"/api/playlists/{ids['playlist']}/items/{ids['item']}",
                      json={"duration": 15}, headers=headers)
    check(resp.status_code == 200, f"a plain duration write should not need the flag, got {resp.status_code}: {resp.text}")

    # --- a plan WITH the flag can still set one ---------------------------------------------------
    token2, ids2 = workspace(has_transitions=True)
    headers2 = {"Authorization": f"Bearer {token2}"}
    resp = client.put(f"/api/playlists/{ids2['playlist']}/items/{ids2['item']}",
                      json={"transition": "slide_left"}, headers=headers2)
    check(resp.status_code == 200, f"a plan WITH transitions should be able to set one, got {resp.status_code}: {resp.text}")
    if resp.status_code == 200:
        check(resp.json().get("transition") == "slide_left", f"the set transition did not stick: {resp.json()}")

    # --- the flag is then turned off for that same workspace, as an admin edit would ------------
    set_plan_feature(ids2["plan"], "transitions", False)

    # A resave that leaves the transition exactly as it already was must still go through --
    # the item carries a transition from before the downgrade, and nothing about it is changing.
    resp = client.put(f"/api/playlists/{ids2['playlist']}/items/{ids2['item']}",
                      json={"transition": "slide_left", "duration": 20}, headers=headers2)
    check(resp.status_code == 200,
          f"resaving an item's unchanged transition after a downgrade should not be refused, got {resp.status_code}: {resp.text}")

    # Picking a genuinely DIFFERENT transition after the downgrade is the paid feature again.
    resp = client.put(f"/api/playlists/{ids2['playlist']}/items/{ids2['item']}",
                      json={"transition": "fade"}, headers=headers2)
    check(resp.status_code == 403,
          f"changing to a different transition after a downgrade should still be refused, got {resp.status_code}: {resp.text}")


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
    print("OK - an item's own transition override needs the transitions flag too")
