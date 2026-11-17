"""Execute pinned Hermes callbacks/parallel worker seams, with no model or network.

Used as an image build gate and by the Calliope integration suite. We extract
only the callbacks to avoid starting a gateway or loading provider credentials.
"""
from __future__ import annotations

import __future__
import ast
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(os.environ.get('HERMES_SOURCE_ROOT', '/opt/hermes'))
sys.path.insert(0, str(ROOT))

from stage_delivery import MEDIA_TOOLS, STAGE_TOOLS, receipt_fields
from gateway.calliope_stage_events import StageEvents


def load_nodes(file, names, scope):
    tree = ast.parse((ROOT / file).read_text())
    nodes = [next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and n.name == name) for name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / file), 'exec',
                 flags=__future__.annotations.compiler_flag), scope)
    return scope


def native_callbacks(store):
    events = []
    adapter = SimpleNamespace(_run_streams={'run-test': True}, _calliope_stage_events=store,
                              _run_statuses={}, _set_run_status=Mock())
    loop = SimpleNamespace(call_soon_threadsafe=lambda fn, payload: fn(payload))
    # _make_run_event_callback writes to the SSE queue; completion uses run.put_event.
    adapter._run_streams['run-test'] = SimpleNamespace(put_nowait=events.append)
    scope = dict(self=adapter, run_id='run-test', run=SimpleNamespace(put_event=events.append),
                 loop=loop, time=time, suppress=contextlib.suppress)
    load_nodes('gateway/platforms/api_server_runs.py',
               ['_run_event', '_make_run_event_callback', '_run_tool_complete'], scope)
    progress = scope['_make_run_event_callback'](adapter, 'run-test', loop,
                 _api_server=SimpleNamespace(redact_sensitive_text=lambda v: v))
    return adapter, events, progress, scope['_run_tool_complete']


