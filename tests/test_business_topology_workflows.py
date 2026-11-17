from __future__ import annotations

import uuid

import pytest
from psycopg import sql


def test_business_topology_automatic_scope_excludes_appliance_internals(rvbbit):
    suffix = uuid.uuid4().hex[:10]
    schema_name = f"bt_scope_{suffix}"
    business_relation = f"{schema_name}.customers"
    dlt_relation = f"{schema_name}._dlt_pipeline_state"
    run_id = None

    try:
        rvbbit.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        rvbbit.execute(
            sql.SQL("CREATE TABLE {}.customers (id bigint, name text)").format(
                sql.Identifier(schema_name)
            )
        )
        rvbbit.execute(
            sql.SQL("CREATE TABLE {}._dlt_pipeline_state (version bigint)").format(
                sql.Identifier(schema_name)
            )
        )

        allowed = rvbbit.execute(
            """
            SELECT rvbbit._business_topology_relation_allowed(%s,%s),
                   rvbbit._business_topology_relation_allowed(%s,%s),
                   rvbbit._business_topology_relation_allowed('rvbbit','settings'),
                   rvbbit._business_topology_relation_allowed('hindsight','memory_units'),
                   rvbbit._business_topology_relation_allowed('_dlt_crm_stage','contacts')
            """,
            (schema_name, "customers", schema_name, "_dlt_pipeline_state"),
        ).fetchone()
        assert allowed == (True, False, False, False, False)

        run_id = rvbbit.execute(
            """
            SELECT rvbbit.business_topology_start_workflow(
                p_schemas=>ARRAY[%s],p_sample_rows=>64,
                p_max_work_items=>10,p_max_llm_calls=>2
            )
            """,
            (schema_name,),
        ).fetchone()[0]
        relations = rvbbit.execute(
            "SELECT parameters->'relations' FROM rvbbit.business_topology_workflow_runs WHERE run_id=%s",
            (run_id,),
        ).fetchone()[0]
        assert relations == [business_relation]
        assert dlt_relation not in relations

        rvbbit.execute(
            "DELETE FROM rvbbit.business_topology_workflow_runs WHERE run_id=%s",
            (run_id,),
        )
        run_id = None
        with pytest.raises(Exception, match="appliance-internal and cannot be excavated"):
            rvbbit.execute(
                "SELECT rvbbit.business_topology_start_workflow(p_relations=>ARRAY['rvbbit.settings'])"
            )
    finally:
        if run_id is not None:
            rvbbit.execute(
                "DELETE FROM rvbbit.business_topology_workflow_runs WHERE run_id=%s",
                (run_id,),
            )
        rvbbit.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema_name))
        )


def test_business_topology_fresh_scope_rotates_domain_batches(rvbbit):
    suffix = uuid.uuid4().hex[:10]
    schemas = [f"bt_domain_a_{suffix}", f"bt_domain_b_{suffix}"]
    run_ids: list[uuid.UUID] = []
    try:
        for schema_name in schemas:
            rvbbit.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
            for index in range(30):
                rvbbit.execute(
                    sql.SQL("CREATE TABLE {}.{} (id bigint, label text)").format(
                        sql.Identifier(schema_name),
                        sql.Identifier(f"business_{index:02d}"),
                    )
                )

        first = rvbbit.execute(
            """
            SELECT rvbbit.business_topology_start_workflow(
                p_sample_rows=>64,p_max_relations=>100,
                p_max_work_items=>10,p_max_llm_calls=>2
            )
            """
        ).fetchone()[0]
        run_ids.append(first)
        first_params = rvbbit.execute(
            "SELECT parameters FROM rvbbit.business_topology_workflow_runs WHERE run_id=%s",
            (first,),
        ).fetchone()[0]
        assert first_params["scope_mode"] == "automatic_domain_batch"
        assert first_params["max_relations"] == 24
        assert len(first_params["relations"]) <= 24
        assert {
            relation.rsplit(".", 1)[0] for relation in first_params["relations"]
        } == {first_params["scope_schema"]}

        rvbbit.execute(
            """
            UPDATE rvbbit.business_topology_workflow_runs
               SET status='completed',phase='completed',finished_at=clock_timestamp()
             WHERE run_id=%s
            """,
            (first,),
        )
        second = rvbbit.execute(
            """
            SELECT rvbbit.business_topology_start_workflow(
                p_sample_rows=>64,p_max_relations=>100,
                p_max_work_items=>10,p_max_llm_calls=>2
            )
            """
        ).fetchone()[0]
        run_ids.append(second)
        second_params = rvbbit.execute(
            "SELECT parameters FROM rvbbit.business_topology_workflow_runs WHERE run_id=%s",
            (second,),
        ).fetchone()[0]
        assert second_params["scope_schema"] != first_params["scope_schema"]
        assert {
            relation.rsplit(".", 1)[0] for relation in second_params["relations"]
        } == {second_params["scope_schema"]}
    finally:
        if run_ids:
            rvbbit.execute(
                "DELETE FROM rvbbit.business_topology_workflow_runs WHERE run_id=ANY(%s)",
                (run_ids,),
            )
        for schema_name in schemas:
            rvbbit.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )


