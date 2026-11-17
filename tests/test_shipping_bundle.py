"""The install artifact must work without a source checkout or inherited state."""
import hashlib
import base64
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture(scope='module')
def shipping():
    return module(ROOT / 'scripts/release/shipping.py', 'shipping')


@pytest.fixture(scope='module')
def artifact(tmp_path_factory, shipping):
    path = tmp_path_factory.mktemp('shipping') / 'release'
    shipping.bundle(path, candidate=True)
    return path


def settings(**overrides):
    return {'OPENROUTER_API_KEY': 'sk-or-example-test-only',
            'CALLIOPE_ADMIN_EMAIL': 'owner@example.invalid',
            'CALLIOPE_ADMIN_PASSWORD': 'test-only-strong-password',
            'CALLIOPE_PUBLIC_URL': 'http://localhost:8080', **overrides}


def initialized(artifact, tmp_path, **overrides):
    root = tmp_path / 'relocated'
    shutil.copytree(artifact, root)
    configure = module(root / 'configure.py', 'installer')
    values = configure.configure(root, settings(**overrides))
    return root, configure, values


def compose_config(root):
    if not shutil.which('docker'):
        pytest.skip('Docker Compose required for relocation validation')
    return json.loads(subprocess.check_output(['docker', 'compose', 'config', '--format', 'json'], cwd=root, text=True))


def test_release_checksums_and_catalog_are_complete(artifact, shipping):
    shipping.validate()
    for relative, expected in json.loads((artifact / 'checksums.json').read_text()).items():
        assert hashlib.sha256((artifact / relative).read_bytes()).hexdigest() == expected
    manifest = json.loads((artifact / 'release.json').read_text())
    assert manifest['distribution_state'] == 'development-candidate'
    assert len(manifest['runtime_images']) == 3
    assert all(':latest' not in image for image in manifest['runtime_images'])
    assert {image.rsplit('/', 1)[-1].split(':', 1)[0] for image in manifest['runtime_images']} == {
        'calliope-python-runtime', 'calliope-mcp-gateway', 'rvbbit-capability-dlt-mirror',
    }
    sql = (artifact / 'config/capabilities.sql').read_text()
    assert sql.count('upsert_capability_catalog_entry(') == 3
    assert '"install_mode": "image"' in sql


@pytest.mark.parametrize('profiles', ['', 'computer', 'computer,specialists'])
def test_relocated_bundle_generates_complete_compose(artifact, tmp_path, profiles):
    root, _, _ = initialized(artifact, tmp_path, COMPOSE_PROFILES=profiles)
    config = compose_config(root)
    services = config['services']
    assert {'calliope', 'postgres', 'hermes', 'hindsight', 'ui-studio', 'clover-research'} <= services.keys()
    assert ('desktop' in services) == ('computer' in profiles)
    assert ('clover-gliner' in services) == ('specialists' in profiles)
    for service in services.values():
        assert not service.get('build')
        assert ':latest' not in service['image']
        for mount in service.get('volumes', []):
            if mount['type'] == 'bind' and mount['source'] != '/var/run/docker.sock':
                assert Path(mount['source']).is_relative_to(root)
                assert Path(mount['source']).exists()
    config_text = json.dumps(config)
    assert str(ROOT) not in config_text
    assert 'hutch.clover' not in config_text
    assert services['hindsight']['environment']['HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL'] == 'http://clover:8090/v1'
    # A failed rerank degrades recall to fusion order instead of returning no memories.
    assert services['hindsight']['environment']['HINDSIGHT_API_RERANKER_1_PROVIDER'] == 'rrf'
    assert services['hindsight']['environment']['HINDSIGHT_API_RERANKER_MAX_RETRIES'] == '0'
    routes = json.loads((root / 'state/clover/hutch.json').read_text())
    assert routes['ledger_postgres']['instance_id'] == 'calliope'
    rerank = next(route for route in routes['backends'] if route['name'] == 'rerank')
    assert rerank['upstream_params']['model'] == 'voyageai/rerank-3'
    # Rust clients on the private network cannot validate the external SQL CA.
    # External SQL remains protected by the separate TLS/HBA contract below.
    assert 'sslmode=disable' in (root / 'state/clover/ledger.env').read_text()
    assert 'sslmode=disable' in (root / 'state/clover/research.env').read_text()
    assert (root / '.env').stat().st_mode & 0o777 == 0o600
    assert (root / 'state').stat().st_mode & 0o777 == 0o700
    key = root / 'state/clover/credentials/openrouter.key'
    assert key.read_text().strip() == 'sk-or-example-test-only'
    assert 'sk-or-example-test-only' not in (root / '.env').read_text()
    assert services['calliope']['environment']['CALLIOPE_OPENROUTER_KEY_FILE'] == '/run/clover-credentials/openrouter.key'
    assert services['hermes']['environment']['CALLIOPE_OPENROUTER_KEY_FILE'] == '/run/clover-credentials/openrouter.key'
    # server.py would be PID 1 otherwise and never reap exited headless Chrome children
    assert services['calliope'].get('init') is True



