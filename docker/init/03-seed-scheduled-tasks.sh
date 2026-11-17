#!/bin/bash
# First-boot Scheduler seed for a fresh monorepo/appliance database volume. The
# normal hosted bootstrap repairs this inventory on existing volumes; this early
# seed makes a fresh database useful immediately.

set -uo pipefail

CRON_DB="${RVBBIT_CRON_DATABASE:-postgres}"
TARGET_DB="${POSTGRES_DB:-rvbbit}"

echo "rvbbit: seeding hosted Scheduled Tasks in ${CRON_DB} for ${TARGET_DB}"

if psql -X -v ON_ERROR_STOP=1 \
    --username "${POSTGRES_USER:-postgres}" \
    --dbname "$CRON_DB" \
    -v target_db="$TARGET_DB" <<'SQL'
CREATE EXTENSION IF NOT EXISTS pg_cron;

SELECT cron.schedule_in_database(
    'rvbbit_calliope_dreams',
    '0 3 * * *',
    'SELECT rvbbit.calliope_dream_enqueue(''cron'',''calliope@system'',false);',
    :'target_db'
)
WHERE NOT EXISTS (
    SELECT 1 FROM cron.job
     WHERE jobname='rvbbit_calliope_dreams'
       AND database=:'target_db'
);

SELECT cron.schedule_in_database('rvbbit_catalog_refresh','0 3 * * *','CALL rvbbit.catalog_crawl_run();',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_catalog_refresh' AND database=:'target_db');
SELECT cron.schedule_in_database('rvbbit_catalog_registration_crawl','*/2 * * * *','SELECT rvbbit.catalog_crawl_requests_tick();',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_catalog_registration_crawl' AND database=:'target_db');
UPDATE cron.job SET schedule='*/2 * * * *',command='SELECT rvbbit.catalog_crawl_requests_tick();',active=true
 WHERE jobname='rvbbit_catalog_registration_crawl' AND database=:'target_db';
-- rvbbit_business_context_steward: retired by docs/MEANING_PLAN.md (the tick was already disabled on live installs)
SELECT cron.unschedule(jobid) FROM cron.job WHERE jobname='rvbbit_business_context_steward' AND database=:'target_db';
-- Retired by docs/MEANING_PLAN.md §5 (2026-09-19): the excavator, the business-context
-- refresh lanes that enqueued it, the semantic watch and the knowledge loop. Meaning
-- (rvbbit_meaning_sweep + the in-process narrative worker) replaces them; the old
-- projection stays readable but no longer refreshes.
SELECT cron.unschedule(jobid) FROM cron.job
 WHERE database=:'target_db'
   AND jobname IN ('rvbbit_business_context_catalog_repair','rvbbit_business_context_data_refresh',
                   'rvbbit_business_semantic_watch_tick','rvbbit_knowledge_loop',
                   'rvbbit_topology_nightly_sweep','rvbbit_topology_sweep_continue');

-- CALL provides the transaction boundary between the route-key merge and its
-- retained RVBBIT delta publication; do not rewrite this as SELECT tick().
SELECT cron.schedule_in_database('rvbbit_work_graph_snapshot','*/15 * * * *','CALL rvbbit.work_graph_snapshot_run();',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_work_graph_snapshot' AND database=:'target_db');
-- Keep label inference off the snapshot transaction and the initial Floor request.
SELECT cron.schedule_in_database('rvbbit_floor_presentations','2-59/15 * * * *','CALL rvbbit.floor_presentations_warm();',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_floor_presentations' AND database=:'target_db');
UPDATE cron.job SET schedule='2-59/15 * * * *',command='CALL rvbbit.floor_presentations_warm();',active=true
 WHERE jobname='rvbbit_floor_presentations' AND database=:'target_db';

UPDATE cron.job SET schedule='*/15 * * * *',command='CALL rvbbit.work_graph_snapshot_run();',active=true
 WHERE jobname='rvbbit_work_graph_snapshot' AND database=:'target_db';
-- rvbbit_business_context_safety: retired by docs/MEANING_PLAN.md (the tick was already disabled on live installs)
SELECT cron.unschedule(jobid) FROM cron.job WHERE jobname='rvbbit_business_context_safety' AND database=:'target_db';
-- Five cheap freshness workers maintain canonical Parquet supply. Five
-- independent unified-layout workers maintain automatic Vortex plus accepted
-- cluster/Hive targets. Each slot commits between tables, steals unlocked
-- work, and exits immediately when no eligible candidate exists. The shared
-- heavy-maintenance pool remains the real bound on expensive concurrency.
SELECT cron.unschedule(jobid)
  FROM cron.job
 WHERE database=:'target_db'
   AND jobname IN ('rvbbit_olap_autopilot','rvbbit_accel_tick',
                   'rvbbit_variant_tick','rvbbit_workload_layout_tick',
                   'rvbbit_layout_tick');
SELECT cron.unschedule(jobid)
  FROM cron.job
 WHERE database=:'target_db'
   AND (jobname ~ '^rvbbit_accel_tick_worker_[0-9]+$'
        OR jobname ~ '^rvbbit_workload_layout_tick_worker_[0-9]+$'
        OR jobname ~ '^rvbbit_layout_tick_worker_[0-9]+$')
   AND jobname !~ '^rvbbit_(accel_tick|layout_tick)_worker_[1-5]$';
SELECT cron.schedule_in_database(
           format('rvbbit_accel_tick_worker_%s',slot),
           '* * * * *',
           format('CALL rvbbit.accel_tick_worker_pass(%s, 1, 4, 1);',slot),
           :'target_db')
  FROM generate_series(1,1) AS slot
 WHERE NOT EXISTS (
       SELECT 1 FROM cron.job
        WHERE jobname=format('rvbbit_accel_tick_worker_%s',slot)
          AND database=:'target_db');
SELECT cron.schedule_in_database(
           format('rvbbit_layout_tick_worker_%s',slot),
           '* * * * *',
           format('CALL rvbbit.layout_tick_worker_pass(%s, 5, 4);',slot),
           :'target_db')
  FROM generate_series(1,5) AS slot
 WHERE NOT EXISTS (
       SELECT 1 FROM cron.job
        WHERE jobname=format('rvbbit_layout_tick_worker_%s',slot)
          AND database=:'target_db');
UPDATE cron.job
   SET schedule='* * * * *',
       command=format('CALL rvbbit.accel_tick_worker_pass(%s, 1, 4, 1);',
                      substring(jobname from '([0-9]+)$')::integer),
       active=true
 WHERE database=:'target_db'
   AND jobname = 'rvbbit_accel_tick_worker_1';
-- one freshness worker is enough once evidence gates the folds (0446); retire extra slots
SELECT cron.unschedule(jobid) FROM cron.job
 WHERE database=:'target_db' AND jobname ~ '^rvbbit_accel_tick_worker_[2-9]$';
UPDATE cron.job
   SET schedule='* * * * *',
       command=format('CALL rvbbit.layout_tick_worker_pass(%s, 5, 4);',
                      substring(jobname from '([0-9]+)$')::integer),
       active=true
 WHERE database=:'target_db'
   AND jobname ~ '^rvbbit_layout_tick_worker_[1-5]$';
SELECT cron.schedule_in_database('rvbbit_accel_observer','7 * * * *','SELECT rvbbit.accel_autopilot_observe(''scheduler'');',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_accel_observer' AND database=:'target_db');
SELECT cron.schedule_in_database('rvbbit_accel_autopilot_act','37 * * * *','SELECT rvbbit.accel_autopilot_act(''scheduler'');',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_accel_autopilot_act' AND database=:'target_db');
SELECT cron.schedule_in_database('rvbbit-maintain','*/15 * * * *','SELECT rvbbit.maintain();',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit-maintain' AND database=:'target_db');
SELECT cron.schedule_in_database('rvbbit-storage-maintain','25 * * * *','SELECT rvbbit.maintain(storage_tables => 1);',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit-storage-maintain' AND database=:'target_db');
SELECT cron.schedule_in_database('rvbbit-postgres-diagnostics','11 * * * *','SELECT rvbbit.collect_postgres_diagnostics();',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit-postgres-diagnostics' AND database=:'target_db');
UPDATE cron.job SET schedule='11 * * * *',command='SELECT rvbbit.collect_postgres_diagnostics();',active=true
 WHERE jobname='rvbbit-postgres-diagnostics' AND database=:'target_db';
SELECT cron.schedule_in_database('rvbbit_reap_orphaned_files','13 * * * *','SELECT rvbbit.reap_orphaned_files(interval ''1 hour'', 100000);',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_reap_orphaned_files' AND database=:'target_db');
UPDATE cron.job SET schedule='13 * * * *',command='SELECT rvbbit.reap_orphaned_files(interval ''1 hour'', 100000);',active=true
 WHERE jobname='rvbbit_reap_orphaned_files' AND database=:'target_db';
SELECT cron.schedule_in_database('rvbbit_materialize_all','0 * * * *','SELECT rvbbit.materialize_all_metrics();',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_materialize_all' AND database=:'target_db');
SELECT cron.schedule_in_database('rvbbit_refresh_cubes','0 */2 * * *','CALL rvbbit.refresh_all_cubes();',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_refresh_cubes' AND database=:'target_db');
SELECT cron.schedule_in_database('rvbbit_route_optimize','0 5 * * *','SELECT rvbbit.route_optimize_auto(20, 600, 3);',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_route_optimize' AND database=:'target_db');
SELECT cron.schedule_in_database('rvbbit_brain_sync','0 2 * * *','CALL rvbbit.brain_update_drain(''auto'');',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_brain_sync' AND database=:'target_db');
-- find (docs/FIND_PLAN.md): nightly snapshot rebuild (dense tier if stale, relation refs,
-- usage, lineage) and the budgeted literal-values index sweep. Crawls also refresh the
-- snapshots through the catalog_runs trigger; these keep a quiet install fresh.
SELECT cron.schedule_in_database('rvbbit_find_refresh','25 4 * * *','SELECT rvbbit.find_refresh(''db_catalog'', true, ''cron'');',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_find_refresh' AND database=:'target_db');
UPDATE cron.job SET schedule='25 4 * * *',command='SELECT rvbbit.find_refresh(''db_catalog'', true, ''cron'');',active=true
 WHERE jobname='rvbbit_find_refresh' AND database=:'target_db';
SELECT cron.schedule_in_database('rvbbit_value_index_sweep','*/10 * * * *','CALL rvbbit.value_index_sweep(120);',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_value_index_sweep' AND database=:'target_db');
UPDATE cron.job SET schedule='*/10 * * * *',command='CALL rvbbit.value_index_sweep(120);',active=true
 WHERE jobname='rvbbit_value_index_sweep' AND database=:'target_db';
SELECT cron.schedule_in_database('rvbbit_meaning_sweep','*/10 * * * *','CALL rvbbit.meaning_sweep(120);',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_meaning_sweep' AND database=:'target_db');
UPDATE cron.job SET schedule='*/10 * * * *',command='CALL rvbbit.meaning_sweep(120);',active=true
 WHERE jobname='rvbbit_meaning_sweep' AND database=:'target_db';
-- rvbbit_meaning_desire_path: register dossier candidates as implied metrics, propose the used ones, retire the silent ones (0445)
SELECT cron.schedule_in_database('rvbbit_meaning_desire_path','50 4 * * *','SELECT rvbbit.meaning_desire_path_tick();',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_meaning_desire_path' AND database=:'target_db');
UPDATE cron.job SET schedule='50 4 * * *',command='SELECT rvbbit.meaning_desire_path_tick();',active=true
 WHERE jobname='rvbbit_meaning_desire_path' AND database=:'target_db';
SELECT cron.schedule_in_database('rvbbit_brain_enrich','*/5 * * * *','CALL rvbbit.brain_enrich_drain(NULL, 20, 0, 270, ''cron'');',:'target_db')
WHERE NOT EXISTS (SELECT 1 FROM cron.job WHERE jobname='rvbbit_brain_enrich' AND database=:'target_db');

UPDATE cron.job SET active=true
 WHERE database=:'target_db' AND jobname LIKE 'rvbbit%'
   AND jobname <> 'rvbbit_sync' AND jobname NOT LIKE 'rvbbit_alert_%'
   AND command !~* 'rvbbit\.(run_sync|alert_sweep|alert_worker_tick)';
SQL
then
  echo "rvbbit: hosted Scheduled Tasks are present and enabled"
else
  echo "rvbbit: could not seed hosted Scheduled Tasks; add them from DataRabbit Scheduler"
fi

exit 0
