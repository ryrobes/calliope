"""SQL map publication/repair boundaries and the real vendored Mapbox renderer."""
import asyncio
import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest
import calliope
import server
import stage_maps
from test_generated_image_delivery import db, seed, authorize
from test_stage_charts import feedback_db
from test_zen_chat_layout import zen_page
from test_stage_packing import prepare, geometry, no_overlaps

ROOT=Path(__file__).parent
QUERIES={'places':'select lon,lat,name from places'}
SOURCES={'places':{'dataset':'places','longitude':'lon','latitude':'lat','tooltip':['name']}}
LAYERS=[{'id':'places','source':'places','type':'circle','paint':{'circle-radius':12,'circle-color':'@accent'}}]


def payload(**changes):
    return stage_maps.validate(**{**dict(title='Places',queries=QUERIES,sources=SOURCES,layers=LAYERS),**changes})


def test_contract_and_geometry_never_turn_missing_coordinates_into_zero():
    p=payload()
    fc,stats=stage_maps.resolve_sources(p,{'places':[{'lon':0,'lat':0},{'lon':'-73.9','lat':'40.7'},
        {'lon':None,'lat':1},{'lon':'','lat':1},{'lon':True,'lat':1},{'lon':181,'lat':1},{'lon':1,'lat':'NaN'}]})
    assert len(fc['places']['features'])==2
    assert stats['places']['invalid_geometry_rows']==5
    with pytest.raises(ValueError,match='none of'):
        stage_maps.resolve_sources(p,{'places':[{'lon':None,'lat':None}]})
    assert stage_maps.resolve_sources(p,{'places':[]})[1]['places']['features']==0
    polygon={'type':'Polygon','coordinates':[[[0,0],[1,0],[1,1],[0,0]]]}
    assert stage_maps.geometry(json.dumps(polygon))==polygon
    assert stage_maps.geometry({'type':'Polygon','coordinates':[[[0,0],[1,0],[1,1]]]}) is None
    assert stage_maps.geometry({'type':'LineString','coordinates':[[0,0],[1,1]]})
    for bad in [dict(height='100%'),dict(height=100),dict(size='50%'),dict(row_limit=100000),
                dict(sources={'x':{'url':'https://private'}}),dict(layers=[{'id':'x','type':'custom','source':'places'}]),
                dict(layers=LAYERS*2),dict(viewport={'center':[None,1]}),dict(viewport={'zoom':True}),
                dict(sources={'places':{**SOURCES['places'],'tooltip':'<b>name</b>'}})]:
        with pytest.raises((ValueError,TypeError)):payload(**bad)


def test_publish_update_feedback_retry_and_scope(feedback_db,monkeypatch):
    db=feedback_db;dest=seed(db)
    monkeypatch.setattr(server,'_conn',db);monkeypatch.setattr(server,'MAPBOX_TOKEN','pk.test')
    monkeypatch.setattr(server,'tool_validate_sql',lambda sql:{'valid':True,'safe_select':not sql.lower().startswith('delete')})
    token=authorize(monkeypatch)
    def publish(**kwargs):
        return server.tool_publish_map(**{**dict(title='Places',queries=QUERIES,sources=SOURCES,layers=LAYERS,caller_email='owner@example.com'),**kwargs})
    try:
        first=publish();assert 'error' not in first,first
        sid=first['surface_id'];assert publish()['surface_id']==sid
        assert 'error' in publish(queries={'places':'delete from places'})
        assert 'error' in server.tool_read_map(sid,'stranger@example.com')
        attempt=stage_maps.begin_render(db,sid,'owner@example.com')
        report=dict(attempt=attempt,sequence=1,status='error',phase='render',message='Bad chart input in map layer')
        assert stage_maps.record_render(db,sid,'stranger@example.com',report)['reason']=='not_owner'
        assert stage_maps.record_render(db,sid,'owner@example.com',report)['feedback']=='queued'
        assert stage_maps.record_render(db,sid,'owner@example.com',report)['reason']=='stale_report'
        read=server.tool_read_map(sid,'owner@example.com');assert read['last_render_error']['message']==report['message']
        with db() as conn:
            note=conn.execute('select * from rvbbit.calliope_agent_messages').fetchone()
            assert 'read_map' in note['body'] and report['message'] in note['body']
            assert note['payload']['map_surface_id']==sid
        second=server.tool_update_map(sid,'owner@example.com',title='Corrected places')
        assert second['surface_id']!=sid,second
        assert 'error' in server.tool_update_map(sid,'owner@example.com',title='Stale overwrite')
        third=server.tool_update_map(second['surface_id'],'owner@example.com',title='Places')
        assert len({sid,second['surface_id'],third['surface_id']})==3 # A -> B -> A remains a new revision
        streamed=calliope._insert_progressive_result_surfaces(db,None,'owner@example.com',dest['session_id'],dest['turn_id'],'mcp__Warehouse__update_map',third,{},'update')
        assert len(streamed)==1 and streamed[0]['kind']=='map'
        with db() as conn:
            assert conn.execute('select status from rvbbit.calliope_agent_messages').fetchone()['status']=='cancelled'
            assert conn.execute("select count(*) n from rvbbit.calliope_surfaces where kind='map'").fetchone()['n']==3
        other=str(uuid4())
        with db() as conn:
            conn.execute("INSERT INTO rvbbit.calliope_sessions VALUES(%s,'owner@example.com','other')",(other,))
            conn.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status) VALUES(%s,%s,'running')",(other,other))
        foreign=stage_maps.publish(db,dict(session_id=other,turn_id=other),'Other',payload())
        assert 'error' in publish(replaces_surface_id=foreign['surface_id'])
    finally:server._AUTHORIZATION_CONTEXT.reset(token)
    token=authorize(monkeypatch,trusted=False)
    try:assert publish()['error']['code']=='NO_STAGE_CONTEXT'
    finally:server._AUTHORIZATION_CONTEXT.reset(token)


