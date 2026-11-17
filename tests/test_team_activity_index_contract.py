"""The Team page's tool-activity query needs its partial index on hermes_messages."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "crates" / "pg_rvbbit" / "sql" / "migrations"


def test_assistant_messages_index_matches_the_team_query():
    """tool_activity() joins sessions to hermes_messages on (session_id, store) and
    keeps role='assistant' rows by ts; without this index it walked every message
    of every session (7-21 s cold on the AWS pilot)."""
    sql = "\n".join(p.read_text(encoding="utf-8") for p in sorted(MIGRATIONS.glob("*.sql")))
    assert re.search(r"CREATE INDEX IF NOT EXISTS hermes_messages_assistant_session_idx\s+"
                     r"ON rvbbit\.hermes_messages \(session_id, store, ts\)\s+WHERE role = 'assistant'", sql)
    query = (ROOT / "services" / "calliope" / "calliope.py").read_text(encoding="utf-8")
    assert "m.session_id=v.hermes_session_id AND m.store=v.store" in query
    assert "m.role='assistant' AND m.ts>=" in query
