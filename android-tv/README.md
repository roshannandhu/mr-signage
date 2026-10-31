# MR Signage Android TV player

## Supported devices

- Android TV 8.0/API 26 or newer with Android's Leanback/TV feature.
- A managed/default-HOME deployment is required for guaranteed unattended launch
  after a reboot. A normal sideloaded app cannot bypass Android's background-activity
  launch restrictions.
- The app holds a foreground `specialUse` service and a partial wake lock while
  running, and the player window uses `FLAG_KEEP_SCREEN_ON`. Android 14+ deployments
  through Google Play must declare and justify this foreground-service use in Play
  Console.

## Build-time server configuration

There are two environment flavors:

| Flavor | Default | Network policy |
|---|---|---|
| `dev` | `http://signage.local:8000/` | Explicitly permits cleartext for emulator/LAN development |
| `production` | `https://signage.example.com/` | Cleartext denied; HTTPS only |

Always override the placeholder for a deployable build. The URL must include `http`
or `https`; a trailing slash is normalized automatically.

```powershell
cd android-tv
gradle :app:assembleDevDebug `
  -PMR_API_BASE_URL=http://192.168.1.20:8000/

gradle :app:assembleProductionRelease `
  -PMR_API_BASE_URL=https://signage.example.com/
```

Debug APK output:

```text
app/build/outputs/apk/dev/debug/app-dev-debug.apk
```

Configure a release signing key in the normal Android build/CI secret store before
distributing a release APK. Every future in-place update must use the same key and a
higher `versionCode`.

## Release signing

Release builds are signed with the debug key unless `android-tv/keystore.properties` exists
(see `keystore.properties.example`). That file and the `.jks` are gitignored — the key never
enters the repo.

Generate the key once, and keep the only copy backed up somewhere safe:

    keytool -genkeypair -v -keystore android-tv/olrac-release.jks       -alias olrac -keyalg RSA -keysize 2048 -validity 10000

**Losing this key means no existing install can ever be updated again** — Android refuses an
update whose signature does not match. Switching to it from the debug key has the same effect
once: every TV already in the field must be uninstalled and reinstalled by hand to cross over.
Do that deliberately, not by accident.

## Install and pair

1. Enable developer options/ADB on the TV and install the APK:

   ```powershell
   adb connect TV_IP_ADDRESS
   adb install -r app/build/outputs/apk/dev/debug/app-dev-debug.apk
   ```

2. Launch MR Signage. If the preconfigured API URL is wrong, edit **Server URL**, then
   choose **Save and reconnect**. After pairing, reopen this screen from the remote with
   **Up, Up, Down, Down, OK** within five seconds, then enter the screen's 4-digit
   maintenance pin. The pin is shown under **Details** on the screen's dashboard page; a
   TV that has never synced accepts `0000` until its first sync.
3. Enter the six-digit code in the web dashboard and assign a playlist. Wait for the
   first complete download before testing offline playback.

Server-provided media URLs containing `localhost` or `127.0.0.1` are rewritten to the
configured API host for local development. Public/CDN URLs are left unchanged.

## Kiosk and boot provisioning

1. On the player's setup screen choose **Set as default TV launcher**, accept Android's
   HOME-role prompt, and reboot once to verify it.
2. If the vendor launcher still wins after reboot, enroll the TV with the OEM's
   device-owner/kiosk solution and configure `com.olrac.signage/.MainActivity` as the
   persistent HOME activity. This OEM provisioning is part of deployment, not an app
   permission that can be silently granted by a sideloaded APK.
3. Exempt MR Signage from vendor battery/background restrictions where the TV exposes such
   controls.

The boot receiver listens for locked boot, normal boot, vendor quick boot, user unlock,
and package replacement. It starts the playback-protection foreground service; the
default-HOME role is the supported mechanism that brings the UI to the foreground on
modern Android.

## Acceptance checks

Use a playlist whose media has fully cached, then verify all of these on every target
TV/OEM firmware:

1. Disconnect both Wi-Fi/Ethernet and make the API unreachable. Force-stop and cold
   launch MR Signage: cached content should start within five seconds.
2. With MR Signage set as HOME and the API unreachable, power-cycle the TV: cached content
   should be visible within 60 seconds with no remote input.
3. Clear app data or use a fresh TV while the API is unreachable: the setup screen
   should say pairing will resume automatically. Restore the network/API and verify a
   pairing code appears without restarting the app.
4. Leave a mixed playlist running for at least two hours. Confirm the display does not
   blank and playback does not stop.
5. Install a same-key APK with a higher `versionCode` using `adb install -r`; the
   player should return to the foreground and retain its cached playlist.

The repository's API-36 Android TV emulator gate covers the standard Android behavior.
The physical two-hour and power-cycle checks remain mandatory for each supported OEM
model because launchers and energy policies are vendor-controlled.
