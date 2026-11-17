Source: https://github.com/Bombatomica64/gpui-mobile at
`431d8cd075fb69fe2c62d382700d80a9163ca54d`. Calliope uses the Apache-2.0 option.
Only Cargo.toml, README, licenses and src are vendored; examples are not needed.

Local change: `src/ios/window.rs` implements PlatformWindow's
`text_input_state_changed`, `show_soft_keyboard`, and `hide_soft_keyboard` by
delegating to the existing deferred UIKit keyboard methods. The pinned Android
window already implements these hooks; its source is unchanged. Without them,
ordinary GPUI inputs focus on iOS but never present the software keyboard.

Remove the source patch when the upstream revision includes these hooks. GPUI
core, Kit, renderer and Android backend revisions have not been upgraded.

`src/ios/platform.rs` also constructs clipboard NSStrings with an explicit UTF-8
length. The upstream C-string constructor read beyond Rust strings on Copy.
