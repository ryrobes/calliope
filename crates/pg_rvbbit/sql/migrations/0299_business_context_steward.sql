-- 0299: Business Context steward and Change Graph.
--
-- The catalog and mirror controller provide frequent change signals, but a
-- full semantic excavation is deliberately expensive.  This migration adds a
-- cheap structural fingerprint per business domain, queues a bounded topology
-- run only when that fingerprint changes, and records a deterministic
-- business-level diff against the currently promoted Value System.

CREATE TABLE IF NOT EXISTS rvbbit.business_context_domains (
    domain_key text PRIMARY KEY,
    status text NOT NULL DEFAULT 'pending_catalog',
    modeled_fingerprint text,
    observed_fingerprint text,
    observed_snapshot jsonb,
    latest_observation_id bigint,
    pending_workflow_run_id uuid
        REFERENCES rvbbit.business_topology_workflow_runs(run_id) ON DELETE SET NULL,
    last_completed_workflow_run_id uuid
        REFERENCES rvbbit.business_topology_workflow_runs(run_id) ON DELETE SET NULL,
    last_change_id uuid,
    dirty_reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    dirty_since timestamptz,
    last_observed_at timestamptz,
    last_modeled_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT business_context_domains_key_check CHECK (
        domain_key ~ '^[A-Za-z_][A-Za-z0-9_$]{0,127}$'
    ),
    CONSTRAINT business_context_domains_status_check CHECK (
        status IN (
            'pending_catalog','clean','dirty','queued','running','review','error'
        )
    ),
    CONSTRAINT business_context_domains_snapshot_check CHECK (
        observed_snapshot IS NULL OR jsonb_typeof(observed_snapshot)='object'
    ),
    CONSTRAINT business_context_domains_reasons_check CHECK (
        jsonb_typeof(dirty_reasons)='array'
    )
);

CREATE INDEX IF NOT EXISTS business_context_domains_steward_idx
    ON rvbbit.business_context_domains(status,dirty_since,updated_at);

CREATE TABLE IF NOT EXISTS rvbbit.business_context_domain_observations (
    observation_id bigserial PRIMARY KEY,
    domain_key text NOT NULL
        REFERENCES rvbbit.business_context_domains(domain_key) ON DELETE CASCADE,
    fingerprint text NOT NULL,
    previous_fingerprint text,
    changed boolean NOT NULL,
    source_type text NOT NULL,
    source_id text,
    relation_count integer NOT NULL,
    column_count integer NOT NULL,
    snapshot jsonb NOT NULL,
    reason jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT business_context_observation_source_check CHECK (
        source_type IN ('seed','mirror','catalog','manual','schedule','workflow')
    ),
    CONSTRAINT business_context_observation_snapshot_check CHECK (
        jsonb_typeof(snapshot)='object' AND jsonb_typeof(reason)='object'
    )
);

CREATE INDEX IF NOT EXISTS business_context_observations_domain_idx
    ON rvbbit.business_context_domain_observations(domain_key,observed_at DESC);

CREATE TABLE IF NOT EXISTS rvbbit.business_context_changes (
    change_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_key text NOT NULL
        REFERENCES rvbbit.business_context_domains(domain_key) ON DELETE CASCADE,
    workflow_run_id uuid NOT NULL
        REFERENCES rvbbit.business_topology_workflow_runs(run_id) ON DELETE RESTRICT,
    from_graph_id uuid
        REFERENCES rvbbit.business_value_graph_proposals(graph_id) ON DELETE RESTRICT,
    to_graph_id uuid NOT NULL UNIQUE
        REFERENCES rvbbit.business_value_graph_proposals(graph_id) ON DELETE RESTRICT,
    observation_id bigint
        REFERENCES rvbbit.business_context_domain_observations(observation_id)
        ON DELETE SET NULL,
    change_kind text NOT NULL,
    severity double precision NOT NULL,
    status text NOT NULL,
    headline text NOT NULL,
    diff jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    reviewed_by text,
    reviewed_at timestamptz,
    CONSTRAINT business_context_changes_kind_check CHECK (
        change_kind IN ('baseline','no_change','structural','business_logic')
    ),
    CONSTRAINT business_context_changes_status_check CHECK (
        status IN ('baseline','observed','pending_review','accepted','dismissed')
    ),
    CONSTRAINT business_context_changes_severity_check CHECK (
        severity BETWEEN 0.0 AND 1.0
    ),
    CONSTRAINT business_context_changes_diff_check CHECK (
        jsonb_typeof(diff)='object'
    )
);

CREATE INDEX IF NOT EXISTS business_context_changes_domain_idx
    ON rvbbit.business_context_changes(domain_key,created_at DESC);

COMMENT ON TABLE rvbbit.business_context_domains IS
    'Cheap per-domain freshness state for the Business Context steward. modeled_fingerprint is the source structure last semantically excavated; observed_fingerprint is current live structure.';
COMMENT ON TABLE rvbbit.business_context_domain_observations IS
    'Append-only structural fingerprint receipts emitted by mirror, catalog, manual, and scheduled Business Context checks.';
COMMENT ON TABLE rvbbit.business_context_changes IS
    'Versioned business-level Change Graph receipts comparing a new Value System proposal with the currently promoted graph.';

