"""Persistent Hermes specialist routing and Stage observability contracts."""
from __future__ import annotations

import json
import re
import runpy
import sqlite3
import sys
from pathlib import Path

import yaml


_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import calliope  # noqa: E402
import hermes_profiles  # noqa: E402
import server  # noqa: E402
import specialist_cron_reconcile  # noqa: E402


def test_agent_avatar_urls_are_profile_owned_and_path_bounded():
    assert calliope._agent_avatar_url("iris.png") == "/calliope/specialists/iris.png"
    assert calliope._agent_avatar_url("generated-order-desk.png") == (
        "/calliope/specialists/generated-order-desk.png"
    )
    assert calliope._agent_avatar_url("../iris.png") is None
    assert calliope._agent_avatar_url("iris.svg") is None
    assert calliope._agent_avatar_url("generated-../iris.png") is None


def test_profile_identity_reconciler_upgrades_only_the_legacy_generated_stub(tmp_path):
    repo = _HERE.parents[1]
    module = runpy.run_path(
        str(repo / "docker/calliope/reconcile-hermes-specialists.py")
    )
    source = tmp_path / "source.yaml"
    target = tmp_path / "profile.yaml"
    marker = tmp_path / ".managed" / "profile.sha256"
    source.write_text(
        "name: Iris\nrole_label: Dashboard Studio\ndescription: Builds dashboards.\n",
        encoding="utf-8",
    )
    target.write_text(
        "description: Legacy generated stub.\ndescription_auto: false\n",
        encoding="utf-8",
    )
    reconcile = module["_reconcile_profile_identity"]
    assert reconcile(source, target, marker) == "upgraded-legacy"
    assert "name: Iris" in target.read_text(encoding="utf-8")

    target.write_text(
        "name: Custom Iris\nrole_label: Studio\ndescription: Operator-owned.\n",
        encoding="utf-8",
    )
    assert reconcile(source, target, marker) == "preserved"
    assert "Custom Iris" in target.read_text(encoding="utf-8")


def test_agent_skills_app_exposes_profile_identity_separately_from_runtime_yaml():
    repo = _HERE.parents[1]
    source = (
        repo / "datarabbit/src/components/desktop/agent-skills-window.tsx"
    ).read_text(encoding="utf-8")
    supervisor = (
        repo / "docker/calliope/hermes-gateway-supervisor.py"
    ).read_text(encoding="utf-8")
    assert 'label="Identity YAML"' in source
    assert 'label="Runtime YAML"' in source
    assert '"profile": "profile.yaml"' in supervisor
    assert '"restart_required": kind != "profile"' in supervisor


def test_agent_skills_app_preserves_mode_and_prioritizes_custom_skills():
    repo = _HERE.parents[1]
    source = (
        repo / "datarabbit/src/components/desktop/agent-skills-window.tsx"
    ).read_text(encoding="utf-8")
    supervisor = (
        repo / "docker/calliope/hermes-gateway-supervisor.py"
    ).read_text(encoding="utf-8")

    profile_click = source.split("key={profile.key}", 1)[1].split("className={cn", 1)[0]
    assert 'setMode("skills")' not in profile_click
    assert 'VIEW_MODE_STORAGE_KEY = "rvbbit.agent-skills.view-mode"' in source
    assert "setSelectedSkill(null)" in profile_click
    assert "skillOriginRank(left.origin) - skillOriginRank(right.origin)" in source
    assert 'origin === "custom" ? 0' in source
    assert 'origin === "empty" ? -1' in source          # a hollow copy sorts first, as a defect
    assert '>empty</span>' in source
    assert '>local</span>' in source
    assert "Created or changed here" in source
    assert '>Built in</span>' in source
    assert '"origin": origin' in supervisor
    assert '"is_custom": origin not in {"builtin", "empty"}' in supervisor
    assert 'origin = "empty"' in supervisor              # a zero-byte SKILL.md is a defect, not a customisation
    assert "@lru_cache(maxsize=1)" in supervisor


def test_specialist_skill_toggle_retries_a_transient_profile_write_conflict(monkeypatch):
    calls = []

    def fake_control(method, path, payload, *, timeout):
        calls.append((method, path, payload, timeout))
        if len(calls) == 1:
            return {
                "ok": False,
                "phase": "compare_and_swap",
                "error": "profile config changed during the update",
                "http_status": 409,
            }
        return {"ok": True, "profile": "dashboard-studio", "enabled": False}

    monkeypatch.setattr(hermes_profiles, "_control", fake_control)
    result = hermes_profiles.toggle_skill(
        lambda: None,
        "dashboard-studio",
        "calliope-live-dashboards",
        False,
        actor="admin@example.test",
    )

    assert result["ok"] is True
    assert len(calls) == 2
    assert calls[0][2] == calls[1][2]


def test_agent_skills_errors_surface_validation_details_instead_of_bare_http_status():
    repo = _HERE.parents[1]
    source = (
        repo / "datarabbit/src/components/desktop/agent-skills-window.tsx"
    ).read_text(encoding="utf-8")
    assert "validation?.errors?.[0]" in source
    assert "issue.path" in source
    assert "issueMessage" in source


def test_profile_cron_executions_project_to_bounded_scheduled_run_receipts(tmp_path):
    profile = "knowledge-gardener"
    job_id = "garden123"
    cron = tmp_path / "profiles" / profile / "cron"
    output = cron / "output" / job_id / "2026-08-14_13-46-15.md"
    output.parent.mkdir(parents=True)
    output.write_text(
        "# Cron Job\n\n## Response\n\nRefreshed the semantic garden.\n",
        encoding="utf-8",
    )
    (cron / "jobs.json").write_text(
        json.dumps({"jobs": [{
            "id": job_id,
            "name": "Calliope knowledge gardening",
            "prompt": "Inspect and maintain durable business knowledge.",
            "schedule": "53 4 * * *",
        }]}),
        encoding="utf-8",
    )
    (cron / "usage_audit.jsonl").write_text(
        json.dumps({
            "ts": "2026-08-14T13:46:15.198Z",
            "job_id": job_id,
            "fire_id": "fire-1",
            "model": "calliope",
            "prompt_tokens": 720,
            "completion_tokens": 90,
            "total_tokens": 810,
            "duration_ms": 126433,
        }) + "\n",
        encoding="utf-8",
    )
    with sqlite3.connect(cron / "executions.db") as connection:
        connection.execute(
            "CREATE TABLE executions (id TEXT PRIMARY KEY,job_id TEXT,status TEXT,"
            "source TEXT,process_id TEXT,pid INTEGER,process_started_at INTEGER,"
            "claimed_at TEXT,started_at TEXT,finished_at TEXT,error TEXT,"
            "output_path TEXT,delivery_outcome TEXT,hermes_session_id TEXT)"
        )
        connection.execute(
            "INSERT INTO executions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "execution-1", job_id, "completed", "scheduler", "process-1", 42,
                100, "2026-08-14T13:44:08.386605+00:00",
                "2026-08-14T13:44:08.500000+00:00",
                "2026-08-14T13:46:15.300000+00:00", None, str(output), "suppressed",
                "cron_exact_signal_session",
            ),
        )

    specialists = [{
        "id": "11bff5e7-e44a-4e45-beb9-2a44dac04268",
        "specialist_key": profile,
        "profile_name": profile,
    }]
    first = specialist_cron_reconcile.scan(tmp_path, specialists)
    second = specialist_cron_reconcile.scan(tmp_path, specialists)

    assert first == second
    assert len(first) == 1
    receipt = first[0]
    assert receipt["status"] == "complete"
    assert receipt["hermes_session_id"] == "cron_exact_signal_session"
    assert receipt["result"] == "Refreshed the semantic garden."
    assert receipt["usage"]["total_tokens"] == 810
    assert receipt["runtime"]["delivery_outcome"] == "suppressed"
    assert receipt["runtime"]["ledger"] == "profile_local"


def test_legacy_shared_cron_ledger_is_reconciled_without_overriding_profile_local(tmp_path):
    specialist = {
        "id": "11bff5e7-e44a-4e45-beb9-2a44dac04268",
        "specialist_key": "signal-watch",
        "profile_name": "signal-watch",
    }
    cron = tmp_path / "profiles" / "signal-watch" / "cron"
    cron.mkdir(parents=True)
    (cron / "jobs.json").write_text(
        json.dumps({"jobs": [{"id": "watch1", "name": "Watch", "prompt": "Watch."}]}),
        encoding="utf-8",
    )
    legacy = tmp_path / "cron"
    legacy.mkdir()
    with sqlite3.connect(legacy / "executions.db") as connection:
        connection.execute(
            "CREATE TABLE executions (id TEXT PRIMARY KEY,job_id TEXT,status TEXT,"
            "source TEXT,claimed_at TEXT,started_at TEXT,finished_at TEXT,error TEXT)"
        )
        connection.execute(
            "INSERT INTO executions VALUES (?,?,?,?,?,?,?,?)",
            (
                "old-execution", "watch1", "failed", "scheduler",
                "2026-08-13T11:15:00+00:00", "2026-08-13T11:15:01+00:00",
                "2026-08-13T11:15:02+00:00", "provider unavailable",
            ),
        )

    items = specialist_cron_reconcile.scan(tmp_path, [specialist])

    assert len(items) == 1
    assert items[0]["status"] == "failed"
    assert items[0]["error"] == "provider unavailable"
    assert items[0]["runtime"]["ledger"] == "legacy_shared"


def test_specialist_thread_ids_are_canonical_and_reject_invalid_values():
    value = "41502968-0490-42B2-83C0-15D82C86E150"
    assert server._specialist_uuid(value) == value.lower()
    assert server._specialist_uuid("not-a-thread") is None
    assert server._specialist_uuid(None) is None


def test_specialist_handoff_projects_observable_stage_conversation():
    run_id = "0cd38f1d-1ffc-4c40-b59d-aac0de777ef4"
    thread_id = "41502968-0490-42b2-83c0-15d82c86e150"
    projected = calliope._project_tool_result(
        "delegate_calliope_specialist",
        {
            "status": "complete",
            "run_id": run_id,
            "thread_id": thread_id,
            "round": 2,
            "round_count": 2,
            "specialist": {
                "key": "dashboard-studio",
                "name": "Dashboard Studio",
                "profile": "dashboard-studio",
            },
            "conversation": [
                {"role": "calliope", "content": "Build a revenue dashboard."},
                {
                    "role": "specialist",
                    "name": "Dashboard Studio",
                    "content": "Published the dashboard and verified its bridge.",
                },
            ],
            "transcript": [
                {"ordinal": 1, "role": "calliope", "kind": "handoff", "text": "Build a revenue dashboard."},
                {"ordinal": 2, "role": "specialist", "kind": "progress", "text": "Checking the semantic catalog."},
                {"ordinal": 3, "role": "specialist", "kind": "assistant", "text": "Published the dashboard and verified its bridge."},
            ],
            "tool_activity": [
                {"event": "tool.completed", "tool_name": "publish_dashboard"}
            ],
            "stage_surfaces": [
                {"id": "surface-1", "kind": "artifact", "title": "Revenue"}
            ],
            "elapsed_ms": 3200,
        },
        {"specialist_key": "dashboard-studio"},
        "delegate-1",
    )

    assert len(projected) == 1
    surface = projected[0]
    assert surface["kind"] == "action"
    assert surface["lineage_key"] == f"specialist-thread:{thread_id}"
    assert surface["payload"]["mode"] == "specialist_thread"
    assert surface["payload"]["round_count"] == 2
    assert surface["payload"]["conversation"][1]["role"] == "specialist"
    assert surface["payload"]["transcript"][1]["kind"] == "progress"
    assert surface["presentation"]["view"] == "specialist_conversation"


def test_durable_specialist_ledger_builds_a_canonical_parent_stage_receipt():
    thread_id = "41502968-0490-42b2-83c0-15d82C86e150".lower()
    run_id = "0cd38f1d-1ffc-4c40-b59d-aac0de777ef4"
    projected = calliope._specialist_thread_projection({
        "status": "complete",
        "run_id": run_id,
        "thread_id": thread_id,
        "turn_id": "cc407cdf-fbf3-492a-904c-15f8759f7262",
        "round": 2,
        "round_count": 2,
        "specialist": {
            "key": "warehouse-steward",
            "name": "Warehouse Steward",
            "profile": "warehouse-steward",
        },
        "transcript": [
            {"role": "calliope", "round": 1, "text": "Give me your verdict."},
            {"role": "specialist", "round": 1, "text": "Operationally healthy."},
            {"role": "calliope", "round": 2, "text": "Challenge that claim."},
            {"role": "specialist", "round": 2, "text": "Correctness is unverified."},
        ],
        "tool_activity": [],
        "stage_surfaces": [],
    })

    assert projected["lineage_key"] == f"specialist-thread:{thread_id}"
    assert projected["tool_call_id"] == f"specialist-thread-ledger:{thread_id}:r2"
    assert projected["payload"]["mode"] == "specialist_thread"
    assert projected["payload"]["round_count"] == 2
    assert len(projected["payload"]["transcript"]) == 4
    assert projected["source"]["projection_source"] == "durable_specialist_ledger"


