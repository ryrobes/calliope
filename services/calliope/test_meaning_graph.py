"""Meaning graph contracts: publication, identity, honest evidence, and bounds."""
from types import SimpleNamespace

import pytest
import meaning_graph as mg
from app_actions import ActionError


@pytest.fixture
def packet():
    return ({'domain_key': 'subject:service', 'name': 'Service', 'about': 'Published business meaning',
             'members': ['data.jobs', 'data.parts'], 'core': ['data.jobs'], 'candidate_metrics': [
                 {'name': 'Jobs completed', 'formula': 'COUNT(*)', 'why': 'Definition idea'}],
             'candidate_breakdowns': [], 'updated_at': '2026-10-09'},
            [{'relation': 'data.jobs', 'business_name': 'Visits', 'n_rows': 20}, {'relation': 'data.parts', 'n_rows': 4}],
            [{'name': 'jobs_completed', 'version': 2, 'labels': {'status': 'implied', 'source_objects': ['data.jobs'],
               'probe': {'ok': True, 'value': 12}, 'registered_at': '2026-10-01'}, 'history': []}],
            [{'slug': 'field', 'name': 'Field notebook', 'latest_version': 3, 'runtime_kind': 'a2ui',
              'dependencies': [{'kind': 'table', 'object_ref': 'data.jobs', 'source': 'sql-literal'}]}],
            [{'from_relation': 'data.jobs', 'to_relation': 'data.parts', 'state': 'unverified',
              'basis': 'naming', 'from_column': 'part_id', 'to_column': 'id'}])


def test_graph_uses_typed_edges_and_provisional_meaning(packet):
    result = mg.project(*packet)
    nodes = {n['id']: n for n in result['nodes']}
    assert nodes['area:subject:service']['status'] == 'provisional'
    assert nodes['metric:jobs_completed']['status'] == 'implied'
    assert nodes['metric:jobs_completed']['preview'] == {'value': 12, 'label': 'Registration sample', 'at': '2026-10-01'}
    assert not any(n['kind'] == 'idea' for n in nodes.values())
    assert any(e['label'] == 'used by' and e['from'] == 'relation:data.jobs' for e in result['edges'])
    join = next(e for e in result['edges'] if e['label'] == 'joins to')
    assert join['state'] == 'unverified' and join['evidence']['basis'] == 'naming'
    assert all(e['from'] in nodes and e['to'] in nodes for e in result['edges'])


def test_graph_never_invents_a_metric_trend(packet):
    assert 'series' not in mg.project(*packet)['nodes'][3]
    packet[2][0]['history'] = [{'value': 7, 'observed_at': 'today'}, {'value': 5, 'observed_at': 'yesterday'}]
    metric = next(n for n in mg.project(*packet)['nodes'] if n['kind'] == 'metric')
    assert metric['series'] == [5, 7] and metric['preview']['label'] == 'Saved daily snapshot'


def test_pg_role_does_not_inherit_service_account_aggregate_samples(packet):
    result = mg.project(*packet, role='warehouse_reader')
    assert all('preview' not in n for n in result['nodes'])


def test_bounded_expansion_keeps_focused_object_and_stable_ids(packet):
    packet[1].extend({'relation': 'data.extra' + str(i)} for i in range(30))
    first = mg.project(*packet, limit=4)
    focused = mg.project(*packet, limit=4, focus='relation:data.extra29')
    assert first['complete'] and not first['more']
    assert len([n for n in focused['nodes'] if n['kind'] == 'relation']) == 32
    assert any(n['id'] == 'relation:data.extra29' for n in focused['nodes'])
    assert first['nodes'][0]['reference'] == focused['nodes'][0]['reference']


def test_unregistered_ideas_are_not_executable_metric_nodes(packet):
    result = mg.project(packet[0], packet[1], [], [], [])
    idea = next(n for n in result['nodes'] if n['kind'] == 'idea')
    assert idea['status'] == 'suggested' and 'metric' not in idea
    assert idea['id'] == mg.stable('idea', 'subject:service', 'Jobs completed')


@pytest.mark.parametrize('bad', [{}, {'kind': 'meaning', 'area': '', 'node': 'metric:x'},
    {'kind': 'meaning', 'area': 'a', 'node': 'sql:DELETE'}, {'kind': 'meaning', 'area': 'a', 'node': 'metric:x', 'sql': 'SELECT 1'},
    {'kind': 'meaning', 'area': 'a\n', 'node': 'metric:x'}])
