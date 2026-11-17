"""Build one idle-trimmed replay after capture, without touching the live guest.

Original chunks remain the evidence source and fallback. The replay and its
source-time map are published together; retention accounts for both copies.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time

import desktop_access as access
from desktop_evidence import directory, write_json
import desktop_storage as storage

FPS = 2
IDLE_SECONDS = 10
CONTEXT_SECONDS = 2
VERSION = 1
log = logging.getLogger(__name__)
_thumbnail_lock = threading.Lock()


def thumbnail(conn_factory, config, run_id):
    """Small, cached preview of archived footage; never fetch or record the desktop.

    The route authorizes the run first. Retention owns this image exactly as it
    owns its source video, including an expiry check after encoding.
    """
    target = directory(config, run_id)
    manifest_path = target / 'manifest.json'
    poster = target / 'video' / 'thumbnail.jpg'

    def read_manifest():
        if not manifest_path.is_file():
            raise access.DesktopError('EVIDENCE_PENDING', 'Recording preview is not ready yet.', 404)
        value = json.loads(manifest_path.read_text())
        if value.get('expired_at'):
            raise access.DesktopError('RECORDING_EXPIRED', 'This recording has expired.', 410)
        return value

    with _thumbnail_lock:
        manifest = read_manifest()
        if poster.is_file() and not poster.is_symlink():
            return poster.read_bytes()
        segments = manifest.get('segments') or []
        if not segments:
            raise access.DesktopError('EVIDENCE_PENDING', 'Recording preview is not ready yet.', 404)
        segment = segments[0]
        name = str(segment.get('file', ''))
        if not storage.SEGMENT.fullmatch(name):
            raise ValueError('Invalid thumbnail source')
        source = target / 'video' / name
        if source.is_symlink() or not source.is_file() or source.stat().st_size != segment['bytes']:
            raise access.DesktopError('EVIDENCE_PENDING', 'Recording preview is not ready yet.', 404)
        with tempfile.TemporaryDirectory(prefix='.thumbnail-', dir=target / 'video') as temp:
            output = Path(temp) / 'thumbnail.jpg'
            command(['ffmpeg', '-v', 'error', '-nostdin', '-threads', '1', '-filter_threads', '1',
                     '-i', str(source), '-vf', 'thumbnail=8,scale=640:-2:force_original_aspect_ratio=decrease',
                     '-frames:v', '1', '-threads', '1', '-q:v', '5', '-y', str(output)], timeout=20)
            with conn_factory() as conn, conn.transaction():
                storage.lock(conn)
                read_manifest()  # Do not publish an image after its video expires.
                output.replace(poster)
                return poster.read_bytes()


def command(args, *, timeout=1800):
    # File-backed stderr avoids retaining a long ffmpeg log in memory.
    with tempfile.TemporaryFile(mode='w+') as errors:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=errors, text=True, timeout=timeout, check=False)
        errors.seek(0)
        diagnostic = errors.read()
    if result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} failed: {diagnostic[-1500:]}')
    return result.stdout, diagnostic


def probe(path):
    output, _ = command(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                         '-show_entries', 'stream=width,height,nb_frames,duration:format=duration',
                         '-of', 'json', str(path)], timeout=30)
    result = json.loads(output)
    stream = result['streams'][0]
    duration = float(stream.get('duration') or result['format']['duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('Invalid video duration')
    return {**stream, 'duration': duration}


def merge(ranges):
    result = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def freeze_ranges(log_text, duration):
    result, start = [], None
    for field, raw in re.findall(r'lavfi\.freezedetect\.(freeze_start|freeze_end):\s*([\d.eE+-]+)', log_text):
        at = float(raw)
        if not math.isfinite(at):
            continue
        if field == 'freeze_start':
            start = max(0, at)
        elif start is not None:
            result.append([start, min(duration, at)])
            start = None
    if start is not None:
        result.append([start, duration])
    return result


def retained_ranges(freezes, duration, protected=()):
    """Remove only long still middles, retaining context and action receipts."""
    cuts = []
    for start, end in freezes:
        if end - start < IDLE_SECONDS:
            continue
        left = math.ceil((start + CONTEXT_SECONDS) * FPS) / FPS
        right = math.floor((end - CONTEXT_SECONDS) * FPS) / FPS
        if right > left:
            cuts.append([left, right])
    keep, cursor = [], 0
    for start, end in merge(cuts):
        keep.append([cursor, min(duration, start)])
        cursor = min(duration, end)
    keep.append([cursor, duration])
    for at in protected:
        keep.append([max(0, math.floor((at - CONTEXT_SECONDS) * FPS) / FPS),
                     min(duration, math.ceil((at + CONTEXT_SECONDS) * FPS) / FPS)])
    return merge(keep)


def timeline(spans, keep):
    result, output = [], 0
    for left, right in keep:
        for span in spans:
            start, end = max(left, span['input_start']), min(right, span['input_end'])
            if end <= start:
                continue
            duration = end - start
            result.append({'source_start': span['source_start'] + start - span['input_start'],
                           'source_end': span['source_start'] + end - span['input_start'],
                           'replay_start': output, 'replay_end': output + duration})
            output += duration
    return result


def source_signature(manifest):
    return hashlib.sha256(json.dumps(manifest.get('segments', []), sort_keys=True).encode()).hexdigest()


def build(target, manifest, work):
    """Two low-thread-count ffmpeg passes; source files are never changed."""
    segments = manifest.get('segments', [])
    if not segments or manifest.get('recording') or manifest.get('expired_at'):
        raise ValueError('Replay requires finalized footage')
    spans, entries, cursor = [], [], 0
    width = height = None
    for segment in segments:
        name = segment['file']
        if not storage.SEGMENT.fullmatch(name):
            raise ValueError('Invalid source segment')
        path = target / 'video' / name
        if path.is_symlink() or path.stat().st_size != segment['bytes']:
            raise ValueError('Source video length mismatch')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != segment['sha256']:
                raise ValueError('Source video checksum mismatch')
        info = probe(path)
        width, height = width or int(info['width']), height or int(info['height'])
        # Recorder footage is 2 FPS. Snap the concat boundaries to that grid.
        duration = round(info['duration'] * FPS) / FPS
        if duration <= 0:
            raise ValueError('Empty source video')
        spans.append({'input_start': cursor, 'input_end': cursor + duration,
                      'source_start': float(segment['start'])})
        entries += [f"file '../{name}'", f'duration {duration:.6f}']
        cursor += duration
    concat = work / 'source.ffconcat'
    concat.write_text('ffconcat version 1.0\n' + '\n'.join(entries) + '\n')
    normalized = f'fps={FPS},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1'
    inputs = ['ffmpeg', '-hide_banner', '-nostdin', '-nostats', '-threads', '1', '-filter_threads', '1',
              '-f', 'concat', '-safe', '0', '-i', str(concat), '-an']
    _, detected = command(inputs + ['-vf', normalized + f',freezedetect=n=0.0001:d={IDLE_SECONDS}', '-f', 'null', '-'])
    protected = []
    events = target / 'events.jsonl'
    if events.exists():
        with events.open() as stream:
            for line in stream:
                event = json.loads(line)
                if event.get('type') not in {'execution.started', 'execution.finished', 'human.input', 'control.changed'}:
                    continue
                source_at = float(event['at']) - float(manifest['started_at'])
                for span in spans:
                    if span['source_start'] <= source_at < span['source_start'] + span['input_end'] - span['input_start']:
                        protected.append(span['input_start'] + source_at - span['source_start'])
                        break
    keep = retained_ranges(freeze_ranges(detected, cursor), cursor, protected)
    time_map = timeline(spans, keep)
    filters = [normalized]
    # Stamp SOURCE elapsed time before selecting frames and resetting their PTS.
    for span in spans:
        offset = span['source_start'] - span['input_start']
        filters.append("drawtext=font=monospace:"
                       f"text='T+%{{pts\\:hms\\:{offset:.6f}}}':fontsize=14:fontcolor=white@0.85:"
                       "box=1:boxcolor=black@0.4:boxborderw=4:x=8:y=h-th-8:"
                       f"enable='gte(t,{span['input_start']:.6f})*lt(t,{span['input_end']:.6f})'")
    select = '+'.join(f'gte(t,{start:.6f})*lt(t,{end:.6f})' for start, end in keep)
    filters += [f"select='{select}'", f'setpts=N/({FPS}*TB)']
    script = work / 'filters.txt'
    script.write_text(','.join(filters))
    output = work / 'replay.mp4'
    version, _ = command(['ffmpeg', '-version'], timeout=10)
    major = re.search(r'ffmpeg version n?(\d+)', version)
    filter_option = '-/filter:v' if major and int(major[1]) >= 7 else '-filter_script:v'
    command(inputs + ['-loglevel', 'error', filter_option, str(script), '-c:v', 'libx264',
                      '-preset', 'veryfast', '-crf', '30', '-threads', '1', '-pix_fmt', 'yuv420p',
                      '-r', str(FPS), '-fps_mode', 'cfr',
                      '-movflags', '+faststart', '-y', str(output)])
    info = probe(output)
    expected = time_map[-1]['replay_end']
    if abs(info['duration'] - expected) > .01:
        raise ValueError('Replay duration does not match its receipt time map')
    # Decode before publishing. A failed build always leaves the originals usable.
    command(['ffmpeg', '-v', 'error', '-xerror', '-threads', '1', '-i', str(output), '-f', 'null', '-'])
    with output.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'status': 'ready', 'version': VERSION, 'file': 'replay.mp4',
            'bytes': output.stat().st_size, 'sha256': digest, 'duration': info['duration'],
            'source_duration': cursor, 'session_duration': max(s['source_start'] + s['input_end'] - s['input_start'] for s in spans),
            'skipped_seconds': round(cursor - expected, 3), 'timeline': time_map,
            'source_signature': source_signature(manifest), 'timestamp': 'original_session_elapsed',
            'idle_threshold_seconds': IDLE_SECONDS, 'context_seconds': CONTEXT_SECONDS,
            'created_at': datetime.now(timezone.utc).isoformat()}


def process(conn_factory, config, item, requester):
    target = item['path']
    manifest_path, replay_path = target / 'manifest.json', target / 'replay.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('expired_at') or manifest.get('recording') or not manifest.get('segments'):
        return
    prior = json.loads(replay_path.read_text()) if replay_path.exists() else {}
    if prior.get('status') == 'ready' and prior.get('version') == VERSION and prior.get('source_signature') == source_signature(manifest) and (target / 'video/replay.mp4').is_file():
        return
    if prior.get('retry_after', 0) > time.time():
        return
    write_json(replay_path, {'status': 'processing', 'version': VERSION})
    try:
        with tempfile.TemporaryDirectory(prefix='.replay-', dir=target / 'video') as temporary:
            work = Path(temporary)
            metadata = build(target, manifest, work)
            with conn_factory() as conn, conn.transaction():
                # Retention may run during encoding; never resurrect expired footage.
                storage.lock(conn)
                current = json.loads(manifest_path.read_text())
                if current.get('expired_at') or source_signature(current) != source_signature(manifest):
                    replay_path.unlink(missing_ok=True)
                    return
                (work / 'replay.mp4').replace(target / 'video/replay.mp4')
                with (target / 'video/replay.mp4').open('rb') as stream:
                    os.fsync(stream.fileno())
                directory_fd = os.open(target / 'video', os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                write_json(replay_path, metadata)
                access.append_event(conn_factory, item['id'], requester, 'recording.replay_ready',
                                    {k: metadata[k] for k in ('bytes', 'duration', 'source_duration', 'skipped_seconds')},
                                    f'replay:{VERSION}:{metadata["source_signature"]}')
    except Exception as exc:
        write_json(replay_path, {'status': 'deferred', 'version': VERSION, 'retry_after': time.time() + 300})
        log.warning('Computer replay deferred for %s: %s', item['id'], type(exc).__name__)


def maintain(conn_factory, config):
    # Independent from the capture/archive lock; one encoder per appliance.
    with conn_factory() as lease, lease.transaction():
        if not lease.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('calliope-computer-replay',0)) AS acquired").fetchone()['acquired']:
            return
        for item in storage.inventory(config):
            if item['manifest'].get('expired_at') or item['manifest'].get('recording'):
                continue
            with conn_factory() as conn:
                row = conn.execute("SELECT r.requester FROM rvbbit.calliope_desktop_runs r "
                    "WHERE r.id=%s::uuid AND r.status IN ('finished','interrupted','failed') AND EXISTS "
                    "(SELECT 1 FROM rvbbit.calliope_workbench_events e WHERE e.desktop_run_id=r.id "
                    "AND e.idempotency_key='evidence.archived')", (item['id'],)).fetchone()
            if row:
                process(conn_factory, config, item, row['requester'])


def work(conn_factory, config):
    while True:
        try:
            maintain(conn_factory, config)
        except Exception as exc:
            log.warning('Computer replay worker unavailable: %s', type(exc).__name__)
        threading.Event().wait(30)
