from types import SimpleNamespace
import pytest
import meaning_data as md

REF = {'kind': 'meaning', 'area': 'subject:service', 'node': 'relation:data.claims'}

@pytest.mark.parametrize('body', [
 {'reference': REF, 'sql': 'SELECT 1'}, {'reference': REF, 'offset': -1},
 {'reference': REF, 'offset': True}, {'reference': REF, 'aggregate': 'drop'},
 {'reference': REF, 'aggregate': 'sum'}, {'reference': REF, 'filter': {'field': 'branch'}},
 {'reference': REF, 'filter': {'field': 'branch', 'value': ['x']}},
])
def test_options_reject_executable_or_unbounded_input(body):
    with pytest.raises(ValueError): md.options(body)

def test_exploration_is_resolved_authorized_readonly_and_parameterized(monkeypatch):
    calls = []
    class Conn:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self, query, params=()):
            text = query if isinstance(query,str) else query.as_string()
            calls.append((text,params))
            if 'pg_class' in text: rows=[{'nspname':'data','relname':'claims','oid':42}]
            elif 'pg_attribute' in text: rows=[{'name':'branch','type':'text','display_type':'text'},{'name':'amount','type':'numeric','display_type':'numeric'}]
            elif ' AS key' in text: rows=[{'key':'A','value':'120.00','records':'2'}]*21
            else: rows=[{'branch':'A','amount':'60.00'}]*41
            return SimpleNamespace(fetchall=lambda:rows,fetchone=lambda:rows[0])
    def conn(**kwargs):
        assert kwargs == {'read_only':True,'role':'reader'}
        return Conn()
    monkeypatch.setattr(md.meaning_graph,'resolve',lambda s,a,r: {'node':{'kind':'relation','source':'data.claims'}})
    server=SimpleNamespace(_conn=conn,_session_pg_role=lambda a:'reader')
    value="A'; DROP TABLE claims; --"
    result=md.explore(server,'person',{'reference':REF,'dimension':'branch','measure':'amount','aggregate':'sum','filter':{'field':'branch','value':value}})
    assert len(result['rows'])==40 and result['has_more']
    assert len(result['groups'])==20 and result['groups_more']
    select=next((q,p) for q,p in calls if 'LIMIT 41' in q)
    assert value not in select[0] and select[1]==[value,0]
    group=next(q for q,p in calls if ' AS key' in q)
    assert 'SUM("amount")' in group and 'WHERE' not in group
    assert result['scope']=='live source query'

def test_unauthorized_reference_fails_before_any_data_connection(monkeypatch):
    def denied(*args):raise ValueError('unavailable')
    monkeypatch.setattr(md.meaning_graph,'resolve',denied)
    with pytest.raises(ValueError):md.explore(SimpleNamespace(),'person',{'reference':REF})


def test_shelves_group_multiple_dimensions_rollup_rules_and_date_drilldown(monkeypatch):
    from decimal import Decimal
    calls=[]
    fields=[{'name':n,'type':t,'display_type':t} for n,t in [('branch','text'),('day','date'),('amount','numeric')]]
    class Conn:
        def execute(self, query, params=()):
            text=query if isinstance(query,str) else query.as_string()
            calls.append((text,params))
            rows=[{'branch':None,'day':'2026-10-01 00:00:00+00','amount':Decimal('9007199254740993.12')}] if 'meaning_rollup' in text else []
            return SimpleNamespace(fetchall=lambda:rows,description=[SimpleNamespace(name=n,type_code=t) for n,t in [('branch',25),('day',1184),('amount',1700)]])
    spec={'groupBy':[{'column':{'name':'branch'}},{'column':{'name':'day'},'grain':'month'}],
          'measures':[{'id':'m','alias':'amount','column':{'name':'amount'},'agg':'sum'}],
          'filters':[{'column':{'name':'branch'},'op':'neq','value':"O'Reilly"}]}
    req=md.options({'reference':REF,'spec':spec,'filter':[{'field':'branch','value':None},{'field':'day','grain':'month','value':'2026-10-01 00:00:00+00'}]})
    result=md.explore_shelves(None,'person',Conn(),{'kind':'relation','source':'data.claims'},fields,False,md.sql.Identifier('data','claims'),req)
    assert result['rollup']['rows'][0]['amount']=='9007199254740993.12'
    assert result['rollup']['column_types'][-1]=={'name':'amount','oid':1700}
    aggregate=next(q for q,p in calls if 'meaning_rollup' in q)
    assert 'GROUP BY "branch", date_trunc(\'month\', "day")' in aggregate
    assert "O''Reilly" in aggregate and 'LIMIT 201' in aggregate
    rows,params=next((q,p) for q,p in calls if 'LIMIT 41' in q)
    assert "date_trunc('month', \"day\") IS NOT DISTINCT FROM %s" in rows
    assert params==[None,'2026-10-01 00:00:00+00',0]
    assert 'IS NOT DISTINCT FROM' not in aggregate


def test_shelves_preserve_cube_semantics_and_bound_distinct_probe(monkeypatch):
    from test_cube_shelf import DATASET, DEFINITION
    monkeypatch.setattr(md.meaning_graph,'inspect_object',lambda *a:{'detail':DEFINITION})
    calls=[]
    class Conn:
        def execute(self, query, params=()):
            calls.append(query)
            return SimpleNamespace(fetchall=lambda:[{'value':None}]*201)
    fields=[{'name':c['name'],'display_type':c['type']} for c in DATASET['columns']]
    req=md.options({'reference':REF,'spec':{'groupBy':[],'measures':[]},'probe':{'field':'region'}})
    result=md.explore_shelves(None,'person',Conn(),{'kind':'cube','source':'cubes.retail'},fields,False,md.sql.Identifier('cubes','retail'),req)
    assert len(result['values'])==200 and result['truncated']
    assert 'LIMIT 201' in calls[0]
    req['spec']['measures']=[{'id':'bad','alias':'bad','column':{'name':'distinct_people'},'agg':'sum'}]
    with pytest.raises(ValueError,match='supported aggregation'):
        md.explore_shelves(None,'person',Conn(),{'kind':'cube','source':'cubes.retail'},fields,False,md.sql.Identifier('cubes','retail'),req)


@pytest.mark.parametrize('body',[
 {'spec':{'sql':'SELECT 1'}}, {'spec':[]}, {'probe':{'field':'branch'}},
 {'spec':{},'probe':{'field':'branch','search':42}}, {'filter':[{'field':'branch','value':None}]},
 {'spec':{},'filter':[{'field':'day','value':'now','grain':'pg_sleep'}]},
])
def test_shelf_request_rejects_unstructured_inputs(body):
    with pytest.raises(ValueError):md.options({'reference':REF,**body})
