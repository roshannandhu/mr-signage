#!/usr/bin/env bash
#
# Build the watchdog APK straight from the Android SDK build tools.
#
#   ./build.sh              -> build/mr-watchdog.apk
#   ./build.sh install      -> also adb install -r onto the connected device
#
# Why not Gradle: this app is five Java files, one resource and zero dependencies. Gradle
# would add a wrapper, a daemon and a pinned JDK for a build that javac finishes in a
# second — and its daemon needs a loopback socket, which is not always available. aapt2,
# javac, d8 and apksigner are the whole toolchain, exactly how the original launcher was
# built.
set -euo pipefail

cd "$(dirname "$0")"

SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/AppData/Local/Android/Sdk}}"
[ -d "$SDK" ] || { echo "Android SDK not found at $SDK — set ANDROID_HOME" >&2; exit 1; }

# Newest installed build-tools and platform, rather than a version pinned here that goes
# stale the moment the SDK updates.
BT="$(ls -d "$SDK"/build-tools/*/ | sort -V | tail -1)"
PLATFORM="$(ls -d "$SDK"/platforms/android-*/ | sort -V | tail -1)"
ANDROID_JAR="${PLATFORM}android.jar"
[ -f "$ANDROID_JAR" ] || { echo "no android.jar under $PLATFORM" >&2; exit 1; }

JAVA_HOME="${JAVA_HOME:-$HOME/jdk-17/jdk-17.0.2}"
JAVAC="$JAVA_HOME/bin/javac"
[ -x "$JAVAC" ] || { echo "javac not found at $JAVAC — set JAVA_HOME" >&2; exit 1; }

MIN_SDK=21
TARGET_SDK=34
OUT=build
APK="$OUT/mr-watchdog.apk"

echo "  sdk        $SDK"
echo "  build-tools $(basename "${BT%/}")"
echo "  platform   $(basename "${PLATFORM%/}")"

rm -rf "$OUT"
mkdir -p "$OUT/res" "$OUT/classes" "$OUT/dex"

# 1. Resources -----------------------------------------------------------------
"${BT}aapt2.exe" compile --dir res -o "$OUT/res.zip"
"${BT}aapt2.exe" link \
    -I "$ANDROID_JAR" \
    --manifest AndroidManifest.xml \
    --java "$OUT/gen" \
    --min-sdk-version "$MIN_SDK" \
    --target-sdk-version "$TARGET_SDK" \
    -o "$OUT/base.apk" \
    "$OUT/res.zip"

# 2. Java ----------------------------------------------------------------------
# R.java lands in gen/ only when the app declares resources it references by id; this app
# does not, so the glob is allowed to come back empty.
mapfile -t SOURCES < <(find src "$OUT/gen" -name '*.java' 2>/dev/null)
"$JAVAC" -source 8 -target 8 -nowarn \
    -bootclasspath "$ANDROID_JAR" -classpath "$ANDROID_JAR" \
    -d "$OUT/classes" "${SOURCES[@]}"

# 3. Dex -----------------------------------------------------------------------
# The SDK's d8.bat and apksigner.bat re-expand %JAVA_HOME% unquoted, so they break on a JDK
# path containing a space. Their jars are invoked directly here, which skips cmd entirely.
mapfile -t CLASSES < <(find "$OUT/classes" -name '*.class')
"$JAVA_HOME/bin/java" -cp "$(cygpath -w "${BT}lib/d8.jar")" com.android.tools.r8.D8 \
    --lib "$(cygpath -w "$ANDROID_JAR")" --min-api "$MIN_SDK" \
    --output "$(cygpath -w "$OUT/dex")" "${CLASSES[@]}"

# 4. Package and sign ----------------------------------------------------------
cp "$OUT/base.apk" "$OUT/unsigned.apk"
# `zip` is not guaranteed on Windows, but the JDK ships jar, which writes the same format.
(cd "$OUT/dex" && "$JAVA_HOME/bin/jar" uf "../unsigned.apk" classes.dex)

"${BT}zipalign.exe" -f 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"

KEYSTORE="$HOME/.android/debug.keystore"
if [ ! -f "$KEYSTORE" ]; then
    echo "  creating a debug keystore"
    "$JAVA_HOME/bin/keytool" -genkeypair -keystore "$KEYSTORE" -alias androiddebugkey \
        -storepass android -keypass android -keyalg RSA -keysize 2048 -validity 10950 \
        -dname "CN=Android Debug,O=Android,C=US"
fi

"$JAVA_HOME/bin/java" -jar "$(cygpath -w "${BT}lib/apksigner.jar")" sign \
    --ks "$(cygpath -w "$KEYSTORE")" --ks-pass pass:android --key-pass pass:android \
    --ks-key-alias androiddebugkey \
    --out "$(cygpath -w "$APK")" "$(cygpath -w "$OUT/aligned.apk")"

rm -f "$OUT/unsigned.apk" "$OUT/aligned.apk" "$OUT/base.apk" "$OUT/res.zip"
echo
echo "  built $APK ($(du -h "$APK" | cut -f1))"

if [ "${1:-}" = "install" ]; then
    "$SDK/platform-tools/adb.exe" install -r "$APK"
    echo "  installed"
fi
