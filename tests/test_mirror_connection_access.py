"""Real SSH forwarding plus credential/profile cleanup without external services."""
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import threading
import time
from urllib.parse import parse_qsl, urlsplit

import pytest

PATH = Path(__file__).resolve().parents[1] / 'capabilities/templates/dlt-mirror/connection_access.py'
spec = importlib.util.spec_from_file_location('mirror_connection_access', PATH)
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)


@pytest.fixture
def ssh_source(tmp_path):
    if not shutil.which('sshd') or not shutil.which('ssh-keygen'):
        pytest.skip('Local OpenSSH server/client required for the isolated tunnel test')
    for name in ('host', 'client'):
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', 'test-passphrase' if name == 'client' else '', '-f', str(tmp_path / name)], check=True)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    listener.settimeout(.2)
    source_port = listener.getsockname()[1]
    stop = threading.Event()
    def serve():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
                with conn:
                    conn.sendall(b'mirror-source-reached')
            except socket.timeout:
                pass
            except OSError:
                break
    server = threading.Thread(target=serve, daemon=True)
    server.start()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        ssh_port = probe.getsockname()[1]
    config = tmp_path / 'sshd_config'
    config.write_text(f'''Port {ssh_port}
ListenAddress 127.0.0.1
HostKey {tmp_path / 'host'}
PidFile {tmp_path / 'sshd.pid'}
AuthorizedKeysFile {tmp_path / 'client.pub'}
StrictModes no
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
AllowTcpForwarding yes
PermitOpen 127.0.0.1:{source_port}
LogLevel ERROR
''')
    log = (tmp_path / 'sshd.log').open('w+')
    process = subprocess.Popen([shutil.which('sshd'), '-D', '-e', '-f', str(config)], stdout=subprocess.DEVNULL, stderr=log)
    try:
        for _ in range(100):
            if process.poll() is not None:
                log.seek(0)
                pytest.fail('Isolated sshd did not start: ' + log.read())
            try:
                with socket.create_connection(('127.0.0.1', ssh_port), timeout=.1):
                    break
            except OSError:
                time.sleep(.03)
        key = (tmp_path / 'host.pub').read_text().split()
        yield {'version': 1, 'dsn': f'postgresql://reader:fake-password@127.0.0.1:{source_port}/company', 'ssh': {
            'host': '127.0.0.1', 'port': ssh_port, 'username': pwd.getpwuid(os.getuid()).pw_name,
            'auth': 'key', 'private_key': (tmp_path / 'client').read_text(), 'passphrase': 'test-passphrase',
            'host_key': f'[127.0.0.1]:{ssh_port} {key[0]} {key[1]}'}}
    finally:
        process.terminate()
        process.wait(timeout=5)
        stop.set()
        listener.close()
        server.join(timeout=2)
        log.close()


def test_real_tunnel_authenticates_encrypted_key_and_closes(ssh_source, tmp_path):
    with access.access(json.dumps(ssh_source), tmp_path / 'state') as dsn:
        query = dict(parse_qsl(urlsplit(dsn).query))
        port = int(query['port'])
        assert query['hostaddr'] == '127.0.0.1'
        with socket.create_connection(('127.0.0.1', port), timeout=3) as connection:
            assert connection.recv(100) == b'mirror-source-reached'
    with pytest.raises(OSError):
        socket.create_connection(('127.0.0.1', port), timeout=.2)


def test_real_tunnel_rejects_untrusted_host_key(ssh_source, tmp_path):
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(tmp_path / 'wrong')], check=True)
    key = (tmp_path / 'wrong.pub').read_text().split()
    ssh_source['ssh']['host_key'] = f"[127.0.0.1]:{ssh_source['ssh']['port']} {key[0]} {key[1]}"
    with pytest.raises(RuntimeError, match='identity does not match'):
        with access.access(json.dumps(ssh_source), tmp_path / 'state'):
            pytest.fail('Mismatched host identity was accepted')


def test_tls_private_files_are_scoped_and_removed_on_failure(tmp_path):
    profile = {'version': 1, 'dsn': 'postgresql://reader:password@db/company', 'tls': {
        'ca': '-----BEGIN CERTIFICATE-----\ncanary\n-----END CERTIFICATE-----'}}
    with pytest.raises(RuntimeError):
        with access.access(json.dumps(profile), tmp_path) as dsn:
            query = dict(parse_qsl(urlsplit(dsn).query))
            certificate = Path(query['sslrootcert'])
            assert certificate.stat().st_mode & 0o777 == 0o600
            assert query['sslmode'] == 'verify-full'
            raise RuntimeError('Source probe failed')
    assert not certificate.exists()


def test_legacy_dsn_and_structured_redaction():
    dsn = 'postgresql://reader:password@db/company'
    assert access.profile(dsn)['dsn'] == dsn
    assert access.secret_values(json.dumps({'version': 1, 'dsn': dsn, 'ssh': {'host': 'public-host', 'auth': 'key', 'private_key': 'private-canary'}})) == [dsn, 'private-canary']
