-- 0418: Retire an excavation generation, and make generations observable.
--
-- Companion to 0417 (Excavation with a Map). A sweep re-run on top of the
-- pre-map generation would not show anything new: business_value_stage_graph
-- files every graph into the family with the same domain_key and compares it
-- to that family's pending-or-active graph — an identical fingerprint is
-- instantly superseded as a repeat, a changed one becomes a revision of the
-- old canonical. So the old generation is retired as a generation:
--   * graphs → superseded (never demoted: no denylist entries, nothing
--     legitimate is blocked from rediscovery), promotions → demoted,
--     their cubes dropped and their metric definitions removed;
--   * family pointers cleared (rows kept) so the next excavation of the same
--     domain lands as a fresh baseline with lineage intact;
--   * watch specs disabled, steward domain rows settled, pending change
--     reviews dismissed, the business-context knowledge graph re-crawled.
-- Every receipt (runs, bundles, assertions, events, denylist) stays. Human
-- decisions stay: affirmed-by-use and manually promoted graphs are kept.
-- Dry-run by default — the receipt says exactly what would happen.

-- A family pointer is worth keeping only while it points at a graph that is
-- still promoted or under review. NULL pointers are "live" (nothing to clear).
CREATE OR REPLACE FUNCTION rvbbit._business_value_graph_is_live(p_graph_id uuid)
RETURNS boolean
LANGUAGE sql STABLE
AS $fn$
    SELECT p_graph_id IS NULL OR EXISTS (
        SELECT 1 FROM rvbbit.business_value_graph_proposals p
         WHERE p.graph_id=p_graph_id
           AND p.status IN ('proposed','approved','needs_revision','promoted'));
$fn$;