def test_map_queries_use_viewer_scoped_transport_and_return_geometry_only(monkeypatch):
    import concurrent.futures
    viewers=[]
    monkeypatch.setattr(server,'_record',lambda *a,**k:None)
    def query(sql,**kwargs):
        viewers.append(server._SESSION_SUB.get());return {'rows':[{'lon':0,'lat':0,'name':'Origin'}],'row_count':1,'truncated':False}
    def submit(key,fn,refresh=False):
        f=concurrent.futures.Future();f.set_result(fn());return f,'miss'
    monkeypatch.setattr(server,'tool_run_sql',query);monkeypatch.setattr(server,'_dashboard_query_submit',submit)
    result=asyncio.run(server.stage_map_data({'id':str(uuid4()),'payload':payload()},'viewer@example.com'))
    assert viewers==['viewer@example.com']
    assert 'datasets' not in result
    assert result['sources']['places']['features'][0]['properties']['name']=='Origin'


def test_map_tools_registered_and_parameterized():
    from mcp.server.fastmcp import FastMCP
    mcp=FastMCP('maps');server._register(mcp)
    tools=mcp._tool_manager._tools
    assert set(tools['publish_map'].parameters['required'])=={'title','queries','sources','layers'}
    assert tools['read_map'].parameters['required']==['surface_id']
    assert tools['update_map'].parameters['required']==['surface_id']
    assert calliope._canonical_tool('mcp__Warehouse__publish_map')=='publish_map'


