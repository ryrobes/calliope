"""Image request telemetry is bounded, scoped and independent of inference."""
import contextlib
import importlib.util
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

import artifact_progress
import artifact_render
import artifact_work

spec = importlib.util.spec_from_file_location('image_progress_observer',
    Path(__file__).resolve().parents[2]/'docker/calliope/hermes-artifact-progress.py')
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


def test_native_and_mcp_image_calls_emit_only_safe_display_metadata():
    events = []
    agent = SimpleNamespace(tool_progress_callback=lambda _event, _name, **kw: events.append(kw['progress']))
    for name in ['image_generate', 'mcp__Warehouse__generate_image', 'image_edit']:
        ref = SimpleNamespace(name=name, call_id=name, args={'prompt': 'Stars password=PRIVATE ' + 'x'*3000,
            'title':'Night sky','aspect_ratio':'16:9','api_key':'PRIVATE','reference_image':'PRIVATE'})
        observer.observe_tool(agent, ref, started=True)
        pending = events[-1]
        assert pending['phase'] == 'generating' and pending['kind'] == 'image'
        assert pending['call_id'] == name and len(pending['prompt']) == 1600
        assert pending['aspect_ratio'] == '16:9' and 'PRIVATE' not in str(pending)
        assert not {'render_html','preview_html','arguments','reference_image'} & pending.keys()
        observer.observe_tool(agent, ref, result={'image':'/private/result.png'})
        assert events[-1]['phase'] == 'image_ready'
        assert events[-1]['build_id'] == pending['build_id']
        assert '/private' not in str(events[-1])
        n = len(events)
        observer.observe_tool(agent, ref, result={'image':'/private/result.png'})
        assert len(events) == n, 'Parallel completion and transcript replay must not duplicate events'


def test_deferred_image_keeps_inner_call_identity_and_failure():
    events = []
    agent = SimpleNamespace(tool_progress_callback=lambda *a, **kw: events.append(kw['progress']))
    ref = SimpleNamespace(name='tool_call', call_id='outer', args={'tool_call': {
        'id':'inner','name':'mcp__Warehouse__generate_image','arguments':{'prompt':'Ocean'}}})
    observer.observe_tool(agent, ref, started=True)
    observer.observe_tool(agent, ref, result={'error':'provider failure'})
    assert [e['phase'] for e in events] == ['generating','failed']
    assert all(e['call_id'] == 'inner' for e in events)
    assert all(e['prompt'] == 'Ocean' for e in events)


def test_image_snapshot_is_scoped_replayable_and_retires_after_failure(tmp_path, monkeypatch):
    session, turn, run = map(str, [uuid4(), uuid4(), uuid4()])
    config = SimpleNamespace(file_root=tmp_path)
    now = time.time()
    event = {'kind':'image','build_id':'image-one','call_id':'c','phase':'generating','prompt':'A map', 'updated_at':now}
    artifact_work.record(config, session, turn, event, {'run_id':run, 'key':'iris'})
    def conn():
        return contextlib.nullcontext(SimpleNamespace(execute=lambda sql, args:
            SimpleNamespace(fetchall=lambda:[{'id':id,'status':'running'} for id in args[1]])))
    item = artifact_work.snapshot(config, conn, session)[0]
    assert item['agent_run_id'] == run and item['turn_id'] == turn and item['prompt'] == 'A map'
    assert artifact_work.snapshot(config, conn, str(uuid4())) == []
    assert artifact_render.describe(config, session, item, None, 'viewer', 'owner') == {}
    artifact_work.record(config, session, turn, {**event,'phase':'failed','updated_at':now+1}, {'run_id':run,'key':'iris'})
    monkeypatch.setattr(artifact_work.time, 'time', lambda: now+20)
    assert artifact_work.snapshot(config, conn, session) == []


def test_image_contract_drops_html_and_credentials_even_on_replay():
    event = artifact_progress.public_progress({'kind':'image','build_id':'image-a','phase':'generating',
        'prompt':'Night sky','call_id':'one','render_html':'SECRET','preview_html':'<p>SECRET</p>',
        'arguments':{'token':'SECRET'},'image_url':'https://example.invalid/SECRET'})
    assert event['prompt'] == 'Night sky' and 'SECRET' not in str(event)
    assert artifact_progress.public_progress(event) == event
