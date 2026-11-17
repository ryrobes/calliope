"""Discovery -> actionable contract, using the six real Sol 6 search receipts."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import threading

import pytest
from mcp.server.fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import capability_discovery as discovery
import server

spec = importlib.util.spec_from_file_location('hermes_discovery', ROOT/'docker/calliope/hermes-capability-discovery.py')
hermes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hermes)


@pytest.fixture
def registry(monkeypatch):
    # No delayed index writes during an offline contract test.
    start = threading.Thread.start
    monkeypatch.setattr(threading.Thread, 'start',
                        lambda thread: None if thread.name == 'capability-tool-sync' else start(thread))
    mcp = FastMCP('discovery-contracts')
    server._register(mcp)
    return mcp


def index(registry):
    return [dict(name=t.name, description=t.description, parameters=t.parameters)
            for t in registry._tool_manager.list_tools()]


class Connection:
    def __init__(self, rows): self.rows, self.calls = rows, []
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def execute(self, query, params=None):
        self.calls.append((query, params))
        self.result = self.rows if 'capability_search_for' in query else []
        return self
    def fetchall(self): return self.result
    def fetchone(self): return {'ok': False}


def prepare(monkeypatch, rows, denied=()):
    c = Connection(rows)
    monkeypatch.setattr(server, '_conn', lambda: c)
    monkeypatch.setattr(server.hermes_skills, 'sync_skill_capabilities', lambda _c: None)
    monkeypatch.setattr(server, '_capability_discovery_subject', lambda *_a, **_k: 'verified@example.com')
    monkeypatch.setattr(server, '_capability_visible_names', lambda _kind, names, **_kw: set(names)-set(denied))
    monkeypatch.setattr(server, '_refresh_runtime_pack_matches', lambda _c, matches: matches)
    monkeypatch.setattr(server, '_capability_data_hits', lambda _query: None)
    return c


SEARCHES = json.loads((HERE/'fixtures/discovery-sol6.json').read_text())['searches']


@pytest.mark.parametrize('sample', SEARCHES, ids=[s['query'] for s in SEARCHES])
def test_real_search_replay_has_a_callable_next_step(monkeypatch, registry, sample):
    c = prepare(monkeypatch, sample['matches'])
    catalog = index(registry)
    result = server.tool_capability_search(sample['query'], _tool_index=catalog)
    definitions = [{'type':'function','function':dict(name='mcp__Warehouse__'+t['name'],
                    description=t['description'],parameters=t['parameters'])} for t in catalog]
    names = {d['function']['name'] for d in definitions}
    enriched = hermes.enrich(result, source='mcp__Warehouse__capability_search', definitions=definitions,
                             direct_names={'tool_call','tool_describe'}, deferred_names=names)
    first = enriched['matches'][0]
    expected = 'dashboard_template' if sample['query']=='dashboard_template' else 'artifact_runtime_help'
    assert first['name'] == expected
    assert first['catalog_status'] == 'available'
    assert first['next']['tool'] == 'tool_call'
    assert first['next']['arguments']['calls'][0]['name'] == 'mcp__Warehouse__'+expected
    if expected == 'artifact_runtime_help':
        assert first['parameters']['properties']['topic']['enum']
        assert first['next']['arguments']['calls'][0]['arguments']['topic'] in ('maps','queries')
    else:
        assert not c.calls, 'Exact lookup must not embed/search/reconcile the catalog'
    assert sum(m['kind'] in discovery.METHOD_KINDS for m in enriched['matches']) <= 2
    assert len(json.dumps(enriched)) < 17000
    assert sorted(i for ids in enriched['groups'].values() for i in ids) == list(range(len(enriched['matches'])))


def test_explicit_kinds_do_not_inject_a_runtime_reference(monkeypatch, registry):
    row=dict(kind='cap_playbook',name='playbook.dashboards',score=1,doc='Method')
    prepare(monkeypatch,[row])
    r=server.tool_capability_search('mapbox playbook',kinds=['cap_playbook'],_tool_index=index(registry))
    assert [m['kind'] for m in r['matches']]==['cap_playbook']
    assert r['groups']=={'methods':[0]}


def test_hidden_reference_and_exact_name_are_not_advertised(monkeypatch, registry):
    prepare(monkeypatch,[],denied={'artifact_runtime_help','dashboard_template'})
    for q in ['mapbox API','dashboard_template']:
        r=server.tool_capability_search(q,_tool_index=index(registry))
        assert r['matches']==[]
        assert 'lookup' not in r


def test_bare_exact_tool_does_not_turn_into_table_search(monkeypatch, registry):
    c=prepare(monkeypatch,[])
    monkeypatch.setattr(server,'_capability_data_hits',lambda _:pytest.fail('Tool name sent to data search'))
    r=server.tool_capability_search('describe_table',_tool_index=index(registry))
    assert r['matches'][0]['name']=='describe_table'
    assert not c.calls


def test_qualified_tool_name_never_guesses_another_server(registry):
    assert discovery.local_candidates(index(registry),'mcp__Other__dashboard_template')==[]


def test_exact_lookup_keeps_the_verified_internal_caller(monkeypatch,registry):
    prepare(monkeypatch,[])
    caller = {'email': 'managed-task@example.com'}
    seen = []
    def visible(_kind, names, **kwargs):
        seen.append(kwargs['caller_override'])
        return set(names)
    monkeypatch.setattr(server, '_capability_visible_names', visible)
    server.tool_capability_search('describe_table', _tool_index=index(registry), _caller_override=caller)
    assert seen == [caller]


def test_tools_are_not_starved_by_method_hits():
    rows=[dict(kind='cap_playbook',name=f'playbook.{i}',score=1,doc='Method') for i in range(12)]
    rows += [dict(kind='cap_mcp_tool',name='do_work',score=.2,doc='Tool')]
    r=discovery.select_rows(rows,'perform a task',8)
    assert r[0]['name']=='do_work'
    assert sum(x['kind']=='cap_playbook' for x in r)==2
    assert len(discovery.select_rows(rows,'methods',8,['cap_playbook']))==8
    assert discovery.select_rows(rows,'build a dashboard without playbooks',8)[0]['name']=='do_work'


def test_registry_contract_replaces_legacy_sql_sibling_advice():
    doc=discovery.LEGACY_SIGNATURE+discovery.LEGACY_SUFFIX
    r=server._capability_search_match(dict(kind='cap_mcp_tool',name='missing',score=1,doc=doc))
    assert 'SQL-side sibling' not in r['doc']
    assert 'external agents' not in r['doc']
    assert r['catalog_status']=='not_in_current_registry'
    assert r['next']['tool']=='get_tool_help'


@pytest.mark.parametrize('topic',['overview','maps','queries','charts','theme','publication','verification'])
def test_runtime_references_are_focused_and_do_not_leak_tokens(monkeypatch,topic):
    monkeypatch.setattr(server,'MAPBOX_TOKEN','pk.private-test-sentinel')
    r=server.tool_artifact_runtime_help(topic)
    text=json.dumps(r)
    assert r['topic']==topic and 'error' not in r
    assert 'pk.private-test-sentinel' not in text
    assert 'template_html' not in r
    assert len(text)<8000
    if topic=='maps':
        assert 'queryRenderedFeatures' in text and 'selectedCell === id ? null' in text
    if topic=='verification':
        assert 'reload=True' in text


def test_unavailable_maps_and_unknown_reference(monkeypatch):
    monkeypatch.setattr(server,'MAPBOX_TOKEN','')
    assert server.tool_artifact_runtime_help('maps')['available'] is False
    assert server.tool_artifact_runtime_help('invented')['error']['code']=='INVALID_TOPIC'


def test_registered_reference_schema_has_only_its_topic(registry):
    t=registry._tool_manager._tools['artifact_runtime_help']
    assert set(t.parameters['properties'])=={'topic'}
    assert t.parameters['properties']['topic']['default']=='overview'
    assert 'maps' in t.parameters['properties']['topic']['enum']


def test_mcp_search_uses_live_registry_contract(monkeypatch,registry):
    prepare(monkeypatch,[])
    monkeypatch.setattr(server,'_logged',lambda _name,_args,fn:fn())
    r=asyncio.run(registry._tool_manager._tools['capability_search'].fn('artifact_runtime_help'))
    assert r['matches'][0]['parameters']['properties']['topic']['enum']
    assert r['lookup']=='exact_registry_name'
