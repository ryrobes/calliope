"""Agent-to-agent mailbox (0336): async specialist rounds, queued replies,
delivery as agent_message turns, and the tool surface around them."""

import asyncio
import json
import re
from pathlib import Path

import pytest

import calliope
import hermes_mirror as hm
import server

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _Conn:
    """Route each SQL statement to canned rows by substring; record writes."""

    def __init__(self, routes=None):
        self.routes = routes or []
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return self

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        for needle, rows in self.routes:
            if needle in sql:
                return _Cursor(rows if callable(rows) is False else rows(params))
        return _Cursor([])


# ── delivery mode + tool surface ─────────────────────────────────────────────

def test_delivery_mode_accepts_sync_and_async_spellings_only():
    assert server._specialist_delivery_mode(None) == "sync"
    assert server._specialist_delivery_mode("sync") == "sync"
    assert server._specialist_delivery_mode("Async") == "mailbox"
    assert server._specialist_delivery_mode("background") == "mailbox"
    with pytest.raises(ValueError):
        server._specialist_delivery_mode("later")


def test_delegation_tools_expose_mode_and_new_tools_are_registered_on_the_right_lanes():
    src = (_HERE / "server.py").read_text(encoding="utf-8")
    for tool in ("delegate_calliope_specialist", "delegate_all_calliope_specialists",
                 "continue_calliope_specialist", "await_calliope_specialists", "ask_calliope"):
        assert f'mcp.tool(name="{tool}")' in src, tool
    assert server._mcp_delegate_calliope_specialist.__annotations__["mode"] is str
    assert server._mcp_continue_calliope_specialist.__annotations__["mode"] is str
    assert server._mcp_delegate_all_calliope_specialists.__annotations__["mode"] is str
    # the docstrings teach the model the contract, not just the parameter
    assert 'mode="async"' in server._mcp_delegate_calliope_specialist.__doc__
    assert "[AGENT MESSAGE]" in server._mcp_delegate_calliope_specialist.__doc__
    assert "ASYNC SPECIALIST WORK" in server._INSTRUCTIONS
    assert "await_calliope_specialists once (never in a loop)" in server._INSTRUCTIONS

    import yaml
    config = yaml.safe_load(
        (_REPO / "docker/calliope/hermes-config.yaml").read_text(encoding="utf-8")
    )
    parent = config["mcp_servers"]["Warehouse"]["tools"]["exclude"]
    roundtable = config["mcp_servers"]["Roundtable"]["tools"]["include"]
    # blocking waits ride the long lane; the specialist reply channel is hidden from parents
    assert "await_calliope_specialists" in parent and "await_calliope_specialists" in roundtable
    assert "ask_calliope" in parent and "ask_calliope" not in roundtable
    assert "tools" not in config["mcp_servers"]["Specialist"]


def test_run_calliope_specialist_carries_delivery_into_the_run_row_and_schedules_async():
    src = (_HERE / "server.py").read_text(encoding="utf-8")
    body = src[src.index("async def _run_calliope_specialist("):src.index("async def _mcp_delegate_calliope_specialist(")]
    assert "delivery=\"sync\"" in body
    assert "\"mailbox\" if delivery == \"mailbox\" else \"sync\"" in body
    assert "_schedule_specialist_background_round(run_id, execute)" in body
    assert "return _specialist_queued_payload(run_id)" in body
    # both terminal paths (live + recovery) queue the outcome, idempotently
    assert body.count("_queue_specialist_round_outcome, run_id") == 3
    recovery = src[src.index("async def _recover_specialist_execution("):src.index("def _start_specialist_recovery_worker(")]
    assert recovery.count("_queue_specialist_round_outcome, run_id") == 2
    # async rounds are told about ask_calliope; sync rounds are not
    assert "call ask_calliope" in body and 'if delivery == "mailbox" else ""' in body


# ── the reply that goes into the mailbox ────────────────────────────────────

