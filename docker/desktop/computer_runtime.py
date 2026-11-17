"""Persistent desktop runs, cancellable code jobs and independent video capture.

This is a guest service, not a task planner. Calliope supplies ordinary code and
intent; this module owns execution, single-writer state and durable evidence.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import logging
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Callable


class ComputerError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.status = code, status


def identifier(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        raise ComputerError("INVALID_ID", "Expected a run or job UUID.", 400) from None


def atomic_json(path: Path, value: Any) -> None:
    temp = path.with_suffix(".tmp")
    with temp.open("w") as f:
        json.dump(value, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


class Recorder:
    """Finalize each segment independently; never depend on the viewing client."""

    def __init__(self, directory: Path, display: str, resolution: str):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        prior_path = directory / "manifest.json"
        prior = self.recover(directory) if prior_path.exists() else {}
        self.started_at = prior.get("started_at", time.time())
        self.previous_segments = prior.get("segments", [])
        self.capture_started_at = time.time()
        self.offset = self.capture_started_at - self.started_at
        # A restarted recorder must never truncate an earlier segment or list.
        numbers = [int(p.stem) for p in directory.glob("??????.mp4") if p.stem.isdigit()]
        next_number = max(numbers, default=-1) + 1
        self.index = directory / f"segments-{next_number:06d}.csv"
        # The CSV is finalized by ffmpeg independently of our process. Keep
        # its time origin durable so restart can recover a just-closed segment
        # even when it was not included in the last published manifest.
        atomic_json(directory / f"capture-{next_number:06d}.json", {
            "index": self.index.name, "offset": self.offset, "started_at": self.started_at,
            "resolution": resolution, "fps": 2})
        self.masking_receipt = directory / f"masking-{next_number:06d}.json"
        self.log = (directory / "capture.log").open("ab")
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("computer_capture.py")),
             "--display", display, "--resolution", resolution,
             "--record", str(directory), "--start-number", str(next_number)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=self.log,
            start_new_session=True,
        )
        self.metadata = {"fps": 2, "resolution": resolution,
                         "started_at": self.started_at, "segments": [],
                         "unfinalized_files": prior.get("unfinalized_files", [])}
        # A live process may still be importing GTK or starting its encoder.
        # Do not admit a job until capture has actually delivered its first frame.
        deadline = time.monotonic() + 10
        while self.process.poll() is None and not self.masking_receipt.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        if not self.masking_receipt.exists() or not self.alive():
            self.stop()
            raise ComputerError("RECORDING_FAILED", "Desktop capture did not deliver its first frame.", 503)
        self.manifest()

    @staticmethod
    def masking(directory: Path) -> list[dict]:
        return [{"first_segment": int(path.stem.split("-")[1]), **json.loads(path.read_text())}
                for path in sorted(directory.glob("masking-??????.json"))
                if path.stem.split("-")[1].isdigit()]

    @staticmethod
    def recover(directory: Path) -> dict:
        path = directory / "manifest.json"
        value = json.loads(path.read_text()) if path.exists() else {}
        segments = {s["file"]: s for s in value.get("segments", [])}
        captures = sorted(directory.glob("capture-??????.json"))
        for metadata in captures:
            capture = json.loads(metadata.read_text())
            index = directory / Path(capture["index"]).name
            if not index.is_file():
                continue
            for row in csv.reader(index.read_text().splitlines()):
                if len(row) != 3:
                    continue
                name, start, end = row
                name = Path(name).name
                if len(name) != 10 or not name[:6].isdigit() or not name.endswith('.mp4'):
                    continue
                try:
                    start, end = float(start), float(end)
                except ValueError:
                    continue
                video = directory / name
                if not video.is_file() or not all(map(math.isfinite, (start, end))) or end <= start:
                    continue
                with video.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                segments[name] = {"file": name, "start": capture["offset"] + start,
                                  "end": capture["offset"] + end, "bytes": video.stat().st_size, "sha256": digest}
            for key in ('started_at', 'resolution', 'fps'):
                value.setdefault(key, capture[key])
        value.update(segments=sorted(segments.values(), key=lambda segment: segment['file']), recording=False,
                     unfinalized_files=[p.name for p in sorted(directory.glob('??????.mp4')) if p.name not in segments])
        value["masking_captures"] = Recorder.masking(directory)
        if value.get('unfinalized_files'):
            value['recovery_note'] = 'Unfinalized tail files are retained but are not advertised as playable footage.'
        atomic_json(path, value)
        return value

    def alive(self) -> bool:
        heartbeat = self.masking_receipt.stat().st_mtime if self.masking_receipt.exists() else self.capture_started_at
        return self.process.poll() is None and time.time() - heartbeat < 5

    def manifest(self) -> dict:
        segments = [dict(item) for item in self.previous_segments]
        index = self.index
        if index.exists():
            for row in csv.reader(index.read_text().splitlines()):
                if len(row) != 3:
                    continue
                name, start, end = row
                path = self.directory / Path(name).name
                if path.is_file():
                    segments.append({"file": path.name, "start": self.offset + float(start),
                                     "end": self.offset + float(end), "bytes": path.stat().st_size})
        value = {**self.metadata, "segments": segments, "recording": self.alive(),
                 "masking_captures": self.masking(self.directory)}
        atomic_json(self.directory / "manifest.json", value)
        return value

    def stop(self) -> dict:
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                # The capture worker owns its encoder in this private process group.
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # The worker may finish between the timeout and kill.
                self.process.wait(timeout=2)
        self.log.close()
        value = self.manifest()
        for item in value["segments"]:
            with (self.directory / item["file"]).open("rb") as f:
                item["sha256"] = hashlib.file_digest(f, "sha256").hexdigest()
        value["ended_at"] = time.time()
        atomic_json(self.directory / "manifest.json", value)
        return value


class ComputerRuntime:
    def __init__(self, root: Path, *, display: str = ":1",
                 resolution: Callable[[], str] = lambda: "1280x720",
                 recorder_factory: Callable = Recorder,
                 revoke_input: Callable[[], None] = lambda: None,
                 prepare_input: Callable[[str], None] = lambda session: None):
        self.root = root / "computer"
        self.root.mkdir(parents=True, exist_ok=True)
        self.display, self.resolution = display, resolution
        self.recorder_factory = recorder_factory
        self.revoke_input = revoke_input
        self.prepare_input = prepare_input
        self.lock = threading.RLock()
        self.recorder = None
        self.process = None
        self.job_id = None
        self.closed = threading.Event()
        self.state_path = self.root / "state.json"
        self.state = {"active_run": None, "mode": "idle", "generation": 0,
                      "queue": [], "runs": {}, "jobs": {}}
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
            # An unobserved external submission may already have succeeded.
            # Never replay code automatically after the supervisor restarts.
            for item in self.state["jobs"].values():
                if item["status"] in {"running", "queued"}:
                    item.update(status="interrupted", ended_at=time.time())
            for item in self.state["runs"].values():
                if item["status"] in {"active", "queued"}:
                    item["status"] = "interrupted"
                    self._event(item["id"], "runtime.interrupted", {})
                    video = self._directory(item["id"]) / 'video'
                    if video.is_dir():
                        recovered = Recorder.recover(video)
                        self._event(item["id"], 'recording.recovered', {
                            'segments': len(recovered['segments']), 'unfinalized_files': recovered['unfinalized_files']})
            self.state.update(active_run=None, mode="idle", queue=[], human_actor=None)
            self.state.pop("pause_reason", None)
            self.state["generation"] += 1
        self._save()
        self.monitor = threading.Thread(target=self._watch, daemon=True)
        self.monitor.start()

    def _save(self):
        atomic_json(self.state_path, self.state)

    def _directory(self, run_id: str) -> Path:
        path = self.root / "runs" / identifier(run_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _event(self, run_id: str, kind: str, payload: dict):
        with (self._directory(run_id) / "events.jsonl").open("a") as f:
            json.dump({"id": str(uuid.uuid4()), "at": time.time(), "type": kind,
                       "run_id": run_id, "payload": payload}, f, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

    def status(self) -> dict:
        with self.lock:
            result = json.loads(json.dumps(self.state))
            result["recording"] = self.recorder.manifest() if self.recorder else None
            return result

    def prune_recordings(self, run_id, segments):
        """Acknowledge archived segments without touching jobs or receipts."""
        rid = identifier(run_id)
        if not isinstance(segments, list) or not 1 <= len(segments) <= 100:
            raise ComputerError('INVALID_SEGMENTS', 'Supply 1 to 100 archived segments.', 400)
        with self.lock:
            run = self.state['runs'].get(rid)
            if not run or run['status'] not in {'finished', 'failed', 'interrupted'}:
                raise ComputerError('RUN_NOT_FINISHED', 'Only completed recordings can be released.')
            directory = self._directory(rid) / 'video'
            if directory.is_symlink() or directory.parent.is_symlink():
                raise ComputerError('INVALID_RECORDING', 'Recording directory changed.')
            manifest = json.loads((directory / 'manifest.json').read_text())
            known = {item['file']: item for item in manifest.get('segments', [])}
            paths = []
            for item in segments:
                if (not isinstance(item, dict) or not isinstance(item.get('file'), str)
                        or not re.fullmatch(r'\d{6}\.mp4', item['file'])
                        or not re.fullmatch(r'[0-9a-f]{64}', str(item.get('sha256', '')))):
                    raise ComputerError('INVALID_SEGMENT', 'Invalid archived segment.', 400)
                metadata = known.get(item['file'])
                if not metadata or any(metadata.get(key) != item.get(key) for key in ('bytes', 'sha256')):
                    raise ComputerError('ARCHIVE_MISMATCH', 'Archive does not match the finalized recording.')
                path = directory / item['file']
                if path.is_symlink():
                    raise ComputerError('ARCHIVE_MISMATCH', 'Recording file changed.')
                if path.exists():
                    with path.open('rb') as stream:
                        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                    if digest != item['sha256'] or path.stat().st_size != item['bytes']:
                        raise ComputerError('ARCHIVE_MISMATCH', 'Recording bytes changed.')
                paths.append(path)
            # Validate the entire batch before removing any file. Missing files
            # are already acknowledged, so a lost response is safe to retry.
            for path in paths:
                path.unlink(missing_ok=True)
            return {'run_id': rid, 'acknowledged': len(paths)}

    def open_run(self, run_id: str, context: dict) -> dict:
        rid = identifier(run_id)
        with self.lock:
            if rid in self.state["runs"]:
                return dict(self.state["runs"][rid])
            item = {"id": rid, "context": context, "status": "queued", "created_at": time.time(),
                    "queued_behind": self.state["active_run"]}
            self.state["runs"][rid] = item
            self.state["queue"].append(rid)
            self._event(rid, "run.opened", context)
            self._activate_next()
            self._save()
            return dict(item)

    def _activate_next(self):
        if self.state["active_run"] or not self.state["queue"]:
            return
        rid = self.state["queue"][0]
        try:
            self.recorder = self.recorder_factory(
                self._directory(rid) / "video", self.display, self.resolution())
        except Exception as exc:
            self.state["runs"][rid]["error"] = str(exc)
            self._event(rid, "recording.failed", {"error": str(exc)})
            return
        self.state["queue"].pop(0)
        self.state.update(active_run=rid, mode="agent", human_actor=None)
        self.state.pop("pause_reason", None)
        self.state["generation"] += 1
        self.state["runs"][rid].update(status="active", started_at=time.time())
        self._event(rid, "run.started", {"generation": self.state["generation"],
                                         "queued_behind": self.state["runs"][rid].get("queued_behind")})

    def browser_auth(self, run_id, origin, cookie, expires_at):
        from computer_browser_auth import install
        with self.lock:
            rid = identifier(run_id)
            if self.state['active_run'] != rid:
                raise ComputerError('RUN_NOT_ACTIVE', 'Browser identity belongs to another run.')
            try:
                result = install(origin, cookie, expires_at)
            except Exception:
                # Browser may be starting/restarting; the appliance retries.
                # Do not serialize Playwright errors, which can contain cookies.
                return {'status': 'pending', 'run_id': rid}
            return {**result, 'run_id': rid}

    def execute(self, run_id: str, job_id: str, *, code: str, intent: str,
                language: str = "python", timeout: int = 600) -> dict:
        rid, jid = identifier(run_id), identifier(job_id)
        if language not in {"python", "shell"} or not code.strip() or not intent.strip():
            raise ComputerError("INVALID_CODE", "Supply Python or shell code and its intent.", 400)
        if not 1 <= timeout <= 86400:
            raise ComputerError("INVALID_TIMEOUT", "Timeout must be 1–86400 seconds.", 400)
        with self.lock:
            old = self.state["jobs"].get(jid)
            if old:
                if (old["run_id"], old["code"], old["language"]) != (rid, code, language):
                    raise ComputerError("IDEMPOTENCY_CONFLICT", "Job ID was used for different code.")
                return self.job(jid)
            if self.state["active_run"] != rid:
                raise ComputerError("DESKTOP_BUSY", "This run is queued or no longer active.")
            if self.state["mode"] == "human":
                raise ComputerError("HUMAN_CONTROL", "Agent execution is paused while a person is driving.")
            if self.state["mode"] != "agent":
                reason = self.state.get("pause_reason") or {}
                raise ComputerError(reason.get("code", "DESKTOP_PAUSED"), reason.get("message",
                    "The desktop is paused. Inspect its state; do not assume permission is needed."))
            if self.process:
                raise ComputerError("JOB_RUNNING", "Wait for the active job or stop it first.")
            if not self.recorder or not self.recorder.alive():
                raise ComputerError("RECORDING_FAILED", "Recording must be restored before executing.", 503)
            directory = self._directory(rid) / "jobs" / jid
            directory.mkdir(parents=True, exist_ok=True)
            script = directory / ("code.py" if language == "python" else "code.sh")
            script.write_text(code)
            item = {"id": jid, "run_id": rid, "code": code, "intent": intent,
                    "language": language, "status": "running", "started_at": time.time(),
                    "timeout": timeout, "generation": self.state["generation"]}
            if language == 'shell':
                item['terminal'] = {'format': 'asciicast-v2', 'columns': 100, 'rows': 24}
                (directory / 'terminal.cast').write_text(json.dumps({
                    'version': 2, 'width': 100, 'height': 24,
                    'timestamp': item['started_at'], 'title': 'Callie’s terminal'}) + '\n')
            self.state["jobs"][jid] = item
            self._event(rid, "execution.started", item)
            self._save()
            env = {k: v for k, v in os.environ.items()
                   if k not in {"CALLIOPE_DESKTOP_CONTROL_KEY", "VNC_PW", "VNC_VIEW_ONLY_PW"}}
            env.update(DISPLAY=self.display, CALLIOPE_COMPUTER_RUN=rid,
                       CALLIOPE_COMPUTER_JOB=jid,
                       CALLIOPE_CUA_SESSION=f"Callie-g{self.state['generation']}",
                       PYTHONPATH=str(Path(__file__).parent), PYTHONUNBUFFERED="1")
            from desktop_session import session_environment
            env.update(session_environment())
            try:
                self.prepare_input(env['CALLIOPE_CUA_SESSION'])
            except Exception as exc:
                # Cursor decoration must never prevent useful computer work.
                logging.warning('Computer cursor setup skipped: %s', type(exc).__name__)
            if os.environ.get("CALLIOPE_DESKTOP_CONTROL_KEY"):
                from desktop_decision_token import issue
                env["CALLIOPE_COMPUTER_DECISION_TOKEN"] = issue(
                    os.environ["CALLIOPE_DESKTOP_CONTROL_KEY"], rid, jid, self.state["generation"], timeout)
            if language == 'shell':
                argv = [sys.executable, str(Path(__file__).with_name('computer_terminal.py')), str(script)]
            else:
                argv = [sys.executable, str(script)]
            argv = [sys.executable, str(Path(__file__).with_name("computer_job.py")),
                    str(os.getpid()), *argv]
            if os.environ.get('CALLIOPE_COMPUTER_ALLOW_SUDO', '').lower() == 'true':
                item['root_supervisor'] = True
                env['CALLIOPE_COMPUTER_SUPERVISOR_PID_FILE'] = str(directory / 'supervisor.pid')
                env['CALLIOPE_COMPUTER_JOB_UID'] = '1000'
                argv = ['sudo', '-n', '-E', *argv]
            self._save()
            try:
                with (directory / "stdout.txt").open("wb") as out, (directory / "stderr.txt").open("wb") as err:
                    self.process = subprocess.Popen(argv, env=env, cwd=Path.home(),
                                                    stdin=subprocess.DEVNULL, stdout=out,
                                                    stderr=err, start_new_session=True)
            except Exception as exc:
                item.update(status="failed", error=str(exc), ended_at=time.time())
                self._event(rid, "execution.finished", item)
                self._save()
                raise ComputerError("EXECUTION_FAILED", str(exc), 500) from exc
            self.job_id = jid
            return dict(item)

    def job(self, job_id: str, *, offset: int = 0, stdout_offset: int | None = None,
            stderr_offset: int = 0, limit: int = 32000) -> dict:
        with self.lock:
            item = dict(self.state["jobs"].get(identifier(job_id)) or {})
            if not item:
                raise ComputerError("JOB_NOT_FOUND", "Unknown desktop job.", 404)
            path = self._directory(item["run_id"]) / "jobs" / item["id"]
            for stream in ("stdout", "stderr"):
                p = path / f"{stream}.txt"
                if p.exists():
                    with p.open("rb") as f:
                        stream_offset = (offset if stdout_offset is None else stdout_offset) if stream == "stdout" else stderr_offset
                        f.seek(min(p.stat().st_size, max(0, stream_offset)))
                        data = f.read(max(1, min(limit, 256000)))
                        item[stream] = data.decode("utf-8", "replace")
                        item[f"{stream}_next_offset"] = f.tell()
                    item[f"{stream}_bytes"] = p.stat().st_size
            return item

    def terminal(self, job_id: str, offset=0):
        from desktop_terminal import read_page
        with self.lock:
            item = self.state['jobs'].get(identifier(job_id))
            if not item or not item.get('terminal'):
                raise ComputerError('TERMINAL_NOT_FOUND', 'This job has no terminal recording.', 404)
            path = self._directory(item['run_id']) / 'jobs' / item['id'] / 'terminal.cast'
            return {**read_page(path, offset), 'status': item['status'],
                    'complete': item['status'] != 'running', 'intent': item['intent'], 'code': item['code']}

    def _cancel(self, reason: str):
        if not self.process:
            return
        process = self.process
        item = self.state['jobs'][self.job_id]
        if item.get('root_supervisor'):
            if process.poll() is not None:
                self._settle_job('completed' if process.returncode == 0 else 'failed', process.returncode)
                return
            pid_path = self._directory(item['run_id']) / 'jobs' / self.job_id / 'supervisor.pid'
            if pid_path.exists():
                # The privileged subreaper can stop sudo children as well as
                # ordinary descendants. Never grant host privileges/mounts.
                pid = int(pid_path.read_text())
                subprocess.run(['sudo', '-n', 'kill', '-TERM', str(pid)], timeout=3, check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    raise ComputerError('STOP_FAILED', 'The privileged job has not stopped; keep the desktop paused.', 503)
                self._settle_job(reason, process.returncode)
                return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
            # Terminate inherited descendants even if their parent exited.
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)
        self._settle_job(reason, process.returncode)

    def _settle_job(self, status: str, exit_code: int):
        item = self.state["jobs"][self.job_id]
        item.update(status=status, exit_code=exit_code, ended_at=time.time())
        if status == 'timed_out':
            item['error'] = ("Job timed out and its process tree was stopped. For persistent GUI apps, "
                             "use cua.call('launch_app', {'name': ..., 'additional_arguments': [...]}); "
                             "nohup/disown/background shell children remain part of this job.")
        # Forget the finished process even if the disk cannot accept its
        # receipt. Otherwise recovery could mistake it for a live writer.
        self.process, self.job_id = None, None
        self._event(item["run_id"], "execution.finished",
                    {k: v for k, v in item.items() if k != "code"})
        self._save()

    def control(self, run_id: str, mode: str, actor: str | None = None,
                expected_generation: int | None = None) -> dict:
        if mode not in {"human", "agent", "paused"}:
            raise ComputerError("INVALID_MODE", "Choose human, agent or paused.", 400)
        with self.lock:
            if expected_generation is not None and expected_generation != self.state["generation"]:
                raise ComputerError("CONTROL_CHANGED", "The computer has a different driver.")
            rid = identifier(run_id)
            if self.state["active_run"] != rid:
                raise ComputerError("RUN_NOT_ACTIVE", "This run does not own the computer.")
            # Deny new execution before revoking the old driver's input.
            self.state["mode"] = "paused"
            self.state["human_actor"] = None
            self.state["generation"] += 1
            try:
                self._cancel("cancelled")
            finally:
                # Stop input even when writing cancellation evidence fails.
                try:
                    self.revoke_input()
                finally:
                    self._save()
            if mode != "paused" and (not self.recorder or not self.recorder.alive()):
                if self.recorder:
                    self.recorder.stop()
                self.recorder = None
                try:
                    self.recorder = self.recorder_factory(
                        self._directory(rid) / "video", self.display, self.resolution())
                except Exception as exc:
                    self._event(rid, "recording.failed", {"error": str(exc)})
                    raise ComputerError("RECORDING_FAILED", "Recording could not restart; the computer remains paused.", 503) from exc
                self._event(rid, "recording.restarted", {"generation": self.state["generation"]})
            self.state["mode"] = mode
            self.state.pop("pause_reason", None)
            self.state.pop("storage_error", None)
            self.state["human_actor"] = actor if mode == "human" else None
            self._event(rid, "control.changed", {"mode": mode, "generation": self.state["generation"]})
            self._save()
            return self.status()

    def human_input(self, run_id: str, generation: int, actor: str, event: dict):
        with self.lock:
            rid = identifier(run_id)
            if (self.state["active_run"] != rid or self.state["mode"] != "human"
                    or self.state["generation"] != generation
                    or self.state.get("human_actor") != actor):
                raise ComputerError("CONTROL_CHANGED", "The computer has a different driver.")
            self._event(rid, "human.input", {"actor": actor, **event})
            return {"recorded": True}

    def finish(self, run_id: str, outcome: str) -> dict:
        with self.lock:
            rid = identifier(run_id)
            item = self.state["runs"].get(rid)
            if not item:
                raise ComputerError("RUN_NOT_FOUND", "Unknown desktop run.", 404)
            if item["status"] not in {"active", "queued"}:
                return dict(item)
            if self.state["active_run"] == rid:
                if self.state["mode"] == "human":
                    raise ComputerError("HUMAN_CONTROL", "Hand back control before finishing this run.")
                self.state["mode"] = "paused"
                self._cancel("cancelled")
                self.revoke_input()
                manifest = self.recorder.stop() if self.recorder else None
                self._event(rid, "recording.finished", {"manifest": manifest})
                self.recorder = None
                self.state.update(active_run=None, mode="idle", human_actor=None)
            if rid in self.state["queue"]:
                self.state["queue"].remove(rid)
            item.update(status="finished", outcome=outcome, ended_at=time.time())
            self._event(rid, "run.finished", {"outcome": outcome})
            self._activate_next()
            self._save()
            return dict(item)

    def _watch(self):
        while not self.closed.wait(0.1):
            with self.lock:
                try:
                    self._tick()
                except OSError as exc:
                    self.state.update(mode="paused", human_actor=None, storage_error=type(exc).__name__,
                        pause_reason={"code": "RECORDING_STORAGE_FAILED", "message":
                            "Recording storage failed. This is a system fault, not a human takeover or permission request."})
                    self.state["generation"] += 1
                    try:
                        self._cancel("recording_failed")
                    except OSError:
                        pass
                    try:
                        self.revoke_input()
                    except Exception:
                        pass
                    logging.error("Computer paused after evidence storage failure: %s", type(exc).__name__)

    def _tick(self):
        if self.recorder and not self.recorder.alive() and self.state["mode"] != "paused":
            self.state["pause_reason"] = {"code": "RECORDING_FAILED", "message":
                "Desktop recording stalled or stopped. This is a system fault, not a human takeover or permission request."}
            self.state["mode"] = "paused"
            self.state["human_actor"] = None
            self.state["generation"] += 1
            self._save()
            self._cancel("recording_failed")
            try:
                self.revoke_input()
            except Exception as exc:
                self._event(self.state["active_run"], "input.revocation_failed", {"error": str(exc)})
            self._event(self.state["active_run"], "recording.failed", self.state["pause_reason"])
            self._save()
        if self.process:
            item = self.state["jobs"][self.job_id]
            code = self.process.poll()
            if code is not None:
                self._settle_job("completed" if code == 0 else "failed", code)
            elif time.time() - item["started_at"] > item["timeout"]:
                self._cancel("timed_out")

    def close(self):
        self.closed.set()
        self.monitor.join(timeout=3)
        with self.lock:
            self._cancel("interrupted")
            if self.recorder:
                self.recorder.stop()
                self.recorder = None
            self._save()
