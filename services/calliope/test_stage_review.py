"""Regression coverage for composable receipts and private visual feedback."""
import asyncio
import json
from decimal import Decimal
from uuid import uuid4
from pathlib import Path
import pytest
import calliope
import server
import stage_capture
import stage_references
from test_generated_image_delivery import db, seed
from test_zen_chat_layout import zen_page


def test_batch_references_survive_live_final_and_specialist_projection(db):
    dest = seed(db)
    good = {'columns': [{'name': 'n', 'type': 'int4'}], 'rows': [{'n': 7}], 'row_count': 1}
    result = {'results': {'first': good.copy(), 'second': good.copy(), 'broken': {'error': 'bad sql'}}}
    args = {'queries': {'first': 'select 7 n', 'second': 'select 7 n', 'broken': 'select broken'}}
    stage_references.publish(db, dest, 'run_sql_multi', args, result)
    refs = result['stage_refs']
    assert len(refs) == 2 and refs[0]['surface_id'] != refs[1]['surface_id']
    assert result['results']['first']['surface_id'] == refs[0]['surface_id']
    assert 'surface_id' not in result['results']['broken']
    for project in [
        calliope._project_progressive_sql_result('run_sql_multi', result, args, 'real-call'),
        calliope._project_progressive_specialist_result('run_sql_multi', result, args, 'real-call',
            specialist_key='analyst', specialist_run_id=str(uuid4()), specialist_thread_id=str(uuid4()), specialist_round=1),
        calliope.project_messages([
            {'role':'assistant','tool_calls':[{'id':'real-call','function':{'name':'run_sql_multi','arguments':json.dumps(args)}}]},
            {'role':'tool','tool_name':'run_sql_multi','tool_call_id':'real-call','content':json.dumps(result)}])]:
        assert len(project) == 2
        assert {r['tool_call_id'] for r in project} == {result['_stage_call_id']}
        calliope._insert_surfaces(db, dest['session_id'], dest['turn_id'], project)
    rows = calliope._insert_progressive_result_surfaces(db, None, 'owner@example.com', dest['session_id'], dest['turn_id'],
                                                      'run_sql_multi', result, args, 'real-call')
    assert {r['id'] for r in rows} == {r['surface_id'] for r in refs}
    with db() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_surfaces').fetchone()['n'] == 2


def test_capture_authorizes_parent_and_scopes_members(db):
    dest = seed(db)
    with db() as conn:
        conn.execute("CREATE FUNCTION rvbbit.calliope_session_can_view(uuid,text,boolean) RETURNS boolean LANGUAGE sql AS 'SELECT $2=''owner@example.com'''")
    q = {'columns':[{'name':'n'}], 'rows':[{'n':1}]}
    stage_references.publish(db, dest, 'run_sql', {'sql':'select 1 n'}, q)
    import stage_boards
    b = stage_boards.publish(db, dest, 'Check', [{'ref':q['surface_id']}])
    target, members = stage_capture.load(db, b['surface_id'], 'owner@example.com')
    assert [m['id'] for m in members] == [q['surface_id']]
    with pytest.raises(LookupError): stage_capture.load(db, b['surface_id'], 'outsider@example.com')
    found = stage_references.lookup(db, dest['session_id'], 'owner@example.com', 'Check')
    assert found['surfaces'][0]['surface_id'] == b['surface_id']
    assert stage_references.lookup(db, dest['session_id'], 'outsider@example.com')['surfaces'] == []


