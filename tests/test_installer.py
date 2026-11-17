"""deploy/install.sh: the curl | bash installer shipped with every release."""
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'deploy' / 'install.sh'


def run_helper(script, stdin=''):
    """Run shell code with the installer's functions loaded (main is not called)."""
    body = INSTALLER.read_text().rsplit('\nmain "$@"', 1)[0]
    return subprocess.run(['bash', '-c', body + '\n' + script], input=stdin,
                          capture_output=True, text=True, check=True).stdout


def test_installer_parses_and_runs_main_last():
    subprocess.run(['bash', '-n', str(INSTALLER)], check=True)
    # A partial download must not run anything: everything is in functions until the last line.
    assert INSTALLER.read_text().rstrip().endswith('main "$@"')
    assert os.access(INSTALLER, os.X_OK)


@pytest.mark.parametrize('manifest, expected', [
    ('{\n  "calliope_version": "1.2.0-rc.4",\n  "bundle_version": "1.2.0-rc.4+bundle.1"\n}', '1.2.0-rc.4+bundle.1'),
    ('{"calliope_version": "1.2.0-rc.4", "images": ["calliope-server"]}', '1.2.0-rc.4'),
])
def test_release_version_prefers_the_bundle_version(manifest, expected):
    assert run_helper('release_version', manifest) == expected


def test_questions_read_the_terminal_not_stdin():
    # With curl | bash, stdin is the script itself.
    text = INSTALLER.read_text()
    assert 'read -r -p "$2${3:+ ${D}[$3]${N}}: " reply </dev/tty' in text
    assert 'read -r -s -p "$2: " reply </dev/tty' in text
    # The key is checked with OpenRouter as a header on stdin, never in argv.
    assert '-H @- https://openrouter.ai/api/v1/key' in text


def test_versions_compare_numerically():
    assert run_helper('version_ge 2.31.0 2.30 && echo yes') == 'yes\n'
    assert run_helper('version_ge 2.4.1 2.30 || echo no') == 'no\n'


def test_release_ships_the_installer():
    workflow = yaml.safe_load((ROOT / '.github/workflows/calliope-images.yml').read_text())
    steps = workflow['jobs']['bundle']['steps']
    publish = next(step for step in steps if step.get('name') == 'Publish the GitHub release')
    assert 'deploy/install.sh' in publish['run']
    assert '-linux-amd64.tar.gz.sha256' in publish['run']
    assert workflow['jobs']['bundle']['permissions']['contents'] == 'write'
    assert "'deploy/install.sh': 'install.sh'" in (ROOT / 'scripts/release/shipping.py').read_text()
