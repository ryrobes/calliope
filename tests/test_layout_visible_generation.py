"""Layout variants are built from and validated against the *visible* canonical
generation. Retained snapshot generations (cubes, truncate-and-load mirrors)
must not inflate expected rows or leave Vortex variants for dead row groups —
that made every such table read 'Variant behind' forever."""
import uuid

import pytest


def _snapshot(conn, name: str, rows: int):
    conn.execute(
        f"SELECT * FROM rvbbit.snapshot_load('{name}'::regclass, "
        f"$q$SELECT g AS id, 'r' || (g % 4) AS region, DATE '2026-01-01' + (g % 30) AS event_date, g % 100 AS amount "
        f"FROM generate_series(1, {rows}) AS g$q$)"
    )


def _build_layouts(conn, table_oid: int, passes: int = 3):
    for _ in range(passes):
        conn.execute("SELECT * FROM rvbbit.layout_tick_worker(1, 1, false)")


def test_retained_snapshot_generations_do_not_leave_layouts_behind(rvbbit):
    if not rvbbit.execute("SELECT to_regprocedure('rvbbit.snapshot_load(regclass,text)') IS NOT NULL").fetchone()[0]:
        pytest.skip("snapshot_load is not installed")
    name = f"layout_gen_{uuid.uuid4().hex[:8]}"
    try:
        rvbbit.execute("SET rvbbit.compact_vortex_layout = 'on'")
        rvbbit.execute(
            f"CREATE TABLE {name} (id integer PRIMARY KEY, region text NOT NULL, "
            f"event_date date NOT NULL, amount integer NOT NULL) USING rvbbit"
        )
        rvbbit.execute(
            f"SELECT rvbbit.set_acceleration_storage_policy('{name}'::regclass, "
            "generation_semantics => 'snapshot', history_policy => 'retained')"
        )
        table_oid = rvbbit.execute(f"SELECT '{name}'::regclass::oid").fetchone()[0]
        _snapshot(rvbbit, name, 256)
        rvbbit.execute(f"SELECT rvbbit.accept_workload_layout('{name}'::regclass, 'cluster', 'event_date')")
        rvbbit.execute("SELECT rvbbit.enqueue_variant_build(%s::regclass, 'visible_gen_test', 10)", (table_oid,))
        _build_layouts(rvbbit, table_oid)

        # A second, smaller snapshot: the old generation is retained, one is visible.
        _snapshot(rvbbit, name, 100)
        gens, visible_rows = rvbbit.execute(
            "SELECT (SELECT count(DISTINCT generation) FROM rvbbit.row_groups WHERE table_oid = %s), "
            "(SELECT coalesce(sum(n_rows), 0) FROM rvbbit.row_groups_visible WHERE table_oid = %s)",
            (table_oid, table_oid),
        ).fetchone()
        assert gens >= 2 and visible_rows == 100

        rvbbit.execute("SELECT rvbbit.enqueue_variant_build(%s::regclass, 'visible_gen_test', 10)", (table_oid,))
        _build_layouts(rvbbit, table_oid, passes=4)

        statuses = {
            layout: (status, expected, actual)
            for layout, status, expected, actual in rvbbit.execute(
                "SELECT layout, status, expected_rows, actual_rows FROM rvbbit.layout_variant_status WHERE table_oid = %s",
                (table_oid,),
            ).fetchall()
        }
        # Every configured layout expects exactly the visible rows, and is ready.
        for layout, (status, expected, actual) in statuses.items():
            assert expected == 100, (layout, statuses)
            assert status == "ready" and actual == 100, (layout, statuses)
        assert "vortex_scan" in statuses and any(l.startswith("cluster:") for l in statuses)
        # No Vortex variant survives for a retired row group.
        assert rvbbit.execute(
            "SELECT count(*) FROM rvbbit.row_group_variants v WHERE v.table_oid = %s AND v.layout = 'vortex_scan' "
            "AND NOT EXISTS (SELECT 1 FROM rvbbit.row_groups_visible rg WHERE rg.table_oid = v.table_oid AND rg.rg_id = v.rg_id)",
            (table_oid,),
        ).fetchone()[0] == 0
    finally:
        rvbbit.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
