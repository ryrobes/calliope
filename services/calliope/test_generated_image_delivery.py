"""Image completion stays attached to the initiating turn after MCP disconnects."""
import os
import threading
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

import expert_portraits
import server


@pytest.fixture
def db():
    dsn = os.environ.get("IMAGE_DELIVERY_TEST_DSN")
    if not dsn:
        pytest.skip("requires an empty disposable PostgreSQL database")
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute("SELECT to_regnamespace('rvbbit')").fetchone()[0] is None
        conn.execute("""
            CREATE SCHEMA rvbbit;
            CREATE TABLE rvbbit.calliope_sessions(id uuid PRIMARY KEY,owner_email text,hermes_session_id text);
            CREATE TABLE rvbbit.calliope_design_profiles(id uuid PRIMARY KEY,name text);
            CREATE TABLE rvbbit.calliope_design_profile_versions(id uuid PRIMARY KEY,profile_id uuid,version int,tokens jsonb);
            CREATE TABLE rvbbit.calliope_turns(id uuid PRIMARY KEY,session_id uuid REFERENCES rvbbit.calliope_sessions,design_profile_version_id uuid,status text,author_email text DEFAULT 'owner@example.com');
            CREATE TABLE rvbbit.calliope_specialist_runs(hermes_session_id text,owner_email text,source_session_id uuid,source_turn_id uuid,status text);
            CREATE TABLE rvbbit.generated_images(id uuid PRIMARY KEY DEFAULT gen_random_uuid(),owner_email text,prompt text,model text,png bytea);
            CREATE TABLE rvbbit.calliope_surfaces(id uuid PRIMARY KEY,session_id uuid REFERENCES rvbbit.calliope_sessions,turn_id uuid REFERENCES rvbbit.calliope_turns,
                ordinal int,kind text,title text,tool_name text,tool_call_id text,lineage_key text,parent_surface_id uuid,artifact_slug text,artifact_version int,
                payload jsonb,source jsonb,presentation jsonb,design_profile_version_id uuid,created_at timestamptz DEFAULT now(),
                UNIQUE(session_id,tool_call_id,lineage_key));
        """)
        try:
            yield lambda: psycopg.connect(dsn, row_factory=dict_row)
        finally:
            conn.execute("DROP SCHEMA rvbbit CASCADE")


def seed(factory):
    session, turn = str(uuid4()), str(uuid4())
    with factory() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_sessions VALUES(%s,'owner@example.com','calliope_original')", (session,))
        conn.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status) VALUES(%s,%s,'running')", (turn, session))
    return {"session_id": session, "turn_id": turn}


def authorize(monkeypatch, session="calliope_original", trusted=True):
    monkeypatch.setattr(server, "_is_trusted_hermes_transport", lambda _id: trusted)
    token = server._AUTHORIZATION_CONTEXT.set(SimpleNamespace(
        subject="owner@example.com", client_id="hermes", session_ref=session))
    return token


def test_late_image_is_committed_once_to_original_turn_without_a_tool_reply(db, monkeypatch):
    destination = seed(db)
    monkeypatch.setattr(server, "_conn", db)
    token = authorize(monkeypatch)
    try:
        captured = server._image_stage_destination("owner@example.com")
    finally:
        server._AUTHORIZATION_CONTEXT.reset(token)
    assert captured == destination
    started, release = threading.Event(), threading.Event()
    errors = []
    monkeypatch.setattr(expert_portraits, "_config", lambda _conn: {"model": "image-test"})

    def generate(*_args):
        started.set()
        assert release.wait(5)
        return b"\x89PNG\r\n\x1a\nfixture"

    monkeypatch.setattr(expert_portraits, "_generate_png", generate)

    def worker():
        try:
            # Deliberately discard the response: nobody projects this result.
            expert_portraits.generate_image(db, "owner@example.com", "A chart", stage_destination=captured)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    assert started.wait(5)
    with db() as conn:
        conn.execute("UPDATE rvbbit.calliope_turns SET status='complete'")
        conn.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status) VALUES(%s,%s,'running')", (uuid4(), destination["session_id"]))
    release.set()
    thread.join(10)
    assert not thread.is_alive() and not errors
    with db() as conn:
        image = conn.execute("SELECT * FROM rvbbit.generated_images").fetchone()
        rows = conn.execute("SELECT * FROM rvbbit.calliope_surfaces").fetchall()
        assert len(rows) == 1
        assert str(rows[0]["turn_id"]) == destination["turn_id"]
        assert rows[0]["payload"]["image_url"].endswith(str(image["id"]))
        expert_portraits._publish_generated_image(conn, captured, str(image["id"]), "image-test")
        assert conn.execute("SELECT count(*) AS n FROM rvbbit.calliope_surfaces").fetchone()["n"] == 1


