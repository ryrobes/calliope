#!/usr/bin/env bash
set -euo pipefail
# Worker-only backport of upstream's ATSPI_NO_CACHE/GetItems fix. Noble's
# 2.52 client still prefetches the complete app cache when caching is disabled.
# https://github.com/GNOME/at-spi2-core/blob/main/NEWS (2.61.0)
# Keep the system GTK/ATK bridge and LibreOffice packages untouched.
cd /tmp
curl -fsSLo atspi.tar.xz https://download.gnome.org/sources/at-spi2-core/2.52/at-spi2-core-2.52.0.tar.xz
echo '0ac3fc8320c8d01fa147c272ba7fa03806389c6b03d3c406d0823e30e35ff5ab  atspi.tar.xz' | sha256sum -c -
tar -xJf atspi.tar.xz
python3 - <<'PY'
from pathlib import Path
p = Path('at-spi2-core-2.52.0/atspi/atspi-misc.c')
s = p.read_text()
old = 'if (!app->bus)\n    return; /* application has gone away / been disposed */'
assert s.count(old) == 1
p.write_text(s.replace(old, 'if (!app->bus || atspi_no_cache)\n    return; /* disposed, or cache prefetch disabled by this client */'))
PY
meson setup atspi-build at-spi2-core-2.52.0 --prefix=/opt/calliope-atspi --libdir=lib \
    -Dx11=disabled -Dintrospection=disabled -Dgtk2_atk_adaptor=false -Duse_systemd=false -Ddocs=false
ninja -C atspi-build atspi/libatspi.so.0.0.1
install -D atspi-build/atspi/libatspi.so.0.0.1 /opt/calliope-atspi/lib/libatspi.so.0
install -D at-spi2-core-2.52.0/COPYING /opt/calliope-atspi/share/COPYING
# Ship the exact corresponding patched source with the isolated shared library.
tar -cJf /opt/calliope-atspi/share/source-2.52.0.tar.xz at-spi2-core-2.52.0
