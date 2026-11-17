"""Rooms against a real database: members, home-chat artifacts, knocking, catch-up."""
import json
import uuid

import pytest

import application_teams
import artifact_access
import calliope
import calliope_access as access
import calliope_inputs as inputs
import calliope_rooms as rooms
import server
from test_workspace_sql import db, OWNER, SESSION  # noqa: F401

MEMBER = "michael@example.com"
TEAMMATE = "tess@example.com"
STRANGER = "stranger@example.com"
OTHER_SESSION = str(uuid.uuid4())


@pytest.fixture
def room_db(db):
    with db() as c:
        c.execute("""ALTER TABLE rvbbit.calliope_sessions ADD COLUMN title text DEFAULT 'Pricing room',
          ADD COLUMN channel text DEFAULT 'web', ADD COLUMN archived boolean DEFAULT false,
          ADD COLUMN created_at timestamptz DEFAULT now(), ADD COLUMN updated_at timestamptz DEFAULT now(),
          ADD COLUMN hermes_session_id text DEFAULT 'hermes-room';
          CREATE TABLE rvbbit.calliope_turns(id uuid PRIMARY KEY,session_id uuid,status text DEFAULT 'complete',
            ordinal integer,client_turn_id uuid,user_message text DEFAULT '',assistant_message text,
            turn_kind text NOT NULL DEFAULT 'chat',completed_at timestamptz,origin_context jsonb DEFAULT '{}'::jsonb,
            created_at timestamptz DEFAULT now(),UNIQUE(session_id,ordinal));
          CREATE TABLE rvbbit.calliope_surfaces(id uuid PRIMARY KEY,session_id uuid,turn_id uuid,ordinal int DEFAULT 1,
            kind text,title text DEFAULT '',tool_name text DEFAULT '',tool_call_id text DEFAULT '',
            lineage_key text DEFAULT '',artifact_slug text,artifact_version int,payload jsonb DEFAULT '{}'::jsonb,
            source jsonb DEFAULT '{}'::jsonb,created_at timestamptz DEFAULT now());
          CREATE TABLE rvbbit.calliope_briefs(session_id uuid);
          CREATE TABLE rvbbit.calliope_workflow_runs(session_id uuid);
          CREATE TABLE rvbbit.calliope_agent_messages(id uuid PRIMARY KEY,owner_email text,kind text,from_kind text,
            from_ref text,from_label text,to_session_id uuid,subject text,body text,payload jsonb,status text DEFAULT 'queued',
            not_before timestamptz DEFAULT now(),attempts int DEFAULT 0,last_error text,delivered_turn_id uuid,
            delivered_at timestamptz,created_at timestamptz DEFAULT now());""")
        c.execute(application_teams.DDL)
        c.execute(access.DDL)
        c.execute(server._DASHBOARDS_DDL)
        c.execute(artifact_access.DDL)
        c.execute(calliope._INBOX_DDL)
        inputs.ensure(c)
        rooms.ensure(c)
        c.execute(
            "INSERT INTO rvbbit.application_principals(email,display_name) VALUES"
            "(%s,'Owner'),(%s,'Michael'),(%s,'Tess'),(%s,'Stranger')",
            (OWNER, MEMBER, TEAMMATE, STRANGER),
        )
        c.execute("INSERT INTO rvbbit.calliope_sessions(id,owner_email,title) VALUES(%s,%s,'Elsewhere')",
                  (OTHER_SESSION, OWNER))
    return db


def _invite(db, *people, teams=()):
    snapshot = access.get_access(db, {"subject": OWNER}, SESSION)
    return access.replace_access(
        db, {"subject": OWNER}, SESSION, snapshot["session"]["access_revision"],
        people=list(people), team_ids=list(teams),
    )