def test_session_read_repair_adds_missing_parent_conversation_without_hiding_evidence(monkeypatch):
    thread_id = "41502968-0490-42b2-83c0-15d82c86e150"
    session_id = "fc057382-0ddc-4d0c-9d73-a3cb39674f24"
    turn_id = "cc407cdf-fbf3-492a-904c-15f8759f7262"
    rows = [{
        "id": thread_id,
        "source_session_id": session_id,
        "root_turn_id": turn_id,
        "status": "complete",
        "objective": "Assess acceleration health.",
        "round_count": 1,
        "last_run_id": "0cd38f1d-1ffc-4c40-b59d-aac0de777ef4",
        "last_run_status": "complete",
        "latest_reply": "The trial remains under evaluation.",
        "latest_turn_id": turn_id,
        "specialist_key": "acceleration-steward",
        "profile_name": "acceleration-steward",
        "display_name": "Acceleration Steward",
        "updated_at": None,
    }]
    evidence = {
        "id": "value-system-surface",
        "session_id": session_id,
        "turn_id": turn_id,
        "ordinal": 1,
        "kind": "value_system",
        "title": "Sh Value System",
        "payload": {"mode": "value_system"},
        "source": {"specialist_thread_id": thread_id},
        "presentation": {"view": "value_river"},
    }
    detail = {
        "status": "complete",
        "run_id": rows[0]["last_run_id"],
        "thread_id": thread_id,
        "turn_id": turn_id,
        "round": 1,
        "round_count": 1,
        "specialist": {
            "key": "acceleration-steward",
            "name": "Acceleration Steward",
            "profile": "acceleration-steward",
        },
        "transcript": [
            {"role": "calliope", "round": 1, "text": "Assess the system."},
            {"role": "specialist", "round": 1, "text": "Still under evaluation."},
        ],
        "tool_activity": [],
        "stage_surfaces": [{"id": evidence["id"], "kind": "value_system"}],
        "completed_at": "2026-08-14T13:00:00+00:00",
    }

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(calliope, "_specialist_thread_detail", lambda *_args: detail)
    merged = calliope._merge_specialist_thread_surfaces(
        Connection,
        "alice@example.com",
        [evidence],
        rows,
    )

    assert len(merged) == 2
    assert merged[0] == evidence
    conversation = merged[1]
    assert conversation["turn_id"] == turn_id
    assert conversation["payload"]["mode"] == "specialist_thread"
    assert conversation["payload"]["round_count"] == 1
    assert conversation["id"] == f"specialist-thread-detail:{thread_id}"


def test_session_read_repair_refreshes_a_current_round_after_identity_change(monkeypatch):
    thread_id = "41502968-0490-42b2-83c0-15d82c86e150"
    session_id = "fc057382-0ddc-4d0c-9d73-a3cb39674f24"
    turn_id = "cc407cdf-fbf3-492a-904c-15f8759f7262"
    row = {
        "id": thread_id,
        "source_session_id": session_id,
        "root_turn_id": turn_id,
        "status": "complete",
        "objective": "Assess warehouse health.",
        "round_count": 2,
        "last_run_id": "0cd38f1d-1ffc-4c40-b59d-aac0de777ef4",
        "last_run_status": "complete",
        "latest_reply": "Healthy.",
        "latest_turn_id": turn_id,
        "specialist_key": "warehouse-steward",
        "profile_name": "warehouse-steward",
        "display_name": "Hestia",
    }
    current = {
        "id": "existing-surface",
        "session_id": session_id,
        "turn_id": turn_id,
        "ordinal": 4,
        "payload": {
            "mode": "specialist_thread",
            "thread_id": thread_id,
            "round_count": 2,
            "specialist": {
                "key": "warehouse-steward",
                "name": "Warehouse Steward",
                "profile": "warehouse-steward",
            },
        },
        "source": {"specialist_thread_id": thread_id},
        "presentation": {"view": "specialist_conversation"},
    }
    detail = {
        "status": "complete",
        "run_id": row["last_run_id"],
        "thread_id": thread_id,
        "turn_id": turn_id,
        "round_count": 2,
        "specialist": {
            "key": "warehouse-steward",
            "name": "Hestia",
            "profile": "warehouse-steward",
        },
        "transcript": [],
        "tool_activity": [],
        "stage_surfaces": [],
    }

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(calliope, "_specialist_thread_detail", lambda *_args: detail)
    merged = calliope._merge_specialist_thread_surfaces(
        Connection, "alice@example.com", [current], [row]
    )

    assert len(merged) == 1
    assert merged[0]["id"] == "existing-surface"
    assert merged[0]["payload"]["specialist"]["name"] == "Hestia"
    assert merged[0]["payload"]["round_count"] == 2


def test_turn_completion_backfills_a_missing_specialist_surface_from_the_ledger(monkeypatch):
    thread_id = "41502968-0490-42b2-83c0-15d82c86e150"
    session_id = "fc057382-0ddc-4d0c-9d73-a3cb39674f24"
    turn_id = "cc407cdf-fbf3-492a-904c-15f8759f7262"
    row = {
        "id": thread_id,
        "source_session_id": session_id,
        "root_turn_id": turn_id,
        "status": "complete",
        "objective": "Challenge the health verdict.",
        "round_count": 2,
        "last_run_id": "0cd38f1d-1ffc-4c40-b59d-aac0de777ef4",
        "last_run_status": "complete",
        "latest_reply": "Operationally healthy; correctness unverified.",
        "latest_turn_id": turn_id,
        "specialist_key": "warehouse-steward",
        "profile_name": "warehouse-steward",
        "display_name": "Warehouse Steward",
        "updated_at": None,
    }
    detail = {
        "status": "complete",
        "run_id": row["last_run_id"],
        "thread_id": thread_id,
        "turn_id": turn_id,
        "round": 2,
        "round_count": 2,
        "specialist": {
            "key": "warehouse-steward",
            "name": "Warehouse Steward",
            "profile": "warehouse-steward",
        },
        "transcript": [
            {"role": "calliope", "round": 1, "text": "Assess health."},
            {"role": "specialist", "round": 1, "text": "Healthy."},
            {"role": "calliope", "round": 2, "text": "Challenge it."},
            {"role": "specialist", "round": 2, "text": "Correctness unverified."},
        ],
        "tool_activity": [],
        "stage_surfaces": [],
    }

    class Result:
        @staticmethod
        def fetchall():
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def execute(sql, params):
            assert "FROM rvbbit.calliope_surfaces" in sql
            assert params == (session_id,)
            return Result()

    monkeypatch.setattr(calliope, "_specialist_thread_rows", lambda *_args: [row])
    monkeypatch.setattr(calliope, "_specialist_thread_detail", lambda *_args: detail)

    projected = calliope._project_missing_specialist_threads(
        Connection,
        "alice@example.com",
        session_id,
        turn_id,
        [],
    )
    reconciled = calliope._project_missing_specialist_threads(
        Connection,
        "alice@example.com",
        session_id,
        turn_id,
        projected,
    )

    assert len(projected) == 1
    assert projected[0]["payload"]["round_count"] == 2
    assert reconciled == projected


def test_namespaced_deferred_specialist_handoff_projects_from_hermes_transcript():
    run_id = "0cd38f1d-1ffc-4c40-b59d-aac0de777ef4"
    thread_id = "41502968-0490-42b2-83c0-15d82c86e150"
    value = {
        "status": "complete",
        "run_id": run_id,
        "thread_id": thread_id,
        "specialist": {
            "key": "warehouse-steward",
            "name": "Warehouse Steward",
            "profile": "warehouse-steward",
        },
        "conversation": [
            {"role": "calliope", "content": "Check appliance health."},
            {
                "role": "specialist",
                "name": "Warehouse Steward",
                "content": "Mirrors are healthy; catalog freshness is degraded.",
            },
        ],
    }
    result = (
        '<untrusted_tool_result source="mcp__Warehouse__delegate_calliope_specialist">\n'
        "External data warning.\n\n"
        + json.dumps({"result": json.dumps(value)})
        + "\n</untrusted_tool_result>"
    )
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "delegate-1",
                "function": {
                    "name": "tool_call",
                    "arguments": json.dumps({
                        "name": "mcp__Warehouse__delegate_calliope_specialist",
                        "arguments": {"specialist_key": "warehouse-steward"},
                    }),
                },
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "delegate-1",
            "tool_name": "mcp__Warehouse__delegate_calliope_specialist",
            "content": result,
        },
    ]

    projected = calliope.project_messages(messages)

    assert len(projected) == 1
    assert projected[0]["lineage_key"] == f"specialist-thread:{thread_id}"
    assert projected[0]["payload"]["mode"] == "specialist_thread"


def test_specialist_query_and_published_artifact_project_during_the_handoff():
    run_id = "0cd38f1d-1ffc-4c40-b59d-aac0de777ef4"
    query = calliope._project_progressive_specialist_result(
        "mcp__Specialist__run_sql",
        {
            "structuredContent": {
                "columns": [{"name": "status"}, {"name": "jobs"}],
                "rows": [["healthy", 6]],
                "row_count": 1,
            }
        },
        {"sql": "select 'healthy' status, 6 jobs"},
        "specialist-query-1",
        specialist_key="warehouse-steward",
        specialist_run_id=run_id,
        intent="Checking the mirror controller.",
    )
    artifact = calliope._project_progressive_specialist_result(
        "mcp__Specialist__create_live_app",
        {"slug": "revenue-watch", "version": 2, "app_kind": "dashboard"},
        {"name": "Revenue Watch"},
        "specialist-artifact-1",
        specialist_key="dashboard-studio",
        specialist_run_id=run_id,
    )

    assert query[0]["kind"] == "query"
    assert query[0]["source"]["specialist_run_id"] == run_id
    assert query[0]["source"]["exploration"]["intent"].startswith("Checking")
    assert artifact[0]["kind"] == "artifact"
    assert artifact[0]["artifact_slug"] == "revenue-watch"
    assert artifact[0]["presentation"]["specialist_key"] == "dashboard-studio"
    assert artifact[0]["presentation"]["phase"] == "published"
    assert artifact[0]["source"]["phase"] == "published"
    assert calliope._project_progressive_specialist_result(
        "mcp__Specialist__upload_artifact",
        {"artifact_id": "scratch-only"},
        {},
        "scratch-1",
        specialist_key="dashboard-studio",
        specialist_run_id=run_id,
    ) == []


def test_specialist_live_frames_are_ordered_and_idempotent():
    run_id = "0cd38f1d-1ffc-4c40-b59d-aac0de777ef4"
    snapshot = {
        "run": {
            "id": run_id,
            "status": "complete",
            "specialist": {
                "key": "warehouse-steward",
                "name": "Warehouse Steward",
                "profile": "warehouse-steward",
            },
            "started_at": "2026-08-14T05:00:00+00:00",
            "completed_at": "2026-08-14T05:00:20+00:00",
        },
        "messages": [
            {"ordinal": 1, "role": "calliope", "kind": "handoff", "body": "Check mirrors."},
            {"ordinal": 2, "role": "specialist", "kind": "progress", "body": "Checking the controller."},
            {"ordinal": 3, "role": "tool", "kind": "tool_started", "body": "", "tool_name": "mirror_status", "call_id": "same-native-call"},
            {"ordinal": 4, "role": "tool", "kind": "tool_completed", "body": "", "tool_name": "mirror_status", "call_id": "same-native-call"},
            {"ordinal": 5, "role": "specialist", "kind": "assistant", "body": "Six enabled; zero failed."},
        ],
        "surfaces": [{"id": "surface-1", "kind": "query", "title": "Mirror health"}],
    }
    state = {}

    frames = calliope._specialist_live_frames(snapshot, state, "turn-1")
    text = b"".join(frames).decode()

    assert '"type":"started"' in text
    assert '"type":"handoff"' in text
    assert '"type":"progress"' in text
    assert '"type":"tool.started"' in text
    assert text.count('"call_id":"same-native-call"') == 2
    assert '"type":"assistant"' in text
    assert "event: calliope.surfaces" in text
    assert '"type":"completed"' in text
    assert state["ordinal"] == 5
    assert calliope._specialist_live_frames(snapshot, state, "turn-1") == []


def test_specialist_live_snapshots_keep_parallel_runs_independent():
    turn_id = "11111111-1111-4111-8111-111111111111"
    iris_run = "22222222-2222-4222-8222-222222222222"
    eos_run = "33333333-3333-4333-8333-333333333333"

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return list(self.rows)

    class Conn:
        def __init__(self):
            self.executed = []

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, params=None):
            self.executed.append((sql, params))
            if "ORDER BY r.created_at,r.id LIMIT 32" in sql:
                return Cursor([
                    {
                        "id": iris_run,
                        "thread_id": "44444444-4444-4444-8444-444444444444",
                        "round_no": 1,
                        "status": "running",
                        "delivery": "mailbox",
                        "specialist_key": "dashboard-studio",
                        "profile_name": "dashboard-studio",
                        "display_name": "Iris",
                        "started_at": None,
                        "completed_at": None,
                    },
                    {
                        "id": eos_run,
                        "thread_id": "55555555-5555-4555-8555-555555555555",
                        "round_no": 1,
                        "status": "complete",
                        "delivery": "sync",
                        "specialist_key": "outbound-fulfillment",
                        "profile_name": "outbound-fulfillment",
                        "display_name": "Eos",
                        "started_at": None,
                        "completed_at": None,
                    },
                ])
            if "SELECT m.run_id,m.ordinal" in sql:
                return Cursor([
                    {
                        "run_id": iris_run,
                        "ordinal": 2,
                        "role": "specialist",
                        "kind": "tool_completed",
                        "body": "Building the dashboard.",
                        "payload": {"tool_name": "todo", "plan": {"confirmed": True, "revision": 6,
                            "todos": [{"id": "build", "content": "Build dashboard", "status": "pending"}]}},
                        "created_at": None,
                    },
                    {
                        "run_id": eos_run,
                        "ordinal": 4,
                        "role": "specialist",
                        "kind": "assistant",
                        "body": "The dispatch analysis is ready.",
                        "payload": {},
                        "created_at": None,
                    },
                ])
            if "SELECT sf.* FROM rvbbit.calliope_surfaces" in sql:
                return Cursor([
                    {
                        "id": "66666666-6666-4666-8666-666666666666",
                        "session_id": "77777777-7777-4777-8777-777777777777",
                        "turn_id": turn_id,
                        "kind": "artifact",
                        "title": "Editorial overview",
                        "source": {"specialist_run_id": iris_run},
                        "payload": {},
                        "presentation": {},
                    },
                    {
                        "id": "88888888-8888-4888-8888-888888888888",
                        "session_id": "77777777-7777-4777-8777-777777777777",
                        "turn_id": turn_id,
                        "kind": "query",
                        "title": "Dispatch health",
                        "source": {"specialist_run_id": eos_run},
                        "payload": {},
                        "presentation": {},
                    },
                ])
            return Cursor([])

    conn = Conn()
    snapshots = calliope._specialist_live_snapshots(
        lambda: conn,
        turn_id,
        after_ordinals={iris_run: 1, eos_run: 3},
    )

    assert [snapshot["run"]["specialist"]["name"] for snapshot in snapshots] == [
        "Iris",
        "Eos",
    ]
    assert [snapshot["messages"][0]["ordinal"] for snapshot in snapshots] == [2, 4]
    assert snapshots[0]["messages"][0]["plan"]["revision"] == 6
    assert snapshots[1]["messages"][0]["plan"] is None
    assert [snapshot["surfaces"][0]["title"] for snapshot in snapshots] == [
        "Editorial overview",
        "Dispatch health",
    ]
    cursor_params = next(
        params for sql, params in conn.executed if "SELECT m.run_id,m.ordinal" in sql
    )
    assert json.loads(cursor_params[1]) == {iris_run: 1, eos_run: 3}

    states = {}
    frames = []
    for snapshot in snapshots:
        run_id = snapshot["run"]["id"]
        frames.extend(
            calliope._specialist_live_frames(
                snapshot,
                states.setdefault(run_id, {}),
                turn_id,
            )
        )
    rendered = b"".join(frames).decode()
    assert '"name":"Iris"' in rendered and '"name":"Eos"' in rendered
    assert rendered.count("event: calliope.surfaces") == 2
    assert states[iris_run]["ordinal"] == 2
    assert states[eos_run]["ordinal"] == 4


