"""Install the pinned upstream Rust renderer for this Linux prototype."""
import hashlib
import platform
from pathlib import Path
import urllib.request
import zipfile

VERSION = '1.9.0'
SHA256 = 'f557637a159c7510a1486e1e41a86c3919abd71c18abc0826b499c76c712aa53'
if platform.system() != 'Linux' or platform.machine() != 'x86_64':
    raise SystemExit('This installer is pinned for Linux x86_64. See the chart notes for other release assets.')
dest = Path(__file__).resolve().parents[1] / 'local/vega'
dest.mkdir(parents=True, exist_ok=True)
archive = dest / f'vl-convert-{VERSION}.zip'
if not archive.exists():
    urllib.request.urlretrieve(f'https://github.com/vega/vl-convert/releases/download/v{VERSION}/vl-convert_linux-64.zip', archive)
if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
    raise SystemExit('Renderer archive checksum mismatch.')
with zipfile.ZipFile(archive) as bundle:
    for name in ['vl-convert', 'LICENSE', 'thirdparty_font.md', 'thirdparty_rust.yaml', 'thirdparty_javascript.md']:
        (dest / name).write_bytes(bundle.read('bin/' + name))
(dest / 'vl-convert').chmod(0o755)
print(f'Installed vl-convert {VERSION}: {dest / "vl-convert"}')

# Stable 1.9's CLI cannot export a scene graph; its Python ABI exposes that API.
# Unpack the verified abi3 wheel locally, without modifying system Python.
WHEEL = 'vl_convert_python-1.9.0-cp37-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl'
WHEEL_URL = 'https://files.pythonhosted.org/packages/6f/61/dc6f4a38cf1b8699f64c57d7f021ca42c39bfe782d8a6eaefb7e8418e925/' + WHEEL
WHEEL_SHA256 = '849e6773a7e05d58ab215386b1065e7713f4846b9ac6b0d743bb3e1b20337231'
wheel = dest / WHEEL
if not wheel.exists(): urllib.request.urlretrieve(WHEEL_URL, wheel)
if hashlib.sha256(wheel.read_bytes()).hexdigest() != WHEEL_SHA256:
    raise SystemExit('Renderer wheel checksum mismatch.')
with zipfile.ZipFile(wheel) as bundle:
    bundle.extractall(dest / 'python')
print(f'Installed chart scene exporter {VERSION}: {dest / "python"}')