def test_every_grant_is_a_membership_and_teams_take_part(room_db):
    db = room_db
    _invite(db, MEMBER)
    with db() as c:
        assert access.require_participant(c, SESSION, MEMBER)["access_role"] == "member"
        assert access.require_view(c, SESSION, OWNER)["access_role"] == "owner"
        with pytest.raises(access.CalliopeAccessError):
            access.require_view(c, SESSION, STRANGER)
        team = c.execute(
            "INSERT INTO rvbbit.teams(id,slug,name,created_by,updated_by) "
            "VALUES(gen_random_uuid(),'pricing','Pricing',%s,%s) RETURNING id",
            (OWNER, OWNER),
        ).fetchone()["id"]
        c.execute("INSERT INTO rvbbit.team_members(team_id,principal_email,added_by) VALUES(%s,%s,%s)",
                  (team, TEAMMATE, OWNER))
    _invite(db, MEMBER, teams=[str(team)])
    with db() as c:
        # A Team's members are room members, not spectators.
        assert access.require_participant(c, SESSION, TEAMMATE)["access_role"] == "member"
        assert {m["email"] for m in rooms.members(c, SESSION)} == {OWNER, MEMBER, TEAMMATE}
        assert rooms.is_room(c, SESSION)
        json_session = calliope._session_json(access.require_view(c, SESSION, MEMBER))
        assert json_session["read_only"] is False and json_session["room"] is True
        assert json_session["can_manage"] is False
        # Only a channel that can't take messages here, or archiving, is read-only.
        c.execute("UPDATE rvbbit.calliope_sessions SET channel='google_chat' WHERE id=%s", (SESSION,))
        assert access.require_view(c, SESSION, MEMBER)["access_role"] == "viewer"
        with pytest.raises(access.CalliopeAccessError):
            access.require_participant(c, SESSION, MEMBER)


def _artifact(c, slug, owner, home):
    return c.execute(
        "INSERT INTO rvbbit.dashboards(slug,name,owner_email,latest_version,home_session_id) "
        "VALUES(%s,%s,%s,3,%s::uuid) RETURNING id",
        (slug, slug.title(), owner, home),
    ).fetchone()["id"]


def test_room_members_see_and_change_the_rooms_artifacts_only_from_the_room(room_db):
    db = room_db
    _invite(db, MEMBER)
    with db() as c:
        made_here = _artifact(c, "funnel", OWNER, SESSION)
        made_elsewhere = _artifact(c, "pricing", OWNER, OTHER_SESSION)
        assert c.execute("SELECT rvbbit.artifact_can_view(%s,%s) AS ok", (made_here, MEMBER)).fetchone()["ok"]
        assert not c.execute("SELECT rvbbit.artifact_can_view(%s,%s) AS ok", (made_elsewhere, MEMBER)).fetchone()["ok"]
        editor = artifact_access.require_editor(c, "funnel", {"subject": MEMBER}, session_id=SESSION)
        assert editor["editor_role"] == "member" and editor["home_is_room"] is True
        owner_edit = artifact_access.require_editor(c, "funnel", {"subject": OWNER}, session_id=None)
        assert owner_edit["editor_role"] == "owner"
        with pytest.raises(artifact_access.ArtifactAccessError) as wrong_chat:
            artifact_access.require_editor(c, "funnel", {"subject": MEMBER}, session_id=OTHER_SESSION)
        assert wrong_chat.value.code == "ARTIFACT_EDIT_IN_HOME_CHAT"
        assert wrong_chat.value.detail["home_session_id"] == SESSION
        with pytest.raises(artifact_access.ArtifactAccessError) as hidden:
            artifact_access.require_editor(c, "pricing", {"subject": MEMBER}, session_id=SESSION)
        assert hidden.value.code == "ARTIFACT_NOT_FOUND"
        # A viewer by artifact grant (not in the room) may look, not change.
        c.execute("INSERT INTO rvbbit.artifact_view_grants(artifact_id,principal_email,granted_by) VALUES(%s,%s,%s)",
                  (made_elsewhere, MEMBER, OWNER))
        with pytest.raises(artifact_access.ArtifactAccessError) as owner_only:
            artifact_access.require_editor(c, "pricing", {"subject": MEMBER}, session_id=SESSION)
        assert owner_only.value.code == "ARTIFACT_OWNER_ONLY"
    assert server._artifact_shared_edit_needs_version(editor, None)["error"]["code"] == "EXPECTED_VERSION_REQUIRED"
    assert server._artifact_shared_edit_needs_version(editor, 3) is None
    assert server._artifact_shared_edit_needs_version({"editor_role": "owner", "home_is_room": False}, None) is None


