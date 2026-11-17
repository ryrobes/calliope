#!/usr/bin/env bash
set -euo pipefail
until xdpyinfo >/dev/null 2>&1; do sleep 1; done
# The browser belongs to this dedicated guest. Its loopback CDP listener is
# reachable by Cua and guest code, never published through the viewer proxy.
# Kasm's launcher injects another --user-data-dir. Use the package executable
# so Cua can prove one unambiguous profile and one exact visible browser.
if [[ -x /opt/google/chrome/chrome ]]; then
  browser=/opt/google/chrome/chrome
elif [[ -x /usr/lib/chromium/chromium ]]; then
  browser=/usr/lib/chromium/chromium
else
  browser=$(command -v chromium || command -v chromium-browser)
fi
profile="$HOME/.config/callie-browser"
# QEMU guests have Mesa/llvmpipe, but Chrome can blocklist it after restart.
# Select that renderer explicitly instead of relying on deprecated automatic
# SwiftShader fallback. GPU-backed installations can opt into native selection.
renderer_args=()
case "${CALLIOPE_BROWSER_RENDERER:-software}" in
  software)
    export LIBGL_ALWAYS_SOFTWARE=1
    renderer_args=(--use-gl=angle --use-angle=gl --ignore-gpu-blocklist)
    ;;
  auto) ;;
  *) echo 'CALLIOPE_BROWSER_RENDERER must be software or auto' >&2; exit 1 ;;
esac
browser_pid=''
cleanup() {
  trap - TERM INT
  if [[ -n $browser_pid ]]; then
    kill "$browser_pid" 2>/dev/null || true
    wait "$browser_pid" 2>/dev/null || true
  fi
  exit 0
}
trap cleanup TERM INT
while true; do
  # This supervisor exclusively owns this profile. A previous container's
  # hostname/PID lock must not make a persistent profile unusable after restart.
  rm -f "$profile/SingletonLock" "$profile/SingletonSocket" "$profile/SingletonCookie"
  "$browser" "${renderer_args[@]}" --no-sandbox --no-first-run --no-default-browser-check \
    --force-renderer-accessibility --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port=9223 --user-data-dir="$profile" \
    --restore-last-session --hide-crash-restore-bubble --start-maximized &
  browser_pid=$!
  wait "$browser_pid" || true
  browser_pid=''
  sleep 2
done
