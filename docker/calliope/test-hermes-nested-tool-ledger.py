"""Pinned native dispatcher: child success, failures, cancellation and ownership."""
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import patch
import logging
logging.disable(logging.CRITICAL)
import model_tools as m
from gateway import calliope_ledger as ledger
from gateway.calliope_run_context import bind_run_id

# Keep native dispatch, pre/post hook plumbing and ledger serialization. Replace
# only registry work with an offline nested tool fixture.
def execute(name,args,original,ids,**kwargs):
    if name=='outer':
        return m.handle_function_call('terminal',{'mode':args['mode']},task_id=ids.task_id,session_id=ids.session_id)
    if args['mode']=='error':raise ValueError('private detail')
    if args['mode']=='cancel':raise KeyboardInterrupt()
    return json.dumps({'ok':True})
with tempfile.TemporaryDirectory() as directory:
    os.environ['CALLIOPE_LEDGER_DIR']=directory
    with bind_run_id('nested-test'), patch.object(m,'_dispatch_bridge_tool',return_value=None), \
         patch.object(m,'_execute_tool',side_effect=execute), \
         patch.object(m,'_apply_transform_tool_result_hook',side_effect=lambda name,args,result,*a:result):
        for mode in ('ok','error','cancel'):
            with m.suppress_post_tool_call_hook('outer-'+mode):
                try:
                    m.handle_function_call('outer',{'mode':mode},session_id='s',tool_call_id='outer-'+mode,
                        skip_pre_tool_call_hook=True,skip_tool_request_middleware=True)
                except KeyboardInterrupt:
                    assert mode=='cancel'
        # Distinct concurrent calls of the same tool get distinct IDs too.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _:m.handle_function_call('terminal',{'mode':'ok'},session_id='s'),range(2)))
    rows=[json.loads(line) for p in Path(directory).glob('*.jsonl') for line in p.read_text().splitlines()]
    starts=[r for r in rows if r['event']=='tool.started']
    ends=[r for r in rows if r['event']=='tool.completed']
    assert len(starts)==len(ends)==5,(starts,ends)
    ids={r['tool_call_id'] for r in starts}
    assert len(ids)==5 and ids=={r['tool_call_id'] for r in ends}
    assert not any(r['tool_call_id'].startswith('outer-') for r in ends)
    assert {r.get('status') for r in ends}>={'ok','error','interrupted'}
    assert 'private detail' not in json.dumps(rows)
# Auxiliary calls with no API id must not steal an outstanding main attempt.
with tempfile.TemporaryDirectory() as directory:
    os.environ['CALLIOPE_LEDGER_DIR']=directory
    ledger.observe('pre_api_request',session_id='s')
    ledger.observe('aux.completed',session_id='s',task_id='compression',usage={'input_tokens':1})
    ledger.observe('post_api_request',session_id='s',usage={'input_tokens':1})
    events=[json.loads(line) for p in Path(directory).glob('*.jsonl') for line in p.read_text().splitlines()]
    assert events[0]['attempt_id']==events[2]['attempt_id'] and events[1]['attempt_id'] is None
print('Native nested lifecycle closure, concurrent IDs and cancellation: passed')

# Workspace receipts can succeed at the HTTP/MCP layer while their SQL run failed.
wrapped = '<untrusted_tool_result source="mcp">\nUntrusted data.\n\n' + json.dumps({
    'result': json.dumps({'run': {'id': 'workspace-1', 'status': 'failed', 'error': 'private timeout'}})
}) + '\n</untrusted_tool_result>'
assert ledger.application_failure(wrapped) == 'workspace_run_failed'
assert ledger.application_failure({'rows': [{'run': {'id': 'customer', 'status': 'failed'}}]}) is None
