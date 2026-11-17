"""Provider chunk -> real native callback -> durable replay, without inference."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get('HERMES_SOURCE_ROOT', '/opt/hermes'))
sys.path.insert(0, str(ROOT))
from gateway.calliope_artifact_progress import BuildObserver, observe_chunk, observe_tool, partial_string
from gateway.calliope_stage_events import StageEvents
from artifact_progress import draft_html, public_progress
spec = importlib.util.spec_from_file_location('receipt_tests', Path(__file__).with_name('test-hermes-stage-events.py'))
receipts = importlib.util.module_from_spec(spec); spec.loader.exec_module(receipts)


class BuildTests(unittest.TestCase):
    def test_saved_app_json_is_forwarded_as_definition_and_partial_edits_are_not(self):
        out=[]
        agent=SimpleNamespace(tool_progress_callback=lambda *a,**k:out.append(k['progress']))
        doc={'schema':'calliope.app.v1','data':{'state':{}},'components':[]}
        ref=SimpleNamespace(name='write_file',args={'path':'/safe/visit.app.json','content':json.dumps(doc)},task_id='session-a',call_id='app-1')
        with patch('gateway.calliope_artifact_progress.read_draft',return_value=json.dumps(doc)) as read:
            observe_tool(agent,ref,result={'success':True})
            self.assertEqual(out[-1]['render_app'],doc)
            self.assertEqual(out[-1]['kind'],'a2ui')
            self.assertNotIn('render_html',out[-1])
            read.return_value='{"schema":'
            observe_tool(agent,SimpleNamespace(name='terminal',args={},call_id='app-edit'),result={'success':True})
            self.assertNotIn('render_app',out[-1])

    def test_image_request_and_completion_cross_gateway_callback_and_replay(self):
        with contextlib.closing(StageEvents()) as store:
            _, events, callback, _ = receipts.native_callbacks(store)
            agent = SimpleNamespace(tool_progress_callback=callback)
            ref = SimpleNamespace(name='image_generate', call_id='image-one',
                                  args={'prompt':'A quiet night sky', 'aspect_ratio':'16:9'})
            observe_tool(agent, ref, started=True)
            pending = events[-1]['progress']
            self.assertEqual(pending['kind'], 'image')
            self.assertEqual(pending['phase'], 'generating')
            self.assertEqual(pending['prompt'], 'A quiet night sky')
            self.assertEqual(store.read('run-test')['artifact_progress'], [pending])
            self.assertEqual(store.read('another-run')['artifact_progress'], [])
            observe_tool(agent, ref, result={'image':'/private/result.png'})
            saved = store.read('run-test')['artifact_progress'][0]
            self.assertEqual(saved['phase'], 'image_ready')
            self.assertEqual(saved['call_id'], 'image-one')
            self.assertNotIn('/private', json.dumps(saved))
            self.assertEqual(store.read('run-test')['stage_events'], [])

    def test_three_provider_wires_before_tool_execution_and_replay(self):
        args = json.dumps({'path': '/tmp/dashboard.html', 'content': '<main>Real draft</main>'})
        for wire in ('chat', 'anthropic', 'responses'):
            with self.subTest(wire=wire), contextlib.closing(StageEvents()) as store:
                _, events, callback, _ = receipts.native_callbacks(store)
                agent = SimpleNamespace(tool_progress_callback=callback, _current_api_request_id='request-1')
                if wire == 'chat':
                    chunks = [{'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'name': 'write_file', 'arguments': args}}]}}]}]
                elif wire == 'anthropic':
                    chunks = [{'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'tool_use', 'name': 'write_file'}},
                              {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'input_json_delta', 'partial_json': args}}]
                else:
                    chunks = [{'type': 'response.output_item.added', 'output_index': 0, 'item': {'type': 'function_call', 'name': 'write_file'}},
                              {'type': 'response.function_call_arguments.delta', 'output_index': 0, 'delta': args}]
                for chunk in chunks: observe_chunk(agent, chunk)
                event = events[-1]['progress']
                self.assertEqual(event['phase'], 'composing')
                self.assertGreater(event['received_bytes'], 0)
                self.assertIn('Real draft', event['preview_html'])
                self.assertEqual(event['preview_kind'], 'incoming')
                self.assertNotIn('arguments', event)
                self.assertEqual(store.read('run-test')['artifact_progress'][0]['build_id'], event['build_id'])
                self.assertEqual(store.read('another-run')['artifact_progress'], [])
                ref = SimpleNamespace(name='write_file', args=json.loads(args), call_id='write-1')
                observe_tool(agent, ref, {'success': True})
                self.assertIn('Real draft', store.read('run-test')['artifact_progress'][0]['preview_html'])
                saved_revision = events[-1]['progress']['revision']
                observe_tool(agent, ref, {'success': True})
                self.assertEqual(events[-1]['progress']['revision'], saved_revision)
                # A subsequent composing update retains the last useful preview in replay.
                with patch('gateway.calliope_artifact_progress.time.time', return_value=time.time()+3):
                    for chunk in chunks: observe_chunk(agent, chunk)
                self.assertIn('Real draft', store.read('run-test')['artifact_progress'][0]['preview_html'])

    def test_streaming_draft_updates_before_the_write_completes(self):
        out=[]; obs=BuildObserver(SimpleNamespace(tool_progress_callback=lambda *a,**k:out.append(k['progress'])))
        with patch('gateway.calliope_artifact_progress.time.time', return_value=100):
            obs.delta('c','write_file','{"path":"app.html","content":"<main><h1>First section</h1>')
        first=out[-1]['preview_html']
        with patch('gateway.calliope_artifact_progress.time.time', return_value=102):
            obs.delta('c',text='<p>Another section arrived</p></main>"}')
        self.assertNotEqual(first,out[-1]['preview_html'])
        self.assertIn('Another section arrived',out[-1]['preview_html'])
        self.assertEqual(out[-1]['preview_kind'],'incoming')
        self.assertEqual(out[-1]['phase'],'composing')

    def test_partial_json_string_does_not_invent_split_escapes(self):
        self.assertEqual(partial_string('{"content":"hello\\nworld\\u2','content'),'hello\nworld')
        self.assertEqual(partial_string('{"content":"hello\\nworld\\u263a"}','content'),'hello\nworld☺')
        self.assertEqual(partial_string('{"content":"<p class=\\"x\\">ok</p>"}','content'),'<p class="x">ok</p>')
        self.assertEqual(partial_string(r'{"content":"<h1>\ud83d\ude80</h1>"}','content'),'<h1>🚀</h1>')
        self.assertEqual(partial_string(r'{"content":"hello\ud83d','content'),'hello')

    def test_completed_shell_edit_refreshes_only_tracked_file_and_only_when_changed(self):
        out=[]; agent=SimpleNamespace(tool_progress_callback=lambda *a,**k:out.append(k['progress']))
        write=SimpleNamespace(name='write_file',args={'path':'/safe/app.html','content':'<h1>before</h1>'},task_id='session-a',call_id='first')
        with patch('gateway.calliope_artifact_progress.read_draft', return_value='<h1>before</h1>') as read:
            observe_tool(agent,write,result={'success':True})
            saved=len(out)
            observe_tool(agent,SimpleNamespace(name='terminal',args={},call_id='unchanged'),result={'success':True})
            self.assertEqual(len(out),saved)
            read.return_value='<h1>after shell edit</h1>'
            observe_tool(agent,SimpleNamespace(name='terminal',args={},call_id='changed'),result={'success':True})
            self.assertIn('after shell edit',out[-1]['preview_html'])
            self.assertEqual(out[-1]['preview_kind'],'saved')
            self.assertEqual(out[-1]['build_id'],out[0]['build_id'])
            self.assertEqual({tuple(c.args) for c in read.call_args_list},{('/safe/app.html','session-a')})

    def test_non_artifacts_and_private_thinking_are_not_forwarded(self):
        out = []; agent = SimpleNamespace(tool_progress_callback=lambda *a, **k: out.append(k))
        observe_chunk(agent, {'type':'content_block_delta','delta':{'type':'thinking_delta','thinking':'PRIVATE'}})
        observe_tool(agent, SimpleNamespace(name='write_file',args={'path':'/tmp/config.yaml','content':'secret'},call_id='c'), {'success':True})
        self.assertEqual(out, [])

    def test_throttle_and_bounds(self):
        out=[]; obs=BuildObserver(SimpleNamespace(tool_progress_callback=lambda *a, **k: out.append(k)))
        with patch('gateway.calliope_artifact_progress.time.time', return_value=100):
            for i in range(100): obs.delta('call', 'write_file', '{"path":"a.html","content":"' + 'x'*5000)
        self.assertEqual(len(out), 1)
        self.assertLessEqual(len(obs.calls['call']['text']), 180000)
        self.assertEqual(obs.calls['call']['bytes'], 100*(5000+len('{"path":"a.html","content":"')))

    def test_v4a_patch_paths_and_retry_accumulator(self):
        out=[]; agent=SimpleNamespace(tool_progress_callback=lambda *a, **k:out.append(k['progress']))
        content='*** Begin Patch\n*** Update File: one.html\n@@\n-old\n+new\n*** Add File: two.svg\n+<svg/>\n*** End Patch'
        observe_chunk(agent, {'choices':[{'delta':{'tool_calls':[{'index':0, 'id':'first', 'function':{'name':'patch', 'arguments':json.dumps({'mode':'patch','patch':content})}}]}}]})
        self.assertEqual({p['filename'] for p in out},{'one.html','two.svg'})
        observe_chunk(agent, {'choices':[{'delta':{'tool_calls':[{'index':0, 'id':'retry', 'function':{'name':'write_file','arguments':'{"path":"new.html"}'}}]}}]})
        self.assertEqual(agent._calliope_build_observer.calls[':0']['text'],'{"path":"new.html"}')

    def test_failed_write_does_not_preview_attempted_content(self):
        out=[]; agent=SimpleNamespace(tool_progress_callback=lambda *a, **k:out.append(k['progress']))
        observe_tool(agent, SimpleNamespace(name='write_file', args={'path':'a.html','content':'not saved'}, call_id='bad'), {'error':'disk full'})
        self.assertEqual(out[-1]['phase'], 'failed'); self.assertNotIn('preview_html', out[-1])

    def test_replay_survives_restart_retains_preview_and_rejects_stale(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'runs.sqlite'; store=StageEvents(path)
            store.record_work('r', {'build_id':'a','updated_at':10,'phase':'preview','preview_html':'<p>saved</p>'})
            store.record_work('r', {'build_id':'a','updated_at':11,'phase':'composing'})
            store.record_work('r', {'build_id':'a','updated_at':9,'phase':'failed'})
            store.close(); store=StageEvents(path)
            self.assertEqual(store.read('r')['artifact_progress'][0]['phase'], 'composing')
            self.assertIn('saved', store.read('r')['artifact_progress'][0]['preview_html']);store.close()

    def test_inert_html_contract(self):
        source='<style>main > p{color:red;background:url(https://example.invalid/pixel)}</style><main><script>alert(1)</script><iframe><img src=x></iframe><p onclick="x()">Keep me</p><img src="https://example.invalid/pixel"><meta http-equiv="refresh" content="0;url=https://example.invalid"></main>'
        result=draft_html(source)
        self.assertIn("default-src 'none'",result); self.assertIn('main > p',result);self.assertIn('Keep me',result)
        for forbidden in ('<script', '<iframe', 'onclick', '<img', 'refresh', 'alert(1)'): self.assertNotIn(forbidden,result)
        self.assertIsNone(draft_html('a'*120001))
        self.assertIsNone(draft_html('<head><title>App</title></head><body><div id=app></div><script>renderApp()</script></body>'))
        self.assertIsNone(public_progress({'build_id':'../../x','phase':'composing'}))
        self.assertEqual(public_progress({'build_id':'ok','phase':'composing','lines':123})['lines'], [])
        self.assertNotIn('source', public_progress({'build_id':'ok','phase':'composing','source':'private'}))


if __name__ == '__main__': unittest.main()
