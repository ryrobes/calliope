"""A Playbook's home thread, whole-chat saves, Admin-managed built-ins, and who may schedule one."""
import uuid

import pytest

import calliope
import playbook_access
import playbook_home
import server
from test_workspace_sql import db  # noqa: F401

OWNER = "owner@example.com"
ADMIN = "admin@example.com"
OTHER = "other@example.com"

SCHEMA = """
ALTER TABLE rvbbit.calliope_sessions ADD COLUMN title text, ADD COLUMN archived boolean DEFAULT false,
    ADD COLUMN hermes_session_id text;
CREATE TABLE rvbbit.calliope_turns (id uuid PRIMARY KEY, session_id uuid, ordinal int, turn_kind text DEFAULT 'chat',
    status text DEFAULT 'complete', user_message text, assistant_message text, author_display_name text,
    author_email text, response_receipt jsonb DEFAULT '{}');
CREATE TABLE rvbbit.calliope_surfaces (id uuid PRIMARY KEY, session_id uuid, turn_id uuid, ordinal int DEFAULT 0,
    kind text, title text, artifact_version text, payload jsonb DEFAULT '{}', source jsonb DEFAULT '{}',
    created_at timestamptz DEFAULT now());
CREATE TABLE rvbbit.capability_access_policies (capability_kind text, capability_name text, visibility text,
    owner_email text, created_by text, updated_by text, revision int DEFAULT 1, updated_at timestamptz,
    PRIMARY KEY (capability_kind, capability_name));
CREATE TABLE rvbbit.calliope_playbooks (id uuid PRIMARY KEY,
    capability_kind text GENERATED ALWAYS AS ('cap_playbook') STORED, capability_name text UNIQUE,
    owner_email text, source_session_id uuid, latest_version int DEFAULT 1, approved_version int,
    access_revision bigint DEFAULT 1, archived boolean DEFAULT false, created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(), approved_at timestamptz, approved_by text, archived_at timestamptz,
    archived_by text, playbook_kind text DEFAULT 'method');
CREATE TABLE rvbbit.calliope_playbook_versions (id uuid PRIMARY KEY, playbook_id uuid, version int, title text,
    synopsis text, readiness text DEFAULT 'ready', semantic_contract jsonb, contract_hash text,
    evidence_refs jsonb DEFAULT '[]', source_session_id uuid, source_turn_id uuid, source_from_ordinal int,
    source_through_ordinal int, sketch_id uuid, sketch_revision int, change_summary text DEFAULT '',
    created_by text, created_at timestamptz DEFAULT now(), UNIQUE (playbook_id, version));
CREATE TABLE rvbbit.capability_access_events (event_id uuid, capability_kind text, capability_name text,
    event_type text, credential_actor text, human_subject text, auth_mode text, delegated boolean, platform text,
    session_ref text, before_state jsonb, after_state jsonb, detail jsonb, created_at timestamptz DEFAULT now());
CREATE TABLE rvbbit.capability_access_grants (capability_kind text, capability_name text, team_id uuid,
    principal_email text, granted_by text);
CREATE TABLE rvbbit.teams (id uuid PRIMARY KEY, slug text, name text, description text, system_key text,
    archived boolean DEFAULT false, revision int DEFAULT 1);
CREATE TABLE rvbbit.team_members (team_id uuid, principal_email text);
CREATE TABLE rvbbit.application_principals (email text PRIMARY KEY, display_name text, last_seen_at timestamptz,
    login_allowed boolean, observation_count int);
CREATE TABLE rvbbit.viewers (session_id uuid, email text);
CREATE TABLE rvbbit.calliope_session_schedules (id uuid PRIMARY KEY, session_id uuid, owner_email text,
    playbook_id uuid, playbook_version int, mission text, schedule text, prompt text, job_name text,
    created_by text, enabled boolean DEFAULT true, hermes_job_id text, created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now());
CREATE FUNCTION rvbbit.capability_can_use(kind text, name text, subject text) RETURNS boolean LANGUAGE sql AS $$
    SELECT EXISTS (SELECT 1 FROM rvbbit.capability_access_policies p WHERE p.capability_kind=kind
        AND p.capability_name=name AND (p.visibility='everyone' OR p.owner_email=lower(subject)))
    OR EXISTS (SELECT 1 FROM rvbbit.capability_access_grants g WHERE g.capability_kind=kind
        AND g.capability_name=name AND g.principal_email=lower(subject)) $$;
CREATE FUNCTION rvbbit.calliope_session_can_view(sid uuid, subject text, strict boolean) RETURNS boolean
    LANGUAGE sql AS $$
    SELECT EXISTS (SELECT 1 FROM rvbbit.calliope_sessions s WHERE s.id=sid AND lower(s.owner_email)=lower(subject))
        OR EXISTS (SELECT 1 FROM rvbbit.viewers v WHERE v.session_id=sid AND v.email=lower(subject)) $$;
"""