def test_desktop_accounts_have_valid_separate_auth_headers(artifact, tmp_path):
    root, _, values = initialized(artifact, tmp_path, COMPOSE_PROFILES='computer')
    env = compose_config(root)['services']['calliope']['environment']
    for name, principal in [('CALLIOPE_DESKTOP_VNC_BASIC', 'kasm_user'),
                            ('CALLIOPE_DESKTOP_VNC_VIEW_BASIC', 'kasm_viewer')]:
        header = values[name]
        assert header.startswith('Basic ')
        assert base64.b64decode(header[6:]).decode() == principal + ':' + values['CALLIOPE_DESKTOP_VNC_PASSWORD']
        assert env['WAREHOUSE_' + name] == header


@pytest.mark.parametrize('origin', ['http://192.168.1.20:8080', 'http://calliope.example', 'http://[2001:db8::20]'])
def test_remote_http_is_rejected_before_writing_secrets(artifact, tmp_path, origin):
    root = tmp_path / 'release'
    shutil.copytree(artifact, root)
    installer = module(root / 'configure.py', 'installer')
    with pytest.raises(ValueError, match='require HTTPS'):
        installer.configure(root, settings(CALLIOPE_PUBLIC_URL=origin))
    assert not (root / '.env').exists()
    assert not (root / 'state').exists()

def test_repeat_init_preserves_all_credentials(artifact, tmp_path):
    root, installer, _ = initialized(artifact, tmp_path)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='Already initialized'):
        installer.configure(root, settings(OPENROUTER_API_KEY='replacement'))
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert before == after


@pytest.mark.parametrize('overrides,host,port,bind,tls', [
    ({}, 'localhost', 5432, '0.0.0.0', 'generated'),
    ({'CALLIOPE_PUBLIC_URL': 'https://calliope.example'}, 'calliope.example', 5432, '0.0.0.0', 'generated'),
    ({'CALLIOPE_SQL_HOST': 'sql.example', 'CALLIOPE_SQL_BIND': '192.168.1.20:15432',
      'CALLIOPE_SQL_TLS': 'provided'}, 'sql.example', 15432, '192.168.1.20', 'provided'),
    ({'CALLIOPE_SQL_HOST': '2001:db8::20', 'CALLIOPE_SQL_BIND': '[::]:15432'},
     '2001:db8::20', 15432, '::', 'generated'),
])
def test_remote_sql_endpoint_and_tls(artifact, tmp_path, overrides, host, port, bind, tls):
    root, _, _ = initialized(artifact, tmp_path, **overrides)
    services = compose_config(root)['services']
    db = services['postgres']
    exposed = db['ports'][0]
    assert (exposed['host_ip'], int(exposed['published']), exposed['target']) == (bind, port, 5432)
    assert 'ssl=on' in db['command']
    assert 'cron.use_background_workers=on' in db['command']
    assert db['depends_on']['postgres-tls']['condition'] == 'service_completed_successfully'
    init = services['postgres-tls']
    assert init['image'] == db['image']
    assert init['network_mode'] == 'none'
    assert init['environment'] == {'CALLIOPE_SQL_HOST': host, 'CALLIOPE_SQL_TLS': tls}
    connections = json.loads((root / 'connections.json').read_text())
    assert connections['postgres'] == {'host': host, 'port': port, 'database': 'rvbbit',
        'sslmode': 'verify-full', 'sslrootcert': 'state/postgres-tls/ca.crt' if tls == 'generated' else 'system'}
    assert connections['mcp'] == connections['web'] + '/mcp'
    assert 'password' not in json.dumps(connections).lower()
    assert (root / 'config/pg_hba.conf').is_file()


