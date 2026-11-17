"""Retention must survive interrupted cleanup without losing task evidence."""
import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

import desktop_storage as storage
import desktop_evidence as evidence


class Connection:
    def __init__(self, row=None):
        self.row = row
        self.result = None
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def transaction(self):
        return self
    def execute(self, sql, args=None):
        self.result = self.row if 'SELECT r.status' in sql else None
        return self
    def fetchone(self):
        return self.result


@pytest.fixture
def example(tmp_path, monkeypatch):
    config = SimpleNamespace(file_root=tmp_path)
    rid = str(uuid.uuid4())
    directory = evidence.directory(config, rid)
    (directory / 'video').mkdir(parents=True)
    video = b'archived test video'
    (directory / 'video/000000.mp4').write_bytes(video)
    (directory / 'events.jsonl').write_text('task receipts\n')
    segment = {'file': '000000.mp4', 'bytes': len(video), 'sha256': hashlib.sha256(video).hexdigest()}
    evidence.write_json(directory / 'manifest.json', {'segments': [segment]})
    item = storage.inventory(config)[0]
    item.update(terminal=True, archived=True, ended_at=100, keep=False, requester='person@example.com')
    receipts = []
    monkeypatch.setattr(storage.access, 'append_event', lambda *args: receipts.append(args))
    return SimpleNamespace(config=config, item=item, receipts=receipts, segment=segment, directory=directory)


def test_retention_protects_active_kept_and_unverified_runs(example):
    item = example.item
    storage.verify_archive(item)
    gib = 1024 ** 3
    active = {**item, 'id': 'active', 'terminal': False, 'bytes': 2*gib}
    kept = {**item, 'id': 'kept', 'keep': True, 'bytes': gib}
    recent = {**item, 'id': 'recent', 'ended_at': 999, 'bytes': gib}
    old = {**item, 'id': 'old', 'ended_at': 1, 'bytes': gib}
    unverified = {**item, 'id': 'unverified', 'manifest': {}, 'bytes': 0}
    chosen = storage.candidates([active, kept, recent, old, unverified], {'retention_days': 0, 'max_video_gib': 4}, 1000)
    assert [(entry['id'], reason) for entry, reason in chosen] == [('old', 'capacity')]
    chosen = storage.candidates([old, kept, active], {'retention_days': 1, 'max_video_gib': 0}, 90000)
    assert [(entry['id'], reason) for entry, reason in chosen] == [('old', 'age')]
    assert not storage.candidates([old], {'retention_days': 0, 'max_video_gib': 0}, 90000)


def test_guest_offline_expiry_then_retry_keeps_checksum_outbox_and_receipts(example):
    x = example
    requests = []
    class Guest:
        offline = True
        def request(self, method, path, body):
            requests.append((method, path, body))
            if self.offline:
                raise OSError('offline')
    guest = Guest()
    with pytest.raises(OSError):
        storage.prune_guest(Connection, guest, x.item)
    assert x.item['manifest']['archive_verified_at']
    storage.expire(Connection, x.config, x.item, 'age')
    assert not (x.directory / 'video/000000.mp4').exists()
    assert (x.directory / 'events.jsonl').read_text() == 'task receipts\n'
    x.item['manifest'] = json.loads((x.directory / 'manifest.json').read_text())
    assert x.item['manifest']['segments'] == []
    guest.offline = False
    storage.prune_guest(Connection, guest, x.item)
    assert requests[-1][2]['segments'] == [x.segment]
    assert x.receipts[-1][3] == 'evidence.guest_pruned'


def test_checksum_mismatch_keeps_guest_and_prevents_expiry(example):
    x = example
    (x.directory / 'video/000000.mp4').write_bytes(b'x' * x.segment['bytes'])
    with pytest.raises(ValueError, match='checksum'):
        storage.verify_archive(x.item)
    assert not storage.candidates([x.item], {'retention_days': 1, 'max_video_gib': 0}, 9999999)
    assert 'archive_verified_at' not in json.loads((x.directory / 'manifest.json').read_text())


