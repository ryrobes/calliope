"""0325/0326/0486: soft daily budgets and the read-driven acceleration actor.

These run against a migrated RVBBIT bench database (see conftest) and skip
otherwise. Everything happens inside one rolled-back transaction."""
import pytest


def _installed(conn):
    return conn.execute(
        "SELECT to_regprocedure('rvbbit.accel_autopilot_act(text,boolean)') IS NOT NULL"
    ).fetchone()[0]


def test_budget_accounting_is_soft_and_charges_only_expensive_refreshes(rvbbit):
    if not _installed(rvbbit):
        pytest.skip("run rvbbit.migrate() to install 0325/0326")
    # Overdue horizon: max(target, factor × min_interval, 15 min).
    assert rvbbit.execute("SELECT rvbbit.accel_budget_overdue_secs(NULL, 900)").fetchone()[0] == 3600
    assert rvbbit.execute("SELECT rvbbit.accel_budget_overdue_secs(7200, 60)").fetchone()[0] == 7200
    assert rvbbit.execute("SELECT rvbbit.accel_budget_overdue_secs(NULL, 60)").fetchone()[0] == 900
    definition = rvbbit.execute(
        "SELECT pg_get_functiondef('rvbbit._accel_tick_batch(integer,boolean,integer)'::regprocedure)"
    ).fetchone()[0]
    assert "rvbbit.accel_budget_spent(cand.f_oid, cand.budget_epoch_at)" in definition
    assert "refreshing anyway" in definition
    rvbbit.execute("BEGIN")
    try:
        rvbbit.execute("INSERT INTO rvbbit.settings (key, value) VALUES ('accel_budget_cheap_refresh_ms', '5000') "
                       "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value")
        assert rvbbit.execute("SELECT rvbbit.accel_budget_setting_int('accel_budget_cheap_refresh_ms', 2000)").fetchone()[0] == 5000
        # A cheap delta (600 ms) and an expensive full (40 s) on the same table: only one is charged.
        rvbbit.execute("SELECT rvbbit._accel_log_event(nextval('rvbbit.accel_sweep_id_seq'), 'freshness', 'table_finished', "
                       "4242::oid, 't', 'scheduled', 'delta', 'dirty', 'ok', NULL, 600, 10, 1)")
        rvbbit.execute("SELECT rvbbit._accel_log_event(nextval('rvbbit.accel_sweep_id_seq'), 'freshness', 'table_finished', "
                       "4242::oid, 't', 'scheduled', 'full', 'dirty', 'ok', NULL, 40000, 10, 1)")
        rvbbit.execute("SELECT rvbbit._accel_log_event(nextval('rvbbit.accel_sweep_id_seq'), 'replacement', 'table_finished', "
                       "4242::oid, 't', 'scheduled', 'full', 'variant', 'ok', NULL, 90000, 10, 1)")
        assert rvbbit.execute("SELECT rvbbit.accel_budget_spent(4242::oid, now() - interval '1 hour')").fetchone()[0] == 1
    finally:
        rvbbit.execute("ROLLBACK")


def _reads_installed(conn):
    return conn.execute("SELECT to_regclass('rvbbit.accel_read_days') IS NOT NULL").fetchone()[0]


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


