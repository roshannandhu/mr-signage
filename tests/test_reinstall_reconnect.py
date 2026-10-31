"""One account on the TV and the dashboard, and a screen that survives a reinstall.

    python tests/test_reinstall_reconnect.py

The scenario this covers is the one an installer actually performs:

  1. The installer signs in ON THE TV with the same MR Signage account they use in the
     dashboard. The screen joins that workspace with nobody else involved.
  2. The dashboard shows exactly ONE screen -- not a ghost row per attempt.
  3. Content is assigned; the TV plays it.
  4. The app is reinstalled (or its data cleared). The TV must come back as the SAME
     screen, keep its playlist, and resume playing without anyone signing in again.
  5. A factory reset rotates ANDROID_ID, so the device id changes too. The screen is
     reclaimed through the installation id instead, and still does not duplicate.

Every step asserts the fleet count, because duplication is the failure mode that is
invisible until a quota is hit or a client is billed for screens that do not exist.
"""

import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEMP_DIR = tempfile.TemporaryDirectory(prefix="olrac-reinstall-test-", ignore_cleanup_errors=True)
DB_PATH = Path(TEMP_DIR.name) / "reinstall.db"
import psycopg2  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402

test_db_name = f"olrac_test_{DB_PATH.stem.replace('-', '_')}"
try:
    conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    conn.cursor().execute(f"DROP DATABASE IF EXISTS {test_db_name}")
    conn.cursor().execute(f"CREATE DATABASE {test_db_name} OWNER olrac")
    conn.close()
except Exception:
    pass
os.environ["DATABASE_URL"] = f"postgresql://olrac:olrac_password@localhost:5432/{test_db_name}"
os.environ["SECRET_KEY"] = "reinstall-test-secret-not-for-production"
os.environ["INITIAL_ADMIN_USERNAME"] = "shop-owner"
os.environ["INITIAL_ADMIN_PASSWORD"] = "shop-password-123"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock"
os.environ["PAYMENT_PROVIDER"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.limiter import limiter  # noqa: E402
from backend.main import app  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)
limiter.enabled = False

client = TestClient(app)
client.__enter__()

# The TV's two identities, as DeviceState derives them.
#
# device_id is UUID.nameUUIDFromBytes("olrac_screen_$ANDROID_ID") -- DETERMINISTIC, so a
# reinstall regenerates the same value even though SharedPreferences was wiped. A factory
# reset rotates ANDROID_ID, so it changes there.
#
# installation_id prefers the HARDWARE SERIAL ("sn_<serial>"), the only identifier that
# survives a factory reset, and falls back to ANDROID_ID ("hw_<id>") when the serial is not
# readable.
#
# Step 5 below therefore models a DEVICE-OWNER panel, where the serial IS readable and the
# installation id is stable across a wipe. On a sideloaded install on Android 10+ the
# serial is blocked, installation_id falls back to ANDROID_ID, and a factory reset is NOT
# automatically recoverable -- see test_factory_reset_without_serial_is_visible below,
# which pins that honestly rather than pretending otherwise.
DEVICE_ID = "3f2b9c14-7a6d-3e51-9c88-0d4e2a7b6f01"
INSTALLATION_ID = "sn_RTK00A1B2C3"

