"""Focused contracts for the private Calliope Work Inbox handoff surface."""
from __future__ import annotations

import inspect
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import calliope  # noqa: E402
import server  # noqa: E402


class _Result:
    def __init__(self, rows):
        self.rows = rows if isinstance(rows, list) else ([rows] if rows else [])

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


def test_work_inbox_schema_is_migrated_and_service_self_healing():
    migration = (
        ROOT / "crates" / "pg_rvbbit" / "sql" / "migrations"
        / "0228_calliope_work_inbox.sql"
    ).read_text(encoding="utf-8")
    retention = (
        ROOT / "crates" / "pg_rvbbit" / "sql" / "migrations"
        / "0342_calliope_work_item_retention.sql"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(
        encoding="utf-8"
    )

    assert "CREATE TABLE IF NOT EXISTS rvbbit.calliope_work_items" in migration
    assert "owner_email text NOT NULL" in migration
    assert "session_id uuid REFERENCES rvbbit.calliope_sessions" in migration
    assert "UNIQUE (owner_email, source, dedupe_key)" in migration
    assert "0228_calliope_work_inbox" in registry
    assert "ADD COLUMN IF NOT EXISTS expires_at timestamptz" in retention
    assert "t.error = 'Stopped by you.'" in retention
    assert "0342_calliope_work_item_retention" in registry
    assert "CREATE TABLE IF NOT EXISTS rvbbit.calliope_work_items" in calliope._INBOX_DDL
    assert "ADD COLUMN IF NOT EXISTS expires_at timestamptz" in calliope._INBOX_DDL
    assert "t.status IN ('failed','interrupted')" in inspect.getsource(calliope.ensure_tables)


def test_hermes_publisher_resolves_owner_from_session_and_accepts_no_recipient():
    session_id = str(uuid.uuid4())
    inserted = {}
    now = datetime.now(timezone.utc)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params=None):
            if query.startswith("SELECT id,owner_email"):
                return _Result({"id": session_id, "owner_email": "owner@example.com"})
            if query.startswith("INSERT INTO rvbbit.calliope_work_items"):
                inserted["params"] = params
                return _Result({
                    "id": params[0],
                    "owner_email": params[1],
                    "session_id": params[2],
                    "kind": params[3],
                    "source": params[4],
                    "source_ref": params[5],
                    "dedupe_key": params[6],
                    "title": params[7],
                    "summary": params[8],
                    "urgency": params[9],
                    "state": "unread",
                    "context": {"open_url": "/gallery", "value": 42},
                    "action_prompt": params[11],
                    "due_at": params[12],
                    "expires_at": params[13],
                    "created_at": now,
                    "updated_at": now,
                })
            raise AssertionError(query)

    item = calliope.publish_work_item(
        Connection,
        session_id,
        "result",
        "Weekly check complete",
        "Pipeline coverage changed.",
        "high",
        "Explain the change and recommend a response.",
        {"open_url": "/gallery", "value": 42},
        "weekly-pipeline-check",
        "2026-08-03T09:00:00-04:00",
        source="calliope_work_order",
    )

    assert inserted["params"][1] == "owner@example.com"
    assert item["session_id"] == session_id
    assert item["open_url"] == "/gallery"
    assert item["state"] == "unread"
    item_expiry = datetime.fromisoformat(item["expires_at"])
    assert item_expiry == inserted["params"][13]
    assert timedelta(days=6) < item_expiry - now < timedelta(days=8)
    assert "owner" not in inspect.signature(calliope.publish_work_item).parameters
    assert "owner" not in inspect.signature(server._mcp_calliope_work_item).parameters
    assert "email" not in inspect.signature(server._mcp_calliope_work_item).parameters
    assert "expires_at" in inspect.signature(server._mcp_calliope_work_item).parameters


def test_work_item_inputs_are_bounded_and_due_dates_must_be_unambiguous():
    context = calliope._bounded_work_context({f"payload_{i}": "x" * 10_000 for i in range(40)})
    assert context["truncated"] is True
    assert calliope._work_due_at("2026-08-01T12:00:00Z").tzinfo is not None
    assert calliope._work_expires_at("2026-08-15T12:00:00Z").tzinfo is not None
    with pytest.raises(ValueError, match="timezone"):
        calliope._work_due_at("2026-08-01T12:00:00")
    with pytest.raises(ValueError, match="ISO-8601"):
        calliope._work_due_at("next Tuesday")
    with pytest.raises(ValueError, match="expires_at"):
        calliope._work_expires_at("sometime later")


def test_complete_turn_never_promotes_an_explicit_user_stop(monkeypatch):
    queries = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params=None):
            queries.append((query, params))
            return _Result(None)

    monkeypatch.setattr(
        calliope,
        "publish_work_item",
        lambda *_args, **_kwargs: pytest.fail("a user stop is not Inbox work"),
    )

    calliope._complete_turn(
        Connection,
        str(uuid.uuid4()),
        "",
        None,
        "interrupted",
        calliope._USER_STOPPED_TURN_ERROR,
    )

    assert not any("SELECT t.id,t.session_id" in query for query, _ in queries)
    assert not any("calliope_work_items" in query for query, _ in queries)


