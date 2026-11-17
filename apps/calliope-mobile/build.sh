#!/usr/bin/env bash
# Build the isolated Android experiment. No desktop dependency changes.
set -euo pipefail
cd "$(dirname "$0")"
mobile_tools="${CALLIOPE_MOBILE_TOOLS:-$HOME/.local/share/calliope-mobile}"
if [[ -x "$mobile_tools/cargo/bin/rustup" ]]; then
    export RUSTUP_HOME="$mobile_tools/rustup" CARGO_HOME="$mobile_tools/cargo"
    export PATH="$mobile_tools/cargo/bin:$PATH"
fi
export ANDROID_HOME="${ANDROID_HOME:-$mobile_tools/sdk}"
export ANDROID_NDK_HOME="${ANDROID_NDK_HOME:-$ANDROID_HOME/ndk/27.2.12479018}"
export JAVA_HOME="${CALLIOPE_ANDROID_JAVA_HOME:-$mobile_tools/jdk17}"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-8}"
[[ -x "$JAVA_HOME/bin/java" ]] || { echo 'Set CALLIOPE_ANDROID_JAVA_HOME to JDK 17 or 21' >&2; exit 1; }
[[ -d "$ANDROID_NDK_HOME" ]] || { echo 'Install NDK 27.2.12479018 in ANDROID_HOME' >&2; exit 1; }
command -v cargo-ndk >/dev/null
cargo ndk -t arm64-v8a --platform 26 -o android/app/src/main/jniLibs build --release --locked
# The backend is linked into our library, not loaded separately by Android.
rm -f android/app/src/main/jniLibs/arm64-v8a/libgpui_mobile*.so
printf 'sdk.dir=%s\n' "$ANDROID_HOME" > android/local.properties
(cd android && ./gradlew --no-daemon assembleRelease)
mkdir -p dist
cp android/app/build/outputs/apk/release/app-release.apk dist/calliope-mobile.apk
echo "APK: $PWD/dist/calliope-mobile.apk"
