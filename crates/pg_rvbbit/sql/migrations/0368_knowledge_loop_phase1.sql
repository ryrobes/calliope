-- 0368: Knowledge Loop phase 1 — the adoption ladder and demotion signals.
--
-- Doctrine (docs/KNOWLEDGE_LOOP_PLAN.md): human review is an override,
-- never a gate. Value-system proposals that clear operator-set evidence
-- floors are adopted automatically as provisional; affirmation is earned
-- by sustained corrected-free usage; one explicit correction demotes,
-- passive signal only decays. Demoted claims enter a deny-list the next
-- excavation must respect or explicitly rebut with new evidence.
--
-- This migration deliberately adds NO new review surface. It rides on:
--   * business_value_promote_graph (0297) — which already self-enforces
--     reconciliation and validated-metric preconditions and accepts
--     status='proposed';
--   * business_semantic_assertion_states (0351) — effective_confidence
--     and validation_state per claim;
--   * business_context_turn_receipts (0300) — usage and correction
--     signals per served packet;
--   * business_context_mark_domain_dirty (0299) — demotion queues a
--     re-excavation through the normal steward path.

-- Operator-set policy. Flat numbers, visible, never model-tuned.
CREATE TABLE IF NOT EXISTS rvbbit.knowledge_loop_policy (
    kind text PRIMARY KEY,
    auto_adopt_enabled boolean NOT NULL DEFAULT true,
    adopt_confidence_floor double precision NOT NULL DEFAULT 0.70 CHECK (
        adopt_confidence_floor BETWEEN 0.0 AND 1.0
    ),
    adopt_max_per_tick integer NOT NULL DEFAULT 1 CHECK (adopt_max_per_tick BETWEEN 0 AND 10),
    affirm_min_turns integer NOT NULL DEFAULT 5 CHECK (affirm_min_turns >= 1),
    affirm_window_days integer NOT NULL DEFAULT 21 CHECK (affirm_window_days >= 1),
    demote_correction_threshold integer NOT NULL DEFAULT 1 CHECK (
        demote_correction_threshold >= 1
    ),
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO rvbbit.knowledge_loop_policy (kind)
VALUES ('value_graph') ON CONFLICT (kind) DO NOTHING;

-- The ladder lives on the promotion row; the proposal keeps its status
-- machinery. 'manual' = a human promoted it (pre-loop behavior).
ALTER TABLE rvbbit.business_value_graph_promotions
    ADD COLUMN IF NOT EXISTS adoption_state text NOT NULL DEFAULT 'manual',
    ADD COLUMN IF NOT EXISTS adoption_state_changed_at timestamptz;
ALTER TABLE rvbbit.business_value_graph_promotions
    DROP CONSTRAINT IF EXISTS business_value_graph_adoption_state_check;
ALTER TABLE rvbbit.business_value_graph_promotions
    ADD CONSTRAINT business_value_graph_adoption_state_check CHECK (
        adoption_state IN ('manual','auto_provisional','affirmed','demoted')
    );

-- 'demoted' joins the proposal status set: stops serving as governed,
-- keeps every receipt, never deletes.
ALTER TABLE rvbbit.business_value_graph_proposals
    DROP CONSTRAINT IF EXISTS business_value_graph_status_check;
ALTER TABLE rvbbit.business_value_graph_proposals
    ADD CONSTRAINT business_value_graph_status_check CHECK (
        status IN ('proposed','approved','promoted','needs_revision',
                   'rejected','superseded','demoted')
    );

-- Append-only ladder history.
CREATE TABLE IF NOT EXISTS rvbbit.knowledge_loop_events (
    event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    graph_id uuid NOT NULL,
    kind text NOT NULL,
    from_state text,
    to_state text,
    reason text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    actor text NOT NULL DEFAULT 'knowledge-loop',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT knowledge_loop_events_kind_check CHECK (
        kind IN ('adopted','affirmed','demoted','adopt_failed','rebutted')
    ),
    CONSTRAINT knowledge_loop_events_evidence_check CHECK (
        jsonb_typeof(evidence)='object'
    )
);
CREATE INDEX IF NOT EXISTS knowledge_loop_events_graph_idx
    ON rvbbit.knowledge_loop_events (graph_id, created_at DESC);

-- Demoted-claim deny-list. A future proposal whose assertions carry one
-- of these fingerprints is not auto-adoptable until the entry is
-- rebutted (by an operator, or by machinery attaching new evidence).
CREATE TABLE IF NOT EXISTS rvbbit.knowledge_loop_denylist (
    family_id uuid NOT NULL,
    fingerprint text NOT NULL,
    subject_key text,
    predicate text,
    reason text NOT NULL,
    source_graph_id uuid,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    rebutted_at timestamptz,
    rebutted_by text,
    rebuttal_graph_id uuid,
    PRIMARY KEY (family_id, fingerprint)
);

-- Aggregate assertion evidence for one graph.
CREATE OR REPLACE FUNCTION rvbbit.knowledge_loop_graph_confidence(
    p_graph_id uuid
) RETURNS jsonb
LANGUAGE sql STABLE
AS $fn$
    SELECT jsonb_build_object(
        'assertions', count(*),
        'avg_confidence', round(avg(state.effective_confidence)::numeric, 3),
        'min_confidence', round(min(state.effective_confidence)::numeric, 3),
        'contradicted', count(*) FILTER (WHERE state.validation_state='contradicted'),
        'supported', count(*) FILTER (WHERE state.validation_state='supported')
    )
    FROM rvbbit.business_semantic_assertions assertion
    JOIN rvbbit.business_semantic_assertion_states state USING (assertion_id)
    WHERE assertion.graph_id=p_graph_id
$fn$;

-- Demote one graph: stop governed serving, deny-list its claims, queue
-- re-excavation through the normal steward path. Never deletes.
CREATE OR REPLACE FUNCTION rvbbit.knowledge_loop_demote(
    p_graph_id uuid,
    p_reason text,
    p_actor text DEFAULT 'knowledge-loop',
    p_evidence jsonb DEFAULT '{}'::jsonb
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_proposal rvbbit.business_value_graph_proposals%ROWTYPE;
    v_prior text;
    v_claims integer := 0;
BEGIN
    SELECT * INTO v_proposal
      FROM rvbbit.business_value_graph_proposals
     WHERE graph_id=p_graph_id
     FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'value graph % was not found',p_graph_id; END IF;
    IF v_proposal.status='demoted' THEN
        RETURN jsonb_build_object('status','already_demoted','graph_id',p_graph_id);
    END IF;
    v_prior := v_proposal.status;

    UPDATE rvbbit.business_value_graph_proposals
       SET status='demoted', usable=false, updated_at=clock_timestamp(),
           review_reason=left('knowledge-loop demotion: '||coalesce(p_reason,''),500)
     WHERE graph_id=p_graph_id;

    UPDATE rvbbit.business_value_graph_promotions
       SET adoption_state='demoted', adoption_state_changed_at=clock_timestamp()
     WHERE graph_id=p_graph_id;

    INSERT INTO rvbbit.knowledge_loop_denylist (
        family_id, fingerprint, subject_key, predicate, reason,
        source_graph_id, evidence
    )
    SELECT assertion.family_id, assertion.fingerprint, assertion.subject_key,
           assertion.predicate, left(coalesce(p_reason,'demoted'),500),
           p_graph_id, p_evidence
      FROM rvbbit.business_semantic_assertions assertion
     WHERE assertion.graph_id=p_graph_id
    ON CONFLICT (family_id, fingerprint) DO NOTHING;
    GET DIAGNOSTICS v_claims = ROW_COUNT;

    INSERT INTO rvbbit.knowledge_loop_events (
        graph_id, kind, from_state, to_state, reason, evidence, actor
    ) VALUES (
        p_graph_id, 'demoted', v_prior, 'demoted',
        left(coalesce(p_reason,''),500), p_evidence, coalesce(p_actor,'knowledge-loop')
    );

    BEGIN
        PERFORM rvbbit.business_context_mark_domain_dirty(
            v_proposal.domain_key, 'manual', p_graph_id::text,
            jsonb_build_object(
                'source','knowledge_loop','event','demotion',
                'reason',left(coalesce(p_reason,''),200)
            )
        );
    EXCEPTION WHEN OTHERS THEN NULL; -- a bad domain key must not block demotion
    END;

    RETURN jsonb_build_object(
        'status','demoted','graph_id',p_graph_id,'prior_status',v_prior,
        'denylisted_claims',v_claims
    );
END
$fn$;

-- Operator rebuttal: clears a deny-list entry so the claim may be
-- re-proposed and auto-adopted again.
CREATE OR REPLACE FUNCTION rvbbit.knowledge_loop_rebut(
    p_family_id uuid,
    p_fingerprint text,
    p_actor text,
    p_rebuttal_graph_id uuid DEFAULT NULL
) RETURNS boolean
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE v_found boolean;
BEGIN
    UPDATE rvbbit.knowledge_loop_denylist
       SET rebutted_at=clock_timestamp(), rebutted_by=p_actor,
           rebuttal_graph_id=p_rebuttal_graph_id
     WHERE family_id=p_family_id AND fingerprint=p_fingerprint
       AND rebutted_at IS NULL;
    v_found := FOUND;
    IF v_found THEN
        INSERT INTO rvbbit.knowledge_loop_events (graph_id, kind, reason, actor)
        VALUES (coalesce(p_rebuttal_graph_id, p_family_id), 'rebutted',
                left('deny-list entry rebutted: '||p_fingerprint,500), p_actor);
    END IF;
    RETURN v_found;
END
$fn$;

-- Demotion sweep: the asymmetry rule. Explicit correction signals against
-- a served graph demote at the operator threshold; a claim the Evidence
-- Lab has watched into repeated contradiction demotes too. Passive
-- signals never demote here — they only withhold affirmation below.
CREATE OR REPLACE FUNCTION rvbbit.knowledge_loop_demote_tick()
RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_policy rvbbit.knowledge_loop_policy%ROWTYPE;
    v_row record;
    v_demoted jsonb := '[]'::jsonb;
BEGIN
    SELECT * INTO v_policy FROM rvbbit.knowledge_loop_policy WHERE kind='value_graph';
    IF NOT FOUND THEN RETURN jsonb_build_object('status','no_policy'); END IF;

    FOR v_row IN
        SELECT summary.graph_id,
               corrections.n AS correction_count,
               contradicted.n AS contradicted_claims
          FROM rvbbit.business_value_graph_summary summary
          JOIN LATERAL (
              SELECT count(*)::integer AS n
                FROM rvbbit.business_context_turn_receipts receipt
               WHERE summary.graph_id=ANY(receipt.used_graph_ids)
                 AND receipt.feedback_signal IN ('possible_correction','possible_contradiction')
                 AND receipt.created_at >= coalesce(summary.promoted_at, summary.proposed_at)
          ) corrections ON true
          JOIN LATERAL (
              SELECT count(*)::integer AS n
                FROM rvbbit.business_semantic_assertions assertion
                JOIN rvbbit.business_semantic_assertion_states state USING (assertion_id)
               WHERE assertion.graph_id=summary.graph_id
                 AND state.validation_state='contradicted'
                 AND state.consecutive_failures >= 2
          ) contradicted ON true
         WHERE summary.status='promoted'
           AND (corrections.n >= v_policy.demote_correction_threshold
                OR contradicted.n > 0)
         LIMIT 5
    LOOP
        PERFORM rvbbit.knowledge_loop_demote(
            v_row.graph_id,
            CASE
                WHEN v_row.correction_count >= v_policy.demote_correction_threshold
                    THEN v_row.correction_count||' correction signal(s) from served turns'
                ELSE v_row.contradicted_claims||' claim(s) watched into repeated contradiction'
            END,
            'knowledge-loop',
            jsonb_build_object(
                'correction_receipts', v_row.correction_count,
                'contradicted_claims', v_row.contradicted_claims
            )
        );
        v_demoted := v_demoted || to_jsonb(v_row.graph_id);
    END LOOP;
    RETURN jsonb_build_object('status','complete','demoted',v_demoted);
END
$fn$;

-- Affirmation sweep: earned, not clicked. Sustained corrected-free usage
-- since promotion, and no contradicted claims.
CREATE OR REPLACE FUNCTION rvbbit.knowledge_loop_affirm_tick()
RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_policy rvbbit.knowledge_loop_policy%ROWTYPE;
    v_row record;
    v_affirmed jsonb := '[]'::jsonb;
BEGIN
    SELECT * INTO v_policy FROM rvbbit.knowledge_loop_policy WHERE kind='value_graph';
    IF NOT FOUND THEN RETURN jsonb_build_object('status','no_policy'); END IF;

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
        INSERT INTO rvbbit.knowledge_loop_events (
            graph_id, kind, from_state, to_state, reason, evidence
        ) VALUES (
            v_row.graph_id, 'affirmed', 'auto_provisional', 'affirmed',
            v_row.turns||' corrected-free turns in '||v_policy.affirm_window_days||' days',
            jsonb_build_object('turns', v_row.turns)
        );
        v_affirmed := v_affirmed || to_jsonb(v_row.graph_id);
    END LOOP;
    RETURN jsonb_build_object('status','complete','affirmed',v_affirmed);
END
$fn$;

-- Auto-adoption sweep. The heavy preconditions (reconciled, non-fallback,
-- validated metrics) are re-enforced inside business_value_promote_graph;
-- this policy layer adds the evidence floor, the contradiction veto, and
-- the deny-list check, then promotes and marks provenance.
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
    v_count integer := 0;
BEGIN
    SELECT * INTO v_policy FROM rvbbit.knowledge_loop_policy WHERE kind='value_graph';
    IF NOT FOUND OR NOT v_policy.auto_adopt_enabled THEN
        RETURN jsonb_build_object('status','disabled');
    END IF;

    FOR v_row IN
        SELECT proposal.graph_id, proposal.family_id
          FROM rvbbit.business_value_graph_proposals proposal
         WHERE proposal.status='proposed'
           AND proposal.usable
           AND coalesce((proposal.receipt->>'fallback')::boolean,false)=false
           AND coalesce((proposal.receipt #>> '{evaluation,all_dimension_reconciliations_passed}')::boolean,false)
           AND coalesce((proposal.receipt #>> '{evaluation,validated_metrics}')::integer,0) > 0
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
        'status','complete','adopted',v_adopted,'failed',v_failed
    );
END
$fn$;

-- One tick, cron-safe: demote first (corrections outrank everything),
-- then affirm, then adopt within budget.
CREATE OR REPLACE FUNCTION rvbbit.knowledge_loop_tick()
RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_demote jsonb; v_affirm jsonb; v_adopt jsonb;
BEGIN
    IF NOT pg_try_advisory_xact_lock(
        hashtextextended('rvbbit.knowledge-loop-tick',0)
    ) THEN
        RETURN jsonb_build_object('status','busy');
    END IF;
    v_demote := rvbbit.knowledge_loop_demote_tick();
    v_affirm := rvbbit.knowledge_loop_affirm_tick();
    v_adopt := rvbbit.knowledge_loop_auto_adopt_tick();
    RETURN jsonb_build_object(
        'status','complete','demote',v_demote,'affirm',v_affirm,'adopt',v_adopt
    );
END
$fn$;

-- The 0297 immutability guard froze every promoted proposal outright —
-- correct for content (the receipt stays evidence forever), but it also
-- blocked the ladder's promoted→demoted transition. Allow exactly that
-- transition while keeping every content field frozen.
CREATE OR REPLACE FUNCTION rvbbit._business_value_preserve_promoted_graph()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF OLD.status='promoted' AND EXISTS (
        SELECT 1 FROM rvbbit.business_value_graph_promotions WHERE graph_id=OLD.graph_id
    ) THEN
        NEW.domain_key := OLD.domain_key;
        NEW.name := OLD.name;
        NEW.summary := OLD.summary;
        NEW.source_relations := OLD.source_relations;
        NEW.primary_metric_key := OLD.primary_metric_key;
        NEW.graph := OLD.graph;
        NEW.receipt := OLD.receipt;
        NEW.proposed_by := OLD.proposed_by;
        NEW.proposed_at := OLD.proposed_at;
        IF NEW.status='demoted' THEN
            -- the knowledge-loop demotion: status, usable, review fields,
            -- and updated_at may change; everything above stays frozen.
            RETURN NEW;
        END IF;
        NEW.status := 'promoted';
        NEW.reviewed_by := OLD.reviewed_by;
        NEW.reviewed_at := OLD.reviewed_at;
        NEW.review_reason := OLD.review_reason;
        NEW.updated_at := OLD.updated_at;
    END IF;
    RETURN NEW;
END
$fn$;

-- Surface the ladder in the summary view (used by the context packet and
-- DataRabbit). Column list is append-only vs 0341.
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
       END AS lifecycle,
       coalesce(promotion.adoption_state,'manual') AS adoption_state,
       promotion.adoption_state_changed_at
  FROM rvbbit.business_value_graph_proposals proposal
  LEFT JOIN rvbbit.business_value_system_families family USING (family_id)
  LEFT JOIN rvbbit.business_value_graph_promotions promotion USING (graph_id);

COMMENT ON TABLE rvbbit.knowledge_loop_policy IS
    'Operator-set adoption/affirmation/demotion floors for the Knowledge Loop. Flat numbers, never model-tuned.';
COMMENT ON TABLE rvbbit.knowledge_loop_denylist IS
    'Fingerprints of demoted semantic claims. Blocks re-adoption until rebutted by an operator or by new evidence.';
COMMENT ON FUNCTION rvbbit.knowledge_loop_tick() IS
    'Cron entry point: demote on explicit signal, affirm earned usage, auto-adopt proposals clearing operator floors.';