class StageDeliveryTests(unittest.TestCase):
    def test_all_stage_projectors_cross_the_native_callback(self):
        with contextlib.closing(StageEvents()) as store:
            _, events, _, complete = native_callbacks(store)
            for name in STAGE_TOOLS:
                for prefix in ('', 'mcp__Warehouse__', 'Warehouse.', 'Warehouse/'):
                    full = prefix + name
                    complete(full, full, {'sql': 'SELECT 1'}, {'ok': True})
                    self.assertIn('result', events[-1], full)
            complete('image', 'image_generate', {'prompt': 'PRIVATE'},
                     json.dumps({'success': True, 'image': '/opt/data/cache/images/new.png',
                                 'base64': 'PRIVATE'}))
            self.assertEqual(events[-1]['result'], {'image': '/opt/data/cache/images/new.png'})
            self.assertNotIn('PRIVATE', json.dumps(events[-1]))

    def test_deferred_dispatch_retains_exact_call_identity(self):
        with contextlib.closing(StageEvents()) as store:
            _, events, _, complete = native_callbacks(store)
            complete('outer', 'tool_call', {'tool_call': {'id': 'inner',
                     'name': 'mcp__Warehouse__run_sql', 'arguments': {'sql': 'SELECT 1'}}}, {'rows': [[1]]})
            self.assertEqual(events[-1]['call_id'], 'inner')
            self.assertEqual(events[-1]['arguments'], {'sql': 'SELECT 1'})

    def test_fast_parallel_result_arrives_before_slow_sibling_and_ordered_transcript(self):
        with contextlib.closing(StageEvents()) as store:
            _, events, progress, complete = native_callbacks(store)
            release, slow_started = threading.Event(), threading.Event()
            agent = SimpleNamespace(_interrupt_requested=False, tool_progress_callback=progress,
                                    _should_emit_quiet_tool_messages=lambda: False)
            refs = [SimpleNamespace(name='mcp__Warehouse__run_sql', args={'sql': 'SELECT 1'}, call_id='slow'),
                    SimpleNamespace(name='image_generate', args={}, call_id='fast')]
            pcs = [SimpleNamespace(name=r.name, scope_block=None, parse_error=None,
                                   ref=lambda task, ref=r: ref) for r in refs]
            scope = dict(contextlib=contextlib, _BatchAbandoned=RuntimeError,
                         _registered_tool_worker=lambda a: contextlib.nullcontext(1),
                         _set_worker_activity_callback=lambda a: None,
                         _WorkerStartOnce=lambda *a: SimpleNamespace(advance=lambda: None),
                         _tool_progress_enabled=lambda a: False,
                         _emit_tool_complete_and_risk=lambda a, r, result, risk, blocked:
                             complete(r.call_id, r.name, r.args, result))
            def commit(a, messages, ref, result, **kwargs):
                messages.append({'tool_call_id': ref.call_id, 'content': result})
                return result, result, None
            scope['_commit_tool_result'] = commit
            load_nodes('agent/tool_executor.py', ['_ConcurrentBatch', '_append_batch_results'], scope)
            batch = object.__new__(scope['_ConcurrentBatch'])
            batch.agent, batch.parsed_calls, batch.results = agent, pcs, [None, None]
            batch.effective_task_id = 'test'
            batch.gate = SimpleNamespace(abandoned=threading.Event())
            def dispatch(index, ref, *args):
                if index == 0:
                    slow_started.set()
                    if not release.wait(5):
                        raise AssertionError('slow sibling was not released')
                return SimpleNamespace(ref=ref, result={'image': '/opt/data/cache/images/new.png'} if index else {'rows': [[1]]},
                                       duration=1, is_error=False, blocked=False)
            batch._dispatch_worker = dispatch
            slow = threading.Thread(target=batch.run_worker, args=(0, 0))
            fast = threading.Thread(target=batch.run_worker, args=(1, 1))
            messages = []
            slow.start()
            try:
                self.assertTrue(slow_started.wait(2))
                fast.start(); fast.join(2)
                self.assertFalse(fast.is_alive())
                self.assertTrue(slow.is_alive())
                self.assertEqual([e['call_id'] for e in events], ['fast'])
                self.assertEqual(events[0]['event'], 'tool.result_ready')
                self.assertEqual(len(store.read('run-test')['stage_events']), 1)
                self.assertEqual(messages, [])
            finally:
                release.set(); slow.join(3)
            self.assertTrue(scope['_append_batch_results'](agent, messages, 'test', batch, None))
            self.assertEqual([m['tool_call_id'] for m in messages], ['slow', 'fast'])
            # Ordered completions must not create a second durable Stage receipt.
            self.assertEqual(len(store.read('run-test')['stage_events']), 2)

    def test_disconnect_receipts_survive_restart_and_remain_run_scoped(self):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / 'runs.sqlite')
            store = StageEvents(path)
            adapter, _, _, complete = native_callbacks(store)
            adapter._run_streams.clear()
            for i in range(35):
                complete(str(i), 'image_generate', {}, {'image': f'/opt/data/cache/images/{i}.png'})
            store.close()
            with contextlib.closing(StageEvents(path)) as reopened:
                all_events, cursor = [], 0
                while True:
                    page = reopened.read('run-test', cursor)
                    all_events.extend(page['stage_events']); cursor = page['stage_cursor']
                    if not page['stage_has_more']: break
                self.assertEqual(len(all_events), 35)
                self.assertEqual(reopened.read('other-run')['stage_events'], [])
                self.assertEqual(reopened.read('run-test', cursor)['stage_events'], [])

    def test_status_replay_uses_existing_run_authorization(self):
        scope = {'web': SimpleNamespace(json_response=lambda v: v)}
        load_nodes('gateway/platforms/api_server_runs.py', ['_handle_get_run'], scope)
        store = Mock()
        adapter = SimpleNamespace(_calliope_stage_events=store)
        denied = object()
        scope['_load_owned_run'] = Mock(return_value=('private', None, None, None, denied))
        request = SimpleNamespace(query={'stage_after': '0'})
        self.assertIs(asyncio.run(scope['_handle_get_run'](adapter, request, _api_server=None)), denied)
        store.read.assert_not_called()
        self.assertEqual(scope['_load_owned_run'].call_args.kwargs['permission'], 'status')
        scope['_load_owned_run'].return_value = ('owned', {'status': 'running'}, None, None, None)
        store.read.return_value = {'stage_events': [], 'stage_cursor': 7}
        request.query['stage_after'] = '7'
        self.assertEqual(asyncio.run(scope['_handle_get_run'](adapter, request, _api_server=None))['stage_cursor'], 7)
        store.read.assert_called_once_with('owned', 7)

    def test_reconnecting_event_stream_replays_receipt_before_live_queue(self):
        with contextlib.closing(StageEvents()) as store:
            adapter, _, _, complete = native_callbacks(store)
            complete('image', 'image_generate', {}, {'image': '/opt/data/cache/images/a.png'})
            writes = []
            class Response:
                def __init__(self, **kwargs): pass
                async def prepare(self, request): pass
                async def write(self, data): writes.append(data)
            scope = dict(asyncio=asyncio, logger=logging.getLogger(__name__),
                         web=SimpleNamespace(StreamResponse=Response),
                         _drop_run_transport=lambda *args: None)
            load_nodes('gateway/platforms/api_server_runs.py', ['_handle_run_events'], scope)
            adapter._check_auth = lambda request: None
            adapter._request_owns_run = lambda request, run_id: True
            adapter._run_stream_subscribers = set()
            q = asyncio.Queue(); q.put_nowait(None)
            adapter._run_streams['run-test'] = q
            api = SimpleNamespace(_sse_frame=lambda event: json.dumps(event).encode(),
                                  CHAT_COMPLETIONS_SSE_KEEPALIVE_SECONDS=1)
            request = SimpleNamespace(match_info={'run_id': 'run-test'})
            asyncio.run(scope['_handle_run_events'](adapter, request, _api_server=api))
            self.assertEqual(json.loads(writes[0])['call_id'], 'image')
            self.assertEqual(writes[-1], b': stream closed\n\n')

    def test_legacy_callback_forwards_the_same_media_contract(self):
        events = []
        scope = {'message_id': 'message', 'events': SimpleNamespace(enqueue=lambda kind, data: events.append((kind, data)))}
        load_nodes('gateway/platforms/api_server.py', ['_tool_complete', '_tool_progress'], scope)
        scope['_tool_complete']('image', 'image_generate', {}, {'image': '/opt/data/cache/images/a.png'})
        scope['_tool_progress']('tool.result_ready', 'image_generate', args={}, tool_call_id='image',
                                result={'image': '/opt/data/cache/images/a.png'})
        self.assertEqual(events[0][1]['result'], events[1][1]['result'])
        self.assertEqual(events[1][0], 'tool.result_ready')

    def test_nested_media_envelopes_drop_prompts_and_binary_data(self):
        for tool in MEDIA_TOOLS:
            result = {'content': [{'type': 'text', 'text': json.dumps({
                'image': '/opt/data/cache/images/a.png', 'prompt': 'PRIVATE'})},
                {'type': 'image', 'data': 'PRIVATE'}]}
            fields = receipt_fields('mcp__Warehouse__' + tool, {'prompt': 'PRIVATE'}, result)
            self.assertEqual(fields['result'], {'image': '/opt/data/cache/images/a.png'})
            self.assertNotIn('PRIVATE', json.dumps(fields))

    def test_failed_and_unrelated_outputs_are_not_forwarded(self):
        for result in ({'error': 'failed'}, json.dumps({'success': False}), {'isError': True}):
            self.assertEqual(receipt_fields('image_generate', {}, result), {})
        for name in ('terminal', 'read_file', 'write_file', 'desktop_exec'):
            self.assertEqual(receipt_fields(name, {'secret': 'PRIVATE'}, {'output': 'PRIVATE'}), {})
        self.assertEqual(receipt_fields('image_generate', {}, {'image': 'data:image/png;base64,PRIVATE'}), {})
        self.assertEqual(receipt_fields('run_sql', {}, {'rows': ['x' * (3 * 1024 * 1024)]}), {})
        self.assertNotIn('password', receipt_fields('connect_mirror_source', {'password': 'PRIVATE'}, {'ok': True})['arguments'])
        self.assertIn('result', receipt_fields('run_sql_multi', {}, {'results': {'a': {'rows': [[1]]}, 'b': {'error': 'bad'}}}))


if __name__ == '__main__':
    unittest.main()