def test_specialist_destination_uses_its_parent_and_untrusted_calls_cannot_attach(db, monkeypatch):
    destination = seed(db)
    with db() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_specialist_runs VALUES('iris','owner@example.com',%s,%s,'running')", tuple(destination.values()))
        conn.execute("UPDATE rvbbit.calliope_turns SET status='complete'")
    monkeypatch.setattr(server, "_conn", db)
    token = authorize(monkeypatch, "iris")
    try:
        assert server._image_stage_destination("owner@example.com") == destination
        assert server._image_stage_destination("someone-else@example.com") is None
        monkeypatch.setattr(server, "_is_trusted_hermes_transport", lambda _id: False)
        assert server._image_stage_destination("owner@example.com") is None
    finally:
        server._AUTHORIZATION_CONTEXT.reset(token)


def test_failed_image_creates_no_stage_artifact(db, monkeypatch):
    destination = seed(db)
    monkeypatch.setattr(expert_portraits, "_config", lambda _conn: {"model": "image-test"})
    monkeypatch.setattr(expert_portraits, "_generate_png", lambda *_args: None)
    result = expert_portraits.generate_image(db, "owner@example.com", "A chart", stage_destination=destination)
    assert result["error"]["code"] == "NO_IMAGE"
    with db() as conn:
        assert conn.execute("SELECT count(*) AS n FROM rvbbit.generated_images").fetchone()["n"] == 0
        assert conn.execute("SELECT count(*) AS n FROM rvbbit.calliope_surfaces").fetchone()["n"] == 0


def test_generated_metadata_reaches_stage_and_exact_copies_only_in_same_thread(db, monkeypatch):
    import calliope
    import image_metadata
    destination = seed(db)
    monkeypatch.setattr(expert_portraits, '_config', lambda _: {'model': 'image-test'})
    monkeypatch.setattr(expert_portraits, '_generate_png', lambda *_: b'PNG test bytes')
    result = expert_portraits.generate_image(db, 'owner@example.com', 'Show lead lifecycle and human handoff',
        stage_destination=destination, title='Lifecycle mock v2', purpose='dashboard mockup', creator_session='iris-session')
    with db() as conn:
        original = conn.execute('SELECT * FROM rvbbit.calliope_surfaces').fetchone()
        assert original['title'] == 'Lifecycle mock v2'
        assert original['payload']['image_metadata']['generation_brief'] == 'Show lead lifecycle and human handoff'
        assert original['payload']['content_sha256'] == result['content_sha256']
        turn2 = str(uuid4())
        conn.execute("INSERT INTO rvbbit.calliope_turns(id,session_id,status) VALUES(%s,%s,'complete')", (turn2,destination['session_id']))
    copied = {'kind':'document','title':'renamed mock','tool_name':'hermes_local_file',
              'tool_call_id':'copied-file','lineage_key':'copied-file',
              'payload':{'mime_type':'image/png','content_sha256':result['content_sha256'],'original_name':'renamed.png'},'source':{}}
    same = calliope._insert_surfaces(db,destination['session_id'],turn2,[copied])[0]
    assert same['payload']['image_metadata']['same_image_as'] == str(original['id'])
    assert same['payload']['image_metadata']['creator_session'] == 'iris-session'
    compact, _ = calliope._compact_surface_context(db, destination['session_id'], None, candidate_limit=120)
    copied_context = next(i for i in compact if i['surface_id'] == same['id'])
    assert copied_context['image']['generation_brief'] == 'Show lead lifecycle and human handoff'
    import stage_context
    assert stage_context.descriptor(copied_context)['image']['purpose'] == 'dashboard mockup'
    another = seed(db)
    other = calliope._insert_surfaces(db,another['session_id'],another['turn_id'],[copied])[0]
    assert 'generation_brief' not in other['payload']['image_metadata']
    with db() as conn:
        legacy = {'kind':'image','title':'Generated image','payload':{'image_id':result['image_id']}}
        recovered = image_metadata.enrich_rows(conn,destination['session_id'],[legacy])[0]
        assert recovered['payload']['image_metadata']['generation_brief'] == 'Show lead lifecycle and human handoff'


def test_image_tool_exposes_and_forwards_optional_metadata(monkeypatch):
    import inspect
    assert {'title', 'purpose'} <= set(inspect.signature(server._mcp_generate_image).parameters)
    monkeypatch.setattr(server, '_caller', lambda: ('owner@example.com',))
    monkeypatch.setattr(server, '_logged', lambda name, args, fn: fn())
    monkeypatch.setattr(server, '_image_stage_destination', lambda _: {'session_id':'session', 'turn_id':'turn'})
    received = {}
    def generate(*args, **kwargs):
        received.update(kwargs)
        return {'ok':True}
    monkeypatch.setattr(expert_portraits, 'generate_image', generate)
    assert server._mcp_generate_image('prompt', title='Mock v2', purpose='dashboard mockup') == {'ok':True}
    assert received['title'] == 'Mock v2' and received['purpose'] == 'dashboard mockup'
