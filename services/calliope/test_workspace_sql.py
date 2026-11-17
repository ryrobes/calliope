"""Exercise workspace work and rollback against a disposable PostgreSQL database."""
import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

import auth
import calliope
import server
import workspace_sql as workspace
from test_zen_chat_layout import zen_page  # noqa: F401

OWNER = "admin@example.com"
SESSION = str(uuid.uuid4())
SEED = "workspace-test-only-secret"


@pytest.fixture
def db(monkeypatch):
    admin = os.environ.get("ACTIVITY_TEST_ADMIN_DSN")
    if not admin:
        pytest.skip("ACTIVITY_TEST_ADMIN_DSN must identify a disposable test cluster")
    name = "workspace_test_" + uuid.uuid4().hex[:10]
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {name} TEMPLATE template0 ENCODING 'UTF8'")

    def factory():
        # Match the real service's writable autocommit connection.
        return psycopg.connect(make_conninfo(admin, dbname=name), row_factory=dict_row, autocommit=True)

    try:
        with factory() as conn:
            conn.execute("CREATE SCHEMA rvbbit; CREATE SCHEMA sales; CREATE TABLE rvbbit.calliope_sessions(id uuid PRIMARY KEY,owner_email text)")
            conn.execute("INSERT INTO rvbbit.calliope_sessions VALUES(%s,%s)", (SESSION, OWNER))
            conn.execute(calliope._ACTION_DDL)
            conn.execute("CREATE TABLE sales.orders(id integer, amount numeric); INSERT INTO sales.orders VALUES(1,10),(2,20)")
            conn.execute("CREATE VIEW sales.order_view AS SELECT * FROM sales.orders")
        monkeypatch.setattr(auth, "JWT_SECRET", SEED)
        monkeypatch.setattr(calliope.calliope_dreams, "is_company_admin", lambda factory, owner: owner == OWNER)
        yield factory
    finally:
        role = "calliope_work_" + hashlib.sha256(name.encode()).hexdigest()[:16]
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f"DROP DATABASE {name} WITH (FORCE)")
            conn.execute(f"DROP ROLE IF EXISTS {role}")


def execute(db, sql):
    return workspace.execute(db, workspace.plan(sql), seed=SEED, schema_allowed=lambda s: s in {"calliope", "sales"})


def test_create_populate_join_index_alter_and_materialize_without_approval(db):
    result = calliope.run_workspace_sql_for_session(db, SESSION, """
        CREATE TABLE calliope.rollup AS SELECT count(*) AS n, sum(amount) AS amount FROM sales.order_view;
        ALTER TABLE calliope.rollup ADD COLUMN note text;
        UPDATE calliope.rollup SET note='Résumé; keep this together';
        CREATE INDEX rollup_n ON calliope.rollup(n);
        CREATE VIEW calliope.summary AS SELECT * FROM calliope.rollup;
        CREATE MATERIALIZED VIEW calliope.snapshot AS SELECT * FROM calliope.summary;
        REFRESH MATERIALIZED VIEW calliope.snapshot;
        ANALYZE calliope.rollup;
        SELECT r.amount, o.id, r.note FROM calliope.snapshot r JOIN sales.orders o ON o.id=r.n;
    """, authorized_owner=OWNER)
    assert result["approval_required"] is False
    run = result["run"]
    assert run["status"] == "complete", run.get("error")
    assert run["approved_at"] is None
    assert run["action_snapshot"]["executor"] == "workspace_sql"
    assert run["verification"]["committed"] is True
    assert run["result"]["statements"][-1]["rows"][0]["note"] == "Résumé; keep this together"
    with db() as conn:
        assert conn.execute("SELECT count(*) n FROM sales.orders").fetchone()["n"] == 2
        assert conn.execute("SELECT sum(amount) a FROM calliope.snapshot").fetchone()["a"] == 30


