#!/usr/bin/env bash
set -euo pipefail
app_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
  '') cargo build --release --locked --manifest-path "$app_dir/Cargo.toml" ;;
  --launcher-only) ;; # Refresh the icon/launcher without rebuilding Rust.
  *) echo 'Usage: install-local.sh [--launcher-only]' >&2; exit 2 ;;
esac
python3 - "$app_dir" <<'PY'
import os
import pathlib
import shutil
import sys

app = pathlib.Path(sys.argv[1])
data = pathlib.Path(os.environ.get('XDG_DATA_HOME', pathlib.Path.home() / '.local/share'))
applications = data / 'applications'
icons = data / 'icons/hicolor/scalable/apps'
applications.mkdir(parents=True, exist_ok=True)
icons.mkdir(parents=True, exist_ok=True)
shutil.copyfile(app / 'assets/ai.calliope.canvas.svg', icons / 'ai.calliope.canvas.svg')
def quoted(value):
    return '"' + ''.join('\\' + c if c in '\\"`$' else c for c in str(value)) + '"'
desktop = applications / 'ai.calliope.canvas.desktop'
desktop.write_text('\n'.join([
    '[Desktop Entry]', 'Version=1.0', 'Type=Application',
    'Name=Calliope Canvas', 'Comment=A personal canvas connected to Calliope',
    'Exec=' + quoted(app / 'run.sh'),
    'Icon=ai.calliope.canvas', 'Terminal=false',
    'Categories=Office;', 'Keywords=Calliope;Callie;Canvas;',
    'StartupWMClass=ai.calliope.canvas', '',
]))
print('Installed launcher:', desktop)
PY
if command -v update-desktop-database >/dev/null; then
  update-desktop-database "${XDG_DATA_HOME:-$HOME/.local/share}/applications" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null; then
  gtk-update-icon-cache --force --ignore-theme-index "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" >/dev/null 2>&1 || true
fi
