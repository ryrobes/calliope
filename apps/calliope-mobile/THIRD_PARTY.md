The Android IME proxy in `android/app/src/main/java/dev/gpui/mobile/GpuiInputActivity.java`
is adapted from [gpui-mobile](https://github.com/Bombatomica64/gpui-mobile/tree/431d8cd075fb69fe2c62d382700d80a9163ca54d),
by Balamurali Pandranki and contributors. Used under its Apache-2.0 option;
the license is included in `LICENSE-gpui-mobile`. Modified for a plain Activity
and the host-driven IME entry point, October 2026. The Gradle wrapper also comes
from that example (Gradle is Apache-2.0).

Mobile dependencies are pinned to the compatible GPUI 0.3.8 / GPUI Kit 0.7.1
fork revisions listed in Cargo.toml. The version selection was informed by
[GPUI Kit's mobile guide](https://gpui-kit.com/docs/mobile/) and the
[Gpui-android component assessment](https://github.com/Bombatomica64/Gpui-android).
No component-gallery application code is included here.

The A2UI renderer compiles the existing vendored MIT-licensed renderer source
in `../calliope-canvas/vendor/a2ui-gpui`. Its upstream notices remain there.

The UIKit containment and keyboard-layout setup in `ios/App.swift` follows the
same pinned gpui-mobile example, under its Apache-2.0 option. Calliope supplies
its own shared app root, asset source, Keychain storage and host bridge.

`vendor/gpui-mobile` preserves that same pinned source under its Apache-2.0
option, with the iOS window's missing keyboard focus/show/hide hooks added.
Android source is unchanged. Its `CALLIOPE.md` records the exact upstream base
and local change; original license files are included.

`vendor/io-surface` contains io-surface 0.16.1, with its original MIT and Apache-2.0
licenses. Its macOS-only CGL helper/dependency is gated to macOS so the iOS
core-video dependency does not link desktop OpenGL. See its `CALLIOPE.md` notice.

AndroidX Core 1.15.0 supplies FileProvider for read-only attachment handoff.
It is distributed under Apache-2.0 by the Android Open Source Project.
