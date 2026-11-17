#!/usr/bin/env bash
# Build the shared native client for an Apple Silicon iOS Simulator on macOS.
set -euo pipefail
cd "$(dirname "$0")"
[[ "$(uname -s)" == Darwin ]] || { echo 'Run this script on the Mac build host.' >&2; exit 1; }
[[ "$(uname -m)" == arm64 ]] || { echo 'This feasibility build targets an Apple Silicon Simulator.' >&2; exit 1; }
[[ $# == 0 || ($# == 1 && "${1:-}" == --release) ]] || { echo 'Usage: ./build-ios.sh [--release]' >&2; exit 1; }
export PATH="$HOME/.cargo/bin:/opt/homebrew/bin:$PATH"
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-4}"
export IPHONEOS_DEPLOYMENT_TARGET=16.0
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$PWD/target}"
[[ "$CARGO_TARGET_DIR" == /* ]] || export CARGO_TARGET_DIR="$PWD/$CARGO_TARGET_DIR"
profile=debug
configuration=Debug
if [[ "${1:-}" == --release ]]; then profile=release; configuration=Release; fi
set --
if [[ "$profile" == release ]]; then set -- --release; fi
cargo rustc --locked --lib --target aarch64-apple-ios-sim "$@" --crate-type staticlib
(cd ios && xcodegen generate)
xcodebuild -project ios/CalliopeMobile.xcodeproj -scheme CalliopeMobile \
    -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' \
    -configuration "$configuration" -derivedDataPath ios/build CODE_SIGN_IDENTITY=- \
    "CALLIOPE_STATIC_LIB=$CARGO_TARGET_DIR/aarch64-apple-ios-sim/$profile/libcalliope_mobile.a" build
echo "App: $PWD/ios/build/Build/Products/$configuration-iphonesimulator/CalliopeMobile.app"
