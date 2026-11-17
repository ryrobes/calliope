"""Explicit Dream exploration: one queue entry, saved context, real Floor state."""
import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import calliope
import dream_handoffs as handoffs
import floor_data
from test_generated_image_delivery import db, seed
from test_floor_attention import attention_db, OWNER
from test_floor_attention import test_floor_classifies_real_decisions_separately_from_notices as setup_floor
from test_page_floor import dream_tables, projection


def dream(db):
    id = uuid4()
    with db() as c:
        c.execute("INSERT INTO rvbbit.calliope_dreams(id,owner_email,title,thesis) VALUES(%s,%s,'Find retention opportunities','A hypothesis with saved evidence')", (id, OWNER))
    return id


def notebook(db):
    dest = seed(db)
    surface = uuid4()
    with db() as c:
        c.execute("INSERT INTO rvbbit.calliope_surfaces(id,session_id,turn_id,kind) VALUES(%s,%s,%s,'dream')", (surface, dest['session_id'], dest['turn_id']))
    return dest, surface


def test_concurrent_clicks_queue_once_and_expired_preparation_can_retry(db):
    with db() as c: dream_tables(c)
    id = dream(db)
    with ThreadPoolExecutor(max_workers=4) as pool:
        tickets = list(pool.map(lambda _: handoffs.claim(db, OWNER, id), range(4)))
    assert sum(t['claimed'] for t in tickets) == 1
    old = next(t for t in tickets if t['claimed'])
    with db() as c:
        c.execute("UPDATE rvbbit.calliope_dream_handoffs SET claimed_at=now()-interval '6 minutes'")
    ticket = handoffs.claim(db, OWNER, id)
    assert ticket['claimed'] and ticket['claim_token'] != old['claim_token']
    handoffs.release(db, OWNER, id, old['claim_token'])
    dest, surface = notebook(db)
    with pytest.raises(RuntimeError):
        handoffs.launch(db, OWNER, id, old['claim_token'], dest['session_id'], surface, 'Dream', 'Explore')
    handoffs.launch(db, OWNER, id, ticket['claim_token'], dest['session_id'], surface, 'Dream', 'Explore saved evidence')
    again = handoffs.claim(db, OWNER, id)
    assert not again['claimed'] and str(again['session_id']) == dest['session_id']
    with pytest.raises(RuntimeError):
        handoffs.launch(db, OWNER, id, ticket['claim_token'], dest['session_id'], surface, 'Dream', 'Again')
    with db() as c:
        messages = c.execute('SELECT * FROM rvbbit.calliope_agent_messages').fetchall()
    assert len(messages) == 1
    assert messages[0]['payload']['surface_id'] == str(surface)
    assert messages[0]['body'] == 'Explore saved evidence'
    with db() as c:
        c.execute("UPDATE rvbbit.calliope_surfaces SET payload=%s::jsonb WHERE id=%s",
                  ('{"dream":{"title":"Retention","thesis":"A hypothesis","evidence":[{"summary":"Saved cohort query"}],"output":{"implementation_prompt":"Compare cohorts"}}}', surface))
    _, selected = calliope._compact_surface_context(db, dest['session_id'], str(surface))
    assert selected['dream']['thesis'] == 'A hypothesis'
    assert selected['dream']['evidence'][0]['summary'] == 'Saved cohort query'



def test_failed_queue_rolls_back_link_and_releases_claim(db):
    with db() as c:
        dream_tables(c)
        c.execute("ALTER TABLE rvbbit.calliope_agent_messages ADD CONSTRAINT reject_test_queue CHECK(false)")
    id = dream(db); ticket = handoffs.claim(db, OWNER, id); dest, surface = notebook(db)
    with pytest.raises(Exception, match='reject_test_queue'):
        handoffs.launch(db, OWNER, id, ticket['claim_token'], dest['session_id'], surface, 'Dream', 'Explore')
    with db() as c:
        assert c.execute('SELECT session_id FROM rvbbit.calliope_dream_handoffs').fetchone()['session_id'] is None
    handoffs.release(db, OWNER, id, ticket['claim_token'])
    assert handoffs.claim(db, OWNER, id)['claimed']