def test_parent_turn_keeps_following_async_specialists_after_delegate_returns():
    source = (calliope._ASSET_DIR.parent / "calliope.py").read_text(encoding="utf-8")
    stream = source.split("async def stream() -> AsyncIterator[bytes]:", 1)[1]
    stream = stream.split("@mcp.custom_route", 1)[0]

    assert "_specialist_live_snapshots" in stream
    assert 'specialist_watch.setdefault("runs", {})' in stream
    assert "for snapshot in snapshots:" in stream
    assert "specialist_watch.clear()" not in stream
    # The single false assignment is initialization. Tool completion and
    # partial fan-out failure must leave the watcher enabled.
    assert stream.count('specialist_watch["enabled"] = False') == 0
    assert stream.count('specialist_watch["enabled"] = True') == 4
    assert "recovered_specialists" in stream


def test_specialist_activity_is_attributed_to_parent_calliope_owner(monkeypatch):
    linked = {
        "owner": "owner@example.com",
        "calliope_session_id": "755303d5-7f91-42c3-bbea-b989e34a56c9",
        "kind": "specialist",
        "specialist_key": "warehouse-steward",
        "trigger_kind": "delegated",
        "status": "running",
    }
    monkeypatch.setattr(
        server,
        "_calliope_activity_for_hermes_session",
        lambda _conn, session_ref: linked if session_ref == "calliope_specialist_123" else None,
    )
    context = {
        "channel": "hermes",
        "client_app": "hermes_api",
        "session_ref": "calliope_specialist_123",
        "provenance": {"source": "hermes", "platform": "api_server"},
    }

    caller, resolved = server._resolve_activity_context(
        object(), context, "calliope-service", "static-key", tool="mirror_status"
    )

    assert caller == "owner@example.com"
    assert resolved["channel"] == "web"
    assert resolved["client_app"] == "calliope_specialist:warehouse-steward"
    assert resolved["provenance"]["specialist_key"] == "warehouse-steward"


def test_scheduled_specialist_cron_activity_joins_to_the_same_run_ledger(monkeypatch):
    linked = {
        "owner": "owner@example.com",
        "calliope_session_id": "",
        "kind": "specialist",
        "specialist_key": "knowledge-gardener",
        "trigger_kind": "scheduled",
        "status": "running",
    }
    monkeypatch.setattr(
        server,
        "_calliope_activity_for_hermes_session",
        lambda _conn, session_ref: linked if session_ref.startswith("cron_") else None,
    )
    context = {
        "channel": "automation",
        "client_app": "hermes_cron",
        "session_ref": "cron_garden123_20260814_134408",
        "provenance": {"source": "hermes", "platform": "cron"},
    }

    caller, resolved = server._resolve_activity_context(
        object(), context, "calliope-service", "static-key", tool="ask_brain"
    )

    assert caller == "owner@example.com"
    assert resolved["channel"] == "automation"
    assert resolved["client_app"] == "calliope_specialist:knowledge-gardener"
    assert resolved["provenance"]["trigger_kind"] == "scheduled"
    assert resolved["provenance"]["specialist_key"] == "knowledge-gardener"


def test_specialist_transport_fallbacks_cannot_be_recorded_as_success():
    assert server._specialist_terminal_failure("")
    assert server._specialist_terminal_failure(
        "Operation interrupted: waiting for model response (1.8s elapsed)."
    )
    assert server._specialist_terminal_failure("Operation interrupted.")
    assert server._specialist_terminal_failure("Context length exceeded (1,453 tokens).")
    assert not server._specialist_terminal_failure(
        "Mirrors are current; catalog drift is the remaining concern."
    )


def test_specialist_handoff_forces_profile_scoped_warehouse_transport():
    repo = _HERE.parents[1]
    source = Path(server.__file__).read_text(encoding="utf-8")
    reconciler = repo.joinpath(
        "docker/calliope/reconcile-hermes-specialists.py"
    ).read_text(encoding="utf-8")

    # The full reusable doctrine belongs in each specialist's managed SOUL so
    # it survives compaction and benefits from prompt caching. The handoff
    # retains a compact hard rail as defense against a hand-edited SOUL.
    assert "mcp__Specialist__" in source
    assert "never the parent mcp__Warehouse__ transport" in source
    assert "COLLABORATION CONTRACT governs this round" in source
    assert "the default\n  mcp__Warehouse__ prefix belongs" in reconciler
    assert "inherit Calliope's full Warehouse capability library" in reconciler
    assert "never as an opening" in reconciler          # discovery is for an ability you cannot see, not a ritual
    assert "automatically projected" in reconciler
    assert "never search for a separate" in reconciler


def test_specialist_transport_is_a_parallel_lane_on_the_full_warehouse_registry():
    repo = _HERE.parents[1]
    config = yaml.safe_load(
        (repo / "docker/calliope/hermes-config.yaml").read_text(encoding="utf-8")
    )
    parent = config["mcp_servers"]["Warehouse"]
    roundtable = config["mcp_servers"]["Roundtable"]
    specialist = config["mcp_servers"]["Specialist"]
    assert specialist["url"].split("?", 1)[0] == parent["url"]
    assert specialist["url"].endswith("?lane=specialist")
    assert roundtable["url"].split("?", 1)[0] == parent["url"]
    assert roundtable["url"].endswith("?lane=roundtable")
    assert parent["supports_parallel_tool_calls"] is True
    assert roundtable["supports_parallel_tool_calls"] is True
    assert specialist["supports_parallel_tool_calls"] is True
    assert parent["timeout"] == 90
    # every tool that blocks on a specialist run rides the long lane (a single
    # delegation waits for its child; on the 90 s lane that was an MCP timeout)
    long_lane = [
        "delegate_all_calliope_specialists",
        "delegate_calliope_specialist",
        "continue_calliope_specialist",
        "await_calliope_specialists",
    ]
    # ask_calliope is a specialist's reply channel: hidden from the parent lanes,
    # visible on the unfiltered specialist lane.
    assert parent["tools"]["exclude"] == long_lane + ["ask_calliope"]
    assert roundtable["timeout"] >= 2_100
    assert roundtable["tools"]["include"] == long_lane
    assert "tools" not in specialist or "ask_calliope" not in (
        specialist.get("tools", {}).get("exclude") or []
    )
    lane = config["platform_toolsets"]["api_server"]
    assert "hermes-api-server" not in lane and "web" not in lane
    assert lane == [
        "terminal",
        "file",
        "vision",
        "image_gen",
        "skills",
        "browser",
        "todo",
        "memory",
        "session_search",
        "code_execution",
        "cronjob",
        "homeassistant",
        "Warehouse",
        "Roundtable",
    ]

    reconciler = (
        repo / "docker/calliope/reconcile-hermes-specialists.py"
    ).read_text(encoding="utf-8")
    assert '"Specialist",' in reconciler and "hermes-api-server" not in reconciler

    registry = Path(server.__file__).read_text(encoding="utf-8")
    for tool in (
        "capability_search",
        "business_context_for_question",
        "ask_brain",
        "run_sql",
        "administer_local_sql",
        "create_live_app",
    ):
        assert f'mcp.tool(name="{tool}")' in registry


def test_specialist_automatically_inherits_exact_current_turn_objects():
    source = {
        "session_id": "755303d5-7f91-42c3-bbea-b989e34a56c9",
        "turn_id": "0cd38f1d-1ffc-4c40-b59d-aac0de777ef4",
        "ordinal": 7,
        "turn_kind": "chat",
        "selected_surface_id": "302a98e1-d01e-46e3-aa2d-7557a86f4454",
        "object_refs": [{"kind": "artifact", "ref_id": "revenue@3"}],
        "evidence_refs": [{"surface_id": "evidence-1", "title": "Revenue"}],
        "attachments": [{"id": "image-1", "name": "reference.png"}],
    }
    profile = {
        "id": "ca1100e0-0000-4000-8000-000000000101",
        "profile_id": "ca1100e0-0000-4000-8000-000000000001",
        "profile_name": "Adaptive Calliope",
        "profile_description": "Follow the viewer theme.",
        "version": 1,
        "compiled_prompt": "Use the exact runtime theme token contract.",
        "tokens": {"behavior": {"theme_source": "viewer"}},
    }

    inherited = server._specialist_current_turn_context(
        source,
        {"constraint": "Preserve the existing queries."},
        design_profile=profile,
        selected_surface={"id": source["selected_surface_id"], "kind": "artifact"},
        profile_assets=[{"source_kind": "image", "original_name": "mood.webp"}],
    )

    assert inherited["provided"] == {"constraint": "Preserve the existing queries."}
    turn = inherited["current_turn"]
    assert turn["object_refs"] == source["object_refs"]
    assert turn["evidence_refs"] == source["evidence_refs"]
    assert turn["attachments"] == source["attachments"]
    assert turn["selected_surface"]["id"] == source["selected_surface_id"]
    assert turn["design_profile"]["authoritative"] is True
    assert turn["design_profile"]["version_id"] == profile["id"]
    assert turn["design_profile"]["compiled_prompt"] == profile["compiled_prompt"]
    assert turn["design_profile"]["compiled_prompt_chars"] == len(
        profile["compiled_prompt"]
    )
    assert turn["design_profile"]["tokens"] == profile["tokens"]
    assert turn["design_profile"]["assets"][0]["original_name"] == "mood.webp"

    implementation = Path(server.__file__).read_text(encoding="utf-8")
    assert "t.design_profile_version_id,t.selected_surface_id,t.object_refs" in implementation
    assert "PINNED CURRENT-TURN DESIGN PROFILE — AUTHORITATIVE" in implementation
    assert "*image_parts" in implementation
    # progressive and final specialist projections inherit the pinned profile
    # (the round body lives inside execute(); match modulo indentation)
    assert "calliope._insert_progressive_result_surfaces" in implementation
    assert re.search(r"last_progress,\n\s+design_profile,", implementation)
    assert re.search(r"projected,\n\s+design_profile,", implementation)


def test_warehouse_publication_freezes_active_turn_profile_before_stage_projection(monkeypatch):
    profile = {
        "id": "ca1100e0-0000-4000-8000-000000000101",
        "profile_id": "ca1100e0-0000-4000-8000-000000000001",
        "profile_name": "Adaptive Calliope",
        "version": 1,
        "tokens": {
            "behavior": {
                "theme_source": "viewer",
                "runtime": "rvbbit.adaptive-theme.v1",
            },
        },
    }
    monkeypatch.setattr(server, "_active_calliope_design_profile", lambda: profile)

    manifest = server._with_active_calliope_design_profile({"app_kind": "dashboard"})

    assert manifest["app_kind"] == "dashboard"
    assert manifest["design_profile"]["version_id"] == profile["id"]
    assert manifest["design_profile"]["adaptive"] is True
    assert manifest["design_profile"]["behavior"]["theme_source"] == "viewer"


def test_main_calliope_strongly_prefers_organic_specialist_ownership_without_gatekeeping():
    instruction_start = server._INSTRUCTIONS.index("SPECIALIST OWNERSHIP:")
    instruction_end = server._INSTRUCTIONS.index(
        "CALLIOPE PLAYBOOKS:", instruction_start
    )
    instructions = server._INSTRUCTIONS[instruction_start:instruction_end]

    assert "specialist_key dashboard-studio" in instructions
    assert "specialist_key warehouse-steward" in instructions
    assert "specialist_key semantic-architect" in instructions
    assert "specialist_key signal-watch" in instructions
    assert "specialist_key knowledge-gardener" in instructions
    assert "specialist_key analytical-skeptic" in instructions
    assert "specialist_key acceleration-steward" not in instructions
    assert "specialist_key integration-scout" not in instructions
    assert "Strongly prefer Iris" in instructions
    assert "Strongly prefer Hestia" in instructions
    assert "acceleration" in instructions
    assert "call list_calliope_specialists when a key is unknown" in instructions
    assert "do not ask the user to approve a handoff" in instructions
    assert "preferred ownership rules, not prohibitions" in instructions
    assert "when the user asks Calliope to do it herself" in instructions
    assert "specialist is unavailable, has failed" in instructions
    assert "delegation is step zero" in instructions
    assert "do not rediscover them first" in instructions
    # 2026-08-18: an explicit user process ("build it, then send it to Iris for
    # a review") was turned into "Iris, own the full workflow and review your
    # own build" on the second API call, before any skill was loaded. The
    # override has to live where the model reads first: these instructions
    # and the delegate tool itself.
    assert "AN EXPLICIT USER PROCESS ALWAYS WINS" in instructions
    assert "never hand the whole process to a specialist" in instructions
    assert "review must come from someone who did not build the artifact" in instructions
    delegate_doc = str(server._mcp_delegate_calliope_specialist.__doc__)
    assert "an explicit user process always wins" in " ".join(delegate_doc.split())
    assert 'never "own the full workflow"' in " ".join(delegate_doc.split())


