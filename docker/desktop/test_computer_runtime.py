from pathlib import Path
import json
import os
import sys
import time
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from computer_runtime import ComputerError, ComputerRuntime


@pytest.mark.parametrize('delivers_frame', [True, False])
def test_recorder_admission_requires_first_frame_and_cleans_up_failed_capture(tmp_path, monkeypatch, delivers_frame):
    import subprocess
    import computer_runtime
    popen = subprocess.Popen
    children = []
    receipt = tmp_path / 'masking-000000.json'
    script = ("import pathlib,time; time.sleep(.15); "
              + (f"pathlib.Path({str(receipt)!r}).write_text('{{\"frames\":1}}'); time.sleep(30)"
                 if delivers_frame else "pass"))

    def capture(_command, **kwargs):
        child = popen([sys.executable, '-c', script], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(computer_runtime.subprocess, 'Popen', capture)
    if delivers_frame:
        recorder = computer_runtime.Recorder(tmp_path, ':1', '1024x768')
        try:
            assert receipt.exists() and recorder.alive()
            assert recorder.manifest()['masking_captures'][0]['frames'] == 1
        finally:
            recorder.stop()
        assert recorder.log.closed
    else:
        with pytest.raises(ComputerError, match='first frame'):
            computer_runtime.Recorder(tmp_path, ':1', '1024x768')
        assert json.loads((tmp_path / 'manifest.json').read_text())['recording'] is False
    assert children[0].poll() is not None


def test_restart_recovers_finalized_csv_segment_missing_from_last_manifest(tmp_path):
    from computer_runtime import Recorder
    (tmp_path/'manifest.json').write_text(json.dumps({'recording': True, 'started_at': 100, 'segments': []}))
    (tmp_path/'capture-000004.json').write_text(json.dumps({
        'index': 'segments-000004.csv', 'offset': 150, 'started_at': 100, 'resolution': '1280x720', 'fps': 2}))
    (tmp_path/'segments-000004.csv').write_text('000004.mp4,0,30\n000005.mp4,30,')
    (tmp_path/'000004.mp4').write_bytes(b'finalized segment')
    (tmp_path/'000005.mp4').write_bytes(b'partial segment')
    value = Recorder.recover(tmp_path)
    assert value['recording'] is False
    assert len(value['segments']) == 1 and value['segments'][0]['start'] == 150
    assert value['segments'][0]['end'] == 180 and value['segments'][0]['sha256']
    assert value['unfinalized_files'] == ['000005.mp4']
    assert (tmp_path/'000005.mp4').read_bytes() == b'partial segment'


class Capture:
    def __init__(self, directory, display, resolution):
        self.running = True

    def alive(self):
        return self.running

    def manifest(self):
        return {"recording": self.running, "segments": []}

    def stop(self):
        self.running = False
        return self.manifest()


@pytest.fixture
def runtime(tmp_path):
    value = ComputerRuntime(tmp_path, recorder_factory=Capture)
    yield value
    value.close()


def uid():
    return str(uuid.uuid4())


def test_browser_auth_tracks_active_run_and_never_persists_cookie(runtime, monkeypatch):
    import computer_browser_auth
    first, second = uid(), uid()
    runtime.open_run(first, {})
    runtime.open_run(second, {})
    monkeypatch.setattr(computer_browser_auth, 'install', lambda *args: {'status': 'ready'})
    with pytest.raises(ComputerError, match='another run'):
        runtime.browser_auth(second, 'https://callie.example', 'private-cookie', time.time() + 300)
    assert runtime.browser_auth(first, 'https://callie.example', 'private-cookie', time.time() + 300)['status'] == 'ready'
    assert 'private-cookie' not in runtime.state_path.read_text()
    assert 'private-cookie' not in (runtime._directory(first) / 'events.jsonl').read_text()
    runtime.finish(first, 'Done')
    with pytest.raises(ComputerError, match='another run'):
        runtime.browser_auth(first, 'https://callie.example', 'private-cookie', time.time() + 300)
    def unavailable(*args):
        raise RuntimeError('an upstream failure mentioning private-cookie')
    monkeypatch.setattr(computer_browser_auth, 'install', unavailable)
    result = runtime.browser_auth(second, 'https://callie.example', 'private-cookie', time.time() + 300)
    assert result['status'] == 'pending' and 'private-cookie' not in json.dumps(result)


def test_shell_has_real_terminal_timed_output_and_stop_preserves_prefix(runtime, tmp_path):
    rid, jid = uid(), uid()
    marker = tmp_path / 'must-not-run'
    runtime.open_run(rid, {})
    code = f"test -t 1 || exit 9\nprintf '\\033[32mready café\\033[0m\\n'\nsleep 30\ntouch {marker}"
    runtime.execute(rid, jid, code=code, intent='Inspect PTY and cancellation', language='shell')
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        page = runtime.terminal(jid)
        if len(page['events']) > 1:
            break
        time.sleep(.02)
    assert page['events'][0]['version'] == 2
    assert 'café' in page['events'][1][2]
    assert not page['complete']
    runtime.control(rid, 'human')
    result = runtime.terminal(jid)
    assert result['status'] == 'cancelled' and result['complete']
    assert result['events'][:len(page['events'])] == page['events']
    assert not marker.exists()
    with pytest.raises(ComputerError, match='paused'):
        runtime.execute(rid, uid(), code='echo blocked', intent='Must respect human control', language='shell')


def wait_job(runtime, jid, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runtime.job(jid)
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    pytest.fail("Job did not settle")


def test_two_threads_share_one_writer_and_do_not_replay_duplicate_job(runtime, tmp_path):
    first, second, job = uid(), uid(), uid()
    runtime.open_run(first, {"session_id": "first"})
    assert runtime.open_run(second, {"session_id": "second"})["status"] == "queued"
    with pytest.raises(ComputerError, match="queued"):
        runtime.execute(second, uid(), code="print('wrong')", intent="Should queue")
    out = tmp_path / "count.txt"
    code = f"from pathlib import Path\np=Path({str(out)!r})\np.write_text(p.read_text()+'x' if p.exists() else 'x')\nprint('done')"
    runtime.execute(first, job, code=code, intent="Write one marker")
    assert wait_job(runtime, job)["stdout"] == "done\n"
    runtime.execute(first, job, code=code, intent="Write one marker")
    assert out.read_text() == "x"
    runtime.finish(first, "Marker verified")
    assert runtime.status()["active_run"] == second
    events = [json.loads(line) for line in (runtime.root / 'runs' / second / 'events.jsonl').read_text().splitlines()]
    admitted = next(event for event in events if event['type'] == 'run.started')
    assert admitted['payload']['queued_behind'] == first


def test_takeover_stops_code_and_blocks_new_jobs_until_resumed(runtime, tmp_path):
    run, job = uid(), uid()
    out = tmp_path / "should-not-exist"
    runtime.open_run(run, {})
    runtime.execute(run, job, code=f"import time\nfrom pathlib import Path\ntime.sleep(2)\nPath({str(out)!r}).touch()", intent="Wait then write")
    runtime.control(run, "human")
    assert runtime.job(job)["status"] == "cancelled"
    assert not out.exists()
    with pytest.raises(ComputerError, match="person"):
        runtime.execute(run, uid(), code="print(1)", intent="Blocked during demonstration")
    runtime.control(run, "agent")
    next_job = uid()
    runtime.execute(run, next_job, code="print('resumed')", intent="Verify resume")
    assert wait_job(runtime, next_job)["stdout"] == "resumed\n"


def test_capture_failure_cancels_execution_and_preserves_events(runtime):
    run, job = uid(), uid()
    runtime.open_run(run, {})
    runtime.execute(run, job, code="import time; time.sleep(20)", intent="Long job")
    runtime.recorder.running = False
    assert wait_job(runtime, job)["status"] == "recording_failed"
    assert runtime.status()["mode"] == "paused"
    events = [json.loads(x) for x in (runtime.root / "runs" / run / "events.jsonl").read_text().splitlines()]
    assert any(x["type"] == "recording.failed" for x in events)


def test_capture_failure_still_kills_writer_when_evidence_disk_is_full(runtime, monkeypatch):
    run, job = uid(), uid()
    runtime.open_run(run, {})
    runtime.execute(run, job, code='import time; time.sleep(20)', intent='Wait for disk failure')
    process = runtime.process
    revoked = []
    runtime.revoke_input = lambda: revoked.append(True)
    def full(*args):
        raise OSError(28, 'No space left on device')
    with monkeypatch.context() as patch:
        patch.setattr(runtime, '_save', full)
        patch.setattr(runtime, '_event', full)
        runtime.recorder.running = False
        deadline = time.monotonic() + 3
        while (process.poll() is None or not revoked) and time.monotonic() < deadline:
            time.sleep(.02)
        assert process.poll() is not None
        assert runtime.state['mode'] == 'paused'
        assert runtime.state['generation'] > 1 and revoked
        assert runtime.monitor.is_alive()


def test_restart_marks_uncertain_work_interrupted_without_executing(tmp_path):
    run, job = uid(), uid()
    first = ComputerRuntime(tmp_path, recorder_factory=Capture)
    first.open_run(run, {})
    first.execute(run, job, code="import time; time.sleep(20)", intent="Outstanding task")
    first.close()
    second = ComputerRuntime(tmp_path, recorder_factory=Capture)
    try:
        assert second.status()["active_run"] is None
        assert second.status()["runs"][run]["status"] == "interrupted"
        assert second.job(job)["status"] == "interrupted"
    finally:
        second.close()


def test_job_timeout_and_full_output_remain_inspectable(runtime):
    run, job = uid(), uid()
    runtime.open_run(run, {})
    runtime.execute(run, job, code="print('x'*40000); import time; time.sleep(20)", intent="Timeout test", timeout=1)
    result = wait_job(runtime, job)
    assert result["status"] == "timed_out"
    assert result["stdout_bytes"] == 40001
    assert len(result["stdout"]) == 32000
    assert runtime.job(job, offset=32000)["stdout"] == 'x'*8000+'\n'


def test_control_credentials_are_not_in_the_job_environment(runtime, monkeypatch):
    monkeypatch.setenv("CALLIOPE_DESKTOP_CONTROL_KEY", "private-control-key")
    monkeypatch.setenv("VNC_PW", "private-vnc-password")
    run, job = uid(), uid()
    runtime.open_run(run, {})
    runtime.execute(run, job, code="import os; print(os.getenv('CALLIOPE_DESKTOP_CONTROL_KEY'), os.getenv('VNC_PW'))", intent="Inspect job environment")
    assert wait_job(runtime, job)["stdout"] == "None None\n"


def test_human_input_requires_current_actor_and_generation(runtime):
    run = uid()
    runtime.open_run(run, {})
    state = runtime.control(run, 'human', 'first@example.com')
    runtime.human_input(run, state['generation'], 'first@example.com', {'kind': 'pointer', 'x': 1})
    runtime.control(run, 'human', 'second@example.com')
    with pytest.raises(ComputerError):
        runtime.human_input(run, state['generation'], 'first@example.com', {'kind': 'pointer', 'x': 2})
    with pytest.raises(ComputerError):
        runtime.finish(run, 'Cannot finish while human socket owns input')


def test_agent_pause_racing_human_takeover_cannot_revoke_new_owner(runtime):
    run = uid()
    runtime.open_run(run, {})
    generation = runtime.status()['generation']
    runtime.control(run, 'human', 'person@example.com')
    with pytest.raises(ComputerError, match='different driver'):
        runtime.control(run, 'paused', 'agent', expected_generation=generation)
    assert runtime.status()['mode'] == 'human'
    assert runtime.status()['human_actor'] == 'person@example.com'


def test_failed_revocation_leaves_input_paused_and_generation_persisted(runtime):
    run = uid()
    runtime.open_run(run, {})
    old = runtime.status()['generation']
    def fail():
        raise RuntimeError('driver unavailable')
    runtime.revoke_input = fail
    with pytest.raises(RuntimeError, match='unavailable'):
        runtime.control(run, 'human', 'person@example.com')
    state = json.loads(runtime.state_path.read_text())
    assert state['mode'] == 'paused' and state['human_actor'] is None
    assert state['generation'] > old
    runtime.revoke_input = lambda: None


def test_human_control_also_requires_recording(runtime):
    run = uid()
    runtime.open_run(run, {})
    runtime.recorder.running = False
    def broken_capture(*args):
        raise RuntimeError('No display')
    runtime.recorder_factory = broken_capture
    with pytest.raises(ComputerError, match='[Rr]ecording'):
        runtime.control(run, 'human', 'person@example.com')
    assert runtime.status()['mode'] == 'paused'


def test_capture_can_restart_without_replaying_cancelled_work(runtime):
    run, job = uid(), uid()
    runtime.open_run(run, {})
    runtime.execute(run, job, code='import time; time.sleep(20)', intent='Interrupted work')
    old = runtime.recorder
    old.running = False
    assert wait_job(runtime, job)['status'] == 'recording_failed'
    runtime.control(run, 'agent')
    assert runtime.recorder is not old and runtime.recorder.alive()
    assert runtime.status()['mode'] == 'agent'
    assert runtime.job(job)['status'] == 'recording_failed'
    assert runtime.process is None


def test_recorder_restart_preserves_segments_and_elapsed_gap(tmp_path, monkeypatch):
    import computer_runtime as module
    prior = {'fps': 2, 'resolution': '1024x768', 'started_at': time.time() - 60,
             'segments': [{'file': '000000.mp4', 'start': 0, 'end': 30, 'bytes': 3}]}
    (tmp_path / '000000.mp4').write_bytes(b'old')
    # A partial final segment must also be left intact rather than overwritten.
    (tmp_path / '000001.mp4').write_bytes(b'partial')
    (tmp_path / 'manifest.json').write_text(json.dumps(prior))
    commands = []
    class Process:
        def __init__(self, argv, **kwargs):
            commands.append(argv)
            (tmp_path / 'masking-000002.json').write_text('{"frames":1}')
        def poll(self):
            return None
    monkeypatch.setattr(module.subprocess, 'Popen', Process)
    recorder = module.Recorder(tmp_path, ':1', '1024x768')
    try:
        (tmp_path / '000002.mp4').write_bytes(b'new')
        recorder.index.write_text('000002.mp4,0.0,30.0\n')
        manifest = recorder.manifest()
        assert commands[0][commands[0].index('--start-number') + 1] == '2'
        assert manifest['segments'][0] == prior['segments'][0]
        assert manifest['segments'][1]['start'] >= 60
        assert (tmp_path / '000000.mp4').read_bytes() == b'old'
        assert (tmp_path / '000001.mp4').read_bytes() == b'partial'
    finally:
        recorder.log.close()


def test_stalled_capture_is_unhealthy_and_stop_still_terminates_its_process(tmp_path, monkeypatch):
    import computer_runtime as module
    signals = []
    class Process:
        def __init__(self, *args, **kwargs):
            self.done = False
            (tmp_path / 'masking-000000.json').write_text('{"frames":1}')
        def poll(self):
            return 0 if self.done else None
        def send_signal(self, sig):
            signals.append(sig)
            self.done = True
        def wait(self, timeout):
            return 0
    monkeypatch.setattr(module.subprocess, 'Popen', Process)
    recorder = module.Recorder(tmp_path, ':1', '1024x768')
    stale = time.time() - 6
    os.utime(recorder.masking_receipt, (stale, stale))
    assert not recorder.alive()
    result = recorder.stop()
    assert signals == [module.signal.SIGINT]
    assert not result['recording']


def test_recovery_preserves_masking_receipts(tmp_path):
    from computer_runtime import Recorder
    summary = {'policy': 'atspi-password-regions-v1', 'frames': 42,
               'masked_frames': 17, 'incomplete_frames': 2, 'recording': False}
    (tmp_path / 'masking-000003.json').write_text(json.dumps(summary))
    result = Recorder.recover(tmp_path)
    assert result['masking_captures'] == [{'first_segment': 3, **summary}]


@pytest.mark.parametrize('exited_before_kill', [False, True])
def test_stalled_capture_stop_kills_encoder_group_and_tolerates_exit_race(tmp_path, monkeypatch, exited_before_kill):
    import computer_runtime as module
    groups = []
    class Process:
        pid = 987654
        done = False
        def __init__(self, *args, **kwargs):
            (tmp_path / 'masking-000000.json').write_text('{"frames":1}')
        def poll(self):
            return 0 if self.done else None
        def send_signal(self, sig):
            pass  # Simulate an accessibility RPC that has not returned.
        def wait(self, timeout):
            if timeout == 6:
                raise module.subprocess.TimeoutExpired('capture', timeout)
            self.done = True
            return 0
    def kill_group(pid, sig):
        groups.append((pid, sig))
        if exited_before_kill:
            raise ProcessLookupError()
    monkeypatch.setattr(module.subprocess, 'Popen', Process)
    monkeypatch.setattr(module.os, 'killpg', kill_group)
    recorder = module.Recorder(tmp_path, ':1', '1024x768')
    assert not recorder.stop()['recording']
    assert groups == [(987654, module.signal.SIGKILL)]


def test_takeover_cancels_detached_descendant_after_script_parent_exits(runtime, tmp_path):
    run, job = uid(), uid()
    ready, output = tmp_path / 'detached-ready', tmp_path / 'detached-output'
    child = f"import pathlib,time; pathlib.Path({str(ready)!r}).touch(); time.sleep(1); pathlib.Path({str(output)!r}).touch()"
    code = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}], start_new_session=True)"
    runtime.open_run(run, {})
    runtime.execute(run, job, code=code, intent='Detached ordinary subprocess')
    deadline = time.monotonic() + 3
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(.02)
    assert ready.exists()
    assert runtime.job(job)['status'] == 'running'
    runtime.control(run, 'human', 'person@example.com')
    time.sleep(1.1)
    assert not output.exists()
    assert runtime.job(job)['status'] == 'cancelled'


