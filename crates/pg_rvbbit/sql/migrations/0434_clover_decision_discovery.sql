-- SQL helpers are callable capabilities even when not rvbbit.operators rows.
-- Publish exact signatures through the existing seed -> capability graph path.
INSERT INTO rvbbit.capability_seed(name,kind,signature,description,example,cost_class,tags)
VALUES
('truth','cap_operator','rvbbit.truth(state jsonb, predicate text, threshold double precision DEFAULT 0.5) → boolean','Jev semantic predicate; state also accepts text. TRUE only when score > threshold. NULL on missing input or inference failure. Shares the clover_decide semantic receipt cache with truth_score. Use deterministic SQL for authoritative status, opt-out, dates and enrollment; use semantic judgments for ambiguous evidence, not to override those facts.','SELECT rvbbit.truth(''Please cancel my appointment'', ''Does the customer request cancellation?'')','metered_llm',ARRAY['semantic','jev','decision','classifier','tree','cache']),
('truth_score','cap_operator','rvbbit.truth_score(state jsonb, predicate text) → double precision','Jev score in [0,1], not calibrated probability. State also accepts text; NULL on missing input or failure. Shares cached evidence with truth and clover_decide.','SELECT rvbbit.truth_score(''Please cancel my appointment'', ''Does the customer request cancellation?'')','metered_llm',ARRAY['semantic','jev','decision','classifier','tree','cache']),
('choose','cap_operator','rvbbit.choose(state jsonb, categories jsonb) → text','Jev classifier: 2..16 label-to-description entries. Overloads accept state text and/or categories text[]. Include an explicit unclear/other label if needed; no automatic abstention. Uses the clover_decide semantic cache.','SELECT rvbbit.choose(''Please move my appointment'', ARRAY[''cancel'',''reschedule'',''other''])','metered_llm',ARRAY['semantic','jev','decision','classifier','tree','cache']),
('leaf','cap_operator','rvbbit.leaf(label text) → jsonb','Build an inert decision tree leaf. No inference. Returns the label when reached by decision_tree.','SELECT rvbbit.leaf(''manual review'')','free',ARRAY['semantic','jev','decision','classifier','tree','cache']),
('branch','cap_operator','rvbbit.branch(question text, yes jsonb, no jsonb, threshold double precision DEFAULT 0.5) → jsonb','Build an inert binary Jev decision branch; children are branch, choice_branch or leaf JSONB. No inference until evaluated. Follows yes only when score > threshold.','SELECT rvbbit.branch(''Does the message request cancellation?'', rvbbit.leaf(''cancel''), rvbbit.leaf(''other''))','free',ARRAY['semantic','jev','decision','classifier','tree','cache']),
('choice_branch','cap_operator','rvbbit.choice_branch(question text, choices jsonb, routes jsonb) → jsonb','Build an inert multiway decision branch. choices maps labels to descriptions (2..16); routes maps the EXACT SAME labels to child branch/leaf JSONB. This is a three-argument function, not branch with extra options.','SELECT rvbbit.choice_branch(''What help is needed?'', ''{"billing":"Invoices or payments","other":"Other requests"}''::jsonb, jsonb_build_object(''billing'',rvbbit.leaf(''finance''),''other'',rvbbit.leaf(''review'')))','free',ARRAY['semantic','jev','decision','classifier','tree','cache']),
('decision_tree','cap_operator','rvbbit.decision_tree(state jsonb, tree jsonb, max_steps integer DEFAULT 16) → jsonb','Evaluate a bounded Jev tree, visiting only the selected path. State also accepts text. Each node sees the original evidence plus prior fallible judgments. Returns status, label, path, steps, tree_id and version; each path entry records question, route, score, model and contract. NULL input stays NULL. Node decisions share the clover_decide cache. Keep authoritative business rules as SQL CASE gates; reserve reasoning for ambiguous conflicts.','SELECT rvbbit.decision_tree(''Please cancel my appointment'', rvbbit.branch(''Does the customer request cancellation?'',rvbbit.leaf(''cancel''),rvbbit.leaf(''review'')))','metered_llm',ARRAY['semantic','jev','decision','classifier','tree','cache']),
('decision_tree_batch','cap_operator','rvbbit.decision_tree_batch(states jsonb, tree jsonb, max_steps integer DEFAULT 16) → jsonb','Bulk Jev decision trees in ordered waves, with per-node semantic caching and deduplication. states MUST be a JSONB array, up to 256 entries; text[] is not an overload. Convert a SQL array using to_jsonb(ARRAY[...]) or build jsonb_agg(context ORDER BY id) over a bounded slice. Returns a JSONB array in input order; NULL entries stay NULL. Finished rows stop participating. Same tree/result contract as decision_tree.','SELECT rvbbit.decision_tree_batch(to_jsonb(ARRAY[''Please cancel'',''Please reschedule'']), rvbbit.branch(''Does the customer request cancellation?'',rvbbit.leaf(''cancel''),rvbbit.leaf(''other'')))','metered_llm',ARRAY['semantic','jev','decision','classifier','tree','cache']),
('truth_question','cap_operator','rvbbit.truth_question(predicate text) → jsonb','Build a named noul question for clover_decide or prewarm_operator. No inference; prewarming this question shares cache with truth and truth_score.','SELECT rvbbit.truth_question(''Does the customer request cancellation?'')','free',ARRAY['semantic','jev','decision','classifier','tree','cache']),
('choice_question','cap_operator','rvbbit.choice_question(categories jsonb) → jsonb','Build a named choice question for clover_decide or prewarm_operator. Also accepts categories text[]. No inference; shares cache with choose.','SELECT rvbbit.choice_question(ARRAY[''billing'',''scheduling'',''other''])','free',ARRAY['semantic','jev','decision','classifier','tree','cache'])
ON CONFLICT(name) DO UPDATE SET kind=excluded.kind,signature=excluded.signature,
 description=excluded.description,example=excluded.example,cost_class=excluded.cost_class,tags=excluded.tags;

-- Seed edits have no updated_at; compare their canonical docs directly.
CREATE OR REPLACE FUNCTION rvbbit.capability_search_stale() RETURNS boolean
LANGUAGE sql STABLE AS $fn$
    WITH g AS (
        SELECT count(*) AS n, max(updated_at) AS ts
        FROM rvbbit.catalog_docs WHERE graph_id = 'rvbbit_capabilities'
    )
    SELECT (SELECT n FROM g) = 0
        OR EXISTS (
            SELECT 1 FROM rvbbit.capability_seed s
            WHERE NOT EXISTS (
                SELECT 1 FROM rvbbit.catalog_docs d
                WHERE d.graph_id='rvbbit_capabilities' AND d.kind=s.kind AND d.rel_name=s.name
                  AND d.doc=rvbbit.capability_doc(s.kind,s.name,s.signature,s.description,s.example,s.cost_class,s.tags)
            )
        )
        OR greatest(
               coalesce((SELECT max(updated_at) FROM rvbbit.operators), 'epoch'::timestamptz),
               coalesce((SELECT max(updated_at) FROM rvbbit.capability_catalog), 'epoch'::timestamptz),
               coalesce((SELECT max(created_at) FROM rvbbit.metric_defs), 'epoch'::timestamptz),
               coalesce((SELECT max(created_at) FROM rvbbit.cube_defs), 'epoch'::timestamptz),
               coalesce((SELECT max(created_at) FROM rvbbit.brain_sources), 'epoch'::timestamptz)
           ) > coalesce((SELECT ts FROM g), 'epoch'::timestamptz);
$fn$;