@pytest.mark.parametrize('overrides', [
    {'CALLIOPE_SQL_HOST': 'https://sql.example'}, {'CALLIOPE_SQL_HOST': 'sql.example:5432'},
    {'CALLIOPE_SQL_HOST': ''}, {'CALLIOPE_SQL_BIND': '0.0.0.0:0'},
    {'CALLIOPE_SQL_BIND': '0.0.0.0:65536'}, {'CALLIOPE_SQL_BIND': '999.0.0.1:5432'},
    {'CALLIOPE_SQL_TLS': 'disabled'},
])
def test_bad_sql_config_leaves_no_state(artifact, tmp_path, overrides):
    root = tmp_path / 'release'
    shutil.copytree(artifact, root)
    installer = module(root / 'configure.py', 'installer')
    with pytest.raises(ValueError):
        installer.configure(root, settings(**overrides))
    assert not (root / '.env').exists()
    assert not (root / 'state').exists()
    assert not (root / 'connections.json').exists()


@pytest.mark.parametrize('origin,http_port,https_port,https_target', [
    ('http://localhost', 80, 8443, 443),
    ('http://127.0.0.1:8088', 8088, 8443, 443),
    ('https://calliope.example', 80, 443, 443),
    ('https://calliope.example:18443', 80, 18443, 18443),
])
def test_public_origin_ports_match_proxy_listeners(artifact, tmp_path, origin, http_port, https_port, https_target):
    root, _, _ = initialized(artifact, tmp_path, CALLIOPE_PUBLIC_URL=origin)
    proxy = compose_config(root)['services']['origin']
    ports = {int(p['published']): p['target'] for p in proxy['ports']}
    assert ports[http_port] == 80
    assert ports[https_port] == https_target
    if origin.startswith('https:'):
        assert proxy['environment']['ORIGIN_LISTEN'] == origin.removeprefix('https://')
    else:
        assert proxy['environment']['ORIGIN_LISTEN'] == ':80'
    assert proxy['environment']['CALLIOPE_TRUSTED_PROXIES'] == '127.0.0.1/32'


def test_operator_https_proxy_keeps_plain_listeners(artifact, tmp_path):
    root, _, values = initialized(
        artifact, tmp_path, CALLIOPE_PUBLIC_URL='https://calliope.local', CALLIOPE_TLS='proxy',
        CALLIOPE_TRUSTED_PROXIES='10.0.2.2, 192.168.1.0/24',
        CALLIOPE_HTTP_BIND='0.0.0.0:8080', CALLIOPE_DATA_BIND='0.0.0.0:8081')
    assert (values['CALLIOPE_PUBLIC_HOST'], values['CALLIOPE_TLS']) == ('', 'proxy')
    services = compose_config(root)['services']
    origin = services['origin']
    env = origin['environment']
    assert (env['ORIGIN_LISTEN'], env['BURROW_LISTEN']) == (':80', ':81')
    assert env['CALLIOPE_TRUSTED_PROXIES'] == '10.0.2.2/32 192.168.1.0/24'
    assert (env['WAREHOUSE_PUBLIC_URL'], env['DATARABBIT_PUBLIC_URL']) == (
        'https://calliope.local', 'https://calliope.local:3001')
    published = {(p.get('host_ip', '0.0.0.0'), int(p['published'])): p['target'] for p in origin['ports']}
    assert published[('0.0.0.0', 8080)] == 80 and published[('0.0.0.0', 8081)] == 81
    # No ACME listener or public HTTPS port: the operator's proxy owns them.
    assert not [port for (ip, port) in published if ip != '127.0.0.1' and port in (80, 443, 3001)]
    connections = json.loads((root / 'connections.json').read_text())
    assert (connections['web'], connections['datarabbit']) == ('https://calliope.local', 'https://calliope.local:3001')


