-- 0371: adoption backoff — a failing proposal is not a free retry.
--
-- Field finding (test-hosted1, first night live): three proposals whose
-- cubes fail reconciliation inside business_value_promote_graph were
-- retried every 15-minute tick — ~30 failed materialization attempts
-- each. Two rules fix it: at most one adoption attempt per graph per
-- 24 hours, and three strikes moves the proposal to needs_revision with
-- the failure receipt so the steward's next excavation regenerates it
-- instead of the tick hammering it forever.

CREATE OR REPLACE FUNCTION rvbbit.knowledge_loop_auto_adopt_tick()
RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_policy rvbbit.knowledge_loop_policy%ROWTYPE;
    v_row record;
    v_confidence jsonb;
    v_adopted jsonb := '[]'::jsonb;
    v_failed jsonb := '[]'::jsonb;
    v_benched jsonb := '[]'::jsonb;
    v_count integer := 0;
BEGIN
    SELECT * INTO v_policy FROM rvbbit.knowledge_loop_policy WHERE kind='value_graph';
    IF NOT FOUND OR NOT v_policy.auto_adopt_enabled THEN
        RETURN jsonb_build_object('status','disabled');
    END IF;

    -- three strikes: bench the proposal for regeneration, with receipts.
    FOR v_row IN
        SELECT proposal.graph_id, proposal.domain_key
          FROM rvbbit.business_value_graph_proposals proposal
         WHERE proposal.status='proposed'
           AND (SELECT count(*) FROM rvbbit.knowledge_loop_events event
                 WHERE event.graph_id=proposal.graph_id
                   AND event.kind='adopt_failed') >= 3
         LIMIT 10
    LOOP
        UPDATE rvbbit.business_value_graph_proposals
           SET status='needs_revision', usable=false,
               review_reason='knowledge-loop: adoption failed 3+ times; awaiting re-excavation',
               updated_at=clock_timestamp()
         WHERE graph_id=v_row.graph_id;
        BEGIN
            PERFORM rvbbit.business_context_mark_domain_dirty(
                v_row.domain_key, 'manual', v_row.graph_id::text,
                jsonb_build_object('source','knowledge_loop','event','adoption_benched')
            );
        EXCEPTION WHEN OTHERS THEN NULL;
        END;
        v_benched := v_benched || to_jsonb(v_row.graph_id);
    END LOOP;

    FOR v_row IN
        SELECT proposal.graph_id, proposal.family_id
          FROM rvbbit.business_value_graph_proposals proposal
         WHERE proposal.status='proposed'
           AND proposal.usable
           AND coalesce((proposal.receipt->>'fallback')::boolean,false)=false
           AND coalesce((proposal.receipt #>> '{evaluation,all_dimension_reconciliations_passed}')::boolean,false)
           AND coalesce((proposal.receipt #>> '{evaluation,validated_metrics}')::integer,0) > 0
           AND NOT EXISTS (
               SELECT 1 FROM rvbbit.knowledge_loop_events event
                WHERE event.graph_id=proposal.graph_id
                  AND event.kind='adopt_failed'
                  AND event.created_at > clock_timestamp() - interval '24 hours')
           AND NOT EXISTS (
               SELECT 1
                 FROM rvbbit.business_semantic_assertions assertion
                 JOIN rvbbit.knowledge_loop_denylist deny
                   ON deny.family_id=assertion.family_id
                  AND deny.fingerprint=assertion.fingerprint
                WHERE assertion.graph_id=proposal.graph_id
                  AND deny.rebutted_at IS NULL)
         ORDER BY proposal.proposed_at
         LIMIT 10
    LOOP
        EXIT WHEN v_count >= v_policy.adopt_max_per_tick;
        v_confidence := rvbbit.knowledge_loop_graph_confidence(v_row.graph_id);
        CONTINUE WHEN coalesce((v_confidence->>'assertions')::integer,0)=0;
        CONTINUE WHEN coalesce((v_confidence->>'contradicted')::integer,0) > 0;
        CONTINUE WHEN coalesce((v_confidence->>'avg_confidence')::double precision,0.0)
                      < v_policy.adopt_confidence_floor;
        BEGIN
            PERFORM rvbbit.business_value_promote_graph(
                v_row.graph_id, NULL, NULL, 'knowledge-loop auto-adoption'
            );
            UPDATE rvbbit.business_value_graph_promotions
               SET adoption_state='auto_provisional',
                   adoption_state_changed_at=clock_timestamp()
             WHERE graph_id=v_row.graph_id;
            INSERT INTO rvbbit.knowledge_loop_events (
                graph_id, kind, from_state, to_state, reason, evidence
            ) VALUES (
                v_row.graph_id, 'adopted', 'proposed', 'auto_provisional',
                'cleared operator floor '||v_policy.adopt_confidence_floor,
                v_confidence
            );
            v_adopted := v_adopted || to_jsonb(v_row.graph_id);
            v_count := v_count + 1;
        EXCEPTION WHEN OTHERS THEN
            INSERT INTO rvbbit.knowledge_loop_events (
                graph_id, kind, reason, evidence
            ) VALUES (
                v_row.graph_id, 'adopt_failed', left(SQLERRM,500), v_confidence
            );
            v_failed := v_failed || to_jsonb(v_row.graph_id);
        END;
    END LOOP;
    RETURN jsonb_build_object(
        'status','complete','adopted',v_adopted,'failed',v_failed,
        'benched',v_benched
    );
END
$fn$;