def test_actor_promotes_tables_read_on_enough_days_and_hand_edits_release_them(rvbbit, temp_table):
    if not (_installed(rvbbit) and _reads_installed(rvbbit)):
        pytest.skip("run rvbbit.migrate() to install 0486")
    rvbbit.execute("BEGIN")
    try:
        rvbbit.execute(f"CREATE TABLE {temp_table} (id int PRIMARY KEY, payload text)")
        rvbbit.execute(f"INSERT INTO {temp_table} SELECT g, md5(g::text) FROM generate_series(1, 500) g")
        rvbbit.execute(f"ANALYZE {temp_table}")
        rvbbit.execute("UPDATE rvbbit.accel_autopilot_config SET mode = 'observe', min_table_bytes = 0, "
                       "act_promote_min_read_days = 3 WHERE singleton")
        noop = rvbbit.execute("SELECT rvbbit.accel_autopilot_act('test', true)").fetchone()[0]
        assert noop["acted"] is False and "act" in noop["reason"]

        rvbbit.execute("UPDATE rvbbit.accel_autopilot_config SET mode = 'act' WHERE singleton")
        _read_on(rvbbit, temp_table, [0, 1])
        not_yet = rvbbit.execute("SELECT rvbbit.accel_autopilot_act('test', false)").fetchone()[0]
        assert temp_table not in [p["table"].split(".")[-1] for p in not_yet["promoted"]]

        _read_on(rvbbit, temp_table, [0, 1, 4])
        rvbbit.execute("SELECT rvbbit.accel_autopilot_observe('test')")
        result = rvbbit.execute("SELECT rvbbit.accel_autopilot_act('test', false)").fetchone()[0]
        mine = [p for p in result["promoted"] if p["table"].split(".")[-1] == temp_table]
        assert len(mine) == 1 and mine[0]["read_days"] == 3
        assert result["failures"] == []
        row = rvbbit.execute(
            "SELECT strategy, active, managed_by, daily_refresh_budget, min_interval_secs "
            f"FROM rvbbit.accel_policy WHERE table_oid = '{temp_table}'::regclass"
        ).fetchone()
        assert row == ("scheduled", True, "autopilot", None, 900)
        assert rvbbit.execute(
            f"SELECT status FROM rvbbit.accel_autopilot_candidates WHERE table_oid = '{temp_table}'::regclass"
        ).fetchone()[0] == "managed"
        assert rvbbit.execute(
            "SELECT count(*) FROM rvbbit.accel_activity_log WHERE lane = 'autopilot' AND action = 'promote' "
            f"AND status = 'ok' AND table_oid = '{temp_table}'::regclass"
        ).fetchone()[0] == 1
        # 0495: one row explains the decision.
        if rvbbit.execute("SELECT to_regclass('rvbbit.accel_autopilot_decisions')").fetchone()[0]:
            decision = rvbbit.execute(
                "SELECT status, managed_by, policy_active, read_days, last_action, reasons "
                f"FROM rvbbit.accel_autopilot_decisions WHERE table_oid = '{temp_table}'::regclass"
            ).fetchone()
            assert decision[:5] == ("managed", "autopilot", True, 3, "promote")
            assert any("promoted by the autopilot" in reason for reason in decision[5])
        # The act run builds nothing; the freshness worker does.
        assert rvbbit.execute(
            f"SELECT count(*) FROM rvbbit.row_groups WHERE table_oid = '{temp_table}'::regclass"
        ).fetchone()[0] == 0
        again = rvbbit.execute("SELECT rvbbit.accel_autopilot_act('test', false)").fetchone()[0]
        assert temp_table not in [p["table"].split(".")[-1] for p in again["promoted"]]
        # The worker plans the first build (it cannot read this transaction's own rows).
        plan = rvbbit.execute(
            "SELECT action, reason FROM rvbbit._accel_tick_batch(50, true, 1) WHERE table_oid = %s::regclass",
            (temp_table,),
        ).fetchone()
        assert plan is not None and plan[0] in ("full", "delta") and "baseline" in plan[1]

        # An operator's edit releases the table from management (never parked afterwards).
        rvbbit.execute(f"SELECT rvbbit.set_accel_policy('{temp_table}'::regclass, strategy => 'scheduled', "
                       "min_interval_secs => 300, note => 'operator override')")
        assert rvbbit.execute(
            f"SELECT managed_by FROM rvbbit.accel_policy WHERE table_oid = '{temp_table}'::regclass"
        ).fetchone()[0] is None
    finally:
        rvbbit.execute("ROLLBACK")