def test_reference_rejects_ambiguous_or_executable_identity(bad):
    with pytest.raises(ValueError): mg.reference(bad)


def test_missing_node_does_not_fall_back_to_another_record(monkeypatch, packet):
    monkeypatch.setattr(mg, 'graph', lambda *a: mg.project(*packet))
    with pytest.raises(ActionError) as e:
        mg.resolve(None, 'person', {'kind': 'meaning', 'area': 'subject:service', 'node': 'metric:removed'})
    assert e.value.status == 404


def test_dossier_is_hidden_when_role_cannot_read_all_members(packet):
    class C:
        def execute(self, sql, params=None):
            self.sql = sql
            return self
        def fetchall(self):
            return [packet[0]] if 'meaning_published_domains' in self.sql else [{'relation': 'data.jobs'}]
    domains, role = mg.permitted_areas(SimpleNamespace(_session_pg_role=lambda _: 'reader'), C(), 'person')
    assert domains == [] and role == 'reader'


def test_counts_are_hints_and_do_not_hide_business_metrics():
    generic = {'name': 'rows', 'labels': {'formula': 'count ( * )'}}
    distinct = {'name': 'customers', 'labels': {'formula': 'COUNT(DISTINCT customer_id)'}}
    conditional = {'name': 'completed', 'labels': {'formula': "COUNT(*) FILTER (WHERE status = 'done')"}}
    assert mg.metric_presentation(generic)[0] == 'count'
    assert mg.metric_presentation(distinct)[0] == 'measure'
    assert mg.metric_presentation(conditional)[0] == 'measure'


def test_initial_catalog_prefers_measures_but_keeps_alias_identities(packet):
    metrics = [
        {'name': 'a_rows', 'labels': {'formula': 'COUNT(*)', 'source_objects': ['data.jobs']}},
        {'name': 'z_cost', 'labels': {'formula': 'SUM(cost)', 'source_objects': ['data.jobs']}},
        {'name': 'zz_cost_alias', 'labels': {'formula': 'SUM(cost)', 'source_objects': ['data.jobs']}},
    ]
    result = mg.project(packet[0], packet[1], metrics, [], [])
    nodes = [n for n in result['nodes'] if n['kind'] == 'metric']
    assert [n['metric'] for n in nodes] == ['z_cost', 'zz_cost_alias', 'a_rows']
    assert nodes[0]['display_group'] == nodes[1]['display_group']
    assert nodes[0]['reference'] != nodes[1]['reference']
    assert len(nodes) == 3


def test_catalog_distinguishes_loaded_available_and_hard_caps(packet):
    packet[1].extend({'relation': 'data.extra' + str(i)} for i in range(50))
    packet[0]['candidate_breakdowns'] = [{'name': 'By ' + str(i)} for i in range(10)]
    result = mg.project(*packet, limit=12)
    catalog = {c['kind']: c for c in result['catalog']}
    assert catalog['relation'] == {'kind': 'relation', 'loaded': 52, 'available': 52, 'cap': 52,
                                   'available_is_lower_bound': False, 'can_load': False}
    assert catalog['breakdown']['loaded'] == 10 and not catalog['breakdown']['can_load']
    larger = mg.project(*packet, limit=40)
    sources = next(c for c in larger['catalog'] if c['kind'] == 'relation')
    assert sources['loaded'] == 52 and sources['available'] == 52 and not sources['can_load']


@pytest.mark.parametrize('role', [None, 'warehouse_reader'])
def test_inspection_is_version_scoped_metadata_not_metric_execution(monkeypatch, role):
    calls = []
    node = {'id': 'metric:cost', 'kind': 'metric', 'metric': 'cost', 'version': 3}
    monkeypatch.setattr(mg, 'resolve', lambda *a: {'node': node})
    class C:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, sql, params=None):
            calls.append((sql, params)); return self
        def fetchone(self):
            return {'sql': 'SELECT expensive_function()', 'params': {'days': 30},
                    'grain': 'per day', 'labels': {'window': 'last {days} days', 'why': 'Cost', 'probe': {'secret': 1}}}
        def fetchall(self):
            return [{'value': {'value': 24}, 'observed_at': 'now', 'data_as_of': 'then', 'time': 12}]
    server = SimpleNamespace(_session_pg_role=lambda _: role, _conn=lambda **kw: C())
    d = mg.inspect_object(server, 'person', {})['detail']
    assert d['defaults'] == {'days': 30} and d['window'] == 'last {days} days'
    assert 'probe' not in d and 'labels' not in d
    assert all('expensive_function' not in sql for sql, _ in calls)
    assert next(params for sql, params in calls if 'metric_defs' in sql) == ('cost', 3)
    history = [(sql, params) for sql, params in calls if 'metric_default_observations' in sql]
    if role:
        assert not history and not d['observations'] and d['observations_restricted']
    else:
        assert history[0][1] == ('cost', 3) and "metric_version=%s" in history[0][0]
        assert d['observations'][0]['value'] == 24 and d['observation_scope'] == {'days': 30}