def test_dashboard_skill_routes_primary_build_outcomes_but_keeps_direct_fallback():
    repo = _HERE.parents[1]
    skill = (
        repo / "docker/calliope/hermes-skills/calliope-live-dashboards/SKILL.md"
    ).read_text(encoding="utf-8")

    assert "## Preferred ownership" in skill
    assert 'specialist_key="dashboard-studio"' in skill
    assert "do not list specialists first" in skill
    assert "not a prohibition" in skill
    assert "Main Calliope may execute the workflow directly" in skill
    assert "inside Iris's Dashboard Studio profile" in skill
    assert "Routing is step zero" in skill
    assert "do not rediscover evidence first" in skill
    assert "An explicit user process wins over this default" in skill
    assert "Scope decisions are questions" in skill
    assert "automatically carries the exact current-turn design profile" in skill
    assert "Never paraphrase or substitute for those pinned objects" in skill
    assert "substantive visual revision for ownership purposes" in skill

    soul = (
        repo / "docker/calliope/hermes-specialists/dashboard-studio/SOUL.md"
    ).read_text(encoding="utf-8")
    assert "Own only the discovery still missing from the delegated assignment" in soul
    assert "Do not repeat Library" in soul
    assert "exact `current_turn` envelope" in soul
    assert "never invent a replacement theme" in soul


def test_specialist_manifest_and_migration_keep_memory_and_messages_separate():
    repo = _HERE.parents[1]
    manifest = json.loads(
        (repo / "docker/calliope/hermes-specialists/manifest.json").read_text()
    )
    profiles = {item["key"]: item for item in manifest["profiles"]}
    identities = {
        key: yaml.safe_load(
            (repo / f"docker/calliope/hermes-specialists/{key}/profile.yaml").read_text()
        )
        for key in profiles
    }
    assert {key: item["name"] for key, item in identities.items()} == {
        "dashboard-studio": "Iris",
        "warehouse-steward": "Hestia",
        "semantic-architect": "Athena",
        "signal-watch": "Argus",
        "knowledge-gardener": "Demeter",
        "analytical-skeptic": "Cassandra",
    }
    assert profiles["dashboard-studio"]["memory_bank"] == "calliope-dashboard-studio"
    assert profiles["warehouse-steward"]["memory_bank"] == "calliope-warehouse-steward"
    assert len({item["memory_bank"] for item in profiles.values()}) == len(profiles)
    assert all(item["memory_mission"] for item in profiles.values())
    assert identities["dashboard-studio"]["signature_font"] == "Italiana"
    assert identities["dashboard-studio"]["avatar_file"] == "iris.png"

    reconciler = (
        repo / "docker/calliope/reconcile-hermes-specialists.py"
    ).read_text(encoding="utf-8")
    assert '"bank_mission": spec["memory_mission"]' in reconciler
    assert '"retain_tags": ["calliope-specialist", f"profile:{key}"]' in reconciler
    assert '"observation_scopes": [f"calliope-specialist:{key}"]' in reconciler
    assert 'profile / "profile.yaml"' in reconciler
    assert 'markers / "profile.sha256"' in reconciler

    migration = (
        repo / "crates/pg_rvbbit/sql/migrations/0302_calliope_specialists.sql"
    ).read_text()
    assert "calliope_specialist_runs" in migration
    assert "calliope_specialist_messages" in migration
    assert "source_turn_id" in migration
    assert "UNIQUE(run_id,ordinal)" in migration

    identity_migration = (
        repo
        / "crates/pg_rvbbit/sql/migrations/0310_calliope_specialist_identities.sql"
    ).read_text()
    assert "UPDATE rvbbit.calliope_specialists" in identity_migration
    assert "('dashboard-studio', 'Iris'" in identity_migration
    assert "('analytical-skeptic', 'Cassandra'" in identity_migration
    migrations = (repo / "crates/pg_rvbbit/src/migrations.rs").read_text()
    assert '"0310_calliope_specialist_identities"' in migrations


def test_specialists_receive_advisory_capability_search_bearings():
    repo = _HERE.parents[1]
    migration = (
        repo
        / "crates/pg_rvbbit/sql/migrations/0320_specialist_capability_bearings.sql"
    ).read_text(encoding="utf-8")
    server_source = (repo / "services/calliope/server.py").read_text(encoding="utf-8")
    demeter = (
        repo / "docker/calliope/hermes-specialists/knowledge-gardener/SOUL.md"
    ).read_text(encoding="utf-8")

    assert "business_topology" in migration
    assert "value_systems" in migration
    assert "Advisory capability_search bearings" in migration
    assert "CAPABILITY BEARINGS — ADVISORY" in server_source
    assert "not as a restricted tool list" in server_source
    assert "Business Topology and value systems" in demeter
    migrations = (repo / "crates/pg_rvbbit/src/migrations.rs").read_text()
    assert '"0320_specialist_capability_bearings"' in migrations


def test_specialist_handoff_inherits_bounded_recent_parent_conversation():
    rows = [
        {
            "ordinal": 1,
            "user_message": "First question",
            "assistant_message": "First answer",
        },
        {
            "ordinal": 2,
            "user_message": "Follow-up",
            "assistant_message": "Second answer",
        },
    ]

    class Result:
        def fetchall(self):
            return list(reversed(rows))

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, query, args):
            assert "FROM rvbbit.calliope_turns" in query
            assert args[1:] == (3, 6)
            return Result()

    context = server._specialist_parent_conversation_context(
        lambda: Connection(),
        {
            "session_id": "6b3ab2c0-4aa6-4310-ae3a-acde63f347e3",
            "ordinal": 3,
            "user_message": "@Cassandra What did you think of that?",
        },
    )

    assert context["scope"] == "recent_parent_conversation"
    assert [turn["ordinal"] for turn in context["completed_turns"]] == [1, 2]
    assert context["completed_turns"][1]["calliope"] == "Second answer"
    assert context["current_user_message"].startswith("@Cassandra")

    source = (_HERE / "server.py").read_text(encoding="utf-8")
    assert "async def _mcp_delegate_all_calliope_specialists" in source
    assert "await asyncio.gather" in source
    assert 'CALLIOPE_SPECIALIST_FANOUT_CONCURRENCY", "4"' in source
    assert "asyncio.Semaphore(fanout_concurrency)" in source
    assert 'mcp.tool(name="delegate_all_calliope_specialists")' in source
    assert "Do not emulate it with repeated individual delegations" in source


def test_specialist_profiles_bound_tool_heavy_context_without_losing_audit_history():
    repo = _HERE.parents[1]
    reconciler = (
        repo / "docker/calliope/reconcile-hermes-specialists.py"
    ).read_text(encoding="utf-8")
    assert '"protect_last_n": 12' in reconciler
    assert '"proactive_prune_tokens": 120000' in reconciler
    assert '"proactive_prune_min_result_chars": 6000' in reconciler
    assert '"262144"' in reconciler          # working budget; 131k left ~6k over the ~86k base
    assert '"skills": {"creation_nudge_interval": 0}' in reconciler   # no reflex skill curator
    assert '"Specialist"' in reconciler
    assert "?lane=specialist" in reconciler
    assert "calliope_specialist_messages" in (
        repo / "crates/pg_rvbbit/sql/migrations/0302_calliope_specialists.sql"
    ).read_text(encoding="utf-8")


def test_specialist_execution_uses_durable_hermes_runs_and_restart_recovery():
    repo = _HERE.parents[1]
    server = (repo / "services/calliope/server.py").read_text(encoding="utf-8")
    migration = (
        repo
        / "crates/pg_rvbbit/sql/migrations/0306_calliope_specialist_execution.sql"
    ).read_text(encoding="utf-8")

    assert "calliope._iter_hermes_run(" in server
    assert 'base_url=f"{config.hermes_url}{prefix}"' in server
    assert "_start_specialist_recovery_worker()" in server
    assert "_start_specialist_cron_reconcile_worker()" in server
    assert "_recover_specialist_execution" in server
    assert "_await_specialist_result" in server
    assert "hermes_run_id" in migration
    assert "lease_expires_at" in migration
    assert "calliope_specialist_runs_parent_identity_idx" in migration
    assert "status IN ('running','complete')" in migration


def test_specialists_are_persistent_multi_round_agent_threads():
    repo = _HERE.parents[1]
    server_source = (repo / "services/calliope/server.py").read_text(encoding="utf-8")
    calliope_source = (repo / "services/calliope/calliope.py").read_text(encoding="utf-8")
    migration = (
        repo / "crates/pg_rvbbit/sql/migrations/0307_calliope_specialist_threads.sql"
    ).read_text(encoding="utf-8")

    assert "calliope_specialist_threads" in migration
    assert "calliope_specialist_runs_thread_round_idx" in migration
    assert "calliope_specialist_runs_thread_active_idx" in migration
    assert "DROP CONSTRAINT IF EXISTS calliope_specialist_runs_hermes_session_id_key" in migration
    assert "async def _mcp_continue_calliope_specialist" in server_source
    assert "def _mcp_list_calliope_specialist_threads" in server_source
    assert 'thread_id=thread_id' in server_source
    assert "next_task != prior_task" in server_source
    assert "exact retries remain idempotent" in server_source
    assert 'session_ref.startswith("calliope_specialist_")' in server_source
    assert "cannot invoke " in server_source
    assert '"least two substantive rounds unless the specialist is blocked.' in server_source
    assert '"the same thread_id with an increased round_count' in server_source
    assert '"receipt is never a specialist reply.' in server_source
    assert '"specialist_thread"' in calliope_source
    assert "specialist_threads" in calliope_source
    assert calliope_source.count('"continue_calliope_specialist"') >= 4
    assert "/api/calliope/specialist-threads/{thread_id}" in calliope_source
    assert "_specialist_thread_detail" in calliope_source


def test_hosted_config_upgrade_enables_profile_multiplexing(tmp_path):
    repo = _HERE.parents[1]
    reconciler = runpy.run_path(
        str(repo / "docker/calliope/reconcile-hermes-config.py")
    )["reconcile"]
    config = tmp_path / "config.yaml"
    config.write_text(
        "model:\n  default: calliope\n"
        "gateway:\n  existing_setting: preserved\n",
        encoding="utf-8",
    )

    assert reconciler(config) is True
    payload = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert payload["gateway"] == {
        "existing_setting": "preserved",
        "multiplex_profiles": True,
        "multiplex_profile_allowlist": [
            "dashboard-studio",
            "warehouse-steward",
            "semantic-architect",
            "signal-watch",
            "knowledge-gardener",
            "analytical-skeptic",
        ],
    }


def test_specialist_profile_config_keeps_skill_toggles_but_lands_curator_default(tmp_path):
    """A profile owns its `skills.disabled` list; the appliance default that turns
    off Hermes' reflex skill curator (creation_nudge_interval 0) still lands when
    the profile has not chosen one (upstream 10 counts as unchosen), and an
    operator's own interval survives."""
    repo = _HERE.parents[1]
    module = runpy.run_path(str(repo / "docker/calliope/reconcile-hermes-specialists.py"))
    profile = tmp_path / "profiles" / "dashboard-studio"
    profile.mkdir(parents=True)
    markers = tmp_path / ".managed"
    (profile / "config.yaml").write_text(
        "model:\n  default: old\nskills:\n  disabled: [foo]\n", encoding="utf-8",
    )
    assert module["_reconcile_config"](profile, module["_profile_config"]("dashboard-studio"), markers) == "updated"
    payload = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert payload["skills"] == {"disabled": ["foo"], "creation_nudge_interval": 0}
    assert payload["model"]["context_length"] == 262144

    (profile / "config.yaml").write_text(
        "skills:\n  disabled: []\n  creation_nudge_interval: 10\n", encoding="utf-8",
    )
    module["_reconcile_config"](profile, module["_profile_config"]("dashboard-studio"), markers)
    assert yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))["skills"]["creation_nudge_interval"] == 0

    (profile / "config.yaml").write_text(
        "skills:\n  disabled: []\n  creation_nudge_interval: 40\n", encoding="utf-8",
    )
    module["_reconcile_config"](profile, module["_profile_config"]("dashboard-studio"), markers)
    assert yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))["skills"]["creation_nudge_interval"] == 40


