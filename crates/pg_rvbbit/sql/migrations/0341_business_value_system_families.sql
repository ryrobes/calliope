-- 0341: reconcile repeated Business Topology excavations into durable Value
-- System families.  A reconciled proposal may be used as provisional semantic
-- context without forcing a human review.  Only one material conflict with an
-- existing canonical promotion may occupy the decision lane for a family.

CREATE TABLE IF NOT EXISTS rvbbit.business_value_system_families (
    family_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_key text NOT NULL UNIQUE,
    active_graph_id uuid REFERENCES rvbbit.business_value_graph_proposals(graph_id) ON DELETE SET NULL,
    canonical_graph_id uuid REFERENCES rvbbit.business_value_graph_proposals(graph_id) ON DELETE SET NULL,
    pending_graph_id uuid REFERENCES rvbbit.business_value_graph_proposals(graph_id) ON DELETE SET NULL,
    revision integer NOT NULL DEFAULT 0 CHECK (revision>=0),
    observation_count integer NOT NULL DEFAULT 0 CHECK (observation_count>=0),
    review_state text NOT NULL DEFAULT 'provisional'
        CHECK (review_state IN ('provisional','stable','needs_decision')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

ALTER TABLE rvbbit.business_value_graph_proposals
    ADD COLUMN IF NOT EXISTS family_id uuid
        REFERENCES rvbbit.business_value_system_families(family_id) ON DELETE SET NULL;
ALTER TABLE rvbbit.business_value_graph_proposals
    ADD COLUMN IF NOT EXISTS family_revision integer;
ALTER TABLE rvbbit.business_value_graph_proposals
    ADD COLUMN IF NOT EXISTS semantic_fingerprint text;
ALTER TABLE rvbbit.business_value_graph_proposals
    ADD COLUMN IF NOT EXISTS predecessor_graph_id uuid
        REFERENCES rvbbit.business_value_graph_proposals(graph_id) ON DELETE SET NULL;
ALTER TABLE rvbbit.business_value_graph_proposals
    ADD COLUMN IF NOT EXISTS usable boolean NOT NULL DEFAULT false;

CREATE UNIQUE INDEX IF NOT EXISTS business_value_graph_family_revision_idx
    ON rvbbit.business_value_graph_proposals(family_id,family_revision)
    WHERE family_id IS NOT NULL AND family_revision IS NOT NULL;
CREATE INDEX IF NOT EXISTS business_value_graph_family_status_idx
    ON rvbbit.business_value_graph_proposals(family_id,status,updated_at DESC);

CREATE OR REPLACE FUNCTION rvbbit.business_value_graph_semantic_fingerprint(
    p_graph jsonb
) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $fn$
    SELECT md5(jsonb_build_object(
        'sources',coalesce((
            SELECT jsonb_agg(lower(btrim(value)) ORDER BY lower(btrim(value)))
              FROM jsonb_array_elements_text(coalesce(p_graph->'source_relations','[]'::jsonb)) value
        ),'[]'::jsonb),
        'metrics',coalesce((
            SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                'kind',lower(metric->>'metric_kind'),
                'unit',lower(metric->>'unit'),
                'event_sql',regexp_replace(lower(btrim(metric->>'event_sql')),'\\s+',' ','g'),
                'grain',lower(btrim(metric->>'grain')),
                'time',lower(btrim(metric->>'time_semantics')),
                'dimensions',coalesce((
                    SELECT jsonb_agg(lower(coalesce(dimension->>'key',dimension->>'name'))
                                     ORDER BY lower(coalesce(dimension->>'key',dimension->>'name')))
                      FROM jsonb_array_elements(coalesce(metric->'dimensions','[]'::jsonb)) dimension
                ),'[]'::jsonb)
            )) ORDER BY regexp_replace(lower(btrim(metric->>'event_sql')),'\\s+',' ','g'))
              FROM jsonb_array_elements(coalesce(p_graph->'metrics','[]'::jsonb)) metric
        ),'[]'::jsonb),
        'edges',coalesce((
            SELECT jsonb_agg(jsonb_build_object(
                'relationship',lower(edge->>'relationship'),
                'parent_sql',coalesce((
                    SELECT regexp_replace(lower(btrim(metric->>'event_sql')),'\\s+',' ','g')
                      FROM jsonb_array_elements(coalesce(p_graph->'metrics','[]'::jsonb)) metric
                     WHERE metric->>'metric_key'=edge->>'parent_metric_key' LIMIT 1
                ),''),
                'child_sql',coalesce((
                    SELECT regexp_replace(lower(btrim(metric->>'event_sql')),'\\s+',' ','g')
                      FROM jsonb_array_elements(coalesce(p_graph->'metrics','[]'::jsonb)) metric
                     WHERE metric->>'metric_key'=edge->>'child_metric_key' LIMIT 1
                ),'')
            ) ORDER BY edge::text)
              FROM jsonb_array_elements(coalesce(p_graph->'edges','[]'::jsonb)) edge
        ),'[]'::jsonb)
    )::text)
