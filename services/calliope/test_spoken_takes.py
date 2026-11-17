"""Spoken takes are kept per person: each viewer of an answer has their own take,
made on the server for the people who were there when it finished."""
import uuid

import pytest

import calliope
import calliope_rooms as rooms
from test_rooms import MEMBER, OTHER_SESSION, TEAMMATE, _invite, room_db  # noqa: F401
from test_workspace_sql import OWNER, SESSION, db  # noqa: F401

CFG = type("Cfg", (), {"voice_prepare_timeout_seconds": 30, "enabled": True, "voice_enabled": True})()


@pytest.fixture
def takes_db(room_db):
    with room_db() as c:
        c.execute("""ALTER TABLE rvbbit.calliope_turns ADD COLUMN IF NOT EXISTS author_email text,
                       ADD COLUMN IF NOT EXISTS response_receipt jsonb NOT NULL DEFAULT '{}'::jsonb;
                     ALTER TABLE rvbbit.calliope_sessions ADD COLUMN IF NOT EXISTS specialist_key text""")
        c.execute(calliope._RESPONSE_PREFERENCES_DDL)
        c.execute(calliope._TAKES_DDL)
    return room_db


def _turn(db, session=SESSION, *, author=OWNER, ordinal=1, kind="chat", status="complete", receipt="{}"):
    turn = str(uuid.uuid4())
    with db() as c:
        c.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,ordinal,turn_kind,status,assistant_message,"
                  "author_email,response_receipt) VALUES(%s,%s,%s,%s,%s,'Revenue is up 8%%.',%s,%s::jsonb)",
                  (turn, session, ordinal, kind, status, author, receipt))
    return turn


def _render(script):
    return {"id": str(uuid.uuid4()), "version": calliope._VOICE_RENDER_VERSION, "mode": "conversational",
            "script": script, "created_at": "2026-10-20T22:00:00+00:00"}


def _prefer(db, email, *, conversational=False, read_aloud="off"):
    with db() as c:
        c.execute("INSERT INTO rvbbit.calliope_response_preferences(owner_email,conversational_answers,read_aloud_mode) "
                  "VALUES(%s,%s,%s)", (email, conversational, read_aloud))


def test_each_person_in_a_room_keeps_their_own_take(takes_db):
    _invite(takes_db, MEMBER)
    turn_id = _turn(takes_db)
    mine, theirs = _render("Up eight, nice."), _render("Revenue rose eight percent.")
    calliope._store_voice_render(takes_db, CFG, OWNER, {"id": turn_id, "session_id": SESSION}, mine, keep_current=False)
    calliope._store_voice_render(takes_db, CFG, MEMBER, {"id": turn_id, "session_id": SESSION}, theirs, keep_current=False)
    # A reopened chat shows each person their own take, never the last one written.
    assert calliope._viewer_takes(takes_db, OWNER, SESSION)[turn_id]["presentation"]["script"] == "Up eight, nice."
    assert calliope._viewer_takes(takes_db, MEMBER, SESSION)[turn_id]["presentation"]["script"] == "Revenue rose eight percent."
    # Audio is only ever someone's own take.
    assert calliope._voice_render_for_audio(takes_db, MEMBER, turn_id, theirs["id"])["script"] == theirs["script"]
    with pytest.raises(LookupError):
        calliope._voice_render_for_audio(takes_db, MEMBER, turn_id, mine["id"])
    # A new take keeps the replaced one as an earlier take, still playable.
    calliope._store_voice_render(takes_db, CFG, OWNER, {"id": turn_id, "session_id": SESSION, "take": mine},
                                 _render("Eight up!"), keep_current=False)
    assert [t["script"] for t in calliope._voice_takes_for_turn(takes_db, OWNER, turn_id)] == ["Up eight, nice."]
    assert calliope._voice_render_for_audio(takes_db, OWNER, turn_id, mine["id"])["script"] == "Up eight, nice."
    assert calliope._voice_takes_for_turn(takes_db, MEMBER, turn_id) == []


