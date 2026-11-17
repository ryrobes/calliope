"""Verify actual PG inventory, graph membership, isolation, and partial failures."""
import json
import pytest
import gallery_brain as brain
from test_workspace_sql import db, OWNER  # noqa:F401

@pytest.fixture(autouse=True)
def cache(monkeypatch):
    monkeypatch.setattr(brain, '_agent_identities', lambda: {})
    brain._cache.clear()
    yield
    brain._cache.clear()


def seed(db):
    with db() as c:
        c.execute('''CREATE TABLE rvbbit.brain_sources(source_id bigint,label text,kind text,enabled bool,created_at timestamptz,last_synced_at timestamptz);
        INSERT INTO rvbbit.brain_sources VALUES(1,'Meetings','google_meet',true,now(),now()),(2,'SOPs','manual',false,now(),NULL),(3,'Empty','manual',true,now(),NULL);
        CREATE TABLE rvbbit.brain_documents(doc_id bigint,source_id bigint,deleted_at timestamptz,enriched_at timestamptz,enrich_hash text,content_hash text,ingested_at timestamptz,enrich_last_error text);
        INSERT INTO rvbbit.brain_documents VALUES(1,1,NULL,now(),'a','a',now(),NULL),(2,2,NULL,NULL,NULL,'b',now(),'SECRET ERROR'),(3,1,now(),NULL,NULL,NULL,now(),NULL);
        CREATE FUNCTION rvbbit.brain_doc_label(bigint) RETURNS text LANGUAGE sql AS $$SELECT 'doc '||$1$$;
        CREATE TABLE rvbbit.brain_chunks(doc_id bigint);
        INSERT INTO rvbbit.brain_chunks VALUES(1),(1),(2),(3);
        CREATE TABLE rvbbit.kg_nodes(node_id bigint,graph_id text,kind text,label_norm text);
        INSERT INTO rvbbit.kg_nodes VALUES(1,'brain','document','doc 1'),(2,'brain','document','doc 2'),(3,'brain','person','SECRET PERSON'),(4,'brain','document','doc 3');
        ALTER TABLE rvbbit.kg_nodes ADD COLUMN label text, ADD COLUMN properties jsonb DEFAULT '{}'::jsonb, ADD COLUMN updated_at timestamptz DEFAULT now();
        UPDATE rvbbit.kg_nodes SET label=label_norm;
        CREATE TABLE rvbbit.kg_edges(graph_id text,subject_node_id bigint,object_node_id bigint,predicate_norm text);
        INSERT INTO rvbbit.kg_edges VALUES('brain',1,3,'mentions'),('brain',2,3,'mentions'),('brain',4,3,'mentions');
        ALTER TABLE rvbbit.kg_edges ADD COLUMN edge_id bigint GENERATED ALWAYS AS IDENTITY;
        CREATE SCHEMA hindsight;
        CREATE TABLE hindsight.banks(bank_id text,name text,created_at timestamptz,updated_at timestamptz);
        INSERT INTO hindsight.banks VALUES('a','Calliope',now(),now()),('b','Iris',now(),now());
        CREATE TABLE hindsight.memory_units(id uuid,bank_id text,fact_type text,event_date timestamptz,created_at timestamptz);
        INSERT INTO hindsight.memory_units VALUES('00000000-0000-0000-0000-000000000001','a','world',now(),now()),('00000000-0000-0000-0000-000000000002','a','experience',now(),now()),('00000000-0000-0000-0000-000000000003','b','world',now(),now());
        CREATE TABLE hindsight.memory_links(from_unit_id uuid,to_unit_id uuid,link_type text,weight float,bank_id text);
        INSERT INTO hindsight.memory_links VALUES('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002','semantic',1,'a');
        CREATE TABLE hindsight.documents(bank_id text);
        INSERT INTO hindsight.documents VALUES('a'),('b');
        CREATE TABLE hindsight.entities(id uuid,bank_id text);
        INSERT INTO hindsight.entities VALUES('00000000-0000-0000-0000-000000000004','a');
        CREATE TABLE hindsight.unit_entities(unit_id uuid,entity_id uuid);
        INSERT INTO hindsight.unit_entities VALUES('00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000004');''')


