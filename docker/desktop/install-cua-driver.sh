#!/usr/bin/env bash
# Reproducible driver install for both image builds and an existing guest.
set -euo pipefail
version=0.28.2
case "$(uname -m)" in
  x86_64) arch=x86_64; digest=a1d99fd04bb4927ef5ffdbe60eb91ed8b51a2bab60e10fc604a75bd59ce69c3e ;;
  aarch64|arm64) arch=arm64; digest=55e8a32839a4ac369a773df4dac87b345bd4567779221ade4a5e39223a45a2e8 ;;
  *) echo 'Cua Driver requires Linux x86_64 or arm64' >&2; exit 1 ;;
esac
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
archive="cua-driver-rs-${version}-linux-${arch}-binary.tar.gz"
curl --fail --location --retry 3 \
  "https://github.com/trycua/cua/releases/download/cua-driver-rs-v${version}/${archive}" \
  --output "$tmp/$archive"
echo "$digest  $tmp/$archive" | sha256sum --check --status
install -d /opt/cua-driver
tar -xzf "$tmp/$archive" -C /opt/cua-driver
chmod 0755 /opt/cua-driver/cua-driver
ln -sfn /opt/cua-driver/cua-driver /usr/local/bin/cua-driver
printf '%s\n' "$version" > /opt/cua-driver/VERSION
