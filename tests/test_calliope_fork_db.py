"""Fork-from-here against a real rvbbit database (rolled back): what is copied, what is not."""
from __future__ import annotations

import datetime as dt
import importlib
import json
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from conftest import RVBBIT_DSN

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "calliope"))
fork = importlib.import_module("calliope_fork")


def test_fork_copies_the_chat_up_to_a_message_and_republishes_its_artifacts():
    tag = uuid.uuid4().hex[:8]
    owner, member = f"owner-{tag}@example.invalid", f"member-{tag}@example.invalid"
    slug, other = f"fork-art-{tag}", f"fork-other-{tag}"
    t0 = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.timezone.utc)
    conn = psycopg.connect(RVBBIT_DSN, row_factory=dict_row)
    try:
        conn.execute("ALTER TABLE rvbbit.calliope_sessions ADD COLUMN IF NOT EXISTS forked_from jsonb")
        for email in (owner, member):
            conn.execute("INSERT INTO rvbbit.application_principals(email,display_name) VALUES (%s,%s)", (email, email))
        source = str(uuid.uuid4())
        conn.execute("INSERT INTO rvbbit.calliope_sessions (id,owner_email,hermes_session_id,title) VALUES (%s,%s,%s,%s)",
                     (source, owner, f"calliope_test_{tag}", "Build room"))
        conn.execute("INSERT INTO rvbbit.calliope_session_view_grants (session_id,principal_email,granted_by) "
                     "VALUES (%s,%s,%s)", (source, member, owner))
        turns = []
        for n, (kind, text, author, run) in enumerate([
            ("chat", f"Build {slug}", owner, "r1"),
            ("chat", f"Tweak {slug} and also {other}", member, "r2"),
            ("note", "looks good", member, None),
            ("chat", f"Rewrite {slug} completely", owner, "r3"),
        ], start=1):
            tid = str(uuid.uuid4())
            turns.append(tid)
            conn.execute(
                "INSERT INTO rvbbit.calliope_turns (id,session_id,ordinal,turn_kind,user_message,assistant_message,status,"
                "author_email,hermes_run_id,created_at,completed_at) VALUES (%s,%s,%s,%s,%s,%s,'complete',%s,%s,%s,%s)",
                (tid, source, n, kind, text, f"Done with {slug} (see /d/{slug})", author, run,
                 t0 + dt.timedelta(minutes=10 * n), t0 + dt.timedelta(minutes=10 * n + 5)))
        art = conn.execute("INSERT INTO rvbbit.dashboards(slug,name,owner_email,home_session_id,latest_version) "
                           "VALUES (%s,'Art',%s,%s,3) RETURNING id", (slug, owner, source)).fetchone()["id"]
        conn.execute("INSERT INTO rvbbit.dashboards(slug,name,owner_email) VALUES (%s,'Other',%s)", (other, owner))
        for version, minute in ((1, 12), (2, 22), (3, 42)):
            conn.execute("INSERT INTO rvbbit.dashboard_versions(dashboard_id,version,html,kind,created_by,created_at) "
                         "VALUES (%s,%s,%s,'live',%s,%s)",
                         (art, version, f"<h1>{slug} v{version}</h1>", owner, t0 + dt.timedelta(minutes=minute)))
        surface = str(uuid.uuid4())
        conn.execute("INSERT INTO rvbbit.calliope_surfaces (id,session_id,turn_id,ordinal,kind,title,tool_name,tool_call_id,lineage_key,artifact_slug,"
                     "artifact_version,payload) VALUES (%s,%s,%s,1,'artifact','Art','publish_dashboard','call-1','art',%s,2,%s::jsonb)",
                     (surface, source, turns[1], slug, json.dumps({"slug": slug, "turn": turns[1]})))
        conn.execute("INSERT INTO rvbbit.calliope_surfaces (id,session_id,turn_id,ordinal,kind,title,tool_name,tool_call_id,lineage_key,artifact_slug,"
                     "artifact_version,payload) VALUES (%s,%s,%s,2,'artifact','Other','update_dashboard','call-2','other',%s,1,'{}'::jsonb)",
                     (str(uuid.uuid4()), source, turns[1], other))

        # The member forks at the note (message 3): turns 1-3, the artifact as it was then (v2).
        plan = fork.plan(conn, source, turns[2], member)
        assert [t["ordinal"] for t in plan["turns"]] == [1, 2, 3]
        assert plan["hermes_until"] == (t0 + dt.timedelta(minutes=25)).timestamp()  # end of the last agent turn
        assert [(a["slug"], a["fork_version"]) for a in plan["artifacts"]] == [(slug, 2)]
        assert plan["edited_only"] == [other] and plan["slugs"] == {slug: f"{slug}-f1"}
        new = str(uuid.uuid4())
        row = fork.apply(conn, plan, owner=member, session_id=new, hermes_session_id=f"calliope_fork_{tag}")
        assert row["owner_email"] == member and row["title"] == "Build room (fork)"
        assert row["forked_from"]["artifacts"] == [{"from": slug, "from_version": 2, "slug": f"{slug}-f1"}]
        copied = conn.execute("SELECT * FROM rvbbit.calliope_turns WHERE session_id=%s ORDER BY ordinal", (new,)).fetchall()
        assert [(t["ordinal"], t["author_email"]) for t in copied] == [(1, owner), (2, member), (3, member)]
        assert copied[1]["user_message"] == f"Tweak {slug}-f1 and also {other}"
        assert all(t["hermes_run_id"] is None for t in copied)
        piece = conn.execute("SELECT * FROM rvbbit.calliope_surfaces WHERE session_id=%s AND title='Art'", (new,)).fetchone()
        assert piece["artifact_slug"] == f"{slug}-f1" and piece["artifact_version"] == 2
        assert piece["payload"] == {"slug": f"{slug}-f1", "turn": str(copied[1]["id"])}
        copy = conn.execute("SELECT * FROM rvbbit.dashboards WHERE slug=%s", (f"{slug}-f1",)).fetchone()
        assert (copy["owner_email"], str(copy["home_session_id"]), copy["latest_version"]) == (member, new, 2)
        assert [v["html"] for v in conn.execute("SELECT html FROM rvbbit.dashboard_versions WHERE dashboard_id=%s "
                                                "ORDER BY version", (copy["id"],)).fetchall()] == [
            f"<h1>{slug}-f1 v1</h1>", f"<h1>{slug}-f1 v2</h1>"]
        assert not conn.execute("SELECT 1 FROM rvbbit.calliope_session_view_grants WHERE session_id=%s", (new,)).fetchone()
        assert fork.forked_copy(conn, new, slug) == f"{slug}-f1" and fork.forked_copy(conn, source, slug) is None
        # The original is untouched.
        assert conn.execute("SELECT latest_version FROM rvbbit.dashboards WHERE slug=%s", (slug,)).fetchone()["latest_version"] == 3
        assert conn.execute("SELECT count(*) AS n FROM rvbbit.calliope_turns WHERE session_id=%s", (source,)).fetchone()["n"] == 4

        # A message still being answered cannot be forked from; nor can someone outside the chat fork it.
        conn.execute("UPDATE rvbbit.calliope_turns SET status='running' WHERE id=%s", (turns[1],))
        try:
            fork.plan(conn, source, turns[2], member)
            raise AssertionError("forked past a running turn")
        except fork.ForkError as exc:
            assert exc.code == "TURN_NOT_FINISHED"
    finally:
        conn.rollback()
        conn.close()