ACCOUNT = {"username": "shop-owner", "password": "shop-password-123"}

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def dashboard_token() -> str:
    response = client.post("/api/auth/token", data=ACCOUNT)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def fleet(token: str) -> list[dict]:
    response = client.get("/api/screens/", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return response.json()


def tv_sign_in(device_id: str, installation_id: str, name: str = "Shop Front TV"):
    """What the player sends when the installer types their account on the TV."""
    return client.post(
        "/api/screens/sign-in",
        json={
            "device_id": device_id,
            "installation_id": installation_id,
            "username": ACCOUNT["username"],
            "password": ACCOUNT["password"],
            "name": name,
            "model": "Realtek TV",
            "manufacturer": "Realtek",
        },
    )


def tv_register(device_id: str, installation_id: str):
    """What the player sends on every cold start, before it knows anything."""
    return client.post(
        "/api/screens/register",
        json={
            "device_id": device_id,
            "installation_id": installation_id,
            "hardware_name": "Realtek TV",
            "device_model": "Realtek TV",
            "manufacturer": "Realtek",
        },
    )


def run() -> None:
    token = dashboard_token()

    # --- 1. The installer signs in on the TV with the dashboard account ---------------
    signed_in = tv_sign_in(DEVICE_ID, INSTALLATION_ID)
    check(signed_in.status_code == 200, f"TV sign-in failed: {signed_in.text}")
    if signed_in.status_code != 200:
        return
    screen = signed_in.json()
    screen_id = screen["id"]
    check(screen["status"] == "online", f"screen did not come up online: {screen['status']}")
    check(bool(screen.get("device_secret")), "TV sign-in issued no device credential")

    # --- 2. The dashboard shows exactly one screen ------------------------------------
    check(len(fleet(token)) == 1, f"expected 1 screen after sign-in, got {len(fleet(token))}")

    # Signing in again (installer retries, or the TV reboots mid-setup) must not add a row.
    tv_sign_in(DEVICE_ID, INSTALLATION_ID)
    tv_sign_in(DEVICE_ID, INSTALLATION_ID)
    check(len(fleet(token)) == 1, f"repeat sign-in duplicated the screen ({len(fleet(token))} rows)")

    # --- 3. Give it something to play --------------------------------------------------
    headers = {"Authorization": f"Bearer {token}"}
    playlist = client.post("/api/playlists/", json={"name": "Shop Loop"}, headers=headers)
    check(playlist.status_code in (200, 201), f"playlist creation failed: {playlist.text}")
    playlist_id = playlist.json()["id"]

    db = database.SessionLocal()
    try:
        advert = models.Content(
            organization_id=db.query(models.Screen).filter(models.Screen.id == screen_id).one().organization_id,
            type="video", file_url="/uploads/advert.mp4", name="Client Advert",
            file_size_bytes=1024, status="ready",
        )
        db.add(advert)
        db.flush()
        db.add(models.PlaylistItem(playlist_id=playlist_id, content_id=advert.id, duration=15, order=0))
        db.commit()
    finally:
        db.close()

    assigned = client.post(f"/api/screens/{screen_id}/assign/{playlist_id}", headers=headers)
    check(assigned.status_code == 200, f"playlist assignment failed: {assigned.text}")

    playing = client.get(f"/api/screens/{DEVICE_ID}/sync")
    check(playing.status_code == 200, f"sync failed: {playing.text}")
    before = playing.json()
    check(before.get("playlist") is not None, "the screen was given no playlist to play")
    check(
        bool(before.get("playlist", {}).get("items")),
        "the playlist reached the TV with no items in it",
    )

    # --- 4. REINSTALL ------------------------------------------------------------------
    # SharedPreferences is wiped, so the player has no pairing flag, no cached playlist and
    # no device secret. But device_id is derived from ANDROID_ID rather than stored, so it
    # regenerates to the same value -- which is what lets the server recognise the TV. The
    # player's first call after a cold start is /register.
    reinstalled = tv_register(DEVICE_ID, INSTALLATION_ID)
    check(reinstalled.status_code == 200, f"register after reinstall failed: {reinstalled.text}")
    body = reinstalled.json()

    # This is the assertion that decides whether the installer has to drive back to site:
    # "waiting_pairing" means the player shows a sign-in screen instead of resuming.
    check(
        body["status"] != "waiting_pairing",
        "SITE VISIT: after reinstall the TV was asked to pair again instead of resuming",
    )
    check(body["id"] == screen_id, f"reinstall produced a different screen row ({body['id']} != {screen_id})")
    check(len(fleet(token)) == 1, f"reinstall duplicated the screen ({len(fleet(token))} rows)")

    # ...and the adverts come back on their own, from the server, with no local cache.
    resumed = client.get(f"/api/screens/{DEVICE_ID}/sync")
    check(resumed.status_code == 200, f"sync after reinstall failed: {resumed.text}")
    after = resumed.json()
    check(after.get("playlist") is not None, "PLAYBACK LOST: no playlist after reinstall")
    if after.get("playlist"):
        check(
            after["playlist"]["id"] == playlist_id,
            "the TV came back on a different playlist than it was assigned",
        )
        check(
            len(after["playlist"]["items"]) == len(before["playlist"]["items"]),
            "adverts were lost across the reinstall",
        )

    # A reinstalled screen has no credential, so it must be handed a fresh one -- otherwise
    # it can never authenticate again and is stuck on the legacy path forever.
    check(
        bool(body.get("device_secret")),
        "reinstalled screen was given no new device credential",
    )

    # --- 5. FACTORY RESET (device-owner panel, serial readable) --------------------------
    # A reset rotates ANDROID_ID, so device_id changes. The serial does not, so
    # installation_id is unchanged and is the only thing that can tie the panel back to its
    # row. If that fails the fleet silently grows by one every time a screen is wiped --
    # which on an estate of identical hardware is indistinguishable from a new install.
    reset_device_id = "b7c4e2a9-1f30-3d62-8a45-6e9c0b1d4f77"
    reset = tv_register(reset_device_id, INSTALLATION_ID)
    check(reset.status_code == 200, f"register after factory reset failed: {reset.text}")
    check(
        reset.json()["id"] == screen_id,
        "GHOST SCREEN: a factory reset created a second row instead of reclaiming the first",
    )
    check(len(fleet(token)) == 1, f"factory reset duplicated the screen ({len(fleet(token))} rows)")

    survivor = fleet(token)[0]
    check(
        survivor["playlist_id"] == playlist_id,
        "the reclaimed screen lost its playlist assignment",
    )

    # --- 6. The re-issued credential must actually authenticate --------------------------
    # Returning a secret is only half the job: if it does not work, the screen is still
    # dead the moment ALLOW_LEGACY_DEVICE_AUTH is turned off. Proven by exchanging it for a
    # token and syncing with that token alone -- no legacy fallback involved.
    new_secret = reset.json().get("device_secret") or body.get("device_secret")
    check(bool(new_secret), "no credential to test after reclaim")
    if new_secret:
        exchanged = client.post(
            "/api/screens/auth",
            json={"device_id": reset_device_id, "device_secret": new_secret},
        )
        check(exchanged.status_code == 200, f"re-issued credential was rejected: {exchanged.text}")
        if exchanged.status_code == 200:
            authed = client.get(
                f"/api/screens/{reset_device_id}/sync",
                headers={"Authorization": f"Bearer {exchanged.json()['access_token']}"},
            )
            check(authed.status_code == 200, f"authenticated sync failed: {authed.text}")
            check(
                authed.json().get("playlist") is not None,
                "authenticated sync returned no playlist",
            )
            # Only an authenticated screen is told its maintenance pin.
            check(
                authed.json().get("maintenance_pin") is not None,
                "an authenticated screen was refused its maintenance pin",
            )

    if failures:
        print("REINSTALL / RECONNECT FAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("same account on TV and dashboard, no duplicates, resumes after reinstall: verified")


def test_factory_reset_without_serial_is_visible() -> None:
    """A wipe on a panel whose serial is unreadable CANNOT be auto-recovered.

    Pinned deliberately, because the failure is otherwise silent and only shows up as a
    fleet count that creeps upward. Both identities are ANDROID_ID-derived on that tier, a
    reset rotates it, and nothing ties the panel to its old row -- so it is a genuinely new
    screen as far as the server can tell, and re-adopting it has to be an operator action.

    Asserting the duplicate is the point: if a future change makes this pass silently, it
    is matching on something that is NOT unique per unit -- Build.MODEL, say -- and on an
    estate of identical TVs that would collapse the whole fleet onto one row.
    """
    token = dashboard_token()
    before = len(fleet(token))

    sideloaded_device = "c1d2e3f4-0000-3aaa-9bbb-1c2d3e4f5a60"
    sideloaded_install = "hw_1111222233334444"       # ANDROID_ID tier, no serial available
    tv_sign_in(sideloaded_device, sideloaded_install, name="Back Room TV")
    check(len(fleet(token)) == before + 1, "the sideloaded screen did not register")

    # The wipe: both identities change together, because both came from ANDROID_ID.
    wiped_device = "d2e3f4a5-0000-3bbb-9ccc-2d3e4f5a6b71"
    wiped_install = "hw_5555666677778888"

    # The panel is not recognised, so it registers as an UNCLAIMED screen -- organisation
    # NULL, status waiting_pairing -- and the player shows its sign-in screen. It is not in
    # the tenant's fleet yet, which is why the duplicate is invisible at this point.
    registered = tv_register(wiped_device, wiped_install)
    check(
        registered.json()["status"] == "waiting_pairing",
        "an unrecognised panel should come back unclaimed, not attached to a workspace",
    )
    check(
        len(fleet(token)) == before + 1,
        "an unclaimed screen must not appear in the tenant's fleet",
    )

    # The duplicate appears at the moment the installer signs in again on that TV, which is
    # exactly what they will do when they see the sign-in screen. THIS is the ghost row.
    tv_sign_in(wiped_device, wiped_install, name="Back Room TV")
    after = len(fleet(token))
    check(
        after == before + 2,
        f"expected an unrecoverable wipe to become a SECOND screen, fleet went {before + 1} -> {after}",
    )


def test_register_will_not_mint_a_credential_for_a_stranger() -> None:
    """A caller holding only the device id must not be handed a device secret.

    /register re-issues a credential to a screen that has lost one, which is what lets a
    reinstalled panel recover without a site visit. It used to accept possession of the
    device id as the whole proof -- and a device id is not a secret: /register echoes it,
    the dashboard shows it, the logs print it. So anyone who read one could take over the
    screen, and because the re-issue overwrites the stored hash, the real panel was locked
    out at the same moment. The hardware installation id is the proof now required.
    """
    token = dashboard_token()
    device = "stranger-dev-" + uuid.uuid4().hex[:8]
    install = "sn_" + uuid.uuid4().hex[:10]

    signed_in = tv_sign_in(device, install, name="Corner TV")
    check(signed_in.status_code == 200, f"setup sign-in failed: {signed_in.text}")

    # The genuine panel, reinstalled: same hardware id, no credential. Must recover.
    genuine = tv_register(device, install)
    check(genuine.status_code == 200, f"reinstall register failed: {genuine.text}")
    reissued = genuine.json().get("device_secret")
    check(reissued is not None, "a reinstalled screen was not re-issued a credential")

    # An attacker who scraped the device id but has never touched the hardware.
    attacker = client.post(
        "/api/screens/register",
        json={"device_id": device, "installation_id": "sn_" + uuid.uuid4().hex[:10]},
    )
    check(
        attacker.json().get("device_secret") is None,
        "SECURITY: /register handed a device secret to a caller with a mismatched "
        "installation id",
    )

    # ...and the same request with no hardware id at all.
    bare = client.post("/api/screens/register", json={"device_id": device})
    check(
        bare.json().get("device_secret") is None,
        "SECURITY: /register handed a device secret to a caller presenting only a device id",
    )

    # The genuine credential must still work after those attempts, i.e. the refusal did
    # not quietly rotate the hash and lock the real panel out anyway.
    still_valid = client.post(
        "/api/screens/auth", json={"device_id": device, "device_secret": reissued}
    )
    check(
        still_valid.status_code == 200,
        f"a refused takeover invalidated the real screen's credential: {still_valid.text}",
    )


if __name__ == "__main__":
    try:
        run()
        test_factory_reset_without_serial_is_visible()
        test_register_will_not_mint_a_credential_for_a_stranger()
        if failures:
            print("REINSTALL / RECONNECT FAILURES:")
            for failure in failures:
                print(f"  - {failure}")
            raise SystemExit(1)
        print("factory reset without a serial correctly shows as a new screen: verified")
    finally:
        client.__exit__(None, None, None)
