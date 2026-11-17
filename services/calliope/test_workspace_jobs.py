"""Real PostgreSQL coverage of deterministic ETL scheduling and recovery."""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

import calliope
import server
import workspace_jobs as jobs
from test_workspace_sql import db, OWNER, SESSION  # noqa: F401


SQL = """CREATE TABLE IF NOT EXISTS calliope.job_rollup(id integer PRIMARY KEY, amount numeric);
INSERT INTO calliope.job_rollup SELECT id, amount FROM sales.orders
ON CONFLICT(id) DO UPDATE SET amount=excluded.amount;
SELECT count(*) AS n, sum(amount) AS total FROM calliope.job_rollup;
"""


def save(db, sql=SQL, interval=43200):
    return jobs.save(db, SESSION, "sales-rollup", sql, interval, authorized_owner=OWNER)


def due(db):
    with db() as conn:
        conn.execute("UPDATE rvbbit.calliope_workspace_jobs SET next_run_at=now()-interval '3 days'")


def test_saved_schedule_runs_real_sql_and_idempotent_repeat_with_receipts(db):
    saved = save(db)
    assert saved["job"]["enabled"] is True
    assert saved["approval_required"] is False
    assert jobs.tick(db) is False  # not prematurely due
    due(db)
    assert jobs.tick(db) is True
    state = jobs.status(db, SESSION, "sales-rollup")
    run = state["runs"][0]
    assert run["status"] == "complete", run.get("error")
    assert run["trigger"] == "schedule"
    assert run["receipt"]["approved_at"] is None
    assert run["receipt"]["result"]["statements"][-1]["rows"] == [{"n": 2, "total": "30"}]
    assert run["receipt"]["action_snapshot"]["executor"] == "workspace_sql"
    assert jobs.tick(db) is False  # missed intervals did not create a backlog
    with db() as conn:
        conn.execute("UPDATE sales.orders SET amount=100 WHERE id=2")
    jobs.manage(db, SESSION, "sales-rollup", "run_now")
    jobs.manage(db, SESSION, "sales-rollup", "run_now")
    assert jobs.tick(db) is True
    assert jobs.tick(db) is False
    state = jobs.status(db, SESSION, "sales-rollup")
    assert len(state["runs"]) == 2
    assert state["runs"][0]["receipt"]["result"]["statements"][-1]["rows"] == [{"n": 2, "total": "110"}]


def test_pause_clears_queue_resume_and_identical_save_preserve_timing(db):
    original = save(db)["job"]
    assert save(db)["job"]["next_run_at"] == original["next_run_at"]
    jobs.manage(db, SESSION, "sales-rollup", "run_now")
    paused = jobs.manage(db, SESSION, "sales-rollup", "pause")["job"]
    assert not paused["enabled"] and not paused["run_requested"]
    assert save(db)["job"]["enabled"] is False
    assert jobs.tick(db) is False
    resumed = jobs.manage(db, SESSION, "sales-rollup", "resume")["job"]
    assert resumed["enabled"] and resumed["next_run_at"]
    assert jobs.manage(db, SESSION, "sales-rollup", "resume")["job"]["next_run_at"] == resumed["next_run_at"]
    replacement = save(db, SQL + " SELECT 42 AS verified;", interval=3600)["job"]
    assert replacement["id"] == original["id"]
    assert replacement["revision"] == 2
    assert replacement["interval_seconds"] == 3600


def test_manual_job_does_not_gain_a_schedule(db):
    save(db, interval=0)
    with pytest.raises(ValueError, match="manual"):
        jobs.manage(db, SESSION, "sales-rollup", "resume")
    assert jobs.tick(db) is False
    jobs.manage(db, SESSION, "sales-rollup", "run_now")
    assert jobs.tick(db)
    assert jobs.status(db, SESSION)["jobs"][0]["enabled"] is False


