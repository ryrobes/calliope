"""Pinned-source integration gate: real lifecycle dispatch and real SDK hook payloads."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

from agent.api_request_hooks import ApiRequestHooksMixin
from agent.turn_facade import TurnFacadeMixin
from gateway.calliope_run_context import bind_run_id
from hermes_cli.lifecycle import has_hook, invoke_hook
from openai.types.chat import ChatCompletion

class Probe(ApiRequestHooksMixin):
    provider = 'custom'
    api_mode = 'chat_completions'

with tempfile.TemporaryDirectory() as directory:
    os.environ['CALLIOPE_LEDGER_DIR'] = directory
    assert has_hook('pre_api_request') and has_hook('post_tool_call')
    assert hasattr(TurnFacadeMixin.run_conversation, '__wrapped__')
    response = ChatCompletion(id='provider-123',created=1,model='calliope',object='chat.completion',
        choices=[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'private answer'}}],
        usage={'prompt_tokens':100,'completion_tokens':9,'total_tokens':109,
               'prompt_tokens_details':{'cached_tokens':80}})
    probe=Probe()
    with bind_run_id('integration-run'):
        invoke_hook('pre_api_request',session_id='s',api_request_id='request-1',model='calliope',
                    request=probe._api_request_payload_for_hook({'messages':[{'content':'private prompt'*9000}]}))
        invoke_hook('post_api_request',session_id='s',api_request_id='request-1',model='calliope',
                    response=probe._api_response_payload_for_hook(response,response.choices[0].message,finish_reason='stop'),
                    usage=probe._usage_summary_for_api_request_hook(response))
        invoke_hook('pre_tool_call',session_id='s',tool_call_id='tool-1',tool_name='run_sql',args={'sql':'private query'})
        invoke_hook('post_tool_call',session_id='s',tool_call_id='tool-1',tool_name='run_sql',status='success',result={'answer':42})
    events=[json.loads(line) for p in Path(directory).glob('*.jsonl') for line in p.read_text().splitlines()]
    assert len(events)==4, len(events)
    assert events[0]['request_json_bytes'] > 50000
    assert events[0]['attempt_id']==events[1]['attempt_id']
    assert events[1]['provider_request_id']=='provider-123'
    assert events[1]['usage']['prompt_tokens']==100
    assert events[1]['usage']['input_tokens']==20
    assert events[1]['response_json_bytes'] > 100
    assert 'private' not in json.dumps(events)
    assert all(r['run_id']=='integration-run' for r in events)
print('Run ledger pinned lifecycle / SDK integration: passed')

# Default-route completions expose actual runtime identity too.
import ast
source=ast.parse(Path('/opt/hermes/gateway/platforms/api_server.py').read_text())
method=next(n for n in ast.walk(source) if isinstance(n,ast.FunctionDef) and n.name=='_finish_turn_result')
method.decorator_list=[]
namespace={}
exec('from __future__ import annotations\n'+ast.unparse(method),namespace)
owner=SimpleNamespace(_turn_runtime_metadata=lambda *a,**kw:{'provider':'custom','model':'calliope','route_source':'global'})
result,usage=namespace['_finish_turn_result'](owner,SimpleNamespace(session_id='s'),{},'s',route=None,requested_runtime=None,route_source='global',confirmed_runtime_lock=False)
assert result['runtime']['model']=='calliope' and usage['runtime']==result['runtime']
from gateway.calliope_ledger import measured_payload
assert measured_payload({},SimpleNamespace(usage=SimpleNamespace(cost=.001)))['_ledger_reported_cost_usd']==.001
print('Default runtime identity and upstream cost metadata: passed')

# Exercise the actual pinned retry loop: a dropped partial response, then success.
# Network/provider behavior is deterministic here; no external inference is used.
from gateway import calliope_ledger as ledger
source = ast.parse(Path('/opt/hermes/agent/chat_completion_helpers.py').read_text())
stream_class = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == '_StreamingCall')
method = next(n for n in stream_class.body if isinstance(n, ast.FunctionDef) and n.name == '_call')
namespace = {'env_int': lambda *a: 2, '_with_stream_emitters': lambda agent, fn: fn()}
exec(ast.unparse(method), namespace)
with tempfile.TemporaryDirectory() as directory:
    os.environ['CALLIOPE_LEDGER_DIR'] = directory
    agent = SimpleNamespace(session_id='retry-session', model='calliope-opus',
                            _current_api_request_id='logical-request', _interrupt_requested=False)
    clients = SimpleNamespace(diag={}, close_once=lambda reason: None)
    probe = SimpleNamespace(agent=agent, clients=clients, result={'response': None},
        _start_stream_attempt=lambda: 'upstream-attempt', _close_managed_stream=lambda: None,
        _route_switched_under_request=lambda: False)
    calls = []
    def wire(attempt):
        calls.append(attempt)
        clients.diag = {'chunks': 2, 'bytes': 123}
        ledger.stream_chunk_metadata(clients.diag, {'id': f'gen-{len(calls)}',
            'model': 'anthropic/example', 'provider': 'test-provider', 'choices': [{'secret': 'private'}]})
        if len(calls) == 1:
            raise ConnectionError('private provider error body')
        return {'id': 'gen-2', 'model': 'anthropic/example'}
    probe._call_wire = wire
    probe._handle_stream_error = lambda *a: True
    with bind_run_id('retry-run'):
        ledger.observe('pre_api_request', session_id=agent.session_id,
                       api_request_id=agent._current_api_request_id)
        namespace['_call'](probe)
        ledger.observe('post_api_request', session_id=agent.session_id,
                       api_request_id=agent._current_api_request_id, usage={'output_tokens': 5})
    rows = [json.loads(line) for p in Path(directory).glob('*.jsonl') for line in p.read_text().splitlines()]
    assert [r['event'] for r in rows] == ['model.started', 'stream.started', 'stream.failed',
                                         'stream.started', 'stream.completed', 'model.completed']
    assert all(r['logical_attempt_id'] == rows[0]['attempt_id'] for r in rows[1:5])
    assert rows[1]['stream_attempt_id'] == rows[2]['stream_attempt_id']
    assert rows[3]['stream_attempt_id'] == rows[4]['stream_attempt_id'] != rows[1]['stream_attempt_id']
    assert rows[2]['provider_request_id'] == 'gen-1' and rows[4]['provider_request_id'] == 'gen-2'
    assert rows[2]['received_chunks'] == 2 and rows[2]['error_type'] == 'ConnectionError'
    assert rows[3]['stream_attempt_no'] == 2 and all(r['run_id'] == 'retry-run' for r in rows)
    assert 'private' not in json.dumps(rows)
    assert all('usage' not in r for r in rows[1:5])  # diagnostic, never double billing
    # Cancellation must close the stream receipt and must not start a new attempt.
    def cancelled(attempt):
        raise InterruptedError('private cancellation')
    probe._call_wire = cancelled
    probe._handle_stream_error = lambda *a: False
    probe.result = {'response': None}
    namespace['_call'](probe)
    rows2 = [json.loads(line) for p in Path(directory).glob('*.jsonl') for line in p.read_text().splitlines()]
    assert [r['event'] for r in rows2[len(rows):]] == ['stream.started', 'stream.failed']
    assert rows2[-1]['error_type'] == 'InterruptedError'
    # Malformed diagnostic metadata cannot fail a model request.
    assert ledger.stream_attempt_finished({'_started': None}, {}) == {}
print('Pinned stream retry / cancellation receipts, correlation and privacy: passed')
