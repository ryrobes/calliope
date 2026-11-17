from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import DateTime, Column, MetaData, Table, select


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "capabilities" / "templates" / "dlt-mirror" / "main.py"
MIGRATION = (
    ROOT
    / "crates"
    / "pg_rvbbit"
    / "sql"
    / "migrations"
    / "0287_dlt_mirror_control_plane.sql"
)
RUNTIME_MIGRATION = (
    ROOT
    / "crates"
    / "pg_rvbbit"
    / "sql"
    / "migrations"
    / "0289_data_mover_runtime_registry.sql"
)
IDENTIFIER_MIGRATION = (
    ROOT
    / "crates"
    / "pg_rvbbit"
    / "sql"
    / "migrations"
    / "0290_dlt_destination_identifier_contract.sql"
)
REGISTRATION_MIGRATION = (
    ROOT
    / "crates"
    / "pg_rvbbit"
    / "sql"
    / "migrations"
    / "0292_dlt_mirror_rvbbit_registration.sql"
)
FOLLOWTHROUGH_MIGRATION = (
    ROOT
    / "crates"
    / "pg_rvbbit"
    / "sql"
    / "migrations"
    / "0293_hosted_onboarding_followthrough.sql"
)
CHANGE_DETECTION_MIGRATION = (
    ROOT
    / "crates"
    / "pg_rvbbit"
    / "sql"
    / "migrations"
    / "0315_mirror_source_change_detection.sql"
)
ATOMIC_PUBLICATION_MIGRATION = (
    ROOT
    / "crates"
    / "pg_rvbbit"
    / "sql"
    / "migrations"
    / "0317_atomic_mirror_publication.sql"
)
FIXTURE_COMPOSE = ROOT / "docker" / "docker-compose.ingestion-test.yml"
FIXTURE_SOURCES = ROOT / "docker" / "sample-sources"