def test_failed_batch_rolls_back_every_statement_and_can_be_repaired(db):
    execute(db, "CREATE TABLE calliope.output(id integer PRIMARY KEY); INSERT INTO calliope.output VALUES(1)")
    result = calliope.run_workspace_sql_for_session(db, SESSION, """
        DELETE FROM calliope.output;
        CREATE TABLE calliope.partial(id integer);
        INSERT INTO calliope.output VALUES(2),(2);
    """)
    assert result["run"]["status"] == "failed"
    assert "Statement 3 failed" in result["run"]["error"]
    assert result["approval_required"] is False
    with db() as conn:
        assert conn.execute("SELECT id FROM calliope.output").fetchall() == [{"id": 1}]
        assert conn.execute("SELECT to_regclass('calliope.partial') r").fetchone()["r"] is None
    repaired = calliope.run_workspace_sql_for_session(db, SESSION, "UPDATE calliope.output SET id=2; SELECT * FROM calliope.output")
    assert repaired["run"]["status"] == "complete"
    assert repaired["run"]["approved_at"] is None


def test_existing_admin_tool_routes_workspace_batches_and_preserves_other_approvals(db):
    result = calliope.administer_local_sql_for_session(db, SESSION,
        "CREATE TABLE calliope.output(id integer); INSERT INTO calliope.output VALUES(1)")
    assert result["execution_mode"] == "workspace_sql"
    assert result["run"]["status"] == "complete", result["run"].get("error")
    outside = calliope.administer_local_sql_for_session(db, SESSION, "CREATE TABLE sales.other(id integer)")
    assert outside["approval_required"] is True
    with db() as conn:
        assert conn.execute("SELECT count(*) n FROM rvbbit.calliope_action_runs WHERE status='planned'").fetchone()["n"] == 1


def test_mixed_batch_does_not_create_a_second_approval_path(db):
    with pytest.raises(ValueError, match="Workspace writes"):
        calliope.administer_local_sql_for_session(db, SESSION,
            "CREATE TABLE calliope.x(id int); DELETE FROM sales.orders")
    with db() as conn:
        assert conn.execute("SELECT count(*) n FROM rvbbit.calliope_action_runs").fetchone()["n"] == 0


def test_last_result_is_retained_for_a_long_batch(db):
    batch = "CREATE TABLE calliope.x(id int);" + "SELECT 1;" * 48 + "SELECT 12.34 AS amount;"
    result = calliope.run_workspace_sql_for_session(db, SESSION, batch)
    assert result["run"]["status"] == "complete", result["run"].get("error")
    statements = result["run"]["result"]["statements"]
    assert len(statements) == 50
    assert statements[-1]["rows"] == [{"amount": "12.34"}]


def test_workspace_audit_references_do_not_reexecute_or_explain_a_batch(db, monkeypatch):
    result = calliope.run_workspace_sql_for_session(db, SESSION,
        "CREATE TABLE calliope.output AS SELECT * FROM sales.orders")
    def unexpected(*args, **kwargs):
        raise AssertionError("Workspace audit must not run EXPLAIN")
    monkeypatch.setattr(server, "_referenced_tables", unexpected)
    assert server._objects("run_workspace_sql", {}, result) == ["calliope.output", "sales.orders"]


def test_workspace_discovery_preserves_source_allowlist(monkeypatch):
    monkeypatch.setattr(server, "_ALLOW_SCHEMAS", {"sales"})
    assert server._schema_allowed("calliope")
    assert server._schema_allowed("sales")
    assert not server._schema_allowed("private")
    assert not server._schema_allowed("rvbbit")