def test_bootstrap_reuses_latest_old_interactive_thread_without_starting_work(db):
    with db() as c: dream_tables(c)
    id = dream(db)
    old, old_surface = notebook(db)
    latest, latest_surface = notebook(db)
    with db() as c:
        c.execute("UPDATE rvbbit.calliope_surfaces SET tool_name='calliope_dream_handoff',source=jsonb_build_object('dream_id',%s::text)", (str(id),))
        c.execute("UPDATE rvbbit.calliope_surfaces SET created_at=now()-interval '1 day' WHERE id=%s", (old_surface,))
        c.execute(handoffs.DDL)
        c.execute(handoffs.DDL)
    ticket = handoffs.claim(db, OWNER, id)
    assert not ticket['claimed'] and str(ticket['session_id']) == latest['session_id']
    assert ticket['surface_id'] == latest_surface
    with db() as c:
        assert c.execute('SELECT count(*) AS n FROM rvbbit.calliope_agent_messages').fetchone()['n'] == 0


def test_started_dream_becomes_a_regular_thread_with_provenance(attention_db):
    db = attention_db
    setup_floor(db)
    with db() as c: c.execute('DELETE FROM rvbbit.work_graph')
    id = dream(db); ticket = handoffs.claim(db, OWNER, id); dest, surface = notebook(db)
    with db() as c:
        c.execute("UPDATE rvbbit.calliope_turns SET ordinal=1,status='complete'")
        c.execute("INSERT INTO rvbbit.work_graph(item_id,item_kind,session_id,owner_email,column_state) VALUES(%s,'chat',%s,%s,'done')", ('chat:'+dest['session_id'], dest['session_id'], OWNER))
    handoffs.launch(db, OWNER, id, ticket['claim_token'], dest['session_id'], surface, 'Dream', 'Explore')
    sql = projection()
    def read(owner=OWNER): return floor_data.read_page(db, sql, owner, floor_data.request_options({}))['cards']
    cards = read()
    assert len(cards) == 1 and cards[0]['column_state'] == 'in_motion'
    assert cards[0]['item_kind'] == 'chat' and cards[0]['active_now']
    assert cards[0]['item_id'] == 'chat:'+dest['session_id']
    assert cards[0]['dream_origin'] == {'dream_id':str(id),'surface_id':str(surface)}
    assert dest['session_id'] in calliope._floor_card_json(cards[0])['open_url']
    with db() as c:
        c.execute("UPDATE rvbbit.calliope_agent_messages SET status='delivered'")
        c.execute("UPDATE rvbbit.work_graph SET column_state='in_motion',active_now=true,active_child_count=2")
    assert read()[0]['active_child_count'] == 2
    with db() as c:
        c.execute("UPDATE rvbbit.work_graph SET column_state='done',active_now=false,active_child_count=0")
    assert read()[0]['column_state'] == 'done' and not read()[0]['active_now']
    with db() as c: c.execute("UPDATE rvbbit.calliope_agent_messages SET status='failed'")
    assert read()[0]['column_state'] == 'awaiting_you'
    # A manual continuation must not leave a stale failed-delivery blocker.
    with db() as c:
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,ordinal,status,turn_kind) VALUES(%s,%s,2,'complete','chat')", (uuid4(), dest['session_id']))
    assert read()[0]['column_state'] == 'done'
    # Shared notebook remains visible to others; its private Dream does not.
    with db() as c:
        c.execute("CREATE OR REPLACE FUNCTION rvbbit.calliope_session_can_view(uuid,text,boolean) RETURNS boolean LANGUAGE sql AS $$ SELECT true $$")
    assert [r['item_kind'] for r in read('other@example.com')] == ['chat']
    with db() as c:
        c.execute("INSERT INTO rvbbit.calliope_dream_events(dream_id,actor_email,event_kind) VALUES(%s,%s,'dismissed')", (id, OWNER))
    assert [r['item_kind'] for r in read()] == ['chat']
    with db() as c: c.execute("UPDATE rvbbit.work_graph SET column_state='archived'")
    assert read()[0]['column_state'] == 'archived'
    assert len(read()) == 1  # Archiving the thread does not resurrect its idea.
    # The Hi-you starter rail must not offer the started Dream a second time.
    tree=ast.parse((Path(__file__).parent/'calliope.py').read_text())
    assignment=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='starter_dream_sql' for t in n.targets))
    ns={};exec(compile(ast.Module(body=[assignment],type_ignores=[]),'<starter>', 'exec'),ns)
    with db() as c:
        c.execute("ALTER TABLE rvbbit.calliope_dreams ADD COLUMN dream_type text, ADD COLUMN output_kind text, ADD COLUMN output jsonb, ADD COLUMN portfolio_score numeric")
        c.execute("DELETE FROM rvbbit.calliope_dream_events")
        assert c.execute(ns['starter_dream_sql'],(OWNER,OWNER)).fetchall()==[]




