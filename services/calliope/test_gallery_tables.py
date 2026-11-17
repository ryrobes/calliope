"""Read real metadata and archived profiles without causing catalog or row writes."""
import json
import pytest
from psycopg.types.json import Jsonb
import gallery_tables as gallery
import server
from test_workspace_sql import db, OWNER  # noqa:F401


def ref(db, name='sales.orders'):
    with db() as c:
        return 'table:'+str(c.execute('SELECT %s::regclass::oid::bigint AS oid',(name,)).fetchone()['oid'])


def observations(db):
    oid = ref(db)[6:]
    with db() as c:
        c.execute('''CREATE TABLE rvbbit.catalog_runs(run_id bigint,graph_id text,status text);
          CREATE TABLE rvbbit.catalog_snapshots(run_id bigint,graph_id text,kind text,schema_name text,rel_name text,col_name text,obj_key text,fingerprint jsonb,captured_at timestamptz);
          CREATE TABLE rvbbit.catalog_docs(graph_id text,kind text,schema_name text,rel_name text,doc text,updated_at timestamptz);
          INSERT INTO rvbbit.catalog_docs VALUES('db_catalog','db_table','sales','orders','Sales order amounts.',now());
          INSERT INTO rvbbit.catalog_runs VALUES(1,'db_catalog','ok'),(2,'db_catalog','ok'),(3,'db_catalog','running');''')
        for run in [1,2,3]:
            c.execute("INSERT INTO rvbbit.catalog_snapshots VALUES(%s,'db_catalog','db_table','sales','orders',NULL,'sales.orders',%s,now()-(4-%s)*interval '1 day')",
                      (run,Jsonb(dict(oid=oid,n_rows=run*100,n_columns=2,sampled=True,n_sampled=50)),run))
            for name in ['id','amount']:
                fp=dict(data_type='integer' if name=='id' else 'numeric',nullable=True,ndv=run*10,null_frac=0.1*run,min=0,max=20,
                        example_values=[10,20],ndv_method='sampled',n_seen=50)
                c.execute("INSERT INTO rvbbit.catalog_snapshots VALUES(%s,'db_catalog','db_column','sales','orders',%s,%s,%s,now())",
                          (run,name,'sales.orders.'+name,Jsonb(fp)))


def test_inventory_and_missing_optional_sources_remain_usable(db,monkeypatch):
    with db() as c:
        c.execute('''CREATE SCHEMA hindsight; CREATE TABLE hindsight.memories(id int);
          CREATE TABLE sales._dlt_loads(id int); ANALYZE sales.orders;''')
    data=gallery.index(db,OWNER)
    assert {t['qualified_name'] for t in data['tables']}=={'sales.orders','sales.order_view'}
    table=gallery.detail(db,OWNER,ref(db))
    assert table['rows']==2 and table['row_source']=='Planner estimate'
    assert table['columns'][1]['name']=='amount'
    assert not table['drift']['comparable']
    assert 'Catalog history' in table['unavailable']
    assert gallery.sample(db,OWNER,ref(db))['rows']==[{'id':'1','amount':'10'},{'id':'2','amount':'20'}]
    for fn in [lambda:gallery.index(db,'other'),lambda:gallery.detail(db,'other',ref(db)),lambda:gallery.sample(db,'other',ref(db))]:
        with pytest.raises(PermissionError):fn()
    monkeypatch.setattr(server,'_ALLOW_SCHEMAS',{'not_sales'})
    assert gallery.index(db,OWNER)['tables']==[]
    with pytest.raises(LookupError):gallery.sample(db,OWNER,ref(db))


def test_saved_history_drift_and_profiles_ignore_incomplete_runs(db):
    observations(db)
    table=gallery.detail(db,OWNER,ref(db))
    assert table['rows']==200 and [h['run_id'] for h in table['history']]==[2,1]
    assert table['catalog_description']['doc']=='Sales order amounts.'
    assert table['drift']['comparable']
    assert {x['field'] for x in table['drift']['changes']}=={'Null fraction','Distinct values'}
    assert table['profiles'][0]['method']=='sampled'
    with db() as c:
        assert c.execute('SELECT count(*) n FROM rvbbit.catalog_snapshots').fetchone()['n']==9
        c.execute("UPDATE rvbbit.catalog_snapshots SET fingerprint=jsonb_set(fingerprint,'{oid}','\"999\"') WHERE kind='db_table'")
    replaced=gallery.detail(db,OWNER,ref(db))
    assert replaced['history']==[] and replaced['catalog_description'] is None and not replaced['profiles']


