"""Restore subpackages omitted by the granite-tsfm 0.3.2 wheel."""
import hashlib
import io
from pathlib import Path
import shutil
import sysconfig
import tarfile
import tempfile
import urllib.request

url = "https://codeload.github.com/ibm-granite/granite-tsfm/tar.gz/refs/tags/v0.3.2"
source = urllib.request.urlopen(url, timeout=120).read()
assert hashlib.sha256(source).hexdigest() == "f79e1b25e77f7916c8492314d0958b994bb2a9b532b3d7750ce4e50227616f63"
with tempfile.TemporaryDirectory() as directory:
    with tarfile.open(fileobj=io.BytesIO(source)) as archive:
        archive.extractall(directory, filter="data")
    shutil.copytree(Path(directory) / "granite-tsfm-0.3.2/tsfm_public",
                    Path(sysconfig.get_paths()["purelib"]) / "tsfm_public", dirs_exist_ok=True)
