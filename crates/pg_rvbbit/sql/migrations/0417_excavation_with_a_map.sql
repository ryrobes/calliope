-- 0417: Excavation with a Map, phase A (docs/EXCAVATION_PLAN.md).
--
-- The excavator treated every relation as a candidate business domain. On the
-- pilot that minted 25 domains from n8n's own app database, 10 from
-- fivetran_metadata, and 492 one-table "domains" from Salesforce setup
-- objects — none of it human-approved, all of it searchable and materialized.
-- Phase A puts the map back:
--   1. a source registry (schema → source system → role) populated from the
--      Fivetran connector map and an internal-tooling list;
--   2. platform-object patterns per source (Salesforce setup objects,
--      Fivetran bookkeeping) that never deserve inference;
--   3. the sweep's admission predicate consults both and rejects empty
--      relations — deterministic, zero inference;
--   4. machine confidence never promotes: auto-adoption is switched off for
--      value graphs, and approval-by-use now promotes a well-used PROPOSAL
--      directly, so materialization moves from the excavator's say-so to
--      affirmation;
--   5. transient upstream failures (429 lane saturation, disconnects) defer a
--      workflow run instead of failing it.
-- Provisional (usable) graphs stay searchable — affirm-by-use needs them
-- reachable; what changes is that nothing plumbing-shaped is admitted and
-- nothing materializes before a human or real usage says so.

