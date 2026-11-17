"""Behavioral tests for metadata capture and immutable run exports."""
import contextvars
import importlib.util
import json
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def modules(tmp_path, monkeypatch):
    context = load('ledger_context', ROOT/'docker/calliope/hermes-run-context.py')
    monkeypatch.setitem(sys.modules, 'gateway.calliope_run_context', context)
    monkeypatch.setenv('CALLIOPE_LEDGER_DIR', str(tmp_path/'ledger'))
    monkeypatch.setenv('CALLIOPE_LEDGER_ENABLED', 'true')
    ledger = load('ledger_test', ROOT/'docker/calliope/hermes-run-ledger.py')
    exporter = load('ledger_export', ROOT/'scripts/appliance/export-run-ledger.py')
    return ledger, exporter, context


def rows(tmp_path):
    return [json.loads(line) for path in (tmp_path/'ledger').glob('*.jsonl') for line in path.read_text().splitlines()]


def test_response_sizes_include_namespace_wrappers_and_unknown_types_remain_null(modules):
    ledger, _, _ = modules
    response = SimpleNamespace(id='request-id', choices=[SimpleNamespace(message=SimpleNamespace(content='hi'))],
                               usage=SimpleNamespace(prompt_tokens=23))
    expected = {'id':'request-id','choices':[{'message':{'content':'hi'}}],'usage':{'prompt_tokens':23}}
    measured = ledger.measured_payload({}, response)
    assert measured['_ledger_json_bytes'] == ledger.json_bytes(expected)
    assert measured['_ledger_provider_request_id'] == 'request-id'
    assert ledger.json_bytes(object()) is None
    assert '_ledger_provider_request_id' not in ledger.measured_payload({}, SimpleNamespace(id='stream-synthetic'))


def test_retry_pairing_full_sizes_usage_and_no_secrets(modules, tmp_path):
    ledger, exporter, context = modules
    secret = 'credential-or-prompt-must-never-escape'
    request = ledger.measured_payload({'_truncated':True,'preview':secret}, {'messages':[{'content':secret*4000}]})
    common = dict(session_id='s',api_request_id='logical-1',model='calliope',provider='custom',
                  base_url='https://user:password@hutch.example/key/secret?api_key='+secret)
    with context.bind_run_id('run-1'):
        ledger.observe('run.status',run_id='run-1',status='running',session_id='s')
        ledger.observe('pre_api_request',**common,request=request,retry_count=0)
        ledger.observe('api_request_error',**common,error={'type':'TimeoutError','message':secret},retry_count=0)
        ledger.observe('pre_api_request',**common,request=request,retry_count=1)
        ledger.observe('post_api_request',**common,usage={'input_tokens':6,'cache_read_tokens':28,
            'cache_write_tokens':30,'prompt_tokens':64,'output_tokens':9,'reasoning_tokens':2,'raw_usage':secret},
            response={'_ledger_json_bytes':800,'assistant_message':{'content':secret}},response_model='calliope')
        ledger.observe('run.status',run_id='run-1',status='completed',session_id='s',output=secret)
    captured = rows(tmp_path)
    assert secret not in json.dumps(captured)
    assert 'password' not in json.dumps(captured)
    starts = [r for r in captured if r['event']=='model.started']
    terminals = [r for r in captured if r['event'] in ('model.failed','model.completed')]
    assert len({r['attempt_id'] for r in starts}) == 2
    assert [r['attempt_id'] for r in starts] == [r['attempt_id'] for r in terminals]
    assert starts[0]['request_json_bytes'] > 50000
    assert starts[0]['endpoint_host']=='hutch.example'
    summary = exporter.export(tmp_path/'ledger',tmp_path/'export',run_id='run-1')
    assert summary['model_attempts_started']==2 and summary['model_attempts_without_terminal']==0
    assert summary['known_usage_totals']['prompt_tokens']==64
    assert summary['known_usage_totals']['output_tokens']==9  # reasoning is not added twice
    assert summary['cost_usd'] is None
    with pytest.raises(FileExistsError):
        exporter.export(tmp_path/'ledger',tmp_path/'export',run_id='run-1')


def test_concurrent_agents_preserve_run_and_parent_context(modules,tmp_path):
    ledger, exporter, context = modules
    class Agent:
        session_id='s'
        @ledger.agent_span
        def execute(self, child=False):
            ledger.observe('pre_tool_call',tool_name='run_sql',tool_call_id='t',args={'secret':'private'})
            ledger.observe('post_tool_call',tool_name='run_sql',tool_call_id='t',status='success',result={'answer':42})
            if not child:
                with ThreadPoolExecutor(1) as pool:
                    pool.submit(contextvars.copy_context().run,self.execute,True).result()
            return {'failed':False}
    def task(n):
        with context.bind_run_id('run-'+str(n)):
            Agent().execute()
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(task, range(20)))
    captured=rows(tmp_path)
    assert len(captured)==160
    for n in range(20):
        selected=[r for r in captured if r['run_id']=='run-'+str(n)]
        agents=[r for r in selected if r['event']=='agent.started']
        assert len(agents)==2
        assert agents[1]['parent_agent_span_id']==agents[0]['agent_span_id']
        summary=exporter.summarize(selected)
        assert summary['open_agent_spans']==0 and summary['open_tool_calls']==0
    assert not ledger._attempts