def test_business_topology_workflow_is_sql_leased_idempotent_and_observable(rvbbit):
    suffix = uuid.uuid4().hex[:10]
    table_name = f"bt_workflow_{suffix}"
    relation = f"public.{table_name}"
    worker_id = f"pytest-topology-{suffix}"
    run_ids: list[uuid.UUID] = []

    try:
        rvbbit.execute(
            sql.SQL("CREATE TABLE {} (id bigint, label text)").format(
                sql.Identifier(table_name)
            )
        )
        first = rvbbit.execute(
            """
            SELECT rvbbit.business_topology_start_workflow(
                p_relations=>ARRAY[%s],p_sample_rows=>64,
                p_max_work_items=>10,p_max_llm_calls=>2,p_run_name=>%s
            )
            """,
            (relation, f"Workflow {suffix}"),
        ).fetchone()[0]
        run_ids.append(first)
        repeated = rvbbit.execute(
            "SELECT rvbbit.business_topology_start_workflow(p_relations=>ARRAY[%s])",
            (relation,),
        ).fetchone()[0]
        assert repeated == first

        rvbbit.execute(
            "SELECT rvbbit.business_topology_register_workflow_worker(%s,'pytest-v1')",
            (worker_id,),
        )
        claimed = rvbbit.execute(
            "SELECT run_id,parameters FROM rvbbit.business_topology_claim_workflow(%s,300)",
            (worker_id,),
        ).fetchone()
        assert claimed[0] == first
        assert claimed[1]["relations"] == [relation]

        assert rvbbit.execute(
            """
            SELECT rvbbit.business_topology_update_workflow(
                %s,%s,'planning',
                '{"population_count":7,"work_item_count":11}'::jsonb,300
            )
            """,
            (first, worker_id),
        ).fetchone()[0]
        status = rvbbit.execute(
            """
            SELECT status,phase,population_count,work_item_count,any_worker_online
              FROM rvbbit.business_topology_workflow_status WHERE run_id=%s
            """,
            (first,),
        ).fetchone()
        assert status == ("running", "planning", 7, 11, True)

        assert rvbbit.execute(
            "SELECT rvbbit.business_topology_cancel_workflow(%s)", (first,)
        ).fetchone()[0] == "cancel_requested"
        rvbbit.execute(
            "SELECT rvbbit.business_topology_fail_workflow(%s,%s,'cancelled','{}'::jsonb)",
            (first, worker_id),
        )
        assert rvbbit.execute(
            "SELECT status FROM rvbbit.business_topology_workflow_runs WHERE run_id=%s",
            (first,),
        ).fetchone()[0] == "cancelled"

        second = rvbbit.execute(
            """
            SELECT rvbbit.business_topology_start_workflow(
                p_relations=>ARRAY[%s],p_sample_rows=>64,
                p_max_work_items=>10,p_max_llm_calls=>2,p_run_name=>'Completion test'
            )
            """,
            (relation,),
        ).fetchone()[0]
        run_ids.append(second)
        assert second != first
        assert rvbbit.execute(
            "SELECT run_id FROM rvbbit.business_topology_claim_workflow(%s,300)",
            (worker_id,),
        ).fetchone()[0] == second
        rvbbit.execute(
            """
            SELECT rvbbit.business_topology_complete_workflow(
                %s,%s,
                '{"schema_version":"rvbbit.business-topology.workflow-result.v1",\
                  "bundles_staged":3}'::jsonb
            )
            """,
            (second, worker_id),
        )
        completed = rvbbit.execute(
            """
            SELECT status,phase,bundles_staged,worker_id,lease_expires_at
              FROM rvbbit.business_topology_workflow_status WHERE run_id=%s
            """,
            (second,),
        ).fetchone()
        assert completed == ("completed", "completed", 3, None, None)
    finally:
        if run_ids:
            rvbbit.execute(
                "DELETE FROM rvbbit.business_topology_workflow_runs WHERE run_id=ANY(%s)",
                (run_ids,),
            )
        rvbbit.execute(
            "DELETE FROM rvbbit.business_topology_workflow_workers WHERE worker_id=%s",
            (worker_id,),
        )
        rvbbit.execute(
            sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(table_name))
        )


