"""Guest queries against a real Postgres, not a mocked connection.

The unit tests stub tool_run_sql. This one starts Postgres and checks the
failure that hid there: route_explain must not run as the guest, and the
guest login must not be able to become the superuser.
"""
import os
import socket
import subprocess
import time
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

import demo_mode
import server

PASSWORD = "demo-guest-password-1"
IMAGE = os.environ.get("DEMO_TEST_POSTGRES_IMAGE", "postgres:16-alpine")


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def guest_db():
    explicit = os.environ.get("DEMO_TEST_DSN", "").strip()
    if explicit:
        _install(explicit)
        yield explicit, explicit
        return
    port = _free_port()
    name = "calliope-demo-pg-" + uuid.uuid4().hex[:8]
    try:
        subprocess.run(
            ["docker", "run", "-d", "--name", name, "-e", "POSTGRES_PASSWORD=postgres",
             "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-p", f"127.0.0.1:{port}:5432", IMAGE],
            check=True, capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"Postgres container did not start: {exc}")
    admin = f"host=127.0.0.1 port={port} dbname=postgres user=postgres"
    for _ in range(60):
        try:
            with psycopg.connect(admin, connect_timeout=2) as conn:
                conn.execute("SELECT 1")
            break
        except psycopg.OperationalError:
            time.sleep(0.5)
    else:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
        pytest.fail("Postgres did not become ready")
    try:
        _install(admin)
        yield admin, admin
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


def _install(admin):
    with psycopg.connect(admin, autocommit=True) as conn:
        database = conn.execute("SELECT current_database()").fetchone()[0]
    cleanup = [
        "DROP SCHEMA IF EXISTS rvbbit CASCADE",
        "DROP SCHEMA IF EXISTS public_demo CASCADE",
        "DROP TABLE IF EXISTS public.pg_stat_statements",
        "DROP OWNED BY calliope_demo_login",
        "DROP OWNED BY calliope_demo_guest",
        "DROP ROLE IF EXISTS calliope_demo_login",
        "DROP ROLE IF EXISTS calliope_demo_guest",
    ]
    with psycopg.connect(admin, autocommit=True) as conn:
        for statement in cleanup:
            try:
                conn.execute(statement)
            except psycopg.Error:
                pass
    statements = [
        "CREATE SCHEMA rvbbit",
        """CREATE FUNCTION rvbbit.route_explain(sql text) RETURNS jsonb
           LANGUAGE sql AS $$ SELECT jsonb_build_object(
             'safe_select', true, 'reason', 'test', 'candidates', '[]'::jsonb) $$""",
        "REVOKE ALL ON SCHEMA rvbbit FROM PUBLIC",
        "CREATE ROLE calliope_demo_guest NOLOGIN",
        """CREATE ROLE calliope_demo_login LOGIN NOSUPERUSER NOCREATEDB
           NOCREATEROLE NOBYPASSRLS INHERIT PASSWORD 'demo-guest-password-1'""",
        "GRANT calliope_demo_guest TO calliope_demo_login",
        f'REVOKE TEMP ON DATABASE "{database}" FROM PUBLIC',
        f'REVOKE TEMP ON DATABASE "{database}" FROM calliope_demo_login',
        f'GRANT TEMP ON DATABASE "{database}" TO CURRENT_USER',
        "CREATE TABLE public.pg_stat_statements (query text)",
        "GRANT SELECT ON public.pg_stat_statements TO PUBLIC",
        "REVOKE ALL ON public.pg_stat_statements FROM PUBLIC",
        "REVOKE ALL ON public.pg_stat_statements FROM calliope_demo_login",
        "CREATE SCHEMA public_demo",
        "GRANT USAGE ON SCHEMA public_demo TO calliope_demo_guest",
        "CREATE TABLE public_demo.orders (id int, region text, amount numeric)",
        "INSERT INTO public_demo.orders VALUES (1, 'North', 10)",
        "GRANT SELECT ON public_demo.orders TO calliope_demo_guest",
        f'GRANT CONNECT ON DATABASE "{database}" TO calliope_demo_guest',
        f'GRANT CONNECT ON DATABASE "{database}" TO calliope_demo_login',
    ]
    with psycopg.connect(admin, autocommit=True) as conn:
        for statement in statements:
            conn.execute(statement)


def test_guest_query_validates_as_the_service_and_runs_as_the_login(guest_db, monkeypatch):
    admin, _ = guest_db
    monkeypatch.setenv("WAREHOUSE_DSN", admin)
    monkeypatch.setenv("CALLIOPE_DEMO_GUEST_PASSWORD", PASSWORD)
    monkeypatch.setenv("CALLIOPE_DEMO_MODE", "1")
    monkeypatch.setattr(server, "DSN", admin)
    sql = "SELECT region, SUM(amount) AS revenue FROM public_demo.orders GROUP BY region"
    result = server.tool_run_sql(sql, _guest=True, _max_rows=10, _statement_timeout_ms=5000)
    assert "error" not in result, result
    row = result["rows"][0]
    assert row["region"] == "North"
    assert int(row["revenue"]) == 10
    assert "route_explain" not in str(result)


def test_guest_login_cannot_become_superuser_or_read_internals(guest_db):
    admin, _ = guest_db
    guest = psycopg.conninfo.make_conninfo(admin, user="calliope_demo_login", password=PASSWORD)
    with psycopg.connect(guest, row_factory=dict_row) as conn:
        who = conn.execute("SELECT current_user AS user_name, rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
        assert who["user_name"] == "calliope_demo_login"
        assert who["rolsuper"] is False
        with pytest.raises(psycopg.Error):
            conn.execute("SELECT set_config('role', 'postgres', false)")
        conn.rollback()
        with pytest.raises(psycopg.Error):
            conn.execute("CREATE TEMP TABLE demo_should_fail(id int)")
        conn.rollback()
        with pytest.raises(psycopg.Error):
            conn.execute("SELECT * FROM public.pg_stat_statements")
        conn.rollback()
        with pytest.raises(psycopg.Error):
            conn.execute("SELECT rvbbit.route_explain('SELECT 1')")