def test_actor_parks_unread_tables_only_after_a_full_window_of_counting(rvbbit, temp_table):
    if not (_installed(rvbbit) and _reads_installed(rvbbit)):
        pytest.skip("run rvbbit.migrate() to install 0486")
    awake, hand = f"{temp_table}_awake", f"{temp_table}_hand"
    rvbbit.execute("BEGIN")
    try:
        rvbbit.execute("UPDATE rvbbit.accel_autopilot_config SET mode = 'act', min_table_bytes = 0, "
                       "act_park_idle_days = 3, act_max_parks_per_run = 100 WHERE singleton")
        for name in (temp_table, awake, hand):
            rvbbit.execute(f"CREATE TABLE {name} (id int PRIMARY KEY, payload text)")
            rvbbit.execute(f"INSERT INTO {name} SELECT g, md5(g::text) FROM generate_series(1, 50) g")
            rvbbit.execute(f"SELECT rvbbit.enable_table('{name}'::regclass, true)")
        for name in (temp_table, hand):
            rvbbit.execute(f"SELECT rvbbit.set_accel_policy('{name}'::regclass, strategy => 'scheduled')")
        rvbbit.execute(f"UPDATE rvbbit.accel_policy SET managed_by = 'autopilot', managed_since = now() - interval '10 days' "
                       f"WHERE table_oid = '{temp_table}'::regclass")
        # Counting started yesterday: nobody read the managed table, but that proves nothing yet.
        rvbbit.execute("DELETE FROM rvbbit.accel_read_days")
        rvbbit.execute("DELETE FROM rvbbit.accel_read_evidence")
        _read_on(rvbbit, awake, [0, 1])
        blocked = rvbbit.execute("SELECT rvbbit.accel_autopilot_act('test', false)").fetchone()[0]
        assert blocked["parked"] == [] and blocked["park_guard"]["may_park"] is False

        # Reads have been counted for 10 days and the database is read today: parked once.
        _read_on(rvbbit, awake, [0, 10])
        _read_on(rvbbit, hand, [5])
        parked = rvbbit.execute("SELECT rvbbit.accel_autopilot_act('test', false)").fetchone()[0]
        parked_names = [p["table"].split(".")[-1] for p in parked["parked"]]
        assert temp_table in parked_names and hand not in parked_names   # never the hand-set one
        row = rvbbit.execute(f"SELECT active, parked_at IS NOT NULL, managed_by FROM rvbbit.accel_policy "
                             f"WHERE table_oid = '{temp_table}'::regclass").fetchone()
        assert row == (False, True, "autopilot")
        assert rvbbit.execute(f"SELECT active, managed_by FROM rvbbit.accel_policy "
                              f"WHERE table_oid = '{hand}'::regclass").fetchone() == (True, None)

        # ... and comes back the next run after someone reads it (clock time:
        # now() is frozen at the start of this test's transaction).
        _read_on(rvbbit, temp_table, [0])
        rvbbit.execute("UPDATE rvbbit.accel_read_evidence SET last_read_at = clock_timestamp() "
                       "WHERE table_oid = %s::regclass", (temp_table,))
        back = rvbbit.execute("SELECT rvbbit.accel_autopilot_act('test', false)").fetchone()[0]
        assert temp_table in [p["table"].split(".")[-1] for p in back["reactivated"]]
        assert rvbbit.execute(f"SELECT active, parked_at FROM rvbbit.accel_policy "
                              f"WHERE table_oid = '{temp_table}'::regclass").fetchone() == (True, None)
    finally:
        rvbbit.execute("ROLLBACK")


def _rebuilt_from_scratch(conn, table, times):
    """Evidence that the freshness worker rebuilt `table`'s copy this many times today."""
    conn.execute(
        "INSERT INTO rvbbit.acceleration_operations (table_oid, table_name, operation, status, started_at, finished_at) "
        "SELECT %s::regclass, %s, 'rebuild_acceleration', 'ok', now() - make_interval(mins => 10 * g), "
        "now() - make_interval(mins => 10 * g) + interval '2 seconds' FROM generate_series(1, %s) g",
        (table, table, times),
    )


