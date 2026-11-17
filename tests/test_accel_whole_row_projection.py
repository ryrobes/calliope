"""Whole-row references through the accelerated scan decode every attribute.

Regression: `collect_needed_attnums` dropped the whole-row Var (attnum 0)
that pull_varattnos reports for `to_jsonb(t)` / `row_to_json(t)` /
`SELECT t` / `json_agg(t)`, found no column references, and fell back to
reading column 1 only. Every other attribute came back NULL — silently wrong
results on any accelerated relation (seen through describe_cube's sample and
plain agent SQL on the hosted appliance), while `rvbbit.force_heap_scan=on`
returned the real rows.
"""
from __future__ import annotations


def _heap(conn, sql):
    conn.execute("SET rvbbit.force_heap_scan = on")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.execute("RESET rvbbit.force_heap_scan")


def _routed_matches_heap(conn, sql):
    routed = conn.execute(sql).fetchall()
    heap = _heap(conn, sql)
    assert routed == heap, (routed, heap)
    return routed


def test_whole_row_references_decode_every_attribute(rvbbit, temp_table):
    rvbbit.execute("SET rvbbit.compact_vortex_layout = 'off'")
    rvbbit.execute("SET rvbbit.compact_hive_layout = 'off'")
    rvbbit.execute(
        f"""
        CREATE TABLE {temp_table} (
            metric_key text NOT NULL,
            period_start timestamptz NOT NULL,
            dimensions jsonb,
            value numeric,
            source_event_rows bigint
        ) USING rvbbit
        """
    )
    rvbbit.execute(
        f"""
        INSERT INTO {temp_table}
        SELECT 'sales_amount',
               timestamptz '2026-01-01 00:00:00+00' + (g || ' days')::interval,
               jsonb_build_object('promo', 'NO PROMOTION', 'n', g),
               (g * 1.25)::numeric,
               g * 10
        FROM generate_series(1, 200) AS g
        """
    )
    refreshed = rvbbit.execute(
        f"SELECT rvbbit.refresh_acceleration('{temp_table}'::regclass, false)"
    ).fetchone()[0]
    assert refreshed["status"] == "ok"

    plan = "\n".join(
        row[0] for row in rvbbit.execute(
            f"EXPLAIN (COSTS OFF) SELECT to_jsonb(t) FROM {temp_table} t ORDER BY period_start LIMIT 1"
        ).fetchall()
    )
    assert "Rvbbit" in plan, plan   # the accelerated scan is what is under test

    # to_jsonb / row_to_json / bare whole-row / json_agg all go through attnum 0.
    for sql in (
        f"SELECT to_jsonb(t) FROM {temp_table} t ORDER BY period_start LIMIT 3",
        f"SELECT row_to_json(t)::jsonb FROM {temp_table} t ORDER BY period_start LIMIT 3",
        f"SELECT (t.*)::text FROM {temp_table} t ORDER BY period_start LIMIT 3",
        f"SELECT jsonb_agg(to_jsonb(t) ORDER BY period_start) FROM {temp_table} t",
        # a whole-row Var mixed with ordinary column Vars and a qual
        f"SELECT to_jsonb(t), value FROM {temp_table} t WHERE source_event_rows > 1900 ORDER BY period_start",
    ):
        rows = _routed_matches_heap(rvbbit, sql)
        assert rows

    first = rvbbit.execute(
        f"SELECT to_jsonb(t) FROM {temp_table} t ORDER BY period_start LIMIT 1"
    ).fetchone()[0]
    assert first["metric_key"] == "sales_amount"
    assert first["value"] == 1.25 and first["source_event_rows"] == 10
    assert first["dimensions"] == {"promo": "NO PROMOTION", "n": 1}
    assert first["period_start"] is not None

    # count(*) with no Vars still works (the "read column 1" fallback is untouched).
    assert rvbbit.execute(f"SELECT count(*) FROM {temp_table}").fetchone()[0] == 200