-- ── 1. Source registry ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rvbbit.business_sources (
    schema_name text PRIMARY KEY,
    source_system text,
    role text NOT NULL CHECK (role IN ('business','derived','plumbing','excluded','unknown')),
    adapter text,
    decided_by text NOT NULL DEFAULT 'populate',
    note text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE rvbbit.business_sources IS
    'Excavation source registry: every schema carries a source system and a role. business/derived are excavated; plumbing/excluded/unknown never are. Operators set decided_by=''operator'' when they change a role; populate() never overrides that.';

-- Rebuild the registry from what the box already knows. Deterministic:
-- internal tooling list, then the Fivetran connector map (exact schema or
-- schema prefixed by the connector name), then transformation-layer naming.
-- Everything else is 'unknown' — not excavated until a person decides.
CREATE OR REPLACE FUNCTION rvbbit.business_sources_populate()
RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_schema text;
    v_role text;
    v_system text;
    v_adapter text;
    v_by text;
    v_note text;
    v_has_connectors boolean := to_regclass('fivetran_metadata.connector') IS NOT NULL;
    v_connector_sql text;
    v_connector record;
    v_touched integer := 0;
BEGIN
    IF v_has_connectors THEN
        v_connector_sql := 'SELECT connector_name::text AS connector_name, connector_type_id::text AS connector_type_id FROM fivetran_metadata.connector';
        IF EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema='fivetran_metadata' AND table_name='connector' AND column_name='_fivetran_deleted') THEN
            v_connector_sql := v_connector_sql || ' WHERE NOT coalesce(_fivetran_deleted,false)';
        END IF;
    END IF;

    FOR v_schema IN
        SELECT n.nspname FROM pg_namespace n
         WHERE n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'
           AND EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace=n.oid AND c.relkind IN ('r','p','m'))
         ORDER BY 1
    LOOP
        v_role := NULL; v_system := NULL; v_adapter := NULL; v_by := 'populate'; v_note := NULL;

        -- The appliance's own schemas and internal tooling: plumbing by definition.
        IF v_schema IN ('rvbbit','hindsight','cubes','seeds','parity','dagster_operational',
                        'fivetran_metadata','n8n','cron','pgsodium','vault')
           OR v_schema LIKE '\_dlt\_%' THEN
            v_role := 'plumbing';
            v_system := CASE WHEN v_schema LIKE '\_dlt\_%' THEN 'dlt' ELSE v_schema END;
            v_by := 'internal_list';
        END IF;

        -- The appliance's own mirror jobs: a destination schema is a business
        -- mirror of the named connection.
        IF v_role IS NULL AND to_regclass('rvbbit.mirror_jobs') IS NOT NULL THEN
            SELECT j.connection_name, 'mirror:'||coalesce(c.dialect, 'unknown')
              INTO v_system, v_adapter
              FROM rvbbit.mirror_jobs j
              LEFT JOIN rvbbit.mirror_connections c USING (connection_name)
             WHERE j.destination_schema = v_schema
             ORDER BY j.enabled DESC, j.job_name
             LIMIT 1;
            IF v_system IS NOT NULL THEN
                v_role := 'business'; v_by := 'mirror_jobs';
            END IF;
        END IF;

        -- The Fivetran connector map: the mirror's own record of what each schema is.
        IF v_role IS NULL AND v_has_connectors THEN
            FOR v_connector IN EXECUTE v_connector_sql LOOP
                IF v_schema = v_connector.connector_name
                   OR v_schema LIKE v_connector.connector_name || '\_%' THEN
                    IF v_connector.connector_type_id = 'fivetran_log' THEN
                        v_role := 'plumbing';
                    ELSIF v_schema <> v_connector.connector_name
                          AND (v_schema LIKE '%\_source' OR v_schema LIKE '%\_staging'
                               OR v_schema LIKE '%\_stg\_%' OR v_schema LIKE 'stg\_%') THEN
                        v_role := 'derived';
                    ELSE
                        v_role := 'business';
                    END IF;
                    v_system := v_connector.connector_type_id;
                    v_adapter := 'fivetran:' || v_connector.connector_type_id;
                    v_by := 'connector_map';
                    EXIT;
                END IF;
            END LOOP;
        END IF;

        -- Transformation layers (dbt-style) are derived: excavated, attributed upstream.
        IF v_role IS NULL AND (v_schema IN ('marts','reporting','intermediate','staging')
                               OR v_schema LIKE '%\_staging' OR v_schema LIKE 'stg\_%'
                               OR v_schema LIKE '%\_source' OR v_schema LIKE '%\_stg\_%'
                               OR v_schema LIKE '%\_marts' OR v_schema LIKE '%\_intermediate') THEN
            v_role := 'derived'; v_system := 'transform'; v_by := 'heuristic';
        END IF;

        IF v_role IS NULL THEN
            v_role := 'unknown';
            -- A hint, never a decision: a connector whose leading token matches.
            IF v_has_connectors THEN
                FOR v_connector IN EXECUTE v_connector_sql LOOP
                    IF split_part(v_schema,'_',1) <> '' AND split_part(v_schema,'_',1) = split_part(v_connector.connector_name,'_',1) THEN
                        v_note := 'possibly connector '||v_connector.connector_name||' ('||v_connector.connector_type_id||') — set role and decided_by=''operator'' to confirm';
                        EXIT;
                    END IF;
                END LOOP;
            END IF;
        END IF;

        INSERT INTO rvbbit.business_sources(schema_name, source_system, role, adapter, decided_by, note)
        VALUES (v_schema, v_system, v_role, v_adapter, v_by, v_note)
        ON CONFLICT (schema_name) DO UPDATE
           SET source_system=coalesce(excluded.source_system, rvbbit.business_sources.source_system),
               adapter=coalesce(excluded.adapter, rvbbit.business_sources.adapter),
               role=excluded.role, decided_by=excluded.decided_by,
               note=coalesce(excluded.note, rvbbit.business_sources.note),
               updated_at=now()
         WHERE rvbbit.business_sources.decided_by <> 'operator';
        IF FOUND THEN v_touched := v_touched + 1; END IF;
    END LOOP;
    RETURN jsonb_build_object(
        'status','complete','touched',v_touched,
        'roles',(SELECT coalesce(jsonb_object_agg(role, n),'{}'::jsonb)
                   FROM (SELECT role, count(*) AS n FROM rvbbit.business_sources GROUP BY role) s));
END
$fn$;
COMMENT ON FUNCTION rvbbit.business_sources_populate() IS
    'Deterministically (re)derives schema roles from the Fivetran connector map, the internal-tooling list, and transformation-layer naming. Never overrides rows with decided_by=''operator''.';

CREATE OR REPLACE FUNCTION rvbbit.business_source_role(p_schema text)
RETURNS text
LANGUAGE sql STABLE
AS $fn$
    SELECT coalesce((SELECT role FROM rvbbit.business_sources WHERE schema_name=btrim(p_schema)), 'unknown');
$fn$;

-- ── 2. Platform-object patterns: relations that never deserve inference ───
CREATE TABLE IF NOT EXISTS rvbbit.business_source_platform_patterns (
    source_system text NOT NULL,        -- '*' applies to every source
    relation_regex text NOT NULL,       -- case-insensitive, matched against the bare relation name
    note text,
    PRIMARY KEY (source_system, relation_regex)
);
COMMENT ON TABLE rvbbit.business_source_platform_patterns IS
    'Setup, metadata, and bookkeeping relations per source system, rejected before any model sees them. Public documentation, not inference. Business objects (leads, tasks, *_history, custom *_c objects) are deliberately NOT here.';

