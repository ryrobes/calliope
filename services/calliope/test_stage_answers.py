"""Answer publication, provenance, revision behavior and the actual Stage UI."""
import json
from pathlib import Path
from uuid import uuid4

import pytest
import calliope
import server
from test_generated_image_delivery import db, seed, authorize
from test_zen_chat_layout import zen_page
from test_stage_packing import prepare, geometry, no_overlaps

ROOT = Path(__file__).parent


def publish(**kwargs):
    return server.tool_publish_answer(caller_email='owner@example.com', **kwargs)


def test_answer_revisions_retries_evidence_and_streaming(db, monkeypatch):
    destination = seed(db)
    monkeypatch.setattr(server, '_conn', db)
    evidence = str(uuid4())
    with db() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_surfaces(id,session_id,turn_id,kind,title) VALUES(%s,%s,%s,'query','Lead volume')",
                     (evidence, destination['session_id'], destination['turn_id']))
    token = authorize(monkeypatch)
    try:
        first = publish(answer='  Paid leads fell.\n', caveat='Matched elapsed days.', evidence_refs=[evidence, 'surface:'+evidence])
        assert 'error' not in first, first
        assert publish(answer='Paid leads fell.', caveat='Matched elapsed days.', evidence_refs=[evidence])['surface_id'] == first['surface_id']
        second = publish(answer='Paid leads fell 12%.', evidence_refs=[evidence])
        third = publish(answer='Paid leads fell.', caveat='Matched elapsed days.', evidence_refs=[evidence])
        assert len({first['surface_id'], second['surface_id'], third['surface_id']}) == 3
        assert 'error' in publish(answer='Stale overwrite.', replaces_surface_id=first['surface_id'])
        fourth = publish(answer='Paid leads fell 10%.', replaces_surface_id=third['surface_id'])
        assert publish(answer='Paid leads fell 10%.', replaces_surface_id=third['surface_id'])['surface_id'] == fourth['surface_id']
        streamed = calliope._insert_progressive_result_surfaces(db, None, 'owner@example.com',
            destination['session_id'], destination['turn_id'], 'publish_answer', fourth, {}, 'mcp-call')
        assert len(streamed) == 1 and streamed[0]['presentation']['phase'] == 'published'
        assert calliope._insert_progressive_result_surfaces(db, None, 'owner@example.com',
            str(uuid4()), destination['turn_id'], 'publish_answer', fourth, {}, 'mcp-call') == []
        with db() as conn:
            rows = conn.execute("SELECT * FROM rvbbit.calliope_surfaces WHERE kind='answer' ORDER BY ordinal").fetchall()
        assert len(rows) == 4
        assert len({r['lineage_key'] for r in rows}) == 1
        assert [str(r['parent_surface_id']) for r in rows[1:]] == [r['surface_id'] for r in [first, second, third]]
        assert rows[0]['payload']['evidence_refs'] == [{'surface_id': evidence, 'title': 'Lead volume', 'kind': 'query'}]
        context, selected = calliope._compact_surface_context(db, destination['session_id'], first['surface_id'])
        assert selected['answer']['answer'] == 'Paid leads fell.'
    finally:
        server._AUTHORIZATION_CONTEXT.reset(token)


def test_answer_scope_validation_and_specialist_routing(db, monkeypatch):
    destination = seed(db)
    monkeypatch.setattr(server, '_conn', db)
    token = authorize(monkeypatch)
    try:
        for args in [{'answer': ''}, {'answer': 'a'*321}, {'answer': 'OK', 'caveat': 'c'*241},
                     {'answer': 'OK', 'evidence_refs': ['https://example.com']},
                     {'answer': 'OK', 'evidence_refs': [str(uuid4())]},
                     {'answer': 'OK', 'replaces_surface_id': str(uuid4())}]:
            assert publish(**args)['error']['code'] == 'ANSWER_PUBLICATION_FAILED'
        other = str(uuid4())
        with db() as conn:
            conn.execute("INSERT INTO rvbbit.calliope_sessions VALUES(%s,'other@example.com','elsewhere')", (other,))
            conn.execute("INSERT INTO rvbbit.calliope_surfaces(id,session_id,turn_id,kind,title,lineage_key) VALUES(%s,%s,%s,'answer','Other','other')",
                         (other, other, destination['turn_id']))
            conn.execute("INSERT INTO rvbbit.calliope_specialist_runs VALUES('child','owner@example.com',%s,%s,'running')", tuple(destination.values()))
        assert 'error' in publish(answer='Other', evidence_refs=[other])
        assert 'error' in publish(answer='Other', replaces_surface_id=other)
    finally:
        server._AUTHORIZATION_CONTEXT.reset(token)
    token = authorize(monkeypatch, session='child')
    try:
        child = publish(answer='The data supports this conclusion.')
        assert 'error' not in child, child
        with db() as conn:
            row = conn.execute('SELECT session_id,turn_id FROM rvbbit.calliope_surfaces WHERE id=%s', (child['surface_id'],)).fetchone()
        assert {k: str(v) for k, v in row.items()} == destination
    finally:
        server._AUTHORIZATION_CONTEXT.reset(token)
    token = authorize(monkeypatch, trusted=False)
    try:
        assert publish(answer='Denied')['error']['code'] == 'NO_STAGE_CONTEXT'
    finally:
        server._AUTHORIZATION_CONTEXT.reset(token)