def test_generated_specialist_cache_is_owned_for_supervisor_reads(tmp_path, monkeypatch):
    repo = _HERE.parents[1]
    module = runpy.run_path(str(repo / "docker/calliope/reconcile-hermes-specialists.py"))
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    cache = tmp_path / ".calliope-seeds" / "generated-specialists.json"
    cache.parent.mkdir()
    cache.write_text('{"specialists": []}\n', encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    calls = []

    monkeypatch.setattr(
        module["shutil"],
        "chown",
        lambda path, *, user, group: calls.append((Path(path), user, group)),
    )
    monkeypatch.setattr(module["os"], "geteuid", lambda: 0)
    marker = cache.parent / "profile.sha256"
    marker.write_text("test-marker")
    module["_normalize_runtime_ownership"](tmp_path, profiles)
    assert (marker, "hermes", "hermes") in calls

    assert (cache, "hermes", "hermes") in calls


def test_specialist_profile_config_pins_the_working_set(tmp_path):
    """Every specialist's desired config carries the Specialist
    pinned tool list, so core-verb schemas stop paying tool_describe
    round-trips (2026-08-21 audit) while discovery stays deferred."""
    repo = _HERE.parents[1]
    module = runpy.run_path(str(repo / "docker/calliope/reconcile-hermes-specialists.py"))
    desired = module["_profile_config"]("dashboard-studio")
    pinned = desired["tools"]["tool_search"]["pinned"]
    common = list(module["SPECIALIST_PINNED_TOOLS"])
    assert pinned == module["specialist_pinned_tools"]("dashboard-studio")
    assert pinned[: len(common)] == common          # the common set is the stable, cacheable prefix; role extras follow
    assert "mcp__Specialist__capability_search" in pinned
    assert "mcp__Specialist__run_sql_multi" in pinned
    assert all(name.startswith("mcp__Specialist__") for name in pinned)


def test_operator_auxiliary_settings_survive_the_image_reconcile(tmp_path):
    """The auxiliary block (background-task lane: openrouter_model,
    free_only, per-task overrides) is a top-level section the model-block
    carry does not cover; under the model-operator marker it travels across
    a regenerated config exactly like the fallback model does."""
    repo = _HERE.parents[1]
    module = runpy.run_path(str(repo / "docker/calliope/reconcile-hermes-specialists.py"))
    profile = tmp_path / "profiles" / "dashboard-studio"
    profile.mkdir(parents=True)
    markers = tmp_path / ".managed"
    markers.mkdir(parents=True)
    (markers / module["MODEL_OPERATOR_MARKER"]).write_text("chosen\n", encoding="utf-8")
    (profile / "config.yaml").write_text(
        "model:\n  default: openai/gpt-5.6-sol\n  provider: openrouter\n"
        "auxiliary:\n  openrouter_model: qwen/qwen3-30b-a3b:free\n  free_only: true\n",
        encoding="utf-8",
    )
    module["_reconcile_config"](profile, module["_profile_config"]("dashboard-studio"), markers)
    payload = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert payload["auxiliary"] == {"openrouter_model": "qwen/qwen3-30b-a3b:free", "free_only": True}

    # Background routing is independent of the main-model ownership marker.
    (markers / module["MODEL_OPERATOR_MARKER"]).unlink()
    module["_reconcile_config"](profile, module["_profile_config"]("dashboard-studio"), markers)
    payload = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert payload["auxiliary"] == {"openrouter_model": "qwen/qwen3-30b-a3b:free", "free_only": True}


def test_specialist_background_default_and_explicit_automatic_survive_reconcile(tmp_path):
    module = runpy.run_path(str(_HERE.parents[1] / "docker/calliope/reconcile-hermes-specialists.py"))
    profile = tmp_path / "profiles" / "dashboard-studio"
    profile.mkdir(parents=True)
    markers = tmp_path / ".managed"
    markers.mkdir()
    desired = lambda: module["_profile_config"]("dashboard-studio")
    module["_reconcile_config"](profile, desired(), markers)
    config = yaml.safe_load((profile / "config.yaml").read_text())
    assert config["auxiliary"]["compression"]["fallback_chain"][0]["model"] == "calliope-luna"
    config["auxiliary"] = {}
    (profile / "config.yaml").write_text(yaml.safe_dump(config))
    module["_reconcile_config"](profile, desired(), markers)
    assert yaml.safe_load((profile / "config.yaml").read_text())["auxiliary"] == {}


def test_operator_reasoning_effort_survives_the_image_reconcile(tmp_path):
    """agent.reasoning_effort is a top-level key, so the model-block carry
    does not cover it; under the model-operator marker it must travel across
    a regenerated config exactly like the fallback model does. An invalid
    hand-edited value is dropped, not carried."""
    repo = _HERE.parents[1]
    module = runpy.run_path(str(repo / "docker/calliope/reconcile-hermes-specialists.py"))
    profile = tmp_path / "profiles" / "dashboard-studio"
    profile.mkdir(parents=True)
    markers = tmp_path / ".managed"
    markers.mkdir(parents=True)
    (markers / module["MODEL_OPERATOR_MARKER"]).write_text("chosen\n", encoding="utf-8")
    (profile / "config.yaml").write_text(
        "model:\n  default: openai/gpt-5.6-sol\n  provider: openrouter\n"
        "agent:\n  reasoning_effort: xhigh\n",
        encoding="utf-8",
    )
    module["_reconcile_config"](profile, module["_profile_config"]("dashboard-studio"), markers)
    payload = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert payload["model"]["default"] == "openai/gpt-5.6-sol"
    assert payload["agent"]["reasoning_effort"] == "xhigh"

    # A junk value does not survive the reconcile.
    (profile / "config.yaml").write_text(
        "model:\n  default: openai/gpt-5.6-sol\n  provider: openrouter\n"
        "agent:\n  reasoning_effort: galactic\n",
        encoding="utf-8",
    )
    module["_reconcile_config"](profile, module["_profile_config"]("dashboard-studio"), markers)
    payload = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert "agent" not in payload

    # Without the marker the appliance default wins (no agent block ships).
    (markers / module["MODEL_OPERATOR_MARKER"]).unlink()
    (profile / "config.yaml").write_text(
        "model:\n  default: openai/gpt-5.6-sol\n  provider: openrouter\n"
        "agent:\n  reasoning_effort: xhigh\n",
        encoding="utf-8",
    )
    module["_reconcile_config"](profile, module["_profile_config"]("dashboard-studio"), markers)
    payload = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert "agent" not in payload


def test_managed_copy_repairs_hollow_skill_copies(tmp_path):
    """2026-08-17: every specialist's SKILL.md on a hosted install was 0 bytes and the
    reconciler 'preserved' the empty tree as an operator edit for three days.
    Empty files where the source has content are a defect and get reseeded;
    a genuinely edited copy is still preserved."""
    repo = _HERE.parents[1]
    module = runpy.run_path(str(repo / "docker/calliope/reconcile-hermes-specialists.py"))
    source = tmp_path / "src" / "skill"
    (source / "references").mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: skill\n---\nreal body\n", encoding="utf-8")
    (source / "references" / "notes.md").write_text("notes\n", encoding="utf-8")
    target = tmp_path / "profile" / "skills" / "productivity" / "skill"
    marker = tmp_path / "profile" / ".calliope-managed" / "skill-skill.sha256"

    assert module["_managed_copy"](source, target, marker) == "seeded"
    # hollow it out the way the box was found: same names, zero bytes
    (target / "SKILL.md").write_text("", encoding="utf-8")
    (target / "references" / "notes.md").write_text("", encoding="utf-8")
    marker.write_text("stale-hash\n", encoding="utf-8")
    assert module["_managed_copy"](source, target, marker) == "repaired"
    assert (target / "SKILL.md").read_text(encoding="utf-8").endswith("real body\n")
    assert module["_managed_copy"](source, target, marker) == "updated"   # hashes agree again; idempotent copy
    # an operator's real edit is still theirs
    (target / "SKILL.md").write_text("---\nname: skill\n---\nmy own body\n", encoding="utf-8")
    assert module["_managed_copy"](source, target, marker) == "preserved"


def test_reconciler_drops_profile_shadow_copies_of_root_grants(tmp_path):
    """A profile copy of a root credential-pool row (same id) is a stale
    shadow of a rotating OAuth grant, never the operator's own key; the
    root stays authoritative and the profile keeps only rows the root lacks."""
    import json
    repo = _HERE.parents[1]
    module = runpy.run_path(str(repo / "docker/calliope/reconcile-hermes-specialists.py"))
    home = tmp_path
    (home / "auth.json").write_text(json.dumps({"credential_pool": {
        "xai-oauth": [{"id": "71ec70", "refresh_token": "fresh"}, {"id": "c8ea98"}],
    }}), encoding="utf-8")
    profile = home / "profiles" / "dashboard-studio"
    profile.mkdir(parents=True)
    (profile / "auth.json").write_text(json.dumps({"providers": {}, "credential_pool": {
        "xai-oauth": [{"id": "71ec70", "refresh_token": "stale"}],
        "openrouter": [{"id": "own1", "access_token": "sk-or-own"}],
    }}), encoding="utf-8")
    assert module["_drop_shadowed_grants"](home, profile) == ["xai-oauth"]
    after = json.loads((profile / "auth.json").read_text(encoding="utf-8"))
    assert "xai-oauth" not in after["credential_pool"]                # falls back to the root's live pair
    assert after["credential_pool"]["openrouter"][0]["id"] == "own1"  # the profile's own key survives
    assert module["_drop_shadowed_grants"](home, profile) == []       # idempotent


def test_specialist_profile_config_carries_the_operators_model_choice(tmp_path):
    """Models & agents writes a specialist's provider/model into its profile
    config plus a `model.operator-managed` marker; the image-upgrade reconciler
    must carry that block forward (like skill toggles) while every other
    appliance default still lands, and normalise it by the shared policy."""
    repo = _HERE.parents[1]
    module = runpy.run_path(str(repo / "docker/calliope/reconcile-hermes-specialists.py"))
    profile = tmp_path / "profiles" / "dashboard-studio"
    profile.mkdir(parents=True)
    markers = profile / ".calliope-managed"
    markers.mkdir()
    (profile / "config.yaml").write_text(
        "model:\n  default: openai/gpt-5.6-sol\n  provider: openrouter\n"
        "  base_url: ${env:RVBBIT_CLOVER_OPENAI_BASE_URL}\n  context_length: 200000\n"
        "compression:\n  protect_last_n: 2\n",
        encoding="utf-8",
    )
    # Without the marker the shipped Hutch default wins (unknown provenance).
    module["_reconcile_config"](profile, module["_profile_config"]("dashboard-studio"), markers)
    payload = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert payload["model"]["provider"] == "custom" and payload["model"]["default"] == "calliope"

    (profile / "config.yaml").write_text(
        "model:\n  default: openai/gpt-5.6-sol\n  provider: openrouter\n"
        "  base_url: ${env:RVBBIT_CLOVER_OPENAI_BASE_URL}\n  context_length: 200000\n",
        encoding="utf-8",
    )
    (markers / module["MODEL_OPERATOR_MARKER"]).write_text("chosen\n", encoding="utf-8")
    assert module["_reconcile_config"](profile, module["_profile_config"]("dashboard-studio"), markers) == "updated"
    payload = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert payload["model"] == {"default": "openai/gpt-5.6-sol", "provider": "openrouter",
                                "context_length": 200000, "max_tokens": 32768}   # stale Hutch binding dropped, budget kept (8192 moved forward)
    assert payload["compression"]["protect_last_n"] == 12                       # appliance defaults still land
    assert payload["mcp_servers"]["Specialist"]["url"].startswith("http://calliope:8765/mcp")
    # Idempotent on the second pass.
    assert module["_reconcile_config"](profile, module["_profile_config"]("dashboard-studio"), markers) == "current"


def test_failed_specialist_handoff_projects_a_surface_and_the_rail_shows_the_exchange():
    """2026-08-17: a dashboard delegation to Iris hit the 90 s MCP transport
    ceiling; Calliope saw only an error, built a second dashboard herself, and
    the notebook read as if she had done it alone. A broken handoff is part of
    the transcript: it projects, and the chat rail renders every exchange."""
    error_result = (
        '<untrusted_tool_result source="mcp__Roundtable__delegate_calliope_specialist">\n'
        "External data warning.\n\n"
        + json.dumps({"error": "MCP call failed: TimeoutError: MCP call timed out after 90.0s (configured timeout: 90.0s)"})
        + "\n</untrusted_tool_result>"
    )
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "delegate-timeout",
                "function": {
                    "name": "tool_call",
                    "arguments": json.dumps({
                        "name": "mcp__Roundtable__delegate_calliope_specialist",
                        "arguments": {
                            "specialist_key": "dashboard-studio",
                            "task": "Build and publish a polished interactive Live Quakes dashboard. Make the map the centerpiece.",
                        },
                    }),
                },
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "delegate-timeout",
            "tool_name": "mcp__Roundtable__delegate_calliope_specialist",
            "content": error_result,
        },
    ]
    projected = calliope.project_messages(messages)
    assert len(projected) == 1
    surface = projected[0]
    assert surface["kind"] == "action"
    assert surface["payload"]["mode"] == "specialist_handoff_failed"
    assert surface["payload"]["specialist"]["key"] == "dashboard-studio"
    assert surface["payload"]["task"].startswith("Build and publish a polished interactive Live Quakes dashboard")
    assert "timed out after 90.0s" in surface["payload"]["error"]
    assert surface["payload"]["timed_out"] is True
    assert surface["source"]["handoff_failed"] is True
    assert surface["presentation"]["view"] == "specialist_handoff_failed"

    # other tools' errors are still not surfaces
    other = dict(messages[1], tool_name="mcp__Warehouse__run_sql")
    other_call = dict(messages[0], tool_calls=[{"id": "delegate-timeout", "function": {"name": "tool_call", "arguments": json.dumps({"name": "mcp__Warehouse__run_sql", "arguments": {"sql": "select 1"}})}}])
    assert calliope.project_messages([other_call, other]) == []

    script = (calliope._ASSET_DIR / "calliope.js").read_text(encoding="utf-8")
    css = (calliope._ASSET_DIR / "calliope.css").read_text(encoding="utf-8")
    # the rail renders every specialist exchange between the user's message and Calliope's reply
    assert "${specialistExchangeMarkup(turn)}" in script
    assert "function specialistExchangesForTurn(turnId)" in script
    assert 'Calliope → <b class="handoff-name">' in script and "→ Calliope · <em class=\"handoff-status\">" in script
    assert 'data-toggle-handoff' in script and "Show the whole message" in script
    # a reply that landed after the parent's tool call failed lives only in the thread ledger
    assert "state.current?.specialist_threads" in script and "ledger?.latest_reply" in script
    assert 'if (payload.mode === "specialist_handoff_failed") return handoffFailedStageMarkup(surface);' in script
    assert "timed out before" in script and "carried on without the reply" in script
    assert ".message-exchange{" in css and ".handoff-body.is-collapsed{" in css and ".specialist-handoff-failed{" in css


