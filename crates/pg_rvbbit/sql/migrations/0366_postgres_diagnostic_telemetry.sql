-- 0366: Deterministic PostgreSQL diagnostics and planner-validated index advice.
--
-- Keep generic PostgreSQL health below the model layer.  Collection records a
-- bounded, PII-free snapshot plus stable findings.  When pg_stat_statements,
-- HypoPG, and EXPLAIN (GENERIC_PLAN) are available, slow SELECTs are planned,
-- candidate indexes are derived from sequential-scan filters, and only indexes
-- the planner actually chooses with a material cost reduction are retained.
-- The inspected statement is never executed and its text is never persisted.

CREATE TABLE IF NOT EXISTS rvbbit.postgres_diagnostic_runs (
    run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    status text NOT NULL DEFAULT 'running',
    server_version_num integer NOT NULL,
    stats_reset timestamptz,
    pgss_reset timestamptz,
    capabilities jsonb NOT NULL DEFAULT '{}'::jsonb,
    snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    error text,
    CONSTRAINT postgres_diagnostic_runs_status_check
        CHECK (status IN ('running','ok','degraded','failed')),
    CONSTRAINT postgres_diagnostic_runs_json_check
        CHECK (jsonb_typeof(capabilities)='object'
           AND jsonb_typeof(snapshot)='object'
           AND jsonb_typeof(summary)='object')
);
CREATE INDEX IF NOT EXISTS postgres_diagnostic_runs_finished_idx
    ON rvbbit.postgres_diagnostic_runs(finished_at DESC,run_id DESC);

CREATE TABLE IF NOT EXISTS rvbbit.postgres_diagnostic_findings (
    run_id bigint NOT NULL
        REFERENCES rvbbit.postgres_diagnostic_runs(run_id) ON DELETE CASCADE,
    finding_key text NOT NULL,
    object_class text NOT NULL,
    object_identity text NOT NULL,
    severity text NOT NULL,
    exactness text NOT NULL,
    summary text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    recommendation text,
    safety jsonb NOT NULL DEFAULT '[]'::jsonb,
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (run_id,finding_key,object_identity),
    CONSTRAINT postgres_diagnostic_findings_severity_check
        CHECK (severity IN ('info','warn','critical')),
    CONSTRAINT postgres_diagnostic_findings_exactness_check
        CHECK (exactness IN ('scraped','cumulative','planner_estimate','unavailable')),
    CONSTRAINT postgres_diagnostic_findings_json_check
        CHECK (jsonb_typeof(evidence)='object' AND jsonb_typeof(safety)='array')
);
CREATE INDEX IF NOT EXISTS postgres_diagnostic_findings_key_idx
    ON rvbbit.postgres_diagnostic_findings(finding_key,object_identity,run_id DESC);

CREATE TABLE IF NOT EXISTS rvbbit.postgres_index_advice (
    run_id bigint NOT NULL
        REFERENCES rvbbit.postgres_diagnostic_runs(run_id) ON DELETE CASCADE,
    candidate_key text NOT NULL,
    table_oid oid NOT NULL,
    table_name text NOT NULL,
    columns text[] NOT NULL,
    query_id bigint NOT NULL,
    calls bigint NOT NULL,
    total_exec_time_ms double precision NOT NULL,
    db_time_pct double precision NOT NULL,
    cost_before double precision NOT NULL,
    cost_after double precision NOT NULL,
    improvement_pct double precision NOT NULL,
    estimated_bytes bigint,
    confidence numeric(5,4) NOT NULL DEFAULT 0.6,
    exactness text NOT NULL DEFAULT 'planner_estimate',
    index_ddl text NOT NULL,
    caveats jsonb NOT NULL DEFAULT '[]'::jsonb,
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (run_id,candidate_key),
    CONSTRAINT postgres_index_advice_columns_check
        CHECK (cardinality(columns) BETWEEN 1 AND 4),
    CONSTRAINT postgres_index_advice_numbers_check
        CHECK (calls>=0 AND total_exec_time_ms>=0 AND db_time_pct>=0
           AND cost_before>0 AND cost_after>=0 AND improvement_pct>=0
           AND confidence BETWEEN 0 AND 1),
    CONSTRAINT postgres_index_advice_exactness_check
        CHECK (exactness='planner_estimate'),
    CONSTRAINT postgres_index_advice_caveats_check
        CHECK (jsonb_typeof(caveats)='array')
);
CREATE INDEX IF NOT EXISTS postgres_index_advice_table_idx
    ON rvbbit.postgres_index_advice(table_oid,improvement_pct DESC,run_id DESC);

COMMENT ON TABLE rvbbit.postgres_diagnostic_runs IS
    'Bounded PostgreSQL health snapshots. Counters retain their reset provenance; raw query text is never stored.';