DROP FUNCTION IF EXISTS rvbbit.business_value_retire_generation(text, boolean, boolean);
CREATE OR REPLACE FUNCTION rvbbit.business_value_retire_generation(
    p_reason text DEFAULT 'Retired: pre-map generation (0417)',
    p_dry_run boolean DEFAULT true,
    p_keep_affirmed boolean DEFAULT true,
    p_keep_domains text[] DEFAULT '{}'::text[]
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_targets jsonb;
    v_target_ids uuid[];
    v_leftover jsonb;
    v_leftover_names text[];
    v_family_keys text[];
    v_row record;
    v_errors jsonb := '[]'::jsonb;
    v_cubes_dropped integer := 0;
    v_metrics_removed integer := 0;
    v_n integer := 0;
    v_promotions integer := 0;
    v_proposals integer := 0;
    v_families integer := 0;
    v_watches integer := 0;
    v_domains integer := 0;
    v_changes integer := 0;
    v_crawl jsonb;
    v_reason text := coalesce(nullif(btrim(p_reason),''), 'Retired: excavation generation');
BEGIN
    IF v_reason NOT LIKE 'Retired:%' THEN
        v_reason := 'Retired: '||v_reason;
    END IF;

    -- Graphs that are currently visible (canonical or provisional) and were
    -- never affirmed by a person or by real use.
    SELECT coalesce(jsonb_agg(jsonb_build_object(
               'graph_id', t.graph_id, 'domain_key', t.domain_key, 'status', t.status,
               'adoption_state', t.adoption_state, 'cube_name', t.cube_name,
               'schema', split_part(t.source_relations[1],'.',1),
               'relations', cardinality(t.source_relations),
               'metric_defs', (SELECT count(*) FROM rvbbit.metric_defs md
                                WHERE md.labels->>'business_value_graph_id'=t.graph_id::text)
           ) ORDER BY t.domain_key), '[]'::jsonb),
           coalesce(array_agg(t.graph_id), '{}'::uuid[])
      INTO v_targets, v_target_ids
      FROM (
          SELECT p.graph_id, p.domain_key, p.status, p.source_relations,
                 pr.adoption_state, pr.cube_name
            FROM rvbbit.business_value_graph_proposals p
            LEFT JOIN rvbbit.business_value_graph_promotions pr USING (graph_id)
           WHERE p.status IN ('proposed','approved','needs_revision','promoted')
             -- pr.adoption_state is NULL for never-promoted graphs; coalesce
             -- keeps three-valued logic from silently dropping them.
             AND NOT (p_keep_affirmed AND coalesce(pr.adoption_state,'') IN ('affirmed','manual'))
             AND NOT (p.domain_key = ANY(coalesce(p_keep_domains,'{}'::text[])))
      ) t;

    -- Cubes left behind by earlier demotions (knowledge_loop_demote never drops them).
    SELECT coalesce(jsonb_agg(jsonb_build_object('graph_id', pr.graph_id, 'cube_name', pr.cube_name)
                              ORDER BY pr.cube_name), '[]'::jsonb),
           coalesce(array_agg(pr.cube_name), '{}'::text[])
      INTO v_leftover, v_leftover_names
      FROM rvbbit.business_value_graph_promotions pr
     WHERE pr.adoption_state='demoted'
       AND NOT (pr.graph_id = ANY(v_target_ids))
       AND (to_regclass('cubes.'||quote_ident(pr.cube_name)) IS NOT NULL
            OR EXISTS (SELECT 1 FROM rvbbit.cube_defs cd WHERE cd.name=pr.cube_name)
            OR EXISTS (SELECT 1 FROM rvbbit.metric_defs md
                        WHERE md.labels->>'business_value_graph_id'=pr.graph_id::text));

    SELECT coalesce(array_agg(DISTINCT f.domain_key), '{}'::text[]) INTO v_family_keys
      FROM rvbbit.business_value_system_families f
     WHERE f.active_graph_id = ANY(v_target_ids)
        OR f.canonical_graph_id = ANY(v_target_ids)
        OR f.pending_graph_id = ANY(v_target_ids);

    IF p_dry_run THEN
        RETURN jsonb_build_object(
            'dry_run', true, 'reason', v_reason,
            'graphs_to_retire', coalesce(cardinality(v_target_ids),0),
            'promotions_to_demote', (SELECT count(*) FROM rvbbit.business_value_graph_promotions
                                      WHERE graph_id = ANY(v_target_ids) AND adoption_state<>'demoted'),
            'cubes_to_drop', (SELECT count(*) FROM rvbbit.business_value_graph_promotions
                               WHERE graph_id = ANY(v_target_ids)
                                 AND to_regclass('cubes.'||quote_ident(cube_name)) IS NOT NULL)
                             + coalesce(cardinality(v_leftover_names),0),
            'metric_defs_to_remove', (SELECT count(*) FROM rvbbit.metric_defs
                                       WHERE (labels->>'business_value_graph_id')::uuid = ANY(v_target_ids)
                                          OR (labels->>'business_value_graph_id')::uuid IN
                                             (SELECT graph_id FROM rvbbit.business_value_graph_promotions
                                               WHERE cube_name = ANY(v_leftover_names))),
            'families_to_clear', coalesce(cardinality(v_family_keys),0),
            'watch_specs_to_disable', (SELECT count(*) FROM rvbbit.business_semantic_watch_specs
                                        WHERE graph_id = ANY(v_target_ids) AND enabled),
            'kept', (SELECT coalesce(jsonb_agg(jsonb_build_object('domain_key', p.domain_key,
                                     'status', p.status, 'adoption_state', pr.adoption_state,
                                     'why', CASE WHEN p.domain_key = ANY(coalesce(p_keep_domains,'{}'::text[]))
                                                 THEN 'operator keep list' ELSE 'affirmed or manual' END)
                                     ORDER BY p.domain_key), '[]'::jsonb)
                       FROM rvbbit.business_value_graph_proposals p
                       LEFT JOIN rvbbit.business_value_graph_promotions pr USING (graph_id)
                      WHERE p.status IN ('proposed','approved','needs_revision','promoted')
                        AND NOT (p.graph_id = ANY(v_target_ids))),
            'targets', v_targets,
            'leftover_demoted_cubes', v_leftover
        );
    END IF;

    -- ── apply ────────────────────────────────────────────────────────────
    FOR v_row IN
        SELECT pr.graph_id, pr.cube_name, pr.adoption_state
          FROM rvbbit.business_value_graph_promotions pr
         WHERE pr.graph_id = ANY(v_target_ids) OR pr.cube_name = ANY(v_leftover_names)
    LOOP
        BEGIN
            IF to_regclass('cubes.'||quote_ident(v_row.cube_name)) IS NOT NULL
               OR EXISTS (SELECT 1 FROM rvbbit.cube_defs WHERE name=v_row.cube_name) THEN
                PERFORM rvbbit.drop_cube(v_row.cube_name);
                v_cubes_dropped := v_cubes_dropped + 1;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            v_errors := v_errors || jsonb_build_object('cube', v_row.cube_name, 'error', left(SQLERRM,300));
        END;
        BEGIN
            DELETE FROM rvbbit.metric_defs
             WHERE labels->>'business_value_graph_id' = v_row.graph_id::text;
            GET DIAGNOSTICS v_n = ROW_COUNT;
            v_metrics_removed := v_metrics_removed + v_n;
        EXCEPTION WHEN OTHERS THEN
            v_errors := v_errors || jsonb_build_object('metrics_of', v_row.graph_id, 'error', left(SQLERRM,300));
        END;
    END LOOP;

    UPDATE rvbbit.business_value_graph_promotions
       SET adoption_state='demoted', adoption_state_changed_at=clock_timestamp()
     WHERE graph_id = ANY(v_target_ids) AND adoption_state<>'demoted';
    GET DIAGNOSTICS v_promotions = ROW_COUNT;

    INSERT INTO rvbbit.knowledge_loop_events (graph_id, kind, from_state, to_state, reason, evidence, actor)
    SELECT p.graph_id, 'demoted', p.status, 'retired', v_reason,
           jsonb_build_object('generation_retirement', true, 'domain_key', p.domain_key), session_user
      FROM rvbbit.business_value_graph_proposals p
     WHERE p.graph_id = ANY(v_target_ids);

    -- The preserve-promoted trigger (0297/0368) freezes a promoted proposal
    -- except for the ladder's promoted→demoted transition, so a retired
    -- promotion takes 'demoted' (the status the loop already understands)
    -- and a never-promoted graph takes 'superseded'. The event row above and
    -- the review_reason carry the "retired, not demoted for cause" meaning.
    UPDATE rvbbit.business_value_graph_proposals p
       SET status=CASE WHEN EXISTS (SELECT 1 FROM rvbbit.business_value_graph_promotions pr
                                     WHERE pr.graph_id=p.graph_id)
                       THEN 'demoted' ELSE 'superseded' END,
           usable=false,
           review_reason=v_reason, reviewed_by=session_user, reviewed_at=clock_timestamp(),
           updated_at=clock_timestamp()
     WHERE p.graph_id = ANY(v_target_ids);
    GET DIAGNOSTICS v_proposals = ROW_COUNT;

    -- Clear family pointers at retired graphs — and at anything else no
    -- longer live (earlier demotions left dangling pointers, and the repeat
    -- check compares a new excavation against the pointed-at graph).
    UPDATE rvbbit.business_value_system_families f
       SET active_graph_id = CASE WHEN f.active_graph_id = ANY(v_target_ids) OR NOT rvbbit._business_value_graph_is_live(f.active_graph_id) THEN NULL ELSE f.active_graph_id END,
           canonical_graph_id = CASE WHEN f.canonical_graph_id = ANY(v_target_ids) OR NOT rvbbit._business_value_graph_is_live(f.canonical_graph_id) THEN NULL ELSE f.canonical_graph_id END,
           pending_graph_id = CASE WHEN f.pending_graph_id = ANY(v_target_ids) OR NOT rvbbit._business_value_graph_is_live(f.pending_graph_id) THEN NULL ELSE f.pending_graph_id END,
           review_state='provisional', updated_at=clock_timestamp()
     WHERE f.domain_key = ANY(v_family_keys)
        OR NOT rvbbit._business_value_graph_is_live(f.active_graph_id)
        OR NOT rvbbit._business_value_graph_is_live(f.canonical_graph_id)
        OR NOT rvbbit._business_value_graph_is_live(f.pending_graph_id);
    GET DIAGNOSTICS v_families = ROW_COUNT;

    UPDATE rvbbit.business_semantic_watch_specs
       SET enabled=false, updated_at=clock_timestamp()
     WHERE graph_id = ANY(v_target_ids) AND enabled;
    GET DIAGNOSTICS v_watches = ROW_COUNT;

    UPDATE rvbbit.business_context_changes
       SET status='dismissed', reviewed_by=coalesce(reviewed_by, session_user),
           reviewed_at=coalesce(reviewed_at, clock_timestamp())
     WHERE to_graph_id = ANY(v_target_ids) AND status IN ('pending_review','observed','baseline');
    GET DIAGNOSTICS v_changes = ROW_COUNT;

    -- Settle steward rows for every family that no longer points at a live
    -- graph, so the steward has nothing stale to refresh.
    UPDATE rvbbit.business_context_domains d
       SET status='clean', dirty_reasons='[]'::jsonb, dirty_since=NULL,
           pending_workflow_run_id=NULL, last_error=NULL, updated_at=clock_timestamp()
     WHERE (d.domain_key = ANY(v_family_keys)
            OR d.domain_key IN (SELECT f.domain_key FROM rvbbit.business_value_system_families f
                                 WHERE f.active_graph_id IS NULL AND f.canonical_graph_id IS NULL
                                   AND f.pending_graph_id IS NULL)
            -- orphans from before families existed: nothing to refresh
            OR NOT EXISTS (SELECT 1 FROM rvbbit.business_value_system_families f
                            WHERE f.domain_key=d.domain_key))
       AND d.status IN ('pending_catalog','dirty','queued','review','error');
    GET DIAGNOSTICS v_domains = ROW_COUNT;

    -- Search sees only canonical/provisional graphs; rebuild the knowledge
    -- graph so the retired generation disappears (embeddings follow via the
    -- usual embed tick).
    BEGIN
        v_crawl := to_jsonb(rvbbit.business_context_crawl());
    EXCEPTION WHEN OTHERS THEN
        v_crawl := jsonb_build_object('error', left(SQLERRM,300));
    END;

    RETURN jsonb_build_object(
        'dry_run', false, 'reason', v_reason,
        'graphs_retired', v_proposals,
        'promotions_demoted', v_promotions,
        'cubes_dropped', v_cubes_dropped,
        'metric_defs_removed', v_metrics_removed,
        'metric_defs_remaining_for_retired', (SELECT count(*) FROM rvbbit.metric_defs
                                              WHERE (labels->>'business_value_graph_id')::uuid = ANY(v_target_ids)),
        'families_cleared', v_families,
        'watch_specs_disabled', v_watches,
        'changes_dismissed', v_changes,
        'domain_rows_settled', v_domains,
        'crawl', v_crawl,
        'errors', v_errors,
        'targets', v_targets,
        'leftover_demoted_cubes', v_leftover
    );
END
$fn$;
COMMENT ON FUNCTION rvbbit.business_value_retire_generation(text, boolean, boolean, text[]) IS
    'Retires every currently visible, never-affirmed value graph as a generation: superseded (no denylist), promotions demoted, cubes dropped, metric defs removed, family pointers cleared, watches disabled, knowledge graph re-crawled. Dry-run by default; the receipt lists every target. Affirmed and manually promoted graphs are kept, as are domain_keys in p_keep_domains.';

-- ── Observability: which generation is each graph from, and how does the
--    new one compare? The boundary is the last retirement (or, before any
--    retirement, when 0417 was applied).
CREATE OR REPLACE VIEW rvbbit.business_value_graph_generations AS
WITH boundary AS (
    SELECT coalesce(
        (SELECT max(created_at) FROM rvbbit.knowledge_loop_events WHERE to_state='retired'),
        (SELECT applied_at FROM rvbbit.schema_migrations WHERE name='0417_excavation_with_a_map'),
        'infinity'::timestamptz) AS at
)
SELECT s.graph_id, s.domain_key, s.name, s.status, s.lifecycle, s.usable,
       pr.adoption_state,
       CASE WHEN s.proposed_at >= boundary.at THEN 'map' ELSE 'pre-map' END AS generation,
       boundary.at AS generation_boundary,
       split_part(s.source_relations[1],'.',1) AS schema_name,
       rvbbit.business_source_role(split_part(s.source_relations[1],'.',1)) AS source_role,
       cardinality(s.source_relations) AS relation_count,
       (cardinality(s.source_relations)=1) AS single_table,
       s.metric_count, s.validated_metric_count, s.fallback, s.reconciled,
       (s.review_reason LIKE 'Retired:%'
        OR EXISTS (SELECT 1 FROM rvbbit.knowledge_loop_events ev
                    WHERE ev.graph_id=s.graph_id AND ev.to_state='retired')) AS retired,
       s.review_reason, s.proposed_at, s.workflow_run_id
  FROM rvbbit.business_value_graph_summary s
  LEFT JOIN rvbbit.business_value_graph_promotions pr USING (graph_id)
  CROSS JOIN boundary;
COMMENT ON VIEW rvbbit.business_value_graph_generations IS
    'Every value graph tagged pre-map / map by the last generation retirement (else the 0417 apply time), with its source schema, registry role, relation count, and lifecycle — the before/after ledger for the excavator.';

CREATE OR REPLACE VIEW rvbbit.business_value_generation_summary AS
SELECT generation, source_role, schema_name,
       count(*) AS graphs,
       count(*) FILTER (WHERE lifecycle='canonical') AS canonical,
       count(*) FILTER (WHERE lifecycle='provisional') AS provisional,
       count(*) FILTER (WHERE retired) AS retired,
       count(*) FILTER (WHERE status='superseded' AND NOT retired) AS superseded,
       count(*) FILTER (WHERE status='demoted' AND NOT retired) AS demoted,
       count(*) FILTER (WHERE single_table) AS single_table,
       round(avg(relation_count)::numeric,1) AS avg_relations,
       count(*) FILTER (WHERE fallback) AS fallback,
       min(proposed_at) AS first_proposed, max(proposed_at) AS last_proposed
  FROM rvbbit.business_value_graph_generations
 GROUP BY 1,2,3;
COMMENT ON VIEW rvbbit.business_value_generation_summary IS
    'Per generation × source role × schema: how many domains, how many canonical/provisional, single-table count, average relations — the old generation is the baseline the new one has to beat.';