$fn$;

COMMENT ON FUNCTION rvbbit.business_value_graph_semantic_fingerprint(jsonb) IS
    'Stable Value System identity over executable semantics. Display names, metric aliases, and cube time buckets do not create a new system.';

-- Backfill one family per currently modeled domain. The worker currently emits
-- one bounded business constellation per domain; grains remain metric/cube
-- projections inside that family rather than identities of their own.
INSERT INTO rvbbit.business_value_system_families(domain_key)
SELECT DISTINCT domain_key
  FROM rvbbit.business_value_graph_proposals
ON CONFLICT (domain_key) DO NOTHING;

WITH ranked AS (
    SELECT proposal.graph_id,family.family_id,
           row_number() OVER (
               PARTITION BY proposal.domain_key
               ORDER BY proposal.proposed_at,proposal.graph_id
           )::integer AS revision
      FROM rvbbit.business_value_graph_proposals proposal
      JOIN rvbbit.business_value_system_families family USING (domain_key)
)
UPDATE rvbbit.business_value_graph_proposals proposal
   SET family_id=ranked.family_id,
       family_revision=ranked.revision,
       semantic_fingerprint=rvbbit.business_value_graph_semantic_fingerprint(proposal.graph)
  FROM ranked
 WHERE ranked.graph_id=proposal.graph_id;

WITH canonical AS (
    SELECT DISTINCT ON (proposal.domain_key)
           proposal.domain_key,proposal.graph_id
      FROM rvbbit.business_value_graph_proposals proposal
      JOIN rvbbit.business_value_graph_promotions promotion USING (graph_id)
     ORDER BY proposal.domain_key,promotion.promoted_at DESC
), latest_candidate AS (
    SELECT DISTINCT ON (proposal.domain_key)
           proposal.domain_key,proposal.graph_id,proposal.semantic_fingerprint
      FROM rvbbit.business_value_graph_proposals proposal
     WHERE proposal.status IN ('proposed','approved','needs_revision')
       AND NOT EXISTS (
           SELECT 1 FROM rvbbit.business_value_graph_promotions promotion
            WHERE promotion.graph_id=proposal.graph_id
       )
     ORDER BY proposal.domain_key,proposal.updated_at DESC,proposal.graph_id DESC
), chosen AS (
    SELECT family.family_id,family.domain_key,canonical.graph_id AS canonical_graph_id,
           candidate.graph_id AS candidate_graph_id,
           CASE
             WHEN canonical.graph_id IS NULL THEN candidate.graph_id
             ELSE canonical.graph_id
           END AS active_graph_id,
           CASE
             WHEN canonical.graph_id IS NOT NULL
              AND candidate.graph_id IS NOT NULL
              AND candidate.semantic_fingerprint IS DISTINCT FROM promoted.semantic_fingerprint
             THEN candidate.graph_id
           END AS pending_graph_id
      FROM rvbbit.business_value_system_families family
      LEFT JOIN canonical USING (domain_key)
      LEFT JOIN latest_candidate candidate USING (domain_key)
      LEFT JOIN rvbbit.business_value_graph_proposals promoted
        ON promoted.graph_id=canonical.graph_id
)
UPDATE rvbbit.business_value_system_families family
   SET canonical_graph_id=chosen.canonical_graph_id,
       active_graph_id=chosen.active_graph_id,
       pending_graph_id=chosen.pending_graph_id,
       revision=coalesce((
           SELECT max(proposal.family_revision)
             FROM rvbbit.business_value_graph_proposals proposal
            WHERE proposal.family_id=family.family_id
       ),0),
       observation_count=coalesce((
           SELECT count(*)::integer
             FROM rvbbit.business_value_graph_proposals proposal
            WHERE proposal.family_id=family.family_id
       ),0),
       review_state=CASE
           WHEN chosen.pending_graph_id IS NOT NULL THEN 'needs_decision'
           WHEN chosen.canonical_graph_id IS NOT NULL THEN 'stable'
           ELSE 'provisional'
       END,
       updated_at=clock_timestamp()
  FROM chosen
 WHERE chosen.family_id=family.family_id;

