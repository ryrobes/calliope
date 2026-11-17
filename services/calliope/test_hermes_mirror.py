"""hermes_mirror: Hermes' session store + agent.log → rvbbit.hermes_* (0334)."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import hermes_mirror as hm  # noqa: E402


# Real lines from a hosted install's agent.log, 2026-08-17.
LINES = [
    "2026-08-17 15:07:18,580 INFO [calliope_1786979177_b48e012eac] agent.turn_context: conversation turn: session=calliope_1786979177_b48e012eac model=calliope provider=custom platform=api_server history=0 msg='Lets make a nice dashboad based on Bikeshare stations and their status. Lets als...'",
    "2026-08-17 15:07:18,481 INFO tools.tool_search: tool_search activated (tier 1): 19 core/visible tools kept, 156 deferred (~34329 tokens), listing full (budget ~4000 tokens)",
    "2026-08-17 15:07:27,625 INFO [calliope_1786979177_b48e012eac] agent.conversation_loop: API call #1: model=calliope provider=custom in=86341 out=609 total=86950 latency=8.9s cache=49226/86341 (57%)",
    "2026-08-17 15:07:27,701 INFO [calliope_1786979177_b48e012eac] agent.tool_executor: tool tool_describe completed (0.06s, 681 chars)",
    "2026-08-17 15:07:36,116 INFO [calliope_1786979177_b48e012eac] agent.tool_executor: tool mcp__Warehouse__search_data completed (2.76s, 27362 chars)",
    "2026-08-17 15:07:45,834 INFO agent.conversation_compression: context compression started: session=calliope_1786979177_b48e012eac messages=14 tokens=~142,326 model=calliope focus=None",
    "2026-08-17 15:08:16,124 INFO agent.conversation_compression: context compression done: session=calliope_1786979177_b48e012eac messages=14->12 rough_tokens=~26,651 awaiting_real_usage=true",
    "2026-08-17 15:08:36,746 INFO [calliope_1786979177_b48e012eac] tools.tool_result_storage: Inline-truncating large tool result: mcp__Warehouse__search_data (155750 chars, no sandbox write)",
    "2026-08-17 15:10:45,485 INFO [calliope_1786979177_b48e012eac] agent.conversation_loop: Turn ended: reason=text_response(finish_reason=stop) model=calliope api_calls=12/500 budget=12/500 tool_turns=8 last_msg_role=assistant response_len=512 session=calliope_1786979177_b48e012eac",
    "2026-08-17 15:11:25,326 WARNING [calliope_1786979177_b48e012eac] agent.tool_executor: Tool skill_manage returned error (0.01s): {\"success\": false, \"error\": \"Refusing background curator patch\"}",
    "2026-08-17 15:17:55,642 INFO [calliope_1786979177_b48e012eac] agent.conversation_loop: API call #1: model=calliope provider=custom in=89495 out=449 total=89944 latency=12.9s",
    "2026-08-17 15:08:16,129 INFO [calliope_1786979177_b48e012eac] agent.conversation_loop: Repaired 1 message-alternation violations before request (session=calliope_1786979177_b48e012eac)",
    "2026-08-17 15:07:21,068 INFO aiohttp.access: 127.0.0.1 [17/Aug/2026:15:07:21 +0000] \"GET /health HTTP/1.1\" 200 526 \"-\" \"curl/8.14.1\"",
    # multiplex profile logs carry a profile tag before the session tag
    "2026-08-17 18:06:01,014 INFO [profile=dashboard-studio] [calliope_specialist_thread_e7fa5c7cf6af4d8895194331d371371d] agent.conversation_loop: API call #1: model=calliope provider=custom in=65494 out=350 total=65844 latency=6.1s cache=31000/65494 (47%)",
]


def test_parse_log_lines_yield_api_calls_and_context_events():
    parsed = [hm.parse_log_line(line) for line in LINES]
    kinds = [(p["table"], p["row"].get("kind")) if p else None for p in parsed]
    assert kinds == [
        ("events", "turn_started"), ("events", "tool_search"), ("api_calls", None),
        ("events", "tool_completed"), ("events", "tool_completed"),
        ("events", "compression_started"), ("events", "compression_done"),
        ("events", "tool_result_truncated"), ("events", "turn_ended"), ("events", "tool_error"),
        ("api_calls", None), None, None, ("api_calls", None),
    ]
    profiled = parsed[13]["row"]
    assert profiled["session_id"] == "calliope_specialist_thread_e7fa5c7cf6af4d8895194331d371371d"
    assert profiled["input_tokens"] == 65494 and profiled["cache_read_tokens"] == 31000
    call = parsed[2]["row"]
    assert call["session_id"] == "calliope_1786979177_b48e012eac" and call["call_no"] == 1
    assert (call["input_tokens"], call["output_tokens"], call["total_tokens"], call["cache_read_tokens"]) == (86341, 609, 86950, 49226)
    assert call["latency_ms"] == 8900 and call["model"] == "calliope" and call["provider"] == "custom"
    assert call["ts"] == datetime(2026, 8, 17, 15, 7, 27, 625000, tzinfo=timezone.utc)
    assert len(call["line_hash"]) == 64
    uncached = parsed[10]["row"]
    assert uncached["cache_read_tokens"] is None and uncached["input_tokens"] == 89495

    started = parsed[5]["row"]                       # session recovered from `session=` in the message
    assert started["session_id"] == "calliope_1786979177_b48e012eac"
    assert started["details"] == {"messages": 14, "tokens": 142326, "model": "calliope"}
    done = parsed[6]["row"]
    assert done["details"] == {"messages_before": 14, "messages_after": 12, "rough_tokens": 26651}
    assert parsed[0]["row"]["details"]["history"] == 0 and parsed[0]["row"]["details"]["platform"] == "api_server"
    assert parsed[1]["row"]["session_id"] is None
    assert parsed[1]["row"]["details"] == {"tier": 1, "visible": 19, "deferred": 156, "deferred_tokens": 34329,
                                           "listing": "full", "budget_tokens": 4000}
    assert parsed[4]["row"]["details"] == {"tool": "mcp__Warehouse__search_data", "seconds": 2.76, "chars": 27362}
    assert parsed[7]["row"]["details"] == {"tool": "mcp__Warehouse__search_data", "chars": 155750}
    assert parsed[8]["row"]["details"] == {"reason": "text_response(finish_reason=stop)", "api_calls": 12,
                                           "tool_turns": 8, "response_len": 512}
    assert parsed[9]["row"]["details"]["tool"] == "skill_manage"
    # idempotence key: same line → same hash; different line → different hash
    assert hm.parse_log_line(LINES[2])["row"]["line_hash"] == call["line_hash"]
    assert hm.parse_log_line(LINES[10])["row"]["line_hash"] != call["line_hash"]


def test_read_log_delta_tails_complete_lines_and_restarts_on_rotation(tmp_path):
    log = tmp_path / "agent.log"
    log.write_text(LINES[0] + "\n" + LINES[2] + "\n" + "2026-08-17 15:07:28,000 INFO x: partial", encoding="utf-8")
    lines, position, identity = hm.read_log_delta(log, 0, None)
    assert lines == [LINES[0], LINES[2]]                       # the partial tail waits
    assert position == len((LINES[0] + "\n" + LINES[2] + "\n").encode())
    assert identity
    # nothing new
    assert hm.read_log_delta(log, position, identity)[0] == []
    # the partial line completes
    with log.open("a", encoding="utf-8") as handle:
        handle.write(" done\n")
    lines2, position2, _ = hm.read_log_delta(log, position, identity)
    assert lines2 == ["2026-08-17 15:07:28,000 INFO x: partial done"] and position2 > position
    # rotation: new content, new first line → restart from zero
    log.write_text(LINES[8] + "\n", encoding="utf-8")
    lines3, position3, identity3 = hm.read_log_delta(log, position2, identity)
    assert lines3 == [LINES[8]] and identity3 != identity and position3 == len((LINES[8] + "\n").encode())
    # truncation without a first-line change: size < position → restart too
    log.write_text(LINES[8] + "\n", encoding="utf-8")
    assert hm.read_log_delta(log, 10_000, identity3)[0] == [LINES[8]]


def test_row_mapping_measures_payloads_and_keeps_json_faithful():
    raw = {
        "id": 7627, "session_id": "s1", "role": "assistant", "content": None, "tool_call_id": None,
        "tool_calls": json.dumps([{"id": "c1", "type": "function", "function": {"name": "write_file", "arguments": json.dumps({"path": "/opt/data/x.html", "content": "<html>" + "x" * 100})}}]),
        "tool_name": None, "effect_disposition": None, "timestamp": 1786981863.0, "token_count": None,
        "finish_reason": "tool_calls", "reasoning": "thinking...", "reasoning_content": None, "reasoning_details": None,
        "codex_reasoning_items": None, "codex_message_items": None, "platform_message_id": None,
        "observed": 0, "active": 1, "compacted": 0, "api_content": None, "display_kind": None, "display_metadata": None,
    }
    row = hm.message_row(raw)
    assert row["id"] == 7627 and row["role"] == "assistant" and row["content"] is None and row["content_chars"] == 0
    assert row["content_sha256"] is None
    assert row["tool_call_count"] == 1 and row["tool_call_names"] == ["write_file"]
    assert row["tool_arg_chars"] == len(json.loads(raw["tool_calls"])[0]["function"]["arguments"])
    assert json.loads(row["tool_calls"])[0]["function"]["name"] == "write_file"
    assert row["reasoning_chars"] == len("thinking...") and row["active"] is True and row["compacted"] is False
    assert row["ts"] == datetime.fromtimestamp(1786981863.0, tz=timezone.utc)

    tool = hm.message_row({"id": 1, "session_id": "s1", "role": "tool", "tool_name": "mcp__Warehouse__get_live_app",
                           "content": "<untrusted_tool_result>" + "y" * 46000 + "\x00", "timestamp": 1.0,
                           "active": 0, "compacted": 1})
    assert tool["content_chars"] == 23 + 46000 and "\x00" not in tool["content"]
    assert tool["compacted"] is True and tool["active"] is False and len(tool["content_sha256"]) == 64

    session = hm.session_row({
        "id": "calliope_1", "source": "api_server", "model": "calliope",
        "model_config": '{"max_iterations": 500, "max_tokens": 8192}', "system_prompt": "SECRET-ISH LONG PROMPT",
        "system_prompt_hash": "abc", "started_at": 1786979177.6, "last_activity_at": 1786982048.2,
        "message_count": 46, "compression_ineffective_count": 2, "archived": 0, "pinned": 0,
        "handoff_state": None, "extra_future_column": "kept-in-raw",
    })
    assert session["started_at"] == datetime.fromtimestamp(1786979177.6, tz=timezone.utc)
    assert json.loads(session["model_config"]) == {"max_iterations": 500, "max_tokens": 8192}
    assert session["archived"] is False and session["compression_ineffective_count"] == 2
    assert session["store"] == "default" and session["profile_name"] is None
    remainder = json.loads(session["raw"])
    assert remainder == {"handoff_state": None, "extra_future_column": "kept-in-raw"}   # system_prompt never mirrored
    iris = hm.session_row({"id": "calliope_specialist_1", "source": "api_server", "started_at": 1.0}, store="dashboard-studio")
    assert iris["store"] == "dashboard-studio" and iris["profile_name"] == "dashboard-studio"
    assert hm.message_row({"id": 1, "session_id": "s", "role": "user", "timestamp": 1.0}, store="dashboard-studio")["store"] == "dashboard-studio"
    assert hm.usage_row({"session_id": "s", "model": "calliope"}, store="dashboard-studio")["store"] == "dashboard-studio"

    usage = hm.usage_row({"session_id": "s", "model": "calliope", "task": "", "billing_provider": None,
                          "api_call_count": 56, "input_tokens": 1550687, "first_seen": 1786979247.6})
    assert usage["billing_provider"] == "" and usage["task"] == "" and usage["input_tokens"] == 1550687
    assert usage["first_seen"].year == 2026 and usage["last_seen"] is None


class _FakePg:
    """Records SQL; answers the state/route lookups sync_* make."""

    def __init__(self):
        self.executed: list[tuple[str, object]] = []
        self.many: list[tuple[str, list]] = []
        self.state: dict[str, dict] = {}

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def executemany(self, sql, rows):
        self.many.append((sql, list(rows)))

    def fetchone(self):
        sql, params = self.executed[-1]
        if "FROM rvbbit.hermes_mirror_state" in sql:
            return self.state.get(params[0])
        return None

    def fetchall(self):
        return []


def _seed_sqlite(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, model TEXT, model_config TEXT, system_prompt TEXT,
            started_at REAL, last_activity_at REAL, message_count INTEGER, archived INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT,
            tool_name TEXT, timestamp REAL, active INTEGER DEFAULT 1, compacted INTEGER DEFAULT 0);
        CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT DEFAULT '', billing_base_url TEXT DEFAULT '',
            billing_mode TEXT DEFAULT '', task TEXT DEFAULT '', api_call_count INTEGER, input_tokens INTEGER, output_tokens INTEGER,
            cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL,
            cost_status TEXT, cost_source TEXT, first_seen REAL, last_seen REAL);
        INSERT INTO sessions VALUES ('s1','api_server','calliope','{}','PROMPT',100.0,200.0,2,0,0);
        INSERT INTO messages VALUES (1,'s1','user','hello',NULL,NULL,101.0,1,0);
        INSERT INTO messages VALUES (2,'s1','assistant','hi',NULL,NULL,102.0,1,0);
        INSERT INTO session_model_usage VALUES ('s1','calliope','custom','https://x/','','',2,100,20,0,0,0,0,0,NULL,NULL,101.0,102.0);
    """)
    conn.commit()
    return conn


