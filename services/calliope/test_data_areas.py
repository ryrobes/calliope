"""Published area discovery, scope, and context survive the exploration lifecycle."""
import json

import pytest
import data_areas
import data_explorer as data
import server
from test_workspace_sql import db, OWNER  # noqa: F401
from test_data_explorer import explorer, build  # noqa: F401


@pytest.fixture
def areas(db):
    with db() as c:
        c.execute('''CREATE TABLE rvbbit.meaning_published_domains(domain_key text,name text,about text,
          members text[],core text[],roles jsonb,areas jsonb,questions jsonb,candidate_metrics jsonb,candidate_breakdowns jsonb,
          gaps jsonb,dossier_at timestamptz,updated_at timestamptz);
          CREATE TABLE rvbbit.cube_catalog(name text,sql text);
          CREATE TABLE rvbbit.metric_catalog(name text,sql text)''')
        c.execute('''INSERT INTO rvbbit.meaning_published_domains VALUES
          ('subject:sales','Sales & fulfillment','Orders and shipments; joining is not verified.',
          ARRAY['sales.orders','sales.order_view'],ARRAY['sales.orders'],'{}',
          '[{"name":"Orders","about":"Order activity","tables":["sales.orders"]}]',
          '["What is total sales?"]','[{"name":"Profit margin","formula":"profit / sales"}]',
          '[{"name":"Sales mix","purpose":"Compare regional contributions.","measures":["SUM(amount)"],"dimensions":["region"],"grain":"Region"}]',
          '["Who owns fulfillment data?"]',now(),now())''')
        c.execute('''INSERT INTO rvbbit.cube_catalog VALUES
          ('sales','WITH x AS (SELECT * FROM sales.orders) SELECT * FROM x'),
          ('secret','SELECT * FROM private.secret');
          INSERT INTO rvbbit.metric_catalog VALUES
          ('sales_total','SELECT sum(amount) FROM sales.orders'),
          ('cube_total','SELECT sum(amount) FROM cubes.sales'),
          ('leak','SELECT * FROM sales.orders JOIN private.secret USING(id)')''')
    return lambda **kwargs: db()


def test_area_catalog_requires_all_member_access_and_searches_dossier(areas):
    assert data_areas.catalog(areas, {'sales.orders': {}}) == []
    catalog = data_areas.catalog(areas, {'sales.orders': {}, 'sales.order_view': {}})
    assert len(catalog) == 1
    assert data_areas.cards(catalog, 'fulfillment')[0]['table_count'] == 2
    assert data_areas.cards(catalog, 'Who owns')[0]['gap_count'] == 1
    assert data_areas.cards(catalog, 'unrelated') == []
    assert data_areas.cards(catalog, 'revenue', ['sales.orders'])[0]['name'] == 'Sales & fulfillment'
    assert 'candidate_metrics' not in data_areas.cards(catalog, '')[0]
    card = data_areas.cards(catalog, '')[0]
    assert card['subtopics'] == ['Orders'] and card['sample_question'] == 'What is total sales?'
    assert card['metric_idea_count'] == 1 and card['breakdown_count'] == 1
    assert data_areas.cards(catalog, 'regional contributions')[0]['key'] == 'subject:sales'


def test_related_definitions_use_sql_lineage_and_exclude_inaccessible_sources(areas):
    available = {k: {} for k in ['sales.orders','sales.order_view','cubes.sales','cubes.secret']}
    area = data_areas.catalog(areas, available)[0]
    cubes, metrics = data_areas.related(areas, area, available)
    assert cubes == ['cubes.sales']
    assert set(metrics) == {'sales_total','cube_total'}
    assert data_areas.sql_relations('invalid sql (((') == set()