def test_counts_real_connections_and_no_contents(db):
    seed(db)
    x=brain.snapshot(db,OWNER)
    assert not x['unavailable'] and not x['graph']['unavailable']
    items={i['ref']:i for i in x['items']}
    assert items['source:1']['documents']==1 and items['source:1']['chunks']==2
    assert items['source:1']['ready']==1 and items['source:2']['pending']==1
    assert items['source:2']['errors']==1 and items['source:3']['documents']==0
    assert items['bank:a']['memories']==2 and items['bank:a']['links']==1
    nodes={n['id']:n for n in x['graph']['nodes']}
    assert set(nodes['entity:3']['sources'])=={'source:1','source:2'}
    assert 'doc:3' not in nodes and 'source:3' in nodes
    assert any(e['kind']=='semantic' for e in x['graph']['edges'])
    assert all(e['source'] in nodes and e['target'] in nodes for e in x['graph']['edges'])
    assert 'SECRET' not in json.dumps(x,default=str)
    # Auth is checked even when a full admin snapshot is cached.
    with pytest.raises(PermissionError):brain.snapshot(db,'outsider@example.com')


def test_selected_sources_are_isolated_and_bad_refs_rejected(db):
    seed(db)
    for ref in ['source:1','bank:a','source:3']:
        x=brain.snapshot(db,OWNER,ref)
        assert len(x['items'])==5
        assert all(set(n['sources'])=={ref} for n in x['graph']['nodes'])
        assert x['graph']['sample_per_source']==100
    with pytest.raises(LookupError):brain.snapshot(db,OWNER,"bank:a';DELETE FROM hindsight.banks;--")
    with pytest.raises(ValueError):brain.snapshot(db,OWNER,'x'*301)


def test_missing_extensions_and_missing_optional_tables(db):
    x=brain.snapshot(db,OWNER)
    assert x['items']==[] and x['graph']['nodes']==[]
    assert set(x['unavailable'])=={'Document sources','Hindsight banks','Knowledge graphs'}
    brain._cache.clear();seed(db)
    with db() as c:c.execute('DROP TABLE hindsight.memory_links')
    x=brain.snapshot(db,OWNER)
    assert len(x['items'])==5
    assert next(i for i in x['items'] if i['ref']=='bank:a')['links'] is None
    assert x['graph']['nodes'] and 'Hindsight links' in x['unavailable']
    assert 'Memory connections' in x['graph']['unavailable']


def test_cache_keeps_refresh_bounded(db):
    seed(db);x=brain.snapshot(db,OWNER)
    with db() as c:c.execute("INSERT INTO hindsight.documents VALUES('a')")
    assert brain.snapshot(db,OWNER)['items']==x['items']
    brain._cache.clear()
    assert next(i for i in brain.snapshot(db,OWNER)['items'] if i['ref']=='bank:a')['documents']==2


def test_agent_banks_use_exact_profiles_and_valid_portrait_paths():
    items=[dict(ref='bank:'+bank,family='memory') for bank in
           ['hermes','calliope-dashboard-studio','warehouse-steward','calliope-unknown','calliope-design-red-team']]
    brain._attach_agents(items,{
        'default':dict(name='Calliope',avatar_night_file='callie-avatar-night.jpg'),
        'dashboard-studio':dict(name='Iris',avatar_file='iris.png'),
        'warehouse-steward':dict(name='Hestia',avatar_file='hestia.png'),
        'design-red-team':dict(name='Nemesis',avatar_file='../../private.png')})
    assert items[0]['agent_name']=='Calliope' and items[0]['agent_profile']=='default'
    assert items[0]['avatar_url']=='/calliope/specialists/callie-avatar-night.jpg'
    assert items[1]['agent_name']=='Iris' and items[1]['avatar_url']=='/calliope/specialists/iris.png'
    assert items[2]['agent_name']=='Hestia'
    assert 'avatar_url' not in items[3]
    assert items[4]['agent_name']=='Nemesis' and items[4]['avatar_url'] is None


def test_avatar_thumbnail_reuses_bounded_portrait_renderer():
    import io
    from pathlib import Path
    from PIL import Image
    import calliope
    original=(Path(__file__).parent/'calliope/iris.png').read_bytes()
    data,mime=calliope._small_agent_avatar(original)
    assert len(data)<64000 and len(data)<len(original)
    assert mime in {'image/webp','image/png'}
    assert Image.open(io.BytesIO(data)).size==(96,96)
    assert calliope._small_agent_avatar(b'invalid image') is None


