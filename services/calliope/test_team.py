"""TEAM's production route logic, with a disposable PostgreSQL activity ledger."""
import ast
import asyncio
import logging
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

SOURCE = Path(__file__).with_name('calliope.py')

def load_function(name, namespace):
    tree = ast.parse(SOURCE.read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace[name]


def route(factory, authenticated=True):
    async def roster():
        return [{'key':'calliope','name':'Calliope'}, {'key':'expert','name':'Expert'}, {'key':'new','name':'New'}]
    return load_function('team_roster', dict(asyncio=asyncio, logging=logging,
        conn_factory=factory, configured_chat_agents=roster, json_response=lambda x:x,
        api_owner=lambda _:('viewer',None) if authenticated else (None,{'error':'unauthorized'})))


def test_requires_authentication_before_loading_roster():
    assert asyncio.run(route(None,False)(None)) == {'error':'unauthorized'}


def test_activity_failure_preserves_roster_without_false_zeroes():
    def broken():
        raise RuntimeError('offline')
    data=asyncio.run(route(broken)(None))
    assert len(data['agents'])==3 and not data['activity_available']
    assert all(a['activity'] is None and a['tools'] is None for a in data['agents'])


def test_activity_privacy_window_and_separate_counts():
    psycopg=pytest.importorskip('psycopg')
    from psycopg.rows import dict_row
    dsn=os.environ.get('TEAM_TEST_DSN')
    if not dsn:
        pytest.skip('TEAM_TEST_DSN must point to an empty disposable database')
    with psycopg.connect(dsn,autocommit=True) as c:
        assert c.execute("SELECT to_regnamespace('rvbbit')").fetchone()[0] is None
        c.execute('''CREATE SCHEMA rvbbit;
          CREATE TABLE rvbbit.calliope_sessions(id uuid PRIMARY KEY,specialist_key text,title text,owner_email text,hermes_session_id text);
          CREATE TABLE rvbbit.calliope_turns(session_id uuid,status text,created_at timestamptz DEFAULT now());
          CREATE TABLE rvbbit.calliope_specialists(id int PRIMARY KEY,specialist_key text,profile_name text);
          CREATE TABLE rvbbit.calliope_specialist_runs(specialist_id int,source_session_id uuid,owner_email text,status text,created_at timestamptz DEFAULT now(),hermes_session_id text);
          CREATE TABLE rvbbit.hermes_messages(id bigint,store text,session_id text,role text,ts timestamptz DEFAULT now(),tool_calls jsonb);
          CREATE FUNCTION rvbbit.calliope_session_can_view(uuid,text,boolean) RETURNS boolean LANGUAGE sql AS $$
            SELECT owner_email=$2 FROM rvbbit.calliope_sessions WHERE id=$1 $$;
          INSERT INTO rvbbit.calliope_specialists VALUES(1,'expert','expert-profile');''')
        try:
            visible,hidden,expert=uuid4(),uuid4(),uuid4()
            for sid,key,title,owner in [(visible,None,'Visible','viewer'),(hidden,None,'Private','other'),(expert,'expert','Direct','viewer')]:
                c.execute('INSERT INTO rvbbit.calliope_sessions(id,specialist_key,title,owner_email) VALUES(%s,%s,%s,%s)',(sid,key,title,owner))
                c.execute("INSERT INTO rvbbit.calliope_turns(session_id,status) VALUES(%s,'complete')",(sid,))
            c.execute("INSERT INTO rvbbit.calliope_turns VALUES(%s,'complete',now()-interval '40 days')",(visible,))
            c.execute("INSERT INTO rvbbit.calliope_specialist_runs(specialist_id,source_session_id,owner_email,status) VALUES(1,%s,'viewer','running'),(1,%s,'other','running'),(1,NULL,'other','complete'),(1,NULL,'viewer','complete')",(visible,hidden))
            result=asyncio.run(route(lambda:psycopg.connect(dsn,row_factory=dict_row))(None))
            rows={a['key']:a['activity'] for a in result['agents']}
            assert result['activity_available']
            assert rows['calliope']['requests']==1
            assert rows['expert']['requests']==1 and rows['expert']['assignments']==2
            assert rows['expert']['active']==1
            assert rows['new']['requests']==0
            assert 'Private' not in str(result)
            # Tool calls must retain the same access boundary, match the profile
            # store, exclude old calls/results, and deduplicate replayed call IDs.
            c.execute("UPDATE rvbbit.calliope_sessions SET hermes_session_id=id::text")
            c.execute("UPDATE rvbbit.calliope_specialist_runs SET hermes_session_id='delegated' WHERE source_session_id=%s",(visible,))
            c.execute("UPDATE rvbbit.calliope_specialist_runs SET hermes_session_id='private-delegated' WHERE source_session_id=%s",(hidden,))
            def message(mid,store,sid,calls,role='assistant',old=False):
                c.execute("INSERT INTO rvbbit.hermes_messages(id,store,session_id,role,ts,tool_calls) VALUES(%s,%s,%s,%s,now()-(%s * interval '1 day'),%s::jsonb)",
                          (mid,store,str(sid),role,40 if old else 0,json.dumps(calls)))
            call=[{'id':'same','function':{'name':'mcp__warehouse__query'}}]
            message(1,'default',visible,call)
            message(2,'default',visible,call) # compacted/replayed copy
            message(3,'default',visible,call,role='tool') # result, not call
            message(4,'default',hidden,[{'id':'private','name':'private_tool'}])
            message(5,'wrong-profile',visible,[{'id':'wrong','name':'wrong_tool'}])
            message(6,'default',visible,[{'id':'old','name':'old_tool'}],old=True)
            message(7,'default',visible,{'malformed':'array expected'})
            message(8,'expert-profile','private-delegated',[{'id':'private','name':'private_tool'}])
            message(9,'expert-profile','delegated',[{'id':'delegate','name':'delegated_tool'}])
            message(11,'expert-profile','delegated',[{'id':f'excluded-{i}','name':name} for i,name in enumerate(['tool_call','todo','mcp__warehouse__tool_call','mcp__warehouse__todo','namespace.todo','namespace/tool_call'])])
            message(10,'expert-profile',expert,[{'id':str(i),'function':{'name':f'tool_{i:02}'}} for i in range(12)])
            result=asyncio.run(route(lambda:psycopg.connect(dsn,row_factory=dict_row))(None))
            tools={a['key']:a['tools'] for a in result['agents']}
            assert tools['calliope']=={'total':1,'top':[{'name':'mcp__warehouse__query','calls':1}]}
            assert tools['expert']['total']==13 and len(tools['expert']['top'])==10
            assert tools['expert']['top'][0]['name']=='delegated_tool'
            assert tools['new']=={'total':0,'top':[]}
            assert 'private_tool' not in str(result) and 'wrong_tool' not in str(result)

        finally:
            c.execute('DROP SCHEMA rvbbit CASCADE')


def test_roster_uses_active_configuration_and_profile_identity():
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def execute(self,sql):
            assert "status='active'" in sql
            return SimpleNamespace(fetchall=lambda:[{'specialist_key':'expert','profile_name':'expert-profile','display_name':'Old','description':'Useful','capabilities':['research']}])
    ns=dict(asyncio=asyncio,conn_factory=Connection,_agent_avatar_url=lambda x:x,
        hermes_profiles=SimpleNamespace(profiles=lambda:{'profiles':[{'key':'expert-profile','name':'Current','avatar_file':'portrait.jpg'}]}))
    rows=asyncio.run(load_function('configured_chat_agents',ns)())
    assert rows[0]['key']=='calliope'
    assert rows[1]['name']=='Current' and rows[1]['avatar_url']=='portrait.jpg'