def test_error_rolls_back_all_writes_and_pauses_until_explicit_retry(db):
    save(db, SQL + " SELECT 1/0;")
    due(db)
    assert jobs.tick(db)
    state = jobs.status(db, SESSION, "sales-rollup")
    assert state["runs"][0]["status"] == "failed"
    assert "rolled back" in state["runs"][0]["error"]
    assert not state["jobs"][0]["enabled"]
    with db() as conn:
        assert conn.execute("SELECT to_regclass('calliope.job_rollup') AS r").fetchone()["r"] is None
    assert not jobs.tick(db)
    save(db)  # repaired SQL replaces the failed configuration
    jobs.manage(db, SESSION, "sales-rollup", "run_now")
    assert jobs.tick(db)
    assert jobs.status(db, SESSION)["runs"][0]["status"] == "complete"


def test_worker_rechecks_authority_and_controls_enforce_ownership(db, monkeypatch):
    save(db)
    with pytest.raises(PermissionError):
        jobs.manage(db, SESSION, "sales-rollup", "pause", authorized_owner="someone@example.com")
    with pytest.raises(PermissionError):
        jobs.status(db, SESSION, authorized_owner="someone@example.com")
    due(db)
    monkeypatch.setattr(calliope.calliope_dreams, "is_company_admin", lambda *args: False)
    assert jobs.tick(db)
    with db() as conn:
        row = conn.execute("SELECT * FROM rvbbit.calliope_workspace_jobs").fetchone()
        assert not row["enabled"]
        assert "Admins Team" in row["last_error"]
    assert jobs.status(db, SESSION)["jobs"][0]["last_status"] == "failed"
    assert not jobs.manage(db, SESSION, "sales-rollup", "pause")["job"]["enabled"]
    with pytest.raises(PermissionError):
        jobs.manage(db, SESSION, "sales-rollup", "resume")


def test_scheduled_dynamic_operator_cte_materialization_uses_same_write_lane(db):
    # Stand in for an arbitrary registered MCP operator, including private runtime
    # access. This exercises the actual function adapter inside scheduled INSERT.
    with db() as conn:
        conn.execute("""CREATE TABLE rvbbit.operators(name text PRIMARY KEY);
            CREATE TABLE rvbbit.operator_private_payload(payload jsonb);
            INSERT INTO rvbbit.operator_private_payload VALUES ('[{"id":1,"n":20},{"id":2,"n":30}]');
            CREATE FUNCTION rvbbit.any_connector_report() RETURNS jsonb LANGUAGE sql AS
            $$ SELECT payload FROM rvbbit.operator_private_payload $$;
            INSERT INTO rvbbit.operators VALUES ('any_connector_report');""")
    sql = """CREATE TABLE IF NOT EXISTS calliope.mcp_rollup(id integer PRIMARY KEY, n integer);
        WITH src AS (SELECT rvbbit.any_connector_report() AS payload),
        rows AS (SELECT x FROM src CROSS JOIN LATERAL jsonb_array_elements(payload) x)
        INSERT INTO calliope.mcp_rollup SELECT (x->>'id')::int, (x->>'n')::int FROM rows
        ON CONFLICT(id) DO UPDATE SET n=excluded.n;
        SELECT count(*) AS n,sum(n) AS total FROM calliope.mcp_rollup"""
    save(db, sql)
    due(db)
    assert jobs.tick(db)
    state = jobs.status(db, SESSION, "sales-rollup")
    assert state["runs"][0]["status"] == "complete", state["runs"][0]["error"]
    assert state["runs"][0]["receipt"]["result"]["statements"][-1]["rows"] == [{"n": 2, "total": 50}]
    assert "calliope_runtime" in state["runs"][0]["receipt"]["result"]["statements"][1]["executed_sql"]


