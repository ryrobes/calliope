from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "docker" / "calliope" / "bin" / "calliope-web-check"
CONFIG_ROOT = ROOT / "docker" / "calliope" / "web-check"
TOOLS = ("prettier", "eslint", "html-validate")


pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in TOOLS),
    reason="web validation CLIs are installed in the Calliope Hermes image",
)


def _run(*paths: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CALLIOPE_WEB_CHECK_CONFIG_ROOT"] = str(CONFIG_ROOT)
    return subprocess.run(
        [str(CHECKER), *(str(path) for path in paths)],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def test_web_check_accepts_the_shipping_dashboard_template():
    result = _run(ROOT / "services" / "calliope" / "dashboard_template.html")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK:" in result.stdout


def test_web_check_rejects_broken_embedded_javascript(tmp_path):
    broken = tmp_path / "broken.html"
    broken.write_text(
        "<!DOCTYPE html><html lang=\"en\"><head><title>Broken</title></head>"
        "<body><script>const result = ;</script></body></html>",
        encoding="utf-8",
    )

    result = _run(broken)

    assert result.returncode == 1
    assert "FAIL:" in result.stderr
