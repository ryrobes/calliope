"""Contract checks for server-owned UI apps, including unsafe input and versioning."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from pglast import parse_sql
import ui_apps

EXAMPLES = Path(__file__).parent / 'examples/ui-apps'

@pytest.fixture
def doc():
    return json.loads((EXAMPLES / 'service-work-orders.json').read_text())


def test_both_apps_are_plain_definitions(doc):
    ui_apps.validate(doc)
    ui_apps.validate_manifest(doc, json.loads((EXAMPLES / 'service-work-orders.manifest.json').read_text()))
    ui_apps.validate(json.loads((EXAMPLES / 'database-clock.json').read_text()))


@pytest.mark.parametrize('height', [160, 179, 961, True, 180.5, '180'])
def test_chart_size_rejected_before_reaching_web_renderer(doc, height):
    chart = next(n for n in doc['components'] if n['component'] == 'CVegaChart')
    chart['height'] = height
    with pytest.raises(ValueError, match='Chart height'):
        ui_apps.validate(doc)


@pytest.mark.parametrize('height', [180, 960])
def test_chart_size_boundaries_match_web_catalog(doc, height):
    next(n for n in doc['components'] if n['component'] == 'CVegaChart')['height'] = height
    ui_apps.validate(doc)


def test_parameters_cannot_turn_into_sql_or_replace_literals(doc):
    doc['data']['state']['filter']['id'] = "'; DROP TABLE calliope_apps.ui_example_jobs; -- $1"
    query = {**doc['queries']['record'], 'sql': "SELECT '$1' AS literal WHERE 'x'=$1"}
    result = ui_apps.bound_sql(query, doc['data'])
    assert len(parse_sql(result)) == 1
    assert "SELECT '$1' AS literal" in result
    assert "''; DROP TABLE" in result


@pytest.mark.parametrize('mutation', ['write', 'multi', 'missing_param', 'state_output', 'cycle', 'missing_event', 'writable_filter', 'prototype', 'remote_chart'])
def test_invalid_definitions_fail_before_publication(doc, mutation):
    if mutation == 'write': doc['queries']['record']['sql'] = 'DELETE FROM calliope_apps.ui_example_jobs WHERE id=$1'
    if mutation == 'multi': doc['queries']['record']['sql'] += '; SELECT 1'
    if mutation == 'missing_param': doc['queries']['record']['params'] = []
    if mutation == 'state_output': doc['queries']['record']['path'] = '/state/filter'
    if mutation == 'cycle': doc['components'][0]['children'].append('root')
    if mutation == 'missing_event': del doc['events']['save_note']
    if mutation == 'writable_filter': doc['events']['filter']['action'] = 'save_note'
    if mutation == 'prototype': doc['data']['state']['__proto__'] = {}
    if mutation == 'remote_chart': doc['data']['charts']['workload']['data'] = {'url':'https://example.com/private'}
    with pytest.raises(ValueError): ui_apps.validate(doc)


def fake_server(doc):
    calls = []
    def query(statement, **kwargs):
        calls.append(statement)
        return {'rows': [{'customer': 'Live record'}], 'row_count': 1}
    return SimpleNamespace(
        _load_live_app_version=lambda slug, version: ({'name':'Test'}, {'kind':'a2ui','version':version or 3,'source_files':{'app.json':json.dumps(doc)}}),
        tool_run_sql=query,
        _execute_app_action_request=lambda slug, name, actor, body: {'committed':True,'version':body['version'],'request_key':body['request_key']},
        calls=calls)


def test_snapshot_sql_and_explicit_version(doc):
    server = fake_server(doc)
    result = ui_apps.snapshot(server,'test','viewer',{'version':2,'state':doc['data']['state']})
    assert result['version'] == 2
    assert result['commands'] == [name for name,event in doc['events'].items() if event.get('action')]
    assert result['forms'] == [{'path':f['path'],'key':f.get('key','id'),'source':f['source']} for f in doc.get('forms',[])]
    assert len(server.calls) == 2
    assert result['messages'][2]['updateDataModel']['value']['record']['customer'] == 'Live record'
    with pytest.raises(ValueError, match='version'):
        ui_apps.snapshot(server,'test','viewer',{'event':'save_note'})


def test_commit_receipt_survives_query_failure(doc):
    server = fake_server(doc)
    server.tool_run_sql = lambda *a, **k: {'error':{'code':'READ_FAILED'}}
    result = ui_apps.snapshot(server,'test','viewer',{'version':2,'event':'save_note','context':{'id':'WO-1001','version':1},'state':doc['data']['state'],'request_key':'test'})
    assert result['receipt']['committed'] is True
    assert result['queries']['record']['error']['code'] == 'READ_FAILED'
    assert result['messages'][2]['updateDataModel']['value']['state']['note'] == ''


def test_route_checks_auth_origin_and_version(doc, monkeypatch):
    import asyncio
    import contextvars
    from concurrent.futures import ThreadPoolExecutor
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.responses import JSONResponse
    import httpx
    import meaning_graph
    monkeypatch.setattr(meaning_graph, 'register', lambda *args: None)
    routes=[]
    class Registry:
        def custom_route(self,path,methods):
            def add(fn):
                routes.append(Route(path,fn,methods=methods))
                return fn
            return add
    server=fake_server(doc)
    server._SESSION_SUB=contextvars.ContextVar('test_actor', default=None)
    pool=ThreadPoolExecutor(max_workers=1)
    server._CONTROL_EXECUTOR=pool
    def authorize(request):
        if request.headers.get('authorization') != 'test':
            return None,None,JSONResponse({'error':{'code':'UNAUTHORIZED'}},401)
        return 'viewer',{},None
    ui_apps.register(Registry(),server,authorize,lambda value,status=200:JSONResponse(value,status))
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=routes)),base_url='https://calliope.test') as client:
            assert (await client.post('/api/apps/example/ui',json={})).status_code == 401
            assert (await client.post('/api/apps/example/ui',headers={'authorization':'test','origin':'https://other.test'},json={})).status_code == 403
            assert (await client.post('/api/apps/example/ui',headers={'authorization':'test'},json={'event':'save_note'})).status_code == 400
            response=await client.post('/api/apps/example/ui',headers={'authorization':'test','origin':'https://calliope.test'},json={})
            assert response.status_code == 200
            assert response.json()['version'] == 3
    try: asyncio.run(check())
    finally: pool.shutdown()


def test_published_artwork_is_a_bundled_enum(doc):
    doc['components'][0]['children'].append('illustration')
    artwork = {'id':'illustration','component':'CArtwork','asset':'neighborhood','size':'banner'}
    doc['components'].append(artwork)
    ui_apps.validate(doc)
    for asset in ('https://example.com/image.png', '../../private.png'):
        artwork['asset'] = asset
        with pytest.raises(ValueError): ui_apps.validate(doc)


def test_generic_chart_route_uses_session_not_artifact_slug(monkeypatch):
    import asyncio
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.responses import JSONResponse
    import httpx
    import ui_chart_render
    import meaning_graph
    monkeypatch.setattr(meaning_graph, 'register', lambda *args: None)
    routes = []
    class Registry:
        def custom_route(self, path, methods):
            def add(fn):
                routes.append(Route(path, fn, methods=methods))
                return fn
            return add
    monkeypatch.setitem(sys.modules, 'auth', SimpleNamespace(read_session_full=lambda r:
        {'identity': 'Viewer', 'mapped': True} if r.headers.get('authorization') == 'test' else None))
    monkeypatch.setattr(ui_chart_render, 'render', lambda actor, body: {'actor': actor})
    with ThreadPoolExecutor(max_workers=2) as pool:
        ui_apps.register(Registry(), SimpleNamespace(_CONTROL_EXECUTOR=pool),
                         lambda r: (None, None, JSONResponse({}, 404)),
                         lambda value, status=200: JSONResponse(value, status))
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=routes)), base_url='https://calliope.test') as client:
                path = '/api/calliope/ui-charts/render'
                assert (await client.post(path, json={})).status_code == 401
                response = await client.post(path, headers={'authorization': 'test'}, json={})
                assert response.status_code == 200
                assert response.json()['actor'] == 'viewer'
        asyncio.run(check())


@pytest.mark.parametrize('height', [0, -1, 1001, '480', True])
def test_scroll_regions_require_bounded_geometry(doc, height):
    children=doc['components'][0]['children']
    child=children[0]
    children[0]='scroll'
    doc['components'].append({'id':'scroll','component':'CScrollArea','child':child,'label':'Records','height':height})
    with pytest.raises(ValueError, match='Scroll height'): ui_apps.validate(doc)


def test_available_scroll_regions_validate_the_minimum(doc):
    children=doc['components'][0]['children']
    child=children[0]
    children[0]='scroll'
    node={'id':'scroll','component':'CScrollArea','child':child,'label':'Records','height':'available','minHeight':200}
    doc['components'].append(node)
    ui_apps.validate(doc)
    for invalid in (0, 601, '200', True):
        node['minHeight']=invalid
        with pytest.raises(ValueError, match='Minimum scroll height'): ui_apps.validate(doc)


def test_record_drafts_hydrate_before_dependent_sql_and_respect_resets():
    doc = {'forms':[{'path':'/state/form','source':'/record','key':'id'}]}
    data = {'state':{'form':{'id':'B','notes':'B work'}},'record':{'id':'A','notes':'server'}}
    hints = [{'path':'/state/form','value':{'id':'A','notes':'A work'}}]
    ui_apps.hydrate(doc,data,False,[], '/record',hints)
    assert data['state']['form']==hints[0]['value']
    data['state']['form']['notes']='latest active edit'
    ui_apps.hydrate(doc,data,False,[], '/record',hints)
    assert data['state']['form']['notes']=='latest active edit'
    ui_apps.hydrate(doc,data,False,['/state/form'], '/record',hints)
    assert data['state']['form']==data['record']
    assert hints[0]['value']['notes']=='A work'


def test_clean_native_form_refreshes_but_legacy_contract_still_preserves(doc):
    form = {'forms':[{'path':'/state/form','source':'/record','key':'id'}]}
    data = {'state':{'form':{'id':'A','notes':'old'}},'record':{'id':'A','notes':'new'}}
    ui_apps.hydrate(form,data,False,[])
    assert data['state']['form']['notes']=='old'
    ui_apps.hydrate(form,data,False,[],drafts=[])
    assert data['state']['form']['notes']=='new'


@pytest.mark.parametrize('drafts', [{}, [{'path':'/not-a-form','value':{}}], [{'path':'/state/form','value':{'id':{}}}]])
def test_bad_draft_hints_are_rejected_before_sql(doc, drafts):
    server=fake_server(doc)
    with pytest.raises(ValueError): ui_apps.snapshot(server,'test','viewer',{'drafts':drafts})
    assert not server.calls


def test_navigation_does_not_require_republishing_old_definitions():
    doc=json.loads((EXAMPLES/'demos/workspace.json').read_text())
    declared=ui_apps.navigation(doc)['paths']
    doc.pop('navigation')
    inferred=ui_apps.navigation(doc)
    assert set(declared)==set(inferred['paths'])
    assert 'select_job' in inferred['events']
    assert 'save_job' not in inferred['events']
    assert 'discard' not in inferred['events']


def test_revisited_draft_is_used_by_dependent_sql_in_the_same_snapshot():
    doc={'schema':'calliope.app.v1','title':'Draft dependencies',
         'components':[{'id':'root','component':'CText','text':{'path':'/decision/text'}}],
         'data':{'state':{'selected':'A','form':{'id':'B','notes':'B draft'}},'record':{'id':'','notes':''},'decision':{'text':''}},
         'forms':[{'path':'/state/form','source':'/record','key':'id'}],
         'queries':{'record':{'sql':'SELECT $1 AS id', 'params':[{'path':'/state/selected'}],'path':'/record','shape':'first'},
                    'decision':{'sql':'SELECT $1 AS text','params':[{'path':'/state/form/notes'}],'path':'/decision','shape':'first','dependsOn':['record']}},
         'events':{}}
    server=fake_server(doc)
    def query(statement,**kwargs):
        server.calls.append(statement)
        return {'rows':[{'id':'A','notes':'server value'}] if len(server.calls)==1 else [{'text':'evaluated'}], 'row_count':1}
    server.tool_run_sql=query
    result=ui_apps.snapshot(server,'test','viewer',{'version':1,'state':doc['data']['state'], 'drafts':[{'path':'/state/form','value':{'id':'A','notes':'restored A draft'}}]})
    assert 'restored A draft' in server.calls[1]
    assert result['messages'][2]['updateDataModel']['value']['state']['form']['notes']=='restored A draft'


@pytest.fixture(autouse=True)
def durable_renderer_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("WAREHOUSE_CALLIOPE_DIR", str(tmp_path / "server-files"))


@pytest.mark.parametrize('href', ['//evil.test', '/\\evil.test', 'javascript:alert(1)',
    'file:///tmp/x', 'http://elsewhere.test', 'https://name:secret@site.test', '/x\ny', 'https:evil.test'])
def test_browser_links_reject_executable_or_ambiguous_destinations(href):
    with pytest.raises(ValueError): ui_apps.validate_link(href)


def test_browser_links_accept_instance_paths_and_bound_https():
    for href in ['', '/d/visits?job=HV-1001', 'https://docs.example.test/help']:
        doc = {'schema': 'calliope.app.v1', 'data': {'state': {}, 'url': href},
            'components': [{'id': 'root', 'component': 'CLink', 'label': 'Analysis', 'href': {'path': '/url'}}]}
        ui_apps.validate(doc)