def test_real_stage_capture_renders_chart_query_and_tall_board(monkeypatch, tmp_path):
    monkeypatch.setattr(server, '_launch_playwright_chromium', lambda p:p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox']))
    async def data(surface, owner, refresh=False):
        assert owner == 'owner@example.com'
        return {'datasets':{'sales':[{'name':'Space Coast','sales':Decimal('24.50')}]}, 'results':{'sales':{'row_count':1}}}
    monkeypatch.setattr(server, 'stage_chart_data', data)
    chart = {'id':str(uuid4()), 'session_id':str(uuid4()), 'kind':'chart', 'title':'Sales',
             'payload':{'queries':{'sales':'select 24 sales'},'runtime':{'scripts':calliope._chart_runtime_scripts()},
                        'spec':{'data':{'name':'sales'},'mark':'bar','encoding':{'x':{'field':'name','type':'nominal'},'y':{'field':'sales','type':'quantitative'}}}}}
    query = {**chart,'id':str(uuid4()),'kind':'query','payload':{'columns':[{'name':'sales','type':'int4'}],'rows':[{'sales':24}]}}
    board = {**chart,'id':str(uuid4()),'kind':'board','payload':{'members':[chart['id'],query['id']], 'cells':[
        {'kind':'text','heading':'Owners brief','span':12},
        {'kind':'surface','ref':chart['id'],'span':12},
        *[{'kind':'surface','ref':query['id'],'span':12,'title':f'Table {i}'} for i in range(5)]]}}
    result = stage_capture.render(board, [chart,query], 'owner@example.com', tmp_path, timeout_ms=10000)
    assert result['status'] == 'ready', result
    assert result['height'] > 1400 and len(result['paths']) > 2
    assert result['charts'][chart['id']]['status'] == 'ready'
    assert all(Path(p).is_file() for p in result['paths'])
    async def broken(*a,**k): raise ValueError('query failed for test')
    monkeypatch.setattr(server, 'stage_chart_data', broken)
    result = stage_capture.render(chart, [], 'owner@example.com', tmp_path, timeout_ms=10000)
    assert result['status'] == 'error' and any('query failed for test' in e for e in result['errors'])


def test_publish_capture_failure_preserves_publication_and_deferred_skips_capture(monkeypatch):
    monkeypatch.setattr(server,'_caller',lambda:('owner@example.com','client'))
    monkeypatch.setattr(server,'_logged',lambda tool,args,thunk:thunk())
    def fail(*a):raise RuntimeError('browser unavailable')
    monkeypatch.setattr(server,'_stage_capture_result',fail)
    result = asyncio.run(server._stage_publish_logged('publish_chart', {}, lambda:{'surface_id':'saved'}))
    assert result['surface_id'] == 'saved' and result['visual_review']['status'] == 'unavailable'
    result = asyncio.run(server._stage_publish_logged('publish_chart', {}, lambda:{'surface_id':'saved'}, defer=True))
    assert result['visual_review']['status'] == 'deferred'


def test_board_review_and_capture_ask_as_the_person_not_the_hermes_token(monkeypatch):
    # Through Hermes the authenticated caller is the service token; the person
    # the turn acts for is known only once the authorization context is set, as
    # _logged does. Asked as the service, a capture found "no chart or board".
    from types import SimpleNamespace
    monkeypatch.setattr(server, '_authenticated_caller',
                        lambda: ('calliope@example.com', server._HERMES_SERVICE_CLIENT_ID))
    monkeypatch.setattr(server, '_forwarded_mcp_caller', lambda *a, **k: None)
    monkeypatch.setattr(server, '_application_authorization_context',
                        lambda *a, **k: SimpleNamespace(subject='person@example.com',
                                                        attributed_subject='person@example.com'))
    monkeypatch.setattr(server, '_record', lambda *a, **k: None)
    monkeypatch.setattr(server, '_hermes_mcp_envelope', lambda: {'session_id': 'hermes-session'})
    assert server._caller()[0] == 'calliope@example.com', 'outside the context the token is the caller'
    asked = []
    monkeypatch.setattr(server, '_stage_capture_result',
                        lambda sid, owner, *a: asked.append(('review', owner)) or {'status': 'ready', 'paths': []})
    result = asyncio.run(server._stage_publish_logged('publish_board', {}, lambda: {'surface_id': 'board'}))
    assert result['visual_review']['status'] == 'ready'
    monkeypatch.setattr(server, '_image_stage_destination',
                        lambda owner: asked.append(('destination', owner)) or {'session_id': 'chat'})
    monkeypatch.setattr(stage_capture, 'load',
                        lambda conn, sid, owner: asked.append(('load', owner)) or ({'session_id': 'chat'}, []))
    asyncio.run(server._mcp_capture_stage_surface('board'))
    assert asked == [('review', 'person@example.com'), ('destination', 'person@example.com'),
                     ('load', 'person@example.com'), ('review', 'person@example.com')]


def test_review_images_remain_private_even_after_vision_read(tmp_path):
    image = tmp_path / 'cache/images/board-review.jpg'
    image.parent.mkdir(parents=True)
    image.write_bytes(b'example')
    config = calliope.CalliopeConfig(hermes_url='http://hermes', hermes_api_key='test', memory_key='test',
                                   file_root=tmp_path/'stage', export_roots=(tmp_path,), max_image_bytes=1024*1024)
    transcript = [
        {'role':'assistant','tool_calls':[{'id':'review','function':{'name':'publish_board','arguments':'{}'}}]},
        {'role':'tool','tool_call_id':'review','content':json.dumps({'visual_review':{'status':'ready'}})+'\nMEDIA:'+str(image)},
        {'role':'assistant','tool_calls':[{'id':'vision','function':{'name':'vision_analyze','arguments':json.dumps({'image':str(image)})}}]},
        {'role':'tool','tool_call_id':'vision','content':'The board is readable.'}]
    assert calliope._publishable_local_paths(transcript, 'Board ready.', config) == []
    assert calliope._publishable_local_paths(transcript, 'MEDIA:'+str(image), config) == [str(image)]


def test_summary_results_do_not_masquerade_as_saved_full_tables(db):
    dest = seed(db)
    result = {'result_mode':'summary','results':{'sales':{'columns':[{'name':'n'}], 'preview':[{'n':7}], 'row_count':40}}}
    stage_references.publish(db, dest, 'run_sql_multi', {'queries':{'sales':'select 7 n'}}, result)
    assert 'stage_refs' not in result and 'validation only' in result['stage_reference_note']
    with db() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_surfaces').fetchone()['n'] == 0


def test_mcp_publish_returns_real_image_content(monkeypatch, tmp_path):
    from PIL import Image
    from mcp.server.fastmcp import FastMCP
    image = tmp_path/'review.png'
    Image.new('RGB',(20,20)).save(image)
    monkeypatch.setattr(server,'_caller',lambda:('owner@example.com','client'))
    monkeypatch.setattr(server,'_logged',lambda tool,args,thunk:thunk())
    monkeypatch.setattr(server,'_stage_capture_result',lambda *a:{'status':'ready','paths':[str(image)]})
    async def publish():
        return await server._stage_publish_logged('publish_chart',{},lambda:{'surface_id':'saved'})
    mcp = FastMCP('stage-review-test')
    mcp.tool()(publish)
    content = asyncio.run(mcp.call_tool('publish', {}))
    assert any(getattr(item,'type',None)=='image' for item in content), content
    text = next(item.text for item in content if getattr(item,'type',None)=='text')
    assert calliope._extract_json(text)['surface_id'] == 'saved'


def test_pinned_artifact_is_live_exact_version_inside_evidence(zen_page):
    page=zen_page
    page.route('**/calliope/artifacts/owners-brief/versions/3?*', lambda r:r.fulfill(content_type='text/html',body='<html><body style="margin:0;background:#135;color:white"><h1>Live dashboard</h1></body></html>'))
    page.evaluate('''() => {
      qa.state.surfaces=[{id:'bundle',kind:'evidence',title:'Evidence',turn_id:'one',payload:{items:[
        {id:'artifact:owners-brief:v3',kind:'artifact',group:'artifacts',title:'Owners brief',summary:'Keep this evidence description',
         handle:{slug:'owners-brief',version:3},thumbnail_url:'/thumbs/old.png'},
        {id:'table:invoices',kind:'db_table',group:'data',title:'Invoices',summary:'A related table'}]}}];
      qa.renderStage(true);
    }''')
    frame=page.locator('[data-evidence-live-preview] iframe')
    frame.wait_for()
    assert '/versions/3?' in frame.get_attribute('data-artifact-src')
    assert 'embed=1' in frame.get_attribute('data-artifact-src')
    frame.scroll_into_view_if_needed()
    page.wait_for_function("document.querySelector('[data-evidence-live-preview] iframe').dataset.artifactActive==='true'")
    assert page.get_by_text('Keep this evidence description').count()==1
    assert page.locator('.evidence-card[data-evidence-kind="db_table"]').count()==1
    preview=page.locator('[data-evidence-live-preview]').bounding_box()
    assert preview['height'] < 600
