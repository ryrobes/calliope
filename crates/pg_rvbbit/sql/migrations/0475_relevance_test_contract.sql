-- Replace the shipped BGE score cutoffs with a portable reranker contract.
-- Custom tests and historical run definitions are preserved.
UPDATE rvbbit.operators SET tests = '[{"name": "relevance_order", "sql": "SELECT rvbbit.clover_relevance(''quick sql engine'', ''fast database'') > rvbbit.clover_relevance(''a nice sandwich'', ''fast database'') AS related_ranks_higher", "expect": {"type": "exact", "value": true}, "description": "Related text must outrank unrelated text. Reranker scores are not calibrated probabilities."}, {"name": "score_bounds", "sql": "SELECT bool_and(score IS NOT NULL AND score BETWEEN 0 AND 1) AS scores_in_range FROM (VALUES (rvbbit.clover_relevance(''quick sql engine'', ''fast database'')), (rvbbit.clover_relevance(''a nice sandwich'', ''fast database''))) AS scores(score)", "expect": {"type": "exact", "value": true}, "description": "Both scores must be non-null and within the 0..1 contract; no model-specific relevance cutoff."}]'::jsonb WHERE name = 'clover_relevance'
  AND tests = $old$[{"name": "close", "sql": "SELECT rvbbit.clover_relevance('quick sql engine','fast database')", "expect": {"type": "min", "value": "0.8"}, "description": ""}, {"name": "far", "sql": "SELECT rvbbit.clover_relevance('a nice sandwich','fast database')", "expect": {"type": "max", "value": "0.3"}, "description": ""}]$old$::jsonb;

-- Keep a later reinstall from the stored managed pack from restoring old cutoffs.
DO $migration$
DECLARE old_instruction text := $old_instruction$UPDATE rvbbit.operators SET tests = '[{"name": "close", "sql": "SELECT rvbbit.clover_relevance(''quick sql engine'',''fast database'')", "expect": {"type": "min", "value": "0.8"}, "description": ""}, {"name": "far", "sql": "SELECT rvbbit.clover_relevance(''a nice sandwich'',''fast database'')", "expect": {"type": "max", "value": "0.3"}, "description": ""}]'::jsonb WHERE name = 'clover_relevance';$old_instruction$;
        new_instruction text := $new_instruction$UPDATE rvbbit.operators SET tests = '[{"name": "relevance_order", "sql": "SELECT rvbbit.clover_relevance(''quick sql engine'', ''fast database'') > rvbbit.clover_relevance(''a nice sandwich'', ''fast database'') AS related_ranks_higher", "expect": {"type": "exact", "value": true}, "description": "Related text must outrank unrelated text. Reranker scores are not calibrated probabilities."}, {"name": "score_bounds", "sql": "SELECT bool_and(score IS NOT NULL AND score BETWEEN 0 AND 1) AS scores_in_range FROM (VALUES (rvbbit.clover_relevance(''quick sql engine'', ''fast database'')), (rvbbit.clover_relevance(''a nice sandwich'', ''fast database''))) AS scores(score)", "expect": {"type": "exact", "value": true}, "description": "Both scores must be non-null and within the 0..1 contract; no model-specific relevance cutoff."}]'::jsonb WHERE name = 'clover_relevance';$new_instruction$;
BEGIN
  IF to_regclass('rvbbit.capability_catalog') IS NOT NULL THEN
    UPDATE rvbbit.capability_catalog SET
      manifest=replace(manifest::text,to_jsonb(old_instruction)::text,to_jsonb(new_instruction)::text)::jsonb,
      catalog_entry=replace(catalog_entry::text,to_jsonb(old_instruction)::text,to_jsonb(new_instruction)::text)::jsonb
    WHERE id='managed/clover';
  END IF;
END $migration$;