def _contract(**changes):
    value = {"outcome": "A reviewed weekly brief.", "when_to_use": ["Weekly review"], "method": ["Gather", "Explain"],
             "deliverable": "A short brief.", "completion_criteria": ["Every claim has evidence"]}
    value.update(changes)
    return value


@pytest.fixture
def books(db):
    with db() as conn:
        conn.execute(SCHEMA)
        conn.execute(playbook_access.HOMES_DDL)
        admins = uuid.uuid4()
        conn.execute("INSERT INTO rvbbit.teams (id,slug,name,system_key) VALUES (%s,'admins','Admins','admins')", (admins,))
        conn.execute("INSERT INTO rvbbit.team_members VALUES (%s,%s)", (admins, ADMIN))
    return db


def _chat(db, owner=OWNER, turns=4, title="Weekly sales review"):
    session = str(uuid.uuid4())
    ids = []
    with db() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_sessions (id,owner_email,title) VALUES (%s,%s,%s)",
                     (session, owner, title))
        for ordinal in range(1, turns + 1):
            turn = str(uuid.uuid4())
            ids.append(turn)
            conn.execute(
                "INSERT INTO rvbbit.calliope_turns (id,session_id,ordinal,user_message,assistant_message,"
                "author_display_name,response_receipt) VALUES (%s,%s,%s,%s,%s,'Owner',"
                "'{\"tools\":[{\"name\":\"run_sql\",\"count\":2}]}')",
                (turn, session, ordinal, f"Question {ordinal}", f"Answer {ordinal} " + "detail " * 40),
            )
            conn.execute(
                "INSERT INTO rvbbit.calliope_surfaces (id,session_id,turn_id,kind,title,source) VALUES "
                "(%s,%s,%s,'query',%s,'{\"sql\":\"SELECT region, sum(total) FROM shop.orders GROUP BY 1\"}')",
                (str(uuid.uuid4()), session, turn, f"Result {ordinal}"),
            )
    return session, ids


def _home(db, source=None, focus=None, owner=OWNER):
    home = str(uuid.uuid4())
    with db() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_sessions (id,owner_email,title) VALUES (%s,%s,%s)",
                     (home, owner, "Playbook · from “Weekly sales review”"))
        conn.execute("INSERT INTO rvbbit.calliope_playbook_homes (session_id,owner_email,source_session_id,"
                     "focus_turn_id) VALUES (%s,%s,%s,%s)", (home, owner, source, focus))
    return home


def _draft(db, subject, session, **kwargs):
    return playbook_access.draft(db, {"subject": subject}, session_id=session, title=kwargs.pop("title", "Weekly review"),
                                 synopsis="Review the week.", contract=_contract(), **kwargs)