def test_actor_releases_tables_rebuilt_from_scratch_too_often_and_retries_later(rvbbit, temp_table):
    """0493: an autopilot-owned table whose copy is rebuilt from scratch all day is
    released (files retired, acceleration off, reads on the plain table); a hand-set
    one only gets a reason; a released table comes back after the retry window."""
    if not rvbbit.execute("SELECT to_regprocedure('rvbbit.accel_rebuilds_last_day(oid)') IS NOT NULL").fetchone()[0]:
        pytest.skip("run rvbbit.migrate() to install 0493")
    hand = f"{temp_table}_hand"
    try:
        # Committed, with a real accelerated copy: a release has files to retire.
        for name in (temp_table, hand):
            rvbbit.execute(f"CREATE TABLE {name} (id int PRIMARY KEY, payload text)")
            rvbbit.execute(f"INSERT INTO {name} SELECT g, md5(g::text) FROM generate_series(1, 2000) g")
            rvbbit.execute(f"SELECT rvbbit.enable_table('{name}'::regclass, true)")
            rvbbit.execute(f"SELECT rvbbit.rebuild_acceleration('{name}'::regclass, false)")
            rvbbit.execute(f"ANALYZE {name}")
        assert rvbbit.execute(
            f"SELECT count(*) FROM rvbbit.row_groups WHERE table_oid = '{temp_table}'::regclass"
        ).fetchone()[0] > 0

        rvbbit.execute("BEGIN")
        rvbbit.execute("UPDATE rvbbit.accel_autopilot_config SET mode = 'act', min_table_bytes = 0, "
                       "act_churn_rebuilds_per_day = 12, act_churn_retry_days = 7, "
                       "act_promote_min_read_days = 2, act_park_idle_days = 30 WHERE singleton")
        for name in (temp_table, hand):
            rvbbit.execute(f"SELECT rvbbit.set_accel_policy('{name}'::regclass, strategy => 'scheduled')")
            _read_on(rvbbit, name, [0, 1, 2])
            # Only the evidence below counts (the setup build was a rebuild too).
            rvbbit.execute("DELETE FROM rvbbit.acceleration_operations WHERE table_oid = %s::regclass", (name,))
        rvbbit.execute(f"UPDATE rvbbit.accel_policy SET managed_by = 'autopilot', managed_since = now() - interval '1 day' "
                       f"WHERE table_oid = '{temp_table}'::regclass")

        # Rebuilt 11 times today: below the line, nothing happens.
        _rebuilt_from_scratch(rvbbit, temp_table, 11)
        _rebuilt_from_scratch(rvbbit, hand, 30)
        calm = rvbbit.execute("SELECT rvbbit.accel_autopilot_act('test', false)").fetchone()[0]
        assert calm["released"] == [] and not [f for f in calm["failures"] if temp_table in f["table"]]

        # The 12th rebuild: released, files retired, triggers gone.
        _rebuilt_from_scratch(rvbbit, temp_table, 1)
        result = rvbbit.execute("SELECT rvbbit.accel_autopilot_act('test', false)").fetchone()[0]
        assert not [f for f in result["failures"] if temp_table in f["table"]]
        mine = [r for r in result["released"] if r["table"].split(".")[-1] == temp_table]
        assert len(mine) == 1 and mine[0]["rebuilds_last_day"] == 12 and mine[0]["files_retired"] is True
        assert hand not in [r["table"].split(".")[-1] for r in result["released"]]   # never a hand-set policy
        assert rvbbit.execute(
            "SELECT strategy, active, managed_by, park_reason, parked_at IS NOT NULL FROM rvbbit.accel_policy "
            f"WHERE table_oid = '{temp_table}'::regclass"
        ).fetchone() == ("scheduled", False, "autopilot", "churn", True)
        assert rvbbit.execute(
            f"SELECT acceleration_enabled FROM rvbbit.tables WHERE table_oid = '{temp_table}'::regclass"
        ).fetchone()[0] is False
        assert rvbbit.execute(
            f"SELECT count(*) FROM rvbbit.row_groups WHERE table_oid = '{temp_table}'::regclass"
        ).fetchone()[0] == 0
        assert rvbbit.execute(
            "SELECT count(*) FROM rvbbit.orphaned_files WHERE reason = 'operator_retire_acceleration' "
            f"AND table_oid = '{temp_table}'::regclass"
        ).fetchone()[0] > 0
        assert rvbbit.execute(
            f"SELECT count(*) FROM pg_trigger WHERE tgrelid = '{temp_table}'::regclass "
            "AND NOT tgisinternal AND tgname LIKE 'rvbbit%%'"
        ).fetchone()[0] == 0
        rvbbit.execute("SET LOCAL enable_seqscan = off")
        plan = "\n".join(r[0] for r in rvbbit.execute(f"EXPLAIN SELECT payload FROM {temp_table} WHERE id = 7"))
        assert "Rvbbit" not in plan and "Index" in plan
        rvbbit.execute("RESET enable_seqscan")
        assert rvbbit.execute(f"SELECT payload FROM {temp_table} WHERE id = 7").fetchone()[0] == \
            rvbbit.execute("SELECT md5('7')").fetchone()[0]

        rvbbit.execute("SELECT rvbbit.accel_autopilot_observe('test')")
        status, reasons = rvbbit.execute(
            f"SELECT status, reasons FROM rvbbit.accel_autopilot_candidates WHERE table_oid = '{temp_table}'::regclass"
        ).fetchone()
        assert status == "released" and any("rebuilt from scratch too often" in r for r in reasons)
        status, reasons = rvbbit.execute(
            f"SELECT status, reasons FROM rvbbit.accel_autopilot_candidates WHERE table_oid = '{hand}'::regclass"
        ).fetchone()
        assert status == "managed" and any("rebuilt from scratch 30 times" in r for r in reasons)
        assert rvbbit.execute("SELECT (rvbbit.accel_autopilot_status() -> 'counts' ->> 'released')::int").fetchone()[0] >= 1

        # Reading it again doesn't bring it back inside the retry window...
        rvbbit.execute("UPDATE rvbbit.accel_read_evidence SET last_read_at = clock_timestamp() "
                       "WHERE table_oid = %s::regclass", (temp_table,))
        early = rvbbit.execute("SELECT rvbbit.accel_autopilot_act('test', false)").fetchone()[0]
        assert temp_table not in [r["table"].split(".")[-1] for r in early["reactivated"]]
        assert temp_table not in [r["table"].split(".")[-1] for r in early["released"]]

        # ... after it, a table still read often enough is turned on again for the worker to rebuild.
        rvbbit.execute("UPDATE rvbbit.accel_policy SET parked_at = now() - interval '8 days' "
                       "WHERE table_oid = %s::regclass", (temp_table,))
        rvbbit.execute("DELETE FROM rvbbit.acceleration_operations WHERE table_oid = %s::regclass", (temp_table,))
        back = rvbbit.execute("SELECT rvbbit.accel_autopilot_act('test', false)").fetchone()[0]
        mine = [r for r in back["reactivated"] if r["table"].split(".")[-1] == temp_table]
        assert len(mine) == 1 and mine[0]["was"] == "released"
        assert rvbbit.execute(
            "SELECT p.active, p.park_reason, p.managed_by, t.acceleration_enabled FROM rvbbit.accel_policy p "
            f"JOIN rvbbit.tables t USING (table_oid) WHERE p.table_oid = '{temp_table}'::regclass"
        ).fetchone() == (True, None, "autopilot", True)

        # An operator's edit takes the table back from the autopilot, reason and all.
        rvbbit.execute("SELECT set_config('rvbbit.accel_autopilot_actor', 'on', true)")
        rvbbit.execute("UPDATE rvbbit.accel_policy SET active = false, park_reason = 'churn', parked_at = now() "
                       "WHERE table_oid = %s::regclass", (temp_table,))
        rvbbit.execute("SELECT set_config('rvbbit.accel_autopilot_actor', 'off', true)")
        rvbbit.execute(f"SELECT rvbbit.set_accel_policy('{temp_table}'::regclass, strategy => 'scheduled', "
                       "note => 'operator override')")
        assert rvbbit.execute(
            f"SELECT managed_by, park_reason FROM rvbbit.accel_policy WHERE table_oid = '{temp_table}'::regclass"
        ).fetchone() == (None, None)
    finally:
        rvbbit.execute("ROLLBACK")
        rvbbit.execute(f"DROP TABLE IF EXISTS {hand} CASCADE")