UPDATE rvbbit.business_value_graph_proposals proposal
   SET usable=(proposal.graph_id IN (
       SELECT active_graph_id FROM rvbbit.business_value_system_families WHERE active_graph_id IS NOT NULL
       UNION
       SELECT canonical_graph_id FROM rvbbit.business_value_system_families WHERE canonical_graph_id IS NOT NULL
       UNION
       SELECT pending_graph_id FROM rvbbit.business_value_system_families WHERE pending_graph_id IS NOT NULL
   )),
       status=CASE
           WHEN proposal.status IN ('proposed','approved','needs_revision')
            AND proposal.graph_id NOT IN (
                SELECT coalesce(active_graph_id,'00000000-0000-0000-0000-000000000000'::uuid)
                  FROM rvbbit.business_value_system_families
                UNION
                SELECT coalesce(pending_graph_id,'00000000-0000-0000-0000-000000000000'::uuid)
                  FROM rvbbit.business_value_system_families
            ) THEN 'superseded'
           ELSE proposal.status
       END;

-- Collapse legacy review noise to the one current candidate per family.
UPDATE rvbbit.business_context_changes change
   SET status='dismissed',reviewed_at=coalesce(reviewed_at,clock_timestamp()),
       reviewed_by=coalesce(reviewed_by,'calliope-family-reconciliation')
 WHERE change.status='pending_review'
   AND change.to_graph_id NOT IN (
       SELECT pending_graph_id FROM rvbbit.business_value_system_families
        WHERE pending_graph_id IS NOT NULL
   );

