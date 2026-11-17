"""Publication must reject broken candidates without executing their scripts."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import subprocess
import shutil
import threading

import pytest
import artifact_preflight as preflight
import server


@pytest.mark.parametrize('body', [
    'const queries = {hero: `select 1`, week: `select 2`;};',
    'const data = ;',
    'return 1;',  # illegal at top level in a browser classic script
])
def test_rejects_classic_syntax_with_html_line(body):
    result = preflight.validate_html('<html>\n<script>\n'+body+'\n</script></html>')
    assert result['error']['code'] == 'INVALID_JAVASCRIPT'
    error = result['error']['errors'][0]
    assert error['line'] == 3
    assert body in error['excerpt']


@pytest.mark.parametrize('html', [
    '<script>throw Error("must not run"); while(true){}; fetch("https://never-fetch");</script>',
    '<script type="module">import x from "not-installed"; await x(); export const value=1;</script>',
    '<script>const value = window?.missing ?? 1;</script>',
    '<script type="application/ld+json">{"not":"javascript"}</script>',
    '<script type="importmap">{"imports":{"x":"/x.js"}}</script>',
    '<script src="/external.js">invalid inline body !!!</script>',
    '<template><script>const bad = ;</script></template>',
    '<textarea><script>const bad = ;</script></textarea>',
    '<script nomodule>const bad = ;</script>',
])
def test_accepts_browser_sources_without_executing_or_fetching(html):
    assert preflight.validate_html(html) is None


def test_rejects_module_syntax():
    result = preflight.validate_html('<script type="module">export const bad = ;</script>')
    assert result['error']['code'] == 'INVALID_JAVASCRIPT'
    assert result['error']['errors'][0]['kind'] == 'module'


def test_multiline_tag_line_offset():
    result = preflight.validate_html('<script\n type="text/javascript">\nconst bad=;\n</script>')
    assert result['error']['errors'][0]['line'] == 3


def test_compiler_unavailable_is_distinct_from_bad_source(monkeypatch):
    monkeypatch.setattr(preflight, '_node_executable', lambda: None)
    assert preflight.validate_html('<script>const good=1;</script>')['error']['code'] == 'HTML_PREFLIGHT_UNAVAILABLE'
    assert preflight.validate_html('<p>Static HTML</p>') is None


@pytest.mark.parametrize('tool', [server.tool_create_live_app, server.tool_publish_dashboard])
def test_create_refuses_invalid_html_before_database_write(monkeypatch, tool):
    monkeypatch.setattr(server, '_ensure_dashboard_tables', lambda: None)
    def forbidden(*a, **k):
        pytest.fail('Invalid source reached publication database')
    monkeypatch.setattr(server, '_conn', forbidden)
    result = tool('Example', html='<script>const bad=;</script>')
    assert result['error']['code'] == 'INVALID_JAVASCRIPT'


@pytest.fixture(scope='module')
def publication_db(tmp_path_factory):
    if not shutil.which('initdb'):
        pytest.skip('PostgreSQL binaries required for atomic publication tests')
    import psycopg
    from psycopg.rows import dict_row
    root = tmp_path_factory.mktemp('publication-db')
    data, sock = root/'pg', root/'socket'
    sock.mkdir()
    subprocess.run(['initdb','-D',str(data),'-A','trust','--no-locale','-E','UTF8'],check=True,capture_output=True)
    subprocess.run(['pg_ctl','-D',str(data),'-l',str(root/'pg.log'),'-o',f"-k {sock} -c listen_addresses=''",'-w','start'],check=True,capture_output=True)
    def connect():
        return psycopg.connect(host=str(sock), dbname='postgres', row_factory=dict_row)
    try:
        with connect() as c:
            c.execute('''CREATE SCHEMA rvbbit;
              CREATE TABLE rvbbit.dashboards(id int PRIMARY KEY,slug text,name text,description text,
                latest_version int,runtime_kind text,app_kind text,manifest jsonb,last_health jsonb,updated_at timestamptz);
              CREATE TABLE rvbbit.dashboard_versions(dashboard_id int,version int,html text,manifest jsonb,
                source_files jsonb,kind text,created_by text,notes text,PRIMARY KEY(dashboard_id,version));''')
        yield connect
    finally:
        subprocess.run(['pg_ctl','-D',str(data),'-m','immediate','-w','stop'],check=True,capture_output=True)


@pytest.fixture
def publisher(monkeypatch, publication_db):
    monkeypatch.setattr(server, '_conn', publication_db)
    monkeypatch.setattr(server, '_ensure_dashboard_tables', lambda: None)
    monkeypatch.setattr(server, '_artifact_authorization', lambda: SimpleNamespace(subject='test@example.com'))
    monkeypatch.setattr(server, '_artifact_require_editor', lambda *a, **k: {'id': 1, 'editor_role': 'owner', 'home_is_room': False})
    monkeypatch.setattr(server, '_authoring_publication', lambda html, manifest, previous: manifest or {})
    monkeypatch.setattr(server, '_prepare_artifact_manifest_for_publish', lambda manifest, **k: (manifest, {}))
    monkeypatch.setattr(server, '_with_active_calliope_design_profile', lambda manifest: manifest)
    for name in ('_crawl_safe', '_enqueue_semantic_enrichment', '_enqueue_artifact_catalog_enrichment', '_auto_thumb', '_publication_design_profile_summary'):
        monkeypatch.setattr(server, name, lambda *a, **k: {})
    with publication_db() as c:
        c.execute('TRUNCATE rvbbit.dashboard_versions, rvbbit.dashboards')
        c.execute("INSERT INTO rvbbit.dashboards VALUES(1,'example','Example','',1,'html','dashboard','{}','{}',now())")
        c.execute("INSERT INTO rvbbit.dashboard_versions VALUES(1,1,'<script>const count=1;</script>','{}','{}','html','test@example.com','initial')")
    return publication_db


@pytest.mark.parametrize('tool', [server.tool_update_live_app, server.tool_update_dashboard])
def test_bad_patch_keeps_last_version_intact(publisher, tool):
    result = tool('example', expected_version=1, edits=[{'find':'const count=1;', 'replace':'const count=;'}])
    assert result['error']['code'] == 'INVALID_JAVASCRIPT'
    with publisher() as c:
        assert c.execute('SELECT latest_version FROM rvbbit.dashboards').fetchone()['latest_version'] == 1
        assert c.execute('SELECT count(*) AS n FROM rvbbit.dashboard_versions').fetchone()['n'] == 1
    good = tool('example', expected_version=1, edits=[{'find':'const count=1;', 'replace':'const count=2;'}])
    assert good['version'] == 2


@pytest.mark.parametrize('tool', [server.tool_update_live_app, server.tool_update_dashboard])
def test_two_publishers_cannot_overwrite_the_same_base_version(publisher, tool):
    start = threading.Barrier(2)
    def update(number):
        start.wait()
        return tool('example', expected_version=1, html=f'<script>const count={number};</script>')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, [2,3]))
    assert sum(r.get('version') == 2 for r in results) == 1
    error = next(r['error'] for r in results if 'error' in r)
    assert error['code'] == 'VERSION_CONFLICT'
    assert error['expected_version'] == 1 and error['current_version'] == 2
    with publisher() as c:
        assert c.execute('SELECT count(*) AS n FROM rvbbit.dashboard_versions').fetchone()['n'] == 2


def test_source_projection_keeps_version_with_exact_html():
    html = '<script>const text="unchanged";</script>'
    doc = {'slug':'example','version':{'version':7,'html':html}}
    assert server._artifact_read_part('source') == 'source'
    assert server._artifact_read_projection(doc,'source') == {'slug':'example','version':7,'html':html}
    assert server._artifact_read_projection(doc,'html') == html


@pytest.mark.parametrize('value', [0,-1,True,1.2,'latest'])
def test_invalid_expected_version_is_rejected(value):
    assert server.tool_update_live_app('example', expected_version=value)['error']['code'] == 'INVALID_ARGUMENT'


@pytest.mark.parametrize('wrapper,target', [
    (server._mcp_update_live_app, 'tool_update_live_app'),
    (server._mcp_update_dashboard, 'tool_update_dashboard'),
])
def test_mcp_forwards_expected_version(monkeypatch, wrapper, target):
    monkeypatch.setattr(server, '_with_forwarded_mcp_caller', lambda ctx, work: work())
    monkeypatch.setattr(server, '_logged', lambda name, args, work: work())
    def update(*args):
        assert args[-1] == 7
        return {'version': 8}
    monkeypatch.setattr(server, target, update)
    assert wrapper('example', expected_version=7)['version'] == 8


@pytest.mark.parametrize('name', ['dashboard_template.html','dashboard_hosted_template.html','examples/tanstack-charts-dashboard.html'])
def test_existing_templates_parse(name):
    assert preflight.validate_html((Path(__file__).parent/name).read_text()) is None