def _run_row(**overrides):
    from datetime import datetime, timedelta, timezone
    started = datetime(2026, 8, 18, 5, 0, tzinfo=timezone.utc)
    row = {
        "id": "11111111-1111-4111-8111-111111111111",
        "thread_id": "22222222-2222-4222-8222-222222222222",
        "round_no": 2,
        "status": "complete",
        "task": "Review v3 of boston-ibr for hierarchy and mobile; keep the 8/4 lead. " * 40,
        "result": "Keep the composition; tighten the district table; mobile clips at 390px.",
        "error": None,
        "started_at": started,
        "completed_at": started + timedelta(seconds=95),
        "display_name": "Iris",
        "specialist_key": "dashboard-studio",
        "profile_name": "dashboard-studio",
        "owner_email": "ryan@example.com",
        "source_session_id": "33333333-3333-4333-8333-333333333333",
        "delivery": "mailbox",
    }
    row.update(overrides)
    return row


def test_specialist_round_message_carries_the_question_it_answers(monkeypatch):
    surfaces = [{"id": "s1", "kind": "artifact", "title": "Boston IBR v4", "artifact_slug": "boston-ibr", "artifact_version": 4}]
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn([("FROM rvbbit.calliope_surfaces", surfaces)]))
    subject, body, payload, kind = server._specialist_round_message(_run_row())
    assert kind == "reply" and subject == "Reply from Iris · round 2"
    assert "Iris finished round 2 in 1m 35s." in body
    assert "You asked Iris (async, thread 22222222-2222-4222-8222-222222222222, round 2):" in body
    assert body.count("Review v3 of boston-ibr") >= 1 and "…" in body  # bounded excerpt
    assert "Iris replied:\nKeep the composition" in body
    assert "- artifact: Boston IBR v4 (boston-ibr v4)" in body
    assert payload["stage_surfaces"][0]["artifact_slug"] == "boston-ibr"
    assert payload["specialist"] == {"key": "dashboard-studio", "name": "Iris", "profile": "dashboard-studio"}

    subject, body, _payload, kind = server._specialist_round_message(
        _run_row(status="failed", error="RuntimeError: xAI refresh token revoked", result=None)
    )
    assert kind == "failed" and "could not finish" in subject
    assert "failed on a auth error" in body or "failed on an auth error" in body
    assert "Recorded error: RuntimeError: xAI refresh token revoked" in body


def test_queue_specialist_round_outcome_is_mailbox_only_terminal_only_and_idempotent(monkeypatch):
    inserted = []

    def conn_factory(*a, **k):
        def insert(params):
            inserted.append(params)
            return [{"id": "m-1"}]
        return _Conn([
            ("FROM rvbbit.calliope_specialist_runs r", [state["run"]]),
            ("FROM rvbbit.calliope_agent_messages WHERE run_id", state.get("existing", [])),
            ("FROM rvbbit.calliope_surfaces", []),
            ("INSERT INTO rvbbit.calliope_agent_messages", insert),
        ])

    monkeypatch.setattr(server, "_conn", conn_factory)
    state = {"run": _run_row(delivery="sync")}
    assert server._queue_specialist_round_outcome("r") is None and not inserted
    state = {"run": _run_row(status="running")}
    assert server._queue_specialist_round_outcome("r") is None and not inserted
    state = {"run": _run_row()}
    assert server._queue_specialist_round_outcome("r") == "m-1"
    assert len(inserted) == 1
    owner, kind, from_kind, from_ref, from_label, to_session, thread_id, run_id, *_rest = inserted[0]
    assert (owner, kind, from_kind, from_ref, from_label) == ("ryan@example.com", "reply", "specialist", "dashboard-studio", "Iris")
    assert to_session == "33333333-3333-4333-8333-333333333333"
    state = {"run": _run_row(), "existing": [{"id": "m-0"}]}
    assert server._queue_specialist_round_outcome("r") == "m-0" and len(inserted) == 1


def test_queued_payload_tells_the_model_how_the_reply_arrives(monkeypatch):
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn([
        ("FROM rvbbit.calliope_specialist_runs r", [{
            "id": "r1", "thread_id": "t1", "round_no": 1, "status": "running", "delivery": "mailbox",
            "task": "x", "started_at": None, "specialist_key": "dashboard-studio",
            "display_name": "Iris", "profile_name": "dashboard-studio",
        }]),
    ]))
    payload = server._specialist_queued_payload("r1")
    assert payload["status"] == "queued" and payload["mode"] == "async"
    assert payload["thread_id"] == "t1" and payload["specialist"]["name"] == "Iris"
    assert "[AGENT MESSAGE]" in payload["next"] and "do not\npoll" not in payload["next"]
    assert "await_calliope_specialists" in payload["next"]