def test_avatar_route_keeps_auth_and_originals_and_thumbnails_generated_images(monkeypatch,tmp_path):
    import asyncio
    import sys
    import types
    from pathlib import Path
    import calliope
    routes={}
    class MCP:
        def custom_route(self,path,methods):
            def add(fn):routes[path]=fn;return fn
            return add
    class Conn:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql,*args):
            if 'to_regclass' in sql:return types.SimpleNamespace(fetchone=lambda:{'r':True})
            if 'SELECT png' in sql:return types.SimpleNamespace(fetchone=lambda:{'png':(calliope._ASSET_DIR/'iris.png').read_bytes()})
            raise ValueError('No writes or unrelated DB operations in avatar test')
    monkeypatch.setitem(sys.modules,'auth',types.SimpleNamespace(read_session_full=lambda r:r.session))
    monkeypatch.setattr(calliope,'ensure_tables',lambda _:None)
    monkeypatch.setattr(calliope.hermes_docs,'ensure',lambda _:None)
    monkeypatch.setattr(calliope.hermes_config,'ensure',lambda _:None)
    monkeypatch.setattr(calliope.provider_settings,'ensure',lambda _:None)
    monkeypatch.setattr(calliope,'_start_session_synopsis_worker',lambda _:None)
    monkeypatch.setenv('WAREHOUSE_HERMES_URL','http://hermes:8642')
    monkeypatch.setenv('WAREHOUSE_HERMES_API_KEY','test')
    monkeypatch.setenv('WAREHOUSE_CALLIOPE_DIR',str(tmp_path))
    calliope.register_calliope_routes(MCP(),Conn,'',lambda x:x)
    route=routes['/calliope/specialists/{avatar_file}']
    def request(file,query=None,session=True):
        return types.SimpleNamespace(path_params={'avatar_file':file},query_params=query or {},session={'identity':'admin@example.com','sub':'admin','mapped':True} if session else None)
    full=asyncio.run(route(request('iris.png')))
    assert Path(full.path).name=='iris.png' and full.media_type=='image/png'
    for file in ['iris.png','generated-test-expert.png']:
        thumb=asyncio.run(route(request(file,{'thumbnail':'1'})))
        assert thumb.status_code==200 and len(thumb.body)<64000
        assert thumb.media_type in {'image/webp','image/png'}
        assert 'private' in thumb.headers['cache-control']
    assert asyncio.run(route(request('iris.png',{'thumbnail':'1'},False))).status_code==401
    assert asyncio.run(route(request('../iris.png',{'thumbnail':'1'}))).status_code==404


def test_knowledge_graphs_bridge_exact_tables_and_keep_filtered_views_bounded(db):
    seed(db)
    with db() as c:
        c.execute("""INSERT INTO rvbbit.kg_nodes(node_id,graph_id,kind,label,label_norm,properties) VALUES
          (10,'db_catalog','db_schema','sales','sales','{"schema":"sales"}'),
          (11,'db_catalog','db_table','sales.orders','sales.orders','{"schema":"sales"}'),
          (12,'db_catalog','db_column','sales.orders.id','sales.orders.id','{"schema":"sales"}'),
          (20,'business_context','business_domain','revenue','revenue','{"name":"Revenue","domain_key":"revenue"}'),
          (21,'business_context','business_source_relation','source orders','source orders','{"relation":"sales.orders","domain_key":"revenue"}'),
          (22,'business_context','business_source_relation','unknown','unknown','{"relation":"missing.orders","domain_key":"revenue"}');
          INSERT INTO rvbbit.kg_edges(graph_id,subject_node_id,object_node_id,predicate_norm) VALUES
          ('db_catalog',10,11,'has_table'),('db_catalog',11,12,'has_column'),
          ('business_context',20,21,'uses_source'),('business_context',20,22,'uses_source');""")
    result=brain.snapshot(db,OWNER)
    assert not result['unavailable'] and not result['graph']['unavailable']
    items={i['ref']:i for i in result['items']}
    assert items['graph:db_catalog']['counts']['db_column']==1
    assert items['graph:business_context']['nodes']==3
    nodes={n['id']:n for n in result['graph']['nodes']}
    assert 'kg:12' not in nodes and nodes['kg:20']['label']=='Revenue'
    bridges=[e for e in result['graph']['edges'] if e['kind']=='same_table']
    assert bridges==[dict(source='kg:21',target='kg:11',kind='same_table')]
    for graph in ['db_catalog','business_context']:
        selected=brain.snapshot(db,OWNER,'graph:'+graph)
        assert all(n['sources']==['graph:'+graph] for n in selected['graph']['nodes'])
        assert not any(e['kind']=='same_table' for e in selected['graph']['edges'])
    # Metadata failure must leave the other families usable.
    brain._cache.clear()
    with db() as c:c.execute('ALTER TABLE rvbbit.kg_nodes DROP COLUMN properties')
    result=brain.snapshot(db,OWNER)
    assert 'Knowledge graph sample' in result['graph']['unavailable']
    assert any(n['id']=='bank:a' for n in result['graph']['nodes'])
