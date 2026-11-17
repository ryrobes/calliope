"""Mirror Hermes' session store and agent.log into rvbbit.hermes_* (migration 0334).

Why this exists: explaining a Calliope turn's context behaviour needed three
sources nobody could join — Hermes' sqlite session store (transcript, tool
results, compaction flags, per-task token totals), Hermes' agent.log (the
ONLY per-API-call prompt/output/cache/latency numbers) and RVBBIT's own tool
activity. The Calliope server already mounts the Hermes volume read-only, so
it mirrors the first two into system tables on a timer; a DataRabbit app and
Calliope herself then read a session's tape with plain SQL.

Design points:
* sqlite is opened `mode=ro` (sees the WAL) with an `immutable=1` fallback.
* Sessions and messages are upserted keyed like the source; message rows of
  recently active sessions are re-upserted because `active`/`compacted` flip
  on compaction (there is no updated_at cursor upstream).
* agent.log is tailed by byte offset with a first-line identity for rotation
  detection; every derived row is keyed by a hash of the log line, so a
  re-read from zero is idempotent.
* No DLT: the mirror sidecar has no Hermes volume, sqlite is not a mirror
  dialect, and the flags make this a merge, not an append.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1] if len(_HERE.parents) > 1 else _HERE
_MIGRATION_NAME = "0334_hermes_session_mirror.sql"
_SCORECARD_MIGRATION_NAME = "0335_calliope_run_scorecard.sql"
_MAILBOX_MIGRATION_NAME = "0336_calliope_agent_messages.sql"
_CRON_MIRROR_MIGRATION_NAME = "0379_hermes_cron_mirror.sql"
STATE_DB_PATH = Path(os.environ.get("WAREHOUSE_HERMES_STATE_DB", "/opt/data/state.db"))
AGENT_LOG_PATH = Path(os.environ.get("WAREHOUSE_HERMES_AGENT_LOG", "/opt/data/logs/agent.log"))
# Multiplex profiles (Iris & co.) keep their own sqlite + logs under
# /opt/data/profiles/<name>/; each becomes a `store` in the mirror.
PROFILES_ROOT = Path(os.environ.get("WAREHOUSE_HERMES_PROFILES_ROOT", "/opt/data/profiles"))
DEFAULT_STORE = "default"
_TICK_SECONDS = max(15, min(3600, int(os.environ.get("WAREHOUSE_HERMES_MIRROR_TICK_SECONDS", "120") or 120)))
_ENABLED = os.environ.get("WAREHOUSE_HERMES_MIRROR", "on").strip().lower() not in {"off", "0", "false", "no"}
# Sessions whose last activity moved since the previous sync get their
# messages re-upserted (flag flips); this slack also covers clock skew.
_SESSION_SLACK_SECONDS = 900
_MAX_LOG_BYTES_PER_TICK = 32 * 1024 * 1024
_MAX_MESSAGE_ROWS_PER_TICK = 20000

_SESSION_COLUMNS = (
    "id", "store", "source", "user_id", "session_key", "chat_id", "chat_type", "thread_id", "display_name",
    "model", "model_config", "system_prompt_hash", "parent_session_id", "profile_name",
    "started_at", "ended_at", "end_reason", "last_activity_at",
    "message_count", "tool_call_count", "api_call_count",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
    "billing_provider", "billing_base_url", "estimated_cost_usd", "actual_cost_usd",
    "title", "title_source",
    "compression_failure_cooldown_until", "compression_failure_error",
    "compression_fallback_streak", "compression_ineffective_count",
    "rewind_count", "archived", "pinned", "raw",
)
_SESSION_TS_COLUMNS = {"started_at", "ended_at", "last_activity_at", "compression_failure_cooldown_until"}
_SESSION_BOOL_COLUMNS = {"archived", "pinned"}
_MESSAGE_COLUMNS = (
    "id", "store", "session_id", "role", "content", "content_chars", "content_sha256",
    "tool_call_id", "tool_name", "tool_calls", "tool_call_count", "tool_call_names", "tool_arg_chars",
    "effect_disposition", "ts", "token_count", "finish_reason",
    "reasoning_chars", "api_content_chars", "observed", "active", "compacted",
    "display_kind", "display_metadata",
)
_USAGE_COLUMNS = (
    "session_id", "store", "model", "billing_provider", "billing_base_url", "billing_mode", "task",
    "api_call_count", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "reasoning_tokens", "estimated_cost_usd", "actual_cost_usd", "cost_status", "cost_source",
    "first_seen", "last_seen",
)
_USAGE_KEY = ("session_id", "model", "billing_provider", "billing_base_url", "billing_mode", "task")


# ── helpers ────────────────────────────────────────────────────────────────

def _epoch_ts(value: Any) -> datetime | None:
    if value in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _sha256(text: str | None) -> str | None:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _json_or_none(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _jsonb(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=str, ensure_ascii=False)


def _log(message: str) -> None:
    print(f"hermes-mirror: {message}", file=sys.stderr)


# ── row mapping (pure; unit-tested) ─────────────────────────────────────────

def session_row(raw: dict[str, Any], store: str = DEFAULT_STORE) -> dict[str, Any]:
    """Map a sqlite `sessions` row to rvbbit.hermes_sessions column values."""
    row: dict[str, Any] = {}
    for column in _SESSION_COLUMNS:
        if column == "raw":
            continue
        if column == "store":
            row["store"] = store
            continue
        value = raw.get(column)
        if column in _SESSION_TS_COLUMNS:
            value = _epoch_ts(value)
        elif column in _SESSION_BOOL_COLUMNS:
            value = bool(value) if value is not None else None
        elif column == "model_config":
            value = _jsonb(_json_or_none(value))
        row[column] = value
    if not row.get("profile_name") and store != DEFAULT_STORE:
        row["profile_name"] = store
    remainder = {
        key: value for key, value in raw.items()
        if key not in _SESSION_COLUMNS and key != "system_prompt"
    }
    row["raw"] = _jsonb(remainder)
    return row


def message_row(raw: dict[str, Any], store: str = DEFAULT_STORE) -> dict[str, Any]:
    """Map a sqlite `messages` row to rvbbit.hermes_messages column values."""
    content = raw.get("content")
    if content is not None and not isinstance(content, str):
        content = str(content)
    if content and "\x00" in content:
        content = content.replace("\x00", "")   # Postgres text refuses NUL bytes
    tool_calls = _json_or_none(raw.get("tool_calls"))
    names: list[str] = []
    arg_chars = 0
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") if isinstance(call.get("function"), dict) else call
            name = fn.get("name") if isinstance(fn, dict) else None
            if isinstance(name, str) and name:
                names.append(name)
            args = fn.get("arguments") if isinstance(fn, dict) else None
            if isinstance(args, str):
                arg_chars += len(args)
            elif args is not None:
                arg_chars += len(json.dumps(args, default=str))
    reasoning_chars = sum(
        len(raw.get(key) or "") for key in
        ("reasoning", "reasoning_content", "reasoning_details", "codex_reasoning_items", "codex_message_items")
        if isinstance(raw.get(key), str)
    )
    return {
        "id": int(raw["id"]),
        "store": store,
        "session_id": str(raw.get("session_id") or ""),
        "role": str(raw.get("role") or ""),
        "content": content,
        "content_chars": len(content or ""),
        "content_sha256": _sha256(content),
        "tool_call_id": raw.get("tool_call_id"),
        "tool_name": raw.get("tool_name"),
        "tool_calls": _jsonb(tool_calls) if tool_calls is not None else None,
        "tool_call_count": len(tool_calls) if isinstance(tool_calls, list) else 0,
        "tool_call_names": names or None,
        "tool_arg_chars": arg_chars,
        "effect_disposition": raw.get("effect_disposition"),
        "ts": _epoch_ts(raw.get("timestamp")) or datetime.fromtimestamp(0, tz=timezone.utc),
        "token_count": raw.get("token_count"),
        "finish_reason": raw.get("finish_reason"),
        "reasoning_chars": reasoning_chars,
        "api_content_chars": len(raw.get("api_content") or "") if isinstance(raw.get("api_content"), str) else 0,
        "observed": bool(raw.get("observed")) if raw.get("observed") is not None else None,
        "active": bool(raw.get("active", 1)),
        "compacted": bool(raw.get("compacted", 0)),
        "display_kind": raw.get("display_kind"),
        "display_metadata": _jsonb(_json_or_none(raw.get("display_metadata"))),
    }


def usage_row(raw: dict[str, Any], store: str = DEFAULT_STORE) -> dict[str, Any]:
    row = {column: raw.get(column) for column in _USAGE_COLUMNS}
    row["store"] = store
    for column in ("billing_provider", "billing_base_url", "billing_mode", "task"):
        row[column] = row[column] or ""
    row["first_seen"] = _epoch_ts(raw.get("first_seen"))
    row["last_seen"] = _epoch_ts(raw.get("last_seen"))
    return row


# ── agent.log parsing (pure; unit-tested) ───────────────────────────────────

# `[profile=<name>] [<session>]` on multiplex profiles, `[<session>]` on the
# default gateway, or no tag at all — any run of bracketed tags before the
# logger name.
_LOG_LINE_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(?P<ms>\d{3}) "
    r"(?P<level>[A-Z]+) (?P<tags>(?:\[[^\]\s]+\] )*)(?P<logger>[\w.]+): (?P<msg>.*)$"
)
_TAG_RE = re.compile(r"\[([^\]\s]+)\]")
_API_CALL_RE = re.compile(
    r"^API call #(?P<n>\d+): model=(?P<model>\S+) provider=(?P<provider>\S+) "
    r"in=(?P<inp>\d+) out=(?P<out>\d+) total=(?P<total>\d+) latency=(?P<lat>[\d.]+)s"
    r"(?: cache=(?P<cache>\d+)/(?P<cache_of>\d+) \((?P<pct>\d+)%\))?"
)
_TURN_START_RE = re.compile(
    r"^conversation turn: session=(?P<session>\S+) model=(?P<model>\S+) provider=(?P<provider>\S+) "
    r"platform=(?P<platform>\S+) history=(?P<history>\d+) msg='(?P<msg>.*)'$"
)
_TURN_END_RE = re.compile(
    r"^Turn ended: reason=(?P<reason>\S+) model=(?P<model>\S+) api_calls=(?P<calls>\d+)/(?P<max>\d+) "
    r"budget=\S+ tool_turns=(?P<tool_turns>\d+) last_msg_role=(?P<role>\S+) response_len=(?P<len>\d+) "
    r"session=(?P<session>\S+)"
)
_COMPRESSION_START_RE = re.compile(
    r"^context compression started: session=(?P<session>\S+) messages=(?P<messages>\d+) "
    r"tokens=~(?P<tokens>[\d,]+) model=(?P<model>\S+)"
)
_COMPRESSION_DONE_RE = re.compile(
    r"^context compression done: session=(?P<session>\S+) messages=(?P<before>\d+)->(?P<after>\d+) "
    r"rough_tokens=~(?P<tokens>[\d,]+)"
)
_COMPRESSION_BLOCKED_RE = re.compile(
    r"^(?:Pre-API compression made insufficient progress: ~(?P<before>[\d,]+) -> ~(?P<after>[\d,]+)"
    r"|.*compression is currently blocked)"
)
_TOOL_DONE_RE = re.compile(r"^tool (?P<tool>\S+) completed \((?P<secs>[\d.]+)s, (?P<chars>\d+) chars\)")
_TOOL_ERROR_RE = re.compile(r"^Tool (?P<tool>\S+) returned error \((?P<secs>[\d.]+)s\): (?P<msg>.*)$")
_TRUNCATED_RE = re.compile(r"^Inline-truncating large tool result: (?P<tool>\S+) \((?P<chars>\d+) chars")
_TOOL_SEARCH_RE = re.compile(
    r"^tool_search activated \(tier (?P<tier>\d+)\): (?P<visible>\d+) core/visible tools kept, "
    r"(?P<deferred>\d+) deferred \(~(?P<deferred_tokens>\d+) tokens\), listing (?P<listing>\S+) "
    r"\(budget ~(?P<budget>\d+) tokens\)"
)
_SESSION_IN_MSG_RE = re.compile(r"session=(?P<session>[A-Za-z0-9_.:-]+)")


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value.replace(",", ""))


def parse_log_line(line: str) -> dict[str, Any] | None:
    """Turn one agent.log line into {'table': 'api_calls'|'events', 'row': {...}} or None."""
    text = line.rstrip("\r\n")
    m = _LOG_LINE_RE.match(text)
    if not m:
        return None
    try:
        ts = datetime.strptime(f"{m.group('date')}.{m.group('ms')}", "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    session = None
    for tag in _TAG_RE.findall(m.group("tags") or ""):
        if tag.startswith("profile="):
            continue
        session = tag
    msg = m.group("msg")
    line_hash = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()

    call = _API_CALL_RE.match(msg)
    if call:
        return {"table": "api_calls", "row": {
            "line_hash": line_hash, "session_id": session, "call_no": int(call.group("n")), "ts": ts,
            "model": call.group("model"), "provider": call.group("provider"),
            "input_tokens": _int(call.group("inp")), "output_tokens": _int(call.group("out")),
            "total_tokens": _int(call.group("total")),
            "cache_read_tokens": _int(call.group("cache")) if call.group("cache") is not None else None,
            "latency_ms": int(round(float(call.group("lat")) * 1000)), "raw": text[:2000],
        }}

    kind = None
    details: dict[str, Any] = {}
    if (t := _TURN_START_RE.match(msg)):
        kind, session = "turn_started", t.group("session")
        details = {"model": t.group("model"), "provider": t.group("provider"), "platform": t.group("platform"),
                   "history": int(t.group("history")), "message_head": t.group("msg")[:200]}
    elif (t := _TURN_END_RE.match(msg)):
        kind, session = "turn_ended", t.group("session")
        details = {"reason": t.group("reason"), "api_calls": int(t.group("calls")),
                   "tool_turns": int(t.group("tool_turns")), "response_len": int(t.group("len"))}
    elif (t := _COMPRESSION_START_RE.match(msg)):
        kind, session = "compression_started", t.group("session")
        details = {"messages": int(t.group("messages")), "tokens": _int(t.group("tokens")), "model": t.group("model")}
    elif (t := _COMPRESSION_DONE_RE.match(msg)):
        kind, session = "compression_done", t.group("session")
        details = {"messages_before": int(t.group("before")), "messages_after": int(t.group("after")),
                   "rough_tokens": _int(t.group("tokens"))}
    elif (t := _COMPRESSION_BLOCKED_RE.match(msg)):
        kind = "compression_blocked"
        details = {"before": _int(t.group("before")), "after": _int(t.group("after"))} if t.group("before") else {}
    elif (t := _TOOL_DONE_RE.match(msg)):
        kind = "tool_completed"
        details = {"tool": t.group("tool"), "seconds": float(t.group("secs")), "chars": int(t.group("chars"))}
    elif (t := _TOOL_ERROR_RE.match(msg)):
        kind = "tool_error"
        details = {"tool": t.group("tool"), "seconds": float(t.group("secs")), "message": t.group("msg")[:400]}
    elif (t := _TRUNCATED_RE.match(msg)):
        kind = "tool_result_truncated"
        details = {"tool": t.group("tool"), "chars": int(t.group("chars"))}
    elif (t := _TOOL_SEARCH_RE.match(msg)):
        kind = "tool_search"
        details = {"tier": int(t.group("tier")), "visible": int(t.group("visible")),
                   "deferred": int(t.group("deferred")), "deferred_tokens": int(t.group("deferred_tokens")),
                   "listing": t.group("listing"), "budget_tokens": int(t.group("budget"))}
    if kind is None:
        return None
    if session is None:
        found = _SESSION_IN_MSG_RE.search(msg)
        session = found.group("session") if found else None
    return {"table": "events", "row": {
        "line_hash": line_hash, "session_id": session, "ts": ts, "kind": kind,
        "details": details, "raw": text[:2000],
    }}


# ── log tailing (file offset + rotation identity) ───────────────────────────

def _file_identity(path: Path) -> str:
    """First 256 bytes hashed — a rotated/truncated file gets a new identity."""
    with path.open("rb") as handle:
        head = handle.read(256)
    return hashlib.sha256(head).hexdigest()[:24]


def read_log_delta(path: Path, position: int, identity: str | None) -> tuple[list[str], int, str]:
    """Return (complete new lines, new position, identity). Restarts from 0 when
    the file was rotated or truncated. Reads at most _MAX_LOG_BYTES_PER_TICK."""
    if not path.is_file():
        return [], position, identity or ""
    current_identity = _file_identity(path)
    size = path.stat().st_size
    if identity and identity != current_identity:
        position = 0
    if position > size:
        position = 0
    with path.open("rb") as handle:
        handle.seek(position)
        chunk = handle.read(_MAX_LOG_BYTES_PER_TICK)
    if not chunk:
        return [], position, current_identity
    last_newline = chunk.rfind(b"\n")
    if last_newline < 0:
        return [], position, current_identity  # a partial line; wait for the rest
    complete = chunk[: last_newline + 1]
    lines = complete.decode("utf-8", "replace").splitlines()
    return lines, position + len(complete), current_identity


# ── sqlite access ───────────────────────────────────────────────────────────

def open_state_db(path: Path = STATE_DB_PATH) -> sqlite3.Connection:
    """Read-only handle that sees the WAL; immutable snapshot as a fallback."""
    last_error: Exception | None = None
    for uri in (f"file:{path}?mode=ro", f"file:{path}?immutable=1"):
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            return conn
        except sqlite3.Error as exc:
            last_error = exc
    raise RuntimeError(f"cannot open Hermes state db {path}: {last_error}")


# ── the sync itself ─────────────────────────────────────────────────────────

def _cursor(pg: Any) -> Any:
    """psycopg connections execute() but only cursors executemany()."""
    return pg.cursor() if hasattr(pg, "cursor") else pg


def _upsert(pg: Any, table: str, columns: Iterable[str], key: Iterable[str], rows: list[dict[str, Any]],
            jsonb_columns: Iterable[str] = ()) -> int:
    columns = tuple(columns)
    key = tuple(key)
    jsonb_columns = set(jsonb_columns)
    if not rows:
        return 0
    placeholders = ", ".join(f"%({c})s::jsonb" if c in jsonb_columns else f"%({c})s" for c in columns)
    updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in columns if c not in key)
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}, synced_at=now()"
    )
    _cursor(pg).executemany(sql, rows)
    return len(rows)


def _state(cursor: Any, source: str) -> tuple[int, str | None, dict[str, Any]]:
    row = cursor.execute(
        "SELECT position, identity, details FROM rvbbit.hermes_mirror_state WHERE source=%s", (source,)
    ).fetchone()
    if not row:
        return 0, None, {}
    row = dict(row) if not isinstance(row, dict) else row
    return int(row.get("position") or 0), row.get("identity"), dict(row.get("details") or {})


def _save_state(cursor: Any, source: str, position: int, identity: str | None, details: dict[str, Any]) -> None:
    cursor.execute(
        "INSERT INTO rvbbit.hermes_mirror_state (source, position, identity, details, updated_at) "
        "VALUES (%s,%s,%s,%s::jsonb,now()) ON CONFLICT (source) DO UPDATE SET position=EXCLUDED.position, "
        "identity=EXCLUDED.identity, details=EXCLUDED.details, updated_at=now()",
        (source, int(position), identity, _jsonb(details or {})),
    )


# Hermes spells the appliance's Hutch endpoint `custom`; the provider control
# surface (and the Models & agents UI) calls it "clover". Every other
# provider id is shared. There are no aliases: the model on the log line is
# the model the agent is configured with.
_CALLIOPE_PROVIDER_IDS = {"custom": "clover"}


def _billing_route(provider: Any, model: Any) -> tuple[str | None, str | None]:
    hermes_provider = str(provider or "").strip().lower()
    if not hermes_provider:
        return (None, None)
    return (_CALLIOPE_PROVIDER_IDS.get(hermes_provider, hermes_provider), str(model or "") or None)


def _state_key(kind: str, store: str) -> str:
    return kind if store == DEFAULT_STORE else f"{kind}:{store}"


def sync_store(pg: Any, sqlite_conn: sqlite3.Connection, store: str = DEFAULT_STORE) -> dict[str, int]:
    """Upsert sessions, changed/new messages and model usage of one sqlite
    store. `pg` is an open psycopg connection (dict rows)."""
    stats = {"sessions": 0, "messages": 0, "usage": 0}
    position, _identity, details = _state(pg, _state_key("state.db", store))
    max_message_id = position
    watermark = float(details.get("session_watermark") or 0.0)
    since = max(0.0, watermark - _SESSION_SLACK_SECONDS)

    if watermark:
        session_rows = sqlite_conn.execute(
            "SELECT * FROM sessions WHERE coalesce(last_activity_at, started_at) >= ? OR started_at >= ?",
            (since, since),
        ).fetchall()
    else:
        session_rows = sqlite_conn.execute("SELECT * FROM sessions").fetchall()
    sessions = [session_row(dict(row), store) for row in session_rows]
    changed_ids = [row["id"] for row in sessions]
    stats["sessions"] = _upsert(pg, "rvbbit.hermes_sessions", _SESSION_COLUMNS, ("id",), sessions,
                                jsonb_columns=("model_config", "raw"))
    new_watermark = watermark
    for row in session_rows:
        candidate = row["last_activity_at"] or row["started_at"] or 0
        if candidate and float(candidate) > new_watermark:
            new_watermark = float(candidate)

    # Messages: everything new (capped per tick), plus a refresh of the
    # already-mirrored rows of every session that moved — compaction flips
    # active/compacted without touching the message row itself.
    new_rows = sqlite_conn.execute(
        "SELECT * FROM messages WHERE id > ? ORDER BY id LIMIT ?",
        (max_message_id, _MAX_MESSAGE_ROWS_PER_TICK),
    ).fetchall()
    capped = len(new_rows) >= _MAX_MESSAGE_ROWS_PER_TICK
    refresh_rows: list[Any] = []
    if changed_ids and max_message_id > 0:
        marks = ",".join("?" for _ in changed_ids)
        refresh_rows = sqlite_conn.execute(
            f"SELECT * FROM messages WHERE id <= ? AND session_id IN ({marks})",
            (max_message_id, *changed_ids),
        ).fetchall()
    messages = [message_row(dict(row), store) for row in [*refresh_rows, *new_rows]]
    stats["messages"] = _upsert(pg, "rvbbit.hermes_messages", _MESSAGE_COLUMNS, ("store", "id"), messages,
                                jsonb_columns=("tool_calls", "display_metadata"))
    if new_rows:
        max_message_id = max(max_message_id, max(int(row["id"]) for row in new_rows))
    # A capped tick leaves the session watermark where it was so the same
    # sessions are refreshed again once the backlog has drained.
    if not capped:
        watermark = new_watermark

    usage = [usage_row(dict(row), store) for row in sqlite_conn.execute("SELECT * FROM session_model_usage").fetchall()]
    stats["usage"] = _upsert(pg, "rvbbit.hermes_model_usage", _USAGE_COLUMNS, _USAGE_KEY, usage)

    _save_state(pg, _state_key("state.db", store), max_message_id, None, {
        "session_watermark": watermark, "capped": capped, "last_stats": stats,
    })
    return stats


def sync_log(pg: Any, log_path: Path = AGENT_LOG_PATH, store: str = DEFAULT_STORE) -> dict[str, int]:
    """Tail one agent.log into hermes_api_calls / hermes_context_events."""
    stats = {"lines": 0, "api_calls": 0, "events": 0}
    position, identity, details = _state(pg, _state_key("agent.log", store))
    lines, new_position, new_identity = read_log_delta(log_path, position, identity)
    if not lines:
        return stats
    calls: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for line in lines:
        parsed = parse_log_line(line)
        if not parsed:
            continue
        row = parsed["row"]
        row["store"] = store
        if parsed["table"] == "api_calls":
            row["route_provider"], row["route_model"] = _billing_route(row.get("provider"), row.get("model"))
            calls.append(row)
        else:
            row["details"] = _jsonb(row["details"])
            events.append(row)
    stats["lines"] = len(lines)
    if calls:
        _cursor(pg).executemany(
            "INSERT INTO rvbbit.hermes_api_calls (line_hash, store, session_id, call_no, ts, model, provider, "
            "route_provider, route_model, input_tokens, output_tokens, total_tokens, cache_read_tokens, "
            "latency_ms, raw) VALUES (%(line_hash)s,%(store)s,%(session_id)s,%(call_no)s,%(ts)s,%(model)s,%(provider)s,"
            "%(route_provider)s,%(route_model)s,%(input_tokens)s,%(output_tokens)s,%(total_tokens)s,"
            "%(cache_read_tokens)s,%(latency_ms)s,%(raw)s) ON CONFLICT (line_hash) DO NOTHING",
            calls,
        )
        stats["api_calls"] = len(calls)
    if events:
        _cursor(pg).executemany(
            "INSERT INTO rvbbit.hermes_context_events (line_hash, store, session_id, ts, kind, details, raw) "
            "VALUES (%(line_hash)s,%(store)s,%(session_id)s,%(ts)s,%(kind)s,%(details)s::jsonb,%(raw)s) "
            "ON CONFLICT (line_hash) DO NOTHING",
            events,
        )
        stats["events"] = len(events)
    _save_state(pg, _state_key("agent.log", store), new_position, new_identity, {"last_stats": stats})
    return stats


_CRON_JOB_COLUMNS = ("profile_key", "job_id", "name", "schedule", "enabled",
                     "provider", "model", "deliver", "skills", "prompt", "raw")
_CRON_EXEC_COLUMNS = ("profile_key", "execution_id", "job_id", "status",
                      "claimed_at", "started_at", "finished_at", "error",
                      "output_path", "delivery_outcome", "hermes_session_id")
_MAX_CRON_EXEC_ROWS = 200


def _cron_job_row(profile_key: str, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile_key": profile_key,
        "job_id": str(raw.get("id") or ""),
        "name": raw.get("name"),
        "schedule": str(raw.get("schedule") or "") or None,
        "enabled": bool(raw.get("enabled", True)),
        "provider": raw.get("provider"),
        "model": raw.get("model"),
        "deliver": raw.get("deliver"),
        "skills": _jsonb(raw.get("skills") or []),
        "prompt": (str(raw.get("prompt") or "")[:4000]) or None,
        "raw": _jsonb(raw),
    }


def _cron_execution_row(profile_key: str, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile_key": profile_key,
        "execution_id": str(raw.get("id") or ""),
        "job_id": raw.get("job_id"),
        "status": raw.get("status"),
        "claimed_at": raw.get("claimed_at") or None,
        "started_at": raw.get("started_at") or None,
        "finished_at": raw.get("finished_at") or None,
        "error": (str(raw.get("error") or "")[:4000]) or None,
        "output_path": raw.get("output_path"),
        "delivery_outcome": raw.get("delivery_outcome"),
        "hermes_session_id": raw.get("hermes_session_id"),
    }


def sync_cron(pg: Any, profiles_root: Path = PROFILES_ROOT) -> dict[str, int]:
    """Mirror every profile's cron store (jobs.json + executions.db).

    This is the duty-cycle observability lane: the 2026-09-03 drift-skip
    incident (three specialists' nightly crons dead for two weeks, the
    scheduler's alert delivered to an unread local file) is invisible until
    executions are rows the health view and alert tick can see. Small tables,
    full upsert each tick, executions capped to the recent window per job
    store — history depth belongs to the sqlite side.
    """
    stats = {"cron_jobs": 0, "cron_execs": 0}
    roots: list[tuple[str, Path]] = [(DEFAULT_STORE, Path("/opt/data/cron"))]
    if profiles_root.is_dir():
        for child in sorted(profiles_root.iterdir()):
            if child.name.startswith(".") or not child.is_dir():
                continue
            roots.append((child.name, child / "cron"))
    for profile_key, cron_dir in roots:
        jobs_path = cron_dir / "jobs.json"
        if jobs_path.is_file():
            try:
                payload = json.loads(jobs_path.read_text(encoding="utf-8"))
                jobs = [
                    _cron_job_row(profile_key, item)
                    for item in (payload.get("jobs") or [])
                    if isinstance(item, dict) and item.get("id")
                ]
                stats["cron_jobs"] += _upsert(
                    pg, "rvbbit.hermes_cron_jobs", _CRON_JOB_COLUMNS,
                    ("profile_key", "job_id"), jobs,
                    jsonb_columns=("skills", "raw"),
                )
            except Exception as exc:  # noqa: BLE001 — one bad store never stops the sweep
                _log(f"cron jobs skipped ({profile_key}): {str(exc)[:160]}")
        exec_path = cron_dir / "executions.db"
        if exec_path.is_file():
            try:
                sqlite_conn = open_state_db(exec_path)
                try:
                    rows = sqlite_conn.execute(
                        "SELECT * FROM executions ORDER BY rowid DESC LIMIT ?",
                        (_MAX_CRON_EXEC_ROWS,),
                    ).fetchall()
                finally:
                    sqlite_conn.close()
                executions = [
                    _cron_execution_row(profile_key, dict(row))
                    for row in rows
                    if dict(row).get("id")
                ]
                stats["cron_execs"] += _upsert(
                    pg, "rvbbit.hermes_cron_executions", _CRON_EXEC_COLUMNS,
                    ("profile_key", "execution_id"), executions,
                )
            except Exception as exc:  # noqa: BLE001
                _log(f"cron executions skipped ({profile_key}): {str(exc)[:160]}")
    return stats


def discover_stores(state_db: Path = STATE_DB_PATH, agent_log: Path = AGENT_LOG_PATH,
                    profiles_root: Path = PROFILES_ROOT) -> list[tuple[str, Path, Path]]:
    """(store, state.db, agent.log) for the default gateway and every profile."""
    stores = [(DEFAULT_STORE, state_db, agent_log)]
    if profiles_root.is_dir():
        for child in sorted(profiles_root.iterdir()):
            if child.name.startswith(".") or not child.is_dir():
                continue
            db = child / "state.db"
            log = child / "logs" / "agent.log"
            if db.is_file() or log.is_file():
                stores.append((child.name, db, log))
    return stores


def sync_once(conn_factory: Callable[[], Any], *, state_db: Path = STATE_DB_PATH,
              agent_log: Path = AGENT_LOG_PATH, profiles_root: Path = PROFILES_ROOT) -> dict[str, Any]:
    """One full mirror pass over every store. Returns per-store stats."""
    result: dict[str, Any] = {}
    for store, db, log in discover_stores(state_db, agent_log, profiles_root):
        entry: dict[str, Any] = {"store": None, "log": None}
        if db.is_file():
            sqlite_conn = open_state_db(db)
            try:
                with conn_factory() as pg:
                    entry["store"] = sync_store(pg, sqlite_conn, store)
            finally:
                sqlite_conn.close()
        if log.is_file():
            with conn_factory() as pg:
                entry["log"] = sync_log(pg, log, store)
        result[store] = entry
    try:
        with conn_factory() as pg:
            result["cron"] = {"store": sync_cron(pg, profiles_root), "log": None}
    except Exception as exc:  # noqa: BLE001 — the session mirror must survive a cron-sweep failure
        _log(f"cron sweep failed ({type(exc).__name__}): {str(exc)[:200]}")
    try:
        with conn_factory() as pg:
            delivered = deliver_standing_summaries(pg)
            if delivered:
                result["standing_summaries"] = delivered
    except Exception as exc:  # noqa: BLE001 — delivery retries on the next tick
        _log(f"standing delivery failed ({type(exc).__name__}): {str(exc)[:200]}")
    return result


def deliver_standing_summaries(pg: Any) -> int:
    """Deliver each completed standing run's final summary onto its thread
    as an agent message (0398 rung 3). The run itself never needs a delivery
    tool: the mirror sees the execution complete, reads the run session's
    final assistant text from the transcript it just ingested, and queues the
    mailbox row. If the transcript has not landed yet the watermark stays put
    and the next tick delivers — self-healing, at-most-once per execution."""
    ready = pg.execute(
        "SELECT to_regclass('rvbbit.calliope_session_schedules') IS NOT NULL "
        "AND to_regclass('rvbbit.calliope_agent_messages') IS NOT NULL AS r"
    ).fetchone()
    if not (ready and ready["r"]):
        return 0
    pending = pg.execute(
        """
        SELECT s.id AS schedule_id, s.session_id, s.owner_email,
               s.last_delivered_at, e.execution_id, e.finished_at,
               e.hermes_session_id, cs.title
          FROM rvbbit.calliope_session_schedules s
          JOIN rvbbit.calliope_sessions cs ON cs.id = s.session_id
          JOIN rvbbit.hermes_cron_executions e
            ON e.profile_key = 'default' AND e.job_id = s.hermes_job_id
         WHERE s.hermes_job_id IS NOT NULL
           AND e.status = 'completed' AND e.finished_at IS NOT NULL
           AND e.hermes_session_id IS NOT NULL
           AND e.finished_at > coalesce(s.last_delivered_at, 'epoch'::timestamptz)
         ORDER BY e.finished_at
         LIMIT 20
        """
    ).fetchall()
    delivered = 0
    for row in pending:
        summary = pg.execute(
            "SELECT left(coalesce(content,''), 6000) AS content "
            "FROM rvbbit.hermes_messages WHERE session_id=%s "
            "AND role='assistant' AND coalesce(content,'')<>'' "
            "ORDER BY id DESC LIMIT 1",
            (row["hermes_session_id"],),
        ).fetchone()
        if not summary or not str(summary.get("content") or "").strip():
            continue  # transcript not mirrored yet; next tick delivers
        pg.execute(
            "INSERT INTO rvbbit.calliope_agent_messages "
            "(owner_email,kind,from_kind,from_ref,from_label,to_session_id,"
            "subject,body,payload) VALUES (%s,'note','system',%s,%s,%s::uuid,"
            "%s,%s,%s::jsonb)",
            (
                str(row["owner_email"]),
                f"standing_run:{row['schedule_id']}",
                "Standing run",
                str(row["session_id"]),
                f"Standing run finished — {str(row.get('title') or 'this thread')[:120]}",
                str(summary["content"]).strip(),
                json.dumps({
                    "standing_run": True,
                    "schedule_id": str(row["schedule_id"]),
                    "execution_id": str(row["execution_id"]),
                    "run_hermes_session_id": str(row["hermes_session_id"]),
                }),
            ),
        )
        pg.execute(
            "UPDATE rvbbit.calliope_session_schedules SET "
            "last_delivered_at=%s, updated_at=now() WHERE id=%s::uuid",
            (row["finished_at"], str(row["schedule_id"])),
        )
        delivered += 1
    return delivered


# ── schema ──────────────────────────────────────────────────────────────────

def _migration_path(name: str = _MIGRATION_NAME) -> Path:
    shipped = _HERE / "sql" / name
    return shipped if shipped.exists() else _REPO / "crates" / "pg_rvbbit" / "sql" / "migrations" / name


def _recorded(conn: Any, name: str) -> bool:
    """Whether the extension chain already applied this migration. It then
    owns those objects, and later migrations may have widened them (a column
    added to a view), so re-running this boot copy would fail: CREATE OR
    REPLACE VIEW cannot drop columns. Unknown means not recorded."""
    try:
        result = conn.execute(
            "SELECT to_regclass('rvbbit.schema_migrations') IS NOT NULL AS ready"
        )
        row = result.fetchone() if result is not None else None
        ready = row.get("ready") if isinstance(row, dict) else (row[0] if row else False)
        if not ready:
            return False
        found = conn.execute(
            "SELECT 1 AS found FROM rvbbit.schema_migrations WHERE name=%s",
            (name.removesuffix(".sql"),),
        ).fetchone()
        return found is not None
    except Exception:  # noqa: BLE001 — a role without catalog access applies as before
        return False


def _apply(conn: Any, name: str) -> bool:
    """Apply one shipped migration unless the extension already recorded it."""
    path = _migration_path(name)
    if not path.is_file() or _recorded(conn, name):
        return False
    conn.execute(path.read_text(encoding="utf-8"))
    return True


def ensure(conn_factory: Callable[[], Any]) -> bool:
    """Apply 0334 (idempotent DDL) so the mirror works before the extension
    chain ships it. Once the extension has recorded a migration, its objects
    are the extension's and this boot copy steps aside."""
    if not _migration_path().is_file():
        return False
    with conn_factory() as pg:
        _apply(pg, _MIGRATION_NAME)
        # The per-run scorecard view (0335) reads these tables; ship it the
        # same way so a run has a scorecard row before the extension chain
        # catches up.
        _apply(pg, _SCORECARD_MIGRATION_NAME)
        # 0336 (agent mailbox + scorecard revision) supersedes the 0335 view;
        # apply it last so the boot-time re-application lands on the newest
        # column set.
        _apply(pg, _MAILBOX_MIGRATION_NAME)
        # 0379 (duty-cycle mirror + health view + alert tick) rides the same
        # boot path so cron observability works before the extension ships it.
        _apply(pg, _CRON_MIRROR_MIGRATION_NAME)
    return True


def ensure_agent_mailbox(conn: Any) -> bool:
    """Apply 0336 on an open connection (Calliope's boot DDL step) so the
    agent mailbox exists before the first async delegation, without waiting
    for the mirror worker or the extension chain."""
    if not _migration_path(_MAILBOX_MIGRATION_NAME).is_file():
        return False
    _apply(conn, _MAILBOX_MIGRATION_NAME)
    return True


# ── worker ──────────────────────────────────────────────────────────────────

_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.Lock()
_WAKE = threading.Event()


def _worker(conn_factory: Callable[[], Any]) -> None:
    misses = 0
    try:
        ensure(conn_factory)
    except Exception as exc:  # noqa: BLE001 — a read-only role gets the tables from the extension instead
        _log(f"schema ensure skipped ({type(exc).__name__}): {str(exc)[:200]}")
    while True:
        try:
            result = sync_once(conn_factory)
            moved = sum(
                sum((entry.get("store") or {}).values()) + sum((entry.get("log") or {}).values())
                for entry in result.values()
            )
            if moved:
                _log(f"synced {json.dumps({k: v for k, v in result.items() if sum((v.get('store') or {}).values()) + sum((v.get('log') or {}).values())}, default=str)}")
            misses = 0
        except Exception as exc:  # noqa: BLE001 — a bad tick must not stop the mirror
            misses += 1
            if misses <= 3 or misses % 30 == 0:
                _log(f"tick failed ({type(exc).__name__}): {str(exc)[:300]}")
        _WAKE.wait(_TICK_SECONDS)
        _WAKE.clear()


def available() -> bool:
    return _ENABLED and (STATE_DB_PATH.is_file() or AGENT_LOG_PATH.is_file() or PROFILES_ROOT.is_dir())


def start_worker(conn_factory: Callable[[], Any]) -> bool:
    """Start the mirror thread once per process; False when there is nothing to mirror."""
    global _THREAD
    if not available():
        return False
    with _THREAD_LOCK:
        if _THREAD and _THREAD.is_alive():
            return True
        _THREAD = threading.Thread(target=_worker, args=(conn_factory,), name="hermes-mirror", daemon=True)
        _THREAD.start()
    return True


def wake() -> None:
    _WAKE.set()