# ── ask_calliope ─────────────────────────────────────────────────────────────

def test_ask_calliope_queues_a_question_only_from_an_async_specialist_round(monkeypatch):
    inserted = []
    thread = {
        "thread_id": "22222222-2222-4222-8222-222222222222", "owner_email": "ryan@example.com",
        "source_session_id": "33333333-3333-4333-8333-333333333333", "objective": "review",
        "specialist_key": "dashboard-studio", "display_name": "Iris", "profile_name": "dashboard-studio",
        "run_id": "11111111-1111-4111-8111-111111111111", "round_no": 1, "delivery": "mailbox",
        "run_status": "running", "task": "Review v3; decide the mobile breakpoint",
    }
    monkeypatch.setattr(server, "_specialist_thread_for_envelope", lambda ctx: dict(thread))

    def insert(params):
        inserted.append(params)
        return [{"id": "q-1"}]

    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn([
        ("INSERT INTO rvbbit.calliope_agent_messages", insert),
    ]))
    monkeypatch.setattr(server, "_logged_async", lambda name, args, thunk: thunk())
    result = asyncio.run(server._mcp_ask_calliope(
        "Should the mobile view drop the map or stack it?", ["drop", "stack"]
    ))
    assert result["status"] == "queued" and result["delivered"] is True
    assert result["message_id"] == "q-1"
    body = inserted[0][10]
    assert "Iris asks:\nShould the mobile view drop the map or stack it?" in body
    assert "- drop\n- stack" in body
    assert 'continue_calliope_specialist(thread_id="22222222-2222-4222-8222-222222222222"' in body
    assert "You asked Iris (async):\n> Review v3; decide the mobile breakpoint" in body

    progress = []
    monkeypatch.setattr(server, "_append_specialist_message", lambda *args: progress.append(args))
    inserted.clear()
    result = asyncio.run(server._mcp_ask_calliope(
        "Revise the supplied query?", decision_to_reopen="One row per state",
        contradicting_evidence="Query Q returns duplicate state keys"))
    assert result["delivered"] is True
    assert "Settled decision questioned:\nOne row per state" in inserted[0][10]
    assert json.loads(progress[0][-1])["scope_deviation"]["evidence"] == "Query Q returns duplicate state keys"

    thread["delivery"] = "sync"
    inserted.clear()
    result = asyncio.run(server._mcp_ask_calliope("Which framing?"))
    assert result["status"] == "sync_round" and result["delivered"] is False and not inserted

    monkeypatch.setattr(server, "_specialist_thread_for_envelope", lambda ctx: None)
    with pytest.raises(PermissionError):
        asyncio.run(server._mcp_ask_calliope("Which framing?"))


# ── delivery as an agent_message turn ────────────────────────────────────────

def _messages():
    return [
        {"id": "m1", "kind": "reply", "from_ref": "dashboard-studio", "from_label": "Iris",
         "thread_id": "22222222-2222-4222-8222-222222222222", "to_session_id": "s",
         "body": "Iris finished round 2 in 1m 35s.\n\nYou asked Iris (async…):\n> Review v3\n\nIris replied:\nKeep the composition."},
        {"id": "m2", "kind": "question", "from_ref": "analytical-skeptic", "from_label": "Cassandra",
         "thread_id": None, "to_session_id": "s", "body": "Cassandra asks:\nIs 282,517 distinct incidents or rows?"},
    ]


