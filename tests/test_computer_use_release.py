import base64
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_provisioned_kasm_viewer_uses_control_password(tmp_path):
    env_file = tmp_path / "calliope.env"
    env_file.write_text(
        "CALLIOPE_DESKTOP_VNC_PASSWORD=control-secret\n"
        "CALLIOPE_DESKTOP_VNC_VIEW_PASSWORD=unused-view-secret\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release" / "provision-computer-use-env.py"),
            "--env-file",
            str(env_file),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    values = dict(
        line.split("=", 1)
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    decoded = base64.b64decode(
        values["CALLIOPE_DESKTOP_VNC_VIEW_BASIC"].removeprefix("Basic ")
    ).decode()
    assert decoded == "kasm_viewer:control-secret"








def test_procedure_runtime_adapter_is_packaged_with_calliope():
    dockerfile = (ROOT / "services" / "calliope" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "services/calliope/procedure_access.py" in dockerfile


def test_procedure_observatory_surfaces_durable_visual_evidence():
    observatory = (
        ROOT
        / "datarabbit"
        / "src"
        / "components"
        / "desktop"
        / "procedure-observatory-window.tsx"
    ).read_text(encoding="utf-8")

    assert "Visual evidence" in observatory
    assert "guided.observed" in observatory
    assert "guided.action.executed" in observatory
    assert "recording.ready" in observatory
    assert "Step screenshots remain the durable visual receipt." in observatory
    assert "<video controls playsInline" in observatory
    assert "<img src={item.url}" in observatory




def test_desktop_image_includes_common_office_and_document_tools():
    dockerfile = (ROOT / "docker" / "Dockerfile.calliope-desktop").read_text(encoding="utf-8")
    desktop = (ROOT / "docker" / "desktop" / "calliope_desktop.py").read_text(encoding="utf-8")
    client = (ROOT / "services" / "calliope" / "calliope" / "calliope.js").read_text(encoding="utf-8")

    for package in (
        "libreoffice-calc",
        "libreoffice-writer",
        "python3-openpyxl",
        "python3-pandas",
        "csvkit",
        "jq",
        "evince",
        "poppler-utils",
        "qpdf",
        "p7zip-full",
    ):
        assert package in dockerfile
    assert 'LD_LIBRARY_PATH="/usr/lib/libreoffice/program:' in dockerfile
    assert "/home/kasm-user/.cache/dconf" in dockerfile
