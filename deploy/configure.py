#!/usr/bin/env python3
"""Initialize a shipping bundle. Python stdlib only; no network calls or inference."""
import argparse
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import subprocess
import sys
from urllib.parse import urlsplit


def env_values(path):
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            raise ValueError('Settings must be KEY=value lines, not shell commands.')
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def private(path, content):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
        f.write(content)


def sql_endpoint(settings, default_host):
    host = settings.get('CALLIOPE_SQL_HOST', default_host).strip()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', part)
                                     for part in host.split('.')):
            raise ValueError('CALLIOPE_SQL_HOST must be a hostname or IP address without a port.')
    bind = settings.get('CALLIOPE_SQL_BIND', '0.0.0.0:5432')
    match = re.fullmatch(r'(?:(\[[0-9a-fA-F:]+\]|[0-9.]+):)?([0-9]+)', bind)
    if not match or not 1 <= int(match[2]) <= 65535:
        raise ValueError('CALLIOPE_SQL_BIND must be a port or IP:port, for example 0.0.0.0:5432.')
    if match[1]:
        ipaddress.ip_address(match[1].strip('[]'))
    mode = settings.get('CALLIOPE_SQL_TLS', 'generated')
    if mode not in ('generated', 'provided'):
        raise ValueError('CALLIOPE_SQL_TLS must be generated or provided.')
    return host, bind, mode, int(match[2])


def bind_address(value, name):
    match = re.fullmatch(r'(?:(\[[0-9a-fA-F:]+\]|[0-9.]+):)?([0-9]+)', value)
    if not match or not 1 <= int(match[2]) <= 65535:
        raise ValueError(f'{name} must be a port or IP:port, for example 127.0.0.1:8080.')
    if match[1]:
        ipaddress.ip_address(match[1].strip('[]'))
    return int(match[2])


def default_http_bind(url, public_port, proxy):
    """Where the plain web listener is published when CALLIOPE_HTTP_BIND is unset.

    Plain HTTP is only allowed on localhost, 127.0.0.1 and ::1. That listener
    stays on 127.0.0.1 so an unattended localhost install is not published on
    every interface. HTTPS keeps port 80 for ACME; its public port is the TLS
    listener, so this default must not reuse that port.
    """
    if proxy:
        return '127.0.0.1:8080'
    if url.scheme == 'https':
        return '80'
    if url.hostname in ('localhost', '127.0.0.1', '::1'):
        return f'127.0.0.1:{public_port}'
    return str(public_port)


def trusted_proxies(settings):
    """The operator's HTTPS proxy, as this host sees its connections."""
    networks = []
    for item in re.split(r'[\s,]+', settings.get('CALLIOPE_TRUSTED_PROXIES', '').strip()):
        if not item:
            continue
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError:
            raise ValueError('CALLIOPE_TRUSTED_PROXIES must list IP addresses or CIDR ranges.') from None
        if network.prefixlen == 0:
            raise ValueError('CALLIOPE_TRUSTED_PROXIES must name your proxy, not every address.')
        networks.append(str(network))
    if not networks:
        raise ValueError('CALLIOPE_TLS=proxy requires CALLIOPE_TRUSTED_PROXIES: the address your HTTPS proxy connects from.')
    return ' '.join(networks)


def data_endpoint(settings, web, proxy=False):
    """A second origin on the same host shares certificates and login cookies."""
    host = f'[{web.hostname}]' if ':' in web.hostname else web.hostname
    origin = settings.get('CALLIOPE_DATA_URL', f'{web.scheme}://{host}:3001').rstrip('/')
    data = urlsplit(origin)
    if (data.scheme != web.scheme or data.hostname != web.hostname or data.username
            or data.password or data.path or data.query or data.fragment):
        raise ValueError('CALLIOPE_DATA_URL must use the same scheme and hostname as CALLIOPE_PUBLIC_URL, with a different port.')
    port = data.port or (443 if data.scheme == 'https' else 80)
    if data.port == 0 or port in {web.port or (443 if web.scheme == 'https' else 80), 80, 5432}:
        raise ValueError('CALLIOPE_DATA_URL needs a separate fixed port (normally 3001).')
    if proxy:
        # The operator's proxy serves the public port; this plain listener is its upstream.
        bind = settings.get('CALLIOPE_DATA_BIND', '127.0.0.1:8081')
        return origin, bind, ':81', bind_address(bind, 'CALLIOPE_DATA_BIND'), 81
    bind = settings.get('CALLIOPE_DATA_BIND', str(port) if data.scheme == 'https' else f'127.0.0.1:{port}')
    match = re.fullmatch(r'(?:(\[[0-9a-fA-F:]+\]|[0-9.]+):)?([0-9]+)', bind)
    if not match or int(match[2]) != port:
        raise ValueError('CALLIOPE_DATA_BIND must publish the port in CALLIOPE_DATA_URL.')
    if match[1]:
        ipaddress.ip_address(match[1].strip('[]'))
    return origin, bind, data.netloc if data.scheme == 'https' else ':81', port, port if data.scheme == 'https' else 81