def test_delivered_turn_text_round_trips_into_agent_cards():
    text = calliope._agent_mailbox_turn_text(_messages())
    assert text.startswith("[AGENT MESSAGE — reply from Iris · thread 22222222-2222-4222-8222-222222222222]\n")
    assert "\n[/AGENT MESSAGE]\n\n[AGENT MESSAGE — question from Cassandra]\n" in text
    blocks = calliope.parse_agent_message_blocks(text)
    assert [(b["kind"], b["label"], b["thread_id"]) for b in blocks] == [
        ("reply", "Iris", "22222222-2222-4222-8222-222222222222"),
        ("question", "Cassandra", None),
    ]
    assert blocks[0]["body"].startswith("Iris finished round 2") and blocks[0]["body"].endswith("Keep the composition.")
    assert blocks[1]["body"] == "Cassandra asks:\nIs 282,517 distinct incidents or rows?"
    # the browser mirrors the same header grammar
    js = (_HERE / "calliope/calliope.js").read_text(encoding="utf-8")
    assert "function parseAgentMessageBlocks" in js and "AGENT MESSAGE — " in js
    assert '"agent_message"' in js and "function agentMessageCardsMarkup" in js
    assert "function renderAgentWork" in js and 'id="agent-work"' in (_HERE / "calliope/index.html").read_text(encoding="utf-8")


def test_delivery_prompt_context_tells_calliope_the_human_did_not_type_it():
    context = calliope._agent_mailbox_prompt_context(_messages())
    assert context.startswith("[CALLIOPE AGENT DELIVERY — internal]")
    assert "deliver 2 agent messages (question, reply)" in context
    assert "the human did not type it" in context
    assert "continue_calliope_specialist" in context and "request_calliope_input" in context


def test_run_turn_accepts_a_delivered_principal_and_agent_message_kind_only_from_the_worker():
    src = (_HERE / "calliope.py").read_text(encoding="utf-8")
    assert "async def run_turn(request, *, delivered=None):" in src
    assert '{"agent_message"} if delivered else set()' in src
    assert "agent_delivery_context" in src
    assert '"kind": "agent"' in src  # turn.started author for delivered turns
    # the worker runs delivery on the serving loop where browsers' brokers live
    assert "asyncio.run_coroutine_threadsafe(" in src
    assert "deliver_agent_messages(batch" in src
    assert "capture_server_loop" in (_HERE / "server.py").read_text(encoding="utf-8")
    # only idle sessions receive a delivery; the claim is per session, oldest first
    assert "NOT EXISTS (SELECT 1 FROM rvbbit.calliope_turns t" in src
    assert "FOR UPDATE OF m SKIP LOCKED LIMIT 1" in src


def test_claim_batch_takes_every_queued_message_for_one_idle_session():
    executed = []

    class Conn(_Conn):
        def execute(self, sql, params=None):
            executed.append((sql, params))
            if "FOR UPDATE OF m SKIP LOCKED LIMIT 1" in sql:
                return _Cursor([{"id": "m1", "to_session_id": "s-1", "owner_email": "test@example.com", "human_answer": False}])
            if "SET status='delivering'" in sql:
                return _Cursor([{"id": "m1", "to_session_id": "s-1"}, {"id": "m2", "to_session_id": "s-1"}])
            return _Cursor([])

    batch = calliope._claim_agent_mailbox_batch(lambda: Conn())
    assert [m["id"] for m in batch] == ["m1", "m2"]
    update_sql, params = executed[-1]
    assert params == ("s-1", "test@example.com", calliope._AGENT_MAILBOX_BATCH)
    assert "attempts=attempts+1" in update_sql


def test_requeue_fails_after_the_attempt_budget_and_delivered_marks_the_turn():
    conn = _Conn()
    calliope._requeue_agent_messages(lambda: conn, ["m1"], "TURN_IN_PROGRESS")
    sql, params = conn.executed[-1]
    assert "status=CASE WHEN attempts>=%s THEN 'failed' ELSE 'queued' END" in sql
    assert params[0] == calliope._AGENT_MAILBOX_MAX_ATTEMPTS and params[2] == "TURN_IN_PROGRESS"
    calliope._mark_agent_messages_delivered(lambda: conn, ["m1"], "turn-9")
    sql, params = conn.executed[-1]
    assert "status='delivered'" in sql and params == ("turn-9", ["m1"])