def test_specialist_thread_projection_keeps_the_reply_past_the_evidence_bound():
    """Iris' Live Quakes run had 36+ tool events before her reply; the bounded
    snapshot kept the first 36 transcript rows and dropped the reply, so the
    Stage card showed only Calliope's assignment until the thread was
    re-fetched. Messages travel in transcript, tool events in tool_activity."""
    tool_rows = [
        {"role": "tool", "kind": "tool_started" if i % 2 == 0 else "tool_completed",
         "text": "mcp__Specialist__run_sql", "round": 1, "ordinal": i + 2}
        for i in range(40)
    ]
    detail = {
        "thread_id": "41502968-0490-42b2-83c0-15d82c86e150",
        "run_id": "0cd38f1d-1ffc-4c40-b59d-aac0de777ef4",
        "round_count": 1,
        "status": "complete",
        "specialist": {"key": "dashboard-studio", "name": "Iris", "profile": "dashboard-studio"},
        "transcript": [
            {"role": "calliope", "kind": "handoff", "text": "Build the Live Quakes dashboard.", "round": 1, "ordinal": 1},
            *tool_rows,
            {"role": "specialist", "kind": "assistant", "text": "Published private Live App: /apps/live-quakes-global-pulse", "round": 1, "ordinal": 99},
        ],
        "tool_activity": [{"event": "tool.completed", "tool_name": "run_sql", "round": 1} for _ in range(40)],
        "stage_surfaces": [],
    }
    projection = calliope._specialist_thread_projection(detail)
    transcript = projection["payload"]["transcript"]
    assert [entry["role"] for entry in transcript] == ["calliope", "specialist"]
    assert transcript[-1]["text"].startswith("Published private Live App")
    assert len(projection["payload"]["tool_activity"]) == calliope._MAX_EVIDENCE_RESULTS   # events still bounded
    assert projection["source"]["projection_source"] == "durable_specialist_ledger"

    # the client fills a persisted receipt that lacks the reply from the thread ledger,
    # for the Stage card and the rail alike
    script = (calliope._ASSET_DIR / "calliope.js").read_text(encoding="utf-8")
    assert "borrows them so the card never shows only Calliope's side" in script
    assert 'ledger?.latest_reply' in script
    # "On the Stage" / "Open conversation" inside Stage cards must work on the Stage, not only in the rail
    stage_handler = script.split('els.stage.addEventListener("click", (event) => {', 1)[1]
    assert 'closest("[data-focus-surface]")' in stage_handler.split("const commandUse", 1)[0]
    assert 'closest("[data-open-specialist-thread]")' in stage_handler.split("const commandUse", 1)[0]
    assert "if (isArtifactCaptureCompanion(surface)) {" in script.split("function focusSurface(id)", 1)[1].split("function ", 1)[0]


def test_specialist_ui_uses_compact_chat_and_full_stage_child_thread():
    script = (calliope._ASSET_DIR / "calliope.js").read_text(encoding="utf-8")
    css = (calliope._ASSET_DIR / "calliope.css").read_text(encoding="utf-8")
    assert '"specialist_thread"' in script
    assert "specialistRunView" in script
    assert "specialistRunMarkup" in script
    assert "agent.role_label || supplied.role_label" in script
    assert "SPECIALIST_ROLE_LABELS" not in script
    assert 'event === "calliope.specialist"' in script
    assert "applyLiveSpecialistEvent" in script
    assert "noteLiveSpecialistSurfaces" in script
    assert "syncLiveSpecialistSurface" in script
    assert "reconcileSpecialistRunSurfaces" in script
    assert 'data-open-specialist-run' in script
    assert 'data-open-specialist-thread' in script
    assert 'data-specialist-thread-anchor' in script
    assert "/api/calliope/specialist-threads/" in script
    assert '["calliope", "specialist"].includes(message.role)' in script
    assert "calliopeAgentAvatarMarkup" in script
    assert "specialistAgentAvatarMarkup" in script
    assert ".specialist-thread-compact" in css
    assert ".specialist-thread-stage" in css
    assert ".specialist-thread-message.specialist" in css
    assert ".session-agent-threads" in css
    assert ".agent-conversation-avatar" in css


def test_fresh_notebook_agent_picker_is_rich_delegated_and_above_the_composer():
    page = (calliope._ASSET_DIR / "index.html").read_text(encoding="utf-8")
    script = (calliope._ASSET_DIR / "calliope.js").read_text(encoding="utf-8")
    css = (calliope._ASSET_DIR / "calliope.css").read_text(encoding="utf-8")

    assert 'id="chat-agent-picker-menu"' in page
    assert 'role="listbox"' in page
    assert 'class="chat-agent-placeholder"' in page
    assert 'class="chat-agent-option-copy"' in script
    assert "agent.description || chatAgentRole(agent)" in script
    assert 'els.chatAgentPickerMenu?.addEventListener("click"' in script
    assert 'event.target.closest("[data-chat-agent]")' in script
    assert 'body: JSON.stringify({ agent_key: agentKey })' in script
    assert 'turn.turn_kind !== "sketch_bootstrap"' in script
    assert ".calliope-head{position:relative;z-index:90" in css


def test_active_turns_are_owned_by_notebooks_instead_of_the_visible_chat():
    script = (calliope._ASSET_DIR / "calliope.js").read_text(encoding="utf-8")
    css = (calliope._ASSET_DIR / "calliope.css").read_text(encoding="utf-8")
    assert "activeRuns: new Map()" in script
    assert "activeRunProjection" in script
    assert "runOwnsVisibleNotebook" in script
    assert "refreshSessionRailOnly" in script
    assert "state.activeRuns.set(sessionId, runState)" in script
    assert "state.activeRuns.delete(sessionId)" in script
    assert "session-live-run" in script
    assert ".session-live-run" in css


def test_turn_event_ledger_keeps_lifecycle_but_not_token_or_surface_bodies():
    assert calliope._compact_turn_stream_event(
        b'event: assistant.delta\ndata: {"delta":"secret draft"}\n\n'
    ) is None
    event, payload = calliope._compact_turn_stream_event(
        b'event: calliope.surfaces\ndata: {"turn_id":"t","surfaces":'
        b'[{"id":"s","kind":"query","title":"Evidence","payload":{"rows":[1,2,3]}}]}\n\n'
    )
    assert event == "calliope.surfaces"
    assert payload == {
        "turn_id": "t",
        "specialist_run_id": None,
        "surfaces": [{"id": "s", "kind": "query", "title": "Evidence"}],
    }

    repo = _HERE.parents[1]
    migration = (
        repo / "crates/pg_rvbbit/sql/migrations/0304_calliope_turn_events.sql"
    ).read_text(encoding="utf-8")
    assert "calliope_turn_events" in migration
    assert "PRIMARY KEY (turn_id,sequence)" in migration


def test_acceleration_steward_is_persistent_scheduled_and_receipt_backed():
    repo = _HERE.parents[1]
    manifest = json.loads(
        (repo / "docker/calliope/hermes-specialists/manifest.json").read_text()
    )
    profiles = {item["key"]: item for item in manifest["profiles"]}
    steward = profiles["warehouse-steward"]
    assert steward["memory_bank"] == "calliope-warehouse-steward"
    assert steward["cron"]["schedule"] == "17 2 * * *"
    assert steward["cron"]["skill"] == "calliope-acceleration-steward"
    assert "Do not wait for human approval" in steward["cron"]["prompt"]

    skill = (
        repo
        / "docker/calliope/hermes-skills/calliope-acceleration-steward/SKILL.md"
    ).read_text(encoding="utf-8")
    assert "acceleration_steward_snapshot" in skill
    assert "incremental-upsert mirrors" in skill
    assert "snapshot/truncate-load mirrors cautiously" in skill
    assert "heap fallback" in skill
    assert "`k=3` first" in skill
    assert "Do not call the snapshot again" in skill

    migration = (
        repo / "crates/pg_rvbbit/sql/migrations/0308_acceleration_steward.sql"
    ).read_text(encoding="utf-8")
    assert "acceleration_steward_decisions" in migration
    assert "system_learning_stewarded_items" in migration
    assert "rvbbit-system-learning" in migration
    assert '"0308_acceleration_steward"' in (
        repo / "crates/pg_rvbbit/src/migrations.rs"
    ).read_text(encoding="utf-8")

    reconciler = (
        repo / "docker/calliope/reconcile-hermes-specialists.py"
    ).read_text(encoding="utf-8")
    assert "def _reconcile_cron" in reconciler
    assert "use_cron_store(profile)" in reconciler
    assert '"attach_to_session": False' in reconciler
    assert '{"API_SERVER_ENABLED": "false"}' in reconciler
    policy = runpy.run_path(str(repo / "docker/calliope/reconcile-hermes-specialists.py"))
    platforms = policy["_profile_config"]("warehouse-steward")["platforms"]
    assert platforms["api_server"]["enabled"] is False
    assert platforms["google_chat"]["enabled"] is False


def test_acceleration_receipt_normalizes_model_evidence_to_json_objects():
    normalize = server._acceleration_steward_json_object
    assert normalize(None, label="value") == {}
    assert normalize({"calls": 43}, label="value") == {"calls": 43}
    assert normalize('{"calls": 43}', label="value") == {"calls": 43}
    assert normalize(["snapshot", "hourly"], label="value") == {
        "items": ["snapshot", "hourly"]
    }
    assert normalize("snapshot mirror", label="value") == {
        "value": "snapshot mirror"
    }


def test_extended_specialist_team_has_private_memory_cron_and_shared_findings():
    repo = _HERE.parents[1]
    manifest = json.loads(
        (repo / "docker/calliope/hermes-specialists/manifest.json").read_text()
    )
    profiles = {item["key"]: item for item in manifest["profiles"]}
    expected = {
        "semantic-architect",
        "signal-watch",
        "knowledge-gardener",
        "analytical-skeptic",
    }
    assert expected <= set(profiles)
    for key in expected:
        assert profiles[key]["memory_bank"] == f"calliope-{key}"
        assert (
            repo / f"docker/calliope/hermes-specialists/{key}/SOUL.md"
        ).is_file()
        assert (
            repo / f"docker/calliope/hermes-skills/calliope-{key}/SKILL.md"
        ).is_file()

    assert profiles["semantic-architect"]["cron"]["schedule"] == "41 3 * * *"
    assert profiles["signal-watch"]["cron"]["schedule"] == "15 11 * * *"
    assert profiles["knowledge-gardener"]["cron"]["schedule"] == "53 4 * * *"
    assert "cron" not in profiles["analytical-skeptic"]
    assert "calliope-integration-scout" in profiles["warehouse-steward"]["skills"]

    migration = (
        repo / "crates/pg_rvbbit/sql/migrations/0309_calliope_specialist_team.sql"
    ).read_text(encoding="utf-8")
    assert "calliope_specialist_findings" in migration
    assert "system_learning_collaborative_items" in migration
    assert "system_learning_stewarded_items" in migration
    assert '"0309_calliope_specialist_team"' in (
        repo / "crates/pg_rvbbit/src/migrations.rs"
    ).read_text(encoding="utf-8")

    source = Path(server.__file__).read_text(encoding="utf-8")
    for tool in (
        "record_specialist_finding",
        "list_specialist_findings",
        "resolve_specialist_finding",
    ):
        assert f"def tool_{tool}" in source
        assert f'mcp.tool(name="{tool}")' in source


def test_hosted_scheduler_seeds_five_accel_and_five_unified_layout_slots():
    repo = _HERE.parents[1]
    for relative in (
        "docker/init/03-seed-scheduled-tasks.sh",
        "docker/uber/bootstrap.sh",
    ):
        source = (repo / relative).read_text(encoding="utf-8")
        assert "generate_series(1,5)" in source
        assert "accel_tick_worker_pass(%s, 5, 4, 1)" in source
        assert "layout_tick_worker_pass(%s, 5, 4)" in source
        assert "rvbbit_olap_autopilot" in source  # legacy cleanup only
        assert "active=true" in source


def test_governed_metric_and_cube_creation_are_not_ui_approval_gated():
    source = Path(server.__file__).read_text(encoding="utf-8")
    for tool in ("accept_proposal", "create_cube", "create_metric"):
        assert f'def tool_{tool}' in source
        assert f'mcp.tool(name="{tool}")' in source
    assert "no separate UI approval is required" in source
    assert "No DataRabbit approval ceremony is required" in source
    assert "No UI approval is required" in source


def test_exploratory_dashboards_do_not_supply_a_default_style():
    repo = _HERE.parents[1]
    blueprint = (
        repo
        / "docker/calliope/hermes-skills/calliope-live-dashboards/references/exploratory-blueprint.md"
    ).read_text(encoding="utf-8")
    assert "Current state" in blueprint
    assert "Movement" in blueprint
    assert "Contributors" in blueprint
    assert "Exact evidence" in blueprint
    assert "Only an explicitly pinned Design Profile" in blueprint
    assert "house style" in blueprint
    assert "A result is a table unless" in blueprint

    server_source = Path(server.__file__).read_text(encoding="utf-8")
    delegate = server_source.split("effective_design_profile_version_id = source.get", 1)[1][:1200]
    assert "_ADAPTIVE_DESIGN_PROFILE_VERSION_ID" not in delegate
    assert "cascades as-is" in server_source


def test_specialist_roster_is_generated_from_the_database_with_static_fallback(monkeypatch):
    """Pausing or adding a specialist must change what Calliope is told:
    the roster line and the preference paragraph are built from
    rvbbit.calliope_specialists at registration, with the hand-written text as
    the DB-unavailable fallback and per-key hand-written sentences preserved."""
    # static assembly is byte-stable: the constants ARE the shipped text
    assert server._SPECIALIST_ROSTER_STATIC in server._INSTRUCTIONS
    assert server._SPECIALIST_PREFERENCES_STATIC in server._INSTRUCTIONS
    assert set(server._SPECIALIST_PREFERENCE_SENTENCES) == set(server._SPECIALIST_ROSTER_ORDER)

    # no database (this process) → live variants are the static text
    monkeypatch.setattr(server, "_specialist_roster_rows", lambda: [])
    assert server._instructions_current() == server._INSTRUCTIONS
    assert server._delegate_doc_current() == server._DELEGATE_DOC_STATIC

    # a changed roster: Iris paused, a new colleague added
    rows = [
        {"specialist_key": "warehouse-steward", "display_name": "Hestia",
         "description": "Warehouse Steward — diagnoses and repairs the appliance."},
        {"specialist_key": "story-editor", "display_name": "Melete",
         "description": "Story Editor — turns dashboards into decision narratives."},
    ]
    monkeypatch.setattr(server, "_specialist_roster_rows", lambda: rows)
    live = server._instructions_current()
    assert ("The managed roster is known: Hestia is Warehouse Steward with specialist_key "
            "warehouse-steward; Melete is Story Editor with specialist_key story-editor.") in live
    assert "Strongly prefer Hestia, the Warehouse Steward" in live
    assert ("Prefer Melete, the Story Editor (specialist_key story-editor): turns dashboards "
            "into decision narratives.") in live
    assert "Iris is Dashboard Studio" not in live          # paused agents disappear
    assert "Strongly prefer Iris" not in live
    assert "AN EXPLICIT USER PROCESS ALWAYS WINS" in live  # invariant text untouched
    assert "delegation is step zero" in live

    doc = server._delegate_doc_current()
    assert "Strongly prefer Hestia, the Warehouse Steward" in doc
    assert "Strongly prefer Iris" not in doc
    assert "Delegate directly by known key" in doc         # invariant tail kept
    assert "an explicit user process always wins" in " ".join(doc.split())
    # idempotent: works from the pristine captured doc, not the patched one
    assert server._delegate_doc_current() == doc

    src = Path(server.__file__).read_text(encoding="utf-8")
    assert src.count("instructions=_instructions_current(),") == 2
    assert "_mcp_delegate_calliope_specialist.__doc__ = _delegate_doc_current()" in src