def test_authoritative_area_handoff_and_scope_rechecked_in_service(areas, monkeypatch):
    available = [{'relation': 'sales.orders','schema':'sales','name':'orders'},
                 {'relation': 'sales.order_view','schema':'sales','name':'order_view'}]
    monkeypatch.setattr(data,'inventory',lambda *args:available)
    monkeypatch.setattr(server,'_conn',areas)
    monkeypatch.setattr(server,'_session_pg_role',lambda sub:None)
    monkeypatch.setattr(server,'_meaning_summary',lambda *a,**kw:{'joins_to':['id → sales.order_view','id → private.secret']})
    monkeypatch.setattr(server,'_metric_catalog_snapshot',lambda *a,**kw:{'metrics':[]})
    monkeypatch.setattr(server,'tool_run_sql',lambda *a,**kw:{'rows':[],'columns':[]})
    monkeypatch.setattr(server,'tool_ask_brain',lambda *a,**kw:{'matches':[]})
    def service(op,args): return server._calliope_data_service(op,args,'reader',OWNER)
    detail = service('area',{'key':'subject:sales'})
    assert len(detail['datasets']) == 2 and len(detail['relationships']) == 2
    assert all('private.secret' not in r['description'] for r in detail['relationships'])
    handoff = service('area_handoff',{'key':'subject:sales','kind':'gap','index':0,'prompt':'fake'})
    assert 'Who owns fulfillment data?' in handoff['prompt']
    assert 'subject:sales' in handoff['prompt'] and 'fake' not in handoff['prompt']
    metric = service('area_handoff',{'key':'subject:sales','kind':'metric','index':0,'formula':'fake'})
    assert 'Proposed calculation: profit / sales' in metric['prompt']
    assert 'not an approved or saved metric definition' in metric['prompt'] and 'fake' not in metric['prompt']
    breakdown = service('area_handoff',{'key':'subject:sales','kind':'breakdown','index':0,'purpose':'fake'})
    assert 'Compare regional contributions.' in breakdown['prompt'] and 'fake' not in breakdown['prompt']
    assert 'Group by: region' in breakdown['prompt'] and 'named grouped analysis' in breakdown['prompt']
    context = service('context',{'sources':['sales.orders'],'question':'sales','area_key':'subject:sales'})
    assert context['meaning_area']['candidate_breakdowns'][0]['name']=='Sales mix'
    assert context['meaning_area']['gaps'] == ['Who owns fulfillment data?']
    context = service('context',{'sources':['sales.orders'],'question':'sales','area_key':'subject:sales','area_topic':'Orders'})
    assert context['meaning_area']['selected_subarea'] == 'Orders'
    with pytest.raises(ValueError,match='subtopic changed'):
        service('context',{'sources':['sales.order_view'],'question':'sales','area_key':'subject:sales','area_topic':'Orders'})
    available.pop()  # Membership changed since discovery: deny both context and handoff.
    for op,args in [('area',{'key':'subject:sales'}), ('area_handoff',{'key':'subject:sales','kind':'gap','index':0}),
                    ('area_handoff',{'key':'subject:sales','kind':'metric','index':0}),
                    ('area_handoff',{'key':'subject:sales','kind':'breakdown','index':0}),
                    ('context',{'sources':['sales.orders'],'question':'sales','area_key':'subject:sales'})]:
        with pytest.raises(PermissionError):service(op,args)


@pytest.mark.parametrize('kind,index',[('gap',-1),('gap',True),('gap',1),('metric',-1),('metric',True),('metric',1),('breakdown',-1),('breakdown',True),('breakdown',1),('unknown',0)])
def test_handoff_rejects_unpublished_questions(kind,index):
    with pytest.raises(ValueError):data_areas.handoff({'gaps':['One gap'],'candidate_metrics':[{'name':'Idea'}]},kind,index)


def test_area_context_survives_clarification_and_selected_model_accounting(explorer):
    exp,operations,charges,responses = explorer
    original = exp.service
    refs = ['sales.orders'] + [f'sales.table_{i}' for i in range(5)]
    captured = []
    area = {'key':'subject:sales','name':'Sales','gaps':['No verified join'],
            'candidate_metrics':[{'name':'Possible margin'}]}
    def service(op,args,subject,owner):
        result = original(op,args,subject,owner)
        if op == 'context':
            assert args['area_key'] == area['key']
            result.update(meaning_area=area)
        return result
    exp.service = service
    client = exp.client_factory()
    complete = client.complete_json
    def recording(**kwargs):
        captured.append(json.loads(kwargs['user']))
        return complete(**kwargs)
    client.complete_json = recording
    responses[:] = [json.dumps({'clarification':{'question':'Which period?','options':['All time']}}),
                    json.dumps({'question':'Total sales for all time'}),
                    json.dumps({'title':'Sales','sql':'SELECT sum(amount) FROM sales.orders','assumptions':[]})]
    draft = build(explorer,sources=refs,area_key=area['key'])
    assert draft['status'] == 'clarification'
    refined = exp.refine(OWNER,'reader',draft['id'],'All time')
    assert refined['status'] == 'ready'
    assert refined['context']['meaning_area'] == area
    assert captured[-1]['context']['meaning_area']['gaps'] == ['No verified join']
    assert [c[0][3] for c in charges] == ['data.generate','data.refine','data.generate']
    assert all(op[1]['area_key']==area['key'] for op in operations if op[0]=='context')
    with pytest.raises(ValueError,match='one to four'):build(explorer,sources=refs)