def test_targeted_schema_excavation_queues_behind_the_active_worker(rvbbit):
    suffix = uuid.uuid4().hex[:10]
    first_schema = f"bt_queue_a_{suffix}"
    second_schema = f"bt_queue_b_{suffix}"
    worker_id = f"pytest-queue-{suffix}"
    run_ids: list[uuid.UUID] = []
    try:
        for schema_name in (first_schema, second_schema):
            rvbbit.execute(
                sql.SQL("CREATE SCHEMA {}; CREATE TABLE {}.events (id bigint)").format(
                    sql.Identifier(schema_name), sql.Identifier(schema_name)
                )
            )
        first = rvbbit.execute(
            "SELECT rvbbit.business_topology_queue_schema(%s)", (first_schema,)
        ).fetchone()[0]
        run_ids.append(first)
        claimed = rvbbit.execute(
            "SELECT run_id FROM rvbbit.business_topology_claim_workflow(%s,300)",
            (worker_id,),
        ).fetchone()[0]
        assert claimed == first

        second = rvbbit.execute(
            "SELECT rvbbit.business_topology_queue_schema(%s)", (second_schema,)
        ).fetchone()[0]
        run_ids.append(second)
        assert second != first
        assert rvbbit.execute(
            "SELECT count(*) FROM rvbbit.business_topology_claim_workflow(%s,300)",
            (f"{worker_id}-other",),
        ).fetchone()[0] == 0
        assert rvbbit.execute(
            "SELECT rvbbit.business_topology_queue_schema(%s)", (second_schema,)
        ).fetchone()[0] == second
    finally:
        if run_ids:
            rvbbit.execute(
                "DELETE FROM rvbbit.business_topology_workflow_runs WHERE run_id=ANY(%s)",
                (run_ids,),
            )
        for schema_name in (first_schema, second_schema):
            rvbbit.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )


def test_expired_cancel_request_is_reconciled_before_next_claim(rvbbit):
    suffix = uuid.uuid4().hex[:10]
    first_schema = f"bt_cancel_a_{suffix}"
    second_schema = f"bt_cancel_b_{suffix}"
    old_worker = f"pytest-old-worker-{suffix}"
    new_worker = f"pytest-new-worker-{suffix}"
    run_ids: list[uuid.UUID] = []
    try:
        for schema_name in (first_schema, second_schema):
            rvbbit.execute(
                sql.SQL("CREATE SCHEMA {}; CREATE TABLE {}.events (id bigint)").format(
                    sql.Identifier(schema_name), sql.Identifier(schema_name)
                )
            )
        first = rvbbit.execute(
            "SELECT rvbbit.business_topology_queue_schema(%s)", (first_schema,)
        ).fetchone()[0]
        run_ids.append(first)
        assert rvbbit.execute(
            "SELECT run_id FROM rvbbit.business_topology_claim_workflow(%s,300)",
            (old_worker,),
        ).fetchone()[0] == first
        assert rvbbit.execute(
            "SELECT rvbbit.business_topology_cancel_workflow(%s)", (first,)
        ).fetchone()[0] == "cancel_requested"
        rvbbit.execute(
            """
            UPDATE rvbbit.business_topology_workflow_runs
               SET lease_expires_at=clock_timestamp()-interval '1 second'
             WHERE run_id=%s
            """,
            (first,),
        )

        second = rvbbit.execute(
            "SELECT rvbbit.business_topology_queue_schema(%s)", (second_schema,)
        ).fetchone()[0]
        run_ids.append(second)
        claimed = rvbbit.execute(
            "SELECT run_id FROM rvbbit.business_topology_claim_workflow(%s,300)",
            (new_worker,),
        ).fetchone()[0]
        assert claimed == second
        reconciled = rvbbit.execute(
            """
            SELECT status,phase,worker_id,lease_expires_at,
                   progress->>'cancel_reconciled_reason'
              FROM rvbbit.business_topology_workflow_runs WHERE run_id=%s
            """,
            (first,),
        ).fetchone()
        assert reconciled == (
            "cancelled", "cancelled", None, None, "worker_lease_expired",
        )
    finally:
        if run_ids:
            rvbbit.execute(
                "DELETE FROM rvbbit.business_topology_workflow_runs WHERE run_id=ANY(%s)",
                (run_ids,),
            )
        for schema_name in (first_schema, second_schema):
            rvbbit.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema_name)
                )
            )
