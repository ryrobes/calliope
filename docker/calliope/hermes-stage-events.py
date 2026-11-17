"""Durable, run-scoped Stage receipts, independent of transcript batch ordering.

Only the existing authenticated run endpoints read this store. It is not a
second transcript; it retains bounded outputs that the Stage can project.
"""
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time

from stage_delivery import MEDIA_TOOLS, STAGE_TOOLS, canonical_tool, receipt_fields

LOG = logging.getLogger(__name__)


class StageEvents:
    def __init__(self, run_db_path=None):
        path = Path(run_db_path).with_name("calliope-stage-events.sqlite") if run_db_path else None
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False, timeout=5)
        if path:
            path.chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS receipts(
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
            call_id TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL,
            UNIQUE(run_id, call_id))""")
        self.db.execute("CREATE INDEX IF NOT EXISTS receipts_run ON receipts(run_id, sequence)")
        self.db.execute("CREATE INDEX IF NOT EXISTS receipts_age ON receipts(created_at)")
        self.db.execute('CREATE TABLE IF NOT EXISTS build_work(run_id TEXT,build_id TEXT,payload TEXT,updated_at REAL,PRIMARY KEY(run_id,build_id))')
        self.db.execute('DELETE FROM build_work WHERE updated_at < ?', (time.time() - 86400,))
        # Bounded replay history, like the gateway's retained run records.
        self.db.execute("DELETE FROM receipts WHERE created_at < ?", (time.time() - 30 * 86400,))
        self.db.commit()
        self.prune_after = time.time() + 3600

    def record(self, run_id, event):
        with self.lock, self.db:
            if time.time() >= self.prune_after:
                self.db.execute("DELETE FROM receipts WHERE created_at < ?", (time.time() - 30 * 86400,))
                self.prune_after = time.time() + 3600
            self.db.execute("INSERT OR IGNORE INTO receipts(run_id,call_id,payload,created_at) VALUES(?,?,?,?)",
                            (run_id, event["call_id"], json.dumps(event), time.time()))
            row = self.db.execute("SELECT sequence FROM receipts WHERE run_id=? AND call_id=?",
                                  (run_id, event["call_id"])).fetchone()
        return int(row[0])

    def read(self, run_id, after=0):
        with self.lock:
            rows = self.db.execute(
                "SELECT sequence,payload FROM receipts WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT 17",
                (run_id, max(0, int(after))),
            ).fetchall()
            work = self.db.execute('SELECT payload FROM build_work WHERE run_id=? ORDER BY updated_at DESC LIMIT 8', (run_id,)).fetchall()
        events, size = [], 0
        for sequence, payload in rows[:16]:
            if events and size + len(payload) > 4 * 1024 * 1024:
                break
            events.append({**json.loads(payload), "stage_sequence": sequence})
            size += len(payload)
        return {"stage_events": events, "artifact_progress": [json.loads(row[0]) for row in work],
                "stage_cursor": events[-1]["stage_sequence"] if events else after,
                "stage_has_more": len(rows) > len(events)}

    def record_work(self, run_id, progress):
        with self.lock, self.db:
            row = self.db.execute('SELECT payload FROM build_work WHERE run_id=? AND build_id=?', (run_id, progress['build_id'])).fetchone()
            previous = json.loads(row[0]) if row else {}
            if previous.get('updated_at', 0) >= progress.get('updated_at', 0):
                return
            progress = {**previous, **progress}
            self.db.execute('INSERT INTO build_work VALUES(?,?,?,?) ON CONFLICT(run_id,build_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at',
                            (run_id, progress['build_id'], json.dumps(progress), time.time()))
            self.db.execute('DELETE FROM build_work WHERE updated_at < ?', (time.time() - 86400,))

    def close(self):
        with self.lock:
            self.db.close()


def publish_receipt(adapter, run_id, payload):
    """Persist before enqueue, including when the live SSE consumer is absent."""
    if not payload.get("call_id") or "result" not in payload:
        return payload
    # TODO is telemetry, not a Stage output; its normal callback still carries it.
    if canonical_tool(payload.get("tool")) not in STAGE_TOOLS | MEDIA_TOOLS:
        return payload
    event = {**payload, "event": "tool.result_ready", "run_id": run_id}
    store = getattr(adapter, "_calliope_stage_events", None)
    if store is not None:
        try:
            sequence = store.record(run_id, event)
            return {**payload, "stage_sequence": sequence}
        except Exception:
            LOG.exception("Could not retain live Stage receipt for run %s", run_id)
    return payload  # Final transcript reconciliation remains the fallback.


def emit_ready(agent, ref, result):
    """Called by a finished parallel worker; never modifies model messages."""
    from gateway.calliope_artifact_progress import observe_tool
    observe_tool(agent, ref, result=result)
    fields = receipt_fields(ref.name, ref.args, result)
    if not fields or canonical_tool(ref.name) not in STAGE_TOOLS | MEDIA_TOOLS:
        return
    callback = getattr(agent, "tool_progress_callback", None)
    if callback:
        try:
            callback("tool.result_ready", ref.name, args=fields["arguments"],
                     result=fields["result"], tool_call_id=ref.call_id)
        except Exception:
            LOG.exception("Could not deliver live Stage receipt")