INSERT INTO rvbbit.business_source_platform_patterns(source_system, relation_regex, note) VALUES
    ('*', '^_fivetran', 'Fivetran bookkeeping'),
    ('*', '^fivetran_', 'Fivetran connector internals and helper tables (formula, picklist, rollup, audit) mirrored into the source schema'),
    ('*', '_share$', 'sharing rows, not business records'),
    ('*', '_change_event$', 'CDC plumbing'),
    ('salesforce', '^(apex_|flow_|flow$|process_|permission_set|object_permissions|field_permissions|setup_entity|setup_assistant|service_setup|entity_definition|entity_particle$|field_definition|relationship_domain|custom_(permission|brand|notification|help|http|object_userlicense)|static_resource|email_template|email_services_|letterhead|enhanced_letterhead|layout|list_view|dashboard|report|folder|document$|attachment$|content_(workspace|folder|distribution|version|document|body)|managed_content|brand_template|auth_|login_|oauth|connected_application|named_credential|remote_proxy|site$|site_|domain|network|topic|tenant|package|install|metadata|wave_|ui_|web_link|duplicate_|dupcheck_|matching_rule|lightning_|visualforce|cron_trigger|async_apex|scheduled_job|background_task|background_operation|calculation_step|data_share|filter_condition|scheduled_object|platform_(event|action)|profile$|user_(license|permission|role|package|app|defined_label|service_presence|email_preferred)|group$|group_member$|queue_sobject$|business_hours$|business_process$|holiday$|org_wide_email|org_metric|external_data|data_type$|data_lineage_|data_lake_object|data_stream|data_space|data_semantic_search|mkt_data_transform|expression_filter|macro|quick_action|quick_text|action_link|assignment_rule|auto_response|escalation|entitlement_template|milestone_type|sla_process|search_layout|translation|language|region$|country$|state$|currency_type|record_type$|security_health_check|scratch_org|active_scratch_org|prompt_(action|error)|collaboration_group|chatter_activity|feed_revision|work_badge|work_access$|fiscal_year_settings|today_goal$|out_of_office$|organization$|file_search_activity|calendar_view$)', 'Salesforce setup, platform, and managed-package internals (public object reference)'),
    ('salesforce', '(_settings?_c|_config(uration)?_c|_setting_c|_settings?_history|_config_history)$', 'managed-package settings/config custom objects, not business records')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION rvbbit._business_source_relation_is_platform(p_schema text, p_relation text)
RETURNS boolean
LANGUAGE sql STABLE
AS $fn$
    SELECT EXISTS (
        SELECT 1 FROM rvbbit.business_source_platform_patterns pat
         WHERE (pat.source_system='*'
                OR pat.source_system = coalesce(
                    (SELECT source_system FROM rvbbit.business_sources WHERE schema_name=btrim(p_schema)),
                    btrim(p_schema)))
           AND lower(btrim(p_relation)) ~* pat.relation_regex
    );
$fn$;

-- ── 3. The admission predicate the sweep planner already consults ─────────
-- Same signature as 0347; adds: source role, platform patterns, emptiness.
CREATE OR REPLACE FUNCTION rvbbit._business_topology_relation_allowed(
    p_schema text,
    p_relation text
) RETURNS boolean
LANGUAGE sql STABLE
AS $allowed$
    SELECT nullif(btrim(p_schema),'') IS NOT NULL
       AND nullif(btrim(p_relation),'') IS NOT NULL
       AND lower(btrim(p_schema)) NOT IN (
           'pg_catalog','information_schema','rvbbit','hindsight','cubes'
       )
       AND lower(btrim(p_schema)) NOT LIKE 'pg_toast%'
       AND lower(btrim(p_schema)) NOT LIKE 'pg_temp_%'
       AND btrim(p_schema) !~* '^_dlt_'
       AND btrim(p_schema)<>ALL(rvbbit._catalog_excluded_schemas())
       AND btrim(p_relation) !~* '^_dlt_'
       AND lower(btrim(p_relation)) NOT IN (
           '_sync_metadata','_sync_state','_snapshot_metadata'
       )
       -- Excavation with a Map: only business/derived sources are dug.
       AND rvbbit.business_source_role(p_schema) IN ('business','derived')
       -- Setup, metadata, and bookkeeping objects never reach a model.
       AND NOT rvbbit._business_source_relation_is_platform(p_schema, p_relation)
       -- Empty relations are not evidence: no live tuples and either an
       -- analyzed zero or (never analyzed, reltuples = -1) a zero-byte heap.
       -- On the pilot this is 800 of 1,269 Salesforce mirrors.
       AND NOT EXISTS (
           SELECT 1 FROM pg_stat_user_tables st
             JOIN pg_class c ON c.oid=st.relid
            WHERE st.schemaname=btrim(p_schema) AND st.relname=btrim(p_relation)
              AND st.n_live_tup=0
              AND (c.reltuples=0 OR (c.reltuples<0 AND pg_relation_size(c.oid)=0))
       );
