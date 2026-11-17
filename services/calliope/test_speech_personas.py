import hashlib
import hmac
import os
from types import SimpleNamespace
from uuid import uuid4
import pytest
from test_calliope_voice import calliope


def test_bank_isolation_preserves_calliope_bank():
    config=SimpleNamespace(presentation_memory_salt='test-salt')
    expected='calliope-presentation-v1-'+hmac.new(b'test-salt',b'calliope.presentation-memory.v1\0a@example.com',hashlib.sha256).hexdigest()[:32]
    assert calliope._presentation_memory_bank_id(config,' A@EXAMPLE.COM ')==expected
    banks={calliope._presentation_memory_bank_id(config,owner,agent) for owner in ['a@example.com','b@example.com'] for agent in ['calliope','iris','athena']}
    assert len(banks)==6


def test_persona_storage_isolates_users_agents_and_preserves_calliope_modes(monkeypatch):
    monkeypatch.setattr(calliope, "_profile_speech_persona", lambda factory, key: "Stock " + key)
    psycopg=pytest.importorskip('psycopg')
    from psycopg.rows import dict_row
    dsn=os.environ.get('TEAM_TEST_DSN')
    if not dsn:pytest.skip('requires disposable PostgreSQL')
    with psycopg.connect(dsn,autocommit=True) as connection:
        assert connection.execute("SELECT to_regnamespace('rvbbit')").fetchone()[0] is None
        connection.execute('CREATE SCHEMA rvbbit')
        try:
            connection.execute(calliope._RESPONSE_PREFERENCES_DDL)
            connection.execute(calliope._AGENT_SPEECH_PERSONAS_DDL)
            connection.execute("INSERT INTO rvbbit.calliope_response_preferences(owner_email,response_personality,read_aloud_mode,conversational_answers,speak_working_updates) VALUES('a@example.com','original','expressive',true,true)")
            factory=lambda:psycopg.connect(dsn,row_factory=dict_row)
            calliope._save_agent_speech_persona(factory,'a@example.com','iris','playful')
            calliope._save_agent_speech_persona(factory,'a@example.com','athena','precise')
            calliope._save_agent_speech_persona(factory,'b@example.com','iris','formal')
            assert calliope._agent_speech_persona(factory,'a@example.com','iris')=='playful'
            assert calliope._agent_speech_persona(factory,'a@example.com','athena')=='precise'
            assert calliope._agent_speech_persona(factory,'b@example.com','iris')=='formal'
            assert calliope._agent_speech_persona(factory,'b@example.com','athena')=='Stock athena'
            calliope._save_agent_speech_persona(factory,'a@example.com','calliope','updated')
            row=connection.execute("SELECT response_personality,read_aloud_mode,conversational_answers,speak_working_updates FROM rvbbit.calliope_response_preferences WHERE owner_email='a@example.com'").fetchone()
            assert row==('updated','expressive',True,True)
            calliope._save_agent_speech_persona(factory,'a@example.com','iris','')
            assert calliope._agent_speech_persona(factory,'a@example.com','iris')=='Stock iris'
            assert calliope._agent_speech_persona(factory,'a@example.com','athena')=='precise'
            connection.execute('CREATE TABLE rvbbit.calliope_sessions(id uuid PRIMARY KEY); CREATE TABLE rvbbit.calliope_turns(id uuid PRIMARY KEY)')
            connection.execute(calliope._PRESENTATION_MEMORY_DDL)
            turn={'id':str(uuid4()),'session_id':str(uuid4())}
            connection.execute('INSERT INTO rvbbit.calliope_sessions VALUES(%s)',(turn['session_id'],))
            connection.execute('INSERT INTO rvbbit.calliope_turns VALUES(%s)',(turn['id'],))
            bank_a='calliope-presentation-v1-'+'a'*32
            bank_b='calliope-presentation-v1-'+'b'*32
            calliope._queue_presentation_memory(connection,'a@example.com',bank_a,turn)
            calliope._queue_presentation_memory(connection,'a@example.com',bank_b,turn)
            assert connection.execute('SELECT bank_id FROM rvbbit.calliope_presentation_memory_queue').fetchone()[0]==bank_b
            connection.execute("UPDATE rvbbit.calliope_presentation_memory_queue SET status='submitted',submitted_at=now(),batch_key='submitted'")
            calliope._queue_presentation_memory(connection,'a@example.com',bank_a,turn)
            assert connection.execute('SELECT bank_id FROM rvbbit.calliope_presentation_memory_queue').fetchone()[0]==bank_b
        finally:connection.execute('DROP SCHEMA rvbbit CASCADE')


def config(tmp_path):
    return calliope.CalliopeConfig(hermes_url='',hermes_api_key='',memory_key='company',file_root=tmp_path,max_image_bytes=1024)