def test_knocking_blocks_only_the_owner_and_letting_in_makes_a_member(room_db):
    db = room_db
    with db() as c:
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,ordinal,user_message,author_email) "
                  "VALUES(%s,%s,1,'Build the pricing page',%s)", (uuid.uuid4(), SESSION, OWNER))
    knock = rooms.knock(db, MEMBER, SESSION, reason="I own the pricing data", via="floor")
    again = rooms.knock(db, MEMBER, SESSION, reason="Also the discounts")
    assert again["id"] == knock["id"]
    with db() as c:
        assert rooms.pending_knock(c, SESSION, MEMBER)["reason"] == "Also the discounts"
        card = c.execute("SELECT * FROM rvbbit.calliope_input_requests WHERE session_id=%s", (SESSION,)).fetchone()
        assert card["request"]["kind"] == "join_request"
        transcript = c.execute("SELECT turn_kind,user_message,author_email FROM rvbbit.calliope_turns "
                               "WHERE turn_kind='knock'").fetchone()
        assert transcript["author_email"] == MEMBER and "Also the discounts" in transcript["user_message"]
        # The owner answers before talking again; the rest of the room keeps talking.
        assert inputs.blocking_request(c, OWNER, SESSION, poster=OWNER) == str(card["id"])
        assert inputs.blocking_request(c, OWNER, SESSION, poster=MEMBER) is None
        inbox = c.execute("SELECT owner_email,kind,state FROM rvbbit.calliope_work_items "
                          "WHERE source='calliope_room'").fetchall()
        assert [(r["owner_email"], r["kind"], r["state"]) for r in inbox] == [(OWNER, "blocked", "unread")]
    with pytest.raises(rooms.RoomError):
        rooms.knock(db, OWNER, SESSION)  # already in
    answered = inputs.change(db, OWNER, str(card["id"]), "answer",
                             {"revision": 1, "answers": {"decision": "let_in", "reply": "Welcome"}})
    assert answered["status"] == "answered" and answered.get("delivery_message_id") in (None, "None")
    with db() as c:
        assert access.require_participant(c, SESSION, MEMBER)["access_role"] == "member"
        assert rooms.pending_knock(c, SESSION, MEMBER)["status"] == "approved"
        items = {(r["owner_email"], r["state"]) for r in c.execute(
            "SELECT owner_email,state FROM rvbbit.calliope_work_items WHERE source='calliope_room'").fetchall()}
        assert (OWNER, "done") in items and (MEMBER, "unread") in items
        assert c.execute("SELECT count(*) AS n FROM rvbbit.calliope_agent_messages").fetchone()["n"] == 0
        assert c.execute("SELECT event_type FROM rvbbit.calliope_session_access_events "
                         "ORDER BY created_at DESC LIMIT 1").fetchone()["event_type"] == "knock_approved"


def test_only_the_owner_decides_a_knock(room_db):
    db = room_db
    _invite(db, MEMBER)
    with db() as c:
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,ordinal,author_email) VALUES(%s,%s,1,%s)",
                  (uuid.uuid4(), SESSION, OWNER))
    rooms.knock(db, TEAMMATE, SESSION, reason="let me in")
    with db() as c:
        card = c.execute("SELECT id FROM rvbbit.calliope_input_requests").fetchone()["id"]
    with pytest.raises(PermissionError):
        inputs.change(db, MEMBER, str(card), "answer", {"revision": 1, "answers": {"decision": "let_in"}})
    inputs.change(db, OWNER, str(card), "answer", {"revision": 1, "answers": {"decision": "not_now"}})
    with db() as c:
        with pytest.raises(access.CalliopeAccessError):
            access.require_view(c, SESSION, TEAMMATE)
        told = c.execute("SELECT title FROM rvbbit.calliope_work_items WHERE owner_email=%s", (TEAMMATE,)).fetchone()
        assert told["title"].startswith("Not now")