def test_the_first_draft_in_a_home_learns_from_the_whole_saved_chat(books):
    source, turns = _chat(books, turns=15)
    home = _home(books, source, turns[9])
    result = _draft(books, OWNER, home)["playbook"]
    # The whole chat (turns 1–15, past the old 12-turn window), anchored on the reply they pointed at.
    assert (result["source_session_id"], result["source_turn_id"]) == (source, turns[9])
    assert (result["source_from_ordinal"], result["source_through_ordinal"]) == (1, 10)
    assert result["home_session_id"] == home
    with books() as conn:
        assert conn.execute("SELECT playbook_id::text FROM rvbbit.calliope_playbook_homes WHERE session_id=%s",
                            (home,)).fetchone()["playbook_id"] == result["id"]
        assert conn.execute("SELECT title FROM rvbbit.calliope_sessions WHERE id=%s",
                            (home,)).fetchone()["title"] == "Playbook · Weekly review"
    # One home holds one Playbook: a second "new" draft there is turned into advice to revise.
    with pytest.raises(playbook_access.PlaybookError) as taken:
        _draft(books, OWNER, home, title="Another one")
    assert taken.value.code == "PLAYBOOK_HOME_TAKEN" and result["id"] in str(taken.value)
    # Revising in the home records the home as that version's source; the root keeps the saved chat.
    revised = _draft(books, OWNER, home, playbook_ref=result["id"], expected_version=1)["playbook"]
    assert revised["version"] == 2 and revised["source_session_id"] == home
    with books() as conn:
        assert str(conn.execute("SELECT source_session_id FROM rvbbit.calliope_playbooks WHERE id=%s",
                                (result["id"],)).fetchone()["source_session_id"]) == source


def test_a_room_member_can_save_a_chat_they_can_see_but_not_one_they_cannot(books):
    source, _ = _chat(books, owner=OTHER)
    with books() as conn:
        conn.execute("INSERT INTO rvbbit.viewers VALUES (%s,%s)", (source, OWNER))
    result = _draft(books, OWNER, _home(books, source))["playbook"]
    assert result["source_session_id"] == source
    with books() as conn:
        conn.execute("DELETE FROM rvbbit.viewers")
    with pytest.raises(playbook_access.PlaybookError) as gone:
        _draft(books, OWNER, _home(books, source))
    assert gone.value.code == "SOURCE_SESSION_NOT_FOUND"


def test_admins_manage_built_in_playbooks_and_everyone_else_cannot(books):
    session, _ = _chat(books, owner=ADMIN)
    other_session, _ = _chat(books, owner=OTHER)
    built_in = str(uuid.uuid4())
    with books() as conn:
        conn.execute("INSERT INTO rvbbit.capability_access_policies VALUES ('cap_playbook','playbook.answers',"
                     "'everyone',%s,'rvbbit-factory','rvbbit-factory')", (playbook_access.FACTORY_OWNER,))
        conn.execute("INSERT INTO rvbbit.calliope_playbooks (id,capability_name,owner_email,approved_version) "
                     "VALUES (%s,'playbook.answers',%s,1)", (built_in, playbook_access.FACTORY_OWNER))
        conn.execute("INSERT INTO rvbbit.calliope_playbook_versions (id,playbook_id,version,title,synopsis,"
                     "semantic_contract,contract_hash,created_by) VALUES (%s,%s,1,'Answers','Short answers.',"
                     "%s,%s,'rvbbit-factory')",
                     (str(uuid.uuid4()), built_in, '{"outcome":"x"}', "a" * 64))
    # Everyone may read the approved version; only an Admin manages it.
    seen = playbook_access.read(books, OTHER, built_in)
    assert seen["can_manage"] is False and seen["built_in"] is True
    with pytest.raises(playbook_access.PlaybookError):
        _draft(books, OTHER, other_session, playbook_ref=built_in, expected_version=1)
    draft = _draft(books, ADMIN, session, playbook_ref=built_in, expected_version=1, title="Answers")
    assert draft["can_manage"] is True and draft["playbook"]["version"] == 2
    # The Admin sees the pending draft in their inventory; others still get v1.
    mine = {item["playbook"]["id"]: item for item in playbook_access.list_visible(books, ADMIN)["playbooks"]}
    assert mine[built_in]["playbook"]["version"] == 2 and mine[built_in]["category"] == "approved"
    assert playbook_access.list_visible(books, ADMIN)["counts"]["review"] == 1
    assert playbook_access.read(books, OTHER, built_in)["playbook"]["version"] == 1
    approved = playbook_access.approve(books, {"subject": ADMIN}, built_in, 2)
    assert approved["playbook"]["approved_version"] == 2
    with pytest.raises(playbook_access.PlaybookError) as sharing:
        playbook_access.replace_access(books, {"subject": ADMIN}, built_in, 1, people=[OTHER])
    assert sharing.value.code == "BUILT_IN_PLAYBOOK_SHARING"
    archived = playbook_access.set_archived(books, {"subject": ADMIN}, built_in, 1, True)
    assert archived["playbook"]["archived"] is True
    with pytest.raises(playbook_access.PlaybookError):
        playbook_access.read(books, OTHER, built_in)


