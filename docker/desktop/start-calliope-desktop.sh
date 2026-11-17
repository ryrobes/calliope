#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) == 0 ]]; then
  if [[ ${CALLIOPE_COMPUTER_ALLOW_SUDO:-false} == true ]]; then
    printf '%s\n' 'kasm-user ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/calliope-desktop
    chmod 0440 /etc/sudoers.d/calliope-desktop
    visudo -cf /etc/sudoers.d/calliope-desktop
  else
    rm -f /etc/sudoers.d/calliope-desktop
  fi
  # Earlier debug sessions sometimes created root-owned browser/VNC files.
  # The preserved home belongs to the desktop user, including those files.
  if [[ ! -f /home/kasm-user/.calliope-home-owner-v1 ]]; then
    chown -R 1000:1000 /home/kasm-user
    touch /home/kasm-user/.calliope-home-owner-v1
    chown 1000:1000 /home/kasm-user/.calliope-home-owner-v1
  fi
  install -d -o 1000 -g 1000 -m 0770 \
    /var/lib/calliope-desktop/computer \
    /var/lib/calliope-desktop/recordings \
    /var/lib/calliope-desktop/screenshots
  install -d -o 1000 -g 1000 -m 0770 /var/lib/calliope-desktop/shared-uploads
  exec setpriv --reuid=1000 --regid=1000 --init-groups bash "$0" "$@"
fi

mkdir -p /home/kasm-user/Shared /home/kasm-user/Desktop
if [[ ! -e /home/kasm-user/Desktop/Shared && ! -L /home/kasm-user/Desktop/Shared ]]; then
  ln -s /home/kasm-user/Shared /home/kasm-user/Desktop/Shared
fi

# Runs before any GUI application, with the persistent home's prior identity.
python3 /opt/calliope-desktop/computer_lifecycle.py startup
python3 /opt/calliope-desktop/calliope_desktop.py &
control_pid=$!
bash /dockerstartup/start-callie-browser.sh &
browser_pid=$!
/dockerstartup/kasm_default_profile.sh \
  /dockerstartup/vnc_startup.sh \
  /dockerstartup/kasm_startup.sh \
  --tail-log &
vnc_pid=$!

cleanup() {
  trap - EXIT TERM INT
  # Stop new jobs and the browser's restart loop before closing app windows.
  kill "$control_pid" "$browser_pid" 2>/dev/null || true
  # Keep X and D-Bus alive while apps receive their normal close request.
  timeout 18s python3 /opt/calliope-desktop/computer_lifecycle.py shutdown || true
  kill "$control_pid" "$browser_pid" "$vnc_pid" 2>/dev/null || true
  # Child cleanup must not hold PID 1 past Docker's grace period.
  sleep 1
  kill -KILL "$control_pid" "$browser_pid" "$vnc_pid" 2>/dev/null || true
}
trap cleanup EXIT TERM INT
# A dead control service must restart the guest instead of leaving an
# apparently healthy, unrecorded desktop accepting input indefinitely.
wait -n "$control_pid" "$vnc_pid"
exit 1
