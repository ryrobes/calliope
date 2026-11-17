import importlib.util
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('export_computer_env', Path(__file__).with_name('export-computer-env.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def source(password='test-password'):
    return {'Config': {'Env': ['VNC_PW=' + password, 'CALLIOPE_DESKTOP_CONTROL_KEY=test-key',
        'WAREHOUSE_DSN=must-not-export', 'HUTCH_API_KEY=must-not-export', 'OTHER_TOKEN=must-not-export']}}


def settings(container=None, **changes):
    options = {'address': '10.0.0.7', 'decision_url': 'https://example.com/api/calliope/computer/decide',
               'version': '82e651e', **changes}
    return module.settings(container or source(), **options)


def test_export_contains_only_guest_credentials_and_nonsecret_configuration(tmp_path):
    values = settings()
    assert values['CALLIOPE_DESKTOP_CONTROL_KEY'] == 'test-key'
    assert values['CALLIOPE_DESKTOP_VNC_PASSWORD'] == 'test-password'
    assert 'must-not-export' not in str(values)
    path = tmp_path / 'computer.env'
    path.write_text('old')
    path.chmod(0o644)
    module.write_env(path, values)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert 'must-not-export' not in path.read_text()


@pytest.mark.parametrize('changes', [
    {'address': 'not-an-address'}, {'version': '../../other'},
    {'decision_url': 'https://user:password@example.com/api/calliope/computer/decide'},
    {'decision_url': 'https://example.com/api/calliope/computer/decide?token=private'},
])
def test_invalid_destination_configuration_rejected(changes):
    with pytest.raises(ValueError):
        settings(**changes)


def test_missing_source_credentials_are_not_silently_rotated():
    with pytest.raises(ValueError, match='missing'):
        settings({'Config': {'Env': []}})


@pytest.mark.skipif(not shutil.which('docker'), reason='Docker Compose CLI unavailable')
def test_compose_preserves_password_punctuation_without_interpolation(tmp_path):
    password = 'spaces $UNCHANGED ${ALSO_UNCHANGED} #hash \\ and "quote" and apostrophe\' and café'
    path = tmp_path / 'computer.env'
    module.write_env(path, settings(source(password)))
    result = subprocess.run(['docker', 'compose', '--env-file', str(path), '-f',
        str(ROOT / 'docker/docker-compose.computer.yml'), 'config', '--environment'],
        check=True, capture_output=True, text=True)
    assert 'CALLIOPE_DESKTOP_VNC_PASSWORD=' + password + '\n' in result.stdout