CREATE OR REPLACE FUNCTION rvbbit.business_context_domain_snapshot(
    p_domain text
) RETURNS jsonb
LANGUAGE sql STABLE
AS $fn$
    WITH relations AS (
        SELECT c.oid,c.relname,c.relkind,
               obj_description(c.oid,'pg_class') AS description
          FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace
         WHERE n.nspname=btrim(p_domain)
           AND c.relkind IN ('r','p','m')
           AND NOT c.relispartition
           AND rvbbit._business_topology_relation_allowed(n.nspname,c.relname)
    ), packets AS (
        SELECT relation.relname,
               jsonb_strip_nulls(jsonb_build_object(
                   'relation',relation.relname,
                   'kind',relation.relkind,
                   'description',relation.description,
                   'columns',coalesce((
                       SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                           'name',attribute.attname,
                           'type',format_type(attribute.atttypid,attribute.atttypmod),
                           'not_null',attribute.attnotnull,
                           'identity',nullif(attribute.attidentity,''),
                           'generated',nullif(attribute.attgenerated,''),
                           'description',col_description(attribute.attrelid,attribute.attnum)
                       )) ORDER BY attribute.attnum)
                         FROM pg_attribute attribute
                        WHERE attribute.attrelid=relation.oid
                          AND attribute.attnum>0
                          AND NOT attribute.attisdropped
                   ),'[]'::jsonb),
                   'constraints',coalesce((
                       SELECT jsonb_agg(jsonb_build_object(
                           'type',constraint_row.contype,
                           'definition',pg_get_constraintdef(constraint_row.oid,true)
                       ) ORDER BY constraint_row.contype,constraint_row.conname)
                         FROM pg_constraint constraint_row
                        WHERE constraint_row.conrelid=relation.oid
                   ),'[]'::jsonb)
               )) AS packet
          FROM relations relation
    )
    SELECT jsonb_build_object(
        'schema_version','rvbbit.business-context.domain-snapshot.v1',
        'domain_key',btrim(p_domain),
        'relation_count',(SELECT count(*) FROM packets),
        'column_count',(
            SELECT count(*)
              FROM pg_attribute attribute
              JOIN relations relation ON relation.oid=attribute.attrelid
             WHERE attribute.attnum>0 AND NOT attribute.attisdropped
        ),
        'relations',coalesce(
            (SELECT jsonb_agg(packet ORDER BY relname) FROM packets),
            '[]'::jsonb
        )
    )
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_context_domain_fingerprint(
    p_domain text
) RETURNS text
LANGUAGE sql STABLE
AS $fn$
    SELECT md5(rvbbit.business_context_domain_snapshot(p_domain)::text)
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_context_mark_domain_dirty(
    p_domain text,
    p_source_type text DEFAULT 'manual',
    p_source_id text DEFAULT NULL,
    p_reason jsonb DEFAULT '{}'::jsonb
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_domain text := btrim(coalesce(p_domain,''));
    v_reason jsonb;
BEGIN
    IF v_domain !~ '^[A-Za-z_][A-Za-z0-9_$]{0,127}$' THEN
        RAISE EXCEPTION 'invalid Business Context domain %',p_domain;
    END IF;
    IF p_source_type NOT IN ('seed','mirror','catalog','manual','schedule','workflow') THEN
        RAISE EXCEPTION 'invalid Business Context source type %',p_source_type;
    END IF;
    IF p_reason IS NULL OR jsonb_typeof(p_reason)<>'object' THEN
        RAISE EXCEPTION 'Business Context dirty reason must be an object';
    END IF;
    v_reason := p_reason||jsonb_strip_nulls(jsonb_build_object(
        'source_type',p_source_type,
        'source_id',nullif(btrim(coalesce(p_source_id,'')),''),
        'at',clock_timestamp()
    ));
    INSERT INTO rvbbit.business_context_domains(
        domain_key,status,dirty_reasons,dirty_since,updated_at
    ) VALUES (
        v_domain,'pending_catalog',jsonb_build_array(v_reason),
        clock_timestamp(),clock_timestamp()
    )
    ON CONFLICT (domain_key) DO UPDATE SET
        status=CASE
            WHEN rvbbit.business_context_domains.status IN ('queued','running')
                THEN rvbbit.business_context_domains.status
            ELSE 'pending_catalog'
        END,
        dirty_reasons=(
            SELECT coalesce(jsonb_agg(value ORDER BY ordinal),'[]'::jsonb)
              FROM jsonb_array_elements(
                  rvbbit.business_context_domains.dirty_reasons||jsonb_build_array(v_reason)
              ) WITH ORDINALITY item(value,ordinal)
             WHERE ordinal>greatest(
                 jsonb_array_length(rvbbit.business_context_domains.dirty_reasons)+1-20,0
             )
        ),
        dirty_since=coalesce(rvbbit.business_context_domains.dirty_since,clock_timestamp()),
        last_error=NULL,
        updated_at=clock_timestamp();
    RETURN jsonb_build_object(
        'domain_key',v_domain,'status','pending_catalog','reason',v_reason
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_context_observe_domain(
    p_domain text,
    p_source_type text DEFAULT 'manual',
    p_source_id text DEFAULT NULL,
    p_reason jsonb DEFAULT '{}'::jsonb
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_domain text := btrim(coalesce(p_domain,''));
    v_snapshot jsonb;
    v_fingerprint text;
    v_previous text;
    v_changed boolean;
    v_observation_id bigint;
    v_relations integer;
    v_columns integer;
    v_status text;
BEGIN
    IF v_domain !~ '^[A-Za-z_][A-Za-z0-9_$]{0,127}$' THEN
        RAISE EXCEPTION 'invalid Business Context domain %',p_domain;
    END IF;
    IF p_source_type NOT IN ('seed','mirror','catalog','manual','schedule','workflow') THEN
        RAISE EXCEPTION 'invalid Business Context source type %',p_source_type;
    END IF;
    IF p_reason IS NULL OR jsonb_typeof(p_reason)<>'object' THEN
        RAISE EXCEPTION 'Business Context observation reason must be an object';
    END IF;
    v_snapshot := rvbbit.business_context_domain_snapshot(v_domain);
    v_relations := coalesce((v_snapshot->>'relation_count')::integer,0);
    v_columns := coalesce((v_snapshot->>'column_count')::integer,0);
    IF v_relations=0 THEN
        RAISE EXCEPTION 'Business Context domain % has no eligible relations',v_domain;
    END IF;
    v_fingerprint := md5(v_snapshot::text);
    INSERT INTO rvbbit.business_context_domains(domain_key,status,updated_at)
    VALUES (v_domain,'pending_catalog',clock_timestamp())
    ON CONFLICT (domain_key) DO NOTHING;
    SELECT modeled_fingerprint INTO v_previous
      FROM rvbbit.business_context_domains
     WHERE domain_key=v_domain
     FOR UPDATE;
    v_changed := v_previous IS NULL OR v_previous<>v_fingerprint;
    INSERT INTO rvbbit.business_context_domain_observations(
        domain_key,fingerprint,previous_fingerprint,changed,
        source_type,source_id,relation_count,column_count,snapshot,reason
    ) VALUES (
        v_domain,v_fingerprint,v_previous,v_changed,p_source_type,
        nullif(btrim(coalesce(p_source_id,'')),''),v_relations,v_columns,
        v_snapshot,p_reason
    ) RETURNING observation_id INTO v_observation_id;
    UPDATE rvbbit.business_context_domains
       SET observed_fingerprint=v_fingerprint,
           observed_snapshot=v_snapshot,
           latest_observation_id=v_observation_id,
           status=CASE
               WHEN pending_workflow_run_id IS NOT NULL THEN status
               WHEN v_changed THEN 'dirty'
               WHEN EXISTS (
                   SELECT 1 FROM rvbbit.business_context_changes change
                    WHERE change.domain_key=v_domain
                      AND change.status='pending_review'
               ) THEN 'review'
               ELSE 'clean'
           END,
           dirty_since=CASE
               WHEN v_changed THEN coalesce(dirty_since,clock_timestamp())
               ELSE NULL
           END,
           dirty_reasons=CASE WHEN v_changed THEN dirty_reasons ELSE '[]'::jsonb END,
           last_observed_at=clock_timestamp(),last_error=NULL,
           updated_at=clock_timestamp()
     WHERE domain_key=v_domain;
    SELECT status INTO v_status
      FROM rvbbit.business_context_domains
     WHERE domain_key=v_domain;
    RETURN jsonb_build_object(
        'domain_key',v_domain,
        'observation_id',v_observation_id,
        'fingerprint',v_fingerprint,
        'previous_fingerprint',v_previous,
        'changed',v_changed,
        'relation_count',v_relations,
        'column_count',v_columns,
        'status',v_status
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_context_finalize_workflow(
    p_run_id uuid
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_proposal record;
    v_previous record;
    v_state rvbbit.business_context_domains%ROWTYPE;
    v_diff jsonb;
    v_change_id uuid;
    v_change_status text;
    v_count integer := 0;
    v_material integer := 0;
BEGIN
    FOR v_proposal IN
        SELECT proposal.*
          FROM rvbbit.business_value_graph_proposals proposal
         WHERE proposal.workflow_run_id=p_run_id
         ORDER BY proposal.domain_key
    LOOP
        INSERT INTO rvbbit.business_context_domains(domain_key,status,updated_at)
        VALUES (v_proposal.domain_key,'pending_catalog',clock_timestamp())
        ON CONFLICT (domain_key) DO NOTHING;
        SELECT * INTO v_state
          FROM rvbbit.business_context_domains
         WHERE domain_key=v_proposal.domain_key
         FOR UPDATE;
        SELECT proposal.graph_id,proposal.graph,promotion.promoted_at
          INTO v_previous
          FROM rvbbit.business_value_graph_proposals proposal
          JOIN rvbbit.business_value_graph_promotions promotion USING (graph_id)
         WHERE proposal.domain_key=v_proposal.domain_key
           AND proposal.graph_id<>v_proposal.graph_id
         ORDER BY promotion.promoted_at DESC
         LIMIT 1;
        IF v_previous.graph_id IS NULL THEN
            v_diff := jsonb_build_object(
                'schema_version','rvbbit.business-context.change-graph.v1',
                'material_change',false,
                'change_kind','baseline',
                'severity',0.0,
                'headline','Initial Business Value System baseline was established.',
                'sources',jsonb_build_object(
                    'added',coalesce(v_proposal.graph->'source_relations','[]'::jsonb),
                    'removed','[]'::jsonb
                ),
                'metrics',jsonb_build_object(
                    'added',coalesce((
                        SELECT jsonb_agg(value-'event_sql' ORDER BY value->>'metric_key')
                          FROM jsonb_array_elements(
                              coalesce(v_proposal.graph->'metrics','[]'::jsonb)
                          )
                    ),'[]'::jsonb),
                    'removed','[]'::jsonb,'changed','[]'::jsonb
                ),
                'relationships',jsonb_build_object(
                    'added',coalesce(v_proposal.graph->'edges','[]'::jsonb),
                    'removed','[]'::jsonb
                ),
                'source_event_sql_exposed',false
            );
            v_change_status := 'baseline';
        ELSE
            v_diff := rvbbit.business_value_graph_diff(v_previous.graph,v_proposal.graph);
            v_change_status := CASE WHEN (v_diff->>'material_change')::boolean
                THEN 'pending_review' ELSE 'observed' END;
        END IF;
        INSERT INTO rvbbit.business_context_changes(
            domain_key,workflow_run_id,from_graph_id,to_graph_id,
            observation_id,change_kind,severity,status,headline,diff
        ) VALUES (
            v_proposal.domain_key,p_run_id,v_previous.graph_id,v_proposal.graph_id,
            v_state.latest_observation_id,v_diff->>'change_kind',
            (v_diff->>'severity')::double precision,v_change_status,
            v_diff->>'headline',v_diff
        )
        ON CONFLICT (to_graph_id) DO UPDATE SET
            from_graph_id=EXCLUDED.from_graph_id,
            observation_id=EXCLUDED.observation_id,
            change_kind=EXCLUDED.change_kind,
            severity=EXCLUDED.severity,
            status=EXCLUDED.status,
            headline=EXCLUDED.headline,
            diff=EXCLUDED.diff
        RETURNING change_id INTO v_change_id;
        UPDATE rvbbit.business_context_domains
           SET status=CASE WHEN v_change_status='pending_review' THEN 'review' ELSE 'clean' END,
               modeled_fingerprint=coalesce(observed_fingerprint,modeled_fingerprint),
               pending_workflow_run_id=NULL,
               last_completed_workflow_run_id=p_run_id,
               last_change_id=v_change_id,
               dirty_since=NULL,dirty_reasons='[]'::jsonb,
               last_modeled_at=clock_timestamp(),last_error=NULL,
               updated_at=clock_timestamp()
         WHERE domain_key=v_proposal.domain_key;
        v_count := v_count+1;
        IF v_change_status='pending_review' THEN v_material := v_material+1; END IF;
    END LOOP;

    UPDATE rvbbit.business_context_domains domain
       SET status='error',pending_workflow_run_id=NULL,
           last_completed_workflow_run_id=p_run_id,
           last_error='Topology workflow completed without a Value Graph proposal for this domain.',
           updated_at=clock_timestamp()
     WHERE domain.pending_workflow_run_id=p_run_id
       AND NOT EXISTS (
           SELECT 1 FROM rvbbit.business_value_graph_proposals proposal
            WHERE proposal.workflow_run_id=p_run_id
              AND proposal.domain_key=domain.domain_key
       );
    RETURN jsonb_build_object(
        'workflow_run_id',p_run_id,'changes_recorded',v_count,
        'pending_review',v_material
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit._business_context_mirror_finished()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_domain text;
BEGIN
    IF NEW.status IN ('succeeded','partial')
       AND OLD.status IS DISTINCT FROM NEW.status THEN
        SELECT destination_schema INTO v_domain
          FROM rvbbit.mirror_jobs WHERE job_name=NEW.job_name;
        IF v_domain IS NOT NULL THEN
            PERFORM rvbbit.business_context_mark_domain_dirty(
                v_domain,'mirror',NEW.run_id::text,
                jsonb_build_object(
                    'job_name',NEW.job_name,'status',NEW.status,
                    'tables_succeeded',NEW.tables_succeeded,
                    'tables_failed',NEW.tables_failed,'rows_loaded',NEW.rows_loaded
                )
            );
        END IF;
    END IF;
    RETURN NULL;
END
$fn$;

DROP TRIGGER IF EXISTS business_context_mirror_finished ON rvbbit.mirror_runs;
CREATE TRIGGER business_context_mirror_finished
AFTER UPDATE OF status ON rvbbit.mirror_runs
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_context_mirror_finished();

CREATE OR REPLACE FUNCTION rvbbit._business_context_catalog_finished()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_domain text;
BEGIN
    IF NEW.status='ok' AND OLD.status IS DISTINCT FROM NEW.status
       AND NEW.graph_id='db_catalog' THEN
        FOR v_domain IN
            SELECT domain_key FROM (
                SELECT domain.domain_key
                  FROM rvbbit.business_context_domains domain
                UNION
                SELECT job.destination_schema
                  FROM rvbbit.mirror_jobs job
                UNION
                SELECT proposal.domain_key
                  FROM rvbbit.business_value_graph_proposals proposal
            ) candidate(domain_key)
             WHERE NEW.schemas IS NULL OR candidate.domain_key=ANY(NEW.schemas)
             ORDER BY domain_key
        LOOP
            BEGIN
                PERFORM rvbbit.business_context_observe_domain(
                    v_domain,'catalog',NEW.run_id::text,
                    jsonb_build_object('catalog_graph',NEW.graph_id)
                );
            EXCEPTION WHEN OTHERS THEN
                UPDATE rvbbit.business_context_domains
                   SET status='error',last_error=left(SQLERRM,2000),
                       updated_at=clock_timestamp()
                 WHERE domain_key=v_domain;
            END;
        END LOOP;
        PERFORM rvbbit.business_context_steward_tick();
    END IF;
    RETURN NULL;
END
$fn$;

DROP TRIGGER IF EXISTS business_context_catalog_finished ON rvbbit.catalog_runs;
CREATE TRIGGER business_context_catalog_finished
AFTER UPDATE OF status ON rvbbit.catalog_runs
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_context_catalog_finished();

CREATE OR REPLACE FUNCTION rvbbit._business_context_workflow_changed()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN RETURN NULL; END IF;
    IF NEW.status='running' THEN
        UPDATE rvbbit.business_context_domains
           SET status='running',updated_at=clock_timestamp()
         WHERE pending_workflow_run_id=NEW.run_id;
    ELSIF NEW.status='completed' THEN
        PERFORM rvbbit.business_context_finalize_workflow(NEW.run_id);
        PERFORM rvbbit.business_context_steward_tick();
    ELSIF NEW.status IN ('failed','cancelled') THEN
        UPDATE rvbbit.business_context_domains
           SET status='error',pending_workflow_run_id=NULL,
               last_error=left(coalesce(NEW.error,'Topology workflow was cancelled.'),2000),
               updated_at=clock_timestamp()
         WHERE pending_workflow_run_id=NEW.run_id;
        PERFORM rvbbit.business_context_steward_tick();
    END IF;
    RETURN NULL;
END
$fn$;

DROP TRIGGER IF EXISTS business_context_workflow_changed
    ON rvbbit.business_topology_workflow_runs;
CREATE TRIGGER business_context_workflow_changed
AFTER UPDATE OF status ON rvbbit.business_topology_workflow_runs
FOR EACH ROW EXECUTE FUNCTION rvbbit._business_context_workflow_changed();

CREATE OR REPLACE FUNCTION rvbbit.business_context_steward_safety_tick()
RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_domain text;
    v_observed integer := 0;
    v_errors jsonb := '[]'::jsonb;
    v_tick jsonb;
BEGIN
    FOR v_domain IN
        SELECT domain_key FROM (
            SELECT domain.domain_key FROM rvbbit.business_context_domains domain
            UNION SELECT job.destination_schema FROM rvbbit.mirror_jobs job
            UNION SELECT proposal.domain_key FROM rvbbit.business_value_graph_proposals proposal
        ) candidate(domain_key)
        ORDER BY domain_key
    LOOP
        BEGIN
            PERFORM rvbbit.business_context_observe_domain(
                v_domain,'schedule',NULL,jsonb_build_object('safety_net',true)
            );
            v_observed := v_observed+1;
        EXCEPTION WHEN OTHERS THEN
            v_errors := v_errors||jsonb_build_array(jsonb_build_object(
                'domain_key',v_domain,'error',left(SQLERRM,500)
            ));
        END;
    END LOOP;
    v_tick := rvbbit.business_context_steward_tick();
    RETURN jsonb_build_object(
        'domains_observed',v_observed,'errors',v_errors,'steward',v_tick
    );
END
$fn$;

-- Existing Value Systems become the initial modeled baseline. New mirrored
-- schemas intentionally remain unmodeled so their first catalog completion
-- queues an excavation.
DO $seed$
DECLARE
    v_domain text;
    v_observation jsonb;
    v_run_id uuid;
BEGIN
    FOR v_domain IN
        SELECT DISTINCT domain_key
          FROM rvbbit.business_value_graph_proposals
         ORDER BY domain_key
    LOOP
        BEGIN
            IF EXISTS (
                SELECT 1 FROM rvbbit.business_context_domains domain
                 WHERE domain.domain_key=v_domain
                   AND domain.modeled_fingerprint IS NOT NULL
            ) THEN
                CONTINUE;
            END IF;
            v_observation := rvbbit.business_context_observe_domain(
                v_domain,'seed',NULL,jsonb_build_object('migration','0299')
            );
            UPDATE rvbbit.business_context_domains
               SET modeled_fingerprint=observed_fingerprint,status='clean',
                   dirty_since=NULL,dirty_reasons='[]'::jsonb,
                   last_modeled_at=clock_timestamp(),updated_at=clock_timestamp()
             WHERE domain_key=v_domain;
            UPDATE rvbbit.business_context_domain_observations
               SET previous_fingerprint=fingerprint,changed=false
             WHERE observation_id=(v_observation->>'observation_id')::bigint;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Business Context baseline for % deferred: %',v_domain,SQLERRM;
        END;
    END LOOP;
    FOR v_run_id IN
        SELECT DISTINCT proposal.workflow_run_id
          FROM rvbbit.business_value_graph_promotions promotion
          JOIN rvbbit.business_value_graph_proposals proposal USING (graph_id)
         WHERE NOT EXISTS (
             SELECT 1 FROM rvbbit.business_context_changes change
              WHERE change.to_graph_id=proposal.graph_id
         )
         ORDER BY proposal.workflow_run_id
    LOOP
        PERFORM rvbbit.business_context_finalize_workflow(v_run_id);
    END LOOP;
END
$seed$;

INSERT INTO rvbbit.capability_mcp_tools(name,summary) VALUES
(
    'business_context_status',
    'Inspect Business Context freshness by domain: modeled versus observed structural fingerprints, mirror or catalog dirty reasons, active topology work, pending review, and the latest Change Graph receipt.'
),
(
    'refresh_business_context',
    'Observe one company-data domain and enqueue a bounded semantic topology refresh only when its structural fingerprint changed. Pass force=true to excavate even when the cheap fingerprint is unchanged.'
),
(
    'business_context_changes',
    'Read versioned business-level Change Graph receipts: added or removed source relations, governed metrics, dimensions, formulas, grains, time semantics, and value relationships, without exposing retained source SQL.'
)
ON CONFLICT (name) DO UPDATE SET summary=EXCLUDED.summary;

DO $capabilities$
DECLARE
    v_rec record;
    v_doc text;
    v_node bigint;
BEGIN
    FOR v_rec IN
        SELECT name,summary FROM rvbbit.capability_mcp_tools
         WHERE name IN (
             'business_context_status','refresh_business_context','business_context_changes'
         )
    LOOP
        v_doc := rvbbit.capability_doc(
            'cap_mcp_tool',v_rec.name,'Warehouse MCP connector tool (external agents)',
            v_rec.summary||' NOT a local SQL function - exposed to agents through the Warehouse MCP connector.',
            '','varies',ARRAY['mcp','business-context','change-graph','steward']
        );
        v_node := rvbbit.kg_assert_node(
            'cap_mcp_tool',v_rec.name,jsonb_build_object('search_doc',v_doc),
            1.0,'',0.0,'rvbbit_capabilities'
        );
        INSERT INTO rvbbit.catalog_docs(
            node_id,graph_id,kind,schema_name,rel_name,col_name,
            doc,embedding,embedded_at,updated_at
        ) VALUES (
            v_node,'rvbbit_capabilities','cap_mcp_tool',NULL,v_rec.name,NULL,
            v_doc,NULL,NULL,clock_timestamp()
        ) ON CONFLICT (graph_id,node_id) DO UPDATE SET
            kind=EXCLUDED.kind,rel_name=EXCLUDED.rel_name,doc=EXCLUDED.doc,
            embedding=NULL,embedded_at=NULL,updated_at=clock_timestamp();
    END LOOP;
END
$capabilities$;

CREATE OR REPLACE FUNCTION rvbbit.business_value_graph_diff(
    p_from_graph jsonb,
    p_to_graph jsonb
) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE
AS $fn$
DECLARE
    v_sources_added jsonb;
    v_sources_removed jsonb;
    v_metrics_added jsonb;
    v_metrics_removed jsonb;
    v_metric_changes jsonb;
    v_edges_added jsonb;
    v_edges_removed jsonb;
    v_material boolean;
    v_logic boolean;
    v_severity double precision := 0.0;
    v_kind text;
    v_headline text;
    v_change_count integer;
BEGIN
    IF p_to_graph IS NULL OR jsonb_typeof(p_to_graph)<>'object' THEN
        RAISE EXCEPTION 'to_graph must be a Business Value Graph object';
    END IF;

    WITH old_values AS (
        SELECT value FROM jsonb_array_elements_text(
            coalesce(p_from_graph->'source_relations','[]'::jsonb)
        ) value
    ), new_values AS (
        SELECT value FROM jsonb_array_elements_text(
            coalesce(p_to_graph->'source_relations','[]'::jsonb)
        ) value
    )
    SELECT
        coalesce((SELECT jsonb_agg(value ORDER BY value) FROM new_values
                   WHERE value NOT IN (SELECT value FROM old_values)),'[]'::jsonb),
        coalesce((SELECT jsonb_agg(value ORDER BY value) FROM old_values
                   WHERE value NOT IN (SELECT value FROM new_values)),'[]'::jsonb)
      INTO v_sources_added,v_sources_removed;

    WITH old_metrics AS (
        SELECT value->>'metric_key' AS metric_key,value
          FROM jsonb_array_elements(coalesce(p_from_graph->'metrics','[]'::jsonb))
    ), new_metrics AS (
        SELECT value->>'metric_key' AS metric_key,value
          FROM jsonb_array_elements(coalesce(p_to_graph->'metrics','[]'::jsonb))
    )
    SELECT
        coalesce((SELECT jsonb_agg(jsonb_build_object(
                    'metric_key',metric_key,'name',value->>'name'
                ) ORDER BY metric_key)
                    FROM new_metrics WHERE metric_key NOT IN (
                        SELECT metric_key FROM old_metrics
                    )),'[]'::jsonb),
        coalesce((SELECT jsonb_agg(jsonb_build_object(
                    'metric_key',metric_key,'name',value->>'name'
                ) ORDER BY metric_key)
                    FROM old_metrics WHERE metric_key NOT IN (
                        SELECT metric_key FROM new_metrics
                    )),'[]'::jsonb)
      INTO v_metrics_added,v_metrics_removed;

    WITH old_metrics AS (
        SELECT value->>'metric_key' AS metric_key,value
          FROM jsonb_array_elements(coalesce(p_from_graph->'metrics','[]'::jsonb))
    ), new_metrics AS (
        SELECT value->>'metric_key' AS metric_key,value
          FROM jsonb_array_elements(coalesce(p_to_graph->'metrics','[]'::jsonb))
    ), shared AS (
        SELECT old.metric_key,old.value AS old_metric,new.value AS new_metric
          FROM old_metrics old JOIN new_metrics new USING (metric_key)
    ), changed AS (
        SELECT metric_key,
               coalesce(new_metric->>'name',old_metric->>'name',metric_key) AS name,
               jsonb_strip_nulls(jsonb_build_object(
                   'formula',CASE WHEN old_metric->>'formula' IS DISTINCT FROM new_metric->>'formula'
                       THEN jsonb_build_object('from',old_metric->>'formula','to',new_metric->>'formula') END,
                   'unit',CASE WHEN old_metric->>'unit' IS DISTINCT FROM new_metric->>'unit'
                       THEN jsonb_build_object('from',old_metric->>'unit','to',new_metric->>'unit') END,
                   'grain',CASE WHEN old_metric->>'grain' IS DISTINCT FROM new_metric->>'grain'
                       THEN jsonb_build_object('from',old_metric->>'grain','to',new_metric->>'grain') END,
                   'time_semantics',CASE WHEN old_metric->>'time_semantics' IS DISTINCT FROM new_metric->>'time_semantics'
                       THEN jsonb_build_object('from',old_metric->>'time_semantics','to',new_metric->>'time_semantics') END,
                   'event_logic_changed',CASE WHEN md5(coalesce(old_metric->>'event_sql',''))
                           IS DISTINCT FROM md5(coalesce(new_metric->>'event_sql',''))
                       THEN true END,
                   'dimensions_added',(
                       SELECT coalesce(jsonb_agg(jsonb_build_object(
                           'key',dimension->>'key','name',dimension->>'name'
                       ) ORDER BY dimension->>'key'),'[]'::jsonb)
                         FROM jsonb_array_elements(coalesce(new_metric->'dimensions','[]'::jsonb)) dimension
                        WHERE dimension->>'key' NOT IN (
                            SELECT prior->>'key' FROM jsonb_array_elements(
                                coalesce(old_metric->'dimensions','[]'::jsonb)
                            ) prior
                        )
                   ),
                   'dimensions_removed',(
                       SELECT coalesce(jsonb_agg(jsonb_build_object(
                           'key',dimension->>'key','name',dimension->>'name'
                       ) ORDER BY dimension->>'key'),'[]'::jsonb)
                         FROM jsonb_array_elements(coalesce(old_metric->'dimensions','[]'::jsonb)) dimension
                        WHERE dimension->>'key' NOT IN (
                            SELECT current->>'key' FROM jsonb_array_elements(
                                coalesce(new_metric->'dimensions','[]'::jsonb)
                            ) current
                        )
                   )
               )) AS changes
          FROM shared
    )
    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'metric_key',metric_key,'name',name,'changes',changes
           ) ORDER BY metric_key) FILTER (
               WHERE changes<>'{"dimensions_added": [], "dimensions_removed": []}'::jsonb
           ),'[]'::jsonb)
      INTO v_metric_changes
      FROM changed;

    WITH old_edges AS (
        SELECT value FROM jsonb_array_elements(coalesce(p_from_graph->'edges','[]'::jsonb))
    ), new_edges AS (
        SELECT value FROM jsonb_array_elements(coalesce(p_to_graph->'edges','[]'::jsonb))
    )
    SELECT
        coalesce((SELECT jsonb_agg(value ORDER BY value::text) FROM new_edges
                   WHERE NOT EXISTS (SELECT 1 FROM old_edges WHERE old_edges.value=new_edges.value)),'[]'::jsonb),
        coalesce((SELECT jsonb_agg(value ORDER BY value::text) FROM old_edges
                   WHERE NOT EXISTS (SELECT 1 FROM new_edges WHERE new_edges.value=old_edges.value)),'[]'::jsonb)
      INTO v_edges_added,v_edges_removed;

    v_material := jsonb_array_length(v_sources_added)>0
        OR jsonb_array_length(v_sources_removed)>0
        OR jsonb_array_length(v_metrics_added)>0
        OR jsonb_array_length(v_metrics_removed)>0
        OR jsonb_array_length(v_metric_changes)>0
        OR jsonb_array_length(v_edges_added)>0
        OR jsonb_array_length(v_edges_removed)>0;
    v_logic := jsonb_array_length(v_metrics_added)>0
        OR jsonb_array_length(v_metrics_removed)>0
        OR jsonb_array_length(v_metric_changes)>0
        OR jsonb_array_length(v_edges_added)>0
        OR jsonb_array_length(v_edges_removed)>0;
    v_change_count := jsonb_array_length(v_sources_added)
        +jsonb_array_length(v_sources_removed)
        +jsonb_array_length(v_metrics_added)
        +jsonb_array_length(v_metrics_removed)
        +jsonb_array_length(v_metric_changes)
        +jsonb_array_length(v_edges_added)
        +jsonb_array_length(v_edges_removed);
    IF NOT v_material THEN
        v_kind := 'no_change';
        v_headline := 'No material business-model change was found.';
        v_severity := 0.0;
    ELSIF v_logic THEN
        v_kind := 'business_logic';
        v_headline := format(
            'Business logic changed across %s metric, contributor, source, or value relationship item%s.',
            v_change_count,CASE WHEN v_change_count=1 THEN '' ELSE 's' END
        );
        v_severity := least(1.0,0.55+0.07*v_change_count);
    ELSE
        v_kind := 'structural';
        v_headline := format(
            'The Value System source boundary changed across %s relation%s; governed metric logic is unchanged.',
            v_change_count,CASE WHEN v_change_count=1 THEN '' ELSE 's' END
        );
        v_severity := least(0.7,0.3+0.08*v_change_count);
    END IF;
    IF jsonb_array_length(v_metrics_removed)>0 THEN
        v_severity := greatest(v_severity,0.9);
    END IF;
    RETURN jsonb_build_object(
        'schema_version','rvbbit.business-context.change-graph.v1',
        'material_change',v_material,
        'change_kind',v_kind,
        'severity',v_severity,
        'headline',v_headline,
        'sources',jsonb_build_object('added',v_sources_added,'removed',v_sources_removed),
        'metrics',jsonb_build_object(
            'added',v_metrics_added,'removed',v_metrics_removed,'changed',v_metric_changes
        ),
        'relationships',jsonb_build_object('added',v_edges_added,'removed',v_edges_removed),
        'source_event_sql_exposed',false
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_context_steward_tick(
    p_domain text DEFAULT NULL,
    p_force boolean DEFAULT false
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_domain rvbbit.business_context_domains%ROWTYPE;
    v_active uuid;
    v_run_id uuid;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rvbbit.business-context-steward',0));
    SELECT run_id INTO v_active
      FROM rvbbit.business_topology_workflow_runs
     WHERE status IN ('queued','running','cancel_requested')
     ORDER BY requested_at LIMIT 1;
    IF v_active IS NOT NULL THEN
        RETURN jsonb_build_object('status','busy','active_workflow_run_id',v_active);
    END IF;
    SELECT * INTO v_domain
      FROM rvbbit.business_context_domains
     WHERE status='dirty'
       AND pending_workflow_run_id IS NULL
       AND (p_domain IS NULL OR domain_key=btrim(p_domain))
     ORDER BY dirty_since NULLS LAST,updated_at
     LIMIT 1 FOR UPDATE;
    IF NOT FOUND AND p_force AND nullif(btrim(coalesce(p_domain,'')),'') IS NOT NULL THEN
        PERFORM rvbbit.business_context_mark_domain_dirty(
            btrim(p_domain),'manual',NULL,jsonb_build_object('force',true)
        );
        PERFORM rvbbit.business_context_observe_domain(
            btrim(p_domain),'manual',NULL,jsonb_build_object('force',true)
        );
        UPDATE rvbbit.business_context_domains
           SET status='dirty',dirty_since=coalesce(dirty_since,clock_timestamp()),
               updated_at=clock_timestamp()
         WHERE domain_key=btrim(p_domain);
        SELECT * INTO v_domain FROM rvbbit.business_context_domains
         WHERE domain_key=btrim(p_domain) FOR UPDATE;
    END IF;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('status','idle','message','No structurally dirty Business Context domain needs excavation.');
    END IF;
    v_run_id := rvbbit.business_topology_start_workflow(
        ARRAY[v_domain.domain_key],NULL,2048,100,500,128,
        'clover_llm','clover',false,
        'Steward refresh - '||v_domain.domain_key||' - '||to_char(clock_timestamp(),'YYYY-MM-DD HH24:MI')
    );
    UPDATE rvbbit.business_context_domains
       SET status='queued',pending_workflow_run_id=v_run_id,last_error=NULL,
           updated_at=clock_timestamp()
     WHERE domain_key=v_domain.domain_key;
    RETURN jsonb_build_object(
        'status','queued','domain_key',v_domain.domain_key,
        'workflow_run_id',v_run_id,
        'observed_fingerprint',v_domain.observed_fingerprint,
        'modeled_fingerprint',v_domain.modeled_fingerprint
    );
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.refresh_business_context(
    p_domain text,
    p_force boolean DEFAULT false
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_observation jsonb;
    v_tick jsonb;
BEGIN
    PERFORM rvbbit.business_context_mark_domain_dirty(
        p_domain,'manual',NULL,jsonb_build_object('requested_by',session_user,'force',p_force)
    );
    v_observation := rvbbit.business_context_observe_domain(
        p_domain,'manual',NULL,jsonb_build_object('requested_by',session_user,'force',p_force)
    );
    v_tick := rvbbit.business_context_steward_tick(p_domain,p_force);
    RETURN jsonb_build_object('observation',v_observation,'steward',v_tick);
END
$fn$;

COMMENT ON FUNCTION rvbbit.business_context_steward_tick(text,boolean) IS
    'Queue at most one structurally dirty Business Context domain using the existing durable bounded topology workflow.';
COMMENT ON FUNCTION rvbbit.business_context_steward_safety_tick() IS
    'Cheap scheduled safety net: observe every known business domain and queue at most one changed domain.';
COMMENT ON FUNCTION rvbbit.business_value_graph_diff(jsonb,jsonb) IS
    'Deterministic business-level graph diff. Source event SQL is compared by hash and never returned.';