def test_sync_store_backfills_then_only_moves_what_changed(tmp_path):
    db = tmp_path / "state.db"
    src = _seed_sqlite(db)
    ro = hm.open_state_db(db)
    pg = _FakePg()

    stats = hm.sync_store(pg, ro)
    assert stats == {"sessions": 1, "messages": 2, "usage": 1}
    upserts = {sql.split(" (")[0]: rows for sql, rows in pg.many}
    assert "INSERT INTO rvbbit.hermes_sessions" in upserts and "INSERT INTO rvbbit.hermes_messages" in upserts
    session_sql = next(sql for sql, _ in pg.many if "hermes_sessions" in sql)
    assert "%(model_config)s::jsonb" in session_sql and "%(raw)s::jsonb" in session_sql and "ON CONFLICT (id) DO UPDATE" in session_sql
    message_sql = next(sql for sql, _ in pg.many if "hermes_messages" in sql)
    assert "ON CONFLICT (store, id) DO UPDATE" in message_sql            # sqlite ids are per store
    usage_sql = next(sql for sql, _ in pg.many if "hermes_model_usage" in sql)
    assert "ON CONFLICT (session_id, model, billing_provider, billing_base_url, billing_mode, task)" in usage_sql
    saved = [p for s, p in pg.executed if "INSERT INTO rvbbit.hermes_mirror_state" in s][-1]
    assert saved[0] == "state.db" and saved[1] == 2                      # max mirrored message id
    assert json.loads(saved[3])["session_watermark"] == 200.0

    # quiet tick: no new messages; the session is still inside the watermark
    # slack so its (two) mirrored rows are refreshed for flag flips — bounded,
    # not a backfill — and usage is always re-upserted (it is tiny).
    pg.state["state.db"] = {"position": 2, "identity": None, "details": {"session_watermark": 200.0}}
    pg.many.clear()
    stats2 = hm.sync_store(pg, hm.open_state_db(db))
    assert stats2 == {"sessions": 1, "messages": 2, "usage": 1}
    # a session that fell out of the slack is left alone entirely
    pg.state["state.db"] = {"position": 2, "identity": None, "details": {"session_watermark": 5000.0}}
    pg.many.clear()
    assert hm.sync_store(pg, hm.open_state_db(db)) == {"sessions": 0, "messages": 0, "usage": 1}
    pg.state["state.db"] = {"position": 2, "identity": None, "details": {"session_watermark": 200.0}}

    # a compaction flips flags on old rows and adds new ones; the session moves
    src.execute("UPDATE messages SET active=0, compacted=1 WHERE id=1")
    src.execute("INSERT INTO messages VALUES (3,'s1','user','[CONTEXT COMPACTION — REFERENCE ONLY] …',NULL,NULL,300.0,1,0)")
    src.execute("INSERT INTO sessions VALUES ('s2','cron','calliope','{}','P',400.0,400.0,0,0,0)")
    src.execute("UPDATE sessions SET last_activity_at=301.0 WHERE id='s1'")
    src.commit()
    pg.many.clear()
    stats3 = hm.sync_store(pg, hm.open_state_db(db))
    assert stats3["sessions"] == 2 and stats3["messages"] == 3     # 2 refreshed (flags) + 1 new
    rows = next(rows for sql, rows in pg.many if "hermes_messages" in sql)
    by_id = {row["id"]: row for row in rows}
    assert by_id[1]["compacted"] is True and by_id[1]["active"] is False and by_id[3]["content"].startswith("[CONTEXT COMPACTION")
    saved = [p for s, p in pg.executed if "INSERT INTO rvbbit.hermes_mirror_state" in s][-1]
    assert saved[1] == 3 and json.loads(saved[3])["session_watermark"] == 400.0


