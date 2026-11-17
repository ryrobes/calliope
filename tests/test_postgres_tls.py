"""Real certificates: validate names, trust, expiry, supplied keys and renewal."""
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tls(monkeypatch):
    spec = importlib.util.spec_from_file_location('postgres_tls', ROOT / 'scripts/release/postgres-tls.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Host tests need not be root or have a postgres user. The image check uses
    # the real UID/GID and PostgreSQL's own key-permissions validation.
    monkeypatch.setattr(module.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_gid=999))
    monkeypatch.setattr(module.os, 'chown', lambda *args: None)
    return module


@pytest.mark.parametrize('host', ['sql.example', '192.0.2.10', '2001:db8::1'])
def test_certificate_trust_hostname_and_idempotence(tls, tmp_path, host):
    tls.prepare(tmp_path, host)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    tls.prepare(tmp_path, host)
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    tls.verify(tmp_path / 'server.crt', tmp_path / 'server.key', host)
    tls.command('verify', '-CAfile', tmp_path / 'ca.crt', tmp_path / 'server.crt')
    assert (tmp_path / 'server.key').stat().st_mode & 0o777 == 0o640
    assert (tmp_path / 'ca.key').stat().st_mode & 0o777 == 0o600
    with pytest.raises(subprocess.CalledProcessError):
        tls.verify(tmp_path / 'server.crt', tmp_path / 'server.key', 'wrong.example')


def test_new_hostname_renews_server_without_replacing_ca(tls, tmp_path):
    tls.prepare(tmp_path, 'old.example')
    ca = (tmp_path / 'ca.crt').read_bytes()
    server = (tmp_path / 'server.crt').read_bytes()
    tls.prepare(tmp_path, 'new.example')
    assert (tmp_path / 'ca.crt').read_bytes() == ca
    assert (tmp_path / 'server.crt').read_bytes() != server
    tls.verify(tmp_path / 'server.crt', tmp_path / 'server.key', 'new.example')


def test_near_expiry_renews_with_same_ca(tls, tmp_path, monkeypatch):
    tls.prepare(tmp_path, 'sql.example')
    ca = (tmp_path / 'ca.crt').read_bytes()
    server = (tmp_path / 'server.crt').read_bytes()
    actual = tls.command
    def expiring(*args, **kwargs):
        if '-checkend' in args and str(30 * 86400) in args:
            return subprocess.CompletedProcess(args, 1)
        return actual(*args, **kwargs)
    monkeypatch.setattr(tls, 'command', expiring)
    tls.prepare(tmp_path, 'sql.example')
    assert (tmp_path / 'ca.crt').read_bytes() == ca
    assert (tmp_path / 'server.crt').read_bytes() != server


def test_provided_certificate_is_validated_never_replaced(tls, tmp_path):
    tls.prepare(tmp_path, 'sql.example')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    tls.prepare(tmp_path, 'sql.example', 'provided')
    with pytest.raises(subprocess.CalledProcessError):
        tls.prepare(tmp_path, 'wrong.example', 'provided')
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    (tmp_path / 'server.key').write_bytes((tmp_path / 'ca.key').read_bytes())
    with pytest.raises(ValueError, match='do not match'):
        tls.prepare(tmp_path, 'sql.example', 'provided')


def test_partial_ca_is_not_silently_replaced(tls, tmp_path):
    tls.prepare(tmp_path, 'sql.example')
    (tmp_path / 'ca.key').unlink()
    with pytest.raises(ValueError, match='Incomplete SQL CA'):
        tls.prepare(tmp_path, 'sql.example')