def test_a_shipped_update_never_overwrites_an_admins_edit(books, monkeypatch):
    monkeypatch.setattr(server, "_conn", books)
    assert server._ensure_factory_playbooks() == 4
    with books() as conn:
        answers = conn.execute("SELECT id::text FROM rvbbit.calliope_playbooks WHERE capability_name="
                               "'playbook.answers'").fetchone()["id"]
        decision = conn.execute("SELECT id::text FROM rvbbit.calliope_playbooks WHERE capability_name="
                                "'playbook.decision'").fetchone()["id"]
    session, _ = _chat(books, owner=ADMIN)
    _draft(books, ADMIN, session, playbook_ref=answers, expected_version=1, title="Answers")
    playbook_access.approve(books, {"subject": ADMIN}, answers, 2)
    playbook_access.set_archived(books, {"subject": ADMIN}, decision, 1, True)
    assert server._ensure_factory_playbooks() == 0  # nothing new shipped
    # A new image ships different contracts for both.
    original = server.json.loads
    def shipped(text, *args, **kwargs):
        seed = original(text, *args, **kwargs)
        if isinstance(seed, dict) and seed.get("name") in {"playbook.answers", "playbook.decision"}:
            seed["contract"]["outcome"] = seed["contract"]["outcome"] + " (updated)"
        return seed
    monkeypatch.setattr(server.json, "loads", shipped)
    assert server._ensure_factory_playbooks() == 1
    with books() as conn:
        row = conn.execute("SELECT latest_version,approved_version,archived FROM rvbbit.calliope_playbooks "
                           "WHERE id=%s", (answers,)).fetchone()
        # The update waits as a draft; the Admin's approved v2 stays live.
        assert (row["latest_version"], row["approved_version"]) == (3, 2)
        archived = conn.execute("SELECT latest_version,archived FROM rvbbit.calliope_playbooks WHERE id=%s",
                                (decision,)).fetchone()
        assert (archived["latest_version"], archived["archived"]) == (1, True)
    assert server._ensure_factory_playbooks() == 0  # and only once


def test_only_someone_who_may_use_a_playbook_can_schedule_it_and_archiving_pauses_it(books):
    private_session, _ = _chat(books, owner=OWNER)
    book = _draft(books, OWNER, private_session)["playbook"]
    playbook_access.approve(books, {"subject": OWNER}, book["id"], 1)
    their_session, _ = _chat(books, owner=OTHER)
    with pytest.raises(LookupError):
        calliope.upsert_session_schedule(books, OTHER, their_session, "0 7 * * 1", playbook_name=book["capability_name"])
    with books() as conn:
        conn.execute("INSERT INTO rvbbit.capability_access_grants VALUES ('cap_playbook',%s,NULL,%s,%s)",
                     (book["capability_name"], OTHER, OWNER))
    scheduled = calliope.upsert_session_schedule(books, OTHER, their_session, "0 7 * * 1",
                                                 playbook_name=book["capability_name"])
    assert scheduled["enabled"] is True
    playbook_access.set_archived(books, {"subject": OWNER}, book["id"], 1, True)
    with books() as conn:
        assert conn.execute("SELECT enabled FROM rvbbit.calliope_session_schedules").fetchone()["enabled"] is False


