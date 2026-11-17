"""Shared Stage search: permissions, semantic decisions, and real UI navigation."""
import asyncio
import json
from types import SimpleNamespace
import uuid

import pytest
from starlette.responses import JSONResponse
import stage_search as search
from test_zen_chat_layout import zen_page
from test_stage_canvas import prepare, mode

SID = str(uuid.uuid4())


class DB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.allowed = True

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if 'calliope_session_role' in sql:
            return SimpleNamespace(fetchone=lambda: {'id': SID, 'access_role': 'viewer'} if self.allowed else None)
        return SimpleNamespace(fetchall=lambda: self.rows)


def route_for(db, monkeypatch, judge):
    routes = {}
    mcp = SimpleNamespace(custom_route=lambda path, methods: lambda fn: routes.setdefault(path, fn))
    monkeypatch.setattr(search, 'judge_batch', judge)
    search.register(mcp, lambda: db, lambda r: ('viewer@example.com', None), JSONResponse,
                    lambda factory, owner, row, cache: row if row['id'] != 'private-playbook' else None)
    return next(iter(routes.values()))


def request(query='places where reports overlap', headers=None, session_id=SID):
    async def body(): return json.dumps({'query': query}).encode()
    return SimpleNamespace(body=body, path_params={'session_id': session_id},
                           headers=headers or {'x-calliope-stage': '1'})


def test_only_authorized_visible_descriptors_reach_semantic_search(monkeypatch):
    db=DB([{'id':'geo','kind':'query','title':'Geographic concordance',
            'payload':{'columns':['county'],'rows':[['private-row']], 'html':'private-body'},
            'source':{'args':{'password':'private-credential'}}},
           {'id':'private-playbook','kind':'playbook','title':'Separately private'}])
    seen=[]
    def judge(factory,owner,session,query,candidates):
        seen.extend(candidates)
        assert owner=='viewer@example.com' and session==SID
        return [{'id':candidates[0]['id'],'score':.92}]
    route=route_for(db,monkeypatch,judge)
    result=asyncio.run(route(request()))
    assert result.status_code==200
    assert json.loads(result.body)=={'matches':[{'id':'geo','score':.92}], 'checked':1,'total':1,'partial':False}
    assert len(seen)==1 and seen[0]['columns']==['county']
    assert 'private-' not in json.dumps(seen)
    assert all(sql.startswith('SELECT') for sql,_ in db.calls)
    db.allowed=False
    assert asyncio.run(route(request())).status_code==404
    assert len(seen)==1


def test_malformed_cross_origin_and_revoked_search_do_not_return_results(monkeypatch):
    db=DB([{'id':'one','title':'A saved result','kind':'answer'}])
    def judge(*args):
        db.allowed=False
        return [{'id':'one','score':.95}]
    route=route_for(db,monkeypatch,judge)
    assert asyncio.run(route(request(headers={'x-calliope-stage':'1','origin':'https://evil.test','host':'lab.test'}))).status_code==403
    assert asyncio.run(route(request(query=''))).status_code==400
    assert asyncio.run(route(request(session_id='invalid'))).status_code==400
    assert not db.calls
    assert asyncio.run(route(request())).status_code==404


def test_failed_batches_are_reported_as_partial(monkeypatch):
    db=DB([{'id':str(i),'kind':'query','title':str(i)} for i in range(35)])
    def judge(factory,owner,session,query,candidates):
        if candidates[0]['id']!='32': raise TimeoutError()
        return [{'id':'34','score':.9}]
    result=json.loads(asyncio.run(route_for(db,monkeypatch,judge)(request())).body)
    assert result=={'matches':[{'id':'34','score':.9}],'checked':3,'total':35,'partial':True}


def test_judgments_validate_answers_cache_by_metadata_and_use_central_operator():
    class DecisionDB(DB):
        def execute(self,sql,params=()):
            self.calls.append((sql,params))
            return SimpleNamespace(fetchone=lambda:{'decision':{'answers':{
                'item_0':{'choice':'related','probabilities':{'related':.94,'unrelated':.06}},
                'item_1':{'choice':'unrelated','probabilities':{'related':.1,'unrelated':.9}}}}})
    db=DecisionDB([])
    candidates=[{'id':'one','title':'Geographic concordance'},{'id':'two','title':'Monthly totals'}]
    search._cache.clear()
    args=(lambda:db,'viewer@example.com',SID,'Where do reports share locations?',candidates)
    assert search.judge_batch(*args)==[{'id':'one','score':.94}]
    first=len(db.calls)
    assert search.judge_batch(*args)==[{'id':'one','score':.94}]
    assert len(db.calls)==first
    candidates[0]['title']='Edited metadata'
    search.judge_batch(*args)
    assert len(db.calls)==first*2
    operator_params=next(params for sql,params in db.calls if 'rvbbit.clover_decide' in sql)
    state,questions=map(json.loads,operator_params)
    assert 'candidates' not in state
    assert 'Geographic concordance' in questions['item_0']['instructions']
    assert 'Monthly totals' not in questions['item_0']['instructions']
    assert 'Monthly totals' in questions['item_1']['instructions']
    assert all(len(q['instructions']) <= 2000 for q in questions.values())
    assert any('rvbbit.request_user' in sql for sql,_ in db.calls)