def test_session_agent_work_lists_async_rounds_and_queued_messages():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    conn = _Conn([
        ("SELECT transaction_timestamp()", [{"observed_at": now}]),
        ("AND r.status IN ('queued','running')", [{
            "run_id": "r1", "thread_id": "t1", "round_no": 1, "status": "running",
            "specialist_key": "dashboard-studio", "display_name": "Iris", "started_at": now,
        }]),
        ("SELECT count(*) FILTER", [{"n": 2}]),
    ])
    work = calliope._session_agent_work(lambda: conn, "s")
    assert work["queued_messages"] == 2
    assert work["pending"][0]["specialist_name"] == "Iris" and work["pending"][0]["thread_id"] == "t1"
    assert work["activity_state"]["phase"] == "team_working"
    assert work["activity_state"]["outstanding"] == 3
    sql = next(sql for sql, _ in conn.executed if "AND r.status IN ('queued','running')" in sql)
    assert "r.delivery='mailbox'" not in sql
    assert "r.status IN ('queued','running')" in sql

    class Broken(_Conn):
        def execute(self, sql, params=None):
            raise RuntimeError("relation does not exist")

    failed = calliope._session_agent_work(lambda: Broken(), "s")
    assert failed["available"] is False
    assert failed["activity_state"]["phase"] == "unknown"
    assert "pending" not in failed  # no fabricated zero-work receipt


def test_session_activity_tracks_completion_through_reply_delivery():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    work = {"pending": [{"run_id": "r", "status": "running", "heartbeat_at": now}],
            "queued_messages": 0, "delivering_messages": 0, "followups": []}
    assert calliope._session_activity_state(work, now)["phase"] == "team_working"
    work["pending"] = []
    work["pending_publications"] = [{"run_id": "r", "completed_at": now}]
    assert calliope._session_activity_state(work, now)["phase"] == "delivering"
    work["pending_publications"] = []
    work["queued_messages"] = 1
    assert calliope._session_activity_state(work, now)["phase"] == "waiting"
    work["queued_messages"] = 0
    work["delivering_messages"] = 1
    assert calliope._session_activity_state(work, now)["phase"] == "delivering"
    work["parent_turns"] = [{"turn_id": "p", "turn_kind": "agent_message", "heartbeat_at": now}]
    work["delivering_messages"] = 0
    assert calliope._session_activity_state(work, now)["phase"] == "responding"
    work["parent_turns"] = []
    assert calliope._session_activity_state(work, now) == {"phase": "ready", "outstanding": 0, "stale_run_ids": []}


def test_session_activity_keeps_followups_and_stale_workers_unfinished():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    work = {"followups": [{"count": 2}]}
    assert calliope._session_activity_state(work, now)["phase"] == "waiting"
    run = {"run_id": "r", "status": "running", "heartbeat_at": now - timedelta(minutes=2)}
    work["pending"] = [run]
    state = calliope._session_activity_state(work, now)
    assert state["phase"] == "unknown" and state["stale_run_ids"] == ["r"]
    # A polling response never refreshes the worker heartbeat. A valid startup
    # lease does cover the period before its first heartbeat, though.
    run["lease_expires_at"] = now + timedelta(minutes=3)
    assert calliope._session_activity_state(work, now)["phase"] == "team_working"
    work = {"parent_turns": [{"turn_id": "p", "turn_kind": "chat", "heartbeat_at": now}]}
    assert calliope._session_activity_state(work, now)["phase"] == "working"
    work = {"pending_publications": [{"run_id": "r", "completed_at": now - timedelta(minutes=2)}]}
    assert calliope._session_activity_state(work, now)["phase"] == "unknown"


# ── schema + wiring ─────────────────────────────────────────────────────────

