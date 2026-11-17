"""Contracts for evidence-backed decisions and their board projection."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from mcp.server.fastmcp import FastMCP

import calliope_decisions as decisions
import calliope_dreams as dreams
import calliope_observations as observations
import server


NOW = datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc)
ALICE_SESSION = "00000000-0000-4000-8000-000000000101"
BOB_SESSION = "00000000-0000-4000-8000-000000000102"
OBSERVATION = "00000000-0000-4000-8000-000000000201"


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Transaction:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _State:
    def __init__(self):
        self.sessions = {
            ALICE_SESSION: {"id": ALICE_SESSION, "owner_email": "alice@example.com"},
            BOB_SESSION: {"id": BOB_SESSION, "owner_email": "bob@example.com"},
        }
        self.observations = {
            OBSERVATION: {
                "id": OBSERVATION,
                "fingerprint": "enrollment-change",
                "scope_kind": "company",
                "owner_email": None,
                "kind": "change",
                "title": "Enrollment mix changed",
                "state": "active",
                "state_revision": 2,
                "evidence": [{
                    "id": "metric:enrollment",
                    "kind": "metric",
                    "evidence_role": "direct",
                    "admission_method": "exact_object_overlap",
                }],
            }
        }
        self.decision_rows = {}
        self.events = []
        self.work_rows = []
        self.kanban_rows = []


class _Conn:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def transaction(self):
        return _Transaction()

    def execute(self, sql, params=()):
        compact = " ".join(sql.split())
        if "FROM rvbbit.calliope_sessions WHERE id=" in compact:
            return _Result(self.state.sessions.get(str(params[0])))
        if "FROM rvbbit.calliope_observations WHERE id=" in compact:
            row = self.state.observations.get(str(params[0]))
            owner = params[1]
            if not row or row["state"] != "active":
                return _Result()
            if row["scope_kind"] == "personal" and row["owner_email"] != owner:
                return _Result()
            return _Result(dict(row))
        if "WHERE owner_email=%s AND observation_id=" in compact:
            owner, observation_id = params
            row = next((
                item for item in self.state.decision_rows.values()
                if item["owner_email"] == owner
                and item["observation_id"] == str(observation_id)
                and item["status"] == "open"
            ), None)
            return _Result(dict(row) if row else None)
        if compact.startswith("INSERT INTO rvbbit.calliope_decision_requests"):
            row = {
                "id": str(params[0]), "owner_email": params[1],
                "session_id": str(params[2]), "observation_id": str(params[3]),
                "decision_kind": params[4], "question": params[5], "summary": params[6],
                "options": json.loads(params[7]), "recommended_option": params[8],
                "urgency": params[9], "evidence": json.loads(params[10]),
                "context": json.loads(params[11]), "revision": params[12],
                "created_by": params[13], "due_at": params[14], "expires_at": params[15],
                "status": "open", "selected_option": None, "response_note": "",
                "created_at": NOW, "updated_at": NOW, "answered_at": None,
                "closed_at": None,
            }
            self.state.decision_rows[row["id"]] = row
            return _Result(dict(row))
        if compact.startswith("UPDATE rvbbit.calliope_decision_requests SET session_id="):
            row = self.state.decision_rows[str(params[12])]
            row.update({
                "session_id": str(params[0]), "decision_kind": params[1],
                "question": params[2], "summary": params[3],
                "options": json.loads(params[4]), "recommended_option": params[5],
                "urgency": params[6], "evidence": json.loads(params[7]),
                "context": json.loads(params[8]), "due_at": params[9],
                "expires_at": params[10], "revision": params[11], "updated_at": NOW,
            })
            return _Result(dict(row))
        if compact.startswith("INSERT INTO rvbbit.calliope_decision_events"):
            self.state.events.append({
                "decision_id": str(params[0]), "event_kind": params[1],
                "actor_email": params[2], "revision": params[3],
                "payload": json.loads(params[4]),
            })
            return _Result()
        if compact.startswith("UPDATE rvbbit.calliope_decision_requests SET status='expired'"):
            return _Result(rows=[])
        if "WHERE id=%s::uuid AND owner_email=%s FOR UPDATE" in compact:
            row = self.state.decision_rows.get(str(params[0]))
            return _Result(dict(row) if row and row["owner_email"] == params[1] else None)
        if compact.startswith("UPDATE rvbbit.calliope_decision_requests SET status='answered'"):
            row = self.state.decision_rows[str(params[4])]
            row.update({
                "status": "answered", "selected_option": params[0],
                "response_note": params[1], "answered_by": params[2],
                "revision": params[3], "answered_at": NOW, "closed_at": NOW,
                "updated_at": NOW,
            })
            return _Result(dict(row))
        if compact.startswith("UPDATE rvbbit.calliope_decision_requests SET status='cancelled'"):
            row = self.state.decision_rows[str(params[2])]
            row.update({
                "status": "cancelled", "response_note": params[0],
                "revision": params[1], "closed_at": NOW, "updated_at": NOW,
            })
            return _Result(dict(row))
        if compact.startswith("SELECT * FROM rvbbit.calliope_decision_requests WHERE owner_email="):
            owner = params[0]
            include_resolved = "AND status='open'" not in compact
            rows = [
                dict(row) for row in self.state.decision_rows.values()
                if row["owner_email"] == owner
                and (include_resolved or row["status"] == "open")
            ]
            return _Result(rows=rows[: int(params[1])])
        if compact.startswith("SELECT status,count(*)::int"):
            owner = params[0]
            counts = {}
            for row in self.state.decision_rows.values():
                if row["owner_email"] == owner:
                    counts[row["status"]] = counts.get(row["status"], 0) + 1
            return _Result(rows=[{"status": key, "n": value} for key, value in counts.items()])
        if "FROM rvbbit.calliope_work_items" in compact:
            return _Result(rows=list(self.state.work_rows))
        if "FROM rvbbit.hermes_kanban_tasks" in compact:
            return _Result(rows=list(self.state.kanban_rows))
        raise AssertionError(f"unexpected SQL: {compact}")


def _factory(state):
    return lambda: _Conn(state)


def _request(factory, question="Should we raise the weekly budget?"):
    return decisions.create_from_observation(
        factory,
        ALICE_SESSION,
        OBSERVATION,
        question,
        [
            {"key": "hold", "label": "Hold", "consequence": "No spend change."},
            {"key": "raise", "label": "Raise 10%", "consequence": "Adds $400."},
        ],
        recommended_option="raise",
        urgency="high",
        context={"metric": "qualified_enrollment"},
    )


def test_create_is_observation_grounded_owner_scoped_and_idempotent():
    state = _State()
    factory = _factory(state)

    first = _request(factory)
    second = _request(factory)

    assert first["changed"] is True and second["changed"] is False
    assert first["decision"]["owner_email"] == "alice@example.com"
    assert first["decision"]["interaction"] == "choose"
    assert first["decision"]["board_projection"] == {
        "board": "calliope-decisions",
        "card_id": f"decision:{first['decision']['id']}",
        "status": "blocked",
        "block_kind": "decision",
        "interaction": "choose",
    }
    assert first["decision"]["evidence"][0]["id"] == "metric:enrollment"
    assert len(state.decision_rows) == 1
    assert [event["event_kind"] for event in state.events] == ["created"]


def test_observation_context_is_ranked_and_session_owner_scoped(monkeypatch):
    state = _State()
    seen = []

    def fake_snapshot(_factory, owner, *, scope_kind, state, limit):
        seen.append((owner, scope_kind, state, limit))
        return {
            "observations": [
                {
                    "id": OBSERVATION,
                    "scope_kind": "company",
                    "kind": "change",
                    "title": "Enrollment mix changed",
                    "summary": "A grounded change.",
                    "entities": ["Enrollment"],
                    "confidence": 0.9,
                    "urgency": 0.8,
                    "ranking": {"surface_role": "lead", "priority": 0.91},
                    "evidence": [{"id": "metric:enrollment"}],
                    "viewer_feedback": {},
                    "last_observed_at": NOW,
                },
                {
                    "id": "00000000-0000-4000-8000-000000000202",
                    "title": "Dismissed topic",
                    "ranking": {"surface_role": "lead"},
                    "evidence": [{"id": "metric:dismissed"}],
                    "viewer_feedback": {"verdict": "not_relevant"},
                },
            ],
            "ranking": {"available": True, "ranked_count": 2},
        }

    monkeypatch.setattr(observations, "snapshot", fake_snapshot)
    result = decisions.observation_context(_factory(state), ALICE_SESSION, limit=99)

    assert seen == [("alice@example.com", "all", "active", 24)]
    assert result["counts"] == {"shown": 1, "decision_ready": 1}
    assert result["observations"][0]["id"] == OBSERVATION
    assert result["observations"][0]["decision_ready"] is True
    with pytest.raises(LookupError, match="session"):
        decisions.observation_context(
            _factory(state), "00000000-0000-4000-8000-000000000999"
        )


def test_revision_and_answer_leave_an_append_only_receipt():
    state = _State()
    factory = _factory(state)
    created = _request(factory)
    revised = _request(factory, "Should we raise the weekly enrollment budget?")
    answered = decisions.answer(
        factory, "Alice@Example.com", created["decision"]["id"], "raise", note="Within cap."
    )

    assert revised["decision"]["revision"] == 2
    assert answered["decision"]["revision"] == 3
    assert answered["decision"]["status"] == "answered"
    assert answered["decision"]["interaction"] == "receipt"
    assert answered["decision"]["selected_option"] == "raise"
    assert [event["event_kind"] for event in state.events] == [
        "created", "updated", "answered",
    ]
    assert decisions.answer(
        factory, "alice@example.com", created["decision"]["id"], "raise", note="Within cap."
    )["changed"] is False
    with pytest.raises(decisions.DecisionConflict, match="already been answered"):
        decisions.answer(
            factory, "alice@example.com", created["decision"]["id"], "hold"
        )
    with pytest.raises(LookupError, match="not found"):
        decisions.answer(factory, "bob@example.com", created["decision"]["id"], "raise")
    board = decisions.queue(factory, "alice@example.com")
    assert board["items"] == []
    assert board["counts"]["receipts"] == 1
    assert board["receipts"][0]["board_projection"]["status"] == "done"


def test_answer_rejects_a_stale_rendered_revision_before_mutating():
    state = _State()
    factory = _factory(state)
    created = _request(factory)
    revised = _request(factory, "Should we raise the weekly enrollment budget?")

    with pytest.raises(decisions.DecisionRevisionConflict, match="changed after"):
        decisions.answer(
            factory,
            "alice@example.com",
            created["decision"]["id"],
            "raise",
            expected_revision=created["decision"]["revision"],
        )

    current = state.decision_rows[created["decision"]["id"]]
    assert current["status"] == "open"
    assert current["revision"] == revised["decision"]["revision"]
    assert [event["event_kind"] for event in state.events] == ["created", "updated"]


def test_answer_accepts_the_current_rendered_revision():
    state = _State()
    factory = _factory(state)
    created = _request(factory)

    result = decisions.answer(
        factory,
        "alice@example.com",
        created["decision"]["id"],
        "hold",
        expected_revision=created["decision"]["revision"],
    )

    assert result["decision"]["status"] == "answered"
    assert result["decision"]["revision"] == created["decision"]["revision"] + 1


def test_queue_projects_decisions_and_external_blockers_without_duplication():
    state = _State()
    factory = _factory(state)
    _request(factory)
    state.work_rows = [{
        "id": "00000000-0000-4000-8000-000000000301",
        "title": "Source access blocked", "summary": "Credentials need repair.",
        "urgency": "critical", "session_id": ALICE_SESSION,
        "context": {}, "created_at": NOW, "updated_at": NOW,
    }]
    state.kanban_rows = [{
        "board": "automation", "id": "task-7", "title": "Review variance",
        "body": "A human must inspect the exception.", "status": "blocked",
        "block_kind": "review", "session_id": ALICE_SESSION,
        "created_at": NOW, "synced_at": NOW,
    }]

    result = decisions.queue(factory, "alice@example.com")

    assert result["counts"] == {
        "open": 3,
        "actionable": 1,
        "receipts": 0,
        "by_source": {"work": 1, "decision": 1, "kanban": 1},
    }
    assert [item["source"] for item in result["items"]] == [
        "work", "decision", "kanban",
    ]
    assert result["receipts"] == []


def test_inputs_are_bounded_and_options_are_strict():
    with pytest.raises(ValueError, match="2 to 6"):
        decisions.normalize_options([{"key": "yes", "label": "Yes"}])
    with pytest.raises(ValueError, match="lowercase"):
        decisions.normalize_options([
            {"key": "YES!", "label": "Yes"}, {"key": "no", "label": "No"},
        ])
    with pytest.raises(ValueError, match="too large"):
        decisions._bounded_object({f"key_{index}": "x" * 2_000 for index in range(48)}, "context")


def test_mcp_decision_tool_has_a_bounded_typed_contract(monkeypatch):
    monkeypatch.setattr(server, "_record", lambda *_args, **_kwargs: None)
    mcp = FastMCP("decision-contract")
    server._register(mcp)

    schema = mcp._tool_manager._tools["calliope_decision_request"].parameters
    properties = schema["properties"]
    assert set(schema["required"]) == {
        "session_id", "observation_id", "question", "options",
    }
    assert properties["options"]["minItems"] == 2
    assert properties["options"]["maxItems"] == 6
    assert properties["decision_kind"]["enum"] == [
        "approval", "choice", "exception", "confirmation",
    ]
    assert properties["urgency"]["enum"] == ["low", "normal", "high", "critical"]
    assert properties["context"]["anyOf"][0]["type"] == "object"
    assert "owner_email" not in properties and "caller_email" not in properties
    context_schema = mcp._tool_manager._tools[
        "calliope_observation_context"
    ].parameters
    assert set(context_schema["required"]) == {"session_id"}
    assert context_schema["properties"]["limit"]["minimum"] == 1
    assert context_schema["properties"]["limit"]["maximum"] == 24


def test_personal_context_learns_from_open_and_answered_decisions():
    class SnapshotConn:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, _params=()):
            if "FROM rvbbit.calliope_decision_requests" in sql:
                return _Result(rows=[{
                    "id": "00000000-0000-4000-8000-000000000401",
                    "decision_kind": "approval",
                    "question": "Approve the budget adjustment?",
                    "summary": "The change remains inside the weekly cap.",
                    "options": [
                        {"key": "approve", "label": "Approve"},
                        {"key": "hold", "label": "Hold"},
                    ],
                    "recommended_option": "approve",
                    "status": "answered",
                    "selected_option": "approve",
                    "response_note": "Proceed this week.",
                    "urgency": "high",
                    "seen_at": NOW,
                }])
            if "FROM rvbbit.calliope_user_dossiers" in sql:
                return _Result({})
            return _Result(rows=[])

    snapshot = dreams.collect_personal_snapshot(
        lambda: SnapshotConn(),
        "alice@example.com",
        NOW - timedelta(days=30),
        NOW + timedelta(seconds=1),
    )

    signal = next(item for item in snapshot["signals"] if item["kind"] == "decision")
    assert signal["selected_option"] == "approve"
    assert signal["recommended_option"] == "approve"
    assert snapshot["source_summary"]["decisions"] == 1
    assert snapshot["evidence"][signal["id"]]["kind"] == "human_decision"
