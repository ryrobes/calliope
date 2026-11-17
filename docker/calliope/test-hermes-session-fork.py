"""Gate: Calliope's fork copies the transcript up to a moment and keeps the source open."""
import inspect
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/opt/hermes")
from gateway import calliope_session_fork as fork  # noqa: E402
from gateway.platforms import api_server  # noqa: E402
from hermes_state import SessionDB  # noqa: E402

source = inspect.getsource(api_server.APIServerAdapter._handle_fork_session)
assert "_calliope_fork.messages_for_fork, db, source_id, body" in source, source
assert "if not _calliope_fork.keeps_source(body):" in source
assert api_server._STATIC_FEATURE_FLAGS.get("calliope_session_fork") is True

db = SessionDB(db_path=Path(tempfile.mkdtemp()) / "state.db")
db.create_session("src", "api_server")
db.append_message("src", "user", "Build regional-orders please", timestamp=100.0)
db.append_message("src", "assistant", "Published regional-orders (see /d/regional-orders).", timestamp=101.0,
                  tool_calls=None)
db.append_message("src", "assistant", "", timestamp=101.5,
                  tool_calls=[{"id": "c1", "type": "function",
                               "function": {"name": "publish_dashboard", "arguments": '{"name": "regional-orders"}'}}])
db.append_message("src", "tool", '{"slug": "regional-orders", "other": "regional-orders-2024"}',
                  tool_call_id="c1", tool_name="publish_dashboard", timestamp=102.0)
db.append_message("src", "tool", "orphan result", tool_call_id="nobody", tool_name="x", timestamp=102.5)
db.append_message("src", "user", "Now change the colours", timestamp=200.0)
db.append_message("src", "assistant", "Done on regional-orders v2.", timestamp=201.0)
# An in-place compaction: the first exchange is archived and a summary stands in for it.
db.append_message("src", "assistant", "Summary: we built regional-orders.", timestamp=150.0,
                  _compressed_summary=True)
db.append_message("src", "assistant", "", timestamp=101.5,
                  tool_calls=[{"id": "c1", "type": "function",
                               "function": {"name": "publish_dashboard", "arguments": "{}"}}])
db.append_message("src", "tool", "[Retained tool evidence] regional-orders", tool_call_id="c1",
                  tool_name="publish_dashboard", timestamp=102.0)
db._execute_write(lambda conn: conn.execute(
    "UPDATE messages SET active = 0, compacted = 1 WHERE session_id = 'src' AND timestamp < 150"))

# Upstream behaviour without Calliope's keys: the live history, untouched.
live = [m["content"] for m in fork.messages_for_fork(db, "src", {})]
assert "Summary: we built regional-orders." in live and "Build regional-orders please" not in live, live

copied = fork.messages_for_fork(db, "src", {"until_ts": 150.0, "keep_source": True,
                                            "replacements": {"regional-orders": "regional-orders-f1"}})
texts = [m["content"] for m in copied]
assert texts == ["Build regional-orders-f1 please",
                 "Published regional-orders-f1 (see /d/regional-orders-f1).",
                 "",
                 '{"slug": "regional-orders-f1", "other": "regional-orders-2024"}'], texts
assert copied[2]["tool_calls"][0]["function"]["arguments"] == '{"name": "regional-orders-f1"}'

assert all("id" not in m and "active" not in m for m in copied)
assert fork.keeps_source({"keep_source": True}) and not fork.keeps_source({})

everything = fork.messages_for_fork(db, "src", {"keep_source": True})
assert [m["content"] for m in everything][-1] == "Done on regional-orders v2." and len(everything) == 6, everything

db.create_session("dst", "api_server", parent_session_id="src")
db.replace_messages("dst", copied)
assert [m["content"] for m in db.get_messages("dst")] == texts  # fresh, active rows
assert db.get_session("src").get("ended_at") is None

print("calliope session fork gate: OK")