def test_migration_0336_is_registered_shipped_and_applied_at_boot():
    migration = (_REPO / "crates/pg_rvbbit/sql/migrations/0336_calliope_agent_messages.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS rvbbit.calliope_agent_messages" in migration
    assert "ADD COLUMN IF NOT EXISTS delivery text NOT NULL DEFAULT 'sync'" in migration
    assert "CHECK (kind IN ('reply', 'question', 'progress', 'note', 'failed', 'cancel'))" in migration
    assert "DROP VIEW IF EXISTS rvbbit.calliope_run_scorecard;" in migration
    for column in ("agent_turns", "human_turns", "agent_messages", "agent_questions", "async_specialist_rounds"):
        assert f"AS {column}" in migration, column
    # 0335 must survive re-application after 0336 recreated the view with more columns
    assert "DROP VIEW IF EXISTS rvbbit.calliope_run_scorecard;" in (
        _REPO / "crates/pg_rvbbit/sql/migrations/0335_calliope_run_scorecard.sql"
    ).read_text(encoding="utf-8")
    assert '"0336_calliope_agent_messages"' in (_REPO / "crates/pg_rvbbit/src/migrations.rs").read_text(encoding="utf-8")
    assert "0336_calliope_agent_messages.sql ./sql/" in (_HERE / "Dockerfile").read_text(encoding="utf-8")
    assert hm._migration_path(hm._MAILBOX_MIGRATION_NAME).is_file()

    class _Pg:
        def __init__(self): self.sql = []
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def execute(self, sql, params=None): self.sql.append(sql)
    pg = _Pg()
    assert hm.ensure(lambda: pg) is True
    assert any("calliope_agent_messages" in sql for sql in pg.sql)
    assert any("calliope_run_notes" in sql for sql in pg.sql)
    pg = _Pg()
    assert hm.ensure_agent_mailbox(pg) is True and any("calliope_agent_messages" in sql for sql in pg.sql)
    assert "hermes_mirror.ensure_agent_mailbox(conn)" in (_HERE / "calliope.py").read_text(encoding="utf-8")


def test_boot_copies_step_aside_once_the_extension_recorded_them():
    # A later migration can widen a view this boot copy creates; re-running it
    # then fails ("cannot drop columns from view") and only adds log noise.
    import hermes_mirror as hm

    class _Result:
        def __init__(self, row): self.row = row
        def fetchone(self): return self.row

    class _Pg:
        def __init__(self, recorded): self.recorded, self.sql = recorded, []
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def execute(self, sql, params=None):
            if "to_regclass('rvbbit.schema_migrations')" in sql:
                return _Result({"ready": True})
            if "FROM rvbbit.schema_migrations" in sql:
                return _Result({"found": 1} if params[0] in self.recorded else None)
            self.sql.append(sql)

    pg = _Pg({"0334_hermes_session_mirror", "0335_calliope_run_scorecard",
              "0336_calliope_agent_messages", "0379_hermes_cron_mirror"})
    assert hm.ensure(lambda: pg) is True and pg.sql == []
    assert hm.ensure_agent_mailbox(pg) is True and pg.sql == []
    pg = _Pg({"0334_hermes_session_mirror"})
    hm.ensure(lambda: pg)
    assert not any(sql.startswith("-- 0334:") for sql in pg.sql)
    assert any(sql.startswith("-- 0335:") for sql in pg.sql)
    assert any("calliope_agent_messages" in sql for sql in pg.sql)


# ── failure-mode hardening (run 11, 2026-08-19) ─────────────────────────────

def test_provider_failure_classifier_names_the_cause():
    assert calliope.classify_provider_failure(
        "RuntimeError: HTTP 402: This request requires more credits, or fewer max_tokens."
    ) == "billing"
    assert calliope.classify_provider_failure("[Errno 32] Broken pipe") == "transport"
    assert calliope.classify_provider_failure("ReadError: peer closed connection") == "transport"
    assert calliope.classify_provider_failure("Error code: 429 - rate limit") == "rate_limit"
    assert calliope.classify_provider_failure("xAI refresh token revoked (401)") == "auth"
    assert calliope.classify_provider_failure(
        'Error code: 400 - INVALID_ARGUMENT: The referenced name `#/$defs/_SketchStyleInput`…'
    ) == "request_rejected"
    assert calliope.classify_provider_failure("") == "unknown"
    # advice: billing forbids a same-provider retry; transport invites one
    assert "do not" in calliope.provider_failure_advice("billing")
    assert "retry after a short wait" in calliope.provider_failure_advice("transport")


def test_failed_round_mail_carries_the_classified_cause(monkeypatch):
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn([("FROM rvbbit.calliope_surfaces", [])]))
    subject, body, payload, kind = server._specialist_round_message(_run_row(
        status="failed", result=None,
        error="RuntimeError: HTTP 402: This request requires more credits, or fewer max_tokens.",
    ))
    assert kind == "failed"
    assert "failed on a billing error" in body
    assert "Cause: billing — the provider refused for billing/credit reasons" in body
    assert "do not" in body and "Recorded error: RuntimeError: HTTP 402" in body
    assert payload["failure"] == {"category": "billing"}