COMMENT ON TABLE rvbbit.postgres_diagnostic_findings IS
    'Deterministic PostgreSQL findings with stable identities, explicit exactness, evidence, and structured safety caveats.';
COMMENT ON TABLE rvbbit.postgres_index_advice IS
    'Planner-confirmed hypothetical index candidates. Suggestions are estimates, never executed queries or automatically built indexes.';

CREATE OR REPLACE FUNCTION rvbbit._record_postgres_finding(
    requested_run_id bigint,
    requested_finding_key text,
    requested_object_class text,
    requested_object_identity text,
    requested_severity text,
    requested_exactness text,
    requested_summary text,
    requested_evidence jsonb DEFAULT '{}'::jsonb,
    requested_recommendation text DEFAULT NULL,
    requested_safety jsonb DEFAULT '[]'::jsonb
) RETURNS void
LANGUAGE sql VOLATILE
SET search_path = pg_catalog, rvbbit
AS $fn$
    INSERT INTO rvbbit.postgres_diagnostic_findings(
        run_id,finding_key,object_class,object_identity,severity,exactness,
        summary,evidence,recommendation,safety
    ) VALUES (
        requested_run_id,requested_finding_key,requested_object_class,
        requested_object_identity,requested_severity,requested_exactness,
        requested_summary,coalesce(requested_evidence,'{}'::jsonb),
        requested_recommendation,coalesce(requested_safety,'[]'::jsonb)
    )
    ON CONFLICT (run_id,finding_key,object_identity) DO UPDATE SET
        severity=EXCLUDED.severity,
        exactness=EXCLUDED.exactness,
        summary=EXCLUDED.summary,
        evidence=EXCLUDED.evidence,
        recommendation=EXCLUDED.recommendation,
        safety=EXCLUDED.safety,
        observed_at=clock_timestamp()
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.collect_postgres_diagnostics(
    include_index_advice boolean DEFAULT true,
    max_statements integer DEFAULT 20,
    min_total_exec_ms double precision DEFAULT 1000,
    min_index_improvement double precision DEFAULT 0.5
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_run_id bigint;
    v_version integer := current_setting('server_version_num')::integer;
    v_pgss_schema text;
    v_hypopg_schema text;
    v_stats_reset timestamptz;
    v_pgss_reset timestamptz;
    v_snapshot jsonb := '{}'::jsonb;
    v_capabilities jsonb;
    v_status text := 'ok';
    v_error text;
    v_count bigint;
    v_max_seconds double precision;
    v_connections integer;
    v_max_connections integer;
    v_row record;
    v_q record;
    v_node jsonb;
    v_match text[];
    v_plan jsonb;
    v_after_plan jsonb;
    v_sql text;
    v_filter text;
    v_schema text;
    v_table text;
    v_relation oid;
    v_columns text[];
    v_column text;
    v_column_list text;
    v_candidate_key text;
    v_index_ddl text;
    v_cost_before double precision;
    v_cost_after double precision;
    v_improvement double precision;
    v_hypo_oid oid;
    v_hypo_name text;
    v_estimated_bytes bigint;
    v_uses_index boolean;
    v_stale_stats boolean;
    v_write_heavy boolean;
    v_caveats jsonb;
    v_findings integer;
    v_candidates integer;
    v_queries_considered integer := 0;
    v_queries_planned integer := 0;
    v_candidates_tested integer := 0;
    v_stale_skipped integer := 0;
BEGIN
    IF NOT pg_try_advisory_xact_lock(hashtextextended('rvbbit.collect_postgres_diagnostics',0)) THEN
        RETURN jsonb_build_object('status','busy','message','another diagnostic collection is running');
    END IF;

    max_statements := greatest(1,least(coalesce(max_statements,20),100));
    min_total_exec_ms := greatest(0,coalesce(min_total_exec_ms,1000));
    min_index_improvement := greatest(0.05,least(coalesce(min_index_improvement,0.5),0.95));

    SELECT n.nspname INTO v_pgss_schema
      FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
     WHERE e.extname='pg_stat_statements';
    SELECT n.nspname INTO v_hypopg_schema
      FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
     WHERE e.extname='hypopg';
    SELECT stats_reset INTO v_stats_reset
      FROM pg_stat_database WHERE datname=current_database();

    IF v_pgss_schema IS NOT NULL THEN
        BEGIN
            EXECUTE format(
                'SELECT stats_reset FROM %I.pg_stat_statements_info',v_pgss_schema
            ) INTO v_pgss_reset;
        EXCEPTION WHEN OTHERS THEN
            v_pgss_reset := NULL;
        END;
    END IF;

    v_capabilities := jsonb_build_object(
        'pg_stat_statements',v_pgss_schema IS NOT NULL,
        'hypopg',v_hypopg_schema IS NOT NULL,
        'generic_plan',v_version>=160000,
        'index_advice_requested',include_index_advice
    );

    SELECT count(*) FILTER (WHERE backend_type='client backend')::integer,
           current_setting('max_connections')::integer
      INTO v_connections,v_max_connections
      FROM pg_stat_activity;

    SELECT jsonb_build_object(
        'database',current_database(),
        'database_size_bytes',pg_database_size(current_database()),
        'connections',v_connections,
        'max_connections',v_max_connections,
        'xact_commit',coalesce(d.xact_commit,0),
        'xact_rollback',coalesce(d.xact_rollback,0),
        'blocks_read',coalesce(d.blks_read,0),
        'blocks_hit',coalesce(d.blks_hit,0),
        'deadlocks',coalesce(d.deadlocks,0),
        'temp_bytes',coalesce(d.temp_bytes,0),
        'checksum_failures',coalesce(d.checksum_failures,0),
        'stats_reset',d.stats_reset,
        'pgss_reset',v_pgss_reset
    ) INTO v_snapshot
      FROM pg_stat_database d WHERE d.datname=current_database();

    INSERT INTO rvbbit.postgres_diagnostic_runs(
        server_version_num,stats_reset,pgss_reset,capabilities,snapshot
    ) VALUES (v_version,v_stats_reset,v_pgss_reset,v_capabilities,v_snapshot)
    RETURNING run_id INTO v_run_id;

    BEGIN
        IF current_setting('autovacuum')='off' THEN
            PERFORM rvbbit._record_postgres_finding(
                v_run_id,'autovacuum_off','cluster','cluster','critical','scraped',
                'Autovacuum is disabled for the cluster.',
                jsonb_build_object('autovacuum','off'),
                'Enable autovacuum; manual vacuum scheduling must also prove wraparound coverage.',
                jsonb_build_array(jsonb_build_object(
                    'id','vacuum-before-enable','action','enable_autovacuum','kind','precondition',
                    'text','Check for near-wraparound databases and vacuum them before relying on normal cadence.'
                ))
            );
        END IF;

        IF current_setting('fsync')='off' THEN
            PERFORM rvbbit._record_postgres_finding(
                v_run_id,'fsync_off','cluster','cluster','critical','scraped',
                'fsync is disabled; a crash can leave the database corrupt.',
                jsonb_build_object('fsync','off'),
                'Restore fsync and restart only under a reviewed durability procedure.'
            );
        END IF;

        IF current_setting('full_page_writes')='off' THEN
            PERFORM rvbbit._record_postgres_finding(
                v_run_id,'full_page_writes_off','cluster','cluster','critical','scraped',
                'full_page_writes is disabled; crash recovery can leave torn pages.',
                jsonb_build_object('full_page_writes','off'),
                'Enable full_page_writes unless an independently verified storage guarantee makes it unnecessary.'
            );
        END IF;

        IF current_setting('data_checksums')='off' THEN
            PERFORM rvbbit._record_postgres_finding(
                v_run_id,'checksums_disabled','cluster','cluster','info','scraped',
                'Data checksums are disabled, so damaged pages may not be detected on read.',
                jsonb_build_object('data_checksums','off'),
                'Consider enabling checksums during a planned maintenance window.'
            );
        END IF;

        IF v_connections::numeric / greatest(v_max_connections,1) >= 0.80 THEN
            PERFORM rvbbit._record_postgres_finding(
                v_run_id,'connection_saturation','cluster','cluster','warn','scraped',
                format('%s of %s connections are in use.',v_connections,v_max_connections),
                jsonb_build_object(
                    'connections',v_connections,'max_connections',v_max_connections,
                    'utilization',round(v_connections::numeric/greatest(v_max_connections,1),4)
                ),
                'Inspect pool sizing and idle sessions before raising max_connections.'
            );
        END IF;

        SELECT count(*)::bigint,
               coalesce(max(extract(epoch FROM clock_timestamp()-xact_start)),0)
          INTO v_count,v_max_seconds
          FROM pg_stat_activity
         WHERE backend_type='client backend' AND state='idle in transaction';
        IF v_count>0 THEN
            PERFORM rvbbit._record_postgres_finding(
                v_run_id,'idle_in_transaction','cluster','cluster','warn','scraped',
                format('%s session(s) are idle in an open transaction.',v_count),
                jsonb_build_object('sessions',v_count,'oldest_seconds',round(v_max_seconds::numeric,3)),
                'Find the owning application and end or time-bound idle transactions.'
            );
        END IF;

        SELECT count(*)::bigint,
               coalesce(max(extract(epoch FROM clock_timestamp()-query_start)),0)
          INTO v_count,v_max_seconds
          FROM pg_stat_activity
         WHERE backend_type='client backend'
           AND cardinality(pg_blocking_pids(pid))>0;
        IF v_count>0 THEN
            PERFORM rvbbit._record_postgres_finding(
                v_run_id,'blocking_chains','cluster','cluster','critical','scraped',
                format('%s session(s) are waiting behind another backend.',v_count),
                jsonb_build_object('blocked_sessions',v_count,'longest_wait_seconds',round(v_max_seconds::numeric,3)),
                'Inspect pg_blocking_pids and transaction ownership before cancelling anything.',
                jsonb_build_array(jsonb_build_object(
                    'id','identify-blocker-owner','action','terminate_backend','kind','precondition',
                    'text','Identify the root blocker, owner, transaction, and rollback impact before terminating a backend.'
                ))
            );
        END IF;

        FOR v_row IN
            SELECT d.datname,age(d.datfrozenxid)::bigint AS xid_age,
                   current_setting('autovacuum_freeze_max_age')::bigint AS freeze_max
              FROM pg_database d WHERE d.datallowconn
               AND age(d.datfrozenxid)>=greatest(
                    (current_setting('autovacuum_freeze_max_age')::numeric*0.75)::bigint,
                    100000000::bigint)
        LOOP
            PERFORM rvbbit._record_postgres_finding(
                v_run_id,'txid_wraparound','database',v_row.datname,
                CASE WHEN v_row.xid_age>=1800000000 THEN 'critical' ELSE 'warn' END,
                'scraped',
                format('Database %I has transaction ID age %s.',v_row.datname,v_row.xid_age),
                jsonb_build_object('database',v_row.datname,'xid_age',v_row.xid_age,
                                   'autovacuum_freeze_max_age',v_row.freeze_max),
                'Investigate vacuum blockers and complete an appropriate VACUUM before the wraparound wall.',
                jsonb_build_array(jsonb_build_object(
                    'id','wraparound-vacuum-first','action','vacuum_full','kind','prohibition',
                    'text','Do not default to VACUUM FULL; normal VACUUM is the wraparound remedy and VACUUM FULL takes an exclusive lock.'
                ))
            );
        END LOOP;

        FOR v_row IN
            SELECT d.datname,d.checksum_failures::bigint AS failures,
                   d.checksum_last_failure
              FROM pg_stat_database d WHERE coalesce(d.checksum_failures,0)>0
        LOOP
            PERFORM rvbbit._record_postgres_finding(
                v_run_id,'checksum_failures','database',v_row.datname,'critical','cumulative',
                format('PostgreSQL has recorded %s checksum failure(s) for database %I.',v_row.failures,v_row.datname),
                jsonb_build_object('database',v_row.datname,'failures',v_row.failures,
                                   'last_failure',v_row.checksum_last_failure,'stats_reset',v_stats_reset),
                'Treat this as possible page corruption; preserve evidence and follow the restore/repair runbook.'
            );
        END LOOP;

        FOR v_row IN
            SELECT c.oid,format('%I.%I',n.nspname,c.relname) AS object_name
              FROM pg_index i
              JOIN pg_class c ON c.oid=i.indexrelid
              JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE NOT i.indisvalid
               AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast','rvbbit','cron')
               AND n.nspname<>ALL(rvbbit._catalog_excluded_schemas())
        LOOP
            PERFORM rvbbit._record_postgres_finding(
                v_run_id,'index_invalid','index',v_row.object_name,'critical','scraped',
                format('Index %s is invalid.',v_row.object_name),
                jsonb_build_object('index',v_row.object_name,'index_oid',v_row.oid),
                'Determine whether this is failed-build debris or a required index before rebuilding or dropping it.',
                jsonb_build_array(jsonb_build_object(
                    'id','verify-invalid-index','action','drop_index','kind','precondition',
                    'text','Verify constraints, replicas, workload use, and the failed build before dropping an invalid index.'
                ))
            );
        END LOOP;

        FOR v_row IN
            SELECT c.oid,format('%I.%I',n.nspname,c.relname) AS object_name
              FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE c.relkind IN ('r','p')
               AND coalesce(c.reloptions,'{}'::text[]) @> ARRAY['autovacuum_enabled=false']
               AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast','rvbbit','cron')
               AND n.nspname<>ALL(rvbbit._catalog_excluded_schemas())
        LOOP
            PERFORM rvbbit._record_postgres_finding(
                v_run_id,'autovacuum_disabled_on_table','relation',v_row.object_name,'critical','scraped',
                format('Autovacuum is disabled on %s.',v_row.object_name),
                jsonb_build_object('table',v_row.object_name,'table_oid',v_row.oid),
                'Re-enable autovacuum or document and monitor a complete manual vacuum policy.'
            );
        END LOOP;

        IF v_pgss_schema IS NULL THEN
            PERFORM rvbbit._record_postgres_finding(
                v_run_id,'pg_stat_statements_missing','cluster','cluster','info','unavailable',
                'pg_stat_statements is unavailable; query-level workload evidence and index advice are disabled.',
                jsonb_build_object('extension','pg_stat_statements'),
                'Enable pg_stat_statements through the provider-appropriate configuration when query diagnostics are wanted.'
            );
        END IF;

        IF include_index_advice THEN
            IF v_pgss_schema IS NULL OR v_hypopg_schema IS NULL OR v_version<160000 THEN
                v_status := 'degraded';
            ELSE
                BEGIN
                    FOR v_q IN EXECUTE format($sql$
                        WITH db_statements AS (
                            SELECT queryid::bigint AS query_id,query::text,calls::bigint,
                                   total_exec_time::float8 AS total_exec_time_ms
                              FROM %I.pg_stat_statements
                             WHERE dbid=(SELECT oid FROM pg_database WHERE datname=current_database())
                               AND queryid IS NOT NULL
                        ), totals AS (
                            SELECT coalesce(sum(total_exec_time_ms),0)::float8 AS total_ms
                              FROM db_statements
                        )
                        SELECT s.*,
                               CASE WHEN t.total_ms>0
                                    THEN 100.0*s.total_exec_time_ms/t.total_ms ELSE 0 END AS db_time_pct
                          FROM db_statements s CROSS JOIN totals t
                         WHERE s.total_exec_time_ms >= $1
                         ORDER BY s.total_exec_time_ms DESC
                         LIMIT $2
                    $sql$,v_pgss_schema)
                    USING min_total_exec_ms,max_statements
                    LOOP
                        v_queries_considered := v_queries_considered+1;
                        v_sql := regexp_replace(btrim(v_q.query),'[;[:space:]]+$','','g');
                        IF v_sql !~* '^select([[:space:]\(]|$)' OR strpos(v_sql,';')>0 THEN
                            CONTINUE;
                        END IF;

                        BEGIN
                            EXECUTE 'EXPLAIN (GENERIC_PLAN, FORMAT JSON) ' || v_sql INTO v_plan;
                        EXCEPTION WHEN OTHERS THEN
                            CONTINUE;
                        END;
                        v_queries_planned := v_queries_planned+1;
                        v_cost_before := nullif((v_plan->0->'Plan'->>'Total Cost')::double precision,0);
                        IF v_cost_before IS NULL THEN CONTINUE; END IF;

                        FOR v_node IN
                            WITH RECURSIVE plan_nodes(node) AS (
                                SELECT v_plan->0->'Plan'
                                UNION ALL
                                SELECT child.value
                                  FROM plan_nodes parent
                                  CROSS JOIN LATERAL jsonb_array_elements(
                                      coalesce(parent.node->'Plans','[]'::jsonb)
                                  ) child
                            )
                            SELECT node FROM plan_nodes
                             WHERE node->>'Node Type'='Seq Scan'
                               AND coalesce(node->>'Relation Name','')<>''
                               AND coalesce(node->>'Filter','')<>''
                        LOOP
                            v_schema := coalesce(nullif(v_node->>'Schema',''),'public');
                            v_table := v_node->>'Relation Name';
                            v_filter := v_node->>'Filter';
                            IF v_schema IN ('pg_catalog','information_schema','pg_toast','rvbbit','cron')
                               OR v_schema=ANY(rvbbit._catalog_excluded_schemas()) THEN
                                CONTINUE;
                            END IF;
                            v_relation := to_regclass(format('%I.%I',v_schema,v_table));
                            IF v_relation IS NULL THEN CONTINUE; END IF;

                            SELECT coalesce(
                                       (last_analyze IS NULL AND last_autoanalyze IS NULL)
                                       OR n_mod_since_analyze>greatest(50,n_live_tup*0.20),false)
                              INTO v_stale_stats
                              FROM pg_stat_user_tables WHERE relid=v_relation;
                            IF coalesce(v_stale_stats,false) THEN
                                v_stale_skipped := v_stale_skipped+1;
                                CONTINUE;
                            END IF;

                            v_columns := ARRAY[]::text[];
                            FOR v_match IN
                                SELECT regexp_matches(
                                    v_filter,
                                    '([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=[[:space:]]*\$[0-9]+','g'
                                )
                            LOOP
                                v_column := v_match[1];
                                IF NOT v_column=ANY(v_columns)
                                   AND EXISTS (SELECT 1 FROM pg_attribute
                                                WHERE attrelid=v_relation AND attname=v_column
                                                  AND attnum>0 AND NOT attisdropped) THEN
                                    v_columns := array_append(v_columns,v_column);
                                END IF;
                                EXIT WHEN cardinality(v_columns)>=4;
                            END LOOP;
                            IF cardinality(v_columns)<4 THEN
                                FOR v_match IN
                                    SELECT regexp_matches(
                                        v_filter,
                                        '([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*(<=|>=|<|>)[[:space:]]*\$[0-9]+','g'
                                    )
                                LOOP
                                    v_column := v_match[1];
                                    IF NOT v_column=ANY(v_columns)
                                       AND EXISTS (SELECT 1 FROM pg_attribute
                                                    WHERE attrelid=v_relation AND attname=v_column
                                                      AND attnum>0 AND NOT attisdropped) THEN
                                        v_columns := array_append(v_columns,v_column);
                                    END IF;
                                    EXIT WHEN cardinality(v_columns)>=4;
                                END LOOP;
                            END IF;
                            IF cardinality(v_columns)=0 THEN CONTINUE; END IF;

                            SELECT string_agg(format('%I',column_name),', ' ORDER BY ordinal)
                              INTO v_column_list
                              FROM unnest(v_columns) WITH ORDINALITY columns(column_name,ordinal);
                            v_index_ddl := format('CREATE INDEX ON %I.%I (%s)',v_schema,v_table,v_column_list);
                            v_candidate_key := format('%I.%I(%s)',v_schema,v_table,array_to_string(v_columns,','));
                            v_candidates_tested := v_candidates_tested+1;
                            v_hypo_oid := NULL; v_hypo_name := NULL; v_estimated_bytes := NULL;

                            BEGIN
                                EXECUTE format(
                                    'SELECT indexrelid::oid,indexname::text FROM %I.hypopg_create_index($1)',
                                    v_hypopg_schema
                                ) INTO v_hypo_oid,v_hypo_name USING v_index_ddl;
                                BEGIN
                                    EXECUTE format('SELECT %I.hypopg_relation_size($1)',v_hypopg_schema)
                                       INTO v_estimated_bytes USING v_hypo_oid;
                                EXCEPTION WHEN OTHERS THEN
                                    v_estimated_bytes := NULL;
                                END;
                                EXECUTE 'EXPLAIN (GENERIC_PLAN, FORMAT JSON) ' || v_sql INTO v_after_plan;
                                v_cost_after := (v_after_plan->0->'Plan'->>'Total Cost')::double precision;
                                WITH RECURSIVE after_nodes(node) AS (
                                    SELECT v_after_plan->0->'Plan'
                                    UNION ALL
                                    SELECT child.value
                                      FROM after_nodes parent
                                      CROSS JOIN LATERAL jsonb_array_elements(
                                          coalesce(parent.node->'Plans','[]'::jsonb)
                                      ) child
                                )
                                SELECT coalesce(bool_or(node->>'Index Name'=v_hypo_name),false)
                                  INTO v_uses_index FROM after_nodes;
                            EXCEPTION WHEN OTHERS THEN
                                v_uses_index := false;
                                v_cost_after := NULL;
                            END;
                            BEGIN
                                EXECUTE format('SELECT %I.hypopg_reset()',v_hypopg_schema);
                            EXCEPTION WHEN OTHERS THEN NULL;
                            END;

                            IF NOT coalesce(v_uses_index,false) OR v_cost_after IS NULL
                               OR v_cost_after>=v_cost_before*(1-min_index_improvement) THEN
                                CONTINUE;
                            END IF;
                            v_improvement := 100.0*(v_cost_before-v_cost_after)/v_cost_before;
                            SELECT coalesce(n_tup_ins+n_tup_upd+n_tup_del>greatest(10000,seq_scan+idx_scan),false)
                              INTO v_write_heavy FROM pg_stat_user_tables WHERE relid=v_relation;
                            v_caveats := jsonb_build_array(
                                'planner estimate, not a measured speedup; verify on a production-like copy before building',
                                'build with CREATE INDEX CONCURRENTLY off-peak; plain CREATE INDEX blocks writes',
                                'the normalized statement was planned under the collector session search_path'
                            );
                            IF coalesce(v_write_heavy,false) THEN
                                v_caveats := v_caveats || jsonb_build_array(
                                    'this table is write-heavy; weigh the read gain against added write amplification'
                                );
                            END IF;

                            INSERT INTO rvbbit.postgres_index_advice(
                                run_id,candidate_key,table_oid,table_name,columns,query_id,calls,
                                total_exec_time_ms,db_time_pct,cost_before,cost_after,
                                improvement_pct,estimated_bytes,index_ddl,caveats
                            ) VALUES (
                                v_run_id,v_candidate_key,v_relation,format('%I.%I',v_schema,v_table),
                                v_columns,v_q.query_id,v_q.calls,v_q.total_exec_time_ms,v_q.db_time_pct,
                                v_cost_before,v_cost_after,v_improvement,v_estimated_bytes,v_index_ddl,v_caveats
                            )
                            ON CONFLICT (run_id,candidate_key) DO UPDATE SET
                                query_id=EXCLUDED.query_id,calls=EXCLUDED.calls,
                                total_exec_time_ms=EXCLUDED.total_exec_time_ms,
                                db_time_pct=EXCLUDED.db_time_pct,cost_before=EXCLUDED.cost_before,
                                cost_after=EXCLUDED.cost_after,improvement_pct=EXCLUDED.improvement_pct,
                                estimated_bytes=EXCLUDED.estimated_bytes,index_ddl=EXCLUDED.index_ddl,
                                caveats=EXCLUDED.caveats,observed_at=clock_timestamp()
                            WHERE EXCLUDED.db_time_pct>rvbbit.postgres_index_advice.db_time_pct
                               OR (EXCLUDED.db_time_pct=rvbbit.postgres_index_advice.db_time_pct
                                   AND EXCLUDED.improvement_pct>rvbbit.postgres_index_advice.improvement_pct);
                        END LOOP;
                    END LOOP;
                EXCEPTION WHEN OTHERS THEN
                    GET STACKED DIAGNOSTICS v_error=MESSAGE_TEXT;
                    v_status := 'degraded';
                    v_capabilities := v_capabilities || jsonb_build_object('index_advice_error',left(v_error,500));
                    BEGIN
                        EXECUTE format('SELECT %I.hypopg_reset()',v_hypopg_schema);
                    EXCEPTION WHEN OTHERS THEN NULL;
                    END;
                END;
            END IF;
        END IF;

        SELECT count(*)::integer INTO v_findings
          FROM rvbbit.postgres_diagnostic_findings WHERE run_id=v_run_id;
        SELECT count(*)::integer INTO v_candidates
          FROM rvbbit.postgres_index_advice WHERE run_id=v_run_id;
        UPDATE rvbbit.postgres_diagnostic_runs
           SET finished_at=clock_timestamp(),status=v_status,capabilities=v_capabilities,
               summary=jsonb_build_object(
                   'findings',v_findings,'index_candidates',v_candidates,
                   'queries_considered',v_queries_considered,'queries_planned',v_queries_planned,
                   'candidates_tested',v_candidates_tested,'stale_stats_skipped',v_stale_skipped,
                   'raw_query_text_persisted',false
               )
         WHERE run_id=v_run_id;

        DELETE FROM rvbbit.postgres_diagnostic_runs
         WHERE finished_at<clock_timestamp()-interval '90 days';

        RETURN jsonb_build_object(
            'status',v_status,'run_id',v_run_id,'findings',v_findings,
            'index_candidates',v_candidates,'capabilities',v_capabilities,
            'queries_considered',v_queries_considered,'queries_planned',v_queries_planned,
            'candidates_tested',v_candidates_tested,'stale_stats_skipped',v_stale_skipped
        );
    EXCEPTION WHEN OTHERS THEN
        GET STACKED DIAGNOSTICS v_error=MESSAGE_TEXT;
        UPDATE rvbbit.postgres_diagnostic_runs
           SET finished_at=clock_timestamp(),status='failed',error=left(v_error,2000)
         WHERE run_id=v_run_id;
        RETURN jsonb_build_object('status','failed','run_id',v_run_id,'error',v_error);
    END;
END
$fn$;

COMMENT ON FUNCTION rvbbit.collect_postgres_diagnostics(boolean,integer,double precision,double precision) IS
    'Collect a bounded deterministic PostgreSQL snapshot and optional HypoPG planner-validated index advice. Never executes or stores inspected query text.';

CREATE OR REPLACE VIEW rvbbit.postgres_diagnostic_latest_run AS
SELECT r.*
  FROM rvbbit.postgres_diagnostic_runs r
 WHERE r.run_id=(
     SELECT max(run_id) FROM rvbbit.postgres_diagnostic_runs
      WHERE status IN ('ok','degraded')
 );

CREATE OR REPLACE VIEW rvbbit.postgres_diagnostic_current_findings AS
SELECT f.*
  FROM rvbbit.postgres_diagnostic_findings f
 WHERE f.run_id=(SELECT run_id FROM rvbbit.postgres_diagnostic_latest_run);

CREATE OR REPLACE VIEW rvbbit.postgres_index_advice_current AS
SELECT a.*
  FROM rvbbit.postgres_index_advice a
 WHERE a.run_id=(SELECT run_id FROM rvbbit.postgres_diagnostic_latest_run);

CREATE OR REPLACE VIEW rvbbit.postgres_diagnostic_status AS
SELECT r.run_id,r.started_at,r.finished_at,r.status,r.server_version_num,
       r.stats_reset,r.pgss_reset,r.capabilities,r.snapshot,r.summary,r.error,
       coalesce((SELECT count(*) FROM rvbbit.postgres_diagnostic_current_findings),0)::bigint AS current_findings,
       coalesce((SELECT count(*) FROM rvbbit.postgres_index_advice_current),0)::bigint AS current_index_candidates
  FROM rvbbit.postgres_diagnostic_latest_run r;

-- Publish only current findings/candidates into the existing System Learning
-- corpus.  Historical runs remain queryable without flooding the Brain.
CREATE OR REPLACE VIEW rvbbit.system_learning_postgres_items AS
SELECT uri,title,content_hash,occurred_at,body,props
  FROM rvbbit.system_learning_collaborative_items
UNION ALL
SELECT
    'rvbbit:postgres-finding:' || md5(f.finding_key || ':' || f.object_identity) AS uri,
    'Postgres ' || replace(f.finding_key,'_',' ') || ': ' || f.object_identity AS title,
    md5(f.summary || f.evidence::text || coalesce(f.recommendation,'')) AS content_hash,
    f.observed_at AS occurred_at,
    concat_ws(E'\n',
        'Deterministic PostgreSQL finding.',
        'Finding: ' || f.finding_key,
        'Object: ' || f.object_identity,
        'Severity: ' || f.severity,
        'Exactness: ' || f.exactness,
        'Summary: ' || f.summary,
        CASE WHEN f.recommendation IS NOT NULL THEN 'Recommendation: ' || f.recommendation END,
        'Evidence: ' || left(f.evidence::text,6000),
        CASE WHEN f.safety<>'[]'::jsonb THEN 'Safety: ' || left(f.safety::text,3000) END
    ) AS body,
    jsonb_build_object(
        'object_type','postgres_finding','finding_key',f.finding_key,
        'object_class',f.object_class,'object_identity',f.object_identity,
        'severity',f.severity,'exactness',f.exactness,'evidence',f.evidence,
        'recommendation',f.recommendation,'safety',f.safety
    ) AS props
FROM rvbbit.postgres_diagnostic_current_findings f
UNION ALL
SELECT
    'rvbbit:postgres-index-advice:' || md5(a.candidate_key) AS uri,
    'Hypothetical index for ' || a.table_name || ' on ' || array_to_string(a.columns,', ') AS title,
    md5(a.index_ddl || a.cost_before::text || a.cost_after::text || a.caveats::text) AS content_hash,
    a.observed_at AS occurred_at,
    concat_ws(E'\n',
        'Planner-validated hypothetical index candidate.',
        'Table: ' || a.table_name,
        'Columns: ' || array_to_string(a.columns,', '),
        'Estimated planner cost: ' || round(a.cost_before::numeric,2)::text || ' -> ' || round(a.cost_after::numeric,2)::text,
        'Estimated improvement: ' || round(a.improvement_pct::numeric,1)::text || '%',
        'Database execution-time share: ' || round(a.db_time_pct::numeric,2)::text || '%',
        'Suggested DDL: ' || a.index_ddl,
        'Caveats: ' || a.caveats::text
    ) AS body,
    jsonb_build_object(
        'object_type','postgres_index_advice','candidate_key',a.candidate_key,
        'table',a.table_name,'columns',a.columns,'query_id',a.query_id,
        'db_time_pct',a.db_time_pct,'cost_before',a.cost_before,'cost_after',a.cost_after,
        'improvement_pct',a.improvement_pct,'estimated_bytes',a.estimated_bytes,
        'confidence',a.confidence,'exactness',a.exactness,'index_ddl',a.index_ddl,
        'caveats',a.caveats
    ) AS props
FROM rvbbit.postgres_index_advice_current a;

CREATE OR REPLACE VIEW rvbbit.system_learning_item_summary AS
SELECT coalesce(props->>'object_type','unknown') AS object_type,
       count(*)::bigint AS items,max(occurred_at) AS last_seen_at
  FROM rvbbit.system_learning_postgres_items
 GROUP BY coalesce(props->>'object_type','unknown');

UPDATE rvbbit.brain_doc_providers
   SET list_sql=$provider$
       SELECT uri,title,content_hash,occurred_at,body,props
         FROM rvbbit.system_learning_postgres_items
   $provider$,
       description='RVBBIT-learned workload, routing, acceleration, specialist, deterministic PostgreSQL, and operator artifacts.',
       updated_at=clock_timestamp()
 WHERE provider='rvbbit-system-learning';

-- Existing installs keep the same pg_health operator, but deterministic
-- evidence becomes its first stop instead of asking the model to rediscover
-- every basic PostgreSQL condition from raw catalogs on every run.
UPDATE rvbbit.operators
   SET steps=jsonb_set(
       steps,'{0,system}',
       to_jsonb(
           'Start with rvbbit.postgres_diagnostic_status, rvbbit.postgres_diagnostic_current_findings, and rvbbit.postgres_index_advice_current when present. Treat these deterministic rows as primary evidence, preserve their exactness and safety fields, and use raw pg_stat views only to refresh or deepen the diagnosis. Never invent a finding that contradicts the deterministic evidence.\n\n'
           || (steps->0->>'system')
       )
   ), updated_at=clock_timestamp()
 WHERE name='pg_health'
   AND jsonb_typeof(steps)='array'
   AND coalesce(steps->0->>'system','') NOT LIKE '%postgres_diagnostic_current_findings%';
