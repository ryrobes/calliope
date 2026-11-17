"""Replay processing must preserve actions, receipt time, and original evidence."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

import desktop_replay as replay


def test_idle_cuts_keep_context_actions_and_short_waits():
    freezes = [[0, 30], [35, 39], [40, 60]]
    assert replay.retained_ranges(freezes, 60, [15]) == [[0, 2], [13, 17], [28, 42], [58, 60]]
    assert replay.retained_ranges([[0, 9]], 9) == [[0, 9]]
    assert replay.freeze_ranges('[x] lavfi.freezedetect.freeze_start: 2\n[x] lavfi.freezedetect.freeze_end: 15\n[x] lavfi.freezedetect.freeze_start: 20', 40) == [[2, 15], [20, 40]]


def test_time_map_preserves_original_clock_across_recorder_restart():
    spans = [{'input_start':0, 'input_end':30, 'source_start':.5},
             {'input_start':30, 'input_end':60, 'source_start':50}]
    assert replay.timeline(spans, [[0, 2], [28, 32], [58, 60]]) == [
        {'source_start':.5,'source_end':2.5,'replay_start':0,'replay_end':2},
        {'source_start':28.5,'source_end':30.5,'replay_start':2,'replay_end':4},
        {'source_start':50,'source_end':52,'replay_start':4,'replay_end':6},
        {'source_start':78,'source_end':80,'replay_start':6,'replay_end':8}]


@pytest.fixture
def footage(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('ffmpeg and ffprobe required')
    target = tmp_path / 'run'
    video = target / 'video'
    video.mkdir(parents=True)
    segments = []
    for i, color in enumerate(['blue', 'blue', 'red']):
        path = video / f'{i:06d}.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=c={color}:s=320x240:r=2:d=15',
                        '-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(path)],check=True)
        segments.append({'file':path.name,'start':i*15,'end':(i+1)*15,'bytes':path.stat().st_size,
                         'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {'started_at':1000,'recording':False,'segments':segments}
    (target/'manifest.json').write_text(json.dumps(manifest))
    # Protect an otherwise invisible action during the first long wait.
    (target/'events.jsonl').write_text(json.dumps({'type':'execution.finished','at':1015,'payload':{}})+'\n')
    return target, manifest


def test_real_ffmpeg_builds_one_seekable_timestamped_replay(footage, tmp_path):
    target, manifest = footage
    with tempfile.TemporaryDirectory(dir=target/'video') as directory:
        work = Path(directory)
        metadata = replay.build(target, manifest, work)
        assert metadata['status'] == 'ready'
        assert metadata['source_duration'] == 45
        assert metadata['duration'] < 20
        assert metadata['skipped_seconds'] > 25
        assert any(span['source_start'] <= 15 < span['source_end'] for span in metadata['timeline'])
        assert metadata['timeline'][0]['source_start'] == 0
        assert metadata['timeline'][-1]['source_end'] == 45
        assert metadata['timestamp'] == 'original_session_elapsed'
        result = (work/'replay.mp4').read_bytes()
        assert result.index(b'moov') < result.index(b'mdat')
        # Save an actual frame for visual inspection of the burned-in source clock.
        subprocess.run(['ffmpeg','-v','error','-ss','3','-i',str(work/'replay.mp4'),'-frames:v','1',
                        '-y','/tmp/computer-replay-timestamp.png'],check=True)
        assert metadata['bytes'] == len(result)
    for segment in manifest['segments']:
        assert hashlib.sha256((target/'video'/segment['file']).read_bytes()).hexdigest() == segment['sha256']


class Connection:
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def transaction(self): return self
    def execute(self, *_): pass


def test_thumbnail_uses_archived_footage_and_is_cached(footage, monkeypatch):
    target,manifest=footage
    monkeypatch.setattr(replay,'directory',lambda *_:target)
    image=replay.thumbnail(Connection,None,'run')
    assert image.startswith(b'\xff\xd8') and image.endswith(b'\xff\xd9')
    monkeypatch.setattr(replay,'command',lambda *_args,**_kw:pytest.fail('Thumbnail should be cached'))
    assert replay.thumbnail(Connection,None,'run')==image
    (target/'manifest.json').write_text(json.dumps({**manifest,'expired_at':'now'}))
    with pytest.raises(replay.access.DesktopError,match='expired'):
        replay.thumbnail(Connection,None,'run')


def test_thumbnail_cannot_publish_after_retention_expires_video(footage,monkeypatch):
    target,manifest=footage
    monkeypatch.setattr(replay,'directory',lambda *_:target)
    def encode(args,**_kwargs):
        Path(args[-1]).write_bytes(b'jpeg')
        (target/'manifest.json').write_text(json.dumps({**manifest,'expired_at':'now'}))
    monkeypatch.setattr(replay,'command',encode)
    with pytest.raises(replay.access.DesktopError,match='expired'):
        replay.thumbnail(Connection,None,'run')
    assert not (target/'video/thumbnail.jpg').exists()


def test_processing_failure_keeps_originals_and_retries_later(footage, monkeypatch):
    target, manifest = footage
    monkeypatch.setattr(replay, 'build', lambda *_: (_ for _ in ()).throw(RuntimeError('encoder stopped')))
    item = {'id':'run', 'path':target}
    replay.process(Connection, None, item, 'viewer')
    metadata = json.loads((target/'replay.json').read_text())
    assert metadata['status'] == 'deferred'
    assert metadata['retry_after'] > replay.time.time()
    assert not (target/'video/replay.mp4').exists()
    assert json.loads((target/'manifest.json').read_text()) == manifest
    assert all((target/'video'/s['file']).is_file() for s in manifest['segments'])
    monkeypatch.setattr(replay, 'build', lambda *_: pytest.fail('Respect retry backoff'))
    replay.process(Connection, None, item, 'viewer')


def test_expiry_during_encoding_cannot_resurrect_video(footage, monkeypatch):
    target, manifest = footage
    def encode(_target, _manifest, work):
        (work/'replay.mp4').write_bytes(b'finished video')
        (target/'manifest.json').write_text(json.dumps({**manifest,'expired_at':'now','segments':[]}))
        return {'source_signature':replay.source_signature(manifest)}
    monkeypatch.setattr(replay, 'build', encode)
    replay.process(Connection, None, {'id':'run','path':target}, 'viewer')
    assert not (target/'video/replay.mp4').exists()
    assert not (target/'replay.json').exists()


def test_publish_is_idempotent_and_records_summary(footage, monkeypatch):
    target, manifest = footage
    calls=[]
    def encode(_target, _manifest, work):
        calls.append('encode')
        (work/'replay.mp4').write_bytes(b'finished video')
        return {'status':'ready','version':replay.VERSION,'bytes':14,'duration':10,'source_duration':45,
                'skipped_seconds':35,'source_signature':replay.source_signature(manifest)}
    monkeypatch.setattr(replay, 'build', encode)
    receipts=[]
    monkeypatch.setattr(replay.access, 'append_event', lambda *args:receipts.append(args))
    item={'id':'run','path':target}
    replay.process(Connection, None, item, 'viewer')
    assert (target/'video/replay.mp4').read_bytes() == b'finished video'
    assert json.loads((target/'replay.json').read_text())['status'] == 'ready'
    assert receipts[0][3] == 'recording.replay_ready'
    replay.process(Connection, None, item, 'viewer')
    assert calls == ['encode']