@pytest.mark.parametrize("statement", [
    "CREATE TABLE public.outside(id integer)",
    "CREATE TABLE calliope.x(id integer); COMMIT; SELECT 1/0",
    "WITH gone AS (DELETE FROM sales.orders RETURNING *) INSERT INTO calliope.x SELECT * FROM gone",
    "DROP TABLE calliope.x CASCADE",
    "TRUNCATE calliope.x CASCADE",
    "ALTER TABLE calliope.x SET SCHEMA sales",
    "ALTER TABLE calliope.x OWNER TO postgres",
    "CREATE FUNCTION calliope.f() RETURNS int LANGUAGE sql AS $$SELECT 1$$",
    "COPY calliope.x FROM PROGRAM 'echo unsafe'",
    "SET SESSION AUTHORIZATION postgres",
    "SELECT 'value'::sales.custom_type",
    "DO $$BEGIN COMMIT; END$$",
    "CREATE TEMP TABLE calliope.x(id integer)",
])
def test_rejects_commands_outside_the_workspace_batch(statement):
    with pytest.raises(ValueError):
        workspace.plan(statement)


def test_native_parser_preserves_unicode_quoted_identifiers_and_string_semicolons():
    batch = workspace.plan('CREATE TABLE calliope."résumé"(note text); INSERT INTO calliope."résumé" VALUES($$one; two$$); SELECT * FROM calliope."résumé";')
    assert len(batch["statements"]) == 3
    assert "one; two" in batch["statements"][1]
    assert batch["writes"] == [("calliope", "résumé")]


def test_database_login_cannot_reset_into_admin_or_write_sources(db):
    batch = workspace.plan("CREATE TABLE calliope.example(id int)")
    dsn, role = workspace._prepare(db, batch, SEED, lambda s: s == "sales")
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute("SELECT session_user,current_user").fetchone() == (role, role)
        assert conn.execute("SELECT current_setting('rvbbit.force_heap_scan')").fetchone() == ("on",)
        for statement in ["UPDATE sales.orders SET amount=0", "SET SESSION AUTHORIZATION alexr", "ALTER ROLE " + role + " SUPERUSER"]:
            with pytest.raises(psycopg.Error):
                conn.execute(statement)


def test_ddl_housekeeping_does_not_require_internal_write_grants(db):
    with db() as conn:
        conn.execute("""
            CREATE TABLE rvbbit.tables(table_oid oid);
            CREATE TABLE rvbbit.test_drop_audit(object_id oid);
            CREATE FUNCTION rvbbit.on_drop_table() RETURNS event_trigger LANGUAGE plpgsql AS $$
            BEGIN
                INSERT INTO rvbbit.test_drop_audit
                SELECT objid FROM pg_event_trigger_dropped_objects() WHERE object_type='table';
            END $$;
            CREATE EVENT TRIGGER workspace_test_drop ON sql_drop EXECUTE FUNCTION rvbbit.on_drop_table();
        """)
    execute(db, "CREATE TABLE calliope.output(id int PRIMARY KEY); DROP TABLE calliope.output")
    with db() as conn:
        row = conn.execute("SELECT prosecdef,proconfig FROM pg_proc WHERE oid='rvbbit.on_drop_table()'::regprocedure").fetchone()
        assert row["prosecdef"] is True
        assert "search_path=pg_catalog, rvbbit" in row["proconfig"]
        assert conn.execute("SELECT count(*) n FROM rvbbit.test_drop_audit").fetchone()["n"] == 1
    dsn, _ = workspace._prepare(db, workspace.plan("SELECT 1"), SEED, lambda _: True)
    with psycopg.connect(dsn, autocommit=True) as conn:
        for statement in ["INSERT INTO rvbbit.tables VALUES(1)", "DELETE FROM rvbbit.test_drop_audit", "SELECT rvbbit.on_drop_table()"]:
            with pytest.raises(psycopg.Error):
                conn.execute(statement)


