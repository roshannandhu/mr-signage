# MR Signage — 80-TV Rollout Deployment Guide

This document covers the end-to-end deployment process for a new production environment, from standing up the server stack to provisioning physical Android TVs in the field.

> **Deploying to Oracle Cloud / Cloudflare / Supabase instead?** See
> [DEPLOYMENT_CLOUD.md](DEPLOYMENT_CLOUD.md). This guide assumes Postgres and Redis run
> in the same Compose stack and the dashboard is served from a container; the split
> hosting path differs in enough places that mixing the two will not work.

## 1. Server Environment Setup

Before starting the stack, ensure the following mandatory environment variables are set in your `.env` file (see `.env.example` for reference):

- `SECRET_KEY`: A cryptographically secure random string used for JWT signing.
- `PUBLIC_BASE_URL`: The public-facing URL of the backend (e.g., `https://api.mrsignage.com`). This must be correct, as it is used to generate absolute URLs for media assets and APK downloads.
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`, `S3_ENDPOINT_URL`: R2/S3 credentials for media storage.
- `PLAY_LOG_RETENTION_DAYS`: Days of raw proof-of-play logs to keep (default 7). At ~316MB/day for 100 screens this is the fastest-growing table in the database; size it against the database you have. `ROLLUP_RETENTION_DAYS` (default 400) bounds the aggregated history separately.

## 2. Storage Configuration

You must explicitly configure your storage backend depending on your deployment scale:

- **Default Local Storage (`AWS_ACCESS_KEY_ID=mock`)**: Media is saved to the local disk. This requires the shared `uploads_data` volume in Docker so both the `backend` and `worker` containers can see the files. **Warning:** Local storage is single-host only. Moving to a second backend host or scaling horizontally will cause media to be lost or become inaccessible.
- **Production Cloud Storage (R2/S3)**: Set real Cloudflare R2 (or AWS S3) credentials in your `.env`. Media is uploaded directly to the object storage bucket, and TVs download assets directly via signed URLs. The backend API never proxies large files. **This is strictly required to scale past one backend instance.**

## 3. Start the Stack

The entire application runs via Docker Compose.

```bash
# Start Postgres, Redis, Backend, Worker, and Frontend
docker compose up -d
```

Verify all 5 containers (`mr_db`, `mr_redis`, `mr_backend`, `mr_worker`, `mr_frontend`) are healthy:
```bash
docker compose ps
```

## 4. Database Migration

The database schema is managed by Alembic. Once the database container is healthy, run the migrations to create all tables:

```bash
docker compose exec backend alembic -c backend/alembic.ini upgrade head
```

## 5. Create the Platform Owner

Before anyone can log into the dashboard, you must seed the initial owner account. Provide the desired username as an argument.

```bash
docker compose exec backend python -m backend.seed_admin <username>
```
*Note: This script will prompt you for a password interactively. It does not store credentials in any file.*

## 6. TV Enrollment & Provisioning

Instead of manually typing a 6-digit code with a remote control 80 times, use the zero-touch enrollment flow.

1. Log into the dashboard (`http://localhost:3000` or your production domain) using the owner account created above.
2. Navigate to **Dashboard > Enrollment**.
3. Generate a new **Enrollment Token**. Set an expiry and max uses (e.g., 100 uses). 
4. Copy the token. **This is a one-time view credential.**
5. Build the Android TV APK with this token baked in, or provide it during the installer wizard on the TV.

## 7. Watchdog Setup (Crucial for Budget TVs)

Budget Android TVs (like the Realtek 2K D5STV) aggressively kill background processes and ignore standard boot receivers. To ensure the signage app recovers from power cuts automatically, you must install the **Watchdog Accessibility Service**.

### Build the Watchdog
Before compiling the watchdog APK in `android-watchdog/`, you **must** update the target package constant in `WatchdogAccessibilityService.java`:
```java
// Change this from tv.ablesign.app to com.olrac.signage
public static final String ABLESIGN = "com.olrac.signage";
```

### Install via ADB on the TV
Connect to the TV via ADB and install both the main player APK and the Watchdog APK.

```bash
# Disable Play Protect blocking
adb shell settings put global verifier_verify_adb_installs 0
adb shell settings put global package_verifier_enable 0

# Install APKs
adb install -r signage-player.apk
adb install -r watchdog.apk

# Enable the Watchdog Accessibility Service (Survives Reboot)
adb shell settings put secure enabled_accessibility_services com.ablesign.bootlauncher/.WatchdogAccessibilityService
adb shell settings put secure accessibility_enabled 1

# Whitelist both apps from battery optimization
adb shell dumpsys deviceidle whitelist +com.olrac.signage
adb shell dumpsys deviceidle whitelist +com.ablesign.bootlauncher
```

### Grant the overlay appop (REQUIRED — "Open app" does nothing without it)

```bash
adb shell appops set com.olrac.signage SYSTEM_ALERT_WINDOW allow
```

Not optional, and not cosmetic. Since Android 12 an app in the background may not start its
own activity, and that applies to every path the player has: a direct `startActivity`, a
full-screen-intent notification, and an `AlarmManager` PendingIntent are all refused.
Holding SYSTEM_ALERT_WINDOW is what puts the app on the system's background-activity-start
allowlist.

Verified by A/B on a Lenovo TB-8505F (Android 13): with the appop denied the dashboard's
"Open app" button leaves the player in the background and logcat shows

    W ActivityTaskManager: Background activity start [callingPackage: com.olrac.signage;
      ... allowBackgroundActivityStart: false]

With it allowed, the same request brings MainActivity to the foreground every time. The
player logs a warning naming this command whenever the appop is missing, so a panel in that
state is greppable rather than mysterious:

    W PlayerLauncher: SYSTEM_ALERT_WINDOW is not granted (reason=sync_command).

Confirm per panel with `adb shell appops get com.olrac.signage SYSTEM_ALERT_WINDOW`; the
answer must be `allow`. `default` means it was never granted.


### Verification
Reboot the TV (`adb shell reboot`). The Android OS will start, wait ~12 seconds to settle, and the Watchdog will automatically bring `com.olrac.signage` to the foreground.