def test_size_bounds_measure_the_rows_not_their_toast(rvbbit, temp_table):
    """0498: a table of a few thousand rows whose bulk is TOASTed text stays held as small."""
    rvbbit.execute("BEGIN")
    try:
        rvbbit.execute(f"CREATE TABLE {temp_table} (id int PRIMARY KEY, body text)")
        rvbbit.execute(f"ALTER TABLE {temp_table} ALTER COLUMN body SET STORAGE EXTERNAL")
        rvbbit.execute(f"INSERT INTO {temp_table} SELECT g, repeat(md5(g::text), 200) FROM generate_series(1, 2000) g")
        rvbbit.execute(f"ANALYZE {temp_table}")
        rvbbit.execute("UPDATE rvbbit.accel_autopilot_config SET mode = 'observe', min_table_bytes = 4 * 1024 * 1024, "
                       "act_promote_min_read_days = 3 WHERE singleton")
        _read_on(rvbbit, temp_table, [0, 1, 2])
        heap, total = rvbbit.execute(
            f"SELECT pg_relation_size('{temp_table}'::regclass), pg_total_relation_size('{temp_table}'::regclass)"
        ).fetchone()
        assert heap < 4 * 1024 * 1024 < total   # 2,000 short rows, 12 MB of TOAST

        status, table_bytes, reasons = rvbbit.execute(
            "SELECT status, table_bytes, reasons FROM rvbbit.accel_autopilot_evaluate() "
            "WHERE table_oid = %s::regclass", (temp_table,)
        ).fetchone()
        assert (status, table_bytes) == ("held", heap)
        assert "smaller than 4096 kB of table data" in reasons
    finally:
        rvbbit.execute("ROLLBACK")