def test_no_privilege_or_execution_when_owner_mismatches_or_user_is_not_admin(db, monkeypatch):
    def deny_participation(*_):
        raise calliope.calliope_access.CalliopeAccessError("SESSION_NOT_FOUND", "No access to this thread", 404)
    monkeypatch.setattr(calliope.calliope_access, "require_participant", deny_participation)
    with pytest.raises(PermissionError, match="No access"):
        calliope.run_workspace_sql_for_session(db, SESSION, "SELECT 1", authorized_owner="other@example.com")
    monkeypatch.setattr(calliope.calliope_dreams, "is_company_admin", lambda *_: False)
    with pytest.raises(PermissionError, match="Admins Team"):
        calliope.run_workspace_sql_for_session(db, SESSION, "SELECT 1")
    with db() as conn:
        assert conn.execute("SELECT count(*) n FROM rvbbit.calliope_action_runs").fetchone()["n"] == 0


def test_source_schema_and_dynamic_sql_checks(db):
    for statement in ["SELECT set_config('role','postgres',false)", "SELECT query_to_xml('DELETE FROM sales.orders',true,false,'')", "SELECT pg_sleep(100)", "SELECT * FROM rvbbit.calliope_action_runs"]:
        with pytest.raises(ValueError):
            execute(db, statement)


def test_concurrent_batches_keep_the_same_workspace_identity(db):
    execute(db, "CREATE TABLE calliope.events(id integer)")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda n: execute(db, f"INSERT INTO calliope.events VALUES({n})"), range(4)))
    assert len({r["database_user"] for r in results}) == 1
    with db() as conn:
        assert conn.execute("SELECT count(*) n FROM calliope.events").fetchone()["n"] == 4


def test_workspace_tool_registered_with_explicit_typed_schema():
    tools = {tool.name: tool for tool in server._build_mcp()._tool_manager.list_tools()}
    schema = tools["run_workspace_sql"].parameters
    assert set(schema["properties"]) == {"session_id", "sql"}
    assert set(schema["required"]) == {"session_id", "sql"}


def test_workspace_receipts_show_sql_but_never_offer_approval(zen_page):
    page = zen_page
    page.evaluate('''() => {
      const action={id:'data.workspace_sql',executor:'workspace_sql',receipt_only:true,category:'admin',title:'Build Calliope workspace data'};
      const base={action_snapshot:action,input_redacted:{sql:'CREATE TABLE calliope.output(id int); INSERT INTO calliope.output VALUES(1);',sql_sha256:'a'.repeat(64),statement_type:'batch'}};
      qa.state.surfaces=['complete','failed','planned'].map((status)=>({
        id:status,kind:'action',turn_id:'one',title:action.title,
        payload:{run:{...base,id:status,status,error:status==='failed'?'Statement 2 failed; entire batch rolled back':null}}
      }));
      qa.renderChat();qa.renderStage();
    }''')
    assert page.locator('[data-approve-local-sql]').count() == 0
    assert 'REVIEW FROZEN' not in page.locator('body').inner_text().upper()
    receipt = page.locator('[data-surface-id="complete"] [data-sql-execution]')
    assert 'Complete' in receipt.inner_text()
    assert 'INSERT INTO calliope.output' in receipt.locator('pre').inner_text()