@pytest.mark.parametrize('overrides', [
    {'CALLIOPE_PUBLIC_URL': 'http://localhost:8080', 'CALLIOPE_TRUSTED_PROXIES': '127.0.0.1'},
    {'CALLIOPE_TRUSTED_PROXIES': ''},
    {'CALLIOPE_TRUSTED_PROXIES': '0.0.0.0/0'},
    {'CALLIOPE_TRUSTED_PROXIES': 'proxy.example'},
    {'CALLIOPE_TRUSTED_PROXIES': '10.0.2.2', 'CALLIOPE_TLS': 'acme'},
    {'CALLIOPE_TRUSTED_PROXIES': '10.0.2.2', 'CALLIOPE_HTTP_BIND': '8080', 'CALLIOPE_DATA_BIND': '8080'},
    {'CALLIOPE_TRUSTED_PROXIES': '10.0.2.2', 'CALLIOPE_DATA_BIND': '5432'},
])
def test_invalid_proxy_settings_leave_no_secrets(artifact, tmp_path, overrides):
    root = tmp_path / 'release'
    shutil.copytree(artifact, root)
    installer = module(root / 'configure.py', 'installer')
    with pytest.raises(ValueError):
        installer.configure(root, settings(**{'CALLIOPE_PUBLIC_URL': 'https://calliope.local',
                                             'CALLIOPE_TLS': 'proxy', **overrides}))
    assert not (root / '.env').exists()
    assert not (root / 'state').exists()


@pytest.mark.parametrize('origin,overrides,data_url,bind,target', [
    ('http://localhost:8080', {}, 'http://localhost:3001', '127.0.0.1', 81),
    ('https://calliope.example', {}, 'https://calliope.example:3001', '0.0.0.0', 3001),
    ('https://calliope.example:18443', {'CALLIOPE_DATA_URL': 'https://calliope.example:9443',
      'CALLIOPE_DATA_BIND': '192.168.1.20:9443'}, 'https://calliope.example:9443', '192.168.1.20', 9443),
])
def test_datarabbit_shares_login_host_and_publishes_https(artifact, tmp_path, origin, overrides, data_url, bind, target):
    root, _, values = initialized(artifact, tmp_path, CALLIOPE_PUBLIC_URL=origin, **overrides)
    services = compose_config(root)['services']
    port = int(data_url.rsplit(':', 1)[1])
    exposed = next(p for p in services['origin']['ports'] if int(p['published']) == port)
    assert (exposed.get('host_ip', '0.0.0.0'), exposed['target']) == (bind, target)
    assert services['origin']['environment']['DATARABBIT_PUBLIC_URL'] == data_url
    assert services['origin']['environment']['WAREHOUSE_PUBLIC_URL'] == origin
    assert services['origin']['environment']['BURROW_LISTEN'] == (data_url.removeprefix('https://') if origin.startswith('https:') else ':81')
    assert services['calliope']['environment']['LENS_PUBLIC_URL'] == data_url
    assert json.loads((root / 'connections.json').read_text())['datarabbit'] == data_url
    assert not services['datarabbit'].get('ports')
    assert values['CALLIOPE_ACME_EMAIL'] == ''  # Account emails need not accept ACME mail.