def test_sample_limits_quoted_identifiers_nulls_and_no_view_execution(db):
    with db() as c:
        c.execute('CREATE TABLE sales."Unusual.table" ("A quote\'" text, payload bytea, empty text)')
        c.execute('INSERT INTO sales."Unusual.table" SELECT repeat(\'x\',10000),decode(\'ff\',\'hex\'),NULL FROM generate_series(1,20)')
        c.execute('CREATE VIEW sales.slow AS SELECT pg_sleep(10)::text AS x')
    sample=gallery.sample(db,OWNER,ref(db,'sales."Unusual.table"'))
    assert len(sample['rows'])==8
    assert len(sample['rows'][0]["A quote'"])==257
    assert sample['rows'][0]['payload']=='[binary]' and sample['rows'][0]['empty'] is None
    assert gallery.sample(db,OWNER,ref(db,'sales.slow'))['rows']==[]
    for value in [None,'sales.orders','table:0','table:99999999999','table:1; SELECT 1']:
        with pytest.raises(ValueError):gallery.sample(db,OWNER,value)


def test_locked_sample_times_out_without_affecting_metadata(db):
    import time
    identity=ref(db)
    with db() as c,c.transaction():
        c.execute('LOCK sales.orders IN ACCESS EXCLUSIVE MODE')
        start=time.monotonic()
        result=gallery.sample(db,OWNER,identity)
        assert time.monotonic()-start<4
        assert not result['rows'] and 'read limit' in result['note']
        table=gallery.detail(db,OWNER,identity)
        assert table['column_count']==2 and 'Storage' in table['unavailable']


def test_business_context_excludes_sql(db):
    with db() as c:
        c.execute('CREATE TABLE rvbbit.business_value_graph_current(graph_id text,name text,summary text,status text,updated_at timestamptz,graph jsonb,source_relations jsonb)')
        c.execute("INSERT INTO rvbbit.business_value_graph_current VALUES('graph-1','Revenue','Order revenue','proposed',now(),%s,'[\"sales.orders\"]')",
                  (Jsonb(dict(source_memberships=[dict(relation='sales.orders',role='event_stream',confidence=.9,rationale='Order grain')],open_questions=['Refund treatment?'],metrics=[dict(event_sql='secret SQL')])) ,))
    table=gallery.detail(db,OWNER,ref(db))
    context=table['business_context'][0]
    assert context['memberships'][0]['role']=='event_stream' and context['questions']==['Refund treatment?']
    assert 'secret SQL' not in json.dumps(context,default=str)


def test_pagination_filters_full_inventory_and_materialized_preview(db):
    with db() as c:
        for i in range(65):
            c.execute(f'CREATE TABLE sales.extra_{i:02}(id int)')
        c.execute('CREATE MATERIALIZED VIEW sales.saved AS SELECT 7 AS answer')
    first=gallery.index(db,OWNER)
    assert len(first['tables'])==60 and first['next_offset']==60
    second=gallery.index(db,OWNER,offset=60)
    assert not ({t['ref'] for t in first['tables']}&{t['ref'] for t in second['tables']})
    search=gallery.index(db,OWNER,query='orders',schema='sales')
    assert search['total']==1 and search['tables'][0]['name']=='orders'
    assert gallery.sample(db,OWNER,ref(db,'sales.saved'))['rows']==[{'answer':'7'}]


