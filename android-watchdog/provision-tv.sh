#!/usr/bin/env bash
#
# Provision one TV: install the watchdog, point it at the player, and make every setting
# it depends on stick.
#
#   ./provision-tv.sh                    # the only connected device
#   ./provision-tv.sh 192.168.0.42:5555  # a specific one (adb connect first if network)
#   ./provision-tv.sh emulator-5554
#
# Every step here is one that was observed failing on a real Android 14/16 device. Nothing
# is included "just in case".
set -euo pipefail
cd "$(dirname "$0")"

SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/AppData/Local/Android/Sdk}}"
ADB="$SDK/platform-tools/adb.exe"
[ -x "$ADB" ] || ADB="$SDK/platform-tools/adb"

WATCHDOG_PKG=com.ablesign.bootlauncher
WATCHDOG_ACT="$WATCHDOG_PKG/.MainActivity"
WATCHDOG_SVC="$WATCHDOG_PKG/$WATCHDOG_PKG.WatchdogAccessibilityService"
PLAYER_PKG=com.olrac.signage
PLAYER_ACT="$PLAYER_PKG.MainActivity"
APK=build/mr-watchdog.apk

TARGET="${1:-}"
if [ -n "$TARGET" ]; then DEV=(-s "$TARGET"); else DEV=(); fi
adb_() { "$ADB" "${DEV[@]}" "$@"; }

[ -f "$APK" ] || { echo "no $APK — run ./build.sh first" >&2; exit 1; }
adb_ get-state >/dev/null 2>&1 || { echo "no device. adb devices / adb connect <ip>:5555" >&2; exit 1; }

echo "  device   $(adb_ shell getprop ro.product.model | tr -d '\r') (Android $(adb_ shell getprop ro.build.version.release | tr -d '\r'))"

# 1. Let the sideload through -------------------------------------------------
adb_ shell settings put global verifier_verify_adb_installs 0 || true
adb_ shell settings put global package_verifier_enable 0 || true

# 2. Install ------------------------------------------------------------------
echo "  installing"
adb_ install -r "$APK" >/dev/null

# 3. Lift the restricted-settings block ---------------------------------------
# Only needed for the OPTIONAL accessibility extra. Android 14+ silently refuses to keep an
# accessibility service enabled for a sideloaded app — settings put appears to work and the
# value reverts to null seconds later. The watchdog no longer depends on it, so this is
# best-effort: it makes the extra stick where it can, and nothing breaks where it cannot.
echo "  allowing restricted settings (for the optional accessibility extra)"
adb_ shell appops set "$WATCHDOG_PKG" ACCESS_RESTRICTED_SETTINGS allow 2>/dev/null || true

# 4. Enable the accessibility extra ---------------------------------------------
# Optional. Boot is handled by BootReceiver's AlarmManager and recovery by the watchdog
# being HOME; neither needs this. It is still switched on where possible because it adds
# one more launch trigger on OEM builds that throttle the others.
# The component must be FULLY QUALIFIED to match what newer Android writes itself.
echo "  enabling accessibility extra (optional)"
adb_ shell settings put secure enabled_accessibility_services "$WATCHDOG_SVC"
adb_ shell settings put secure accessibility_enabled 1

# 5. Point the watchdog at the player ------------------------------------------
adb_ shell am broadcast -a com.ablesign.bootlauncher.CONFIGURE \
    -n "$WATCHDOG_PKG/.ConfigureReceiver" \
    -e package "$PLAYER_PKG" -e activity "$PLAYER_ACT" >/dev/null

# 6. Make it the launcher, and keep both apps awake -----------------------------
adb_ shell cmd package set-home-activity "$WATCHDOG_ACT" >/dev/null 2>&1 || true
adb_ shell dumpsys deviceidle whitelist "+$WATCHDOG_PKG" >/dev/null 2>&1 || true
adb_ shell dumpsys deviceidle whitelist "+$PLAYER_PKG" >/dev/null 2>&1 || true
# Only meaningful on Realme/OPPO builds; harmless elsewhere.
adb_ shell appops set "$WATCHDOG_PKG" AUTO_LAUNCH allow 2>/dev/null || true
adb_ shell appops set "$WATCHDOG_PKG" RUN_IN_BACKGROUND allow 2>/dev/null || true

# 7. Verify what actually matters ----------------------------------------------
sleep 3
echo
FAIL=0
ENABLED="$(adb_ shell settings get secure enabled_accessibility_services | tr -d '\r')"
case "$ENABLED" in
    *WatchdogAccessibilityService*) echo "  OK   accessibility extra enabled" ;;
    # Deliberately not a failure: the watchdog runs without it. It is one more launch
    # trigger on OEM builds that throttle the others, nothing more.
    *) echo "  note accessibility extra is off - not required, the watchdog works without it" ;;
esac

adb_ shell pm list packages 2>/dev/null | tr -d '\r' | grep -q "$WATCHDOG_PKG" \
    && echo "  OK   watchdog installed" \
    || { echo "  FAIL watchdog did not install"; FAIL=1; }

HOME_ACT="$(adb_ shell cmd shortcut get-default-launcher 2>/dev/null | tr -d '\r')"
case "$HOME_ACT" in
    *"$WATCHDOG_PKG"*) echo "  OK   watchdog is the launcher" ;;
    *) echo "  FAIL launcher is not the watchdog: $HOME_ACT"
       echo "       This one IS required: recovery works by the system returning to HOME."
       echo "       Set the watchdog as the default launcher on the TV."
       FAIL=1 ;;
esac

adb_ shell pm list packages 2>/dev/null | tr -d '\r' | grep -q "$PLAYER_PKG" \
    && echo "  OK   player $PLAYER_PKG installed" \
    || { echo "  FAIL player $PLAYER_PKG is not installed"; FAIL=1; }

echo
[ "$FAIL" = 0 ] && echo "  Provisioned. Reboot to confirm: the player should appear within ~15s." \
               || echo "  Finish the failed steps above, then re-run."
exit "$FAIL"