@pytest.mark.parametrize('overrides', [
    {'CALLIOPE_DATA_URL': 'http://calliope.example:3001'},
    {'CALLIOPE_DATA_URL': 'https://other.example:3001'},
    {'CALLIOPE_DATA_URL': 'https://calliope.example'},
    {'CALLIOPE_DATA_URL': 'https://calliope.example:3001/path'},
    {'CALLIOPE_DATA_URL': 'https://user:password@calliope.example:3001'},
    {'CALLIOPE_DATA_URL': 'https://calliope.example:0'},
    {'CALLIOPE_DATA_URL': 'https://calliope.example:65536'},
    {'CALLIOPE_DATA_BIND': '3002'}, {'CALLIOPE_DATA_BIND': '999.0.0.1:3001'},
    {'CALLIOPE_SQL_BIND': '3001'},
])
def test_invalid_data_address_leaves_no_secrets(artifact, tmp_path, overrides):
    root = tmp_path / 'release'
    shutil.copytree(artifact, root)
    installer = module(root / 'configure.py', 'installer')
    with pytest.raises(ValueError):
        installer.configure(root, settings(CALLIOPE_PUBLIC_URL='https://calliope.example', **overrides))
    assert not (root / '.env').exists()
    assert not (root / 'state').exists()


def test_failed_init_does_not_leave_partial_secrets(artifact, tmp_path, monkeypatch):
    root = tmp_path / 'release'
    shutil.copytree(artifact, root)
    installer = module(root / 'configure.py', 'installer')
    original = installer.subprocess.run
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, 'clover-configure')
    monkeypatch.setattr(installer.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        installer.configure(root, settings())
    assert not (root / 'state').exists()
    assert not (root / '.env').exists()
    assert not list(root.glob('.initialize*'))
    monkeypatch.setattr(installer.subprocess, 'run', original)
    installer.configure(root, settings())


def test_reject_unrecognized_profile_before_initialization(artifact, tmp_path):
    root = tmp_path / 'release'
    shutil.copytree(artifact, root)
    installer = module(root / 'configure.py', 'installer')
    with pytest.raises(ValueError, match='COMPOSE_PROFILES'):
        installer.configure(root, settings(COMPOSE_PROFILES='gpu-typo'))
    assert not (root / 'state').exists()


def test_registry_errors_never_authorize_overwriting(shipping, monkeypatch):
    monkeypatch.setattr(shipping.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, '', ''))
    with pytest.raises(ValueError, match='immutable'):
        shipping.require_unpublished('example/image:1.2.0')
    for error in ('unauthorized', 'connection reset', 'denied'):
        monkeypatch.setattr(shipping.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 1, '', error))
        with pytest.raises(ValueError, match='Cannot establish'):
            shipping.require_unpublished('example/image:1.2.0')
    monkeypatch.setattr(shipping.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 1, '', 'manifest unknown'))
    shipping.require_unpublished('example/image:1.2.0')


def test_public_lock_uses_anonymous_registry_access(shipping, monkeypatch):
    def inspect(args, **kwargs):
        assert not (Path(kwargs['env']['DOCKER_CONFIG']) / 'config.json').exists()
        return subprocess.CompletedProcess(args, 0, 'Digest: sha256:' + 'a' * 64, '')
    monkeypatch.setattr(shipping.subprocess, 'run', inspect)
    result = shipping.public_lock(['example/image:1.2.0'])
    assert result['example/image:1.2.0'] == 'example/image:1.2.0@sha256:' + 'a' * 64


@pytest.mark.parametrize('changed,accepted', [
    ('deploy/configure.py\ndocker/origin/Caddyfile.calliope\n', True),
    ('services/calliope/server.py\n', False),
    ('deploy/configure.py\ncrates/pg_rvbbit/src/lib.rs\n', False),
    ('capabilities/templates/dlt-mirror/entrypoint.sh\n', False),
])
def test_installer_revision_cannot_relabel_changed_images(shipping, tmp_path, monkeypatch, changed, accepted):
    base = {'calliope_version': '1.2.0-rc.4', 'revision': 'image-commit', 'registry': 'ghcr.io/example',
            'distribution_state': 'public-images-locked', 'image_lock': {'image': 'image@sha256:abc'}}
    path = tmp_path / 'release.json'
    path.write_text(json.dumps(base))
    monkeypatch.setattr(shipping.subprocess, 'check_output', lambda *args, **kwargs: changed)
    if accepted:
        assert shipping.reuse_bundle_images(path, base, base['registry']) == base
    else:
        with pytest.raises(ValueError, match='Image source changed'):
            shipping.reuse_bundle_images(path, base, base['registry'])


