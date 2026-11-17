import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

import desktop_evidence as evidence


class Connection:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, *args):
        pass


class Guest:
    def __init__(self, files):
        self.files, self.downloads = files, []

    def download(self, key, target, sha256=None):
        self.downloads.append(key)
        data = self.files[key]
        if sha256:
            assert hashlib.sha256(data).hexdigest() == sha256
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


@pytest.fixture
def example(tmp_path, monkeypatch):
    rid, jid = str(uuid.uuid4()), str(uuid.uuid4())
    event = {'id': str(uuid.uuid4()), 'run_id': rid, 'at': 100.0, 'type': 'run.finished', 'payload': {'outcome': 'Verified output'}}
    video = b'finalized-video-fixture'
    manifest = {'started_at': 90.0, 'recording': False,
        'masking_captures': [{'first_segment': 0, 'policy': 'atspi-password-regions-v1',
                             'frames': 60, 'masked_frames': 20, 'incomplete_frames': 1}], 'segments': [
        {'file': '000000.mp4', 'start': 0, 'end': 30, 'bytes': len(video), 'sha256': hashlib.sha256(video).hexdigest()}]}
    prefix = f'computer/runs/{rid}/'
    guest = Guest({prefix+'events.jsonl': (json.dumps(event)+'\n').encode(),
                   prefix+'video/manifest.json': json.dumps(manifest).encode(), prefix+'video/000000.mp4': video,
                   prefix+f'jobs/{jid}/code.py': b'print(42)', prefix+f'jobs/{jid}/stdout.txt': b'42\n',
                   prefix+f'jobs/{jid}/stderr.txt': b''})
    state = {'runs': {rid: {'status': 'finished'}},
             'jobs': {jid: {'id': jid, 'run_id': rid, 'status': 'completed', 'language': 'python'}}}
    run = {'id': rid, 'requester': 'person@example.com', 'status': 'finished'}
    receipts = []
    monkeypatch.setattr(evidence.access, 'append_event', lambda *args: receipts.append(args))
    return SimpleNamespace(config=SimpleNamespace(file_root=tmp_path), guest=guest, state=state,
                           run=run, receipts=receipts, prefix=prefix, video=video, jid=jid, manifest=manifest)


def test_complete_run_keeps_video_code_output_and_receipts_off_guest(example):
    x = example
    evidence.archive_run(Connection, x.config, x.guest, x.run, x.state)
    target = evidence.directory(x.config, x.run['id'])
    x.guest.files.clear()  # Guest replacement cannot remove the archived files.
    assert (target/'video/000000.mp4').read_bytes() == x.video
    assert (target/'jobs'/x.jid/'code.py').read_text() == 'print(42)'
    assert (target/'jobs'/x.jid/'stdout.txt').read_text() == '42\n'
    assert json.loads((target/'manifest.json').read_text())['storage'] == 'appliance_archive'
    assert json.loads((target/'manifest.json').read_text())['masking_captures'] == x.manifest['masking_captures']
    assert x.receipts[-1][3] == 'evidence.archived'


def test_retry_does_not_redownload_verified_finalized_files(example):
    x = example
    evidence.archive_run(Connection, x.config, x.guest, x.run, x.state)
    evidence.archive_run(Connection, x.config, x.guest, x.run, x.state)
    assert x.guest.downloads.count(x.prefix+'video/000000.mp4') == 1
    assert x.guest.downloads.count(x.prefix+f'jobs/{x.jid}/stdout.txt') == 1


def test_live_terminal_is_copied_off_guest_and_cannot_rewrite_archived_prefix(example):
    x = example
    job = x.state['jobs'][x.jid]
    job.update(terminal={'format': 'asciicast-v2'}, language='shell', status='running')
    key = x.prefix + f'jobs/{x.jid}/terminal.cast'
    original = b'{"version":2}\n[0,"o","first"]\n'
    x.guest.files[key] = original
    evidence.archive_run(Connection, x.config, x.guest, x.run, x.state)
    target = evidence.directory(x.config, x.run['id']) / 'jobs' / x.jid
    assert (target / 'terminal.cast').read_bytes() == original
    assert not (target / 'archived.json').exists()
    x.guest.files[key] = b'{"version":2}\n[0,"o","rewritten"]\n'
    with pytest.raises(ValueError, match='previously archived'):
        evidence.archive_run(Connection, x.config, x.guest, x.run, x.state)
    assert (target / 'terminal.cast').read_bytes() == original
    x.guest.files[key] = original + b'[1,"o","last"]\n'
    x.guest.files[x.prefix + f'jobs/{x.jid}/code.sh'] = b'echo first; echo last'
    job['status'] = 'completed'
    evidence.archive_run(Connection, x.config, x.guest, x.run, x.state)
    archived = json.loads((target / 'archived.json').read_text())
    assert archived['terminal']['sha256'] == hashlib.sha256(x.guest.files[key]).hexdigest()


def test_truncated_live_journal_waits_for_complete_trailing_event(example):
    x = example
    x.run['status'] = 'active'
    x.guest.files[x.prefix+'events.jsonl'] += b'{"id":"partially-written'
    evidence.archive_run(Connection, x.config, x.guest, x.run, x.state)
    assert len(x.receipts) == 1
    assert x.receipts[0][3] == 'guest.run.finished'


def test_manifest_cannot_escape_run_archive(example):
    x = example
    x.manifest['segments'][0]['file'] = '../wrong.mp4'
    x.guest.files[x.prefix+'video/manifest.json'] = json.dumps(x.manifest).encode()
    with pytest.raises(ValueError, match='segment name'):
        evidence.archive_run(Connection, x.config, x.guest, x.run, x.state)


def test_completion_after_state_snapshot_is_not_prematurely_marked_archived(example):
    x = example
    x.state['runs'][x.run['id']]['status'] = 'active'
    x.state['jobs'][x.jid]['status'] = 'running'
    evidence.archive_run(Connection, x.config, x.guest, x.run, x.state)
    assert not any(receipt[3] == 'evidence.archived' for receipt in x.receipts)


def test_only_current_handoff_generation_wakes_the_task(example, monkeypatch):
    x = example
    x.run['status'] = 'active'
    x.state.update(active_run=x.run['id'], mode='agent', generation=6)
    notified = []
    monkeypatch.setattr(evidence.access, 'ready_message', lambda *args: notified.append(args))
    def event(generation):
        return {'id': str(uuid.uuid4()), 'run_id': x.run['id'], 'at': 100.0,
                'type': 'control.changed', 'payload': {'mode': 'agent', 'generation': generation}}
    x.guest.files[x.prefix+'events.jsonl'] = (json.dumps(event(5))+'\n').encode()
    evidence.archive_run(Connection, x.config, x.guest, x.run, x.state)
    assert not notified
    x.guest.files[x.prefix+'events.jsonl'] += (json.dumps(event(6))+'\n').encode()
    evidence.archive_run(Connection, x.config, x.guest, x.run, x.state)
    assert len(notified) == 1 and notified[0][1:] == (x.run['id'], 6)
