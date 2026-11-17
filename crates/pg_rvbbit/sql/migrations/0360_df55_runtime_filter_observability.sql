-- Distinguish a DF55 runtime filter that was installed and eligible from one
-- that actually eliminated a whole Parquet row group. A zero pruning counter
-- is otherwise ambiguous on layouts with one row group per file.

CREATE OR REPLACE VIEW rvbbit.df55_execution_observatory AS
SELECT
    e.id,
    e.executed_at,
    e.backend_pid,
    e.database_name,
    e.role_name,
    e.shape_key,
    e.shape_family,
    e.candidate,
    e.executed_engine,
    e.node,
    e.elapsed_ms,
    e.rows_returned,
    e.status,
    e.route_doc #>> '{engine_observability,datafusion_version}' AS datafusion_version,
    e.route_doc #>> '{engine_observability,storage_layout}' AS storage_layout,
    (e.route_doc #>> '{engine_observability,topk,topk_operator_present}')::boolean
        AS topk_operator_present,
    (e.route_doc #>> '{engine_observability,topk,partitioned_topk_operators}')::bigint
        AS partitioned_topk_operators,
    (e.route_doc #>> '{engine_observability,topk,sort_fetch_operators}')::bigint
        AS sort_fetch_operators,
    (e.route_doc #>> '{engine_observability,topk,dynamic_filter_row_groups_pruned}')::bigint
        AS dynamic_filter_row_groups_pruned,
    (e.route_doc #>> '{engine_observability,topk,runtime_pruning_observed}')::boolean
        AS runtime_pruning_observed,
    (e.route_doc #>> '{engine_observability,metrics,pruning,row_groups_pruned_statistics,pruned}')::bigint
        AS statistics_pruned_row_groups,
    (e.route_doc #>> '{engine_observability,metrics,pruning,limit_pruned_row_groups,pruned}')::bigint
        AS limit_pruned_row_groups,
    (e.route_doc #>> '{engine_observability,metrics,pruning,page_index_rows_pruned,pruned}')::bigint
        AS page_index_rows_pruned,
    (e.route_doc #>> '{engine_observability,metrics,counters,predicate_evaluation_errors}')::bigint
        AS predicate_evaluation_errors,
    (e.route_doc #>> '{engine_observability,metrics,counters,bytes_scanned}')::bigint
        AS bytes_scanned,
    (e.route_doc #>> '{engine_observability,vortex,requested}')::boolean AS vortex_requested,
    (e.route_doc #>> '{engine_observability,vortex,plan_nodes}')::bigint AS vortex_plan_nodes,
    (e.route_doc #>> '{engine_observability,vortex,operator_visible}')::boolean
        AS vortex_operator_visible,
    e.route_doc #>> '{engine_observability,physical_plan}' AS physical_plan,
    e.route_doc #> '{engine_observability,operators}' AS operators,
    e.route_doc #> '{engine_observability,metrics}' AS metrics,
    e.route_doc #> '{engine_observability}' AS engine_observability,
    (e.route_doc #>> '{engine_observability,topk,runtime_filter_present}')::boolean
        AS runtime_filter_present,
    (e.route_doc #>> '{engine_observability,topk,runtime_row_group_pruning_eligible}')::boolean
        AS runtime_row_group_pruning_eligible
FROM rvbbit.route_executions e
WHERE e.route_doc ? 'engine_observability';

CREATE OR REPLACE VIEW rvbbit.df55_sidecar_observatory AS
SELECT
    q.id,
    q.observed_at,
    q.instance_id,
    q.hostname,
    q.node_id,
    q.mode,
    q.engine,
    q.layout,
    q.worker_id,
    q.query_hash,
    q.status,
    q.elapsed_ms,
    q.execute_ms,
    q.row_count,
    q.metadata #>> '{engine_observability,datafusion_version}' AS datafusion_version,
    (q.metadata #>> '{engine_observability,topk,topk_operator_present}')::boolean
        AS topk_operator_present,
    (q.metadata #>> '{engine_observability,topk,dynamic_filter_row_groups_pruned}')::bigint
        AS dynamic_filter_row_groups_pruned,
    (q.metadata #>> '{engine_observability,topk,runtime_pruning_observed}')::boolean
        AS runtime_pruning_observed,
    (q.metadata #>> '{engine_observability,vortex,requested}')::boolean AS vortex_requested,
    (q.metadata #>> '{engine_observability,vortex,operator_visible}')::boolean
        AS vortex_operator_visible,
    q.metadata #>> '{engine_observability,physical_plan}' AS physical_plan,
    q.metadata #> '{engine_observability,operators}' AS operators,
    q.metadata #> '{engine_observability,metrics}' AS metrics,
    q.metadata #> '{engine_observability}' AS engine_observability,
    (q.metadata #>> '{engine_observability,topk,runtime_filter_present}')::boolean
        AS runtime_filter_present,
    (q.metadata #>> '{engine_observability,topk,runtime_row_group_pruning_eligible}')::boolean
        AS runtime_row_group_pruning_eligible
FROM rvbbit.duck_sidecar_query_events q
WHERE q.metadata ? 'engine_observability';

COMMENT ON VIEW rvbbit.df55_execution_observatory IS
    'End-to-end DataFusion 55 physical plans, TopK/runtime filter eligibility and pruning counters, and Vortex evidence from routed executions.';

COMMENT ON VIEW rvbbit.df55_sidecar_observatory IS
    'Worker-level DataFusion 55 physical plans, TopK/runtime filter eligibility and pruning counters, and Vortex evidence from sidecar telemetry.';