def test_installer_bundle_preserves_image_provenance(shipping, artifact, tmp_path, monkeypatch):
    base = json.loads((artifact / 'release.json').read_text())
    compose = yaml.safe_load((artifact / 'compose.yaml').read_text())
    refs = {s['image'] for s in compose['services'].values()} | set(base['runtime_images'])
    locks = {ref: ref + '@sha256:' + hashlib.sha256(ref.encode()).hexdigest() for ref in refs}
    base.update(distribution_state='public-images-locked', image_lock=locks, revision='original-image-commit')
    monkeypatch.setattr(shipping, 'require_clean_checkout', lambda: None)
    monkeypatch.setattr(shipping, 'reuse_bundle_images', lambda *args: base)
    monkeypatch.setattr(shipping, 'public_lock', lambda refs: locks)
    def verify(refs, locked, version, revision, **kwargs):
        assert revision == 'original-image-commit' and kwargs['anonymous']
        return {ref: {'revision': revision, 'version': version} for ref in refs}
    monkeypatch.setattr(shipping, 'verify_release_images', verify)
    output = tmp_path / 'installer-update'
    release = shipping.bundle(output, lock=True, reuse_images_from=artifact / 'release.json')
    assert release['bundle_version'] == base['calliope_version'] + '+bundle.1'
    assert release['bundle_revision'] != release['revision'] == 'original-image-commit'
    assert release['image_lock'] == locks
    updated = yaml.safe_load((output / 'compose.yaml').read_text())
    assert updated['services']['calliope']['environment']['WAREHOUSE_CALLIOPE_DESKTOP_IMAGE_REVISION'] == 'original-image-commit'
    assert updated['services']['ui-studio']['environment']['STUDIO_REVISION'] == 'original-image-commit'


def test_publish_retry_only_reuses_matching_version_and_revision(shipping, monkeypatch):
    monkeypatch.setattr(shipping, 'image_exists', lambda ref: True)
    commands = []
    monkeypatch.setattr(shipping, 'run', lambda args, **kw: commands.append(args))
    labels = {'org.opencontainers.image.version': '1.2.0', 'org.opencontainers.image.revision': 'commit-a'}
    monkeypatch.setattr(shipping.subprocess, 'check_output', lambda *a, **kw: json.dumps([{'Config': {'Labels': labels}}]))
    assert shipping.reuse_published('example/image:1.2.0', '1.2.0', 'commit-a')
    assert commands[0][:2] == ['docker', 'pull']
    assert all('push' not in command for command in commands)
    with pytest.raises(ValueError, match='immutable'):
        shipping.reuse_published('example/image:1.2.0', '1.2.0', 'commit-b')
    with pytest.raises(ValueError, match='immutable'):
        shipping.reuse_published('example/image:1.2.0', '1.2.1', 'commit-a')


def test_private_image_bundle_reports_visibility_without_credentials(shipping, tmp_path, monkeypatch):
    monkeypatch.setattr(shipping, 'require_clean_checkout', lambda: None)
    calls = []
    def inspect(refs, *, anonymous=True):
        calls.append(anonymous)
        locks = {ref: ref + '@sha256:' + 'b' * 64 for ref in refs}
        return (locks, [refs[0]]) if anonymous else (locks, [])
    monkeypatch.setattr(shipping, 'inspect_images', inspect)
    verified = []
    def verify(refs, locked, version, revision, *, anonymous):
        assert not anonymous
        assert all(ref in locked for ref in refs)
        verified.extend(refs)
        return {ref: {'version': version, 'revision': revision} for ref in refs}
    monkeypatch.setattr(shipping, 'verify_release_images', verify)
    root = tmp_path / 'private'
    result = shipping.bundle(root, lock_private=True)
    assert calls == [False, True]
    assert result['distribution_state'] == 'awaiting-public-images'
    assert len(result['images_needing_public_access']) == 1
    assert len(verified) == 19
    assert result['image_identities'].keys() == set(verified)
    assert all('@sha256:' in ref for ref in result['image_lock'].values())
    assert not (root / '.env').exists()
    assert not (root / 'config.json').exists()