def test_source_inspection_is_bounded_and_does_not_load_row_values(monkeypatch):
    calls = []
    monkeypatch.setattr(mg, 'resolve', lambda *a: {'node': {'kind': 'relation', 'source': 'data.jobs'}})
    class C:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, sql, params=None): calls.append((sql,params)); return self
        def fetchall(self): return [{'col_name': 'field_' + str(i)} for i in range(81)]
    server = SimpleNamespace(_session_pg_role=lambda _: None, _conn=lambda **kw: C())
    d = mg.inspect_object(server, 'person', {})['detail']
    assert len(d['fields']) == 80 and d['fields_more']
    assert calls[-1][1] == ('data.jobs',)
    assert 'LIMIT 81' in calls[-1][0]
    assert not any(token in calls[-1][0] for token in ['top_values', 'profile', 'ndv', 'null_frac'])


def test_inspection_checks_authorization_before_reading_extra_metadata(monkeypatch):
    def denied(*args): raise ActionError('NOT_FOUND', 'Unavailable', 404)
    monkeypatch.setattr(mg, 'resolve', denied)
    with pytest.raises(ActionError):
        mg.inspect_object(None, 'person', {})


def test_cube_is_visible_between_sources_and_metrics_and_shared_source_is_not_membership(packet):
    packet[2][0]['sources']=['cubes.job_cost']
    cube={'relation':'cubes.job_cost','name':'Job costs','grain':'job_id','sources':['data.jobs','data.parts']}
    result=mg.project(*packet,cubes=[cube])
    nodes={n['id']:n for n in result['nodes']}
    assert nodes['cube:cubes.job_cost']['grain']=='job_id'
    assert any(e['from']=='relation:data.jobs' and e['to']=='cube:cubes.job_cost' for e in result['edges'])
    assert any(e['from']=='cube:cubes.job_cost' and e['to']=='metric:jobs_completed' for e in result['edges'])
    other=dict(cube,relation='cubes.part_stock',sources=['data.parts'])
    shared=mg.project(*packet,cubes=[other])
    edge=next(e for e in shared['edges'] if e['from'].startswith('area:') and e['to']=='cube:cubes.part_stock')
    assert edge['state']=='association' and edge['label']=='shares sources with'
    assert mg.reference(nodes['cube:cubes.job_cost']['reference'])==nodes['cube:cubes.job_cost']['reference']


def test_source_beyond_old_limit_still_has_its_metric_edge(packet):
    packet[1].extend({'relation':'data.extra'+str(i)} for i in range(70))
    packet[2][0]['sources']=['data.extra69']
    result=mg.project(*packet,limit=4)
    assert any(e['from']=='relation:data.extra69' and e['to']=='metric:jobs_completed' for e in result['edges'])
    assert result['complete']


def test_cube_inspector_uses_registered_column_docs_and_never_runs_cube_sql(monkeypatch):
    calls=[]
    monkeypatch.setattr(mg,'resolve',lambda *a:{'node':{'kind':'cube','source':'cubes.costs','cube':'costs','version':2}})
    class C:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,sql,params=None):calls.append((sql,params));self.sql=sql;return self
        def fetchall(self):return [{'col_name':'amount','what':'Recorded cost; additive by job'}] if 'cube_columns' in self.sql else []
        def fetchone(self):return {'sql':'SELECT expensive_cube_function()','grain':'job_id'}
    server=SimpleNamespace(_session_pg_role=lambda _:None,_conn=lambda **kw:C())
    detail=mg.inspect_object(server,'person',{})['detail']
    assert detail['fields'][0]['col_name']=='amount'
    assert detail['sql']=='SELECT expensive_cube_function()'
    assert not any('expensive_cube_function' in sql for sql,_ in calls)