def test_the_transcript_fits_the_budget_and_keeps_the_reply_they_pointed_at(books):
    source, turns = _chat(books, turns=40)
    with books() as conn:
        full, focus = playbook_home.transcript(conn, source, turns[19])
        assert focus == 20 and "Question 1" in full and "Question 40" in full
        assert "the reply they pointed at" in full and "run_sql ×2" in full and "SELECT region" in full
        tight, _ = playbook_home.transcript(conn, source, turns[19], budget=3_000)
    assert len(tight) <= 3_000
    assert "Question 20" in tight and "Question 40" in tight and "left out to fit" in tight


def test_home_context_sends_the_transcript_once_then_points_back(books):
    source, _ = _chat(books)
    home = _home(books, source)
    session = {"id": home}
    first = calliope._playbook_home_context(books, session, OWNER)
    assert "THE SAVED CHAT" in first and "Question 1" in first and f"session_id={home}" in first
    with books() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_turns (id,session_id,ordinal,user_message) VALUES "
                     "(%s,%s,1,'Draft a playbook')", (str(uuid.uuid4()), home))
    later = calliope._playbook_home_context(books, session, OWNER)
    assert "THE SAVED CHAT" not in later and "first message of this thread" in later
    assert calliope._playbook_home_context(books, session, OTHER) == ""
    book = _draft(books, OWNER, home)["playbook"]
    standing = calliope._playbook_home_context(books, session, OWNER)
    assert f"playbook_ref={book['id']}" in standing and "not approved yet" in standing


def test_an_admin_who_leaves_the_team_keeps_a_thread_they_can_copy_from(books):
    built_in = str(uuid.uuid4())
    with books() as conn:
        conn.execute("INSERT INTO rvbbit.capability_access_policies VALUES ('cap_playbook','playbook.answers',"
                     "'everyone',%s,'rvbbit-factory','rvbbit-factory')", (playbook_access.FACTORY_OWNER,))
        conn.execute("INSERT INTO rvbbit.calliope_playbooks (id,capability_name,owner_email,approved_version) "
                     "VALUES (%s,'playbook.answers',%s,1)", (built_in, playbook_access.FACTORY_OWNER))
        conn.execute("INSERT INTO rvbbit.calliope_playbook_versions (id,playbook_id,version,title,synopsis,"
                     "semantic_contract,contract_hash,created_by) VALUES (%s,%s,1,'Answers','Short answers.',"
                     "%s,%s,'rvbbit-factory')", (str(uuid.uuid4()), built_in, '{"outcome":"x"}', "a" * 64))
    home = str(uuid.uuid4())
    with books() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_sessions (id,owner_email,title) VALUES (%s,%s,'Playbook · Answers')",
                     (home, ADMIN))
        conn.execute("INSERT INTO rvbbit.calliope_playbook_homes (session_id,owner_email,playbook_id) VALUES (%s,%s,%s)",
                     (home, ADMIN, built_in))
    assert "is a built-in Playbook" in calliope._playbook_home_context(books, {"id": home}, ADMIN)
    with books() as conn:
        conn.execute("DELETE FROM rvbbit.team_members WHERE principal_email=%s", (ADMIN,))
    assert "their own copy" in calliope._playbook_home_context(books, {"id": home}, ADMIN)
    copy = _draft(books, ADMIN, home, title="My answers")["playbook"]
    assert copy["id"] != built_in and copy["home_session_id"] is None