def test_prune_archived_segments_is_terminal_bounded_verified_and_retryable(runtime):
    import hashlib
    rid = uid()
    runtime.open_run(rid, {})
    segment = {'file': '000000.mp4', 'bytes': 5, 'sha256': hashlib.sha256(b'video').hexdigest()}
    with pytest.raises(ComputerError, match='completed'):
        runtime.prune_recordings(rid, [segment])
    runtime.finish(rid, 'verified')
    directory = runtime.root / 'runs' / rid / 'video'
    directory.mkdir(exist_ok=True)
    (directory / '000000.mp4').write_bytes(b'video')
    (directory / '000001.mp4').write_bytes(b'tail')
    (directory / 'manifest.json').write_text(json.dumps({'segments': [segment], 'unfinalized_files': ['000001.mp4']}))
    for invalid in ([], [segment] * 101, [{**segment, 'file': '../000000.mp4'}],
                    [{**segment, 'sha256': '0' * 64}], [segment, {**segment, 'file': '000001.mp4'}]):
        with pytest.raises(ComputerError):
            runtime.prune_recordings(rid, invalid)
        assert (directory / '000000.mp4').read_bytes() == b'video'
    (directory / '000000.mp4').write_bytes(b'wrong')
    with pytest.raises(ComputerError, match='bytes changed'):
        runtime.prune_recordings(rid, [segment])
    (directory / '000000.mp4').write_bytes(b'video')
    assert runtime.prune_recordings(rid, [segment])['acknowledged'] == 1
    assert runtime.prune_recordings(rid, [segment])['acknowledged'] == 1
    assert not (directory / '000000.mp4').exists()
    assert (directory / '000001.mp4').read_bytes() == b'tail'
    assert (directory.parent / 'events.jsonl').exists()


