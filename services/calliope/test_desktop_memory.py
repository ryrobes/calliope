from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

import desktop_memory as memory


@pytest.fixture
def lesson():
    return {"id": str(uuid.uuid4()), "run_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()),
            "desktop_id": str(uuid.uuid4()), "actor_email": "one@example.com", "feedback": "Use the export date, not the upload date.",
            "goal": "Download the newest report", "outcome": "Claimed success", "status": "pending", "attempts": 0,
            "created_at": datetime.now(timezone.utc), "retention_result": None}


def test_lost_ack_reuses_operation_identity_and_retains_only_explicit_feedback(lesson):
    bodies = []
    def transport(method, bank, path, body=None):
        bodies.append(body)
        if len(bodies) == 1:
            raise TimeoutError("ack lost")
        return {"success": True, "async": True, "operation_id": lesson["id"]}
    with pytest.raises(TimeoutError):
        memory.advance(lesson, transport)
    status, result, delay = memory.advance(lesson, transport)
    assert status == "pending" and delay > 0
    assert bodies[0] == bodies[1]
    assert result["operation_id"] == lesson["id"]
    assert "not independently confirmed" in bodies[0]["items"][0]["content"]
    assert bodies[0]["items"][0]["metadata"]["recording_url"].endswith(lesson["session_id"])


def test_ack_and_processing_are_not_completed_learning(lesson):
    lesson["retention_result"] = {"operation_id": lesson["id"]}
    for state in ("pending", "processing", "completed"):
        status, result, _ = memory.advance(lesson, lambda *args: {"status": state})
        assert status == ("retained" if state == "completed" else "pending")
        assert result["operation_id"] == lesson["id"]


def test_failed_extraction_retries_existing_operation_without_resubmitting(lesson):
    lesson["retention_result"] = {"operation_id": lesson["id"]}
    calls = []
    def transport(method, bank, path, body=None):
        calls.append((method, path))
        return {"status": "failed"}
    assert memory.advance(lesson, transport)[0] == "pending"
    assert calls == [("GET", "operations/" + lesson["id"]),
                     ("POST", "operations/" + lesson["id"] + "/retry")]


def test_banks_separate_people_and_desktops(lesson):
    bank = memory.bank_for(lesson["desktop_id"], lesson["actor_email"])
    assert bank != memory.bank_for(lesson["desktop_id"], "two@example.com")
    assert bank != memory.bank_for(str(uuid.uuid4()), lesson["actor_email"])
    assert "example.com" not in bank


class Connection:
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params):
        self.params = params
        return SimpleNamespace(fetchall=lambda: self.rows)


def test_recall_preserves_attribution_and_rejects_unattributed_memory(lesson, monkeypatch):
    conn, receipts = Connection([lesson]), []
    monkeypatch.setattr(memory.access, "append_event", lambda *args: receipts.append(args))
    facts = {"results": [{"id": "valid", "text": "Prefer export dates", "metadata": {"lesson_id": lesson["id"]}},
                         {"id": "other", "text": "Wrong source", "metadata": {"lesson_id": str(uuid.uuid4())}}]}
    run = {"id": str(uuid.uuid4()), "desktop_id": lesson["desktop_id"], "goal": "Download a newer report"}
    result = memory.recall(lambda: conn, run, lesson["actor_email"], lambda *args: facts)
    assert conn.params[:2] == (lesson["desktop_id"], lesson["actor_email"])
    assert len(result["items"]) == 1
    assert result["items"][0]["memory_id"] == "valid"
    assert result["items"][0]["run_id"] == lesson["run_id"]
    assert receipts[0][1] == run["id"] and receipts[0][3] == "lessons.recalled"


def test_memory_outage_still_returns_labelled_original_correction(lesson, monkeypatch):
    monkeypatch.setattr(memory.access, "append_event", lambda *args: None)
    def unavailable(*args):
        raise TimeoutError()
    run = {"id": str(uuid.uuid4()), "desktop_id": lesson["desktop_id"], "goal": "Get the report"}
    result = memory.recall(lambda: Connection([lesson]), run, lesson["actor_email"], unavailable)
    assert result["status"] == "hindsight_unavailable"
    assert result["items"][0]["kind"] == "original_feedback"
    assert result["items"][0]["retention_status"] == "pending"
    assert result["items"][0]["text"] == lesson["feedback"]


def test_takeover_bookends_do_not_infer_intent_from_inputs():
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    def event(mode, at, generation):
        return {'id': str(uuid.uuid4()), 'actor_email': 'person@example.com',
                'created_at': at, 'payload': {'mode': mode, 'generation': generation}}
    events = [event('human', now, 2), event('agent', now + timedelta(seconds=12), 4),
              event('human', now + timedelta(seconds=30), 6)]
    spans = memory.demonstrations(Connection(events), {'id': str(uuid.uuid4())})
    assert len(spans) == 2
    assert spans[0]['ended_at'] == events[1]['created_at'].isoformat()
    assert spans[0]['end_event_id'] == events[1]['id']
    assert spans[1]['ended_at'] is None
    assert spans[1]['generation'] == 6
    assert 'success' not in spans[0] and 'feedback' not in spans[0]


def test_demonstration_provenance_survives_retention_and_recall(lesson, monkeypatch):
    import json
    lesson['demonstration'] = {'id': str(uuid.uuid4()), 'actor': 'person@example.com',
                             'started_at': lesson['created_at'].isoformat(), 'basis': 'human_explanation'}
    body = memory.retention_body(lesson)['items'][0]
    assert json.loads(body['metadata']['demonstration']) == lesson['demonstration']
    assert lesson['demonstration']['id'] in body['content']
    assert all(isinstance(value, str) for value in body['metadata'].values())
    monkeypatch.setattr(memory.access, 'append_event', lambda *args: None)
    result = memory.recall(lambda: Connection([lesson]),
                          {'id': str(uuid.uuid4()), 'desktop_id': lesson['desktop_id'], 'goal': 'New report'},
                          lesson['actor_email'], lambda *args: {'results': []})
    assert result['items'][0]['demonstration'] == lesson['demonstration']