def test_specialist_answer_ignores_calliope_persona_and_uses_own_bank(monkeypatch,tmp_path):
    captured={}
    turn={'id':str(uuid4()),'session_id':str(uuid4()),'voice_agent_key':'athena','voice_agent_name':'Athena','assistant_message':'Revenue rose ten percent.','user_message':'How is revenue?','response_receipt':{}}
    monkeypatch.setattr(calliope,'_voice_turn',lambda *args:turn)
    monkeypatch.setattr(calliope,'_agent_speech_persona',lambda factory,owner,key:'Precise and thoughtful' if key=='athena' else 'WRONG')
    def recall(*args):
        captured['bank_agent']=args[-1]
        return {'items':['a private Athena callback'],'status':'ok','agent_key':'athena','scope':'private_user_agent'}
    monkeypatch.setattr(calliope,'_presentation_memory_recall',recall)
    def rewrite(*args,**kwargs):
        captured.update(persona=args[3],instruction=args[6],memory=args[7])
        return 'Revenue rose ten percent.','local',None
    monkeypatch.setattr(calliope,'_generate_voice_script',rewrite)
    monkeypatch.setattr(calliope,'_store_voice_render',lambda *args,**kwargs:None)
    render,_=calliope._voice_render_unlocked(None,config(tmp_path),'a@example.com',turn['session_id'],turn['id'],'expressive','Calliope global persona')
    assert captured['persona']=='Precise and thoughtful'
    assert captured['bank_agent']=='athena'
    assert 'Athena' in captured['instruction'] and 'Calliope global persona' not in captured['instruction']
    assert captured['memory']==['a private Athena callback']
    assert render['agent_key']=='athena' and render['presentation_memory']['scope']=='private_user_agent'


def test_preview_reuses_rewriter_and_bank_but_never_retains(monkeypatch,tmp_path):
    captured={}
    def recall(*args):
        captured['bank_agent']=args[-1]
        return {'items':['rapport']}
    def rewrite(*args,**kwargs):
        captured.update(persona=args[3],instruction=args[6],memory=args[7]);return '[happy] I am Iris.','local',None
    monkeypatch.setattr(calliope,'_presentation_memory_recall',recall)
    monkeypatch.setattr(calliope,'_generate_voice_script',rewrite)
    monkeypatch.setattr(calliope,'_queue_presentation_memory',lambda *args:pytest.fail('preview retained'))
    output=calliope._prepare_agent_voice_preview(None,config(tmp_path),'a@example.com',{'key':'iris','name':'Iris','voice_preview_text':'I am Iris. I build dashboards.'},'playful draft')
    assert output=='[happy] I am Iris.'
    assert captured['bank_agent']=='iris' and captured['persona']=='playful draft'
    assert captured['memory']==['rapport'] and 'Iris' in captured['instruction']


def test_progress_uses_specialist_persona(monkeypatch,tmp_path):
    captured={}
    monkeypatch.setattr(calliope,'_voice_progress_context',lambda *args:{'messages':[],'agent_key':'athena','agent_name':'Athena'})
    monkeypatch.setattr(calliope,'_agent_speech_persona',lambda *args:'precise')
    def rewrite(*args,**kwargs):
        captured.update(persona=args[3],instruction=args[6]);return 'Checking definitions.','local',None
    monkeypatch.setattr(calliope,'_generate_voice_script',rewrite)
    calliope._voice_progress_render(None,config(tmp_path),'a@example.com',str(uuid4()),str(uuid4()),'Checking definitions.','expressive','Calliope personality')
    assert captured['persona']=='precise' and 'Athena' in captured['instruction']

@pytest.mark.parametrize('method,authenticated,key,expected',[('GET',False,'iris',401),('PUT',False,'iris',401),('GET',True,'missing',404),('GET',True,'iris',200),('PUT',True,'iris',200)])
def test_persona_route_uses_authenticated_owner(method,authenticated,key,expected):
    import asyncio,json
    from starlette.responses import JSONResponse
    from test_team import load_function
    calls=[]
    async def roster(): return [{'key':'iris'}]
    async def body(): return json.dumps({'personality':'quietly playful','owner':'victim@example.com','agent_key':'calliope'}).encode()
    def read(factory,owner,agent): calls.append((owner,agent));return {'agent_key':agent,'personality':'saved','default_personality':'stock','source':'custom'}
    def save(factory,owner,agent,value): calls.append((owner,agent,value));return value
    route=load_function('team_speech_persona',dict(asyncio=asyncio,json=json,api_owner=lambda request:('actual@example.com',None) if authenticated else (None,JSONResponse({},401)),
        configured_chat_agents=roster,conn_factory=None,_speech_persona_settings=read,_save_agent_speech_persona=save,json_response=lambda value,status=200:JSONResponse(value,status)))
    response=asyncio.run(route(SimpleNamespace(method=method,path_params={'agent_key':key},body=body)))
    assert response.status_code==expected
    if expected==200:
        assert calls[0][:2]==('actual@example.com','iris')
        assert response.headers['cache-control']=='no-store'
    else: assert not calls