CREATE OR REPLACE FUNCTION rvbbit.business_context_finalize_workflow(
    p_run_id uuid
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_proposal record;
    v_previous record;
    v_canonical record;
    v_family rvbbit.business_value_system_families%ROWTYPE;
    v_state rvbbit.business_context_domains%ROWTYPE;
    v_diff jsonb;
    v_change_id uuid;
    v_change_status text;
    v_fingerprint text;
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

        INSERT INTO rvbbit.business_value_system_families(domain_key)
        VALUES (v_proposal.domain_key)
        ON CONFLICT (domain_key) DO NOTHING;
        SELECT * INTO v_family
          FROM rvbbit.business_value_system_families
         WHERE domain_key=v_proposal.domain_key
         FOR UPDATE;

        SELECT proposal.graph_id,proposal.graph,proposal.semantic_fingerprint
          INTO v_canonical
          FROM rvbbit.business_value_graph_proposals proposal
          JOIN rvbbit.business_value_graph_promotions promotion USING (graph_id)
         WHERE proposal.domain_key=v_proposal.domain_key
           AND proposal.graph_id<>v_proposal.graph_id
         ORDER BY promotion.promoted_at DESC
         LIMIT 1;
        IF v_canonical.graph_id IS NOT NULL THEN
            UPDATE rvbbit.business_value_system_families
               SET canonical_graph_id=v_canonical.graph_id,
                   active_graph_id=coalesce(active_graph_id,v_canonical.graph_id),
                   updated_at=clock_timestamp()
             WHERE family_id=v_family.family_id;
            v_family.canonical_graph_id := v_canonical.graph_id;
            IF v_family.active_graph_id IS NULL THEN
                v_family.active_graph_id := v_canonical.graph_id;
            END IF;
        END IF;

        v_fingerprint := rvbbit.business_value_graph_semantic_fingerprint(v_proposal.graph);
        UPDATE rvbbit.business_value_graph_proposals
           SET family_id=v_family.family_id,
               family_revision=coalesce(family_revision,v_family.revision+1),
               semantic_fingerprint=v_fingerprint
         WHERE graph_id=v_proposal.graph_id;

        SELECT proposal.graph_id,proposal.graph,proposal.semantic_fingerprint,proposal.status
          INTO v_previous
          FROM rvbbit.business_value_graph_proposals proposal
         WHERE proposal.graph_id=coalesce(v_family.pending_graph_id,v_family.active_graph_id)
           AND proposal.graph_id<>v_proposal.graph_id;

        IF v_previous.graph_id IS NOT NULL
           AND v_previous.semantic_fingerprint=v_fingerprint THEN
            UPDATE rvbbit.business_value_graph_proposals
               SET status='superseded',usable=false,
                   predecessor_graph_id=v_previous.graph_id,
                   review_reason='Semantically identical repeat excavation; retained as a receipt.',
                   updated_at=clock_timestamp()
             WHERE graph_id=v_proposal.graph_id;
            UPDATE rvbbit.business_value_system_families
               SET revision=greatest(revision,coalesce(v_proposal.family_revision,revision+1)),
                   observation_count=observation_count+1,
                   updated_at=clock_timestamp()
             WHERE family_id=v_family.family_id;
            v_diff := jsonb_build_object(
                'schema_version','rvbbit.business-context.change-graph.v1',
                'material_change',false,'change_kind','no_change','severity',0.0,
                'headline','The existing Value System was observed again with the same executable semantics.',
                'sources',jsonb_build_object('added','[]'::jsonb,'removed','[]'::jsonb),
                'metrics',jsonb_build_object('added','[]'::jsonb,'removed','[]'::jsonb,'changed','[]'::jsonb),
                'relationships',jsonb_build_object('added','[]'::jsonb,'removed','[]'::jsonb),
                'source_event_sql_exposed',false
            );
            v_change_status := 'observed';
        ELSE
            IF v_canonical.graph_id IS NOT NULL THEN
                v_diff := rvbbit.business_value_graph_diff(v_canonical.graph,v_proposal.graph);
            ELSIF v_previous.graph_id IS NOT NULL THEN
                v_diff := rvbbit.business_value_graph_diff(v_previous.graph,v_proposal.graph);
            ELSE
                v_diff := jsonb_build_object(
                    'schema_version','rvbbit.business-context.change-graph.v1',
                    'material_change',false,'change_kind','baseline','severity',0.0,
                    'headline','Initial reconciled Value System became provisionally usable.',
                    'sources',jsonb_build_object(
                        'added',coalesce(v_proposal.graph->'source_relations','[]'::jsonb),
                        'removed','[]'::jsonb
                    ),
                    'metrics',jsonb_build_object(
                        'added',coalesce((
                            SELECT jsonb_agg(value-'event_sql' ORDER BY value->>'metric_key')
                              FROM jsonb_array_elements(coalesce(v_proposal.graph->'metrics','[]'::jsonb))
                        ),'[]'::jsonb),
                        'removed','[]'::jsonb,'changed','[]'::jsonb
                    ),
                    'relationships',jsonb_build_object(
                        'added',coalesce(v_proposal.graph->'edges','[]'::jsonb),
                        'removed','[]'::jsonb
                    ),
                    'source_event_sql_exposed',false
                );
            END IF;

            IF v_canonical.graph_id IS NOT NULL
               AND (v_diff->>'material_change')::boolean THEN
                IF v_family.pending_graph_id IS NOT NULL
                   AND v_family.pending_graph_id<>v_proposal.graph_id THEN
                    UPDATE rvbbit.business_value_graph_proposals
                       SET status='superseded',usable=false,
                           review_reason='Replaced by a newer family revision.',
                           updated_at=clock_timestamp()
                     WHERE graph_id=v_family.pending_graph_id
                       AND status<>'promoted';
                    UPDATE rvbbit.business_context_changes
                       SET status='dismissed',reviewed_at=clock_timestamp(),
                           reviewed_by='calliope-family-reconciliation'
                     WHERE to_graph_id=v_family.pending_graph_id
                       AND status='pending_review';
                END IF;
                UPDATE rvbbit.business_value_graph_proposals
                   SET status='proposed',usable=true,
                       predecessor_graph_id=v_canonical.graph_id,
                       updated_at=clock_timestamp()
                 WHERE graph_id=v_proposal.graph_id;
                UPDATE rvbbit.business_value_system_families
                   SET canonical_graph_id=v_canonical.graph_id,
                       active_graph_id=v_canonical.graph_id,
                       pending_graph_id=v_proposal.graph_id,
                       revision=greatest(revision,coalesce(v_proposal.family_revision,revision+1)),
                       observation_count=observation_count+1,
                       review_state='needs_decision',updated_at=clock_timestamp()
                 WHERE family_id=v_family.family_id;
                v_change_status := 'pending_review';
            ELSE
                IF v_previous.graph_id IS NOT NULL
                   AND v_previous.status<>'promoted' THEN
                    UPDATE rvbbit.business_value_graph_proposals
                       SET status='superseded',usable=false,
                           review_reason='Superseded by a newer reconciled family revision.',
                           updated_at=clock_timestamp()
                     WHERE graph_id=v_previous.graph_id;
                END IF;
                UPDATE rvbbit.business_value_graph_proposals
                   SET status='proposed',usable=true,
                       predecessor_graph_id=coalesce(v_previous.graph_id,v_canonical.graph_id),
                       updated_at=clock_timestamp()
                 WHERE graph_id=v_proposal.graph_id;
                UPDATE rvbbit.business_value_system_families
                   SET active_graph_id=v_proposal.graph_id,pending_graph_id=NULL,
                       canonical_graph_id=coalesce(v_canonical.graph_id,canonical_graph_id),
                       revision=greatest(revision,coalesce(v_proposal.family_revision,revision+1)),
                       observation_count=observation_count+1,
                       review_state=CASE WHEN v_canonical.graph_id IS NULL
                           THEN 'provisional' ELSE 'stable' END,
                       updated_at=clock_timestamp()
                 WHERE family_id=v_family.family_id;
                v_change_status := CASE WHEN v_previous.graph_id IS NULL
                    AND v_canonical.graph_id IS NULL THEN 'baseline' ELSE 'observed' END;
            END IF;
        END IF;

        INSERT INTO rvbbit.business_context_changes(
            domain_key,workflow_run_id,from_graph_id,to_graph_id,
            observation_id,change_kind,severity,status,headline,diff
        ) VALUES (
            v_proposal.domain_key,p_run_id,
            coalesce(v_canonical.graph_id,v_previous.graph_id),v_proposal.graph_id,
            v_state.latest_observation_id,v_diff->>'change_kind',
            (v_diff->>'severity')::double precision,v_change_status,
            v_diff->>'headline',v_diff
        )
        ON CONFLICT (to_graph_id) DO UPDATE SET
            from_graph_id=EXCLUDED.from_graph_id,
            observation_id=EXCLUDED.observation_id,
            change_kind=EXCLUDED.change_kind,severity=EXCLUDED.severity,
            status=EXCLUDED.status,headline=EXCLUDED.headline,diff=EXCLUDED.diff
        RETURNING change_id INTO v_change_id;

        UPDATE rvbbit.business_context_domains
           SET status=CASE WHEN v_change_status='pending_review' THEN 'review' ELSE 'clean' END,
               modeled_fingerprint=coalesce(observed_fingerprint,modeled_fingerprint),
               pending_workflow_run_id=NULL,last_completed_workflow_run_id=p_run_id,
               last_change_id=v_change_id,dirty_since=NULL,dirty_reasons='[]'::jsonb,
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
        'workflow_run_id',p_run_id,'families_reconciled',v_count,
        'pending_review',v_material
    );