def test_ask_pins_table_evidence_and_seeds_prompt_without_running_agent(db):
    import ast,asyncio,uuid
    import calliope
    from pathlib import Path
    from types import SimpleNamespace
    from urllib.parse import urlencode,urlsplit,parse_qs
    identity=ref(db)
    source=ast.parse(Path(calliope.__file__).read_text())
    node=next(n for n in ast.walk(source) if isinstance(n,ast.AsyncFunctionDef) and n.name=='gallery_table_ask');node.decorator_list=[]
    captured=[];new_id=str(uuid.uuid4())
    async def create(*args):return {'id':new_id}
    def surfaces(factory,sid,tid,payload):captured.extend(payload);return [{'id':'pinned-table'}]
    with db() as c:
        c.execute('CREATE TABLE rvbbit.calliope_turns(id uuid,session_id uuid,ordinal int,user_message text,assistant_message text,status text,completed_at timestamptz,turn_kind text)')
    scope=dict(calliope.__dict__,conn_factory=db,config=None,api_owner=lambda request:(OWNER,None),json_response=lambda data,status=200:dict(status=status,data=data),_create_session_record=create,_insert_surfaces=surfaces,urlencode=urlencode)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'handoff','exec'),scope)
    async def body():return {'ref':identity,'question':'Why are there new nulls?'}
    result=asyncio.run(scope['gallery_table_ask'](SimpleNamespace(json=body)))
    assert result['status']==201
    query=parse_qs(urlsplit(result['data']['url']).query)
    assert query['surface']==['pinned-table'] and 'Why are there new nulls?' in query['prompt'][0]
    assert identity in query['prompt'][0] and 'sales.orders' in query['prompt'][0]
    assert captured[0]['payload']['items'][0]['detail']['columns'][1]['name']=='amount'
    with db() as c:
        assert c.execute('SELECT status,turn_kind FROM rvbbit.calliope_turns').fetchall()==[dict(status='complete',turn_kind='action')]


def test_partial_profiles_not_reported_as_drift_and_jobs_are_related(db):
    import workspace_jobs
    from test_workspace_sql import SESSION
    observations(db)
    workspace_jobs.save(db,SESSION,'uses-orders','SELECT * FROM sales.orders',43200)
    with db() as c:
        c.execute("DELETE FROM rvbbit.catalog_snapshots WHERE run_id=1 AND col_name='amount'")
    table=gallery.detail(db,OWNER,ref(db))
    assert not table['drift']['comparable']
    assert table['jobs'][0]['name']=='uses-orders' and table['jobs'][0]['relationship']=='Reads'
    assert 'sql' not in table['jobs'][0]


def test_business_assessments_enrich_search_and_detail_without_history(db):
    with db() as c:
        c.execute('''CREATE TABLE rvbbit.business_value_graph_current(graph_id uuid,name text,status text,
          lifecycle text,usable boolean,updated_at timestamptz,source_relations text[],graph jsonb);
          INSERT INTO rvbbit.business_value_graph_current VALUES
          ('11111111-1111-4111-8111-111111111111','Revenue conversion','accepted','canonical',true,'2026-09-01',ARRAY['sales.orders'],
           '{"source_memberships":[{"relation":"sales.orders","role":"event_stream","rationale":"Completed purchases by customer and day.","confidence":0.94},{"relation":"sales.order_view","rationale":"Wrong membership outside declared sources"}]}'),
          ('22222222-2222-4222-8222-222222222222','Unreviewed alternate','proposed','provisional',false,'2026-09-14',ARRAY['sales.orders'],
           '{"source_memberships":[{"relation":"sales.orders","role":"measure","rationale":"An alternative reading.","confidence":"invalid"}]}');''')
    result = gallery.index(db, OWNER, query='purchases')
    assert [t['qualified_name'] for t in result['tables']] == ['sales.orders']
    primary = result['tables'][0]['business_assessments'][0]
    assert primary['name'] == 'Revenue conversion' and primary['confidence'] == .94
    assert primary['role'] == 'event_stream' and primary['lifecycle'] == 'canonical'
    assert gallery.index(db, OWNER, query='revenue conversion')['total'] == 1
    assert gallery.index(db, OWNER, query='Wrong membership')['total'] == 0
    detail = gallery.detail(db, OWNER, ref(db))
    assert detail['business_assessments'][0] == primary
    assert detail['business_assessments'][1]['confidence'] is None
    assert not detail['history']  # Semantic descriptions do not require a catalog sample.
    with db() as c:
        c.execute("DROP TABLE rvbbit.business_value_graph_current")
    plain = gallery.index(db, OWNER)
    assert plain['tables'] and all(t['business_assessments'] == [] for t in plain['tables'])
    assert 'Business assessments' in plain['unavailable']