def test_recording_fault_is_not_reported_as_human_permission(runtime):
    rid = uid()
    runtime.open_run(rid, {})
    runtime.recorder.running = False
    with runtime.lock:
        runtime._tick()
        assert runtime.state['pause_reason']['code'] == 'RECORDING_FAILED'
    with pytest.raises(ComputerError, match='system fault') as failure:
        runtime.execute(rid, uid(), code='print(1)', intent='Continue')
    assert failure.value.code == 'RECORDING_FAILED'
    runtime.control(rid, 'agent')
    assert 'pause_reason' not in runtime.status()


def test_independent_stream_cursors_do_not_hide_short_errors(runtime):
    run, job = uid(), uid()
    runtime.open_run(run, {})
    runtime.execute(run, job, code="import sys; print('x'*800); print('Yes button not found', file=sys.stderr); sys.exit(1)", intent='Cursor regression')
    result = wait_job(runtime, job)
    assert result['status'] == 'failed'
    # Old callers advancing stdout must still receive the shorter stderr.
    page = runtime.job(job, offset=result['stdout_next_offset'])
    assert page['stdout'] == '' and page['stderr'] == 'Yes button not found\n'
    page = runtime.job(job, stdout_offset=result['stdout_next_offset'], stderr_offset=result['stderr_next_offset'])
    assert page['stdout'] == page['stderr'] == ''
    assert page['stderr_next_offset'] == page['stderr_bytes']
    page = runtime.job(job, stdout_offset=100000, stderr_offset=100000)
    assert page['stdout_next_offset'] == page['stdout_bytes']
    assert page['stderr_next_offset'] == page['stderr_bytes']