def test_catch_up_tells_a_member_what_happened_since_they_left(room_db):
    db = room_db
    _invite(db, MEMBER)
    with db() as c:
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,ordinal,user_message,author_email) "
                  "VALUES(%s,%s,1,'start',%s)", (uuid.uuid4(), SESSION, OWNER))
        assert rooms.catch_up(c, SESSION, MEMBER) is None  # first visit
        for n, (who, kind, text) in enumerate([(OWNER, "chat", "Switch the funnel to weekly"),
                                                (TEAMMATE, "note", "agreed"),
                                                (MEMBER, "chat", "my own message")], start=2):
            c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,ordinal,user_message,author_email,turn_kind) "
                      "VALUES(%s,%s,%s,%s,%s,%s)", (uuid.uuid4(), SESSION, n, text, who, kind))
        summary = rooms.catch_up(c, SESSION, MEMBER)
        assert summary["messages"] == 2 and summary["notes"] == 1
        assert summary["people"] == ["Owner", "Tess"]
        assert rooms.catch_up(c, SESSION, MEMBER) is None  # marked read


def test_tool_authority_follows_the_running_turn_not_a_queued_one(room_db):
    db = room_db
    _invite(db, MEMBER)
    with db() as c:
        c.execute("DROP TABLE rvbbit.calliope_workflow_runs")
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,ordinal,status,author_email) VALUES"
                  "(%s,%s,1,'running',%s),(%s,%s,2,'queued',%s),(%s,%s,3,'complete',%s)",
                  (uuid.uuid4(), SESSION, OWNER, uuid.uuid4(), SESSION, MEMBER, uuid.uuid4(), SESSION, MEMBER))
        c.execute("UPDATE rvbbit.calliope_turns SET turn_kind='note' WHERE ordinal=3")
        assert server._calliope_activity_for_hermes_session(c, "hermes-room")["owner"] == OWNER
        c.execute("UPDATE rvbbit.calliope_turns SET status='complete' WHERE ordinal=1")
        # Neither the queued message nor the note lends its author's authority.
        assert server._calliope_activity_for_hermes_session(c, "hermes-room")["owner"] == OWNER


def test_room_messages_go_to_callie_unless_meant_for_people(room_db):
    db = room_db
    session = {"id": SESSION}

    def route(text, body=None):
        return calliope._room_message_route(db, session, OWNER, "Owner", text, body or {})

    assert route("Michael, thoughts?")["source"] == "solo"
    _invite(db, MEMBER)
    assert route("anything", {"addressed_to": "people"})["addressed_to"] == "people"
    assert route("@callie chart it")["addressed_to"] == "callie"
    assert route("Callie, chart it by week")["addressed_to"] == "callie"
    # Spoken to her by name anywhere: no classifier needed (2026-10-20, Ryan's
    # "looks good, callie - thanks…" went to the people).
    for spoken in ("looks good, callie - thanks for your help with these tests!", "thanks, callie!",
                   "great work callie"):
        assert route(spoken) == {"addressed_to": "callie", "source": "explicit", "confidence": 1.0}, spoken
    assert route("@michael can you check the Q3 numbers?")["addressed_to"] == "people"
    # Naming a person is talking to them: Callie stays out of it (2026-10-20,
    # "hey bob" then "what?" got a reply from Callie).
    for spoken in ("hey michael", "michael, did you see this?", "thanks, michael!"):
        assert route(spoken) == {"addressed_to": "people", "source": "explicit", "confidence": 1.0}, spoken
    # Without Jev any doubt sends the message to Callie, as a solo chat would.
    assert route("nice, agreed") == {"addressed_to": "callie", "source": "fallback", "confidence": None}
    with db() as c:
        # Jev answers with per-choice probabilities (no "confidence").
        c.execute("""CREATE FUNCTION rvbbit.choice_question(jsonb) RETURNS jsonb LANGUAGE sql AS 'SELECT $1';
          CREATE FUNCTION rvbbit.clover_decide(state jsonb, q jsonb) RETURNS jsonb LANGUAGE sql AS $$
            SELECT jsonb_build_object('answers', jsonb_build_object('result',
              CASE WHEN state->>'message' ILIKE '%unscored%' THEN jsonb_build_object('choice', 'people')
              ELSE jsonb_build_object(
                'choice', CASE WHEN state->>'message' ILIKE '%agreed%' THEN 'people' ELSE 'callie' END,
                'probabilities', CASE
                  WHEN state->>'message' ILIKE '%maybe%' THEN '{"people": 0.48, "callie": 0.52}'::jsonb
                  WHEN state->>'message' ILIKE '%agreed%' THEN '{"people": 0.9, "callie": 0.1}'::jsonb
                  ELSE '{"people": 0.1, "callie": 0.9}'::jsonb END) END))
          $$;""")
        # The question asks who the message is addressed to, not whether it asks for work.
        question = calliope._ROOM_ROUTE_CATEGORIES
        assert "addressed to" in question["callie"] and "thanks or reacts to her" in question["callie"]
        assert "unclear who it is for, it is for the people" in question["people"]
    assert route("nice, agreed") == {"addressed_to": "people", "source": "jev", "confidence": 0.9}
    # Callie answers only when Jev is sure the message is hers; in doubt she stays quiet.
    assert route("what? maybe") == {"addressed_to": "people", "source": "jev", "confidence": 0.48}
    assert route("unscored, so who knows")["addressed_to"] == "people"
    assert route("what's churn by region?") == {"addressed_to": "callie", "source": "jev", "confidence": 0.9}