$allowed$;

-- ── 4. Machine confidence never promotes ──────────────────────────────────
UPDATE rvbbit.knowledge_loop_policy
   SET auto_adopt_enabled=false, updated_at=now()
 WHERE kind='value_graph' AND auto_adopt_enabled;

-- Approval by use now also covers usable PROPOSALS: enough clean turns promote
-- and affirm in one step, so materialization (cube, metrics) happens at
-- affirmation rather than at the excavator's say-so. Branch (a) is unchanged
-- from 0368; branch (b) is new.
CREATE OR REPLACE FUNCTION rvbbit.knowledge_loop_affirm_tick()
RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_policy rvbbit.knowledge_loop_policy%ROWTYPE;
    v_row record;
    v_affirmed jsonb := '[]'::jsonb;
    v_promoted jsonb := '[]'::jsonb;
BEGIN
    SELECT * INTO v_policy FROM rvbbit.knowledge_loop_policy WHERE kind='value_graph';
    IF NOT FOUND THEN RETURN jsonb_build_object('status','no_policy'); END IF;

    -- (a) auto-provisional promotions earned by use
    FOR v_row IN
        SELECT promotion.graph_id, usage.turns
          FROM rvbbit.business_value_graph_promotions promotion
          JOIN rvbbit.business_value_graph_proposals proposal USING (graph_id)
          JOIN LATERAL (
              SELECT count(*)::integer AS turns
                FROM rvbbit.business_context_turn_receipts receipt
               WHERE promotion.graph_id=ANY(receipt.used_graph_ids)
                 AND receipt.outcome IN ('complete','partial')
                 AND receipt.completed_at >= clock_timestamp()
                     - make_interval(days => v_policy.affirm_window_days)
          ) usage ON true
         WHERE promotion.adoption_state='auto_provisional'
           AND proposal.status='promoted'
           AND usage.turns >= v_policy.affirm_min_turns
           AND NOT EXISTS (
               SELECT 1 FROM rvbbit.business_context_turn_receipts receipt
                WHERE promotion.graph_id=ANY(receipt.used_graph_ids)
                  AND receipt.feedback_signal IN ('possible_correction','possible_contradiction')
                  AND receipt.created_at >= promotion.promoted_at)
           AND NOT EXISTS (
               SELECT 1 FROM rvbbit.business_semantic_assertions assertion
                JOIN rvbbit.business_semantic_assertion_states state USING (assertion_id)
                WHERE assertion.graph_id=promotion.graph_id
                  AND state.validation_state='contradicted')
         LIMIT 10
    LOOP
        UPDATE rvbbit.business_value_graph_promotions
           SET adoption_state='affirmed', adoption_state_changed_at=clock_timestamp()
         WHERE graph_id=v_row.graph_id;
        INSERT INTO rvbbit.knowledge_loop_events (graph_id, kind, from_state, to_state, reason, evidence)
        VALUES (v_row.graph_id, 'affirmed', 'auto_provisional', 'affirmed',
                v_row.turns||' corrected-free turns in '||v_policy.affirm_window_days||' days',
                jsonb_build_object('turns', v_row.turns));
        v_affirmed := v_affirmed || to_jsonb(v_row.graph_id);
    END LOOP;

    -- (b) usable proposals earned by use: promote and affirm in one step.
    FOR v_row IN
        SELECT proposal.graph_id, usage.turns
          FROM rvbbit.business_value_graph_proposals proposal
          JOIN LATERAL (
              SELECT count(*)::integer AS turns
                FROM rvbbit.business_context_turn_receipts receipt
               WHERE proposal.graph_id=ANY(receipt.used_graph_ids)
                 AND receipt.outcome IN ('complete','partial')
                 AND receipt.completed_at >= clock_timestamp()
                     - make_interval(days => v_policy.affirm_window_days)
          ) usage ON true
         WHERE proposal.status='proposed' AND proposal.usable
           AND usage.turns >= v_policy.affirm_min_turns
           AND NOT EXISTS (SELECT 1 FROM rvbbit.business_value_graph_promotions pr
                            WHERE pr.graph_id=proposal.graph_id)
           AND rvbbit.business_source_role(split_part(proposal.source_relations[1],'.',1))
               IN ('business','derived')
           AND NOT EXISTS (
               SELECT 1 FROM rvbbit.business_context_turn_receipts receipt
                WHERE proposal.graph_id=ANY(receipt.used_graph_ids)
                  AND receipt.feedback_signal IN ('possible_correction','possible_contradiction')
                  AND receipt.created_at >= proposal.proposed_at)
           AND NOT EXISTS (
               SELECT 1 FROM rvbbit.business_semantic_assertions assertion
                JOIN rvbbit.business_semantic_assertion_states state USING (assertion_id)
                WHERE assertion.graph_id=proposal.graph_id
                  AND state.validation_state='contradicted')
           AND NOT EXISTS (
               SELECT 1 FROM rvbbit.business_semantic_assertions assertion
                 JOIN rvbbit.knowledge_loop_denylist deny
                   ON deny.family_id=assertion.family_id AND deny.fingerprint=assertion.fingerprint
                WHERE assertion.graph_id=proposal.graph_id AND deny.rebutted_at IS NULL)
           -- a promotion that failed recently is not retried every tick
           AND NOT EXISTS (
               SELECT 1 FROM rvbbit.knowledge_loop_events ev
                WHERE ev.graph_id=proposal.graph_id AND ev.kind='adopt_failed'
                  AND ev.created_at >= clock_timestamp() - interval '7 days')
         ORDER BY usage.turns DESC, proposal.proposed_at
         LIMIT 5
    LOOP
        BEGIN
            PERFORM rvbbit.business_value_promote_graph(
                v_row.graph_id, NULL, NULL,
                'knowledge-loop: affirmed by use ('||v_row.turns||' clean turns)');
            UPDATE rvbbit.business_value_graph_promotions
               SET adoption_state='affirmed', adoption_state_changed_at=clock_timestamp()
             WHERE graph_id=v_row.graph_id;
            INSERT INTO rvbbit.knowledge_loop_events (graph_id, kind, from_state, to_state, reason, evidence)
            VALUES (v_row.graph_id, 'affirmed', 'proposed', 'affirmed',
                    v_row.turns||' corrected-free turns in '||v_policy.affirm_window_days||' days (promoted by use)',
                    jsonb_build_object('turns', v_row.turns, 'promoted_by_use', true));
            v_promoted := v_promoted || to_jsonb(v_row.graph_id);
        EXCEPTION WHEN OTHERS THEN
            INSERT INTO rvbbit.knowledge_loop_events (graph_id, kind, reason, evidence)
            VALUES (v_row.graph_id, 'adopt_failed', left(SQLERRM, 500),
                    jsonb_build_object('turns', v_row.turns, 'promoted_by_use', true));
        END;
    END LOOP;
    RETURN jsonb_build_object('status','complete','affirmed',v_affirmed,'promoted_by_use',v_promoted);