def _load_module(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(MAIN.parent))
    monkeypatch.setenv("RVBBIT_DSN", "postgresql://control:control-secret@postgres/rvbbit")
    monkeypatch.setenv("RVBBIT_MIRROR_STATE_DIR", str(tmp_path / "state"))
    spec = importlib.util.spec_from_file_location("rvbbit_dlt_mirror_test", MAIN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dlt_mirror_redacts_connection_strings(monkeypatch, tmp_path):
    mirror = _load_module(monkeypatch, tmp_path)
    source = "postgresql://reader:very-secret@db.internal:5432/erp?api_key=also-secret"

    redacted = mirror._redact_uri(source)
    message = mirror._safe_error(
        RuntimeError(
            f"could not connect to {source}; password very-secret; token also-secret"
        ),
        source,
    )

    assert "very-secret" not in redacted
    assert "also-secret" not in redacted
    assert "very-secret" not in message
    assert "also-secret" not in message
    assert "credential redacted" in message


@pytest.mark.parametrize(
    ("source", "dialect", "expected_scheme"),
    [
        ("oracle://reader:secret@db/FREEPDB1", "oracle", "oracle+oracledb"),
        ("oracle+cx_oracle://reader:secret@db/FREEPDB1", "oracle", "oracle+oracledb"),
        ("postgresql://reader:secret@db/reporting", "postgresql", "postgresql+psycopg2"),
        ("mssql://reader:secret@db/reporting", "mssql", "mssql+pymssql"),
    ],
)
def test_dlt_mirror_routes_source_urls_through_shipped_drivers(
    monkeypatch, tmp_path, source, dialect, expected_scheme
):
    mirror = _load_module(monkeypatch, tmp_path)

    normalized = mirror._dsn_with_supported_driver(source, dialect)

    assert normalized.startswith(f"{expected_scheme}://")
    assert "reader:secret@db" in normalized
    if dialect == "oracle":
        assert normalized.endswith("/?service_name=FREEPDB1")
    else:
        assert normalized.split("://", 1)[1] == source.split("://", 1)[1]


def test_dlt_mirror_http_admin_endpoints_fail_closed_without_a_token(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("RVBBIT_MIRROR_TOKEN", raising=False)
    mirror = _load_module(monkeypatch, tmp_path)

    with pytest.raises(mirror.HTTPException) as missing:
        mirror._check_token(None)
    assert missing.value.status_code == 503

    monkeypatch.setenv("RVBBIT_MIRROR_TOKEN", "controller-token")
    configured = _load_module(monkeypatch, tmp_path)
    configured._check_token("Bearer controller-token")
    with pytest.raises(configured.HTTPException) as rejected:
        configured._check_token("Bearer wrong-token")
    assert rejected.value.status_code == 401


def test_dlt_mirror_health_fails_when_controller_auth_is_unconfigured(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("RVBBIT_MIRROR_TOKEN", raising=False)
    mirror = _load_module(monkeypatch, tmp_path)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def execute(_query):
            return SimpleNamespace(
                fetchone=lambda: {
                    "database": "rvbbit",
                    "ready": True,
                    "queued": 0,
                    "running": 0,
                }
            )

    monkeypatch.setattr(mirror, "_connect", Connection)
    response = mirror.Response()

    result = mirror.health(response)

    assert result["ok"] is False
    assert result["database"] is True
    assert result["controller_auth_configured"] is False
    assert response.status_code == 503


def test_dlt_mirror_row_count_uses_extract_metrics_not_csv_cell_counts(monkeypatch, tmp_path):
    """Live MariaDB source, csv loader: dlt normalize row_counts reported 302,083 for a
    10,742-row table — exactly its non-null cell count. Extract metrics are the rows."""
    mirror = _load_module(monkeypatch, tmp_path)
    trace = SimpleNamespace(
        last_extract_info=SimpleNamespace(metrics={
            "1786953416.93": [
                {"table_metrics": {"events": SimpleNamespace(items_count=10742)}},
                {"table_metrics": {"_dlt_pipeline_state": SimpleNamespace(items_count=1)}},
            ]
        }),
        last_normalize_info=SimpleNamespace(row_counts={"_dlt_pipeline_state": 1, "events": 302083}),
    )
    assert mirror._row_count(SimpleNamespace(last_trace=trace), "events") == 10742
    # Missing extract metrics (older traces) still fall back to normalize counts.
    legacy = SimpleNamespace(last_extract_info=None,
                             last_normalize_info=SimpleNamespace(row_counts={"events": 7}))
    assert mirror._row_count(SimpleNamespace(last_trace=legacy), "events") == 7
    assert mirror._row_count(SimpleNamespace(last_trace=None), "events") == 0


def _identity_connection(rows_by_needle: dict[str, Any], executed: list[str]):
    """Fake control connection: answers queries by substring, records DDL."""

    class Result:
        def __init__(self, value):
            self._value = value

        def fetchone(self):
            return self._value if isinstance(self._value, dict) or self._value is None else self._value[0]

        def fetchall(self):
            return self._value if isinstance(self._value, list) else [self._value]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def execute(query, params=None):
            executed.append(" ".join(query.split()))
            for needle, value in rows_by_needle.items():
                if needle in query:
                    return Result(value)
            return Result(None)

    return Connection


def test_dlt_mirror_key_columns_map_declared_keys_onto_normalized_destination_names(monkeypatch, tmp_path):
    mirror = _load_module(monkeypatch, tmp_path)
    cols = ["cust_id", "order_id", "line_item_id", "_dlt_id", "_dlt_load_id"]
    assert mirror._destination_key_columns(["CUST_ID"], cols) == ["cust_id"]            # Oracle upper-case source key
    assert mirror._destination_key_columns(["Order Id", "line-item-id"], cols) == ["order_id", "line_item_id"]
    assert mirror._destination_key_columns(["missing"], cols) is None
    assert mirror._destination_key_columns([], cols) is None and mirror._destination_key_columns(None, cols) is None


def test_dlt_mirror_gives_keyed_destinations_a_real_primary_key(monkeypatch, tmp_path):
    """dlt only emits UNIQUE for _dlt_id; without a PRIMARY KEY RVBBIT's overlay
    never engages and every merge/upsert forces a full parquet rebuild."""
    mirror = _load_module(monkeypatch, tmp_path)
    job = {"destination_schema": "live_quakes", "job_name": "j"}
    table = {"destination_table": "events", "source_table": "events", "primary_key": ["event_id"]}
    executed: list[str] = []
    monkeypatch.setattr(mirror, "_connect", _identity_connection({
        "to_regclass": {"oid": 4242},
        "indisprimary": {"present": False},
        "FROM pg_attribute": [{"attname": "event_id"}, {"attname": "mag"}, {"attname": "_dlt_id"}],
        "AS has_null": {"has_null": False, "has_dupe": False},
        "c.relname = %s": None,
    }, executed))
    assert mirror._ensure_destination_identity(job, table) == "created"
    ddl = [s for s in executed if s.startswith("CREATE") or s.startswith("ALTER")]
    assert ddl == [
        'CREATE UNIQUE INDEX CONCURRENTLY "events_rvbbit_pkey" ON "live_quakes"."events" ("event_id")',
        'ALTER TABLE "live_quakes"."events" ADD CONSTRAINT "events_rvbbit_pkey" PRIMARY KEY USING INDEX "events_rvbbit_pkey"',
    ]

    # Already keyed → nothing to do; a declared key with duplicates → skipped, no DDL.
    executed.clear()
    monkeypatch.setattr(mirror, "_connect", _identity_connection({
        "to_regclass": {"oid": 4242}, "indisprimary": {"present": True}}, executed))
    assert mirror._ensure_destination_identity(job, table) == "present"
    assert not any(s.startswith(("CREATE", "ALTER")) for s in executed)
    executed.clear()
    monkeypatch.setattr(mirror, "_connect", _identity_connection({
        "to_regclass": {"oid": 4242}, "indisprimary": {"present": False},
        "FROM pg_attribute": [{"attname": "widget_id"}, {"attname": "sku"}],
        "AS has_null": {"has_null": False, "has_dupe": True}}, executed))
    assert mirror._ensure_destination_identity(job, {**table, "destination_table": "widgets",
                                                     "primary_key": ["widget_id"]}) == "skipped"
    assert not any(s.startswith(("CREATE", "ALTER")) for s in executed)
    # Unkeyed snapshot tables are left alone.
    assert mirror._ensure_destination_identity(job, {**table, "primary_key": None}) == "unkeyed"


def test_dlt_mirror_table_errors_lead_with_the_database_reason(monkeypatch, tmp_path):
    mirror = _load_module(monkeypatch, tmp_path)
    wrapped = (
        "Pipeline execution failed at `step=load` when processing package with `load_id=1786969993.8` with exception:\n\n"
        "<class 'dlt.load.exceptions.LoadClientJobFailed'>\n"
        "Job with `job_id=widgets.631de8ece9.sql` and `load_id=1786969993.8` failed terminally with message: "
        "mirror staging relation _dlt_live_chaos_stage.widgets has 12 duplicated value(s) of the declared key (widget_id), e.g. 12357, 13321\n"
        "HINT:  The declared mirror key is not unique at the source. Fix the source rows or declare a different key for this mirror table; the target was left untouched.\n"
        "CONTEXT:  PL/pgSQL function publish_mirror_snapshot(regclass,regclass,text[],text[]) line 187 at RAISE\n"
        ". The package is not fully loaded."
    )
    out = mirror._root_cause(wrapped)
    assert out.startswith("mirror staging relation _dlt_live_chaos_stage.widgets has 12 duplicated value(s)")
    assert "HINT:" in out and "[dlt: Pipeline execution failed" in out
    assert "The package is not fully loaded" not in out.split("[dlt:")[0]
    retried = "Job with `job_id=x.sql` had 5 retries which is a multiple of `max_retry_count=5`. Exiting retry loop. You can still rerun the load package to retry this job. Last failure message was: boom happened"
    assert mirror._root_cause(retried).startswith("boom happened")
    assert mirror._root_cause("plain error") == "plain error"


def test_dlt_mirror_drops_stale_load_packages_before_each_table(monkeypatch, tmp_path):
    """A rejected snapshot package must not be retried (and re-fail) on every later run."""
    mirror = _load_module(monkeypatch, tmp_path)
    calls = []

    class Pipeline:
        def list_normalized_load_packages(self):
            return ["1786970362.8097703"]

        def list_extracted_load_packages(self):
            return []

        def drop_pending_packages(self, with_partial_loads=False):
            calls.append(with_partial_loads)

    dropped = mirror._drop_stale_packages(Pipeline(), "run-1", {"source_table": "widgets"})
    assert dropped == ["1786970362.8097703"] and calls == [True]

    class Clean:
        def list_normalized_load_packages(self):
            return []

        def list_extracted_load_packages(self):
            return []

        def drop_pending_packages(self, with_partial_loads=False):
            raise AssertionError("nothing to drop")

    assert mirror._drop_stale_packages(Clean(), "run-2", {"source_table": "widgets"}) == []


def test_dlt_mirror_bigquery_dsn_keeps_its_scheme_and_hides_the_key(monkeypatch, tmp_path):
    mirror = _load_module(monkeypatch, tmp_path)
    dsn = "bigquery://my-project?credentials_base64=eyJ0eXBlIjoic2VydmljZV9hY2NvdW50In0="
    assert mirror._dsn_dialect(dsn) == "bigquery"
    assert mirror._dsn_with_supported_driver(dsn, "bigquery") == dsn        # no +driver suffix for BigQuery
    redacted = mirror._safe_error(RuntimeError(f"could not connect: {dsn}"), dsn)
    assert "eyJ0eXBl" not in redacted and "credential redacted" in redacted
    migration = (ROOT / "crates" / "pg_rvbbit" / "sql" / "migrations" / "0333_mirror_bigquery_dialect.sql").read_text()
    assert "'bigquery'" in migration


def test_dlt_mirror_builds_snapshot_and_incremental_resources(monkeypatch, tmp_path):
    mirror = _load_module(monkeypatch, tmp_path)
    calls = []

    class Resource:
        def __init__(self):
            self.hints = {}
            self.max_table_nesting = None

        def apply_hints(self, **kwargs):
            self.hints.update(kwargs)

    def fake_sql_table(**kwargs):
        resource = Resource()
        calls.append((kwargs, resource))
        return resource

    def fake_incremental(**kwargs):
        return {"incremental": kwargs}

    mirror.dlt = SimpleNamespace(sources=SimpleNamespace(incremental=fake_incremental))
    mirror.sql_table = fake_sql_table
    job = {
        "source_schema": "dbo",
        "chunk_size": 25000,
        "reflection_level": "full",
    }

    snapshot = mirror._resource_for(
        job,
        {
            "source_table": "Customers",
            "destination_table": "customers",
            "load_mode": "snapshot",
            "primary_key": ["CustomerID"],
            "cursor_column": None,
            "initial_cursor": None,
            "included_columns": None,
        },
        "mssql+pymssql://reader:secret@db/erp",
    )
    incremental = mirror._resource_for(
        job,
        {
            "source_table": "Orders",
            "destination_table": "orders",
            "load_mode": "incremental_upsert",
            "primary_key": ["OrderID"],
            "cursor_column": "UpdatedAt",
            "initial_cursor": "2026-01-01T00:00:00Z",
            "included_columns": ["OrderID", "UpdatedAt", "Payload"],
        },
        "mssql+pymssql://reader:secret@db/erp",
    )

    snapshot_call, incremental_call = calls
    assert snapshot_call[0]["write_disposition"] == "replace"
    assert snapshot.hints == {"table_name": "customers"}
    assert snapshot.max_table_nesting == 0
    assert incremental_call[0]["write_disposition"] == {
        "disposition": "merge",
        "strategy": "upsert",
    }
    assert incremental_call[0]["incremental"] == {
        "incremental": {
            "cursor_path": "UpdatedAt",
            "initial_value": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
    }
    assert incremental_call[0]["query_adapter_callback"] is mirror._mssql_query_adapter
    assert incremental.hints == {"table_name": "orders"}
    assert incremental.max_table_nesting == 0


def test_append_only_signature_stays_small_and_changes_with_key_extent(
    monkeypatch, tmp_path
):
    mirror = _load_module(monkeypatch, tmp_path)
    engine = mirror.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE orders (order_id integer primary key, payload text)"
        )
        conn.exec_driver_sql(
            "INSERT INTO orders(order_id,payload) VALUES (1,'one'),(2,'two')"
        )
    job = {"source_schema": "main"}
    table = {"source_table": "orders", "primary_key": ["order_id"]}

    first = mirror._append_only_source_signature(engine, job, table)
    second = mirror._append_only_source_signature(engine, job, table)
    assert first == second
    assert first["row_count"] == 2
    assert set(first) == {"kind", "version", "digest", "row_count"}

    # Existing-row updates deliberately do not alter this opt-in signature:
    # callers assert immutability when they select append_only_key.
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE orders SET payload='changed' WHERE order_id=1")
    assert mirror._append_only_source_signature(engine, job, table) == first

    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO orders(order_id,payload) VALUES (3,'three')")
    changed = mirror._append_only_source_signature(engine, job, table)
    assert changed["row_count"] == 3
    assert changed["digest"] != first["digest"]


def test_unchanged_append_only_snapshot_never_constructs_a_dlt_pipeline(
    monkeypatch, tmp_path
):
    mirror = _load_module(monkeypatch, tmp_path)
    table = {
        "source_table": "orders",
        "destination_table": "orders",
        "load_mode": "snapshot",
        "primary_key": ["order_id"],
        "change_detection": "append_only_key",
    }
    job = {
        "job_name": "erp",
        "connection_name": "erp",
        "source_schema": "sales",
        "destination_schema": "sales",
    }
    records = []
    finished = []
    disposed = []

    class Engine:
        def dispose(self):
            disposed.append(True)

    monkeypatch.setattr(mirror, "_load_job", lambda _run_id: (job, [table]))
    monkeypatch.setattr(
        mirror, "_resolve_source_dsn", lambda _name: ({}, "postgresql://source")
    )
    monkeypatch.setattr(mirror, "create_engine", lambda *_args, **_kwargs: Engine())
    monkeypatch.setattr(
        mirror,
        "_source_signature",
        lambda *_args: {
            "kind": "append_only_key",
            "version": 1,
            "digest": "a" * 64,
            "row_count": 10_000_000,
        },
    )
    monkeypatch.setattr(mirror, "_skip_unchanged_snapshot", lambda *_args: True)
    monkeypatch.setattr(
        mirror, "_record_table", lambda _run, _table, status, *args, **kwargs: records.append(status)
    )
    monkeypatch.setattr(
        mirror, "_finish_run", lambda _run, status, *_args: finished.append(status)
    )
    monkeypatch.setattr(
        mirror,
        "_pipeline_for",
        lambda _job: pytest.fail("unchanged source constructed a dlt pipeline"),
    )

    mirror._process_run({"run_id": "run-1"})

    assert records == ["running", "skipped"]
    assert finished == ["succeeded"]
    assert disposed == [True]


def test_exact_staging_noop_does_not_mark_the_mirror_changed(monkeypatch, tmp_path):
    mirror = _load_module(monkeypatch, tmp_path)
    table = {
        "source_table": "orders",
        "destination_table": "orders",
        "load_mode": "snapshot",
        "primary_key": ["order_id"],
        "change_detection": "always",
    }
    job = {
        "job_name": "erp",
        "connection_name": "erp",
        "source_schema": "sales",
        "destination_schema": "sales",
        "loader_file_format": "csv",
    }
    records = []
    finished = []

    class LoadInfo:
        loads_ids = ["load-1"]

        @staticmethod
        def raise_on_failed_jobs():
            pass

    class Pipeline:
        @staticmethod
        def run(*_args, **_kwargs):
            return LoadInfo()

    monkeypatch.setattr(mirror, "_load_job", lambda _run_id: (job, [table]))
    monkeypatch.setattr(
        mirror, "_resolve_source_dsn", lambda _name: ({}, "postgresql://source")
    )
    monkeypatch.setattr(mirror, "_pipeline_for", lambda _job: Pipeline())
    monkeypatch.setattr(mirror, "_resource_for", lambda *_args: object())
    monkeypatch.setattr(mirror, "_publication_checkpoint", lambda *_args: 41)
    monkeypatch.setattr(
        mirror,
        "_publication_after",
        lambda *_args: {"action": "noop", "source_rows": 25},
    )
    monkeypatch.setattr(mirror, "_ensure_destination_registered", lambda *_args: None)
    monkeypatch.setattr(
        mirror,
        "_record_table",
        lambda _run, _table, status, *args, **kwargs: records.append(status),
    )
    monkeypatch.setattr(
        mirror, "_finish_run", lambda _run, status, *_args: finished.append(status)
    )

    mirror._process_run({"run_id": "run-atomic-noop"})

    assert records == ["running", "skipped"]
    assert finished == ["succeeded"]
    assert mirror._catalog_queue.empty()


def test_dlt_mirror_coerces_json_safe_cursor_values(monkeypatch, tmp_path):
    mirror = _load_module(monkeypatch, tmp_path)

    assert mirror._coerce_initial_cursor(42) == 42
    assert mirror._coerce_initial_cursor("account-42") == "account-42"
    assert mirror._coerce_initial_cursor("2026-08-11") == mirror.date(2026, 8, 11)
    assert mirror._coerce_initial_cursor("2026-08-11T12:30:00Z") == mirror.datetime(
        2026, 8, 11, 12, 30, tzinfo=mirror.timezone.utc
    )


def test_dlt_mirror_normalizes_mssql_datetimeoffset_binds_to_naive_utc(
    monkeypatch, tmp_path
):
    mirror = _load_module(monkeypatch, tmp_path)
    source = Table(
        "orders",
        MetaData(),
        Column("updated_at", DateTime(timezone=True)),
    )
    cursor = datetime.fromisoformat("2026-08-11T09:10:00-05:00")
    query = select(source).where(source.c.updated_at >= cursor)

    adapted = mirror._mssql_query_adapter(query)

    assert list(adapted.compile().params.values()) == [datetime(2026, 8, 11, 14, 10)]


def test_dlt_mirror_relaxes_oracle_reflection_nullability(monkeypatch, tmp_path):
    mirror = _load_module(monkeypatch, tmp_path)
    source = Table(
        "store_orders",
        MetaData(),
        Column("store_name", nullable=False),
        Column("order_status", nullable=False),
    )

    assert mirror._oracle_table_adapter(source) is source
    assert all(column.nullable for column in source.columns)


def test_dlt_mirror_applies_oracle_table_adapter(monkeypatch, tmp_path):
    mirror = _load_module(monkeypatch, tmp_path)
    captured = {}

    class Resource:
        max_table_nesting = None

        @staticmethod
        def apply_hints(**_kwargs):
            pass

    def fake_sql_table(**kwargs):
        captured.update(kwargs)
        return Resource()

    mirror.dlt = SimpleNamespace()
    mirror.sql_table = fake_sql_table
    mirror._resource_for(
        {
            "source_schema": "CO",
            "chunk_size": 50000,
            "reflection_level": "full",
        },
        {
            "source_table": "store_orders",
            "destination_table": "store_orders",
            "load_mode": "snapshot",
            "primary_key": None,
            "cursor_column": None,
            "initial_cursor": None,
            "included_columns": None,
        },
        "oracle+oracledb://reader:secret@db/?service_name=FREEPDB1",
    )

    assert captured["table_adapter_callback"] is mirror._oracle_table_adapter


def test_dlt_mirror_reports_the_exact_normalized_destination_name(
    monkeypatch, tmp_path
):
    mirror = _load_module(monkeypatch, tmp_path)

    class Naming:
        @staticmethod
        def normalize_table_identifier(value):
            return {"Orders": "orders", "OrderLines": "order_lines"}[value]

        @staticmethod
        def shorten_identifier(normalized, _original, maximum):
            return normalized[:maximum]

    monkeypatch.setattr(mirror, "_destination_naming", Naming())
    assert mirror._destination_table_name("Orders") == "orders"
    assert mirror._destination_table_name("OrderLines") == "order_lines"


def test_dlt_mirror_discovers_selectable_non_system_schemas(monkeypatch, tmp_path):
    mirror = _load_module(monkeypatch, tmp_path)

    class Inspector:
        default_schema_name = "public"

        @staticmethod
        def get_schema_names():
            return ["sales", "pg_catalog", "public", "information_schema", "support"]

    class Engine:
        def dispose(self):
            pass

    monkeypatch.setattr(
        mirror,
        "_resolve_source_dsn",
        lambda _name: ({"dialect": "postgresql", "metadata": {}}, "postgresql://safe"),
    )
    monkeypatch.setattr(mirror, "create_engine", lambda *_args, **_kwargs: Engine())
    monkeypatch.setattr(mirror, "inspect", lambda _engine: Inspector())

    result = mirror._discover_schemas("erp")

    assert result["introspection_supported"] is True
    assert result["schemas"] == ["public", "sales", "support"]
    assert result["default_schema"] == "public"


def test_dlt_mirror_hides_sql_server_database_roles_from_schemas(
    monkeypatch, tmp_path
):
    mirror = _load_module(monkeypatch, tmp_path)

    class Inspector:
        default_schema_name = "dbo"

        @staticmethod
        def get_schema_names():
            return [
                "db_accessadmin",
                "db_datareader",
                "db_datawriter",
                "db_owner",
                "dbo",
                "field_ops",
                "guest",
                "sys",
            ]

    class Engine:
        def dispose(self):
            pass

    monkeypatch.setattr(
        mirror,
        "_resolve_source_dsn",
        lambda _name: ({"dialect": "mssql", "metadata": {}}, "mssql://safe"),
    )
    monkeypatch.setattr(mirror, "create_engine", lambda *_args, **_kwargs: Engine())
    monkeypatch.setattr(mirror, "inspect", lambda _engine: Inspector())

    result = mirror._discover_schemas("field_service")

    assert result["introspection_supported"] is True
    assert result["schemas"] == ["dbo", "field_ops"]
    assert result["default_schema"] == "dbo"


def test_dlt_mirror_uses_oracle_privileges_to_hide_maintained_schemas(
    monkeypatch, tmp_path
):
    mirror = _load_module(monkeypatch, tmp_path)
    executed = []

    class Scalars:
        @staticmethod
        def __iter__():
            return iter(["CO", "SH"])

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def execute(query):
            executed.append(str(query))
            return SimpleNamespace(scalars=lambda: Scalars())

    class Engine:
        @staticmethod
        def connect():
            return Connection()

        @staticmethod
        def dispose():
            pass

    class Inspector:
        default_schema_name = "MIRROR_READER"

        @staticmethod
        def get_schema_names():
            raise AssertionError("Oracle discovery must not expose ALL_USERS")

    monkeypatch.setattr(
        mirror,
        "_resolve_source_dsn",
        lambda _name: ({"dialect": "oracle", "metadata": {}}, "oracle://safe"),
    )
    monkeypatch.setattr(mirror, "create_engine", lambda *_args, **_kwargs: Engine())
    monkeypatch.setattr(mirror, "inspect", lambda _engine: Inspector())

    result = mirror._discover_schemas("erp")

    assert result["introspection_supported"] is True
    assert result["schemas"] == ["CO", "SH"]
    assert result["default_schema"] == "CO"
    assert executed and "oracle_maintained" in executed[0].lower()
    assert "all_objects" in executed[0].lower()


def test_dlt_mirror_pipeline_uses_registered_heap_with_transient_merge_staging(
    monkeypatch, tmp_path
):
    mirror = _load_module(monkeypatch, tmp_path)
    captured = {}

    def fake_postgres(**kwargs):
        captured["destination"] = kwargs
        return "destination"

    def fake_pipeline(**kwargs):
        captured["pipeline"] = kwargs
        return "pipeline"

    mirror.dlt = SimpleNamespace(
        destinations=SimpleNamespace(postgres=fake_postgres), pipeline=fake_pipeline
    )
    monkeypatch.setattr(mirror, "RvbbitPostgresDestination", fake_postgres)
    result = mirror._pipeline_for(
        {"job_name": "erp_core", "destination_schema": "erp"}
    )

    assert result == "pipeline"
    assert captured["destination"]["replace_strategy"] == "insert-from-staging"
    assert captured["destination"]["credentials"] == mirror.CONTROL_DSN
    assert captured["destination"]["destination_name"] == "postgres"
    assert captured["destination"]["staging_dataset_name_layout"] == "_dlt_%s_stage"
    assert captured["pipeline"]["dataset_name"] == "erp"
    assert mirror.os.environ["LOAD__TRUNCATE_STAGING_DATASET"] == "true"


def test_dlt_mirror_generates_atomic_snapshot_publication_sql(monkeypatch, tmp_path):
    mirror = _load_module(monkeypatch, tmp_path)

    sql = mirror._atomic_publication_sql(
        '"sales"."orders"',
        '"_dlt_sales_stage"."orders"',
        ["order_id", "status", "_dlt_load_id"],
        ["order_id"],
    )

    assert sql.startswith("SELECT rvbbit.publish_mirror_snapshot(")
    assert "'\"sales\".\"orders\"'::regclass" in sql
    assert "'\"_dlt_sales_stage\".\"orders\"'::regclass" in sql
    assert "ARRAY['order_id', 'status', '_dlt_load_id']::text[]" in sql
    assert "ARRAY['order_id']::text[]" in sql


def test_dlt_followup_publishes_schema_primary_keys(monkeypatch, tmp_path):
    mirror = _load_module(monkeypatch, tmp_path)

    class SqlClient:
        staging = False

        @contextmanager
        def with_staging_dataset(self):
            self.staging = True
            try:
                yield
            finally:
                self.staging = False

        def make_qualified_table_name(self, table_name):
            schema = "_dlt_sales_stage" if self.staging else "sales"
            return f'"{schema}"."{table_name}"'

    table = {
        "name": "orders",
        "columns": {
            "order_id": {"name": "order_id", "data_type": "bigint", "primary_key": True},
            "status": {"name": "status", "data_type": "text"},
            "_dlt_id": {"name": "_dlt_id", "data_type": "text"},
        },
    }
    sql = mirror.RvbbitAtomicSnapshotFollowupJob.generate_sql([table], SqlClient())

    assert len(sql) == 1
    assert "'\"sales\".\"orders\"'::regclass" in sql[0]
    assert "'\"_dlt_sales_stage\".\"orders\"'::regclass" in sql[0]
    assert "ARRAY['order_id']::text[]" in sql[0]


def test_dlt_mirror_creates_only_user_destination_tables_using_rvbbit(
    monkeypatch, tmp_path
):
    mirror = _load_module(monkeypatch, tmp_path)
    create = ['CREATE TABLE "erp"."orders" (\n"id" bigint\n)']

    assert mirror._rvbbit_table_update_sql(
        create,
        generate_alter=False,
        in_staging_dataset=False,
        is_dlt_table=False,
    ) == [create[0] + " USING rvbbit"]
    assert mirror._rvbbit_table_update_sql(
        create,
        generate_alter=False,
        in_staging_dataset=True,
        is_dlt_table=False,
    ) == create
    assert mirror._rvbbit_table_update_sql(
        create,
        generate_alter=False,
        in_staging_dataset=False,
        is_dlt_table=True,
    ) == create
    assert mirror._rvbbit_table_update_sql(
        ['ALTER TABLE "erp"."orders" ADD COLUMN "note" varchar'],
        generate_alter=True,
        in_staging_dataset=False,
        is_dlt_table=False,
    ) == ['ALTER TABLE "erp"."orders" ADD COLUMN "note" varchar']


def test_dlt_mirror_control_plane_is_reference_only_and_registered():
    sql = MIGRATION.read_text(encoding="utf-8")
    identifier_sql = IDENTIFIER_MIGRATION.read_text(encoding="utf-8")
    registration_sql = REGISTRATION_MIGRATION.read_text(encoding="utf-8")
    followthrough_sql = FOLLOWTHROUGH_MIGRATION.read_text(encoding="utf-8")
    change_detection_sql = CHANGE_DETECTION_MIGRATION.read_text(encoding="utf-8")
    atomic_publication_sql = ATOMIC_PUBLICATION_MIGRATION.read_text(encoding="utf-8")
    worker = MAIN.read_text(encoding="utf-8")
    registry = (
        ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs"
    ).read_text(encoding="utf-8")

    assert "0287_dlt_mirror_control_plane" in registry
    assert 'include_str!("../sql/migrations/0287_dlt_mirror_control_plane.sql")' in registry
    assert "'mirror/' || connection_name || '/SOURCE_DSN'" in sql
    assert "resolve_mirror_connection_credential" in sql
    assert "load_mode text NOT NULL DEFAULT 'snapshot'" in sql
    assert "destination_schema ~ '^[a-z][a-z0-9_]{0,47}$'" in sql
    assert "destination_schema !~ '^pg_'" in sql
    assert "destination_table ~ '^_?[a-z0-9]+(_[a-z0-9]+)*$'" in sql
    assert "0290_dlt_destination_identifier_contract" in registry
    assert "NOT VALID" in identifier_sql
    assert "VALIDATE CONSTRAINT mirror_tables_destination_check" in identifier_sql
    assert "CREATE OR REPLACE VIEW rvbbit.mirror_lineage" in sql
    assert "CREATE OR REPLACE VIEW rvbbit.mirror_run_status" in sql
    assert "nullif(c.metadata ->> 'host', '') AS source_host" in sql
    assert "0292_dlt_mirror_rvbbit_registration" in registry
    assert "0293_hosted_onboarding_followthrough" in registry
    assert "0315_mirror_source_change_detection" in registry
    assert "0317_atomic_mirror_publication" in registry
    assert "append_only_key" in change_detection_sql
    assert "skip_unchanged_mirror_snapshot" in change_detection_sql
    assert "destination_relfilenode" in change_detection_sql
    assert "publish_mirror_snapshot" in atomic_publication_sql
    assert "EXCEPT ALL" in atomic_publication_sql
    assert "loader_file_format SET DEFAULT 'csv'" in atomic_publication_sql
    assert "insert-from-staging" in worker
    assert "UNIQUE (destination_schema)" not in sql
    assert "mirror_jobs_destination_schema_key" in followthrough_sql
    assert "mirror_tables_destination_relation_key" in followthrough_sql
    assert "destination_registered" in registration_sql
    assert "acceleration_enabled" in registration_sql
    assert "rvbbit.enable_table" in registration_sql
    assert "RVBBIT_MIRROR_DESTINATION_DSN" not in worker
    assert 'updated[0] = f"{updated[0]} USING rvbbit"' in worker
    assert "rvbbit.enable_table" in worker
    assert 'app.get("/connections/{connection_name}/schemas")' in worker
    assert "inspector.get_schema_names()" in worker
    assert "CALL rvbbit.catalog_crawl_run(schemas => %s)" in worker
    assert "_catalog_queue.put(str(job[\"destination_schema\"]))" in worker
    assert "to_regclass(quote_ident(%s) || '.' || quote_ident(%s))" in worker
    assert "server.password" not in sql


def test_dlt_mirror_dependencies_are_pinned():
    requirements = (
        ROOT / "capabilities" / "templates" / "dlt-mirror" / "requirements.txt"
    ).read_text(encoding="utf-8")
    assert "dlt[postgres,sql_database]==1.29.1" in requirements
    assert "sqlalchemy-bigquery==" in requirements and "google-cloud-bigquery==" in requirements
    assert all("==" in line for line in requirements.splitlines() if line.strip())


def test_dlt_mirror_pins_sqlalchemy_below_the_bigquery_breaking_release():
    # sqlalchemy-bigquery 1.13 mutates SQLAlchemy's operator table at import;
    # SQLAlchemy 2.1 made it immutable, so an unpinned build cannot import the
    # BigQuery dialect at all ("immutabledict object is immutable").
    requirements = (
        ROOT / "capabilities" / "templates" / "dlt-mirror" / "requirements.txt"
    ).read_text(encoding="utf-8")
    pins = dict(line.split("==", 1) for line in requirements.splitlines() if "==" in line)
    assert pins["sqlalchemy-bigquery"] == "1.13.0"
    major, minor = (int(part) for part in pins["SQLAlchemy"].split(".")[:2])
    assert (major, minor) == (2, 0)


def test_ingestion_fixtures_are_network_only_and_use_read_only_principals():
    compose = FIXTURE_COMPOSE.read_text(encoding="utf-8")
    hermes = (FIXTURE_SOURCES / "hermes-stub.py").read_text(encoding="utf-8")
    postgres = (FIXTURE_SOURCES / "postgres-init.sql").read_text(encoding="utf-8")
    mysql = (FIXTURE_SOURCES / "mysql-init.sql").read_text(encoding="utf-8")
    mssql = (FIXTURE_SOURCES / "mssql-init.sql").read_text(encoding="utf-8")
    oracle_dockerfile = (FIXTURE_SOURCES / "oracle" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    oracle_init = (FIXTURE_SOURCES / "oracle" / "rvbbit-init.sh").read_text(
        encoding="utf-8"
    )

    assert "SAMPLE_HERMES_IMAGE:-python:3.12.11-slim-bookworm@sha256:" in compose
    assert "SAMPLE_POSTGRES_IMAGE:-postgres@sha256:" in compose
    assert "SAMPLE_MYSQL_IMAGE:-mysql@sha256:" in compose
    assert "SAMPLE_MSSQL_IMAGE:-mcr.microsoft.com/mssql/server@sha256:" in compose
    assert "SAMPLE_ORACLE_BASE_IMAGE:-gvenzl/oracle-free:23.26.2-slim-faststart@sha256:" in compose
    assert "ports:" not in compose
    assert "external: true" in compose
    assert "rvbbit_uber" in compose
    assert "hermes-session-api" in compose
    assert '"status": "degraded"' in hermes
    assert '"model": {"status": "error"}' in hermes
    assert "/api/sessions" in hermes
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA sales, support TO mirror_reader" in postgres
    assert "GRANT SELECT ON crm.* TO 'mirror_reader'@'%'" in mysql
    assert "GRANT SELECT ON SCHEMA::field_ops TO mirror_reader" in mssql
    assert "6660bad68c07bd143430ace58565b3f727e17263" in oracle_dockerfile
    assert "e5365e55840dd1f23a712704951e6d77f928bb0100807781576ff92fa1cff6ad" in oracle_dockerfile
    assert "sqlldr" in oracle_init
    assert "rvbbit-oracle-fixture-ready" in oracle_init
    assert "test -f /tmp/rvbbit-oracle-fixture-ready" in compose
    assert "GRANT SELECT ON" in oracle_init
    assert "ALTER USER co ACCOUNT LOCK" in oracle_init
    assert "ALTER USER sh ACCOUNT LOCK" in oracle_init


def test_hosted_smoke_includes_both_official_oracle_sample_schemas():
    smoke = (ROOT / "scripts" / "hosted-appliance-smoke.py").read_text(
        encoding="utf-8"
    )

    assert '"oracle+oracledb://mirror_reader:' in smoke
    assert '(("CO", "oracle_co"), ("SH", "oracle_sh"))' in smoke
    assert '"--fixture"' in smoke
    assert '"--skip-profile"' in smoke


def test_warren_registers_and_health_probes_data_mover_runtimes():
    migration = RUNTIME_MIGRATION.read_text(encoding="utf-8")
    registry = (
        ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs"
    ).read_text(encoding="utf-8")
    warren = (ROOT / "warren" / "agent" / "src" / "main.rs").read_text(
        encoding="utf-8"
    )
    python_runtime = (
        ROOT / "crates" / "pg_rvbbit" / "src" / "python_runtime.rs"
    ).read_text(encoding="utf-8")
    bootstrap = (ROOT / "docker" / "uber" / "bootstrap.sh").read_text(
        encoding="utf-8"
    )

    assert "language IN ('python', 'data_mover')" in migration
    assert "register_data_mover_runtime" in migration
    assert "0289_data_mover_runtime_registry" in registry
    assert '"data_mover" => {' in warren
    assert "registering data-mover runtime" in warren
    assert '| "data_mover"' in warren
    assert python_runtime.count("r.language = 'python'") >= 2
    assert "data/dlt-mirror)" in bootstrap
    assert "r.language = 'data_mover'" in bootstrap
