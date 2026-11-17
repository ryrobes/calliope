"""Computer delegation must not rename a human in the shared People directory."""
from contextlib import contextmanager
from pathlib import Path
import subprocess

import psycopg
from psycopg.rows import dict_row
import pytest

import application_teams
import auth
import desktop_browser_auth
from test_desktop_browser_auth import ACTOR, PUBLIC, RID, request, token

ROOT = Path(__file__).resolve().parents[2]
REPAIR = ROOT / 'crates/pg_rvbbit/sql/migrations/0467_computer_principal_profile.sql'
PORTRAIT = 'https://lh3.googleusercontent.com/original-portrait'


@pytest.fixture
def directory(tmp_path, monkeypatch):
    data, sock = tmp_path/'db', tmp_path/'socket'
    sock.mkdir()
    subprocess.run(['initdb','-D',str(data),'-A','trust','--no-locale','-E','UTF8'],check=True,capture_output=True)
    subprocess.run(['pg_ctl','-D',str(data),'-l',str(tmp_path/'pg.log'),'-o',f"-k {sock} -c listen_addresses=''",'-w','start'],check=True,capture_output=True)
    try:
        with psycopg.connect(host=str(sock),dbname='postgres',row_factory=dict_row) as conn:
            conn.execute('CREATE SCHEMA rvbbit')
            conn.execute(application_teams.DDL)
            conn.execute('CREATE TABLE rvbbit.calliope_desktop_runs (id uuid, requester text, status text)')
            conn.execute("INSERT INTO rvbbit.calliope_desktop_runs VALUES (%s,%s,'active')",(RID,ACTOR))
            @contextmanager
            def factory():
                yield conn
            monkeypatch.setenv('WAREHOUSE_PUBLIC_URL',PUBLIC)
            monkeypatch.setattr(auth,'JWT_SECRET','unit-test-profile-key-never-shared')
            monkeypatch.setattr(auth,'AUTH_MODE','shared')
            monkeypatch.setattr(desktop_browser_auth,'_conn_factory',factory)
            yield conn
    finally:
        subprocess.run(['pg_ctl','-D',str(data),'-m','immediate','-w','stop'],check=True,capture_output=True)


def profile(conn, email=ACTOR):
    return conn.execute('SELECT * FROM rvbbit.application_principals WHERE email=%s',(email,)).fetchone()


def observe(conn, mode, name=None, portrait=None, email=ACTOR):
    return application_teams.observe_principal_on_connection(conn,
        {'subject':email,'mode':mode},channel='calliope',display_name=name,
        avatar_provider='google',avatar_source_url=portrait)


def test_signed_computer_visits_preserve_human_profile_and_access(directory):
    conn = directory
    observe(conn,'google','Alex Owner',PORTRAIT)
    original = profile(conn)
    signed = auth.read_session_full(request(token()))
    assert signed['identity'] == ACTOR and signed['mapped']
    assert signed['via'] == 'computer' and signed['computer_run_id'] == RID
    assert not signed['name'] and not signed['picture']
    observe(conn,signed['via'],signed['name'],signed['picture'],signed['identity'])
    # Old callers or People search cannot reintroduce a device profile either.
    observe(conn,'computer','Callie’s Computer','https://lh3.googleusercontent.com/device')
    observe(conn,'password')
    current = profile(conn)
    for key in ('email','display_name','avatar_key','avatar_provider','avatar_source_url','first_seen_at'):
        assert current[key] == original[key], key
    assert conn.execute('SELECT count(*) AS n FROM rvbbit.application_principals').fetchone()['n'] == 1
    assert conn.execute('SELECT requester FROM rvbbit.calliope_desktop_runs').fetchone()['requester'] == ACTOR
    # A later genuine human profile is still allowed to update normally.
    observe(conn,'google','Alex New Name','https://lh3.googleusercontent.com/new-portrait')
    assert profile(conn)['display_name'] == 'Alex New Name'
    assert profile(conn)['avatar_source_url'].endswith('/new-portrait')


def test_computer_first_observation_uses_email_without_inventing_a_person(directory):
    observe(directory,'computer','Callie’s Computer',PORTRAIT)
    current = profile(directory)
    assert current['email'] == ACTOR
    assert current['display_name'] is None and current['avatar_provider'] is None
    assert current['avatar_source_url'] is None
    observe(directory,'google','Alex Owner',PORTRAIT)
    assert profile(directory)['display_name'] == 'Alex Owner'


@pytest.mark.parametrize('repair',[REPAIR.read_text(),application_teams.DDL],ids=['migration','service-startup'])
def test_legacy_repair_is_narrow_replayable_and_preserves_identity(directory,repair):
    conn = directory
    observe(conn,'google','Callie’s Computer',PORTRAIT)
    observe(conn,'google','Casey Computer',email='casey@example.com')
    observe(conn,'password',email='no-name@example.com')
    conn.execute("INSERT INTO rvbbit.team_members(team_id,principal_email,added_by) VALUES (%s,%s,%s)",
        (application_teams.ADMIN_TEAM_ID,ACTOR,ACTOR))
    before = profile(conn)
    conn.execute(repair)
    conn.execute(repair)
    after = profile(conn)
    assert after == {**before,'display_name':None}
    assert profile(conn,'casey@example.com')['display_name'] == 'Casey Computer'
    assert profile(conn,'no-name@example.com')['display_name'] is None
    assert conn.execute('SELECT principal_email FROM rvbbit.team_members').fetchone()['principal_email'] == ACTOR
    assert conn.execute("SELECT coalesce(nullif(display_name,''),email) AS label FROM rvbbit.application_principals WHERE email=%s",(ACTOR,)).fetchone()['label'] == ACTOR
    assert '"0467_computer_principal_profile"' in (ROOT/'crates/pg_rvbbit/src/migrations.rs').read_text()