def test_classifier_materializes_without_granting_private_runtime_access(db):
    with db() as conn:
        conn.execute("CREATE TABLE rvbbit.operators(name text PRIMARY KEY); INSERT INTO rvbbit.operators VALUES('clover_classify_scores'); CREATE TABLE rvbbit.settings(secret text); INSERT INTO rvbbit.settings VALUES('private')")
        conn.execute("""CREATE FUNCTION rvbbit.clover_classify_scores(t text, labels text, opts jsonb DEFAULT '{}')
                        RETURNS jsonb LANGUAGE sql AS $$
                        SELECT jsonb_build_object('label', labels, 'score', 0.9)
                        FROM rvbbit.settings WHERE secret='private' $$""")
    result = execute(db, """CREATE TABLE calliope.enrichment AS
        SELECT id, rvbbit.clover_classify_scores('a', 'family') AS classification FROM sales.orders;
        SELECT count(*) AS n FROM calliope.enrichment WHERE classification->>'label'='family'""")
    assert result['statements'][-1]['rows'] == [{'n': 2}]
    assert 'calliope_runtime.clover_classify_scores' in result['statements'][0]['executed_sql']
    dsn, role = workspace._prepare(db, workspace.plan('SELECT 1'), SEED, lambda s: True)
    with psycopg.connect(dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('SELECT * FROM rvbbit.settings')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('CREATE TABLE calliope_runtime.intrusion(id int)')
    with db() as conn:
        conn.execute("CREATE ROLE unrelated_workspace_reader")
        try:
            assert not conn.execute("SELECT has_schema_privilege('unrelated_workspace_reader','calliope_runtime','USAGE') AS allowed").fetchone()['allowed']
        finally:
            conn.execute('DROP ROLE unrelated_workspace_reader')


def test_classifier_rewrite_only_changes_function_calls():
    statement = "SELECT rvbbit.clover_classify_scores('rvbbit.clover_classify_scores', 'a') AS clover_classify_scores"
    rewritten = workspace._execution_sql(statement, ['clover_classify_scores'])
    assert "calliope_runtime.clover_classify_scores('rvbbit.clover_classify_scores', 'a')" in rewritten
    with pytest.raises(ValueError):
        workspace.plan("SELECT calliope_runtime.clover_classify_scores('a','b')")


def test_dynamic_mcp_operators_materialize_and_new_registrations_need_no_code_change(db):
    with db() as conn:
        conn.execute("CREATE TABLE rvbbit.operators(name text PRIMARY KEY); CREATE TABLE rvbbit.private_config(value text); INSERT INTO rvbbit.private_config VALUES('runtime-access')")
        conn.execute("""CREATE FUNCTION rvbbit.aa_ads_report(account text, opts jsonb DEFAULT NULL)
            RETURNS jsonb LANGUAGE sql CALLED ON NULL INPUT AS $$
            SELECT jsonb_build_object('account',account,'default_omitted',opts IS NULL,'runtime',value)
            FROM rvbbit.private_config $$;
            INSERT INTO rvbbit.operators VALUES('aa_ads_report')""")
    result = execute(db, """CREATE TABLE calliope.ads AS SELECT rvbbit.aa_ads_report(account => 'one') AS payload;
        SELECT payload FROM calliope.ads""")
    assert result['statements'][-1]['rows'][0]['payload'] == {'account': 'one', 'default_omitted': True, 'runtime': 'runtime-access'}
    # A second arbitrary server/tool arrives after the first execution.
    with db() as conn:
        conn.execute("""CREATE FUNCTION rvbbit.fresh_connector_measure(n integer DEFAULT 3)
            RETURNS int LANGUAGE sql AS $$SELECT n*2$$;
            INSERT INTO rvbbit.operators VALUES('fresh_connector_measure')""")
    assert execute(db, 'INSERT INTO calliope.ads SELECT to_jsonb(rvbbit.fresh_connector_measure()); SELECT count(*) n FROM calliope.ads')['statements'][-1]['rows'] == [{'n': 2}]
    dsn, _ = workspace._prepare(db, workspace.plan('SELECT 1'), SEED, lambda _: True)
    with psycopg.connect(dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('SELECT * FROM rvbbit.private_config')


def test_registered_per_server_tools_preserve_sets_overloads_and_signature_drift(db):
    with db() as conn:
        conn.execute("""CREATE TABLE rvbbit.mcp_tools(server text,name text);
            CREATE SCHEMA ads_connector;
            CREATE FUNCTION ads_connector.report(amount integer DEFAULT 2) RETURNS SETOF jsonb
            LANGUAGE sql AS $$SELECT jsonb_build_object('n',n) FROM generate_series(1,amount) n$$;
            CREATE FUNCTION ads_connector.report(label text) RETURNS SETOF jsonb
            LANGUAGE sql AS $$SELECT to_jsonb(label)$$;
            INSERT INTO rvbbit.mcp_tools VALUES('ads_connector','report')""")
    first = execute(db, "CREATE TABLE calliope.first_report AS SELECT * FROM ads_connector.report(); SELECT count(*) n FROM calliope.first_report")
    assert first['statements'][-1]['rows'] == [{'n': 2}]
    assert execute(db, "SELECT * FROM ads_connector.report(label=>'hello')")['statements'][0]['rows'][0]
    with db() as conn:
        # Same types, different parameter name and default after re-discovery.
        conn.execute('DROP FUNCTION ads_connector.report(integer)')
        conn.execute("""CREATE FUNCTION ads_connector.report(limit_rows integer DEFAULT 4) RETURNS SETOF jsonb
            LANGUAGE sql AS $$SELECT to_jsonb(n) FROM generate_series(1,limit_rows) n$$""")
    second = execute(db, 'SELECT count(*) n FROM ads_connector.report(limit_rows=>3)')
    assert second['statements'][0]['rows'] == [{'n': 3}]
    assert execute(db, 'SELECT count(*) n FROM ads_connector.report()')['statements'][0]['rows'] == [{'n': 4}]


def test_unregistered_functions_do_not_gain_administrative_access(db):
    with db() as conn:
        conn.execute("CREATE FUNCTION rvbbit.mutate_something() RETURNS int LANGUAGE sql AS $$SELECT 1$$")
    with pytest.raises(ValueError, match='not a registered'):
        execute(db, 'CREATE TABLE calliope.nope AS SELECT rvbbit.mutate_something()')
    with db() as conn:
        assert conn.execute("SELECT to_regclass('calliope.nope') obj").fetchone()['obj'] is None


def test_dynamic_operator_failure_rolls_back_local_materialization(db):
    with db() as conn:
        conn.execute("""CREATE TABLE rvbbit.operators(name text PRIMARY KEY);
            INSERT INTO rvbbit.operators VALUES('any_source_broken');
            CREATE FUNCTION rvbbit.any_source_broken() RETURNS int LANGUAGE sql AS $$SELECT 1/0$$""")
    with pytest.raises(RuntimeError, match='rolled back'):
        execute(db, 'CREATE TABLE calliope.partial(id int); INSERT INTO calliope.partial SELECT rvbbit.any_source_broken()')
    with db() as conn:
        assert conn.execute("SELECT to_regclass('calliope.partial') obj").fetchone()['obj'] is None


def test_workspace_hook_can_read_operator_names_but_not_definitions(db):
    with db() as conn:
        conn.execute("CREATE TABLE rvbbit.operators(name text PRIMARY KEY, steps jsonb, prompt text); INSERT INTO rvbbit.operators VALUES('example', '[]', 'private definition')")
    dsn, role = workspace._prepare(db, workspace.plan('SELECT 1'), SEED, lambda _: True)
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute('SELECT name FROM rvbbit.operators').fetchall() == [('example',)]
        for query in ['SELECT steps FROM rvbbit.operators', 'SELECT prompt FROM rvbbit.operators',
                      "UPDATE rvbbit.operators SET name='changed'", 'SELECT * FROM rvbbit.operators']:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(query)


def test_app_schema_bootstraps_routes_without_approval_and_joins_data(db, monkeypatch):
    result = calliope.administer_local_sql_for_session(db, SESSION, """
        CREATE TABLE calliope.shared_data AS SELECT * FROM sales.orders;
        CREATE TABLE calliope_apps.tasks(id integer PRIMARY KEY, note text);
        INSERT INTO calliope_apps.tasks VALUES(1,'follow up');
        SELECT t.note,d.amount FROM calliope_apps.tasks t JOIN calliope.shared_data d USING(id);
    """)
    assert result['approval_required'] is False
    assert result['run']['status']=='complete', result['run'].get('error')
    assert result['run']['result']['schemas']==['calliope','calliope_apps']
    assert result['run']['verification']['schemas']==['calliope','calliope_apps']
    with db() as conn:
        owners=conn.execute("SELECT nspname,nspowner FROM pg_namespace WHERE nspname IN ('calliope','calliope_apps') ORDER BY nspname").fetchall()
        assert len(owners)==2 and owners[0]['nspowner']==owners[1]['nspowner']
    monkeypatch.setattr(server,'_ALLOW_SCHEMAS',{'sales'})
    assert server._schema_allowed('calliope_apps') and server._schema_allowed('calliope')
    with pytest.raises(ValueError,match='Workspace writes'):
        workspace.plan('DELETE FROM calliope_apps.tasks; DELETE FROM sales.orders')


def test_existing_workspace_gains_app_schema_without_moving_data(db):
    execute(db,'CREATE TABLE calliope.existing(id int); INSERT INTO calliope.existing VALUES(7)')
    with db() as conn:conn.execute('DROP SCHEMA calliope_apps')  # prior-install shape
    result=execute(db,'CREATE TABLE calliope_apps.new_state AS SELECT * FROM calliope.existing')
    assert result['schema']=='calliope_apps'
    with db() as conn:
        assert conn.execute('SELECT * FROM calliope.existing').fetchall()==[{'id':7}]
        assert conn.execute('SELECT * FROM calliope_apps.new_state').fetchall()==[{'id':7}]


def test_the_decision_tree_family_runs_without_being_a_registered_operator(db):
    # leaf/branch/choice_branch construct JSON and call nothing; decision_tree
    # orchestrates and meters through clover_decide, which IS registered. None
    # of the five belong in rvbbit.operators, so workspace SQL trusts them by
    # name instead. An empty operator registry proves the family is what admits
    # them, not a registration.
    with db() as conn:
        conn.execute("CREATE TABLE rvbbit.operators(name text PRIMARY KEY)")
        conn.execute("""CREATE FUNCTION rvbbit.leaf(label text) RETURNS jsonb LANGUAGE sql IMMUTABLE AS
                        $$ SELECT jsonb_build_object('kind','leaf','label',label) $$;
                        CREATE FUNCTION rvbbit.decision_tree(state jsonb, tree jsonb, max_steps int DEFAULT 16)
                        RETURNS jsonb LANGUAGE sql AS
                        $$ SELECT jsonb_build_object('status','complete','label',tree->>'label') $$;
                        CREATE FUNCTION rvbbit.choose(state text, categories text[])
                        RETURNS text LANGUAGE sql AS $$ SELECT categories[1] $$""")
    result = execute(db, """CREATE TABLE calliope.classified AS
        SELECT id, rvbbit.decision_tree(jsonb_build_object('t', id::text), rvbbit.leaf('kept'))->>'label' AS verdict,
               rvbbit.choose(id::text, ARRAY['a','b']) AS pick
          FROM sales.orders;
        SELECT count(*) AS n FROM calliope.classified WHERE verdict='kept' AND pick='a'""")
    assert result['statements'][-1]['rows'] == [{'n': 2}]
    assert 'calliope_runtime.decision_tree' in result['statements'][0]['executed_sql']
    assert 'calliope_runtime.choose' in result['statements'][0]['executed_sql']


def test_trusting_the_family_does_not_open_the_rest_of_rvbbit(db):
    with db() as conn:
        conn.execute("CREATE TABLE rvbbit.operators(name text PRIMARY KEY)")
        conn.execute("""CREATE FUNCTION rvbbit.read_secrets() RETURNS text LANGUAGE sql AS $$ SELECT 'secret' $$""")
    with pytest.raises(ValueError, match='not a registered RVBBIT operator'):
        execute(db, "CREATE TABLE calliope.leak AS SELECT rvbbit.read_secrets() AS s")
