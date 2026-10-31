# P9 — Zero-touch provisioning for 80+ TVs (no ADB)

**Problem:** deployment currently needs ADB per TV to enable the accessibility watchdog.
That does not scale to 80 sites and cannot be done by a non-technical installer.

**Constraint that cannot be worked around:** an app may not enable its own accessibility
service. `settings put secure enabled_accessibility_services` requires
`WRITE_SECURE_SETTINGS`, which is `signature|privileged` — ADB or system app only. Do not
attempt to bypass this; any technique that appears to work is malware behaviour and will
be broken by the next OEM update.

**Solution:** provision MR Signage as **Device Owner** by QR code. One QR works for the whole
fleet. Device Owner supersedes the accessibility watchdog entirely and additionally
unlocks the silent APK install that P7 could not deliver.

---

## 1. Make the app a working Device Policy Controller

`SignageDeviceAdminReceiver` and the manifest entries already exist but nothing uses
`DevicePolicyManager`. Add `device/DeviceOwnerManager.kt`:

- `isDeviceOwner()` — `DevicePolicyManager.isDeviceOwnerApp(packageName)`
- `applyKioskPolicy()` — call on first run when device owner:
  - `setLockTaskPackages(admin, arrayOf(packageName))`
  - `addPersistentPreferredActivity(...)` for the HOME intent, so the TV boots straight
    into MR Signage with no launcher chooser, permanently
  - `setKeyguardDisabled(admin, true)` and `setStatusBarDisabled(admin, true)`
  - `setPermissionPolicy(admin, PERMISSION_POLICY_AUTO_GRANT)` so runtime permissions
    never prompt on a screen nobody is standing in front of
  - `setUninstallBlocked(admin, packageName, true)`
- `MainActivity` calls `startLockTask()` when device owner, so Home/Recents cannot exit.

**Every one of these must be conditional on `isDeviceOwner()`.** A non-provisioned TV must
keep working exactly as it does today — these calls throw `SecurityException` otherwise.

## 2. Silent updates (finishes P7)

When device owner, `UpdateManager` installs through `PackageInstaller` with no prompt.
Keep the existing prompt path for non-owner devices. Verify the sha256 first — that check
already exists and must not be skipped on the silent path.

## 3. Generate the provisioning QR

New owner-only dashboard page `/dashboard/provisioning`. The backend builds the JSON, the
frontend renders it as a QR:

```json
{
  "android.app.extra.PROVISIONING_DEVICE_ADMIN_COMPONENT_NAME":
      "com.olrac.signage/.receivers.SignageDeviceAdminReceiver",
  "android.app.extra.PROVISIONING_DEVICE_ADMIN_PACKAGE_DOWNLOAD_LOCATION":
      "https://<PUBLIC_BASE_URL>/static/mr-signage.apk",
  "android.app.extra.PROVISIONING_DEVICE_ADMIN_SIGNATURE_CHECKSUM": "<base64url SHA-256 of the signing cert>",
  "android.app.extra.PROVISIONING_WIFI_SSID": "<site wifi>",
  "android.app.extra.PROVISIONING_WIFI_PASSWORD": "<site wifi password>",
  "android.app.extra.PROVISIONING_WIFI_SECURITY_TYPE": "WPA",
  "android.app.extra.PROVISIONING_SKIP_ENCRYPTION": true,
  "android.app.extra.PROVISIONING_LEAVE_ALL_SYSTEM_APPS_ENABLED": true,
  "android.app.extra.PROVISIONING_ADMIN_EXTRAS_BUNDLE": {
      "enrollment_token": "<token from /api/enrollment-tokens>",
      "api_base_url": "https://<PUBLIC_BASE_URL>"
  }
}
```

Non-obvious details that will cost hours if missed:

- The **signature checksum** is the SHA-256 of the *signing certificate*, base64 **url-safe,
  no padding** — not the APK file hash. Wrong value = provisioning fails with no useful error.
- The APK URL must be **publicly reachable over HTTPS** from the TV before it has been
  configured. A localhost or LAN-only URL cannot work here.
- `PROVISIONING_LEAVE_ALL_SYSTEM_APPS_ENABLED` should stay true on budget OEM TVs;
  disabling system apps has bricked panels from some vendors.
- Wi-Fi credentials are embedded in the QR. Generate a **per-site** QR and treat the image
  as a secret — anyone who photographs it gets the Wi-Fi password and an enrollment token.
  Give the token a short expiry and a `max_uses` matching the site's TV count; both fields
  already exist.

## 4. Auto-enrol on first boot

Read `PROVISIONING_ADMIN_EXTRAS_BUNDLE` in `SignageDeviceAdminReceiver.onProfileProvisioningComplete`,
store `api_base_url` and call `/api/screens/enroll` with the embedded token. The TV then
appears in the dashboard by itself — no pairing code typed anywhere.

## 5. Retire the accessibility watchdog on provisioned devices

Once Device Owner works, `android-watchdog/` is only needed for TVs that cannot be
factory-reset. Document both paths; do not delete it.

---

## Deployment paths, in order of preference

| Path | Per TV | Requires | Gets you |
|---|---|---|---|
| **QR Device Owner** | scan a code, ~60s | factory reset + Wi-Fi | kiosk, silent updates, auto-enrol, boot |
| **HOME launcher** | one tap "Always" | nothing | boot into app; no silent update, no lock task |
| Accessibility watchdog | ADB commands | ADB access | boot only — current method |

## Definition of done

- One QR provisions a factory-reset Realtek Android 14 panel end to end: APK installed,
  Device Owner set, auto-enrolled, playing its assigned playlist, **no ADB and no typing**.
- Home and Recents cannot leave the app.
- Power cut → boots straight back into playback.
- A **non-provisioned** TV still installs and runs exactly as before — prove this, it is
  the regression that would strand the fleet already in the field.
- An update installs silently on a provisioned TV and still prompts on one that is not.

## Tests

Unit-test `DeviceOwnerManager` decision logic with `isDeviceOwner()` both true and false,
asserting no policy call is attempted when false. The provisioning flow itself can only be
verified on real hardware or an emulator started with a factory-reset image — say so
plainly rather than claiming automated coverage.
