#!/usr/bin/env python3
"""Create a fresh appliance environment without printing or reusing internal secrets.

The inference key file contains only the operator-provided Clover key. Existing
environment files are never overwritten: upgrades must preserve wrapping keys.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlsplit


def create_environment(path: Path, key: str, email: str, version: str,
                       revision: str, registry: str = 'ghcr.io/ryrobes',
                       origin_bind: str = '127.0.0.1:8080',
                       public_url: str = 'http://localhost:8080') -> None:
    key = key.strip()
    for value in (key, email, version, revision, registry, origin_bind, public_url):
        if not value or any(c in value for c in "\r\n'\x00"):
            raise ValueError('values must be nonempty single-line strings without single quotes')
    if not re.fullmatch(r'[^\s,@]+@[^\s,@]+\.[^\s,@]+', email):
        raise ValueError('supply exactly one administrator email')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', version):
        raise ValueError('version must be an explicit image tag')
    url = urlsplit(public_url)
    if (url.scheme not in ('http', 'https') or not url.hostname
            or url.username or url.password or url.path not in ('', '/')
            or url.query or url.fragment or any(c.isspace() for c in public_url)):
        raise ValueError('public-url must be an HTTP(S) origin without credentials or a path')
    values = {
        'CALLIOPE_VERSION': version, 'CALLIOPE_REVISION': revision,
        'CALLIOPE_REGISTRY': registry,
        'RVBBIT_CLOVER_KEY': key,
        'RVBBIT_CLOVER_OPENAI_BASE_URL': 'http://clover:8090/v1',
        'RVBBIT_CLOVER_REQUIRED_MODEL': 'calliope',
        'WAREHOUSE_CALLIOPE_TTS_PROVIDER': 'hutch',
        'POSTGRES_DB': 'rvbbit',
        'WAREHOUSE_ALLOWED_EMAILS': email,
        'WAREHOUSE_TEAM_BOOTSTRAP_ADMINS': email,
        'WAREHOUSE_PUBLIC_URL': public_url.rstrip('/'),
        'LENS_PUBLIC_URL': public_url.rstrip('/'),
        'WAREHOUSE_HERMES_MEMORY_KEY': 'company',
        'WAREHOUSE_AUTH': 'shared', 'WAREHOUSE_LOGIN_UI': 'lens',
        'RVBBIT_MODE': 'burrow', 'RVBBIT_REQUIRE_CANONICAL_CREDENTIALS': 'true',
        'CALLIOPE_MODEL_CONTEXT_LENGTH': '262144', 'CALLIOPE_MODEL_MAX_TOKENS': '32768',
        'RVBBIT_ORIGIN_PORT': origin_bind,
        'RVBBIT_ORIGIN_TLS_PORT': '127.0.0.1:8443',
        'RVBBIT_BURROW_PORT': '127.0.0.1:3001',
        'RVBBIT_LENS_PORT': '127.0.0.1:3000',
        'RVBBIT_POSTGRES_PORT': '127.0.0.1:55433',
    }
    for name in ('POSTGRES_PASSWORD', 'RVBBIT_CREDENTIAL_KEY', 'RVBBIT_GATEWAY_TOKEN',
                 'RVBBIT_MIRROR_TOKEN', 'WAREHOUSE_JWT_SECRET', 'WAREHOUSE_MCP_KEY',
                 'WAREHOUSE_HERMES_API_KEY', 'WAREHOUSE_HERMES_MCP_KEY',
                 'WAREHOUSE_LOGIN_PASSWORD'):
        values[name] = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL also prevents replacing a symlink or racing another provisioner.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write('# Fresh appliance; generated internal secrets. Keep private.\n')
        for name, value in values.items():
            # Compose treats single-quoted values literally, including dollar signs.
            handle.write(f"{name}='{value}'\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path('/opt/rvbbit/.env'))
    parser.add_argument('--clover-key-file', type=Path, required=True)
    parser.add_argument('--admin-email', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--registry', default='ghcr.io/ryrobes')
    parser.add_argument('--public-url', required=True,
                        help='Browser-facing origin, including the forwarded port for SSH tunnels')
    parser.add_argument('--origin-bind', default='127.0.0.1:8080',
                        help='Use 0.0.0.0:8080 inside a VM behind loopback-only QEMU forwarding')
    args = parser.parse_args()
    create_environment(args.env_file, args.clover_key_file.read_text(), args.admin_email,
                       args.version, args.revision, args.registry, args.origin_bind, args.public_url)
    print(f'Created {args.env_file} with mode 0600; values not printed')


if __name__ == '__main__':
    main()