def test_a_question_in_a_room_reaches_everyone_and_closes_for_all(room_db):
    db = room_db
    _invite(db, MEMBER, TEAMMATE)
    tid, qid = uuid.uuid4(), uuid.uuid4()
    payload = {"title": "Pick a region", "prompt": "Which region first?",
               "fields": [{"key": "region", "label": "Region", "type": "text", "required": True}]}
    with db() as c:
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,ordinal,author_email) VALUES(%s,%s,1,%s)",
                  (tid, SESSION, OWNER))
        c.execute("INSERT INTO rvbbit.calliope_surfaces(id,session_id,turn_id,kind,payload) "
                  "VALUES(%s,%s,%s,'input',%s::jsonb)", (qid, SESSION, tid, json.dumps(payload)))
        for _ in range(2):  # a re-projected surface doesn't notify twice
            calliope._notify_room_question(c, SESSION, str(qid), {"kind": "input", "payload": payload})
        told = c.execute("SELECT owner_email,kind,state FROM rvbbit.calliope_work_items "
                         "WHERE source='calliope_room' ORDER BY owner_email").fetchall()
        assert [(r["owner_email"], r["kind"], r["state"]) for r in told] == [
            (MEMBER, "blocked", "unread"), (TEAMMATE, "blocked", "unread")]
    answered = inputs.change(db, TEAMMATE, str(qid), "answer", {"revision": 1, "answers": {"region": "EMEA"}})
    assert inputs.delivery(db, answered["delivery_message_id"])["answered_by"] == TEAMMATE
    with db() as c:
        states = {r["state"] for r in c.execute(
            "SELECT state FROM rvbbit.calliope_work_items WHERE source='calliope_room'").fetchall()}
        assert states == {"done"}
    # Answered once, closed for everyone.
    with pytest.raises(inputs.InputConflict):
        inputs.change(db, MEMBER, str(qid), "answer", {"revision": 1, "answers": {"region": "APAC"}})