def test_turns_never_carry_the_old_shared_take():
    row = {"id": uuid.uuid4(), "session_id": uuid.uuid4(), "response_receipt": {
        "presentation": {"script": "someone's take"}, "presentation_takes": [], "voice": {}, "citations": {"version": 1}}}
    assert calliope._turn_json(row)["response_receipt"] == {"citations": {"version": 1}}


def test_old_takes_move_to_their_owner_only_where_nobody_else_could_see(takes_db):
    _invite(takes_db, MEMBER)
    legacy = '{"presentation": {"id": "x", "script": "old take"}, "presentation_takes": [{"id": "y"}]}'
    private = _turn(takes_db, OTHER_SESSION, receipt=legacy)
    shared = _turn(takes_db, SESSION, receipt=legacy)
    with takes_db() as c:
        c.execute(calliope._TAKES_DDL)
        c.execute(calliope._TAKES_DDL)  # every start runs it; once copied, a take is never copied again
        rows = c.execute("SELECT turn_id::text,viewer,render->>'script' AS script,jsonb_array_length(earlier) AS n "
                         "FROM rvbbit.calliope_takes").fetchall()
    assert [(r["turn_id"], r["viewer"], r["script"], r["n"]) for r in rows] == [(private, OWNER, "old take", 1)]
    assert shared not in {r["turn_id"] for r in rows}  # in a room there is no telling whose it was


def test_server_takes_go_to_the_asker_the_owner_and_room_members_who_were_there(takes_db):
    # A chat nobody else can see: the owner. Callie's take is on unless they turned it off,
    # and it is one kind of take whatever their read-aloud setting.
    private = _turn(takes_db, OTHER_SESSION)
    with takes_db() as c:
        assert [(p["viewer"], p["mode"]) for p in calliope._server_take_people(c, CFG, private)] == [(OWNER, "expressive")]
    _prefer(takes_db, OWNER, conversational=False)
    with takes_db() as c:
        assert calliope._server_take_people(c, CFG, private) == []
        c.execute("UPDATE rvbbit.calliope_response_preferences SET conversational_answers=true WHERE owner_email=%s", (OWNER,))

    # A room: the person who asked plus whoever had it open; not people who were away.
    _invite(takes_db, MEMBER, TEAMMATE)
    _prefer(takes_db, MEMBER, conversational=True, read_aloud="expressive")
    _prefer(takes_db, TEAMMATE, conversational=True)
    asked = _turn(takes_db, SESSION, author=MEMBER)
    with takes_db() as c:
        rooms.touch_presence(c, SESSION, OWNER)
        people = calliope._server_take_people(c, CFG, asked)
    assert sorted((p["viewer"], p["mode"]) for p in people) == sorted([(MEMBER, "expressive"), (OWNER, "expressive")])

    # Without a voice installed the take has no delivery tags; unfinished or system turns get none.
    no_voice = type("Cfg", (), {"enabled": True, "voice_enabled": False})()
    with takes_db() as c:
        assert {p["mode"] for p in calliope._server_take_people(c, no_voice, asked)} == {"conversational"}
        assert calliope._server_take_people(c, CFG, _turn(takes_db, SESSION, author=MEMBER, ordinal=2, status="running")) == []
        assert calliope._server_take_people(c, CFG, _turn(takes_db, SESSION, author=MEMBER, ordinal=3, kind="sheet_refresh")) == []


def test_server_takes_only_start_inside_the_running_server(monkeypatch):
    started = []
    monkeypatch.setattr(calliope, "_SERVER_TAKES_POOL", type("Pool", (), {"submit": lambda self, *a: started.append(a)})())
    monkeypatch.setattr(calliope, "_SERVER_TAKES_ENABLED", False)
    calliope._schedule_server_takes(lambda: None, "turn-1")
    assert started == []
    monkeypatch.setattr(calliope, "_SERVER_TAKES_ENABLED", True)
    monkeypatch.setenv("CALLIOPE_SERVER_TAKES", "off")
    calliope._schedule_server_takes(lambda: None, "turn-1")
    assert started == []
    monkeypatch.delenv("CALLIOPE_SERVER_TAKES")
    calliope._schedule_server_takes(lambda: None, "turn-1")
    assert len(started) == 1 and started[0][0] is calliope._server_takes_for_turn and started[0][2] == "turn-1"