def test_background_delivery_attaches_saved_dream_as_selected_context():
    tree = ast.parse((Path(__file__).parent/'calliope.py').read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'deliver_agent_messages')
    captured = {}
    async def run(request, delivered):
        captured.update(body=request._body, context=delivered)
        return SimpleNamespace(status_code=503, body=b'{}')
    class Request:
        def __init__(self, session, body): self._body = body
    ns = dict(vars(calliope), run_turn=run, _DeliveredRequest=Request,
              conn_factory=None, calliope_inputs=SimpleNamespace(delivery=lambda *_: None))
    exec(compile(ast.Module(body=[fn], type_ignores=[]), '<delivery>', 'exec'), ns)
    surface = str(uuid4())
    asyncio.run(ns['deliver_agent_messages']([dict(id=str(uuid4()), to_session_id=str(uuid4()),
        from_kind='system', from_ref='dream:'+str(uuid4()), body='Explore',
        payload={'dream_engage':True, 'surface_id':surface})], {'owner':OWNER}))
    assert captured['body']['selected_surface_id'] == surface
    assert captured['context']['agents'] == []
    assert 'human explicitly asked' in captured['context']['prompt_context']
    assert captured['body']['client_turn_id']


@pytest.mark.parametrize('background', [False, True])
def test_interactive_and_async_handoffs_share_one_thread_and_preserve_direction(db, background):
    with db() as c:
        dream_tables(c)
        c.execute("ALTER TABLE rvbbit.calliope_turns ADD COLUMN user_message text, ADD COLUMN assistant_message text, ADD COLUMN completed_at timestamptz, ADD COLUMN turn_kind text")
    id = dream(db)
    created = []
    async def create(*args, **kwargs):
        sid = str(uuid4()); created.append(sid)
        with db() as c:
            c.execute('INSERT INTO rvbbit.calliope_sessions(id,owner_email) VALUES(%s,%s)', (sid, OWNER))
        return {'id':sid}
    def surfaces(factory, sid, tid, items):
        surface = str(uuid4())
        with db() as c:
            c.execute("INSERT INTO rvbbit.calliope_surfaces(id,session_id,turn_id,kind,payload) VALUES(%s,%s,%s,'dream',%s::jsonb)",
                (surface, sid, tid, calliope.json.dumps(items[0]['payload'], default=str)))
        return [{'id':surface}]
    async def discard(session):
        raise AssertionError('Unexpected preparation failure')
    tree = ast.parse((Path(__file__).parent/'calliope.py').read_text())
    names = {'handoff_calliope_dream','prepare_dream_handoff'}
    fns = [n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name in names]
    for fn in fns: fn.decorator_list=[]
    ns = dict(vars(calliope), conn_factory=db, config=None,
        api_owner=lambda request:(request.owner,None), _create_session_record=create,
        _insert_surfaces=surfaces, discard_created_session=discard,
        json_response=lambda data,status=200:SimpleNamespace(data=data,status=status),
        calliope_dreams=SimpleNamespace(_dream_accessible=lambda c,owner,row:row['owner_email']==owner,
            dream_public=lambda row:dict(row),record_event=lambda *args:None))
    exec(compile(ast.Module(body=fns,type_ignores=[]),'<dream-handoff>','exec'),ns)
    class Request:
        owner=OWNER
        path_params={'dream_id':str(id)}
        async def json(self): return {'origin':'floor','background':background,'direction':'Focus on September; investigate only.'}
    first = asyncio.run(ns['handoff_calliope_dream'](Request()))
    assert first.status==201 and first.data['queued']
    assert 'prompt=' not in first.data['url']  # Browser navigation cannot fire a second turn.
    background = not background
    again = asyncio.run(ns['handoff_calliope_dream'](Request()))
    assert again.status==200 and again.data['already_engaged']
    assert again.data['session']['id']==first.data['session']['id']
    assert len(created)==1
    with db() as c:
        messages=c.execute('SELECT * FROM rvbbit.calliope_agent_messages').fetchall()
    assert len(messages)==1 and 'Focus on September; investigate only.' in messages[0]['body']
    other = Request(); other.owner='other@example.com'
    denied = asyncio.run(ns['handoff_calliope_dream'](other))
    assert denied.status==404 and len(created)==1
