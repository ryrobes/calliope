"""Private connection profiles and bounded transport lifetimes for the mirror worker."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def profile(value):
    if not str(value).lstrip().startswith('{'):
        return {'version': 1, 'dsn': value}
    try:
        data = json.loads(value)
        if data.get('version') != 1 or not isinstance(data.get('dsn'), str):
            raise ValueError()
        if set(data) - {'version', 'dsn', 'ssh', 'tls'}:
            raise ValueError()
        if not isinstance(data.get('ssh', {}), dict) or not isinstance(data.get('tls', {}), dict):
            raise ValueError()
        return data
    except (ValueError, AttributeError):
        raise ValueError('The saved connection profile is invalid; update it through the secure connection form.') from None


def secret_values(value):
    """Expand structured credentials before redacting worker exception text."""
    found = []
    def walk(item):
        if isinstance(item, dict):
            for key, v in item.items():
                if key not in {'host', 'port', 'username', 'auth', 'host_key', 'version'}:
                    walk(v)
        elif isinstance(item, str) and item:
            found.append(item)
    walk(profile(value))
    return found


def _write(path, value):
    path.write_text(value)
    path.chmod(0o600)
    return str(path)


def _positive_port(value):
    try:
        value = int(value)
        if 0 < value < 65536:
            return value
    except (ValueError, TypeError):
        pass
    raise ValueError('The connection port must be between 1 and 65535.')


@contextmanager
def access(value, state_dir):
    """Yield an executable DSN; tunnels and private files outlive all source reads."""
    data = profile(value)
    source = data['dsn']
    ssh = data.get('ssh') or {}
    tls = data.get('tls') or {}
    if not ssh and not tls:
        yield source
        return
    child = None
    with tempfile.TemporaryDirectory(prefix='mirror-connect-') as name:
        temp = Path(name)
        parsed = urlsplit(source)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        dialect = parsed.scheme.split('+', 1)[0]
        if tls:
            if dialect not in {'postgresql', 'mysql', 'mariadb'}:
                raise ValueError('Uploaded TLS certificates are not supported for this database driver.')
            if set(tls) - {'ca', 'cert', 'key'} or bool(tls.get('cert')) != bool(tls.get('key')):
                raise ValueError('Provide a matching TLS client certificate and key.')
            names = {'ca': 'sslrootcert', 'cert': 'sslcert', 'key': 'sslkey'} if dialect == 'postgresql' else {'ca': 'ssl_ca', 'cert': 'ssl_cert', 'key': 'ssl_key'}
            for key, contents in tls.items():
                if not isinstance(contents, str) or '-----BEGIN ' not in contents:
                    raise ValueError('Certificates and private keys must use PEM format.')
                query[names[key]] = _write(temp / (key + '.pem'), contents)
            if dialect == 'postgresql':
                query.setdefault('sslmode', 'verify-full' if tls.get('ca') else 'require')
        netloc = parsed.netloc
        try:
            if ssh:
                executable = shutil.which('ssh')
                if not executable:
                    raise RuntimeError('SSH support is not installed in the mirror worker.')
                host = str(ssh.get('host') or '')
                username = str(ssh.get('username') or '')
                if not host or not username or any(re.search(r'[\s\x00-\x1f]', v) or v.startswith('-') for v in (host, username)):
                    raise ValueError('Enter a valid SSH server address and username.')
                port = _positive_port(ssh.get('port') or 22)
                remote_host = parsed.hostname
                remote_port = parsed.port or {'postgresql': 5432, 'mysql': 3306, 'mariadb': 3306, 'mssql': 1433, 'oracle': 1521, 'db2': 50000}.get(dialect)
                if not remote_host or not remote_port or re.search(r'[\s\x00-\x1f]', remote_host):
                    raise ValueError('The database URL must name a server reachable from the SSH server.')
                if ':' in remote_host:
                    remote_host = '[' + remote_host + ']'
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1', 0))
                    local_port = listener.getsockname()[1]
                known_dir = Path(state_dir) / 'ssh-host-keys'
                known_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                known = known_dir / (hashlib.sha256(f'{host}:{port}'.encode()).hexdigest() + '.known_hosts')
                expected = str(ssh.get('host_key') or '').strip()
                if expected:
                    # Keep user-supplied pins per attempt; never overwrite the remembered key.
                    known = temp / 'known_hosts'
                    _write(known, expected + '\n')
                args = [executable, '-F', '/dev/null', '-N', '-T', '-p', str(port), '-l', username,
                        '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=12', '-o', 'ConnectionAttempts=1',
                        '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=2',
                        '-o', 'NumberOfPasswordPrompts=1', '-o', 'IdentitiesOnly=yes', '-o', 'IdentityAgent=none',
                        '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'UserKnownHostsFile=' + str(known),
                        '-o', 'StrictHostKeyChecking=' + ('yes' if expected else 'accept-new'),
                        '-L', f'127.0.0.1:{local_port}:{remote_host}:{remote_port}']
                auth = ssh.get('auth', 'key')
                if auth == 'key':
                    key = str(ssh.get('private_key') or '')
                    if 'PRIVATE KEY-----' not in key:
                        raise ValueError('Enter the SSH private key in PEM or OpenSSH format.')
                    args.extend(['-i', _write(temp / 'identity', key.rstrip() + '\n'), '-o', 'PreferredAuthentications=publickey'])
                    password = str(ssh.get('passphrase') or '')
                elif auth == 'password':
                    password = str(ssh.get('password') or '')
                    if not password:
                        raise ValueError('Enter the SSH password.')
                    args.extend(['-o', 'PreferredAuthentications=password,keyboard-interactive', '-o', 'PubkeyAuthentication=no'])
                else:
                    raise ValueError('Choose a supported SSH authentication method.')
                _write(temp / 'answer', password)
                askpass = temp / 'askpass'
                # The secret is a protected file, never command text or an environment value.
                askpass.write_text('#!/bin/sh\nexec cat "${0%/*}/answer"\n')
                askpass.chmod(0o700)
                env = {**os.environ, 'SSH_ASKPASS': str(askpass), 'SSH_ASKPASS_REQUIRE': 'force', 'DISPLAY': ':mirror'}
                args.extend(['--', host])
                with (temp / 'ssh.log').open('w+') as errors:
                    child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=errors, env=env, start_new_session=True)
                    deadline = time.monotonic() + 18
                    while time.monotonic() < deadline:
                        if child.poll() is not None:
                            errors.seek(0)
                            detail = errors.read()[-3000:]
                            if 'HOST IDENTIFICATION HAS CHANGED' in detail or 'Host key verification failed' in detail:
                                raise RuntimeError('The SSH server identity does not match its saved or expected key. Ask your administrator to verify it.')
                            if 'Permission denied' in detail or 'invalid format' in detail or 'error in libcrypto' in detail:
                                raise RuntimeError('The SSH server rejected the login or private key. Check the SSH credentials.')
                            raise RuntimeError('The SSH tunnel could not be opened. Check the server address, port, access and forwarding permissions.')
                        try:
                            with socket.create_connection(('127.0.0.1', local_port), timeout=.2):
                                break
                        except OSError:
                            time.sleep(.05)
                    else:
                        raise RuntimeError('The SSH server did not become reachable within 18 seconds.')
                if dialect == 'postgresql':
                    # libpq verifies TLS against the real hostname while connecting locally.
                    query.update(hostaddr='127.0.0.1', port=str(local_port))
                else:
                    userinfo = netloc.rsplit('@', 1)[0] + '@' if '@' in netloc else ''
                    netloc = f'{userinfo}127.0.0.1:{local_port}'
            yield urlunsplit((parsed.scheme, netloc, parsed.path, urlencode(query), parsed.fragment))
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=3)