def test_a_message_that_waited_tells_its_author_once_answered_if_they_left(room_db):
    db = room_db
    _invite(db, MEMBER)
    waited, plain = uuid.uuid4(), uuid.uuid4()
    with db() as c:
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,ordinal,author_email,origin_context) VALUES"
                  "(%s,%s,1,%s,'{\"waited_in_queue\": true}'),(%s,%s,2,%s,'{}')",
                  (waited, SESSION, MEMBER, plain, SESSION, MEMBER))
        rooms.touch_presence(c, SESSION, MEMBER)
    calliope._notify_queued_answer(db, str(waited))  # still in the room: they'll see it
    with db() as c:
        assert c.execute("SELECT count(*) AS n FROM rvbbit.calliope_work_items").fetchone()["n"] == 0
        c.execute("DELETE FROM rvbbit.calliope_room_presence")
    calliope._notify_queued_answer(db, str(plain))  # answered right away: nothing to tell
    calliope._notify_queued_answer(db, str(waited))
    with db() as c:
        rows = c.execute("SELECT owner_email,title FROM rvbbit.calliope_work_items").fetchall()
        assert [r["owner_email"] for r in rows] == [MEMBER]
        assert "answered your message" in rows[0]["title"]


def test_the_owner_makes_managers_who_share_and_let_people_in(room_db):
    db = room_db
    snapshot = _invite(db, MEMBER, TEAMMATE)
    revision = snapshot["session"]["access_revision"]
    # A member takes part but doesn't manage.
    with pytest.raises(access.CalliopeAccessError) as member:
        access.replace_access(db, {"subject": MEMBER}, SESSION, revision,
                              people=[MEMBER, TEAMMATE], managers=[MEMBER])
    assert member.value.code == "MANAGER_REQUIRED"
    made = access.replace_access(db, {"subject": OWNER}, SESSION, revision,
                                 people=[MEMBER, TEAMMATE], managers=[MEMBER])
    with db() as c:
        assert access.require_view(c, SESSION, MEMBER)["access_role"] == "manager"
        assert calliope._session_json(access.require_view(c, SESSION, MEMBER))["can_manage"] is True
        assert calliope._session_json(access.require_view(c, SESSION, TEAMMATE))["can_manage"] is False
        assert {m["email"]: m["role"] for m in rooms.members(c, SESSION)}[MEMBER] == "manager"
    # A manager changes who is in the room, but only the owner chooses managers.
    revision = made["session"]["access_revision"]
    with pytest.raises(access.CalliopeAccessError) as promote:
        access.replace_access(db, {"subject": MEMBER}, SESSION, revision,
                              people=[MEMBER, TEAMMATE], managers=[MEMBER, TEAMMATE])
    assert promote.value.code == "OWNER_CHOOSES_MANAGERS"
    with pytest.raises(access.CalliopeAccessError):
        access.replace_access(db, {"subject": MEMBER}, SESSION, revision, people=[TEAMMATE])
    narrowed = access.replace_access(db, {"subject": MEMBER}, SESSION, revision, people=[MEMBER])
    assert narrowed["manage_role"] == "manager"
    with db() as c:
        with pytest.raises(access.CalliopeAccessError):
            access.require_view(c, SESSION, TEAMMATE)
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,ordinal,author_email) VALUES(%s,%s,1,%s)",
                  (uuid.uuid4(), SESSION, OWNER))
    # A manager hears the knock and can answer it.
    rooms.knock(db, TEAMMATE, SESSION, reason="back in, please")
    with db() as c:
        card = c.execute("SELECT id FROM rvbbit.calliope_input_requests").fetchone()["id"]
        told = {r["owner_email"] for r in c.execute(
            "SELECT owner_email FROM rvbbit.calliope_work_items WHERE dedupe_key LIKE 'room-knock:%'").fetchall()}
        assert told == {OWNER, MEMBER}
    inputs.change(db, MEMBER, str(card), "answer", {"revision": 1, "answers": {"decision": "let_in"}})
    with db() as c:
        assert access.require_participant(c, SESSION, TEAMMATE)["access_role"] == "member"
        assert {r["state"] for r in c.execute(
            "SELECT state FROM rvbbit.calliope_work_items WHERE dedupe_key LIKE 'room-knock:%'").fetchall()} == {"done"}
    # The owner can take management back.
    current = access.get_access(db, {"subject": OWNER}, SESSION)
    access.replace_access(db, {"subject": OWNER}, SESSION, current["session"]["access_revision"],
                          people=[MEMBER, TEAMMATE], managers=[])
    with db() as c:
        assert access.require_view(c, SESSION, MEMBER)["access_role"] == "member"
        detail = c.execute("SELECT detail FROM rvbbit.calliope_session_access_events "
                           "ORDER BY created_at DESC LIMIT 1").fetchone()["detail"]
        assert detail["managers_removed"] == [MEMBER]