def test_tombstone_precedes_deletion_and_retries_after_interruption(example, monkeypatch):
    x = example
    storage.verify_archive(x.item)
    from pathlib import Path
    original = Path.unlink
    def interrupted(path, *args, **kwargs):
        if path.suffix == '.mp4':
            assert json.loads((x.directory / 'manifest.json').read_text())['expired_at']
            raise OSError('interrupted cleanup')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', interrupted)
        with pytest.raises(OSError):
            storage.expire(Connection, x.config, x.item, 'capacity')
    item = storage.inventory(x.config)[0]
    item.update(terminal=True, archived=True, ended_at=100, keep=False, requester='person@example.com')
    selected = storage.candidates([item], {'retention_days': 0, 'max_video_gib': 0}, 1000)
    assert selected[0][1] == 'capacity'
    storage.expire(Connection, x.config, item, 'capacity')
    assert not (x.directory / 'video/000000.mp4').exists()
    assert (x.directory / 'events.jsonl').exists()
    item['bytes'] = 0
    assert storage.candidates([item], {'retention_days': 0, 'max_video_gib': 0}, 1000)  # Retry lost receipt.
    item['expiry_recorded'] = True
    assert not storage.candidates([item], {'retention_days': 0, 'max_video_gib': 0}, 1000)


def test_maintenance_expires_archive_even_with_guest_unavailable(example):
    x = example
    evidence.write_json(storage.root(x.config) / 'storage-policy.json', {'retention_days': 1, 'max_video_gib': 0})
    row = {'status': 'finished', 'ended_at': datetime.fromtimestamp(100, timezone.utc), 'requester': 'person@example.com',
           'archived': True, 'guest_pruned': False, 'expiry_recorded': False}
    class Guest:
        def request(self, *args):
            raise OSError('offline')
    storage.maintain(lambda: Connection(row), x.config, Guest())
    assert json.loads((x.directory / 'manifest.json').read_text())['expired_at']
    assert (x.directory / 'events.jsonl').exists()


def test_inventory_ignores_malformed_manifests_and_symlinked_runs(example):
    x = example
    bad = storage.root(x.config) / str(uuid.uuid4())
    bad.mkdir()
    (bad / 'manifest.json').write_text('[]')
    alias = storage.root(x.config) / str(uuid.uuid4())
    alias.symlink_to(x.directory, target_is_directory=True)
    assert len(storage.inventory(x.config)) == 1


@pytest.mark.parametrize('value', [[], {}, {'retention_days': True, 'max_video_gib': 2},
    {'retention_days': -1, 'max_video_gib': 2}, {'retention_days': 1.5, 'max_video_gib': 2}])
def test_invalid_storage_settings_are_rejected(value):
    with pytest.raises(ValueError):
        storage.validate(value)


def test_pin_is_authorized_and_cannot_resurrect_expired_video(example, monkeypatch):
    x = example
    seen = []
    def authorize(factory, sid, actor, rid):
        seen.append((sid, actor, rid))
        return {'id': rid}
    monkeypatch.setattr(storage.access, 'run_for_session', authorize)
    result = storage.set_keep(Connection, x.config, 'session', x.item['id'], 'person', True)
    assert result['keep_recording'] and seen == [('session', 'person', x.item['id'])]
    assert x.receipts[-1][3:5] == ('recording.retention', {'keep': True})
    storage.verify_archive(x.item)
    storage.expire(Connection, x.config, x.item, 'age')
    with pytest.raises(storage.access.DesktopError) as error:
        storage.set_keep(Connection, x.config, 'session', x.item['id'], 'person', True)
    assert error.value.status == 410


def test_replay_counts_toward_budget_and_expires_with_originals(example):
    x = example
    compact = x.directory / 'video/replay.mp4'
    compact.write_bytes(b'compact replay')
    poster=x.directory/'video/thumbnail.jpg'
    poster.write_bytes(b'jpeg preview')
    (x.directory/'replay.json').write_text(json.dumps({'status':'ready'}))
    assert storage.inventory(x.config)[0]['bytes'] == x.segment['bytes'] + compact.stat().st_size + poster.stat().st_size
    storage.verify_archive(x.item)
    storage.expire(Connection, x.config, x.item, 'capacity')
    assert not compact.exists()
    assert not poster.exists()
    assert not (x.directory/'video/000000.mp4').exists()
    assert (x.directory/'events.jsonl').exists()