def test_new_turn_answer_and_explicit_revision(db, monkeypatch):
    destination = seed(db)
    monkeypatch.setattr(server, '_conn', db)
    token = authorize(monkeypatch)
    try:
        first = publish(answer='The first question has an answer.')
        new_turn = str(uuid4())
        with db() as conn:
            conn.execute("UPDATE rvbbit.calliope_turns SET status='complete'")
            conn.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status) VALUES(%s,%s,'running')", (new_turn, destination['session_id']))
        second = publish(answer='The next question has a different answer.')
        revised = publish(answer='The first answer was revised.', replaces_surface_id=first['surface_id'])
        with db() as conn:
            rows = {str(r['id']): r for r in conn.execute("SELECT * FROM rvbbit.calliope_surfaces").fetchall()}
        assert rows[first['surface_id']]['lineage_key'] != rows[second['surface_id']]['lineage_key']
        assert rows[revised['surface_id']]['lineage_key'] == rows[first['surface_id']]['lineage_key']
        assert str(rows[revised['surface_id']]['turn_id']) == new_turn
    finally:
        server._AUTHORIZATION_CONTEXT.reset(token)


@pytest.mark.parametrize('width', [1500, 1000, 430])
def test_answer_full_width_revisions_plain_text_and_natural_height(zen_page, width):
    page = zen_page
    prepare(page, width)
    page.evaluate('''() => {
      qa.state.surfaces=[
        {id:'old',turn_id:'one',ordinal:1,kind:'answer',title:'Answer',lineage_key:'answer:one',created_at:'2026-09-13T12:00:00Z',payload:{answer:'Earlier conclusion.'}},
        {id:'answer',turn_id:'one',ordinal:3,kind:'answer',title:'Answer',lineage_key:'answer:one',created_at:'2026-09-13T12:01:00Z',payload:{
          answer:'September’s enrollment decline came primarily from fewer paid leads, rather than weaker conversion.',
          caveat:'September is incomplete; comparison uses matching elapsed days. <img src=x onerror=alert(1)>',
          evidence_refs:[{surface_id:'evidence',title:'Lead volume <script>bad()</script>'}]}},
        {id:'evidence',turn_id:'one',ordinal:2,kind:'image',title:'Supporting chart',payload:{},created_at:'2026-09-13T12:00:30Z'}];
      qa.renderStage();
    }''')
    page.wait_for_timeout(350)
    assert page.locator('.kind-answer').count() == 1
    assert page.locator('.stage-answer img,.stage-answer script').count() == 0
    assert page.locator('.stage-answer-caveat').inner_text().endswith('<img src=x onerror=alert(1)>')
    card = page.locator('[data-surface-id=answer]')
    assert abs(card.evaluate('(n)=>n.offsetWidth') - page.locator('.zen-packed').evaluate('(n)=>n.clientWidth')) <= 1
    assert page.locator('.stage-answer-headline').evaluate('(n)=>parseFloat(getComputedStyle(n).fontSize)') >= 26
    page.locator('.stage-answer summary').filter(has_text='Evidence').click()
    assert page.locator('[data-focus-surface=evidence]').is_visible()
    page.locator('.stage-answer summary').filter(has_text='Earlier').click()
    assert page.locator('.stage-answer-history').inner_text().endswith('Earlier conclusion.')
    page.wait_for_timeout(250)
    no_overlaps(geometry(page))
    assert card.evaluate('(n)=>n.scrollWidth<=n.clientWidth+1')
    page.screenshot(path=f'/tmp/calliope-stage-answer-{width}.png')


def test_tool_registration_schema():
    from mcp.server.fastmcp import FastMCP
    registry = FastMCP('answer-test')
    server._register(registry)
    tool = registry._tool_manager._tools['publish_answer']
    assert tool.parameters['required'] == ['answer']
    assert set(tool.parameters['properties']) == {'answer', 'caveat', 'evidence_refs', 'replaces_surface_id'}
    assert 'publish_answer' in calliope._PUBLISHED_STAGE_TOOLS
    assert 'publish_answer' in calliope._PROGRESSIVE_TURN_TOOLS
    assert calliope._canonical_tool('mcp_warehouse__publish_answer') == 'publish_answer'


def test_concurrent_publication_deduplicates(db):
    from concurrent.futures import ThreadPoolExecutor
    import stage_answers
    destination = seed(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: stage_answers.publish(db, destination, 'One shared conclusion.'), range(2)))
    assert results[0]['surface_id'] == results[1]['surface_id']
    assert sorted(r['reused'] for r in results) == [False, True]


@pytest.mark.parametrize('width',[650,1500,1950])
def test_answer_headline_and_caveat_have_no_artificial_right_gutter(zen_page,width):
    page=zen_page;page.set_viewport_size({'width':max(width,1500),'height':1100});prepare(page,width)
    page.evaluate('''() => {qa.state.surfaces=[{id:'answer',kind:'answer',turn_id:'one',title:'Answer',ordinal:1,payload:{
      answer:"This thread's out-of-box shelf is three named ideas, not ten: Dashboards for a durable page, What Changed for a moving number, and Decision when they have to pick — charts stay organic tools, not a fourth playbook.",
      caveat:"The factory vague-dashboard method still sits in Library beside Dashboards. Name the playbook in a new thread if you want to be sure the right one loads."}}];qa.renderStage();}''')
    page.wait_for_timeout(200)
    metrics=page.locator('.stage-answer').evaluate('''n=>{
      const s=getComputedStyle(n),content=n.clientWidth-parseFloat(s.paddingLeft)-parseFloat(s.paddingRight);
      return {content,children:[...n.querySelectorAll('.stage-answer-headline,.stage-answer-caveat')].map(p=>({width:p.offsetWidth,max:getComputedStyle(p).maxWidth,wrap:getComputedStyle(p).textWrap}))};
    }''')
    for child in metrics['children']:
        assert abs(child['width']-metrics['content'])<=1
        assert child['max']=='none'
    assert metrics['children'][0]['wrap']=='wrap'
    page.screenshot(path=f'/tmp/calliope-answer-width-{width}.png')