END
$fn$;

-- ── 5. Transient failures defer instead of fail ───────────────────────────
ALTER TABLE rvbbit.business_topology_workflow_runs
    ADD COLUMN IF NOT EXISTS not_before timestamptz;
COMMENT ON COLUMN rvbbit.business_topology_workflow_runs.not_before IS
    'A queued run is not claimable before this instant (set by business_topology_defer_workflow after a transient upstream failure).';

CREATE OR REPLACE FUNCTION rvbbit.business_topology_defer_workflow(
    p_run_id uuid,
    p_worker_id text,
    p_error text,
    p_delay_seconds integer DEFAULT 300
) RETURNS timestamptz
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_until timestamptz := clock_timestamp() + make_interval(secs => least(3600, greatest(30, coalesce(p_delay_seconds,300))));
BEGIN
    UPDATE rvbbit.business_topology_workflow_runs workflow
       SET status='queued', phase='deferred',
           error=left(coalesce(nullif(p_error,''),'deferred'),6000),
           progress=workflow.progress||jsonb_build_object(
               'phase','deferred',
               'deferred_at',clock_timestamp(),
               'deferred_until',v_until,
               'deferred_reason',left(coalesce(p_error,''),500),
               'deferrals',coalesce((workflow.progress->>'deferrals')::integer,0)+1),
           worker_id=NULL, lease_expires_at=NULL,
           not_before=v_until,
           updated_at=clock_timestamp()
     WHERE workflow.run_id=p_run_id
       AND workflow.status='running'
       AND workflow.worker_id=p_worker_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'running workflow % is not claimed by worker %', p_run_id, p_worker_id;
    END IF;
    RETURN v_until;