END
$fn$;

CREATE OR REPLACE VIEW rvbbit.business_value_graph_summary AS
SELECT proposal.graph_id,proposal.workflow_run_id,proposal.domain_key,
       proposal.name,proposal.summary,proposal.status,
       proposal.source_relations,proposal.primary_metric_key,
       jsonb_array_length(proposal.graph->'metrics')::integer AS metric_count,
       jsonb_array_length(proposal.graph->'edges')::integer AS edge_count,
       coalesce((proposal.receipt #>> '{evaluation,validated_metrics}')::integer,0) AS validated_metric_count,
       coalesce((proposal.receipt #>> '{evaluation,all_dimension_reconciliations_passed}')::boolean,false) AS reconciled,
       coalesce((proposal.receipt->>'fallback')::boolean,false) AS fallback,
       proposal.graph,proposal.receipt,proposal.proposed_by,proposal.proposed_at,
       proposal.updated_at,proposal.reviewed_by,proposal.reviewed_at,proposal.review_reason,
       promotion.promotion_id,promotion.cube_name,promotion.cube_version,
       promotion.materialization_grain,promotion.metric_versions,
       promotion.promotion_receipt,promotion.promoted_by,promotion.promoted_at,
       promotion.last_refreshed_at,
       proposal.family_id,proposal.family_revision,proposal.semantic_fingerprint,
       proposal.predecessor_graph_id,proposal.usable,
       family.active_graph_id,family.canonical_graph_id,family.pending_graph_id,
       family.review_state AS family_review_state,family.observation_count,
       CASE
         WHEN proposal.status='promoted' THEN 'canonical'
         WHEN proposal.usable THEN 'provisional'
         ELSE proposal.status
       END AS lifecycle
  FROM rvbbit.business_value_graph_proposals proposal
  LEFT JOIN rvbbit.business_value_system_families family USING (family_id)
  LEFT JOIN rvbbit.business_value_graph_promotions promotion USING (graph_id);

CREATE OR REPLACE VIEW rvbbit.business_value_promotion_catalog AS
SELECT summary.graph_id,summary.workflow_run_id,summary.domain_key,summary.name,
       summary.status,summary.promotion_id,summary.cube_name,
       'cubes.'||summary.cube_name AS cube_relation,
       summary.cube_version,summary.materialization_grain,summary.metric_versions,
       summary.promotion_receipt,summary.promoted_by,summary.promoted_at,
       summary.last_refreshed_at
  FROM rvbbit.business_value_graph_summary summary
 WHERE summary.promotion_id IS NOT NULL;

COMMENT ON TABLE rvbbit.business_value_system_families IS
    'One durable business constellation with canonical, active provisional, and at most one decision-blocked revision.';
COMMENT ON COLUMN rvbbit.business_value_graph_proposals.usable IS
    'True when agents may use this reconciled shadow definition with provisional labeling; independent from canonical cube/metric promotion.';

INSERT INTO rvbbit.business_context_state(singleton,dirty_at)
VALUES (true,clock_timestamp())
ON CONFLICT (singleton) DO UPDATE SET dirty_at=EXCLUDED.dirty_at;