def test_transport_failed_turn_queues_a_capped_delayed_resume_note():
    inserted = []
    state = {"recent": 0}

    def conn_factory(*a, **k):
        def insert(params):
            inserted.append(params)
            return [{"id": "n-1"}]
        return _Conn([
            ("FROM rvbbit.calliope_turns t JOIN rvbbit.calliope_sessions s", [{
                "session_id": "33333333-3333-4333-8333-333333333333",
                "turn_kind": "chat", "owner_email": "ryan@example.com", "archived": False,
            }]),
            ("payload->>'auto_resume'='true'", [{"n": state["recent"]}]),
            ("INSERT INTO rvbbit.calliope_agent_messages", insert),
        ])

    # billing does NOT auto-resume (retrying a spent provider just re-fails)
    assert calliope._queue_transport_resume_note(conn_factory, "t1", "HTTP 402: more credits") is None
    # transport does, with a backoff and a cause-aware body
    assert calliope._queue_transport_resume_note(conn_factory, "t1", "[Errno 32] Broken pipe") == "n-1"
    params = inserted[-1]
    assert params[2] == "Resume after a provider interruption"
    assert "connection to the provider broke mid-stream" in params[3]
    assert "re-dispatch any" in params[3]
    assert json.loads(params[4])["auto_resume"] is True
    assert params[5] == calliope._AUTO_RESUME_BACKOFF_SECONDS
    # capped per session per hour
    state["recent"] = calliope._AUTO_RESUME_MAX_PER_HOUR
    assert calliope._queue_transport_resume_note(conn_factory, "t1", "Broken pipe") is None
    # _complete_turn(failed) is the hook
    src = (_HERE / "calliope.py").read_text(encoding="utf-8")
    assert '_queue_transport_resume_note(conn_factory, turn_id, error)' in src
    # and the worker cancels a note superseded by a completed turn
    assert "superseded by a completed turn" in src


def test_tool_schemas_are_self_contained_for_gemini():
    schema = {
        "$defs": {
            "_SketchStyleInput": {"type": "object", "properties": {"stroke": {"type": "string"}}},
            "_SketchAddText": {
                "type": "object",
                "properties": {"style": {"$ref": "#/$defs/_SketchStyleInput"}},
            },
        },
        "properties": {
            "operations": {"type": "array", "items": {"$ref": "#/$defs/_SketchAddText"}},
            "style": {"$ref": "#/$defs/_SketchStyleInput", "description": "optional style"},
        },
    }
    inlined = server._inline_schema_defs(schema)
    assert "$defs" not in inlined and "$ref" not in json.dumps(inlined)
    items = inlined["properties"]["operations"]["items"]
    assert items["properties"]["style"]["properties"]["stroke"] == {"type": "string"}
    # $ref siblings survive the merge
    assert inlined["properties"]["style"]["description"] == "optional style"
    # cyclic definitions keep their ref rather than recursing forever
    cyclic = {"$defs": {"Node": {"properties": {"next": {"$ref": "#/$defs/Node"}}}},
              "properties": {"root": {"$ref": "#/$defs/Node"}}}
    kept = server._inline_schema_defs(cyclic)
    assert "$defs" in kept  # still referenced, so definitions remain
    # and the live registry publishes no $defs at all (the actual regression:
    # sketch-tool schemas in tool_describe results poisoned Gemini sessions)
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("gemini-safe")
    server._register(mcp)
    offenders = [
        name for name, tool in mcp._tool_manager._tools.items()
        if "$defs" in json.dumps(getattr(tool, "parameters", {}) or {})
    ]
    assert offenders == [], offenders
    assert "update_calliope_sketch" in mcp._tool_manager._tools