def test_actual_mapbox_renderer_packing_camera_and_safe_popups(zen_page):
    page=zen_page;prepare(page,1000)
    calls=[];reports=[]
    render_payload=payload(queries={**QUERIES,'shapes':'select geometry'},
        sources={**SOURCES,'shapes':{'dataset':'shapes','geometry':'geometry'}},
        layers=[{'id':'regions','type':'fill','source':'shapes'},
                {'id':'routes','type':'line','source':'shapes'},
                {'id':'density','type':'heatmap','source':'places'},*LAYERS])
    fc,stats=stage_maps.resolve_sources(render_payload,{
        'places':[{'lon':-74,'lat':40.7,'name':'<img src=x onerror=alert(1)>'},{'lon':-73.95,'lat':40.73,'name':'Second'}],
        'shapes':[{'geometry':{'type':'Polygon','coordinates':[[[-74.02,40.68],[-73.93,40.68],[-73.93,40.75],[-74.02,40.68]]]}},
                  {'geometry':{'type':'LineString','coordinates':[[-74,40.7],[-73.95,40.73]]}}]})
    style={'version':8,'sources':{},'layers':[{'id':'background','type':'background','paint':{'background-color':'#172329'}}]}
    def data(r):
        calls.append(r.request.post_data_json)
        r.fulfill(json={'sources':fc,'source_stats':stats,'results':{'places':{'truncated':False}},'render_attempt':'attempt',
            'runtime':{'enabled':True,'token':'pk.test','styles':{'dark':style,'light':style},'gl_js_url':'/vendor/mapbox-gl@3.15.0/mapbox-gl.js','gl_css_url':'/vendor/mapbox-gl@3.15.0/mapbox-gl.css'}})
    page.route('**/map-data',data)
    page.route('**/map-render',lambda r:(reports.append(r.request.post_data_json),r.fulfill(json={'accepted':True})))
    page.route('https://events.mapbox.com/**',lambda r:r.fulfill(status=204))
    page.add_script_tag(path=str(ROOT/'vendor/mapbox-gl@3.15.0/mapbox-gl.js'))
    page.add_style_tag(path=str(ROOT/'vendor/mapbox-gl@3.15.0/mapbox-gl.css'))
    page.evaluate('''() => {const Base=mapboxgl.Map;window.testMaps=[];mapboxgl.Map=class extends Base{constructor(o){super(o);testMaps.push(this)}};qa.setupEvents();}''')
    surface={'id':'map','kind':'map','turn_id':'one','ordinal':1,'title':'Customer locations','lineage_key':'map','payload':render_payload,'created_at':'2026-09-13T12:00:00Z'}
    page.evaluate('(s)=>{qa.state.surfaces=[s];qa.renderStage()}',surface)
    page.wait_for_function("document.querySelector('.stage-map').classList.contains('is-ready')",timeout=25000)
    assert len(calls)==1 and any(r['status']=='ready' for r in reports)
    assert page.locator('.mapboxgl-canvas').count()==1
    assert page.evaluate("testMaps.at(-1).queryRenderedFeatures({layers:['rvbbit-places']}).length")>=2
    assert page.evaluate("['rvbbit-regions','rvbbit-routes','rvbbit-density'].every(id=>testMaps[0].getLayer(id))")
    assert abs(page.locator('.kind-map').evaluate('(n)=>n.offsetWidth')-page.locator('.zen-packed').evaluate('(n)=>n.clientWidth'))<=1
    no_overlaps(geometry(page))
    page.evaluate("testMaps.at(-1).jumpTo({center:[-74,40.7],zoom:12})")
    page.evaluate("qa.renderStage(); document.body.style.setProperty('--test-stage-width','650px')")
    page.wait_for_timeout(350)
    assert page.evaluate('testMaps.length')==1
    assert page.evaluate('testMaps[0].getZoom()')==12
    page.evaluate("testMaps[0].fire('click',{point:testMaps[0].project([-74,40.7]),lngLat:{lng:-74,lat:40.7}})")
    assert page.locator('.stage-map-tooltip img').count()==0
    assert '<img src=x' in page.locator('.stage-map-tooltip').inner_text()
    # Map gestures must not toggle Stage pinning.
    page.locator('.stage-map-canvas').click(position={'x':40,'y':50})
    assert page.evaluate('qa.state.selectedSurfaceId') is None
    page.get_by_role('button',name='Refresh',exact=True).click()
    page.wait_for_function('testMaps.length===2 && document.querySelector(".stage-map").classList.contains("is-ready")')
    assert len(calls)==2 and calls[-1]['refresh'] is True
    assert page.evaluate('testMaps[1].getZoom()')==12
    page.screenshot(path='/tmp/calliope-stage-map.png')
    page.evaluate('''async () => {const m=testMaps.at(-1);await new Promise(resolve=>{m.once('idle',resolve);document.documentElement.dataset.theme='light'});}''')
    assert page.evaluate('testMaps.length')==2
    assert page.evaluate('testMaps[1].getZoom()')==12
    assert page.evaluate("Boolean(testMaps[1].getLayer('rvbbit-places'))")
    # Native Mapbox validation failures become read_map diagnostics; a new
    # immutable revision replaces the visible failed tile.
    bad=copy.deepcopy(surface);bad['id']='bad-map';bad['ordinal']=2;bad['created_at']='2026-09-13T12:01:00Z'
    bad['payload']['layers'][-1]['paint']['circle-radius']='invalid radius'
    page.evaluate('(s)=>{qa.state.surfaces.push(s);qa.renderStage()}',bad)
    page.wait_for_selector('.stage-map.is-error')
    page.wait_for_timeout(100)
    assert any(r['status']=='error' and 'circle-radius' in r['message'] for r in reports)
    assert page.locator('.kind-map').count()==1
    good=copy.deepcopy(surface);good['id']='fixed-map';good['ordinal']=3;good['created_at']='2026-09-13T12:02:00Z'
    page.evaluate('(s)=>{qa.state.surfaces.push(s);qa.renderStage()}',good)
    page.wait_for_selector('.stage-map.is-ready')
    assert page.locator('.kind-map').count()==1
    queries_before=len(calls)
    maps_before=page.evaluate('testMaps.length')
    page.evaluate('''() => {testMaps.at(-1).jumpTo({center:[-74,40.7],zoom:10});
      qa.els.stage.insertAdjacentHTML('beforeend','<div id="map-test-space" style="height:2400px"></div>');qa.els.stageScroll.scrollTop=1500;}''')
    page.wait_for_function('testMaps.at(-1)._removed')
    page.evaluate('qa.els.stageScroll.scrollTop=0')
    page.wait_for_function('(n)=>testMaps.length>n && document.querySelector(".stage-map").classList.contains("is-ready")',arg=maps_before)
    assert len(calls)==queries_before
    assert page.evaluate('testMaps.at(-1).getZoom()')==10
    page.evaluate('qa.state.surfaces=[];qa.renderStage()')
    page.wait_for_timeout(100)
    assert page.evaluate('testMaps.every(m=>m._removed)')