@pytest.mark.parametrize('indexed', [False, True])
def test_bundle_checks_exact_published_image_identity(shipping, monkeypatch, indexed):
    ref = 'example/image:1.2.0'
    pinned = ref + '@sha256:' + 'b' * 64
    labels = {'org.opencontainers.image.version': '1.2.0',
              'org.opencontainers.image.revision': 'commit-a',
              'org.opencontainers.image.source': 'https://github.com/ryrobes/calliope'}
    config = {'os': 'linux', 'architecture': 'amd64', 'config': {'Labels': labels}}
    def inspect(args, **kwargs):
        assert args[4] == pinned
        assert not (Path(kwargs['env']['DOCKER_CONFIG']) / 'config.json').exists()
        return json.dumps({'linux/amd64': config} if indexed else config)
    monkeypatch.setattr(shipping.subprocess, 'check_output', inspect)
    result = shipping.verify_release_images([ref], {ref: pinned}, '1.2.0', 'commit-a')
    assert result[ref]['platform'] == 'linux/amd64'
    with pytest.raises(ValueError, match='does not match release'):
        shipping.verify_release_images([ref], {ref: pinned}, '1.2.0', 'different-commit')
    labels.pop('org.opencontainers.image.version')
    with pytest.raises(ValueError, match='does not match release'):
        shipping.verify_release_images([ref], {ref: pinned}, '1.2.0', 'commit-a')


def test_incomplete_published_images_do_not_produce_bundle(shipping, tmp_path, monkeypatch):
    monkeypatch.setattr(shipping, 'require_clean_checkout', lambda: None)
    monkeypatch.setattr(shipping, 'inspect_images', lambda refs, **kw: ({}, refs))
    root = tmp_path / 'incomplete'
    with pytest.raises(ValueError, match='incomplete or inaccessible'):
        shipping.bundle(root, lock_private=True)
    assert not root.exists()


def test_generated_storage_bootstraps_in_disposable_postgres(artifact, tmp_path):
    """Opt-in integration check: real SQL from the relocated bundle, no Lab access."""
    import os
    import time
    image = os.getenv('CALLIOPE_SHIPPING_POSTGRES_TEST_IMAGE')
    if not image:
        pytest.skip('Set CALLIOPE_SHIPPING_POSTGRES_TEST_IMAGE for isolated database validation')
    root, _, _ = initialized(artifact, tmp_path)
    container = subprocess.check_output(['docker', 'run', '--rm', '-d', '--network', 'none',
        '-e', 'POSTGRES_PASSWORD=shipping-test-only', image], text=True).strip()
    try:
        for _ in range(120):
            # pg_isready also succeeds against initdb's temporary server,
            # which shuts down before PID 1 becomes the real database process.
            comm = subprocess.run(['docker', 'exec', container, 'cat', '/proc/1/comm'],
                                  capture_output=True, text=True)
            result = subprocess.run(['docker', 'exec', container, 'pg_isready', '-U', 'postgres'],
                                    capture_output=True)
            if comm.stdout.strip() == 'postgres' and result.returncode == 0:
                break
            time.sleep(.25)
        else:
            pytest.fail('Disposable Postgres did not start')
        def sql(statement):
            result = subprocess.run(['docker', 'exec', '-i', container, 'psql', '-U', 'postgres',
                '-X', '-At', '-v', 'ON_ERROR_STOP=1'], input=statement, text=True, capture_output=True)
            assert result.returncode == 0, result.stderr
            return result.stdout
        bootstrap = '\n'.join((root / 'state/clover' / name).read_text()
                              for name in ('ledger-bootstrap.sql', 'research-bootstrap.sql'))
        sql(bootstrap)
        sql(bootstrap)  # Service restarts must not drop tables or reset its roles.
        assert sql("SELECT to_regclass('clover_telemetry.calls') IS NOT NULL AND to_regclass('clover_research.research_runs') IS NOT NULL;").strip() == 't'
        assert sql("SELECT bool_and(NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole) FROM pg_roles WHERE rolname IN ('clover_ledger','clover_research');").strip() == 't'
        sql('CREATE TABLE public.customer_private_data(secret text);')
        assert sql("SELECT has_table_privilege('clover_ledger','public.customer_private_data','SELECT') OR has_table_privilege('clover_research','public.customer_private_data','SELECT');").strip() == 'f'
    finally:
        subprocess.run(['docker', 'rm', '-f', '-v', container], check=True, stdout=subprocess.DEVNULL)