def test_sync_log_stamps_the_route_and_is_offset_driven(tmp_path):
    log = tmp_path / "agent.log"
    log.write_text("\n".join(LINES[:9]) + "\n", encoding="utf-8")
    pg = _FakePg()
    stats = hm.sync_log(pg, log)
    assert stats == {"lines": 9, "api_calls": 1, "events": 8}
    calls_sql, calls = next((s, r) for s, r in pg.many if "hermes_api_calls" in s)
    assert "ON CONFLICT (line_hash) DO NOTHING" in calls_sql
    # No aliases any more: the stamp is the call's own provider/model, with
    # Hermes' `custom` spelled the way the provider control surface does.
    assert calls[0]["route_provider"] == "clover" and calls[0]["route_model"] == "calliope"
    assert calls[0]["store"] == "default" and "%(store)s" in calls_sql
    events_sql, events = next((s, r) for s, r in pg.many if "hermes_context_events" in s)
    assert "%(details)s::jsonb" in events_sql and json.loads(events[2]["details"])["chars"] == 681
    saved = [p for s, p in pg.executed if "INSERT INTO rvbbit.hermes_mirror_state" in s][-1]
    assert saved[0] == "agent.log" and saved[1] == log.stat().st_size and saved[2]
    # second pass from the saved offset: nothing new
    pg.state["agent.log"] = {"position": saved[1], "identity": saved[2], "details": {}}
    pg.many.clear()
    assert hm.sync_log(pg, log) == {"lines": 0, "api_calls": 0, "events": 0}
    assert pg.many == []