def test_agent_model_changes_apply_as_one_batch_with_one_restart():
    """The Models UI accumulates edits and applies them together: specialists
    ride the supervisor's batch endpoint (one verified restart), the default
    rides the revision controller, and the route takes a changes[] list."""
    src = Path(server.__file__).parent.joinpath("provider_settings.py").read_text(encoding="utf-8")
    assert "def set_agent_models(" in src and '"/v1/profile/models"' in src
    supervisor = _HERE.parents[1].joinpath("docker/calliope/hermes-gateway-supervisor.py").read_text(encoding="utf-8")
    assert "def set_profile_models(" in supervisor and '"/v1/profile/models"' in supervisor
    calliope_src = Path(server.__file__).parent.joinpath("calliope.py").read_text(encoding="utf-8")
    assert 'isinstance((body or {}).get("changes"), list)' in calliope_src
    js = Path(server.__file__).parent.joinpath("calliope/calliope.js").read_text(encoding="utf-8")
    assert "pendingAgentChanges" in js
    assert "data-agents-apply-all" in js and "data-agents-discard" in js and "data-agent-revert" in js
    assert "data-agent-apply=" not in js                    # per-row apply (one restart each) is gone
    assert "Agent models, Background Tasks, and Image generation are validated and written as one batch" in js
    assert "data-auxiliary-apply" not in js and "data-auxiliary-revert" in js
    # searchable model picker replaces the monolithic <select>
    assert "modelPickerMarkup" in js and "data-model-picker-input" in js and "data-model-search" in js
    assert "data-agent-model=" not in js
    # appliance-wide fallback + named loadouts ride the same batched machinery
    assert "data-fallback-apply" in js and "data-fallback-clear" in js
    assert "data-loadout-apply" in js and "data-loadout-save" in js and "data-loadout-delete" in js
    assert '"/api/calliope/admin/providers/fallback"' in js
    src_ps = Path(server.__file__).parent.joinpath("provider_settings.py").read_text(encoding="utf-8")
    assert "def set_fallback_model(" in src_ps and "def apply_loadout(" in src_ps
    assert '"/v1/profile/fallback"' in src_ps
    policy = _HERE.parents[1].joinpath("docker/calliope/reconcile-hermes-config.py").read_text(encoding="utf-8")
    assert "def apply_fallback_model(" in policy and "def carry_fallback_model(" in policy
    reconciler = _HERE.parents[1].joinpath("docker/calliope/reconcile-hermes-specialists.py").read_text(encoding="utf-8")
    assert "carry_fallback_model" in reconciler             # operator fallback survives image upgrades
    assert "calliope_model_loadouts" in Path(server.__file__).parent.joinpath("calliope.py").read_text(encoding="utf-8")


def test_session_sketch_crosses_the_delegation_handoff(tmp_path):
    """A layout mockup drawn on the session Sketch reaches the specialist as
    an image part with provenance — fresh sketches ride along automatically,
    stale ones only when the caller asks (context.include_sketch)."""
    import base64 as b64
    import types

    png = b"\x89PNG\r\n\x1a\n" + b"sketchbytes"
    sketches = tmp_path / "sketches"
    sketches.mkdir()
    preview = sketches / "sketch.png"
    preview.write_bytes(png)
    config = types.SimpleNamespace(file_root=tmp_path)
    row = {"revision": 4, "preview_revision": 4, "preview_path": str(preview),
           "last_actor": "calliope", "age_minutes": 3.2}

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def execute(self, sql, params=()): return self
        def fetchone(self): return dict(row)

    part, meta = calliope._session_sketch_image_part(lambda: Conn(), config, "s-1")
    assert part["image_url"]["url"].endswith(b64.b64encode(png).decode())
    assert meta["revision"] == 4 and meta["last_actor"] == "calliope"
    assert "design intent" in meta["note"]

    row["age_minutes"] = 180.0        # stale: skipped by default…
    assert calliope._session_sketch_image_part(lambda: Conn(), config, "s-1") is None
    # …but included when explicitly requested (no age bound)
    assert calliope._session_sketch_image_part(lambda: Conn(), config, "s-1", max_age_minutes=None) is not None

    row.update(age_minutes=1.0, revision=1, preview_revision=1)
    assert calliope._session_sketch_image_part(lambda: Conn(), config, "s-1") is None  # bootstrap canvas
    row.update(revision=5)            # un-rendered newest revision: never send a stale picture
    assert calliope._session_sketch_image_part(lambda: Conn(), config, "s-1") is None

    src = Path(server.__file__).read_text(encoding="utf-8")
    assert "_session_sketch_image_part(" in src
    assert "(clean_context.get('provided') or {}).get('include_sketch')" in src
    assert "clean_context.setdefault('current_turn', {})['sketch'] = sketch[1]" in src


def test_live_specialist_lookup_returns_new_domain_agents(monkeypatch):
    from types import SimpleNamespace

    rows = [{"specialist_key": "generated-retention-expert", "display_name": "Nova",
             "profile_name": "generated-retention-expert", "status": "active",
             "managed": False, "capabilities": ["retention-analysis"]}]
    statements = []

    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, sql):
            statements.append(sql)
            return SimpleNamespace(fetchall=lambda: rows)

    monkeypatch.setattr(server, "_require_application_subject", lambda: "viewer@example.com")
    monkeypatch.setattr(server, "_conn", Connection)
    monkeypatch.setattr(server, "_logged", lambda name, args, fn: fn())
    result = server._mcp_list_calliope_specialists()
    assert result["specialists"][0]["specialist_key"] == "generated-retention-expert"
    assert "WHERE status='active'" in statements[0]
    assert "managed=" not in statements[0]
    assert "specialist_key IN" not in statements[0]


def test_specialist_workers_keep_native_delegation_after_coordinator_restriction():
    module = runpy.run_path(str(_HERE.parents[1] / "docker/calliope/reconcile-hermes-specialists.py"))
    for key in ("dashboard-studio", "generated-retention-expert"):
        lane = module["_profile_config"](key)["platform_toolsets"]["api_server"]
        assert "delegation" in lane
        assert "Specialist" in lane
        assert "Roundtable" not in lane


def test_generated_signature_fonts_are_persistent_and_preserve_custom_identity(tmp_path, monkeypatch):
    module = runpy.run_path(str(_HERE.parents[1] / 'docker/calliope/reconcile-hermes-specialists.py'))
    choose = module['_generated_signature_font']
    backfill = module['_backfill_generated_signature_font']
    monkeypatch.setattr(module['secrets'], 'choice', lambda pool: 'My Soul')
    target = tmp_path / 'profile.yaml'
    source = tmp_path / 'seed.yaml'
    marker = tmp_path / 'profile.sha256'
    assert choose(target) == 'My Soul'
    original = {'name': 'Aletheia', 'description': 'Custom role', 'avatar_file': 'custom.png',
                'signature_font': 'Italiana', 'custom_field': {'keep': True}}
    target.write_text(yaml.safe_dump(original))
    source.write_text(yaml.safe_dump({**original, 'signature_font': choose(target)}))
    marker.write_text('operator-edited-profile')
    backfill(source, target, marker)
    assert yaml.safe_load(target.read_text()) == {**original, 'signature_font': 'My Soul'}
    assert marker.read_text() == 'operator-edited-profile'
    before = target.read_bytes()
    monkeypatch.setattr(module['secrets'], 'choice', lambda pool: 'Rye')
    assert choose(target) == 'My Soul'
    backfill(source, target, marker)
    assert target.read_bytes() == before
    target.write_text(yaml.safe_dump({**original, 'signature_font': 'Custom Font'}))
    assert choose(target) == 'Custom Font'
    backfill(source, target, marker)
    assert yaml.safe_load(target.read_text())['signature_font'] == 'Custom Font'
    assert len(module['GENERATED_SIGNATURE_FONTS']) == 18
    assert all('+' not in font for font in module['GENERATED_SIGNATURE_FONTS'])


def test_pinned_tools_follow_the_bridge_histogram_and_stay_under_the_cap():
    repo = _HERE.parents[1]
    specialists = runpy.run_path(str(repo / "docker/calliope/reconcile-hermes-specialists.py"))
    config = runpy.run_path(str(repo / "docker/calliope/reconcile-hermes-config.py"))
    cap = 48   # hermes-pinned-tools.patch: ToolSearchConfig stops reading pins at 48
    assert "if len(pinned) >= 48:" in (repo / "docker/calliope/hermes-pinned-tools.patch").read_text()
    common = list(specialists["SPECIALIST_PINNED_TOOLS"])
    pins = specialists["specialist_pinned_tools"]
    for role in ("warehouse-steward", "knowledge-gardener", "dashboard-studio", "signal-watch", "analytical-skeptic", "semantic-architect"):
        lane = pins(role)
        assert len(lane) == len(set(lane)) <= cap, (role, len(lane))
        assert lane[: len(common)] == common, role                          # the common set is the stable prefix
        assert set(specialists["SPECIALIST_ROLE_PINNED_TOOLS"][role]) <= set(lane)
    assert pins("commercial-orders") == common                              # a generated domain agent: the common set only
    assert pins("design-red-team") == list(specialists["DESIGN_RED_TEAM_PINNED_TOOLS"])   # Nemesis keeps her short list
    assert specialists["_profile_config"]("warehouse-steward")["tools"]["tool_search"]["pinned"] == pins("warehouse-steward")
    for must in ("find", "meaning_tree", "teach_meaning", "record_specialist_finding", "ask_brain", "administer_local_sql"):
        assert f"mcp__Specialist__{must}" in common, must
    assert "mcp__Specialist__compare" in pins("signal-watch") and "mcp__Specialist__compare" not in pins("dashboard-studio")
    default = config["PINNED_TOOLS_DEFAULT"]
    assert len(default) == len(set(default)) <= cap
    for must in ("begin_calliope_workflow_run", "finish_calliope_workflow_run", "mirror_status", "administer_local_sql", "ask_brain", "teach_meaning"):
        assert f"mcp__Warehouse__{must}" in default, must
    assert "mcp__Warehouse__meaning_tree" in default
    previous = config["PINNED_TOOLS_PREVIOUS_DEFAULTS"]                    # every list a box may carry today, so the reconciler swaps it
    assert len(previous[0]) == 42 and "mcp__Warehouse__meaning_tree" not in previous[0] and "mcp__Warehouse__mirror_status" in previous[0]
    assert len(previous[1]) == 29 and "mcp__Warehouse__teach_meaning" in previous[1] and "mcp__Warehouse__administer_local_sql" not in previous[1]
    text = (repo / "docker/calliope/reconcile-hermes-specialists.py").read_text()
    assert "never as an opening" in text and "inherit Calliope's full Warehouse capability library" in text
    for role in ("analytical-skeptic", "dashboard-studio", "knowledge-gardener", "semantic-architect", "signal-watch"):
        soul = (repo / "docker/calliope/hermes-specialists" / role / "SOUL.md").read_text()
        assert "Capability bearings" not in soul and "Bearings:" in soul, role



def _generated_reconciler(monkeypatch, tmp_path, baked_key="dashboard-studio"):
    """The specialists reconciler wired to the repo's baked sources with a
    one-profile manifest, no learned overlays, and a private staging root,
    so reconcile() can run end-to-end against tmp_path as HERMES_HOME."""
    repo = _HERE.parents[1]
    monkeypatch.setenv("CALLIOPE_HERMES_SEED_ROOT", str(repo / "docker/calliope"))
    monkeypatch.setenv("CALLIOPE_HERMES_SPECIALISTS_ROOT", str(repo / "docker/calliope/hermes-specialists"))
    monkeypatch.setenv("CALLIOPE_HERMES_SKILLS_ROOT", str(repo / "docker/calliope/hermes-skills"))
    monkeypatch.setenv("WAREHOUSE_HERMES_MCP_KEY", "hermes-mcp-key")
    module = runpy.run_path(str(repo / "docker/calliope/reconcile-hermes-specialists.py"))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"profiles": [{
        "key": baked_key, "memory_bank": f"calliope-{baked_key}",
        "memory_mission": "Retain durable studio knowledge.", "skills": [],
    }]}), encoding="utf-8")
    module["MANIFEST"] = manifest
    module["_fetch_agent_overlays"] = lambda _home: {}
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(module["tempfile"], "gettempdir", lambda: str(staging))
    return module


def _roster_server(monkeypatch, payload):
    """Serve `payload` from urllib for the reconciler's roster fetch, or
    fail the fetch when payload is None (Calliope unreachable at boot)."""
    import urllib.request

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read():
            return json.dumps(payload).encode("utf-8")

    def urlopen(request, timeout=None):
        if payload is None:
            raise OSError("calliope is not up yet")
        assert request.get_header("Authorization") == "Bearer hermes-mcp-key"
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