def test_map_http_query_failure_is_saved_and_browser_report_requires_owner(feedback_db, monkeypatch):
    import ast,asyncio,uuid,calliope
    from starlette.responses import Response
    db=feedback_db
    destination=seed(db)
    sid=str(uuid.uuid4())
    with db() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_surfaces(id,session_id,turn_id,kind,lineage_key,payload) VALUES(%s,%s,%s,'map','query-fail',%s::jsonb)",
                     (sid,destination['session_id'],destination['turn_id'],json.dumps({'queries':{'sales':'select bad'},'live':True})))
    async def load(*args,**kwargs):raise ValueError('sales: missing relation')
    monkeypatch.setattr(server,'stage_map_data',load)
    tree=ast.parse((ROOT/'calliope.py').read_text())
    namespace={'api_owner':lambda request:(request.owner,None),'_uuid':calliope._uuid,'Response':Response,
               'conn_factory':db,'asyncio':asyncio,'json':json,'os':__import__('os'),
               'json_response':lambda value,status=200:Response(json.dumps(value),status_code=status)}
    for name in ('get_stage_map_data','report_stage_map_render'):
        fn=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
        fn.decorator_list=[]
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'<map-route>','exec'),namespace)
    class Request:
        owner='owner@example.com'
        path_params={'surface_id':sid}
        body={}
        async def json(self):return self.body
    request=Request()
    response=asyncio.run(namespace['get_stage_map_data'](request))
    assert response.status_code==400
    with db() as conn:
        payload=conn.execute('select payload from rvbbit.calliope_surfaces where id=%s',(sid,)).fetchone()['payload']
        assert payload['render_receipt']['phase']=='query'
        assert payload['render_receipt']['message']=='sales: missing relation'
        assert conn.execute('select count(*) AS n from rvbbit.calliope_agent_messages').fetchone()['n']==1
    request.body={'status':'ready','phase':'render','sequence':1,'attempt':payload['render_attempt']}
    request.owner='stranger@example.com'
    assert asyncio.run(namespace['report_stage_map_render'](request)).status_code==403
    request.owner='owner@example.com'
    assert asyncio.run(namespace['report_stage_map_render'](request)).status_code==200
    with db() as conn:
        assert conn.execute('select status from rvbbit.calliope_agent_messages').fetchone()['status']=='cancelled'
    request.body={'status':'ready','phase':'render','sequence':True}
    assert asyncio.run(namespace['report_stage_map_render'](request)).status_code==400


def test_map_script_route_serves_authenticated_cache_versioned_asset():
    import ast
    from starlette.responses import Response
    tree=ast.parse((ROOT/'calliope.py').read_text())
    fn=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='calliope_stage_maps_js')
    assert ast.literal_eval(fn.decorator_list[0].args[0])=='/calliope/stage-maps.js'
    fn.decorator_list=[]
    namespace={'_canonical_owner':lambda r:(r,None),'Response':Response,
        'browser_asset':lambda request,name,mime:Response((ROOT/'calliope'/name).read_bytes(),media_type=mime)}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'<map-asset-route>','exec'),namespace)
    assert asyncio.run(namespace['calliope_stage_maps_js'](None)).status_code==401
    response=asyncio.run(namespace['calliope_stage_maps_js']('viewer@example.com'))
    assert response.status_code==200 and response.body==(ROOT/'calliope/stage-maps.js').read_bytes()
    assert 'stage-maps.js' in calliope._CACHE_VERSIONED_ASSETS