def test_fork_carries_each_question_as_it_stood_and_never_reasks_an_answered_one():
    """Copying a question card registers a new open request. A fork must give it
    back the answer it had, or every question the chat ever settled blocks again."""
    tag = uuid.uuid4().hex[:8]
    owner, forker = f"asker-{tag}@example.invalid", f"forker-{tag}@example.invalid"
    t0 = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.timezone.utc)
    at = lambda minute: t0 + dt.timedelta(minutes=minute)
    conn = psycopg.connect(RVBBIT_DSN, row_factory=dict_row)
    try:
        conn.execute("ALTER TABLE rvbbit.calliope_sessions ADD COLUMN IF NOT EXISTS forked_from jsonb")
        for email in (owner, forker):
            conn.execute("INSERT INTO rvbbit.application_principals(email,display_name) VALUES (%s,%s)", (email, email))
        source = str(uuid.uuid4())
        conn.execute("INSERT INTO rvbbit.calliope_sessions (id,owner_email,hermes_session_id,title) VALUES (%s,%s,%s,%s)",
                     (source, owner, f"calliope_test_{tag}", "Asking room"))
        conn.execute("INSERT INTO rvbbit.calliope_session_view_grants (session_id,principal_email,granted_by) "
                     "VALUES (%s,%s,%s)", (source, forker, owner))
        turns = []
        for n in range(1, 6):
            turns.append(str(uuid.uuid4()))
            conn.execute(
                "INSERT INTO rvbbit.calliope_turns (id,session_id,ordinal,turn_kind,user_message,assistant_message,status,"
                "author_email,hermes_run_id,created_at,completed_at) VALUES (%s,%s,%s,'chat',%s,'ok','complete',%s,%s,%s,%s)",
                (turns[-1], source, n, f"message {n}", owner, f"run-{tag}-{n}", at(10 * n), at(10 * n + 5)))

        def ask(turn, title, kind=None):
            sid = str(uuid.uuid4())
            payload = {"title": title, "prompt": title, "blocking": True,
                       "fields": [{"id": "pick", "label": "Pick", "type": "choice", "options": ["a", "b"]}]}
            if kind:
                payload["kind"] = kind
            conn.execute("INSERT INTO rvbbit.calliope_surfaces (id,session_id,turn_id,ordinal,kind,title,tool_name,"
                         "tool_call_id,lineage_key,payload) VALUES (%s,%s,%s,1,'input',%s,'ask_user',%s,%s,%s::jsonb)",
                         (sid, source, turns[turn - 1], title, f"call-{sid[:8]}", f"ask-{sid[:8]}", json.dumps(payload)))
            return sid

        def settle(sid, status, minute, answers=None):
            conn.execute(
                "UPDATE rvbbit.calliope_input_requests SET status=%s,answers=%s::jsonb,revision=2,answered_by=%s,"
                "answered_at=%s,closed_by=%s,closed_at=%s,updated_at=%s WHERE id=%s",
                (status, json.dumps(answers or {}), owner if status == "answered" else None,
                 at(minute) if status == "answered" else None, owner, at(minute), at(minute), sid))

        answered = ask(1, "Which region?"); settle(answered, "answered", 18, {"pick": "a", "seen": answered})
        cancelled = ask(2, "Publish it?"); settle(cancelled, "cancelled", 28)
        late = ask(3, "Answered after the fork point"); settle(late, "answered", 48, {"pick": "b"})
        still_open = ask(3, "Never answered")
        knock = ask(2, "Someone wants to join", kind="join_request")
        beyond = ask(5, "Asked after the fork point")
        assert conn.execute("SELECT count(*) AS n FROM rvbbit.calliope_input_requests WHERE session_id=%s",
                            (source,)).fetchone()["n"] == 6

        # Fork at message 3: it ended at minute 35.
        plan = fork.plan(conn, source, turns[2], forker)
        new = str(uuid.uuid4())
        fork.apply(conn, plan, owner=forker, session_id=new, hermes_session_id=f"calliope_fork_{tag}")
        got = {row["title"]: row for row in conn.execute(
            "SELECT s.title,s.id AS surface_id,s.payload,r.* FROM rvbbit.calliope_input_requests r "
            "JOIN rvbbit.calliope_surfaces s ON s.id=r.id WHERE r.session_id=%s", (new,)).fetchall()}
        assert set(got) == {"Which region?", "Publish it?", "Answered after the fork point", "Never answered",
                            "Someone wants to join"}  # the message-5 question was never copied
        assert beyond not in {str(row["id"]) for row in got.values()}

        first = got["Which region?"]
        assert (first["status"], first["answered_by"], first["answered_at"], first["closed_at"], first["revision"]) == (
            "answered", owner, at(18), at(18), 2)
        # Answers are rewritten like the rest of the copy, and the card shows them.
        assert first["answers"] == {"pick": "a", "seen": str(first["surface_id"])}
        assert first["payload"]["request_state"]["status"] == "answered"
        assert first["payload"]["request_state"]["answers"]["pick"] == "a"
        assert first["payload"]["request_state"]["id"] == str(first["surface_id"])
        asked_at = conn.execute("SELECT created_at FROM rvbbit.calliope_input_requests WHERE id=%s",
                                (answered,)).fetchone()["created_at"]
        assert first["created_at"] == asked_at and first["updated_at"] == at(18)  # history, not fresh work

        second = got["Publish it?"]
        assert (second["status"], second["answered_by"], second["closed_by"], second["closed_at"]) == (
            "cancelled", None, owner, at(28))
        # Open at the fork point stays open: forking at the question lets you answer it differently.
        for title in ("Answered after the fork point", "Never answered"):
            assert (got[title]["status"], got[title]["answers"], got[title]["closed_at"]) == ("open", {}, None)
            assert got[title]["payload"]["request_state"]["status"] == "open"
        # A knock on the original room is not a question for the fork.
        joined = got["Someone wants to join"]
        assert (joined["status"], joined["closed_by"]) == ("cancelled", forker) and joined["closed_at"] is not None

        # The original chat's questions are untouched.
        assert {row["status"] for row in conn.execute(
            "SELECT status FROM rvbbit.calliope_input_requests WHERE id=ANY(%s::uuid[])",
            ([answered, cancelled, late, still_open, knock, beyond],)).fetchall()} == {"answered", "cancelled", "open"}
        assert conn.execute("SELECT status FROM rvbbit.calliope_input_requests WHERE id=%s",
                            (late,)).fetchone()["status"] == "answered"
    finally:
        conn.rollback()
        conn.close()