def demo_config(settings):
    """Demo mode is off unless the installer was given an explicit flag."""
    raw = settings.get('CALLIOPE_DEMO_MODE', '').strip().lower()
    if raw in ('', '0', 'false', 'no', 'off'):
        enabled = False
    elif raw in ('1', 'true', 'yes', 'on'):
        enabled = True
    else:
        raise ValueError('CALLIOPE_DEMO_MODE must be 0 or 1.')
    schemas = settings.get('CALLIOPE_DEMO_SCHEMAS', '').strip() or 'public_demo'
    parts = [part.strip().lower() for part in schemas.split(',') if part.strip()]
    if not parts or any(not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', part) for part in parts):
        raise ValueError('CALLIOPE_DEMO_SCHEMAS must be a comma-separated list of schema names.')
    if any(part in {'rvbbit', 'rvbbit_meta', 'hindsight', 'information_schema', 'pg_catalog', 'public'} or part.startswith('pg_') for part in parts):
        raise ValueError('CALLIOPE_DEMO_SCHEMAS cannot name a system schema.')
    # Caddy still redirects "/" to the Floor when demo mode is off. While the
    # flag is on, Caddy proxies "/" and Calliope chooses gallery vs Floor.
    redirect = '/floor'
    custom = settings.get('CALLIOPE_ROOT_REDIRECT', '').strip()
    if custom:
        if not re.fullmatch(r'/[A-Za-z0-9_./-]{0,80}', custom) or '//' in custom:
            raise ValueError('CALLIOPE_ROOT_REDIRECT must be one absolute path.')
        redirect = custom
    return ('1' if enabled else '0'), ','.join(parts), redirect


def prepare(root, workspace, settings):
    settings = dict(settings)
    required = ('OPENROUTER_API_KEY', 'CALLIOPE_ADMIN_EMAIL', 'CALLIOPE_ADMIN_PASSWORD', 'CALLIOPE_PUBLIC_URL')
    for key in required:
        value = settings.get(key, '').strip()
        if not value or any(c in value for c in "\r\n\x00'"):
            raise ValueError(f'Set {key} to a nonempty single-line value without single quotes.')
        settings[key] = value
    if any(c.isspace() for c in settings['OPENROUTER_API_KEY']):
        raise ValueError('The OpenRouter key must not contain whitespace.')
    email = settings['CALLIOPE_ADMIN_EMAIL']
    if not re.fullmatch(r'[^\s,@]+@[^\s,@]+\.[^\s,@]+', email):
        raise ValueError('Supply one administrator email.')
    origin = settings['CALLIOPE_PUBLIC_URL'].rstrip('/')
    url = urlsplit(origin)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError('CALLIOPE_PUBLIC_URL must be an HTTP(S) origin without a path.')
    if url.scheme == 'http' and url.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Remote Calliope and MCP connections require HTTPS. Use a DNS hostname with HTTPS; plain HTTP is only supported on localhost.')
    public_port = url.port or (443 if url.scheme == 'https' else 80)
    if url.port == 0:
        raise ValueError('CALLIOPE_PUBLIC_URL must use a fixed port greater than zero.')
    tls = settings.get('CALLIOPE_TLS', 'automatic')
    if tls not in ('automatic', 'proxy'):
        raise ValueError('CALLIOPE_TLS must be automatic or proxy.')
    proxy = tls == 'proxy'
    if proxy and url.scheme != 'https':
        raise ValueError('CALLIOPE_TLS=proxy means your proxy serves HTTPS; use an https:// CALLIOPE_PUBLIC_URL.')
    proxies = trusted_proxies(settings) if proxy else None
    http_bind = settings.get('CALLIOPE_HTTP_BIND', default_http_bind(url, public_port, proxy))
    http_port = bind_address(http_bind, 'CALLIOPE_HTTP_BIND')
    sql_host, sql_bind, sql_tls, sql_port = sql_endpoint(settings, url.hostname)
    data_url, data_bind, data_listen, data_published, data_port = data_endpoint(settings, url, proxy)
    if data_published == sql_port or (proxy and data_published == http_port):
        raise ValueError('DataRabbit, the web listener and PostgreSQL must use different published ports.')
    project = settings.get('COMPOSE_PROJECT_NAME', 'calliope')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', project):
        raise ValueError('COMPOSE_PROJECT_NAME must be a lowercase Compose project name.')
    profiles = settings.get('COMPOSE_PROFILES', '').split(',')
    if any(p and p not in ('computer', 'specialists') for p in profiles):
        raise ValueError('COMPOSE_PROFILES supports computer and specialists.')
    if len(settings['CALLIOPE_ADMIN_PASSWORD']) < 12:
        raise ValueError('Use at least 12 characters for the administrator password.')
    if any(any(c in str(v) for c in "\r\n\x00'") for v in settings.values()):
        raise ValueError('Settings must be single-line values without single quotes.')
    release = json.loads((root / 'release.json').read_text())
    state = workspace / 'state'
    state.mkdir(mode=0o700)
    (state / 'postgres-tls').mkdir(mode=0o700)
    service_key = secrets.token_hex(32)
    child_env = {**{k: v for k, v in os.environ.items() if k not in ('HF_TOKEN', 'CLOVER_REMOTE_KEY')}, 'OPENROUTER_API_KEY': settings['OPENROUTER_API_KEY'], 'RVBBIT_CLOVER_KEY': service_key}
    optional = (['--gliner-worker', 'http://clover-gliner:8080', '--tspulse-worker', 'http://clover-tspulse:8080']
                if 'specialists' in settings.get('COMPOSE_PROFILES', '').split(',') else [])
    subprocess.run([sys.executable, str(root / 'lib/clover_configure.py'), '--output', str(state / 'clover'),
                    '--chat-model', 'openai/gpt-6.1-sol', '--analytics-worker', 'http://clover-analytics:8080',
                    '--research', *optional], env=child_env, check=True,
                   stdout=subprocess.DEVNULL)
    spec = importlib.util.spec_from_file_location('ledger', root / 'lib/ledger.py')
    ledger = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ledger)
    # The private Compose network uses authenticated internal connections. The
    # published SQL endpoint separately requires the instance CA and TLS.
    ledger.prepare(state / 'clover', project, 'postgres', 5432, 'rvbbit', schema_path=root / 'config/ledger.sql', sslmode='disable')
    spec = importlib.util.spec_from_file_location('research', root / 'lib/research.py')
    research = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(research)
    research.prepare(state / 'clover', schema_path=root / 'config/research.sql', sslmode='disable')
    studio_token = secrets.token_urlsafe(48)
    private(state / 'studio-token', studio_token + '\n')
    # Mounted into distinct UIDs; the containing host directory remains private.
    (state / 'studio-token').chmod(0o444)
    private(state / 'studio-client.json', json.dumps({'url': 'http://ui-studio:18456', 'token': studio_token}) + '\n')
    (state / 'studio-client.json').chmod(0o444)
    memory_scope = settings.get('CALLIOPE_MEMORY_SCOPE', '').strip().lower() or 'shared'
    if memory_scope not in ('shared', 'audience'):
        raise ValueError('CALLIOPE_MEMORY_SCOPE must be shared or audience.')
    memory_extractor = settings.get('CALLIOPE_MEMORY_EXTRACTOR', '').strip().lower() or 'calliope'
    if memory_extractor not in ('calliope', 'hindsight'):
        raise ValueError('CALLIOPE_MEMORY_EXTRACTOR must be calliope or hindsight.')
    demo_mode, demo_schemas, demo_redirect = demo_config(settings)
    demo_password = settings.get('CALLIOPE_DEMO_GUEST_PASSWORD', '').strip()
    if demo_mode == '1' and len(demo_password) < 16:
        demo_password = secrets.token_hex(24)
    values = {
        'COMPOSE_PROJECT_NAME': project,
        'CALLIOPE_VERSION': release['calliope_version'],
        'CALLIOPE_REVISION': release['revision'],
        'CALLIOPE_REGISTRY': release['registry'],
        'RVBBIT_CLOVER_KEY': service_key,
        'POSTGRES_DB': 'rvbbit',
        'WAREHOUSE_ALLOWED_EMAILS': email,
        'WAREHOUSE_TEAM_BOOTSTRAP_ADMINS': email,
        'WAREHOUSE_LOGIN_PASSWORD': settings['CALLIOPE_ADMIN_PASSWORD'],
        'WAREHOUSE_PUBLIC_URL': origin,
        'LENS_PUBLIC_URL': data_url,
        'CALLIOPE_PUBLIC_HOST': url.netloc if url.scheme == 'https' and not proxy else '',
        'CALLIOPE_ACME_EMAIL': settings.get('CALLIOPE_ACME_EMAIL', ''),
        'CALLIOPE_HTTP_BIND': http_bind,
        'CALLIOPE_HTTPS_BIND': settings.get('CALLIOPE_HTTPS_BIND', str(public_port) if url.scheme == 'https' and not proxy else '127.0.0.1:8443'),
        'CALLIOPE_HTTPS_PORT': str(public_port) if url.scheme == 'https' and not proxy else '443',
        'CALLIOPE_DATA_URL': data_url,
        'CALLIOPE_DATA_BIND': data_bind,
        'CALLIOPE_DATA_LISTEN': data_listen,
        'CALLIOPE_DATA_PORT': str(data_port),
        'CALLIOPE_SQL_HOST': sql_host,
        'CALLIOPE_SQL_BIND': sql_bind,
        'CALLIOPE_SQL_TLS': sql_tls,
        'CALLIOPE_TLS': tls,
        **({'CALLIOPE_TRUSTED_PROXIES': proxies} if proxy else {}),
        'CALLIOPE_MEMORY_SCOPE': memory_scope,
        'CALLIOPE_MEMORY_EXTRACTOR': memory_extractor,
        'CALLIOPE_DEMO_MODE': demo_mode,
        'CALLIOPE_DEMO_SCHEMAS': demo_schemas,
        'CALLIOPE_ROOT_REDIRECT': demo_redirect,
        'CALLIOPE_DEMO_GUEST_PASSWORD': demo_password,
        'COMPOSE_PROFILES': settings.get('COMPOSE_PROFILES', ''),
        'CALLIOPE_COMPUTER_ENABLED': 'true' if 'computer' in settings.get('COMPOSE_PROFILES', '').split(',') else 'false',
    }
    for key in ('POSTGRES_PASSWORD', 'RVBBIT_CREDENTIAL_KEY', 'RVBBIT_GATEWAY_TOKEN', 'RVBBIT_MIRROR_TOKEN',
                'WAREHOUSE_JWT_SECRET', 'WAREHOUSE_MCP_KEY', 'WAREHOUSE_HERMES_API_KEY',
                'WAREHOUSE_HERMES_MCP_KEY', 'CALLIOPE_DESKTOP_CONTROL_KEY', 'CALLIOPE_DESKTOP_VNC_PASSWORD'):
        values[key] = secrets.token_hex(32)
    import base64
    for setting, user in (('CALLIOPE_DESKTOP_VNC_BASIC', 'kasm_user'),
                          ('CALLIOPE_DESKTOP_VNC_VIEW_BASIC', 'kasm_viewer')):
        # The pinned Kasm image creates two permission-scoped users with VNC_PW.
        values[setting] = 'Basic ' + base64.b64encode((user + ':' + values['CALLIOPE_DESKTOP_VNC_PASSWORD']).encode()).decode()
    if any(any(c in str(v) for c in "\r\n\x00'") for v in values.values()):
        raise ValueError('Settings must be single-line values without single quotes.')
    private(workspace / '.env', '# Generated once. Preserve this file and state/ when upgrading.\n' + ''.join(f"{k}='{v}'\n" for k, v in values.items()))
    private(workspace / 'connections.json', json.dumps({
        'web': origin, 'mcp': origin + '/mcp', 'datarabbit': data_url,
        'postgres': {'host': sql_host, 'port': sql_port, 'database': 'rvbbit',
                     'sslmode': 'verify-full',
                     'sslrootcert': 'state/postgres-tls/ca.crt' if sql_tls == 'generated' else 'system'},
    }, indent=2) + '\n')
    return values