def test_search_finds_folded_old_queries_and_persists_across_all_views(zen_page):
    page=zen_page;prepare(page)
    field=page.get_by_role('searchbox',name='Search Stage',exact=True)
    field.fill('monthly')
    assert page.locator('.stage-search-results .surface').count()==1
    assert page.locator('.stage-search-results [data-surface-id=q1]').count()==1
    assert field.evaluate('(n)=>n===document.activeElement')
    mode(page,'Topics')
    assert page.locator('[data-canvas-select=q1]').count()==1
    assert page.locator('.canvas-topic').count()==0
    mode(page,'Journey')
    assert page.locator('.canvas-card').count()==1
    mode(page,'Evidence')
    assert page.locator('.canvas-card').count()==1
    assert field.input_value()=='monthly'
    page.get_by_role('button',name='Clear Stage search and filters').click()
    assert field.input_value()==''
    mode(page,'Stage')
    assert page.locator('.stage-search-results').count()==0
    page.locator('.stage-kind-filter summary').click()
    page.select_option('#stage-search-kind','query')
    assert page.locator('.stage-search-results .surface').count()==3
    field.fill('nothing matches')
    assert page.locator('.stage-search-empty').is_visible()
    field.press('Escape')
    assert page.locator('#stage-search-kind').input_value()==''
    assert page.locator('.stage-search-empty').count()==0


def test_semantic_search_adds_related_results_without_turns_or_query_execution(zen_page):
    page=zen_page;prepare(page)
    calls=[]
    def route(r):
        calls.append(r.request.post_data_json)
        assert r.request.headers['x-calliope-stage']=='1'
        r.fulfill(json={'matches':[{'id':'geo','score':.95},{'id':'another-chat','score':1}], 'checked':5,'total':5,'partial':False})
    page.route('**/api/calliope/sessions/notebook/stage-search',route)
    before=page.evaluate('qa.state.turns.length')
    field=page.get_by_role('searchbox',name='Search Stage',exact=True)
    field.fill('coincident locations')
    assert page.locator('.stage-search-results').count()==0
    field.press('Enter')
    page.wait_for_function('document.querySelector("#stage-search-status").textContent.includes("Text + related matches")')
    assert page.locator('.stage-search-results .surface').count()==1
    assert page.locator('.stage-search-results [data-surface-id=geo]').count()==1
    assert page.evaluate('qa.state.turns.length')==before
    assert calls==[{'query':'coincident locations'}]
    mode(page,'Evidence')
    assert page.locator('[data-canvas-select=geo]').count()==1
    # Arrival marks the judgment stale; no background inference on new work.
    page.evaluate('qa.state.surfaces.push({id:"new",kind:"answer",title:"A new result",payload:{}});qa.renderStage()')
    assert 'search again' in page.locator('#stage-search-status').inner_text()
    field.fill('monthly')
    assert page.locator('[data-canvas-select=geo]').count()==0
    assert len(calls)==1


def test_late_response_cannot_repopulate_cleared_or_other_chat_search(zen_page):
    page=zen_page;prepare(page)
    # Test the real controller with a delayed transport that ignores aborts.
    page.evaluate('''() => {
      const rail=document.querySelector('#stage-rail').cloneNode(true);
      rail.id='delayed-search';document.body.append(rail);
      window.delayed=CalliopeStageSearch.create({rail,onChange:()=>{},search:()=>new Promise(resolve=>window.finishSearch=resolve)});
      delayed.update('one',[{id:'one',kind:'query',title:'Monthly'}]);
    }''')
    field=page.locator('#delayed-search input[type=search]');field.fill('places');field.press('Enter')
    page.evaluate('delayed.update("two",[{id:"two",kind:"query",title:"Different chat"}]);finishSearch({matches:[{id:"one",score:1}],checked:1,total:1})')
    assert field.input_value()==''
    assert page.evaluate('delayed.active') is False
    assert page.evaluate('[...delayed.ids]')==['two']
    page.locator('#delayed-search').evaluate('(n)=>n.remove()')


@pytest.mark.parametrize('width',[900,1500,2100])
def test_reserved_rail_never_overlaps_stage_or_canvas(zen_page,width):
    page=zen_page;prepare(page,width)
    field=page.get_by_role('searchbox',name='Search Stage',exact=True)
    field.fill('reports')
    for name in ('Stage','Topics','Journey','Evidence'):
        if name!='Stage':mode(page,name)
        sizes=page.evaluate('''() => {
          const rail=document.querySelector('#stage-rail'),scroll=document.querySelector('#stage-scroll');
          const r=rail.getBoundingClientRect(),s=scroll.getBoundingClientRect();
          return {bottom:r.bottom,top:s.top,overflow:rail.scrollWidth-rail.clientWidth};
        }''')
        assert sizes['bottom']<=sizes['top']+1
        assert sizes['overflow']<=1
    page.screenshot(path=f'/tmp/stage-search-{width}.png')