def test_callies_take_is_on_by_default_and_fast_now_means_voice_on(takes_db):
    assert calliope._default_response_preferences()["conversational_answers"] is True
    saved = calliope._normalize_response_preferences({"version": 1, "read_aloud_mode": "fast",
                                                      "conversational_answers": True})
    assert saved["read_aloud_mode"] == "expressive"
    with takes_db() as c:
        c.execute(calliope._AGENT_SPEECH_PERSONAS_DDL)
        c.execute("CREATE TABLE rvbbit.settings(key text PRIMARY KEY, value jsonb NOT NULL, updated_at timestamptz)")
    _prefer(takes_db, OWNER, conversational=False, read_aloud="fast")
    _prefer(takes_db, MEMBER, conversational=False)
    with takes_db() as c:
        c.execute(calliope._PERSONA_DEFAULT_DDL)  # once, for everyone
        rows = c.execute("SELECT owner_email,conversational_answers,read_aloud_mode "
                         "FROM rvbbit.calliope_response_preferences ORDER BY owner_email").fetchall()
        assert [(r["conversational_answers"], r["read_aloud_mode"]) for r in rows] == [(True, "expressive"), (True, "off")]
        c.execute("UPDATE rvbbit.calliope_response_preferences SET conversational_answers=false WHERE owner_email=%s",
                  (MEMBER,))
        c.execute(calliope._PERSONA_DEFAULT_DDL)  # every start runs it; a later "off" sticks
        assert c.execute("SELECT conversational_answers FROM rvbbit.calliope_response_preferences WHERE owner_email=%s",
                         (MEMBER,)).fetchone()["conversational_answers"] is False


def test_delivery_direction_is_kept_on_the_account_per_agent(takes_db):
    with takes_db() as c:
        c.execute(calliope._AGENT_SPEECH_PERSONAS_DDL)
        c.execute("CREATE TABLE IF NOT EXISTS rvbbit.settings(key text PRIMARY KEY, value jsonb NOT NULL, updated_at timestamptz)")
        c.execute(calliope._PERSONA_DEFAULT_DDL)
    assert calliope._stored_voice_direction(takes_db, OWNER, "calliope") == ""
    calliope._save_voice_direction(takes_db, OWNER, "calliope", "  slower,\x00 land the numbers ")
    calliope._save_voice_direction(takes_db, OWNER, "analytical-skeptic", "deadpan")
    assert calliope._stored_voice_direction(takes_db, OWNER, "calliope") == "slower, land the numbers"
    assert calliope._stored_voice_direction(takes_db, OWNER, "analytical-skeptic") == "deadpan"
    assert calliope._stored_voice_direction(takes_db, MEMBER, "calliope") == ""


def test_each_person_keeps_their_take_of_each_working_update(takes_db):
    _invite(takes_db, MEMBER)
    turn_id = _turn(takes_db, status="running")
    calliope._save_take_note(takes_db, OWNER, turn_id, "Checking  the orders\ntable now.", "On it, pulling orders.")
    calliope._save_take_note(takes_db, MEMBER, turn_id, "Checking the orders table now.", "Oh! Off to the orders table!")
    calliope._save_take_note(takes_db, OWNER, turn_id, "Checking the orders table now.", "Orders table, on it.")  # a retake replaces
    calliope._save_take_note(takes_db, "stranger@example.com", turn_id, "Checking the orders table now.", "nope")
    assert calliope._viewer_take_notes(takes_db, OWNER, SESSION) == {
        turn_id: [{"note": "Checking the orders table now.", "script": "Orders table, on it."}]}
    assert calliope._viewer_take_notes(takes_db, MEMBER, SESSION)[turn_id][0]["script"] == "Oh! Off to the orders table!"
    assert calliope._viewer_take_notes(takes_db, "stranger@example.com", SESSION) == {}