def test_development_image_tag_only_retags_product_images(shipping, tmp_path):
    version = json.loads((ROOT / 'release/calliope-release.json').read_text())['calliope_version']
    tag = version + '-lab.test'
    release = shipping.bundle(tmp_path / 'dev', candidate=True, image_tag=tag)
    compose = yaml.safe_load((tmp_path / 'dev/compose.yaml').read_text())
    images = {s['image'] for s in compose['services'].values()}
    product = {image for image in images if '/calliope-' in image}
    assert product and all(image.endswith(':' + tag) for image in product)
    assert all(not image.endswith(':' + tag) for image in images - product)
    assert release['image_tag'] == tag and release['calliope_version'] == version
    assert all(image.endswith(':' + version) for image in release['runtime_images'])
    environment = compose['services']['calliope']['environment']
    assert environment['WAREHOUSE_CALLIOPE_DESKTOP_IMAGE_REF'].endswith(':' + tag)


@pytest.mark.parametrize('mode,tag', [('lock', 'lab'), ('candidate', 'bad tag'), ('candidate', '-lead')])
def test_image_tag_is_development_only(shipping, tmp_path, mode, tag):
    with pytest.raises(ValueError):
        shipping.bundle(tmp_path / 'dev', **{mode: True}, image_tag=tag)
    assert not (tmp_path / 'dev').exists()


def test_memory_is_shared_and_quoted_unless_the_operator_says_otherwise(artifact, tmp_path):
    root, configure, values = initialized(artifact, tmp_path)
    assert values['CALLIOPE_MEMORY_SCOPE'] == 'shared'
    assert compose_config(root)['services']['hermes']['environment']['CALLIOPE_MEMORY_SCOPE'] == 'shared'
    scoped_root, _, scoped = initialized(artifact, tmp_path / 'scoped', CALLIOPE_MEMORY_SCOPE='Audience')
    assert scoped['CALLIOPE_MEMORY_SCOPE'] == 'audience'
    assert compose_config(scoped_root)['services']['hermes']['environment']['CALLIOPE_MEMORY_SCOPE'] == 'audience'
    with pytest.raises(ValueError, match='shared or audience'):
        initialized(artifact, tmp_path / 'typo', CALLIOPE_MEMORY_SCOPE='private')
    server = compose_config(root)['services']['calliope']['environment']
    assert (server['CALLIOPE_MEMORY_SCOPE'], server['CALLIOPE_MEMORY_EXTRACTOR']) == ('shared', 'calliope')
    assert values['CALLIOPE_MEMORY_EXTRACTOR'] == 'calliope'
    _, _, legacy = initialized(artifact, tmp_path / 'legacy', CALLIOPE_MEMORY_EXTRACTOR='Hindsight')
    assert legacy['CALLIOPE_MEMORY_EXTRACTOR'] == 'hindsight'
    with pytest.raises(ValueError, match='calliope or hindsight'):
        initialized(artifact, tmp_path / 'typo-extractor', CALLIOPE_MEMORY_EXTRACTOR='both')
