"""0484/0486: pg_rvbbit counts reads, and the autopilot classifies tables by them.

These run against a migrated RVBBIT bench database (see conftest) and skip
otherwise. Everything happens inside one rolled-back transaction."""
import pytest


def _installed(conn):
    return conn.execute(
        "SELECT to_regclass('rvbbit.accel_read_days') IS NOT NULL "
        "AND to_regprocedure('rvbbit.accel_autopilot_evaluate()') IS NOT NULL"
    ).fetchone()[0]


def _read_on(conn, table, days_ago):
    """Evidence that clients read `table` on these days (0 = today)."""
    conn.execute(
        "INSERT INTO rvbbit.accel_read_evidence (table_oid, last_read_at) "
        "VALUES (%s::regclass, now() - make_interval(days => %s)) "
        "ON CONFLICT (table_oid) DO UPDATE SET last_read_at = EXCLUDED.last_read_at",
        (table, min(days_ago)),
    )
    conn.execute(
        "INSERT INTO rvbbit.accel_read_days (table_oid, day) "
        "SELECT %s::regclass, (now() AT TIME ZONE 'UTC')::date - d FROM unnest(%s::int[]) d "
        "ON CONFLICT DO NOTHING",
        (table, list(days_ago)),
    )


def _candidate(conn, table):
    return conn.execute(
        "SELECT status, read_days, reasons FROM rvbbit.accel_autopilot_candidates "
        "WHERE table_oid = %s::regclass",
        (table,),
    ).fetchone()


def test_a_client_select_is_a_read_and_an_opted_out_session_is_not(rvbbit, temp_table):
    if not _installed(rvbbit):
        pytest.skip("run rvbbit.migrate() to install 0486")
    quiet = f"{temp_table}_quiet"
    rvbbit.execute("BEGIN")
    try:
        rvbbit.execute(f"CREATE TABLE {temp_table} (id int, payload text)")
        rvbbit.execute(f"CREATE TABLE {quiet} (id int)")
        rvbbit.execute(f"INSERT INTO {temp_table} SELECT g, md5(g::text) FROM generate_series(1, 100) g")
        rvbbit.execute(f"INSERT INTO {quiet} SELECT id FROM {temp_table}")  # writes don't count
        rvbbit.execute(f"SELECT count(*) FROM {temp_table}").fetchone()
        reads = rvbbit.execute(
            "SELECT reads FROM rvbbit.table_reads() WHERE table_oid = %s::regclass", (temp_table,)
        ).fetchone()
        if reads is None:
            pytest.skip("pg_rvbbit is not in shared_preload_libraries here; nothing is counted")
        assert reads[0] >= 1

        rvbbit.execute("SET LOCAL rvbbit.count_reads = off")
        rvbbit.execute(f"SELECT count(*) FROM {quiet}").fetchone()
        rvbbit.execute("SET LOCAL rvbbit.count_reads = on")
        assert rvbbit.execute(
            "SELECT count(*) FROM rvbbit.table_reads() WHERE table_oid = %s::regclass", (quiet,)
        ).fetchone()[0] == 0

        rvbbit.execute("SELECT rvbbit.accel_capture_read_evidence()")
        assert rvbbit.execute(
            "SELECT count(*) FROM rvbbit.accel_read_evidence WHERE table_oid = %s::regclass", (temp_table,)
        ).fetchone()[0] == 1
        assert rvbbit.execute(
            "SELECT count(*) FROM rvbbit.accel_read_days "
            "WHERE table_oid = %s::regclass AND day = (now() AT TIME ZONE 'UTC')::date", (temp_table,)
        ).fetchone()[0] == 1
    finally:
        rvbbit.execute("ROLLBACK")


