"""Video retention for Computer; task receipts and kept examples outlive video."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid

import desktop_access as access

LOCK = "calliope-computer-evidence"
DEFAULTS = {"retention_days": 30, "max_video_gib": 20}
SEGMENT = re.compile(r"\d{6}\.mp4")
VIDEO = re.compile(r"(?:\d{6}|replay)\.mp4")
MEDIA = re.compile(r"(?:(?:\d{6}|replay)\.mp4|thumbnail\.jpg)")


def root(config):
    return Path(config.file_root) / "computer-evidence"


def policy(config):
    path = root(config) / "storage-policy.json"
    return validate(json.loads(path.read_text())) if path.exists() else dict(DEFAULTS)


def validate(value):
    if not isinstance(value, dict):
        raise ValueError('Storage settings must be an object')
    result = {}
    for key, maximum in (("retention_days", 36500), ("max_video_gib", 100000)):
        number = value.get(key)
        if type(number) is not int or not 0 <= number <= maximum:
            raise ValueError(f"{key} must be a whole number between 0 and {maximum}; 0 disables this limit")
        result[key] = number
    return result


def save_policy(conn_factory, config, value, actor):
    from desktop_evidence import write_json
    settings = validate(value)
    with conn_factory() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (LOCK,))
        write_json(root(config) / "storage-policy.json", {
            **settings, "updated_by": actor, "updated_at": datetime.now(timezone.utc).isoformat()})
    return settings


def lock(conn):
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (LOCK,))


def kept(conn, rid):
    row = conn.execute(
        "SELECT payload FROM rvbbit.calliope_workbench_events WHERE desktop_run_id=%s::uuid "
        "AND event_type='recording.retention' ORDER BY sequence DESC LIMIT 1", (rid,)).fetchone()
    return bool(row and row['payload'].get('keep'))


def set_keep(conn_factory, config, session_id, run_id, actor, keep):
    if type(keep) is not bool:
        raise ValueError('keep must be true or false')
    # Pinning and deletion use the same lock across server processes.
    with conn_factory() as conn, conn.transaction():
        lock(conn)
        run = access.run_for_session(conn_factory, session_id, actor, run_id)
        manifest = root(config) / run['id'] / 'manifest.json'
        if manifest.exists() and json.loads(manifest.read_text()).get('expired_at'):
            raise access.DesktopError('RECORDING_EXPIRED', 'This recording has already expired; its receipts remain available.', 410)
        access.append_event(conn_factory, run['id'], actor, 'recording.retention',
                            {'keep': keep}, 'retention:' + str(uuid.uuid4()))
    return {'keep_recording': keep}


def inventory(config):
    result = []
    for path in root(config).glob('*/manifest.json'):
        try:
            rid = str(uuid.UUID(path.parent.name))
            if path.parent.is_symlink() or path.is_symlink() or (path.parent / 'video').is_symlink():
                continue
            manifest = json.loads(path.read_text())
            if not isinstance(manifest, dict):
                continue
            size = sum(p.stat().st_size for p in (path.parent / 'video').glob('*')
                       if MEDIA.fullmatch(p.name) and p.is_file() and not p.is_symlink())
            result.append({'id': rid, 'manifest': manifest, 'bytes': size, 'path': path.parent})
        except (ValueError, OSError):
            continue
    return result


def status(config):
    items = inventory(config)
    return {**policy(config), 'video_bytes': sum(item['bytes'] for item in items),
            'recorded_runs': sum(bool(item['bytes']) for item in items)}


def candidates(items, settings, now):
    """Oldest completed unpinned video first; active/kept bytes still count."""
    total = sum(item['bytes'] for item in items)
    cap = settings['max_video_gib'] * 1024 ** 3
    age = settings['retention_days'] * 86400
    result = []
    for item in sorted(items, key=lambda item: item['ended_at'] or now):
        if (not item['terminal'] or not item['archived'] or item['keep'] or not item['ended_at']
                or not item['manifest'].get('archive_verified_at')
                or (not item['bytes'] and (not item['manifest'].get('expired_at') or item.get('expiry_recorded')))):
            continue
        reason = ('age' if age and now - item['ended_at'] >= age else
                  'capacity' if cap and total > cap else None)
        if item['manifest'].get('expired_at'):
            reason = item['manifest'].get('expiry_reason', 'age')
        if reason:
            result.append((item, reason))
            total -= item['bytes']
    return result


def expire(conn_factory, config, item, reason):
    from desktop_evidence import write_json
    manifest = item['manifest']
    target = item['path']
    # Publish the tombstone first. A crash can leave files for the next sweep,
    # but a request must never silently resurrect expired video from the guest.
    if not manifest.get('expired_at'):
        manifest = {**manifest, 'expired_at': datetime.now(timezone.utc).isoformat(),
                    'expiry_reason': reason, 'original_segments': manifest.get('segments', []), 'segments': []}
        write_json(target / 'manifest.json', manifest)
    for path in (target / 'video').glob('*'):
        if MEDIA.fullmatch(path.name):
            path.unlink(missing_ok=True)
    with conn_factory() as conn:
        conn.execute('UPDATE rvbbit.calliope_workbench_recordings SET expires_at=%s::timestamptz '
                     'WHERE desktop_run_id=%s::uuid', (manifest['expired_at'], item['id']))
    access.append_event(conn_factory, item['id'], item['requester'], 'recording.expired',
                        {'reason': reason, 'expired_at': manifest['expired_at'], 'bytes': item['bytes']}, 'recording.expired')


def verify_archive(item):
    """Persist a cleanup outbox before either copy can be removed.

    The verified checksum list survives video expiry, allowing a guest that
    was offline during retention to acknowledge its cleanup on a later pass.
    """
    from desktop_evidence import write_json
    manifest = item['manifest']
    if manifest.get('archive_verified_at'):
        return
    if manifest.get('expired_at'):
        raise ValueError('Expired manifest lacks archive verification')
    for segment in manifest.get('segments', []):
        name = segment['file']
        if not SEGMENT.fullmatch(name):
            raise ValueError('Invalid recording segment name')
        path = item['path'] / 'video' / name
        if path.is_symlink() or path.stat().st_size != segment['bytes']:
            raise ValueError('Archive length mismatch; guest copy retained')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != segment['sha256']:
            raise ValueError('Archive checksum mismatch; guest copy retained')
    manifest = {**manifest, 'archive_verified_at': datetime.now(timezone.utc).isoformat()}
    write_json(item['path'] / 'manifest.json', manifest)
    item['manifest'] = manifest


def prune_guest(conn_factory, runtime, item):
    """Release only finalized guest segments verified in the archive outbox."""
    verify_archive(item)
    manifest = item['manifest']
    segments = manifest.get('original_segments', []) if manifest.get('expired_at') else manifest.get('segments', [])
    for start in range(0, len(segments), 100):
        runtime.request('POST', 'recordings/archived', {'run_id': item['id'], 'segments': segments[start:start + 100]})
    access.append_event(conn_factory, item['id'], item['requester'], 'evidence.guest_pruned',
                        {'segments': len(segments), 'unfinalized_files': manifest.get('unfinalized_files', [])}, 'evidence.guest_pruned')


def maintain(conn_factory, config, runtime):
    import logging
    log = logging.getLogger(__name__)
    # This may run even while the guest is unavailable; archive retention is
    # independent of the remote computer's availability.
    with conn_factory() as conn, conn.transaction():
        lock(conn)
        items = inventory(config)
        guest_available = True
        for item in items:
            row = conn.execute(
                "SELECT r.status,r.ended_at,r.requester,"
                "EXISTS(SELECT 1 FROM rvbbit.calliope_workbench_events e WHERE e.desktop_run_id=r.id "
                "AND e.event_type='evidence.archived') AS archived,"
                "EXISTS(SELECT 1 FROM rvbbit.calliope_workbench_events e WHERE e.desktop_run_id=r.id "
                "AND e.event_type='evidence.guest_pruned') AS guest_pruned,"
                "EXISTS(SELECT 1 FROM rvbbit.calliope_workbench_events e WHERE e.desktop_run_id=r.id "
                "AND e.event_type='recording.expired') AS expiry_recorded "
                "FROM rvbbit.calliope_desktop_runs r WHERE r.id=%s::uuid", (item['id'],)).fetchone()
            if not row:
                item.update(terminal=False, archived=False, keep=True, ended_at=None)
                continue
            item.update(terminal=row['status'] in {'finished', 'failed', 'interrupted'},
                        archived=row['archived'], keep=kept(conn, item['id']),
                        ended_at=row['ended_at'].timestamp() if row['ended_at'] else None,
                        expiry_recorded=row['expiry_recorded'],
                        requester=row['requester'])
            if item['terminal'] and item['archived'] and not row['guest_pruned']:
                try:
                    verify_archive(item)
                except Exception as exc:
                    log.warning('Computer archive verification deferred for %s: %s', item['id'], type(exc).__name__)
                    continue
                if guest_available:
                    try:
                        prune_guest(conn_factory, runtime, item)
                    except Exception as exc:
                        # One unavailable guest must not cost one timeout per
                        # archived run or prevent the appliance retention pass.
                        guest_available = False
                        log.warning('Computer guest video cleanup deferred for %s: %s', item['id'], type(exc).__name__)
        for item, reason in candidates(items, policy(config), datetime.now(timezone.utc).timestamp()):
            expire(conn_factory, config, item, reason)