def test_sync_once_walks_the_default_store_and_every_profile(tmp_path):
    """Iris & co. keep their own sqlite + agent.log under profiles/<name>/;
    each becomes a `store` with its own watermarks."""
    root = tmp_path / "data"
    (root / "logs").mkdir(parents=True)
    _seed_sqlite(root / "state.db").close()
    (root / "logs" / "agent.log").write_text(LINES[2] + "\n", encoding="utf-8")
    iris = root / "profiles" / "dashboard-studio"
    (iris / "logs").mkdir(parents=True)
    src = _seed_sqlite(iris / "state.db")
    src.execute("UPDATE sessions SET id='calliope_specialist_iris'")
    src.execute("UPDATE messages SET session_id='calliope_specialist_iris'")
    src.commit(); src.close()
    (iris / "logs" / "agent.log").write_text(LINES[2].replace("calliope_1786979177_b48e012eac", "calliope_specialist_iris") + "\n", encoding="utf-8")
    (root / "profiles" / ".hidden").mkdir()
    (root / "profiles" / "empty-profile").mkdir()

    stores = hm.discover_stores(root / "state.db", root / "logs" / "agent.log", root / "profiles")
    assert [name for name, _, _ in stores] == ["default", "dashboard-studio"]

    pg = _FakePg()
    result = hm.sync_once(lambda: pg, state_db=root / "state.db", agent_log=root / "logs" / "agent.log", profiles_root=root / "profiles")
    assert set(result) == {"default", "dashboard-studio"}
    assert result["dashboard-studio"]["store"]["messages"] == 2 and result["dashboard-studio"]["log"]["api_calls"] == 1
    iris_messages = [rows for sql, rows in pg.many if "hermes_messages" in sql][1]
    assert all(row["store"] == "dashboard-studio" for row in iris_messages)
    iris_sessions = [rows for sql, rows in pg.many if "hermes_sessions" in sql][1]
    assert iris_sessions[0]["profile_name"] == "dashboard-studio" and iris_sessions[0]["id"] == "calliope_specialist_iris"
    saved_keys = [p[0] for s, p in pg.executed if "INSERT INTO rvbbit.hermes_mirror_state" in s]
    assert saved_keys == ["state.db", "agent.log", "state.db:dashboard-studio", "agent.log:dashboard-studio"]