def test_provider_bench_is_surfaced_and_clearable():
    supervisor = (_REPO / "docker/calliope/hermes-gateway-supervisor.py").read_text(encoding="utf-8")
    assert "def _provider_bench(" in supervisor
    assert "def clear_provider_bench(" in supervisor
    assert '"/v1/providers/bench/clear"' in supervisor
    # the clear preserves the gateway user's ownership of auth.json (the
    # root-exec trap: a root-owned auth.json = "No LLM provider configured")
    assert "os.chown(auth_path" in supervisor
    import provider_settings
    assert callable(provider_settings.clear_provider_bench)
    with pytest.raises(ValueError):
        provider_settings.clear_provider_bench(lambda: _Conn(), "not-a-provider", actor="t")
    calliope_src = (_HERE / "calliope.py").read_text(encoding="utf-8")
    assert "/api/calliope/admin/providers/bench/clear" in calliope_src
    js = (_HERE / "calliope/calliope.js").read_text(encoding="utf-8")
    assert "data-provider-bench-clear" in js and "Retry now" in js
    skill = (_REPO / "docker/calliope/hermes-skills/calliope-live-dashboards/SKILL.md").read_text(encoding="utf-8")
    assert "bounded pieces" in skill


def test_compact_result_pages_reply_without_duplicating_ui_history():
    reply = 'evidence ' * 5000 + 'FINAL DECISION'
    result = server._compact_specialist_fanout_result({
        'run_id': 'saved-run', 'response': reply, 'conversation': [{'role': 'specialist', 'content': reply}],
    })
    assert result['response'] == reply[:8000]
    assert result['response_truncated'] is True
    assert result['read_more']['arguments'] == {'run_id': 'saved-run', 'offset': 8000}
    assert 'conversation' not in result and 'transcript' not in result


def test_specialist_mailbox_can_preserve_long_reply():
    conn = _Conn([('RETURNING id', [{'id': 'message-id'}])])
    body = 'x' * 32000 + 'END OF REPLY'
    server._queue_agent_message(conn, owner_email='test@example.com', to_session_id='session',
        kind='reply', from_kind='specialist', from_ref='iris', from_label='Iris',
        subject='Reply', body=body, body_limit=None)
    assert conn.executed[0][1][10] == body


@pytest.mark.parametrize('finished', [False, True])
def test_specialist_wait_returns_before_transport_deadline(monkeypatch, finished):
    from types import SimpleNamespace
    run_id = '11111111-1111-4111-8111-111111111111'
    ticks = [0.0]
    conn = _Conn([
        ('SELECT r.id,r.thread_id', [{'id': run_id, 'thread_id': run_id, 'round_no': 1}]),
        ('SELECT id,status', [{'id': run_id, 'status': 'complete' if finished else 'running'}]),
    ])
    monkeypatch.setattr(server, '_conn', lambda: conn)
    monkeypatch.setattr(server, '_application_authorization_context', lambda ctx: SimpleNamespace(subject='test@example.com'))
    monkeypatch.setattr(server, '_hermes_mcp_envelope', lambda ctx: {'platform': 'api_server', 'session_id': 'parent'})
    monkeypatch.setattr(server, 'time', SimpleNamespace(monotonic=lambda: ticks[0]))
    async def sleep(seconds): ticks[0] += seconds
    # Avoid changing the global asyncio module shared by the event loop.
    monkeypatch.setattr(server, 'asyncio', SimpleNamespace(sleep=sleep, to_thread=asyncio.to_thread))
    async def logged(name, args, fn): return await fn()
    monkeypatch.setattr(server, '_logged_async', logged)
    reply = 'verified conclusion ' * 1000
    monkeypatch.setattr(server, '_specialist_result_payload', lambda rid: {'run_id': rid, 'response': reply, 'status': 'complete'})
    acks = []
    monkeypatch.setattr(server, '_acknowledge_awaited_specialist', lambda *args: acks.append(args) or True)
    result = asyncio.run(server._mcp_await_calliope_specialists(run_ids=[run_id], timeout_seconds=1800))
    assert ticks[0] <= 300
    if finished:
        assert result['results'][0]['response'] == reply[:8000] and len(acks) == 1
        assert result['results'][0]['read_more']['arguments']['run_id'] == run_id
    else:
        assert result['status'] == 'partial' and result['waited_seconds'] == 300
        assert result['results'][0]['status'] == 'running' and not acks