def test_stock_resolution_and_private_precedence(monkeypatch):
    from contextlib import contextmanager
    class Connection:
        def execute(self, sql, args):
            assert args == ("athena",)
            return SimpleNamespace(fetchone=lambda: {"profile_name": "semantic-architect"})
    @contextmanager
    def factory():
        yield Connection()
    stock = {"profiles": [{"key": "default", "speech_persona": "Warm coordinator"}, {"key": "semantic-architect", "speech_persona": "Precise architect"}]}
    monkeypatch.setattr(calliope.hermes_profiles, "profiles", lambda: stock)
    assert calliope._profile_speech_persona(factory, "athena") == "Precise architect"
    assert calliope._profile_speech_persona(factory, "calliope") == "Warm coordinator"
    monkeypatch.setattr(calliope, "_stored_speech_persona", lambda *args: "")
    assert calliope._speech_persona_settings(factory, "a@example.com", "athena") == {
        "agent_key": "athena", "personality": "Precise architect", "default_personality": "Precise architect", "source": "profile"}
    stock["profiles"][1]["speech_persona"] = "Updated architect"
    assert calliope._agent_speech_persona(factory, "a@example.com", "athena") == "Updated architect"
    monkeypatch.setattr(calliope, "_stored_speech_persona", lambda *args: "My private voice")
    assert calliope._agent_speech_persona(factory, "a@example.com", "athena") == "My private voice"
    assert calliope._speech_persona_settings(factory, "a@example.com", "athena")["source"] == "custom"


def test_all_shipping_profiles_have_distinct_bounded_personalities():
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[2] / "docker/calliope"
    paths = [root / "hermes-profile.yaml", *root.glob("hermes-specialists/*/profile.yaml")]
    values = [yaml.safe_load(p.read_text())["speech_persona"] for p in paths]
    assert len(values) == len(set(values)) == 8
    assert all(100 < len(value) <= 600 for value in values)

@pytest.mark.parametrize('client_persona,expected',[('', 'Warm stock coordinator'),('My saved style','My saved style')])
def test_calliope_chat_inherits_stock_only_without_custom_text(monkeypatch,tmp_path,client_persona,expected):
    captured={}
    turn={'id':str(uuid4()),'session_id':str(uuid4()),'voice_agent_key':'calliope','assistant_message':'Revenue rose ten percent.','user_message':'How is revenue?','response_receipt':{}}
    monkeypatch.setattr(calliope,'_voice_turn',lambda *args:turn)
    monkeypatch.setattr(calliope,'_profile_speech_persona',lambda *args:'Warm stock coordinator')
    monkeypatch.setattr(calliope,'_presentation_memory_recall',lambda *args:{'items':[]})
    def rewrite(*args,**kwargs):
        captured['persona']=args[3]
        return 'Revenue rose ten percent.','local',None
    monkeypatch.setattr(calliope,'_generate_voice_script',rewrite)
    monkeypatch.setattr(calliope,'_store_voice_render',lambda *args,**kwargs:None)
    calliope._voice_render_unlocked(None,config(tmp_path),'a@example.com',turn['session_id'],turn['id'],'expressive',client_persona)
    assert captured['persona']==expected

@pytest.mark.parametrize('scope,expected_voice,expected_direction',[(None,'athena-profile',''),('calliope','athena-profile',''),('semantic-architect','custom-voice','Custom delivery')])
def test_main_chat_rejects_unscoped_tuning_for_specialists(monkeypatch,tmp_path,scope,expected_voice,expected_direction):
    key='semantic-architect';captured={}
    turn={'id':str(uuid4()),'session_id':str(uuid4()),'voice_agent_key':key,'voice_agent_name':'Athena','assistant_message':'Revenue rose ten percent.','user_message':'How is revenue?','response_receipt':{}}
    monkeypatch.setattr(calliope,'_voice_turn',lambda *args:turn)
    monkeypatch.setattr(calliope,'_agent_speech_persona',lambda *args:'Precise Athena persona')
    monkeypatch.setattr(calliope,'_session_profile_voice',lambda *args:'athena-profile')
    monkeypatch.setattr(calliope,'_voice_synthesis_profile',lambda *args:{'upstream_model':'fish','voice':'callie-default','voice_override':True})
    monkeypatch.setattr(calliope,'_presentation_memory_recall',lambda *args:{'items':[]})
    def rewrite(*args,**kwargs):
        captured.update(persona=args[3],direction=kwargs['direction'],messages=calliope._voice_rewrite_messages(args[6],args[1],[],True))
        return 'Revenue rose ten percent.','local',None
    monkeypatch.setattr(calliope,'_generate_voice_script',rewrite)
    monkeypatch.setattr(calliope,'_store_voice_render',lambda *args,**kwargs:None)
    render,_=calliope._voice_render_unlocked(None,config(tmp_path),'a@example.com',turn['session_id'],turn['id'],'expressive','Wrong Callie persona',{'agent_key':scope,'voice_id':'custom-voice','direction':'Custom delivery'})
    assert render['tts_voice']==expected_voice
    assert captured['direction']==expected_direction
    assert captured['persona']=='Precise Athena persona'
    assert 'Athena' in captured['messages'][0]['content']
    assert 'Calliope' not in captured['messages'][-1]['content']
