# iOS Simulator feasibility host

This UIKit host uses the same Rust shell, A2UI renderer, published definitions,
SQL bindings, charts, drafts and contextual chat as Android. UIKit owns the
window, safe areas and keyboard geometry; GPUI renders the app through Metal.
No WebView or local companion server is involved.

Build on an Apple Silicon Mac with Xcode and an installed iOS Simulator runtime:

```sh
rustup toolchain install 1.98.0 --profile minimal --target aarch64-apple-ios-sim
brew install xcodegen cmake ninja
xcodebuild -downloadPlatform iOS -buildVersion 26.2 -architectureVariant arm64
# Xcode's system components may require local administrator authorization:
sudo xcodebuild -runFirstLaunch -checkForNewerComponents
cd apps/calliope-mobile
./build-ios.sh
```

The build uses the existing GPUI revisions plus a documented source patch to
gpui-mobile's iOS window: ordinary input focus and keyboard requests must call
its existing UIKit show/hide methods, as the Android window already does.
The iOS dependency boundary
currently needs libc 0.2.186 for backtrace 0.3.76's dyld declarations, and the
small `vendor/io-surface` patch to keep CGL off iOS. The pinned gpui-mobile iOS
platform also references its camera and video-player modules unconditionally;
those features are enabled only on iOS. Calliope camera and audio workflows use
the UIKit/AVFoundation host bridges described below.

If Xcode reports a platform missing despite an installed runtime, compare
`xcrun simctl runtime list` with `xcrun simctl runtime match list`. On this test
Mac, the downloader supplied 26.2 build `23C52` while the SDK selected `23C57`,
which was unavailable to download. The supported explicit mapping
`xcrun simctl runtime match set iphoneos26.2 23C52` resolved it. This is a
host-specific override; reset it with `--default` when installing a matching
runtime. Do not blindly copy that build number for another Xcode installation.

The Simulator build is ad-hoc signed with Simulator-only Keychain entitlements.
An unsigned app can launch but cannot use Keychain (`errSecMissingEntitlement`).
Physical devices require a real development team, provisioning and entitlements;
the placeholder Simulator prefix is not a distribution identity.

Create/select a dedicated Simulator rather than taking over another development
session. Use its explicit UUID for install, launch, inspection and screenshots:

```sh
xcrun simctl create 'Calliope iOS Test' \
  com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro \
  com.apple.CoreSimulator.SimRuntime.iOS-26-2
xcrun simctl boot DEVICE_UUID
xcrun simctl bootstatus DEVICE_UUID -b
xcrun simctl install DEVICE_UUID ios/build/Build/Products/Debug-iphonesimulator/CalliopeMobile.app
xcrun simctl launch DEVICE_UUID ai.rvbbit.calliope.mobile
printf '%s\n' '{"op":"inspect"}' | python3 probe_ios.py --device DEVICE_UUID
python3 probe_ios.py --device DEVICE_UUID --screenshot /tmp/calliope-ios.png
```

The structural inspector is an app-private Unix socket with mode 0600, reachable
on the Mac through the Simulator's private container. There is no network
inspection endpoint. Its commands match the Android inspector. The iOS host binds
relative to its fixed private working directory because Simulator container paths
exceed the Unix socket pathname limit.

For an actual keyboard/rotation check, open an app and a disposable **local**
draft, clear the chosen text field, and note its screen point coordinates from
the probe bounds plus the UIKit safe-area offset. Run on the same explicit
Simulator; do not use an automatic destination that could select another device:

```sh
xcodebuild -project ios/CalliopeMobile.xcodeproj -scheme CalliopeMobile \
  -sdk iphonesimulator -destination 'platform=iOS Simulator,id=DEVICE_UUID' \
  -configuration Debug -derivedDataPath ios/build CODE_SIGN_IDENTITY=- \
  "CALLIOPE_STATIC_LIB=$PWD/target/aarch64-apple-ios-sim/debug/libcalliope_mobile.a" \
  'CALLIOPE_TEST_POINT=190,438' 'CALLIOPE_TEST_TEXT=iOS keyboard feasibility check.' \
  -parallel-testing-enabled NO \
  -only-testing:CalliopeInteractionTests/NativeInputTests/testKeyboardRotationAndReturn \
  -resultBundlePath /tmp/calliope-input.xcresult test
```

The point above was observed on the test iPhone in portrait; remeasure for
another layout. The test appends a second line in landscape, backgrounds and
returns, and captures screenshots. Compare the actual bound draft with the
expected text using `probe_ios.py`, then discard only that test draft. XCTest
success alone does not prove the field remains visible after rotation: inspect
the captures too. The optional `testScrollCurrentSurface` takes two real touch
swipes and captures the result when `CALLIOPE_TEST_SCROLL=1` is supplied.
Tests skip without their explicit inputs. Export captures with
`xcrun xcresulttool export attachments --path RESULT.xcresult --output-path DIR`.

Sign in to a reachable HTTPS origin through Account. Sessions are stored in iOS
Keychain with `AfterFirstUnlockThisDeviceOnly`; passwords are cleared after login.
Drafts and caches live in the private Application Support directory and are
excluded from backups. iOS Keychain can survive app reinstallation, so uninstalling
the app alone is not a credential-reset mechanism.

TLS uses Apple's Security.framework validation and installed trust roots. For an
instance with an internal CA, install the administrator-supplied **public** CA
root into this Simulator (never a private key):

```sh
xcrun simctl keychain DEVICE_UUID add-root-cert Calliope-LAN-CA.crt
```

Then sign in at the instance's LAN origin (for example `https://calliope.local`).
Confirm the CA fingerprint with the administrator before trusting it. The Safari
handoff uses its own normal session; app credentials are not included in browser
URLs.

The host now supports native camera capture, private photo-library and document
pickers, Quick Look attachment viewing, microphone capture and streamed PCM
playback. It uses the same server transcription/TTS endpoints and durable app
drafts as Android.

This remains a Simulator development build, not an App Store release. Physical
camera/microphone routing, device lifecycle/performance, VoiceOver, OAuth
callbacks, app icons and distribution signing need separate verification or
implementation. Contextual-app landscape focus reveal also needs work. The
ordinary chat composer handles the keyboard in portrait and landscape.