def test_workers_do_not_overlap_and_pause_is_available_during_execution(db, monkeypatch):
    save(db)
    due(db)
    entered, release = threading.Event(), threading.Event()
    original = calliope.run_workspace_sql_for_session

    def wait(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(calliope, "run_workspace_sql_for_session", wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(jobs.tick, db)
        try:
            assert entered.wait(10)
            assert not jobs.tick(db)
            with pytest.raises(ValueError, match="running"):
                save(db, SQL + " SELECT 2;")
            result = jobs.manage(db, SESSION, "sales-rollup", "run_now")
            assert result["running"] and not result["job"]["run_requested"]
            paused = jobs.manage(db, SESSION, "sales-rollup", "pause")
            assert paused["running"] and not paused["job"]["enabled"]
        finally:
            release.set()
        assert future.result(timeout=15)
    state = jobs.status(db, SESSION)
    assert len(state["runs"]) == 1 and state["runs"][0]["status"] == "complete"
    assert not state["jobs"][0]["enabled"]


@pytest.mark.parametrize("committed", [False, True])
def test_restart_never_replays_an_ambiguous_run(db, committed):
    job = save(db)["job"]
    receipt_id = None
    if committed:
        receipt_id = calliope.run_workspace_sql_for_session(db, SESSION, SQL)["run"]["id"]
    with db() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_workspace_job_runs "
                     "(id,job_id,revision,session_id,sql,sql_sha256,trigger,status,action_run_id) "
                     "VALUES(%s,%s,1,%s,%s,%s,'schedule','running',%s)",
                     (uuid.uuid4(), job["id"], SESSION, SQL, job["sql_sha256"], receipt_id))
    assert not jobs.tick(db)
    state = jobs.status(db, SESSION)
    assert state["runs"][0]["status"] == ("complete" if committed else "interrupted")
    assert state["jobs"][0]["enabled"] == committed
    assert len(state["runs"]) == 1


def test_same_name_concurrent_saves_make_one_job(db):
    # Exercise duplicate creation rather than relying on sequential retries.
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda _: save(db), range(3)))
    assert len({row["job"]["id"] for row in rows}) == 1
    assert sum(row["changed"] for row in rows) == 1


def test_sql_boundaries_apply_when_saving_and_tool_schemas_are_registered(db):
    with pytest.raises(ValueError):
        save(db, "DROP TABLE sales.orders")
    with pytest.raises(ValueError):
        save(db, interval=1)
    tools = {t.name: t for t in server._build_mcp()._tool_manager.list_tools()}
    assert tools["save_workspace_job"].parameters["properties"]["interval_seconds"]["default"] == 0
    assert set(tools["manage_workspace_job"].parameters["required"]) == {"session_id", "job_name", "operation"}
    assert tools["workspace_job_status"].parameters["properties"]["limit"]["default"] == 10
    assert "save_workspace_job" in calliope._KNOWN_TOOLS


def test_native_controls_share_queue_and_pause_semantics_without_chat_dependency(db, monkeypatch):
    job = save(db)["job"]
    result = jobs.manage_by_id(db, OWNER, job["id"], "run_now")
    assert result["job"]["run_requested"]
    assert jobs.tick(db)
    assert jobs.status(db, SESSION)["runs"][0]["status"] == "complete"
    with pytest.raises(LookupError):
        jobs.manage_by_id(db, "other@example.com", job["id"], "pause")
    with pytest.raises(ValueError):
        jobs.manage_by_id(db, OWNER, "invalid-id", "pause")
    monkeypatch.setattr(calliope.calliope_dreams, "is_company_admin", lambda *args: False)
    # The UI off switch remains available even if authority is removed and the
    # original chat was deleted. It must not need the original session UUID.
    with db() as conn:
        conn.execute("DELETE FROM rvbbit.calliope_sessions WHERE id=%s", (SESSION,))
    assert not jobs.manage_by_id(db, OWNER, job["id"], "pause")["job"]["enabled"]
    with pytest.raises(PermissionError):
        jobs.manage_by_id(db, OWNER, job["id"], "resume")


def test_scheduled_app_state_uses_same_workspace_executor(db):
    saved=save(db,SQL.replace('calliope.job_rollup','calliope_apps.job_rollup'))
    due(db)
    assert jobs.tick(db)
    run=jobs.status(db,SESSION,'sales-rollup')['runs'][0]
    assert run['status']=='complete', run.get('error')
    assert run['receipt']['verification']['schema']=='calliope_apps'
    with db() as conn:
        assert conn.execute('SELECT count(*) n FROM calliope_apps.job_rollup').fetchone()['n']==2
