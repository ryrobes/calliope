#!/usr/bin/env bash
# GCE startup script for an otherwise empty, dedicated Debian 12 Computer VM.
# Contains no application credentials and deliberately starts no desktop.
set -euo pipefail
test "$(id -u)" = 0
. /etc/os-release
if [[ "$ID" != debian || "$VERSION_ID" != 12 ]]; then
  echo 'Computer bootstrap requires Debian 12' >&2
  exit 1
fi
bootstrap_receipt=/var/lib/calliope-computer-bootstrap/installed.txt
if [[ -s "$bootstrap_receipt" ]]; then
  # GCE startup scripts also run on reboot. Do not upgrade Docker underneath
  # a migrated desktop every time the host starts.
  systemctl start docker
  docker info >/dev/null
  docker compose version
  exit 0
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl python3
install -m 0755 -d /etc/apt/keyrings
curl --fail --silent --show-error --retry 3 \
  https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod 0644 /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: bookworm
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y --no-install-recommends \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
install -d -m 0700 /opt/calliope-computer
install -d -m 0755 /var/lib/calliope-computer-bootstrap
bootstrap_temporary=$(mktemp /var/lib/calliope-computer-bootstrap/.installed.XXXXXX)
trap 'rm -f "$bootstrap_temporary"' EXIT
{
  date --utc --iso-8601=seconds
  docker version
  docker compose version
  dpkg-query -W docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
} > "$bootstrap_temporary"
chmod 0644 "$bootstrap_temporary"
mv "$bootstrap_temporary" "$bootstrap_receipt"
echo 'Computer host ready; desktop source, limited env and volumes must be installed separately.'