def test_sharing_someone_in_answers_their_knock(room_db):
    db = room_db
    with db() as c:
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,ordinal,author_email) VALUES(%s,%s,1,%s)",
                  (uuid.uuid4(), SESSION, OWNER))
    knock = rooms.knock(db, MEMBER, SESSION, reason="saw it in the Gallery", via="gallery")
    _invite(db, MEMBER)
    with db() as c:
        rooms.settle_knocks(c, SESSION, [MEMBER], OWNER)
        assert rooms.pending_knock(c, SESSION, MEMBER)["status"] == "approved"
        card = c.execute("SELECT status,answered_by FROM rvbbit.calliope_input_requests").fetchone()
        assert (card["status"], card["answered_by"]) == ("answered", OWNER)
        # The owner is no longer blocked by it, and its notice is cleared.
        assert inputs.blocking_request(c, OWNER, SESSION, poster=OWNER) is None
        state = c.execute("SELECT state FROM rvbbit.calliope_work_items WHERE dedupe_key=%s",
                          (f"room-knock:{knock['id']}",)).fetchone()["state"]
        assert state == "done"


def test_each_memory_names_the_people_who_could_read_its_conversation(room_db, monkeypatch):
    db = room_db
    with db() as c:
        session = access.require_view(c, SESSION, OWNER)
        assert access.memory_audience(c, session) == [f"person:{OWNER}"]
        team = c.execute(
            "INSERT INTO rvbbit.teams(id,slug,name,created_by,updated_by) "
            "VALUES(gen_random_uuid(),'pricing','Pricing',%s,%s) RETURNING id::text",
            (OWNER, OWNER),
        ).fetchone()["id"]
    _invite(db, MEMBER.upper(), teams=[team])
    with db() as c:
        assert access.memory_audience(c, session) == sorted(
            [f"person:{OWNER}", f"person:{MEMBER}", f"team:{team}"])
        # An archived Team grants nothing, so it widens nothing.
        c.execute("UPDATE rvbbit.teams SET archived=true WHERE id=%s::uuid", (team,))
        assert access.memory_audience(c, session) == sorted([f"person:{OWNER}", f"person:{MEMBER}"])
        everyone = c.execute("SELECT id::text FROM rvbbit.teams WHERE system_key='everyone'").fetchone()
    if everyone:
        snapshot = access.get_access(db, {"subject": OWNER}, SESSION)
        access.replace_access(
            db, {"subject": OWNER}, SESSION, snapshot["session"]["access_revision"],
            people=[MEMBER], team_ids=[everyone["id"]], confirm_everyone=True,
        )
        with db() as c:
            assert access.memory_audience(c, session) == ["audience:everyone"]
    # The turn sends the person's words with that audience, and nothing at all
    # when the audience can't be read (an unscoped memory is visible to all).
    # Memory is shared, so the words always carry who said them.
    monkeypatch.delenv("CALLIOPE_MEMORY_EXTRACTOR", raising=False)
    remembered = calliope._turn_memory(db, session, "  what did we decide?  ", "Owner")
    assert remembered["text"] == rooms.tag_message("Owner", "what did we decide?") and remembered["audience"]
    assert calliope._turn_memory(db, session, "   ", "Owner")["text"] == ""
    # Calliope's claims extractor writes the memories, so Hermes only recalls,
    # unless the install goes back to Hindsight's own extraction.
    assert remembered["retain"] is False
    monkeypatch.setenv("CALLIOPE_MEMORY_EXTRACTOR", "hindsight")
    assert "retain" not in calliope._turn_memory(db, session, "hi", "Owner")

    def broken():
        raise RuntimeError("database away")
    assert calliope._turn_memory(broken, session, "secret", "Owner") == {"text": "", "audience": []}