def test_a_core_dimension_does_not_make_every_cube_or_metric_a_business_member(packet):
    cube={'relation':'cubes.service_other','name':'Other','grain':'claim_id','sources':['data.jobs']}
    result=mg.project(*packet,cubes=[cube])
    relevant=[e for e in result['edges'] if e['from'].startswith('area:') and e['to'] in ('cube:cubes.service_other','metric:jobs_completed')]
    assert len(relevant)==2 and all(e['state']=='association' for e in relevant)
    cube['labels']={'subject':packet[0]['domain_key']}
    packet[2][0]['labels']['subject']=packet[0]['domain_key']
    result=mg.project(*packet,cubes=[cube])
    relevant=[e for e in result['edges'] if e['from'].startswith('area:') and e['to'] in ('cube:cubes.service_other','metric:jobs_completed')]
    assert all(e['state']!='association' for e in relevant)


def test_dossier_groups_and_neighbours_ride_beside_the_nodes(packet):
    packet[0]['areas'] = [{'name': 'Visits', 'about': 'Field work', 'tables': ['data.jobs', 'data.missing']},
                          {'name': 'Empty', 'tables': []}, {'name': 'Dup', 'tables': ['data.jobs']}]
    other = {'domain_key': 'subject:billing', 'name': 'Billing', 'members': ['bill.invoices', 'data.parts']}
    ties = mg.area_ties(packet[0], [other], [], local=['data.jobs', 'data.parts'])
    result = mg.project(*packet, neighbors=ties)
    group, = result['groups']
    assert group['title'] == 'Visits' and group['members'] == ['relation:data.jobs']
    assert group['id'] == mg.stable('group', 'subject:service', 'Visits')
    neighbour, = result['neighbors']
    assert neighbour['reference'] == mg.ref('subject:billing', 'area:subject:billing')
    assert neighbour['ties'] == [{'local': 'relation:data.parts', 'remote': 'data.parts', 'label': 'shares this source',
                                  'state': 'shared', 'basis': 'A member of both published areas'}]
    assert all(n['kind'] != 'group' for n in result['nodes']), 'groups are structure, not objects'
    assert result['nodes'][0]['groups'] == [{'id': group['id'], 'title': 'Visits', 'size': 2}]


def test_area_ties_prefer_evidence_and_never_offer_a_contradicted_join():
    here = {'domain_key': 'a', 'name': 'A', 'members': ['s.orders', 's.items']}
    joined = {'domain_key': 'b', 'name': 'B', 'members': ['s.stores']}
    refuted = {'domain_key': 'c', 'name': 'C', 'members': ['t.people']}
    links = [{'from_relation': 's.orders', 'from_column': 'store_id', 'to_relation': 's.stores', 'to_column': 'id', 'state': 'verified', 'basis': 'recognition'},
             {'from_relation': 't.people', 'from_column': 'id', 'to_relation': 's.items', 'to_column': 'id', 'state': 'contradicted'}]
    ties = {n['area']: n['ties'] for n in mg.area_ties(here, [joined, refuted], links)}
    assert ties['b'] == [{'local': 'relation:s.orders', 'remote': 's.stores', 'state': 'verified', 'label': 'joins on store_id → id',
                          'basis': 'recognition', 'match_rate': None}]
    assert 'c' not in ties, 'a refuted join is evidence of no tie'
    # The same schema is offered only without stronger evidence, and only while it still discriminates.
    crowd = [{'domain_key': k, 'name': k, 'members': ['s.' + k]} for k in 'wxyz']
    assert [t['label'] for n in mg.area_ties(here, crowd[:2], []) for t in n['ties']] == ['same schema s', 'same schema s']
    assert mg.area_ties(here, crowd, []) == []


def test_landscape_links_one_edge_per_related_pair():
    a = {'domain_key': 'a', 'name': 'A', 'members': ['s.orders', 'shared.calendar']}
    b = {'domain_key': 'b', 'name': 'B', 'members': ['shared.calendar']}
    c = {'domain_key': 'c', 'name': 'C', 'members': ['other.thing']}
    edges = mg.landscape_links([a, b, c], [])
    assert [(e['from'], e['to'], e['label'], e['state']) for e in edges] == [('area:a', 'area:b', 'shares this source', 'shared')]
