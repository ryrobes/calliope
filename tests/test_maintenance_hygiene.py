import uuid

import pytest


def test_orphan_reference_catalogs_have_exact_path_indexes(rvbbit):
    indexes = {
        row[0]
        for row in rvbbit.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'rvbbit' AND indexname IN ("
            "'row_groups_path_idx',"
            "'row_group_variants_path_idx',"
            "'text_dictionaries_path_idx')"
        ).fetchall()
    }
    assert indexes == {
        "row_groups_path_idx",
        "row_group_variants_path_idx",
        "text_dictionaries_path_idx",
    }


def test_catalog_excludes_private_relations_bounds_values_and_retains_per_scope(rvbbit):
    suffix = uuid.uuid4().hex[:8]
    schema = f"catalog_hygiene_{suffix}"
    graph = f"catalog_hygiene_{suffix}"
    visible = f"{schema}.orders"
    private = f"{schema}._dlt_pipeline_state"
    try:
        rvbbit.execute(f"CREATE SCHEMA {schema}")
        rvbbit.execute(f"CREATE TABLE {visible} (id int, payload text)")
        rvbbit.execute(f"CREATE TABLE {private} (id int, state text)")
        rvbbit.execute(
            f"INSERT INTO {visible} VALUES (1, repeat('x', 20000)), (2, repeat('y', 20000))"
        )
        rvbbit.execute(f"INSERT INTO {private} VALUES (1, repeat('z', 20000))")

        rvbbit.execute(
            "SELECT rvbbit.catalog_crawl(%s, %s, 100, 4, false, '')",
            ([schema], graph),
        )
        assert rvbbit.execute(
            "SELECT array_agg(DISTINCT rel_name ORDER BY rel_name) "
            "FROM rvbbit.catalog_snapshots WHERE graph_id = %s",
            (graph,),
        ).fetchone() == (["orders"],)
        assert rvbbit.execute(
            "SELECT max(octet_length(fingerprint::text)) < 10000 "
            "FROM rvbbit.catalog_snapshots WHERE graph_id = %s",
            (graph,),
        ).fetchone() == (True,)

        # Generate independent completed runs in the same exact crawl scope;
        # automatic retention keeps a tail for that scope, not a global tail.
        for _ in range(4):
            rvbbit.execute(
                "INSERT INTO rvbbit.catalog_runs(graph_id,status,schemas,finished_at) "
                "VALUES (%s,'ok',%s,clock_timestamp())",
                (graph, [schema]),
            )
        result = rvbbit.execute("SELECT rvbbit.catalog_maintain(2)").fetchone()[0]
        assert result["status"] == "ok"
        assert rvbbit.execute(
            "SELECT count(*) FROM rvbbit.catalog_runs WHERE graph_id = %s",
            (graph,),
        ).fetchone() == (2,)
    finally:
        rvbbit.execute(
            "DELETE FROM rvbbit.catalog_snapshots WHERE graph_id = %s", (graph,)
        )
        rvbbit.execute("DELETE FROM rvbbit.catalog_docs WHERE graph_id = %s", (graph,))
        rvbbit.execute("DELETE FROM rvbbit.catalog_runs WHERE graph_id = %s", (graph,))
        rvbbit.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def test_mirror_snapshot_publication_replaces_without_tombstones(rvbbit):
    suffix = uuid.uuid4().hex[:8]
    target = f"mirror_snapshot_{suffix}"
    staging = f"mirror_snapshot_stage_{suffix}"
    try:
        rvbbit.execute("SET rvbbit.compact_vortex_layout = 'off'")
        rvbbit.execute("SET rvbbit.compact_hive_layout = 'off'")
        rvbbit.execute(f"CREATE TABLE {target} (id int PRIMARY KEY, value text) USING rvbbit")
        rvbbit.execute(f"CREATE TABLE {staging} (id int PRIMARY KEY, value text)")
        rvbbit.execute(f"INSERT INTO {staging} VALUES (1,'one'),(2,'two'),(3,'three')")

        first = rvbbit.execute(
            f"SELECT rvbbit.publish_mirror_snapshot('{target}'::regclass, "
            f"'{staging}'::regclass, ARRAY['id','value'], ARRAY['id'])"
        ).fetchone()[0]
        assert first["publication_mode"] == "snapshot_current"

        rvbbit.execute(f"TRUNCATE {staging}")
        rvbbit.execute(f"INSERT INTO {staging} VALUES (1,'ONE'),(3,'three'),(4,'four')")
        second = rvbbit.execute(
            f"SELECT rvbbit.publish_mirror_snapshot('{target}'::regclass, "
            f"'{staging}'::regclass, ARRAY['id','value'], ARRAY['id'])"
        ).fetchone()[0]
        assert second["publication_mode"] == "snapshot_current"
        assert rvbbit.execute(f"SELECT * FROM {target} ORDER BY id").fetchall() == [
            (1, "ONE"),
            (3, "three"),
            (4, "four"),
        ]
        assert rvbbit.execute(
            "SELECT generation_semantics, history_policy, retained_generations "
            "FROM rvbbit.acceleration_storage_policy WHERE table_oid = %s::regclass",
            (target,),
        ).fetchone() == ("snapshot", "current", 1)
        assert rvbbit.execute(
            "SELECT count(*) FROM rvbbit.delete_log WHERE table_oid = %s::regclass",
            (target,),
        ).fetchone() == (0,)
    finally:
        rvbbit.execute(f"DROP TABLE IF EXISTS {staging} CASCADE")
        rvbbit.execute(f"DROP TABLE IF EXISTS {target} CASCADE")