END
$fn$;
COMMENT ON FUNCTION rvbbit.business_topology_defer_workflow(uuid,text,text,integer) IS
    'Returns a running run to the queue with a not-before instant, keeping its progress, after a transient upstream failure (429 lane saturation, disconnect). Attempt bounding is the worker''s job.';

-- The claim honors not_before. Body otherwise identical to 0354.
CREATE OR REPLACE FUNCTION rvbbit.business_topology_claim_workflow(
    p_worker_id text,
    p_lease_seconds integer DEFAULT 300
) RETURNS TABLE (
    run_id uuid,
    run_name text,
    parameters jsonb,
    progress jsonb,
    requested_by text,
    attempts integer
)
LANGUAGE plpgsql VOLATILE
AS $fn$
BEGIN
    IF nullif(btrim(p_worker_id),'') IS NULL THEN
        RAISE EXCEPTION 'worker_id is required';
    END IF;
    IF p_lease_seconds NOT BETWEEN 30 AND 3600 THEN
        RAISE EXCEPTION 'lease_seconds must be between 30 and 3600';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'rvbbit.business-topology-workflow-runner',0
    ));

    UPDATE rvbbit.business_topology_workflow_runs workflow
       SET status='cancelled',phase='cancelled',error=NULL,
           progress=workflow.progress||jsonb_build_object(
               'phase','cancelled','cancel_reconciled_at',clock_timestamp(),
               'cancel_reconciled_reason','worker_lease_expired'
           ),
           worker_id=NULL,lease_expires_at=NULL,
           updated_at=clock_timestamp(),finished_at=clock_timestamp()
     WHERE workflow.status='cancel_requested'
       AND (
           workflow.lease_expires_at IS NULL
           OR workflow.lease_expires_at<clock_timestamp()
       );

    IF EXISTS (
        SELECT 1 FROM rvbbit.business_topology_workflow_runs
         WHERE status IN ('running','cancel_requested')
           AND NOT (
               status='running' AND lease_expires_at<clock_timestamp()
           )
    ) THEN
        RETURN;
    END IF;

    RETURN QUERY
    WITH picked AS (
        SELECT workflow.run_id
          FROM rvbbit.business_topology_workflow_runs workflow
         WHERE (
                workflow.status='queued'
                AND (workflow.not_before IS NULL OR workflow.not_before<=clock_timestamp())
            )
            OR (
                workflow.status='running'
                AND workflow.lease_expires_at<clock_timestamp()
            )
         ORDER BY CASE WHEN workflow.status='running' THEN 0 ELSE 1 END,
                  workflow.requested_at
         LIMIT 1
         FOR UPDATE SKIP LOCKED
    ), updated AS (
        UPDATE rvbbit.business_topology_workflow_runs workflow
           SET status='running',
               phase=CASE WHEN workflow.status='queued' THEN 'claimed' ELSE 'resuming' END,
               worker_id=btrim(p_worker_id),
               lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
               attempts=workflow.attempts+1,
               started_at=coalesce(workflow.started_at,clock_timestamp()),
               updated_at=clock_timestamp(),error=NULL,not_before=NULL,
               progress=workflow.progress||jsonb_build_object(
                   'phase',CASE WHEN workflow.status='queued' THEN 'claimed' ELSE 'resuming' END,
                   'worker_id',btrim(p_worker_id),'claimed_at',clock_timestamp()
               )
          FROM picked
         WHERE workflow.run_id=picked.run_id
         RETURNING workflow.*
    )
    SELECT updated.run_id,updated.run_name,updated.parameters,updated.progress,
           updated.requested_by,updated.attempts
      FROM updated;
END
$fn$;

-- Seed the registry on every install that runs this migration.
SELECT rvbbit.business_sources_populate();