def test_inbox_snapshot_dismisses_expired_attention_before_counting():
    statements = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params=None):
            statements.append((query, params))
            if " AS unread," in query:
                return _Result({"unread": 0, "open": 0})
            return _Result([])

    snapshot = calliope._inbox_snapshot(Connection, "owner@example.com")

    expiry_query, params = statements[0]
    assert "state='dismissed'" in expiry_query
    assert "expires_at<=now()" in expiry_query
    assert params == ("owner@example.com",)
    assert snapshot["counts"] == {"unread": 0, "open": 0, "shown": 0, "by_kind": {}}


def test_semantic_watch_event_projects_exact_handle_into_same_inbox_surface():
    item = calliope._watch_event_work_item({
        "event_id": 88,
        "watch_id": uuid.uuid4(),
        "event_kind": "triggered",
        "name": "Revenue floor",
        "message": "Regional revenue fell to 95.",
        "value": 95,
        "threshold": 100,
        "source": {
            "kind": "artifact_object",
            "slug": "regional-brief",
            "version": 4,
            "object_id": "regional_revenue",
            "definition_hash": "abc123",
            "context": {"region": "North"},
        },
        "presentation": {
            "title": "Regional revenue",
            "artifact_name": "Regional Brief",
            "open_url": "/d/regional-brief/versions/4",
            "thumbnail_url": "/thumbs/dashboard/regional-brief.png",
        },
        "comparator": "below",
        "cadence": "normal",
        "payload": {},
        "created_at": datetime.now(timezone.utc),
        "acknowledged_at": None,
    })

    assert item["source"] == "watch"
    assert item["state"] == "unread"
    assert item["urgency"] == "high"
    assert item["handle"]["kind"] == "artifact_object"
    assert item["handle"]["object_id"] == "regional_revenue"
    assert item["open_url"] == "/d/regional-brief/versions/4"


def test_inbox_routes_mcp_routing_prompt_and_themed_ui_ship_together():
    backend = (HERE / "calliope.py").read_text(encoding="utf-8")
    server_source = (HERE / "server.py").read_text(encoding="utf-8")
    page = (HERE / "calliope" / "index.html").read_text(encoding="utf-8")
    script = (HERE / "calliope" / "calliope.js").read_text(encoding="utf-8")
    css = (HERE / "calliope" / "calliope.css").read_text(encoding="utf-8")

    assert '@mcp.custom_route("/api/calliope/inbox", methods=["GET"])' in backend
    assert '"/api/calliope/inbox/items/{source}/{item_id}/investigate"' in backend
    assert '@mcp.custom_route("/api/calliope/inbox/schedule", methods=["POST"])' in backend
    assert "Originating Calliope session_id" in backend
    assert "calliope_work_item" in backend
    assert 'mcp.tool(name="calliope_work_item")' in server_source
    assert "intentionally accepts no email or recipient" in server_source
    assert 'id="work-inbox-open"' in page
    assert 'id="work-inbox-dialog"' in page
    assert "function renderInbox()" in script
    assert "function inboxExplainTooltip" in script
    assert "Next useful move" in script
    assert "Calliope Workflow" in script
    assert "include_resolved=true" in script
    assert "investigateInboxItem" in script
    assert "45_000" in script
    assert 'launch.get("inbox")' in script
    assert "!els.inboxDialog.open" in script
    assert ".work-inbox-dialog" in css
    assert "[data-calliope-tooltip]" in css
    assert "color-mix(in oklch,var(--jade)" in css


@pytest.mark.parametrize('kind',['result','suggestion','goal','scheduled'])
def test_agent_cannot_create_activity_notifications_even_with_claimed_background_context(kind):
    sid=str(uuid.uuid4())
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*_args):return False
        def execute(self,query,params=None):
            assert query.startswith('SELECT id,owner_email'), 'No notification insert/update is allowed'
            return _Result({'id':sid,'owner_email':'owner@example.com'})
    result=calliope.publish_work_item(Connection,sid,kind,'I finished your dashboard',context={'background':True,'notification_policy':'all'})
    assert result['published'] is False and result['id'] is None


def test_playbook_approval_settles_review_without_creating_ready_notification():
    import playbook_access
    writes=[]
    class Connection:
        def execute(self,query,params=None):
            if query.startswith('SELECT to_regclass'):return _Result({'relation':'rvbbit.calliope_work_items'})
            writes.append((query,params));return _Result([])
    playbook_access._sync_inbox_handoff(Connection(),{'id':str(uuid.uuid4()),'owner_email':'owner@example.com'},version=1,title='Useful method',synopsis='Already reviewed',source_session_id=None,approved=True)
    assert len(writes)==1 and writes[0][0].startswith('UPDATE rvbbit.calliope_work_items')
    assert "state='done'" in writes[0][0] and writes[0][1][0]=='owner@example.com'
