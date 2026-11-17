-- 0381: let wide structured extraction finish its JSON object.
--
-- A controlled replay of the known 19-field Enron outlier needed 3,117 output
-- tokens with thinking disabled. Promote the managed extraction route from the
-- intermediate 2,048-token ceiling to 4,096 without changing other LLM calls.

UPDATE rvbbit.operators
SET steps = jsonb_set(steps, '{0,max_tokens}', '4096'::jsonb, false)
WHERE name = 'clover_llm_extract'
  AND jsonb_typeof(steps) = 'array'
  AND steps #>> '{0,kind}' = 'llm'
  AND steps #>> '{0,provider}' = 'clover_llm'
  AND steps #>> '{0,model}' = 'clover'
  AND steps #>> '{0,enable_thinking}' = 'false'
  AND steps #>> '{0,max_tokens}' IN ('700', '2048');