def test_live_activity_never_reports_a_1970_start_from_a_truncated_stamp(rvbbit):
    """0327: application_name is capped at 63 chars; a t= epoch cut to 't=178695'
    read as January 1970 and the observatory showed a 56-year running build."""
    if not rvbbit.execute(
        "SELECT position('rvbbit/fresh t=%s o=%s a=%s' IN pg_get_functiondef('rvbbit._accel_tick_batch(integer,boolean,integer)'::regprocedure)) > 0"
    ).fetchone()[0]:
        pytest.skip("run rvbbit.migrate() to install 0327")
    rvbbit.execute("BEGIN")
    try:
        rvbbit.execute("SELECT set_config('application_name', left('rvbbit/fresh s=123456 w=10/12 o=1234567 a=build_vortex t=1786953416.933 some.table', 63), true)")
        rvbbit.execute("SELECT pg_stat_clear_snapshot()")
        started_recent, elapsed = rvbbit.execute(
            "SELECT current_work_started_at > now() - interval '1 hour', current_work_elapsed_ms "
            "FROM rvbbit.accel_live_activity WHERE pid = pg_backend_pid()"
        ).fetchone()
        assert started_recent is True and elapsed < 60000
        rvbbit.execute("SELECT set_config('application_name', left('rvbbit/fresh t=' || round(extract(epoch from clock_timestamp())::numeric, 3) "
                       "|| ' o=1234567 a=build_vortex s=123456 w=10/12 some.very.long.table.name', 63), true)")
        rvbbit.execute("SELECT pg_stat_clear_snapshot()")
        row = rvbbit.execute(
            "SELECT action, table_oid::bigint, current_work_elapsed_ms < 60000 FROM rvbbit.accel_live_activity WHERE pid = pg_backend_pid()"
        ).fetchone()
        assert row == ("build_vortex", 1234567, True)   # essentials survive truncation
    finally:
        rvbbit.execute("ROLLBACK")


def test_observatory_views_share_the_dashboard_definitions(rvbbit):
    """0329: the observatory views carry the Calliope dashboard's vocabulary
    (eligible / current / fresh / synced) plus implicit targets, delta capability,
    autopilot state — and stay cheap enough for a live page."""
    if not rvbbit.execute("SELECT to_regclass('rvbbit.accel_observatory_tables') IS NOT NULL").fetchone()[0]:
        pytest.skip("run rvbbit.migrate() to install 0329")
    cov = rvbbit.execute("SELECT eligible_tables, current_tables, unaccelerated_tables, coverage_pct, autopilot_mode "
                         "FROM rvbbit.accel_observatory_coverage").fetchone()
    assert cov[0] == cov[1] + cov[2]
    assert cov[3] is None or 0 <= float(cov[3]) <= 100
    assert cov[4] in ("off", "observe", "act")
    # Clean tables are never 'beyond target', whatever their last refresh time.
    assert rvbbit.execute("SELECT count(*) FROM rvbbit.accel_observatory_tables "
                          "WHERE NOT shadow_heap_dirty AND target_bucket <> 'current'").fetchone()[0] == 0
    # A scheduled policy without a freshness target uses min_interval as its target.
    assert rvbbit.execute("SELECT count(*) FROM rvbbit.accel_observatory_tables WHERE strategy = 'scheduled' "
                          "AND freshness_target_secs IS NULL AND effective_target_secs IS DISTINCT FROM min_interval_secs").fetchone()[0] == 0
    # Fresh implies synced and parent current; the layout summary sums to the layouts.
    assert rvbbit.execute("SELECT count(*) FROM rvbbit.accel_observatory_layouts WHERE is_fresh AND NOT (layout_synced AND parent_current)").fetchone()[0] == 0
    n_layouts, n_summary = rvbbit.execute("SELECT (SELECT count(*) FROM rvbbit.accel_observatory_layouts), "
                                          "(SELECT coalesce(sum(layouts), 0) FROM rvbbit.accel_observatory_layout_summary)").fetchone()
    assert n_layouts == n_summary
    for view in ("accel_observatory_strategy", "accel_observatory_target_distance", "accel_observatory_attention",
                 "accel_observatory_rebuild_cost", "accel_observatory_live", "accel_observatory_autopilot_events"):
        rvbbit.execute(f"SELECT * FROM rvbbit.{view} LIMIT 1")
    rows = rvbbit.execute("SELECT count(*) FROM rvbbit.accel_observatory_timeline(3, 50)").fetchone()[0]
    assert rows <= 50
