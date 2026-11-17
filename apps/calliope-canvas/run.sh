#!/usr/bin/env bash
set -euo pipefail
app_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
profile="${CALLIOPE_CANVAS_PROFILE:-release}"
case "$profile" in
  release) build_flags=(--release) ;;
  debug) build_flags=() ;;
  *) echo "CALLIOPE_CANVAS_PROFILE must be release or debug" >&2; exit 2 ;;
esac
binary="$app_dir/target/$profile/calliope-canvas"
if [[ ! -x "$binary" ]]; then
  cargo build --locked "${build_flags[@]}" --manifest-path "$app_dir/Cargo.toml"
fi
if [[ "${1:-}" == "--check" ]]; then
  exec "$binary" --check
fi

# One writer for the personal canvas. A second launch focuses the existing
# window on the local Hyprland desktop; no compositor configuration is changed.
runtime_dir="${XDG_RUNTIME_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/calliope-canvas}"
mkdir -p -- "$runtime_dir"
exec 9>"$runtime_dir/calliope-canvas.lock"
if ! flock -n 9; then
  if command -v hyprctl >/dev/null && [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]]; then
    hyprctl eval 'return hl.dispatch(hl.dsp.focus({window="class:^(ai.calliope.canvas)$"}))' >/dev/null 2>&1 || true
  fi
  exit 0
fi
exec "$binary" "$@"