def test_generated_specialist_reconciler_archives_retired_profiles_and_never_resurrects_them(tmp_path, monkeypatch):
    """Hermes 0.21.4 serves every directory under profiles/ and each one costs
    boot time, so a retired generated profile must leave the served tree: it
    moves to profiles-archive/<key>-<UTC stamp>/ (same volume, kept). Only a
    key the live roster names as retired moves; a baked manifest key never
    does, an operator's own directory (unknown to the table) never does, and
    a failed fetch (cache fallback) never archives anything. Because retired
    keys are absent from `specialists`, neither the durable cache nor the
    manifest path re-stages an archived key on a later boot."""
    module = _generated_reconciler(monkeypatch, tmp_path)
    home = tmp_path
    active = {
        "specialist_key": "commercial-orders", "display_name": "Commerce",
        "role_label": "Commercial Orders", "description": "Knows the order book.",
        "soul_markdown": "# Commerce\n\nYou know the commercial order book.",
        "memory_mission": "Retain order-book knowledge.", "skills": ["calliope-domain-expert"],
    }
    roster = {"specialists": [active],
              "retired": ["offense-mix", "reported-incidents", "dashboard-studio", "never-existed"]}
    for key in ("offense-mix", "operator-own", "dashboard-studio"):
        (home / "profiles" / key / "sessions").mkdir(parents=True)
        (home / "profiles" / key / "sessions" / "state.db").write_text(f"{key} state", encoding="utf-8")

    # Pass 1: a live roster. The retired generated dir is archived, the
    # unknown operator dir and the baked profile stay, the active one stages.
    _roster_server(monkeypatch, roster)
    actions = module["reconcile"](home)
    assert "offense-mix:generated:archived" in actions
    assert "commercial-orders:generated:staged" in actions
    assert not any(a.endswith(":generated:archived") for a in actions if not a.startswith("offense-mix:"))
    assert not (home / "profiles" / "offense-mix").exists()
    archived = sorted((home / "profiles-archive").iterdir())
    assert len(archived) == 1
    assert re.fullmatch(r"offense-mix-\d{8}T\d{6}Z", archived[0].name), archived[0].name
    assert (archived[0] / "sessions" / "state.db").read_text(encoding="utf-8") == "offense-mix state"
    assert (home / "profiles" / "operator-own" / "sessions" / "state.db").is_file()
    assert (home / "profiles" / "dashboard-studio" / "sessions" / "state.db").is_file()
    assert (home / "profiles" / "dashboard-studio" / "config.yaml").is_file()
    assert (home / "profiles" / "commercial-orders" / "SOUL.md").is_file()
    cache = json.loads((home / ".calliope-seeds" / "generated-specialists.json").read_text(encoding="utf-8"))
    assert cache["retired"] == roster["retired"]               # write what you fetched
    assert [i["specialist_key"] for i in cache["specialists"]] == ["commercial-orders"]

    # Pass 2: Calliope unreachable. The cache keeps the active profile
    # staged, nothing re-stages the archived key, and a retired key that
    # (re)appears on disk is NOT archived from a failed fetch.
    (home / "profiles" / "reported-incidents").mkdir()
    (home / "profiles" / "reported-incidents" / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    _roster_server(monkeypatch, None)
    actions = module["reconcile"](home)
    assert "commercial-orders:generated:staged" in actions
    assert not any(":generated:archived" in a for a in actions)
    assert not any(a.startswith("offense-mix:") for a in actions)
    assert not (home / "profiles" / "offense-mix").exists()
    assert (home / "profiles" / "reported-incidents").is_dir()
    assert len(list((home / "profiles-archive").iterdir())) == 1
    assert module["_fetch_generated_specialists"](home) == [active]   # the supervisor's contract, from the cache

    # Pass 3: live again. The lingering retired dir archives now; the one
    # archived earlier produces no action (nothing on disk to move).
    _roster_server(monkeypatch, roster)
    actions = module["reconcile"](home)
    assert "reported-incidents:generated:archived" in actions
    assert not any(a.startswith("offense-mix:") for a in actions)
    assert not (home / "profiles" / "reported-incidents").exists()
    assert sorted(p.name.rsplit("-", 1)[0] for p in (home / "profiles-archive").iterdir()) == [
        "offense-mix", "reported-incidents",
    ]
    assert (home / "profiles" / "operator-own" / "sessions" / "state.db").is_file()
    assert (home / "profiles" / "dashboard-studio" / "sessions" / "state.db").is_file()


def test_generated_specialist_roster_helpers_validate_keys_and_keep_the_supervisor_contract(tmp_path):
    repo = _HERE.parents[1]
    module = runpy.run_path(str(repo / "docker/calliope/reconcile-hermes-specialists.py"))
    roster = {
        "specialists": [{"specialist_key": "offense-mix"}, {"specialist_key": "Bad Key"}, "junk"],
        "retired": ["sales-order-book", {"specialist_key": "payment-settlement"}, "../etc", "sales-order-book", 7],
    }
    assert module["_generated_active_items"](roster) == [{"specialist_key": "offense-mix"}]
    assert module["_generated_retired_keys"](roster) == ["sales-order-book", "payment-settlement"]
    assert module["_generated_retired_keys"]({"specialists": []}) == []      # pre-rename cache shape
    # No profile directory: nothing to archive, no archive root created.
    assert module["_archive_generated_profile"](tmp_path, "sales-order-book") is None
    assert not (tmp_path / "profiles-archive").exists()
    (tmp_path / "profiles" / "sales-order-book").mkdir(parents=True)
    first = module["_archive_generated_profile"](tmp_path, "sales-order-book")
    (tmp_path / "profiles" / "sales-order-book").mkdir()
    second = module["_archive_generated_profile"](tmp_path, "sales-order-book")
    assert first is not None and second is not None and first != second       # a same-second twin gets a suffix
    assert first.parent == second.parent == tmp_path / "profiles-archive"


def test_specialist_profile_config_normalizes_legacy_lane_names_before_converging(tmp_path):
    """Profile configs written before the 2026-09-23 lane rename still say
    mcp__WarehouseSpecialist__* / mcp__WarehouseRoundtable__* in their pins
    and WarehouseSpecialist / WarehouseRoundtable in mcp_servers and toolset
    lists. The reconciler rewrites those names in place first: an
    operator-managed document keeps every choice under the new names (order
    preserved, a rewrite colliding with an existing new name is dropped, the
    legacy mcp_servers entry yields to an existing new key), and a managed
    document simply converges on the shipped names."""
    repo = _HERE.parents[1]
    module = runpy.run_path(str(repo / "docker/calliope/reconcile-hermes-specialists.py"))
    profile = tmp_path / "profiles" / "dashboard-studio"
    profile.mkdir(parents=True)
    markers = profile / ".calliope-managed"
    markers.mkdir()
    (markers / "config.user-managed").write_text("saved from DataRabbit\n", encoding="utf-8")
    legacy = {
        "model": {"default": "openai/gpt-5.6-sol", "provider": "openrouter"},
        "tools": {"tool_search": {"pinned": [
            "mcp__WarehouseSpecialist__run_sql",
            "mcp__Specialist__describe_table",
            "mcp__WarehouseSpecialist__describe_table",          # collapses onto the entry above
            "mcp__WarehouseRoundtable__delegate_calliope_specialist",
            "mcp__Warehouse__run_sql",                            # the parent lane is not renamed
            "custom_tool",
            "custom_tool",                                        # an operator's own duplicate is theirs
        ]}},
        "mcp_servers": {
            "WarehouseSpecialist": {"url": "http://calliope:8765/mcp?lane=specialist", "timeout": 300},
            "Roundtable": {"url": "http://calliope:8765/mcp?lane=roundtable"},
            "WarehouseRoundtable": {"url": "http://legacy-twin"},   # the new key already exists: dropped
            "Other": {"url": "http://other"},
        },
        "platform_toolsets": {"api_server": [
            "terminal", "WarehouseSpecialist", "Warehouse", "WarehouseRoundtable", "Roundtable",
        ]},
        "operator_note": "keep me",
    }
    (profile / "config.yaml").write_text(yaml.safe_dump(legacy, sort_keys=False), encoding="utf-8")
    reconcile = module["_reconcile_config"]
    assert reconcile(profile, module["_profile_config"]("dashboard-studio"), markers) == "normalized-user-managed"
    payload = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert payload["tools"]["tool_search"]["pinned"] == [
        "mcp__Specialist__run_sql",
        "mcp__Specialist__describe_table",
        "mcp__Roundtable__delegate_calliope_specialist",
        "mcp__Warehouse__run_sql",
        "custom_tool",
        "custom_tool",
    ]
    assert list(payload["mcp_servers"]) == ["Specialist", "Roundtable", "Other"]
    assert payload["mcp_servers"]["Specialist"] == {"url": "http://calliope:8765/mcp?lane=specialist", "timeout": 300}
    assert payload["mcp_servers"]["Roundtable"]["url"].endswith("?lane=roundtable")
    assert payload["platform_toolsets"]["api_server"] == ["terminal", "Specialist", "Warehouse", "Roundtable"]
    assert payload["model"] == legacy["model"]                 # the rest of the operator's document is untouched
    assert payload["operator_note"] == "keep me"
    assert "compression" not in payload                        # no appliance defaults landed on a user-managed doc
    text = (profile / "config.yaml").read_text(encoding="utf-8")
    assert "WarehouseSpecialist" not in text and "WarehouseRoundtable" not in text
    assert reconcile(profile, module["_profile_config"]("dashboard-studio"), markers) == "preserved-user-managed"
    assert (markers / "config.user-managed").is_file()         # the operator marker survives

    # An unparseable operator document is left alone, never rewritten.
    (profile / "config.yaml").write_text("mcp_servers: {WarehouseSpecialist: [unclosed\n", encoding="utf-8")
    assert reconcile(profile, module["_profile_config"]("dashboard-studio"), markers) == "preserved-user-managed"
    assert "WarehouseSpecialist: [unclosed" in (profile / "config.yaml").read_text(encoding="utf-8")

    # A managed document (no marker) converges on the shipped names outright,
    # and the operator's model choice still carries under its marker.
    (markers / "config.user-managed").unlink()
    (markers / module["MODEL_OPERATOR_MARKER"]).write_text("chosen\n", encoding="utf-8")
    (profile / "config.yaml").write_text(yaml.safe_dump(legacy, sort_keys=False), encoding="utf-8")
    assert reconcile(profile, module["_profile_config"]("dashboard-studio"), markers) == "updated"
    payload = yaml.safe_load((profile / "config.yaml").read_text(encoding="utf-8"))
    assert payload["tools"]["tool_search"]["pinned"] == module["specialist_pinned_tools"]("dashboard-studio")
    assert list(payload["mcp_servers"]) == ["Specialist"]
    assert "Specialist" in payload["platform_toolsets"]["api_server"]
    assert payload["model"]["default"] == "openai/gpt-5.6-sol" and payload["model"]["provider"] == "openrouter"
    text = (profile / "config.yaml").read_text(encoding="utf-8")
    assert "WarehouseSpecialist" not in text and "WarehouseRoundtable" not in text


def test_generated_specialists_route_reports_active_and_retired_keys(monkeypatch, tmp_path):
    """The reconciler needs to tell "retired" from "unknown": the route serves
    active rows as `specialists` and every other row's key as `retired`, so a
    directory absent from both lists is an operator's own and is never archived."""
    import asyncio
    import types

    routes = {}
    calls = []

    class MCP:
        @staticmethod
        def custom_route(path, methods):
            def register(handler):
                routes[(path, tuple(methods))] = handler
                return handler
            return register

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def execute(sql, params=()):
            calls.append(sql)
            if "to_regclass('rvbbit.generated_specialists')" in sql:
                return Result([{"ready": True}])
            if "generated_specialist_avatars" in sql:
                return Result([{"r": False}])
            if "WHERE g.status='active'" in sql:
                return Result([{
                    "specialist_key": "commercial-orders", "display_name": "Commerce",
                    "role_label": "Commercial Orders", "description": "Knows the order book.",
                    "soul_markdown": "# Commerce", "memory_mission": "Retain order-book knowledge.",
                    "skills": ["calliope-domain-expert"], "soul_version": 2,
                    "avatar_file": None, "updated_at": "2026-09-23 10:00:00+00",
                }])
            if "IS DISTINCT FROM 'active'" in sql:
                return Result([{"specialist_key": "offense-mix"}, {"specialist_key": "sales-order-book"}])
            return Result([])

    fake_auth = types.SimpleNamespace(
        read_session_full=lambda request: request.session,
        google_enabled=lambda: False,
        background_layer=lambda *_args, **_kwargs: '<div class="bg"></div>',
        HERMES_KEY="hermes-key",
    )
    monkeypatch.setitem(sys.modules, "auth", fake_auth)
    monkeypatch.setattr(calliope, "ensure_tables", lambda _factory: None)
    monkeypatch.setattr(calliope.hermes_docs, "ensure", lambda _factory: None)
    monkeypatch.setattr(calliope.hermes_config, "ensure", lambda _factory: None)
    monkeypatch.setenv("WAREHOUSE_HERMES_URL", "http://hermes:8642")
    monkeypatch.setenv("WAREHOUSE_HERMES_API_KEY", "hermes-key")
    monkeypatch.setenv("WAREHOUSE_CALLIOPE_DIR", str(tmp_path))
    assert calliope.register_calliope_routes(
        MCP(), lambda: Connection(), "", lambda document: document
    ) is True
    route = routes[("/api/calliope/generated-specialists", ("GET",))]

    class Request:
        def __init__(self, token):
            self.headers = {"authorization": f"Bearer {token}"}

    assert asyncio.run(route(Request("wrong"))).status_code == 403
    response = asyncio.run(route(Request("hermes-key")))
    assert response.status_code == 200
    payload = json.loads(response.body)
    assert [item["specialist_key"] for item in payload["specialists"]] == ["commercial-orders"]
    assert payload["specialists"][0]["soul_markdown"] == "# Commerce"
    assert payload["retired"] == ["offense-mix", "sales-order-book"]
    assert any("WHERE g.status='active'" in sql for sql in calls)
    assert any("IS DISTINCT FROM 'active'" in sql for sql in calls)
