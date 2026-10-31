# MR Signage Watchdog — build and TV setup

Keeps the signage player on screen: launches it at boot, and puts it back whenever it
exits, crashes, or someone presses Home.

## Build

```bash
./build.sh
```

Produces `build/mr-watchdog.apk`, signed with the debug key. No Gradle — the app is five
Java files with no dependencies, so it builds straight from the SDK's `aapt2`, `javac`, `d8`
and `apksigner`. Needs `ANDROID_HOME` (or the SDK in its default location) and a JDK; set
`JAVA_HOME` if yours is not at `~/jdk-17/jdk-17.0.2`.

## Provision a TV

```bash
./provision-tv.sh                    # the only connected device
./provision-tv.sh 192.168.0.42:5555  # a specific one, after adb connect
```

Installs, configures, and then **verifies** — it fails loudly rather than reporting success
on a TV that will not actually work. Run it once per TV.

## The three things that silently break this

Each of these was observed on a real device. They are why a watchdog can look correctly
installed and still never run.

**1. Android 14+ blocks accessibility services for sideloaded apps.** This is the big one.
`settings put ... enabled_accessibility_services` appears to succeed and then reverts to
`null` a few seconds later, with nothing logged. The watchdog is installed, looks
configured, and is dead. Lifted with:

```bash
adb shell appops set com.ablesign.bootlauncher ACCESS_RESTRICTED_SETTINGS allow
```

On a TV you cannot reach over ADB, the same switch lives at **Settings → Apps → Watchdog →
⋮ → Allow restricted settings**.

**2. Updating the watchdog disables its own service.** Reinstalling the APK clears
`enabled_accessibility_services`. After any watchdog update, re-run `provision-tv.sh` or the
TV will boot to nothing. This does not apply to updating the *player*.

**3. The watchdog must be the HOME app.** Crash recovery works by the system returning to
HOME when the player exits — that is the trigger. If the stock launcher is HOME, the TV
lands there instead and the player never comes back. `provision-tv.sh` sets it and warns if
it did not take.

## Retargeting

One APK works for any player. The target is stored in SharedPreferences and survives
reboots:

```bash
adb shell am broadcast -a com.ablesign.bootlauncher.CONFIGURE \
    -n com.ablesign.bootlauncher/.ConfigureReceiver \
    -e package com.olrac.signage -e activity com.olrac.signage.MainActivity
```

## Checking a TV

```bash
adb shell settings get secure enabled_accessibility_services
```

Must name `WatchdogAccessibilityService`. If it says `null`, it is problem 1 or 2 above.

```bash
adb logcat -d -s WatchdogA11y BootLauncher
```

`AbleSign launched` is the watchdog starting the player. `WatchdogA11y` lines are the
boot-time launch; `BootLauncher` lines are recovery after the player exited.

## How recovery actually works

| Trigger | Handled by | When |
|---|---|---|
| Boot | `BootReceiver` → AlarmManager | `BOOT_COMPLETED`, fires via the system process so OEM background-launch limits do not apply |
| Service start | `WatchdogAccessibilityService` | Once per process, ~12s after connect |
| Player exits or crashes | `MainActivity.onResume` | Every time the watchdog is resumed as HOME, debounced to one attempt per 10s |

The accessibility service launches the player **once** per process lifetime. Everything
after that is the HOME path, which is why point 3 above is not optional.

## Known issue in the player (not the watchdog)

The player's own `BootReceiver` calls `startForegroundService()` for
`ACTION_MY_PACKAGE_REPLACED` and `ACTION_USER_UNLOCKED`. Android 12+ does not allow that
from those broadcasts and throws `ForegroundServiceStartNotAllowedException`; the receiver
catches and logs it, so nothing crashes, but the playback service (sync, telemetry, wake
lock) does not start on a package replace. `BOOT_COMPLETED` is exempt, so ordinary boots are
unaffected.