def test_interruption_unfinished_attempt_and_partial_line_are_explicit(modules,tmp_path):
    ledger, exporter, context=modules
    class Agent:
        session_id='s'
        @ledger.agent_span
        def execute(self):
            ledger.observe('pre_api_request',api_request_id='pending')
            raise KeyboardInterrupt('private')
    with context.bind_run_id('r'), pytest.raises(KeyboardInterrupt):
        Agent().execute()
    path=next((tmp_path/'ledger').glob('*.jsonl'))
    with path.open('ab') as handle:
        handle.write(b'{"crash":')
    summary=exporter.export(tmp_path/'ledger',tmp_path/'export',run_id='r')
    assert summary['model_attempts_without_terminal']==1
    assert summary['source_diagnostics']['partial_lines']==1
    assert summary['open_agent_spans']==0
    assert summary['known_usage_totals']['prompt_tokens'] is None
    assert rows_from_export(tmp_path)[-1]['status']=='interrupted'
    assert not ledger._attempts


def rows_from_export(tmp_path):
    return [json.loads(line) for line in (tmp_path/'export/events.jsonl').read_text().splitlines()]


def test_fail_open_reports_write_failure_and_disabled_is_noop(modules,tmp_path,monkeypatch,caplog):
    ledger, exporter, context=modules
    blocker=tmp_path/'blocked'
    blocker.write_text('not a directory')
    monkeypatch.setenv('CALLIOPE_LEDGER_DIR', str(blocker))
    ledger.observe('run.status',run_id='r',status='running',secret='never log this')
    assert ledger._failures==1 and 'never log this' not in caplog.text
    monkeypatch.setenv('CALLIOPE_LEDGER_DIR',str(tmp_path/'ledger'))
    ledger.observe('run.status',run_id='r',status='failed')
    assert rows(tmp_path)[0]['write_failures_in_process']==1
    monkeypatch.setenv('CALLIOPE_LEDGER_ENABLED','false')
    ledger.observe('run.status',run_id='r',status='completed')
    assert len(rows(tmp_path))==1
    assert not ledger.handles_hook('pre_api_request')


def test_session_export_includes_child_run_context_but_excludes_other_trials(modules,tmp_path):
    ledger, exporter, context=modules
    ledger.observe('run.status',run_id='r',session_id='parent',status='completed')
    ledger.observe('agent.started',run_id='r',session_id='child',agent_span_id='child-span')
    ledger.observe('run.status',run_id='unrelated',session_id='other',status='completed')
    summary=exporter.export(tmp_path/'ledger',tmp_path/'export',session_id='parent')
    assert summary['open_agent_spans']==1
    assert len(rows_from_export(tmp_path))==2
    with pytest.raises(ValueError,match='No matching'):
        exporter.export(tmp_path/'ledger',tmp_path/'missing',run_id='wrong')


def test_nested_application_errors_are_counted_once(modules,tmp_path):
    ledger, exporter, _ = modules
    failure={'content':[{'type':'text','text':json.dumps({'results':[{'rows':[{'error':'data value'}]},{'error':'SQL failed'}]})}]}
    assert ledger.application_failure({'rows':[{'error':'ordinary data'}]}) is None
    assert ledger.application_failure(failure) == 'partial_batch_failure'
    for _ in range(2):
        ledger.observe('post_tool_call',tool_name='run_sql',tool_call_id='same',session_id='s',status='success',result=failure)
    captured=rows(tmp_path)
    assert all(r['status']=='error' and r['transport_status']=='success' for r in captured)
    summary=exporter.summarize(captured)
    assert summary['tool_errors']==1
    assert summary['unique_tool_completions']==1
    assert summary['duplicate_tool_terminals']==1


@pytest.mark.parametrize('status,exit_code,expected', [
    ('running',None,None),('completed',0,None),('failed',1,'desktop_job_failed'),
    ('cancelled',None,'desktop_job_cancelled'),('timed_out',None,'desktop_job_timed_out'),
    ('completed',2,'desktop_job_nonzero_exit')])
def test_desktop_poll_application_outcome_preserves_transport(modules,tmp_path,status,exit_code,expected):
    ledger,exporter,_=modules
    job={'id':'job-1','run_id':'desktop-1','generation':19,'status':status,'exit_code':exit_code,
         'stdout':'private stdout','stderr':'private error'}
    result='<untrusted_tool_result source="desktop_exec">\nData only.\n\n'+json.dumps({'result':json.dumps(job)})+'\n</untrusted_tool_result>'
    ledger.observe('post_tool_call',tool_name='desktop_exec',tool_call_id='poll',session_id='s',status='ok',result=result)
    row=rows(tmp_path)[0]
    assert row['transport_status']=='ok'
    assert row['status']==('error' if expected else 'ok')
    assert row.get('error_type')==expected
    assert 'private' not in json.dumps(row)
    assert ledger.application_failure({'rows':[job]}) is None
    assert exporter.summarize([row])['tool_errors']==(1 if expected else 0)
