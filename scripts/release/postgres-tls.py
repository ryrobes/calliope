#!/usr/bin/env python3
"""Prepare this instance's SQL server certificate; never contact a model or CA API."""
import ipaddress
import os
from pathlib import Path
import pwd
import re
import subprocess
import tempfile


def command(*args, check=True):
    return subprocess.run(['openssl', *map(str, args)], check=check, capture_output=True)


def identity(host):
    try:
        return 'IP:' + str(ipaddress.ip_address(host)), '-verify_ip'
    except ValueError:
        if len(host) > 253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                                     for label in host.split('.')):
            raise ValueError('CALLIOPE_SQL_HOST must be a hostname or IP address, without a port.')
        return 'DNS:' + host, '-verify_hostname'


def verify_identity(cert, host, *, check=True):
    _, flag = identity(host)
    # Older `x509 -checkhost` prints mismatches but exits successfully. Verify
    # against the supplied leaf as a temporary trust anchor: checks identity and
    # validity with reliable status codes, without assuming the user's CA.
    return command('verify', '-trusted', cert, '-partial_chain', flag, host, cert, check=check)


def verify(cert, key, host):
    verify_identity(cert, host)
    public = command('x509', '-in', cert, '-pubkey', '-noout').stdout
    if public != command('pkey', '-in', key, '-pubout').stdout:
        raise ValueError('Postgres server certificate and key do not match.')


def prepare(directory, host, mode='generated'):
    san, _ = identity(host)
    if mode not in ('generated', 'provided'):
        raise ValueError('CALLIOPE_SQL_TLS must be generated or provided.')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o755)  # Only certificates are world-readable; state/ remains private on the host.
    ca, ca_key = directory / 'ca.crt', directory / 'ca.key'
    cert, key = directory / 'server.crt', directory / 'server.key'
    if mode == 'generated':
        if ca.exists() != ca_key.exists():
            raise ValueError('Incomplete SQL CA. Restore both ca.crt and ca.key from the same backup.')
        with tempfile.TemporaryDirectory(prefix='.issue-', dir=directory) as temp:
            temp = Path(temp)
            if not ca.exists():
                command('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', '3650',
                        '-subj', '/CN=Calliope SQL instance CA',
                        '-addext', 'basicConstraints=critical,CA:TRUE',
                        '-addext', 'keyUsage=critical,keyCertSign,cRLSign',
                        '-keyout', temp / 'ca.key', '-out', temp / 'ca.crt')
                (temp / 'ca.key').chmod(0o600)
                (temp / 'ca.key').replace(ca_key)
                (temp / 'ca.crt').replace(ca)
            current = cert.exists() and key.exists()
            if current:
                current = (command('x509', '-in', cert, '-noout', '-checkend', str(30 * 86400), check=False).returncode == 0
                           and verify_identity(cert, host, check=False).returncode == 0
                           and command('verify', '-CAfile', ca, cert, check=False).returncode == 0)
                if current:
                    verify(cert, key, host)
            if not current:
                command('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-sha256',
                        '-subj', '/CN=Calliope SQL', '-keyout', temp / 'server.key', '-out', temp / 'server.csr')
                ext = temp / 'extensions.cnf'
                ext.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n'
                               'extendedKeyUsage=serverAuth\nsubjectAltName=' + san + '\n')
                command('x509', '-req', '-in', temp / 'server.csr', '-CA', ca, '-CAkey', ca_key,
                        '-set_serial', '0x' + os.urandom(16).hex(), '-days', '365', '-sha256',
                        '-extfile', ext, '-out', temp / 'server.crt')
                verify(temp / 'server.crt', temp / 'server.key', host)
                (temp / 'server.key').chmod(0o600)
                (temp / 'server.key').replace(key)
                (temp / 'server.crt').replace(cert)
    else:
        if not cert.is_file() or not key.is_file():
            raise ValueError('Put your certificate chain in state/postgres-tls/server.crt and key in server.key.')
        verify(cert, key, host)
    # PostgreSQL permits a root-owned key readable by its group, but not others.
    group = pwd.getpwnam('postgres').pw_gid
    os.chown(key, 0, group)
    key.chmod(0o640)
    cert.chmod(0o644)
    if ca.exists():
        ca.chmod(0o644)
    print('SQL TLS certificate ready for ' + host, flush=True)


if __name__ == '__main__':
    try:
        prepare(os.environ.get('CALLIOPE_SQL_TLS_DIR', '/tls'), os.environ['CALLIOPE_SQL_HOST'],
                os.environ.get('CALLIOPE_SQL_TLS', 'generated'))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        detail = error.stderr.decode(errors='replace').strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
        raise SystemExit('SQL TLS setup failed: ' + detail)
