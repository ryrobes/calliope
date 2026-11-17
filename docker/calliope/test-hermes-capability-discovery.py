"""Offline scope, schema, payload and executor contracts; also run during image build."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    from tools.calliope_capability_discovery import enrich, enrich_for_agent
except ImportError:
    spec = importlib.util.spec_from_file_location('discovery', Path(__file__).with_name('hermes-capability-discovery.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    enrich = mod.enrich
    enrich_for_agent = mod.enrich_for_agent


def definition(name, properties=None, required=None):
    return {'type': 'function', 'function': {'name': name, 'description': 'Current tool.',
        'parameters': {'type': 'object', 'properties': properties or {}, 'required': required or []}}}


def receipt(*names):
    return {'discovery_version': 1, 'matches': [dict(kind='cap_mcp_tool', name=n,
            parameters={'stale': True}, next={'tool': 'wrong'}) for n in names]}


class DiscoveryContracts(unittest.TestCase):
    def resolve(self, data, definitions, direct=(), deferred=(), source='mcp__Warehouse__capability_search'):
        return enrich(data, source=source, definitions=definitions, direct_names=set(direct), deferred_names=set(deferred))

    def test_direct_tool_uses_actual_schema_without_changing_originals(self):
        name = 'mcp__Warehouse__run_sql'
        definitions = [definition(name, {'sql': {'type': 'string'}}, ['sql'])]
        original = receipt('run_sql'); before = copy.deepcopy((definitions, original))
        r = self.resolve(original, definitions, direct=[name])['matches'][0]
        self.assertEqual(r['parameters'], definitions[0]['function']['parameters'])
        self.assertEqual(r['invocation'], {'tool': name, 'route': 'direct'})
        self.assertEqual(r['next']['needs_arguments'], ['sql'])
        self.assertEqual((definitions, original), before)

    def test_reference_can_be_called_next_without_search_or_describe(self):
        name = 'mcp__Warehouse__artifact_runtime_help'
        d = receipt('artifact_runtime_help'); d['matches'][0]['suggested_arguments'] = {'topic': 'maps'}
        defs = [definition(name, {'topic': {'type': 'string', 'enum': ['overview','maps']}})]
        r = self.resolve(d, defs, deferred=[name])['matches'][0]
        self.assertEqual(r['next'], {'tool':'tool_call','arguments':{'calls':[{'name':name,'arguments':{'topic':'maps'}}]}})

    def test_restricted_profile_does_not_gain_access_from_warehouse(self):
        name = 'mcp__Warehouse__run_workspace_sql'
        source = receipt('run_workspace_sql')
        source['matches'][0].update(already_available=True, schema_status='included', tool_name=name)
        r = self.resolve(source, [definition(name)])['matches'][0]
        self.assertEqual(r['catalog_status'], 'not_in_agent_scope')
        self.assertEqual(r['schema_status'], 'unavailable')
        for k in ['parameters','next','invocation','already_available','tool_name']:
            self.assertNotIn(k,r)

    def test_namespaces_and_similar_names_do_not_alias(self):
        names = ['mcp__Other__run_sql','mcp__Warehouse__run_sql_multi']
        r = self.resolve(receipt('run_sql','mcp__Other__run_sql'), [definition(n) for n in names], deferred=names)
        self.assertTrue(all(m['catalog_status']=='not_in_agent_scope' for m in r['matches']))

    def test_specialist_namespace_resolves_without_warehouse_guess(self):
        name = 'mcp__Specialist__artifact_runtime_help'
        r = self.resolve(receipt('artifact_runtime_help'), [definition(name)], deferred=[name], source='mcp__Specialist__capability_search')
        self.assertEqual(r['matches'][0]['tool_name'], name)

    def test_large_and_excess_schemas_get_exact_describe_not_partial_schema(self):
        names = [f'mcp__Warehouse__tool{i}' for i in range(6)]
        defs = [definition(n) for n in names]
        defs[0]['function']['parameters']['description'] = 'large '*1000
        r = self.resolve(receipt(*(f'tool{i}' for i in range(6))), defs, deferred=names)
        self.assertEqual(sum('parameters' in m for m in r['matches']),3)
        for i in [0,4,5]:
            self.assertEqual(r['matches'][i]['next'], {'tool':'tool_describe','arguments':{'names':[names[i]]}})
        self.assertFalse(r['session_catalog']['tool_list_changed'])

    def test_large_direct_tool_does_not_require_a_missing_discovery_bridge(self):
        name = 'mcp__Warehouse__run_sql'
        defs = [definition(name, {'sql': {'type':'string', 'description':'x'*2000}}, ['sql'])]
        r = self.resolve(receipt('run_sql'), defs, direct=[name])['matches'][0]
        self.assertEqual(r['schema_status'], 'in_tool_list')
        self.assertEqual(r['next'], {'tool':name, 'needs_arguments':['sql']})
        self.assertNotIn('parameters', r)

    def test_nested_mcp_envelope_keeps_metadata_and_representation(self):
        name='mcp__Warehouse__dashboard_template'
        source=json.dumps({'result':json.dumps(receipt('dashboard_template')), 'metadata': {'id': 'keep'}})
        r=json.loads(self.resolve(source,[definition(name)],deferred=[name]))
        self.assertEqual(r['metadata'],{'id':'keep'})
        self.assertIsInstance(r['result'],str)
        self.assertEqual(json.loads(r['result'])['matches'][0]['next']['tool'],'tool_call')

    def test_native_tool_search_receives_same_contract(self):
        name='mcp__Warehouse__dashboard_template'
        source={'tools': {name:{'description':'Template'}},'results':[{'query':'dashboard','matches':[name]}]}
        r=self.resolve(source,[definition(name)],deferred=[name],source='tool_search')
        self.assertEqual(r['tools'][name]['next']['tool'],'tool_call')
        self.assertEqual(r['results'],source['results'])

    def test_methods_stay_optional_documents_with_scoped_loaders(self):
        name='mcp__Warehouse__read_calliope_playbook'
        source={'discovery_version':1,'matches':[{'kind':'cap_playbook','name':'playbook.example',
                'load_with':{'tool':'read_calliope_playbook','playbook_ref':'playbook.example'}}]}
        r=self.resolve(source,[definition(name,{'playbook_ref':{'type':'string'}},['playbook_ref'])],deferred=[name])
        self.assertEqual(r['matches'][0]['load_with'],source['matches'][0]['load_with'])
        self.assertEqual(r['matches'][0]['loader']['invocation']['tool'],name)
        self.assertIn('optional',r['hint'])

    def test_non_discovery_errors_and_non_json_are_unchanged(self):
        for data in ['plain text', json.dumps({'error':'offline'}), {'result':'not json'}, ['x']]:
            self.assertEqual(self.resolve(data,[]),data)
        d=receipt('run_sql')
        self.assertEqual(self.resolve(d,[],source='mcp__Warehouse__run_sql'),d)

    def test_agent_catalog_uses_current_enabled_disabled_and_deferred_scope(self):
        name = 'mcp__Warehouse__run_sql'
        calls = []
        def definitions(**kw):
            calls.append(kw)
            return [definition(name, {'sql': {'type':'string'}}, ['sql'])]
        agent = SimpleNamespace(enabled_toolsets=['warehouse'], disabled_toolsets=['admin'],
                                valid_tool_names={'tool_call','tool_describe'})
        modules = {'model_tools': SimpleNamespace(get_tool_definitions=definitions),
                   'agent.tool_executor': SimpleNamespace(_tool_search_scoped_names=lambda a:{name})}
        with patch.dict(sys.modules, modules):
            r = enrich_for_agent(agent,'mcp__Warehouse__capability_search',receipt('run_sql'))
            self.assertEqual(r['matches'][0]['invocation']['route'], 'tool_call')
            self.assertEqual(calls, [dict(enabled_toolsets=['warehouse'],disabled_toolsets=['admin'],
                                         quiet_mode=True,skip_tool_search_assembly=True)])
            calls.clear()
            self.assertEqual(enrich_for_agent(agent, 'terminal', 'unchanged'), 'unchanged')
            self.assertEqual(calls, [])

    def test_catalog_failure_preserves_the_original_receipt(self):
        def unavailable(**_): raise RuntimeError('Registry unavailable')
        source = receipt('run_sql')
        with patch.dict(sys.modules, {'model_tools':SimpleNamespace(get_tool_definitions=unavailable),
                       'agent.tool_executor':SimpleNamespace(_tool_search_scoped_names=lambda a:set())}):
            self.assertEqual(enrich_for_agent(SimpleNamespace(), 'mcp__Warehouse__capability_search', source), source)

    def test_executor_enriches_before_archiving_and_wrapping_both_paths(self):
        # This checks the actual patched executor during image build. Locally,
        # the overlay application test supplies HERMES_DISCOVERY_TEST_ROOT.
        import os
        root=Path(os.environ.get('HERMES_DISCOVERY_TEST_ROOT','/opt/hermes'))
        p=root/'agent/tool_executor.py'
        if not p.exists():
            self.skipTest('Run against a patched Hermes tree or in the image build')
        module=ast.parse(p.read_text())
        fn=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='_commit_tool_result')
        text=ast.get_source_segment(p.read_text(),fn)
        self.assertLess(text.index('enrich_for_agent(agent,'),text.index('archive_result(getattr'))
        self.assertLess(text.index('enrich_for_agent(agent,'),text.index('maybe_persist_tool_result('))
        self.assertIn('_commit_tool_result(',p.read_text()[p.read_text().index('def _append_batch_results'):])

        # Execute the real commit function with I/O boundaries stubbed, proving
        # the enriched receipt reaches memory, persistence and the model message.
        name = 'mcp__Warehouse__artifact_runtime_help'
        stages = {}
        agent = SimpleNamespace(session_id='isolated-test', enabled_toolsets=['warehouse'],
            disabled_toolsets=[], valid_tool_names={'tool_call','tool_describe'},
            _touch_activity=lambda _:None, tool_progress_callback=None,
            _subdirectory_hints=SimpleNamespace(check_tool_call=lambda *_:''),
            _tool_result_content_for_active_model=lambda _,result:result)
        def persist(**kwargs):
            stages['persisted'] = kwargs['content']
            return kwargs['content']
        namespace = dict(_is_multimodal_tool_result=lambda _:False, maybe_persist_tool_result=persist,
            get_active_env=lambda _:None, _record_persisted_path_for_stub=lambda *_:None,
            make_tool_result_message=lambda name,content,call_id,**_:dict(name=name,content=content,tool_call_id=call_id),
            _flush_session_db_after_tool_progress=lambda *_a,**_k:True, _safe_callback=lambda *_a,**_k:None,
            _LARGE_TOOL_RESULT_TRIM_CHARS=100000)
        exec(compile('from __future__ import annotations\n'+text, str(p), 'exec'), namespace)
        modules = {
            'tools.calliope_capability_discovery':SimpleNamespace(enrich_for_agent=enrich_for_agent),
            'tools.calliope_middle_memory':SimpleNamespace(archive_result=lambda *args:stages.update(archived=args[-1])),
            'model_tools':SimpleNamespace(get_tool_definitions=lambda **_: [definition(name)]),
            'agent.tool_executor':SimpleNamespace(_tool_search_scoped_names=lambda _:{name})}
        ref = SimpleNamespace(name='mcp__Warehouse__capability_search',args={},call_id='search-1',task_id='test')
        messages = []
        with patch.dict(sys.modules, modules):
            namespace['_commit_tool_result'](agent,messages,ref,json.dumps(receipt('artifact_runtime_help')),
                budget=None, tool_duration=.1,is_error=False,blocked=False,effect_disposition=None)
        self.assertEqual(stages['archived'],stages['persisted'])
        self.assertEqual(messages[0]['content'],stages['persisted'])
        self.assertEqual(json.loads(messages[0]['content'])['matches'][0]['next']['tool'],'tool_call')


if __name__ == '__main__':
    unittest.main()