def configure(root, settings):
    root = Path(root).resolve()
    lock = root / '.initialize.lock'
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError('Initialization already running; inspect .initialize.lock before retrying.') from None
    os.close(fd)
    try:
        if any((root / name).exists() for name in ('.env', 'state', 'connections.json')):
            raise ValueError('Already initialized. Keep .env and state/ across upgrades; do not regenerate secrets.')
        with tempfile.TemporaryDirectory(prefix='.initialize-', dir=root) as tmp:
            workspace = Path(tmp)
            result = prepare(root, workspace, settings)
            (workspace / 'state').rename(root / 'state')
            try:
                (workspace / 'connections.json').rename(root / 'connections.json')
                (workspace / '.env').rename(root / '.env')
            except BaseException:
                shutil.rmtree(root / 'state')  # Only the state created by this invocation.
                (root / 'connections.json').unlink(missing_ok=True)
                raise
            return result
    finally:
        lock.unlink()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--settings', type=Path, default=Path('settings.env'))
    a = p.parse_args()
    configure(Path(__file__).resolve().parent, env_values(a.settings))
    print('Initialized. Run: docker compose up -d --wait')
    print('Keep .env and state/ private and backed up. No credentials were printed.')
    print('Web, DataRabbit, MCP and SQL endpoints are listed in connections.json.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as e:
        raise SystemExit(str(e))