def test_worker_is_wired_and_optional():
    server_src = (_HERE / "server.py").read_text(encoding="utf-8")
    assert server_src.count("    _start_hermes_mirror_worker()\n") == 2   # both the plain and the OAuth build paths
    assert "hermes_mirror.start_worker(_conn)" in server_src
    migration = (_HERE.parents[1] / "crates/pg_rvbbit/sql/migrations/0334_hermes_session_mirror.sql").read_text(encoding="utf-8")
    for table in ("hermes_sessions", "hermes_messages", "hermes_model_usage", "hermes_api_calls",
                  "hermes_context_events", "hermes_mirror_state"):
        assert f"CREATE TABLE IF NOT EXISTS rvbbit.{table}" in migration, table
    for view in ("hermes_session_calls", "hermes_message_ledger", "hermes_session_overview"):
        assert f"CREATE OR REPLACE VIEW rvbbit.{view}" in migration, view
    assert '"0334_hermes_session_mirror"' in (_HERE.parents[1] / "crates/pg_rvbbit/src/migrations.rs").read_text(encoding="utf-8")
    # the image ships the migration and the worker self-applies it (idempotent DDL)
    assert hm._migration_path().is_file()
    dockerfile = (_HERE / "Dockerfile").read_text(encoding="utf-8")
    assert "services/calliope/hermes_mirror.py" in dockerfile and "0334_hermes_session_mirror.sql ./sql/" in dockerfile

    class _Pg:
        def __init__(self): self.sql = []
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def execute(self, sql, params=None): self.sql.append(sql)
    pg = _Pg()
    assert hm.ensure(lambda: pg) is True
    assert any("CREATE TABLE IF NOT EXISTS rvbbit.hermes_messages" in sql for sql in pg.sql)
