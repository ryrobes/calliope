"""Short-lived, session-scoped build snapshots. Never notebook evidence/memory."""
import fcntl
import json
from pathlib import Path
import time
from uuid import UUID

from artifact_progress import public_progress

TERMINAL_GRACE_SECONDS = 12
INACTIVE_DRAFT_SECONDS = 60
APP_PREVIEW_GRACE_SECONDS = 300
FOCUS_PHASES = {'composing', 'writing', 'preview', 'checking', 'publishing', 'published'}


def activity_time(event):
    return event.get('updated_at') or event['received_at']


def folder(config, session_id):
    return Path(config.file_root) / 'artifact-work' / str(UUID(str(session_id)))


def record(config, session_id, turn_id, payload, specialist=None):
    event = public_progress(payload)
    if not event:
        return
    specialist = specialist or {}
    agent_run = str(specialist.get('run_id') or turn_id)
    UUID(agent_run)
    path = folder(config, session_id)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = agent_run + '.' + event['build_id']
    file = path / (key + '.json')
    with (path / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = json.loads(file.read_text()) if file.exists() else {}
        if previous.get('updated_at', 0) >= event.get('updated_at', 0):
            return
        # A publishing receipt retires this incarnation. A subsequent write is
        # a new build; a delayed composing snapshot cannot resurrect publication.
        if previous.get('phase') == 'published' and event['phase'] in {'checking', 'publishing'}:
            return
        event = {**previous, **event, 'key': key, 'turn_id': str(turn_id),
                 'agent_run_id': agent_run, 'specialist_run_id': specialist.get('run_id'),
                 'agent_key': specialist.get('key') or 'calliope',
                 'started_at': previous.get('started_at') or time.time(), 'received_at': time.time()}
        temp = file.with_suffix('.part')
        temp.write_text(json.dumps(event))
        temp.replace(file)
        files = sorted(path.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[32:]:
            old.unlink(missing_ok=True)


def published(config, session_id, turn_id, specialist_run_id=None):
    path = folder(config, session_id)
    if not path.exists():
        return
    agent_run = str(specialist_run_id or turn_id)
    with (path / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for file in path.glob(agent_run + '.*.json'):
            event = json.loads(file.read_text())
            if event.get('phase') != 'publishing':
                continue
            event.update(phase='published', received_at=time.time(), updated_at=time.time())
            temp = file.with_suffix('.part')
            temp.write_text(json.dumps(event))
            temp.replace(file)


def snapshot(config, conn_factory, session_id):
    path = folder(config, session_id)
    now = time.time()
    if not path.exists():
        return []
    events = []
    for file in path.glob('*.json'):
        try:
            value = json.loads(file.read_text())
            if now - value.get('received_at', 0) < 7200:
                events.append(value)
        except (OSError, ValueError):
            continue
    if not events:
        return []
    with conn_factory() as conn:
        turns = conn.execute('SELECT id,status FROM rvbbit.calliope_turns WHERE session_id=%s::uuid AND id=ANY(%s::uuid[])',
                             (session_id, list({e['turn_id'] for e in events}))).fetchall()
        runs = conn.execute('SELECT id,status FROM rvbbit.calliope_specialist_runs WHERE source_session_id=%s::uuid AND id=ANY(%s::uuid[])',
                            (session_id, [e['specialist_run_id'] for e in events if e.get('specialist_run_id')])).fetchall()
    statuses = {str(r['id']): r['status'] for r in turns + runs}
    # Focus is per producing run, not notebook or agent name. Keep recently
    # edited siblings for multi-file/parallel writes. A quiet file stops being
    # live only after that run has demonstrably moved to another draft.
    # Include published successors even after their cards disappear, so an old
    # draft cannot return just because its replacement reached the Stage.
    latest = {}
    for event in events:
        if event.get('kind') == 'image' or event['phase'] not in FOCUS_PHASES:
            continue
        run = event['agent_run_id']
        if run not in latest or activity_time(event) > activity_time(latest[run]):
            latest[run] = event
    result = []
    for event in events:
        status = statuses.get(event['agent_run_id'])
        if event['phase'] in {'failed', 'stopped', 'published', 'image_ready'}:
            # A failed step is terminal even while its agent keeps working.
            # Image delivery gets extra grace for publication/SSE ordering.
            grace = 120 if event.get('kind') == 'image' and event['phase'] == 'image_ready' else TERMINAL_GRACE_SECONDS
            if now - event['received_at'] > grace:
                continue
        if event['phase'] == 'published':
            result.append(event)
            continue
        newer = latest.get(event['agent_run_id'])
        if (event.get('kind') != 'image' and event['phase'] not in {'failed', 'stopped'}
                and newer and newer['build_id'] != event['build_id']
                and activity_time(newer) > activity_time(event)
                and now - activity_time(event) >= INACTIVE_DRAFT_SECONDS):
            continue
        if status not in {'running', 'queued'}:
            # Native captures can finish after the authoring turn. Keep a
            # completed draft briefly available, without keeping cancelled work.
            if (status in {'complete', 'completed', 'succeeded'} and event.get('kind') == 'a2ui'
                    and event.get('render_app') and event['phase'] not in {'failed', 'stopped'}
                    and now - event['received_at'] <= APP_PREVIEW_GRACE_SECONDS):
                result.append({**event, 'phase': 'preview', 'producer_complete': True})
                continue
            # Never leave a misleading "building" card after cancellation,
            # observer loss, or gateway restart. Completion is authoritative.
            if now - event['received_at'] > TERMINAL_GRACE_SECONDS:
                continue
            event = {**event, 'phase': 'stopped'}
        result.append(event)
    return sorted(result, key=lambda e: e['started_at'])[-8:]