def test_no_key_mirror_with_inbound_foreign_key_fails_actionably(rvbbit):
    suffix = uuid.uuid4().hex[:8]
    target = f"mirror_referenced_{suffix}"
    staging = f"mirror_referenced_stage_{suffix}"
    dependent = f"mirror_dependent_{suffix}"
    try:
        rvbbit.execute(f"CREATE TABLE {target} (id int PRIMARY KEY, value text) USING rvbbit")
        rvbbit.execute(f"CREATE TABLE {staging} (id int PRIMARY KEY, value text)")
        rvbbit.execute(f"CREATE TABLE {dependent} (target_id int REFERENCES {target}(id))")
        rvbbit.execute(f"INSERT INTO {staging} VALUES (1,'one')")

        with pytest.raises(Exception, match="inbound foreign keys and no declared source key"):
            rvbbit.execute(
                f"SELECT rvbbit.publish_mirror_snapshot('{target}'::regclass, "
                f"'{staging}'::regclass, ARRAY['id','value'], ARRAY[]::text[])"
            )
    finally:
        rvbbit.execute(f"DROP TABLE IF EXISTS {dependent} CASCADE")
        rvbbit.execute(f"DROP TABLE IF EXISTS {staging} CASCADE")
        rvbbit.execute(f"DROP TABLE IF EXISTS {target} CASCADE")


def test_final_lock_noop_gets_cooldown_and_default_fold_bounds(rvbbit):
    suffix = uuid.uuid4().hex[:8]
    table = f"maintenance_cooldown_{suffix}"
    try:
        rvbbit.execute("SET rvbbit.compact_vortex_layout = 'off'")
        rvbbit.execute("SET rvbbit.compact_hive_layout = 'off'")
        rvbbit.execute(f"CREATE TABLE {table} (id int PRIMARY KEY, value text) USING rvbbit")
        rvbbit.execute(f"INSERT INTO {table} VALUES (1,'one'),(2,'two')")
        rvbbit.execute(f"SELECT rvbbit.rebuild_acceleration('{table}'::regclass, false)")
        rvbbit.execute(
            f"SELECT rvbbit.set_accel_policy('{table}'::regclass, "
            "strategy => 'scheduled', min_interval_secs => 0)"
        )
        rvbbit.execute(f"UPDATE {table} SET value = upper(value)")
        rvbbit.execute(
            "INSERT INTO rvbbit.acceleration_operations("
            "table_oid,table_name,operation,status,started_at,finished_at,error) "
            "VALUES (%s::regclass,%s,'rebuild_acceleration','noop',"
            "clock_timestamp(),clock_timestamp(),'final lock busy')",
            (table, table),
        )

        policy = rvbbit.execute(
            "SELECT max_row_groups_before_rebuild,max_tombstones_before_rebuild "
            "FROM rvbbit.accel_policy_effective WHERE table_oid = %s::regclass",
            (table,),
        ).fetchone()
        assert policy[0] == 32
        assert policy[1] >= 100000

        planned = rvbbit.execute(
            "SELECT action,status,executed,reason FROM rvbbit.accel_tick(NULL,true) "
            "WHERE table_oid = %s::regclass",
            (table,),
        ).fetchone()
        assert planned[0:3] == ("skip", "skip", False)
        assert "final lock retry cooling down" in planned[3]
    finally:
        rvbbit.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
