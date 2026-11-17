"""Copy finalized computer evidence into the appliance's persistent file store.

The guest remains the live source; the archive keeps completed footage, code,
output and receipts readable while that guest is unavailable or replaced.
"""
from __future__ import annotations

import hashlib
import asyncio
import json
import logging
import os
from pathlib import Path
import re
import threading
import uuid

import desktop_access as access
import desktop_agents
from desktop_runtime import DesktopRuntime

_thread = None
_lock = threading.Lock()
log = logging.getLogger(__name__)


def directory(config, run_id):
    return Path(config.file_root) / "computer-evidence" / access.uid(run_id)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
    try:
        with temp.open('w') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temp.unlink(missing_ok=True)


def archive_run(conn_factory, config, runtime, run, state):
    rid = access.uid(run["id"])
    target = directory(config, rid)
    target.mkdir(parents=True, exist_ok=True)
    prefix = f"computer/runs/{rid}/"
    runtime.download(prefix + "events.jsonl", target / "guest-events.jsonl")
    # A live journal can be copied between two writes. Only ingest complete
    # newline-terminated records; the next pass will obtain the trailing one.
    events = []
    for line in (target / "guest-events.jsonl").read_bytes().splitlines(keepends=True):
        if line.endswith(b"\n"):
            events.append(json.loads(line))
    journal_temp = target / "events.jsonl.part"
    journal_temp.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events))
    journal_temp.replace(target / "events.jsonl")
    for event in events:
        if event.get("run_id") != rid:
            raise ValueError("Computer journal belongs to a different run")
        if event["type"] != "human.input":
            access.append_event(conn_factory, rid, run["requester"], "guest." + event["type"],
                                {"at": event["at"], **event["payload"]}, "guest:" + access.uid(event["id"]))
        data = event["payload"]
        ready = (event["type"] == "control.changed" and data.get("mode") == "agent") or (
            event["type"] == "run.started" and data.get("queued_behind"))
        if (ready and state.get("active_run") == rid and state.get("mode") == "agent"
                and data.get("generation") == state.get("generation")):
            access.ready_message(conn_factory, rid, data["generation"])

    # A queued run has no recorder yet. Its journal is still worth retaining.
    if run["status"] == "queued":
        return
    manifest_path = target / "guest-manifest.json"
    runtime.download(prefix + "video/manifest.json", manifest_path)
    manifest = json.loads(manifest_path.read_text())
    archived = []
    for segment in manifest.get("segments", []):
        name = segment["file"]
        if not re.fullmatch(r"\d{6}\.mp4", name):
            raise ValueError("Invalid recording segment name")
        local = target / "video" / name
        prior = local.with_suffix(".json")
        metadata = json.loads(prior.read_text()) if prior.exists() else {}
        if (not local.exists() or local.stat().st_size != segment["bytes"]
                or metadata.get("bytes") != segment["bytes"]
                or (segment.get("sha256") and metadata.get("sha256") != segment["sha256"])):
            runtime.download(prefix + "video/" + name, local, segment.get("sha256"))
            if local.stat().st_size != segment["bytes"]:
                local.unlink()
                raise ValueError("Recording segment length did not match manifest")
            with local.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            metadata = {**segment, "sha256": digest}
            write_json(prior, metadata)
        archived.append(metadata)
        with conn_factory() as conn:
            recording_id = str(uuid.uuid5(uuid.UUID(rid), name))
            conn.execute(
                "INSERT INTO rvbbit.calliope_workbench_recordings "
                "(id,desktop_run_id,state,object_key,bytes,sha256,duration_ms,completed_at) "
                "VALUES(%s::uuid,%s::uuid,'ready',%s,%s,%s,%s,now()) ON CONFLICT(id) DO NOTHING",
                (recording_id, rid, str(local.relative_to(config.file_root)), metadata["bytes"],
                 metadata["sha256"], round((segment["end"] - segment["start"]) * 1000)),
            )
    write_json(target / "manifest.json", {**manifest, "segments": archived, "storage": "appliance_archive"})

    all_jobs = [job for job in state.get('jobs', {}).values() if job['run_id'] == rid]
    jobs = [job for job in all_jobs if job['status'] != 'running']
    for job in all_jobs:
        jid = access.uid(job["id"])
        done = target / "jobs" / jid / "archived.json"
        if done.exists():
            continue
        terminal = None
        if job.get('terminal'):
            path = done.parent / 'terminal.cast'
            candidate = done.parent / 'terminal.incoming'
            runtime.download(prefix + 'jobs/' + jid + '/terminal.cast', candidate)
            # Keep the copy outside the guest append-only. A changed/truncated
            # guest recording must never silently replace already archived data.
            if path.exists():
                with path.open('rb') as old, candidate.open('rb') as new:
                    while block := old.read(256 * 1024):
                        if block != new.read(len(block)):
                            candidate.unlink()
                            raise ValueError('Guest terminal recording changed previously archived bytes')
            candidate.replace(path)
            with path.open('rb') as stream:
                terminal = {**job['terminal'], 'bytes': path.stat().st_size,
                            'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}
            write_json(done.parent / 'terminal-state.json',
                       {k: job.get(k) for k in ('id', 'run_id', 'status', 'intent', 'code')})
        if job['status'] == 'running':
            continue
        code_name = "code.py" if job["language"] == "python" else "code.sh"
        for name in (code_name, "stdout.txt", "stderr.txt"):
            runtime.download(prefix + "jobs/" + jid + "/" + name, done.parent / name)
        write_json(done, {**{key: value for key, value in job.items() if key != "code"},
                          **({'terminal': terminal} if terminal else {})})
    if (run["status"] in {"finished", "interrupted", "failed"}
            and state.get("runs", {}).get(rid, {}).get("status") == run["status"]):
        with conn_factory() as conn:
            conn.execute("UPDATE rvbbit.calliope_agent_messages SET status='cancelled' "
                         "WHERE from_kind='system' AND from_ref=%s AND status='queued'", ("computer:" + rid,))
        value = {"segments": len(archived), "jobs": len(jobs), "events": len(events),
                 "object_key": str(target.relative_to(config.file_root))}
        access.append_event(conn_factory, rid, run["requester"], "evidence.archived", value, "evidence.archived")


def sync(conn_factory, config):
    runtime = DesktopRuntime(config)
    # Cross-process serialization prevents two workers publishing the same
    # file simultaneously. Individual DB receipts also have idempotency keys.
    with conn_factory() as lease, lease.transaction():
        if not lease.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('calliope-computer-evidence',0)) AS acquired").fetchone()["acquired"]:
            return
        state = runtime.request("GET", "status")
        access.sync_runs(conn_factory, state)
        with conn_factory() as conn:
            runs = conn.execute(
                "SELECT r.* FROM rvbbit.calliope_desktop_runs r WHERE NOT EXISTS "
                "(SELECT 1 FROM rvbbit.calliope_workbench_events e WHERE e.desktop_run_id=r.id "
                "AND e.idempotency_key='evidence.archived') "
                "ORDER BY CASE WHEN r.status='active' THEN 0 ELSE 1 END,r.created_at"
            ).fetchall()
        for run in runs:
            try:
                archive_run(conn_factory, config, runtime, run, state)
            except Exception as exc:
                log.warning("Computer evidence sync deferred for %s: %s", run["id"], type(exc).__name__)


def start_worker(conn_factory, config):
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        def work():
            while True:
                try:
                    asyncio.run(desktop_agents.cancel_pending(conn_factory, config))
                    sync(conn_factory, config)
                except Exception as exc:
                    log.warning("Computer evidence archive unavailable: %s", type(exc).__name__)
                threading.Event().wait(5)
        def storage_work():
            import desktop_storage
            while True:
                try:
                    desktop_storage.maintain(conn_factory, config, DesktopRuntime(config))
                except Exception as exc:
                    log.warning("Computer storage cleanup deferred: %s", type(exc).__name__)
                threading.Event().wait(60)
        import desktop_replay
        threading.Thread(target=desktop_replay.work, args=(conn_factory, config), name="computer-replay", daemon=True).start()
        _thread = threading.Thread(target=work, name="computer-evidence", daemon=True)
        _thread.start()
        threading.Thread(target=storage_work, name="computer-storage", daemon=True).start()