def test_observe_classifies_by_read_days_and_changes_nothing(rvbbit, temp_table):
    if not _installed(rvbbit):
        pytest.skip("run rvbbit.migrate() to install 0486")
    once, tiny, hand = f"{temp_table}_once", f"{temp_table}_tiny", f"{temp_table}_hand"
    rvbbit.execute("BEGIN")
    try:
        rvbbit.execute("UPDATE rvbbit.accel_autopilot_config SET mode = 'observe', "
                       "min_table_bytes = 65536, act_promote_min_read_days = 3 WHERE singleton")
        for name in (temp_table, once, hand):
            rvbbit.execute(f"CREATE TABLE {name} (id int, payload text)")
            rvbbit.execute(f"INSERT INTO {name} SELECT g, repeat(md5(g::text), 3) FROM generate_series(1, 2000) g")
        rvbbit.execute(f"CREATE TABLE {tiny} (id int)")
        rvbbit.execute(f"INSERT INTO {tiny} VALUES (1)")
        rvbbit.execute(f"SELECT rvbbit.enable_table('{hand}'::regclass, true)")
        rvbbit.execute(f"SELECT rvbbit.set_accel_policy('{hand}'::regclass, strategy => 'manual')")
        _read_on(rvbbit, temp_table, [0, 2, 5])
        _read_on(rvbbit, once, [0])
        _read_on(rvbbit, tiny, [0, 1, 2, 3])
        _read_on(rvbbit, hand, [0, 1, 2, 3])
        rvbbit.execute(  # a read outside the 7-day window does not count
            "INSERT INTO rvbbit.accel_read_days VALUES (%s::regclass, (now() AT TIME ZONE 'UTC')::date - 9)",
            (temp_table,),
        )

        result = rvbbit.execute("SELECT rvbbit.accel_autopilot_observe('test')").fetchone()[0]
        assert result["status"] == "ok" and result["mode"] == "observe"
        assert "observe mode changes nothing" in result["message"]

        status, days, reasons = _candidate(rvbbit, temp_table)
        assert (status, days) == ("ready", 3)
        assert "read on 3 of the last 7 days (ready at 3)" in reasons
        assert _candidate(rvbbit, once)[:2] == ("observing", 1)
        status, _, reasons = _candidate(rvbbit, tiny)
        assert status == "held" and any(r.startswith("smaller than") for r in reasons)
        status, _, reasons = _candidate(rvbbit, hand)
        assert status == "held" and "policy set by hand" in reasons

        # Observing changes no table and no policy; act refuses outside act mode.
        assert rvbbit.execute(
            "SELECT count(*) FROM rvbbit.accel_policy WHERE table_oid = %s::regclass", (temp_table,)
        ).fetchone()[0] == 0
        assert rvbbit.execute(
            "SELECT count(*) FROM rvbbit.tables WHERE table_oid = %s::regclass", (temp_table,)
        ).fetchone()[0] == 0
        noop = rvbbit.execute("SELECT rvbbit.accel_autopilot_act('test', false)").fetchone()[0]
        assert noop["acted"] is False

        counts = rvbbit.execute("SELECT rvbbit.accel_autopilot_status()").fetchone()[0]["counts"]
        assert counts["ready"] >= 1 and "parked" in counts
    finally:
        rvbbit.execute("ROLLBACK")


def test_partitions_and_disabled_tables_are_held(rvbbit, temp_table):
    if not _installed(rvbbit):
        pytest.skip("run rvbbit.migrate() to install 0486")
    part, off = f"{temp_table}_p1", f"{temp_table}_off"
    rvbbit.execute("BEGIN")
    try:
        rvbbit.execute("UPDATE rvbbit.accel_autopilot_config SET mode = 'observe', min_table_bytes = 0 WHERE singleton")
        rvbbit.execute(f"CREATE TABLE {temp_table} (id int, payload text) PARTITION BY RANGE (id)")
        rvbbit.execute(f"CREATE TABLE {part} PARTITION OF {temp_table} FOR VALUES FROM (0) TO (1000000)")
        rvbbit.execute(f"CREATE TABLE {off} (id int)")
        rvbbit.execute(f"INSERT INTO {off} SELECT g FROM generate_series(1, 100) g")
        rvbbit.execute(f"SELECT rvbbit.enable_table('{off}'::regclass, true)")
        rvbbit.execute(f"SELECT rvbbit.disable_table('{off}'::regclass, false)")
        for name in (part, off):
            _read_on(rvbbit, name, [0, 1, 2, 3])
        rvbbit.execute("SELECT rvbbit.accel_autopilot_observe('test')")
        status, _, reasons = _candidate(rvbbit, part)
        assert status == "held" and any("partition" in r for r in reasons)
        status, _, reasons = _candidate(rvbbit, off)
        assert status == "held" and "acceleration disabled by hand" in reasons
    finally:
        rvbbit.execute("ROLLBACK")


def test_observer_off_mode_records_a_skipped_run(rvbbit):
    if not _installed(rvbbit):
        pytest.skip("run rvbbit.migrate() to install 0486")
    rvbbit.execute("BEGIN")
    try:
        rvbbit.execute("UPDATE rvbbit.accel_autopilot_config SET mode = 'off' WHERE singleton")
        result = rvbbit.execute("SELECT rvbbit.accel_autopilot_observe('test-off')").fetchone()[0]
        assert result["status"] == "skipped"
        run = rvbbit.execute(
            "SELECT status, message, source FROM rvbbit.accel_observer_runs WHERE run_id = %s",
            (result["run_id"],),
        ).fetchone()
        assert run == ("skipped", "autopilot mode is off", "test-off")
    finally:
        rvbbit.execute("ROLLBACK")


def test_bundled_services_tables_are_never_candidates(rvbbit, temp_table):
    if not rvbbit.execute(
        "SELECT position('clover_telemetry' IN pg_get_functiondef('rvbbit.accel_autopilot_evaluate()'::regprocedure)) > 0"
    ).fetchone()[0]:
        pytest.skip("run rvbbit.migrate() to install 0487")
    ledger = f"clover_telemetry.{temp_table}"
    rvbbit.execute("BEGIN")
    try:
        rvbbit.execute("UPDATE rvbbit.accel_autopilot_config SET mode = 'observe', min_table_bytes = 0 WHERE singleton")
        rvbbit.execute("CREATE SCHEMA IF NOT EXISTS clover_telemetry")
        rvbbit.execute(f"CREATE TABLE {ledger} (id int)")
        rvbbit.execute(f"INSERT INTO {ledger} SELECT g FROM generate_series(1, 100) g")
        _read_on(rvbbit, ledger, [0, 1, 2, 3])
        rvbbit.execute("SELECT rvbbit.accel_autopilot_observe('test')")
        assert _candidate(rvbbit, ledger) is None
    finally:
        rvbbit.execute("ROLLBACK")
