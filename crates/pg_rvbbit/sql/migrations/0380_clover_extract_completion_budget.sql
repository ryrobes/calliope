-- 0380: structured extraction should spend tokens on JSON, not hidden thought.
--
-- The managed clover_llm_extract operator shipped with a 700-token completion
-- ceiling. Real 19-field records can legitimately exceed it, while Gemma's
-- optional thinking mode can consume the entire allowance before producing a
-- complete object. Preserve locally customized operators, but upgrade the
-- recognizable managed step in place for existing installations.

UPDATE rvbbit.operators
SET steps = jsonb_set(
        CASE
          WHEN steps #>> '{0,max_tokens}' = '700'
            THEN jsonb_set(steps, '{0,max_tokens}', '2048'::jsonb, false)
          ELSE steps
        END,
        '{0,enable_thinking}',
        'false'::jsonb,
        true
    )
WHERE name = 'clover_llm_extract'
  AND jsonb_typeof(steps) = 'array'
  AND steps #>> '{0,kind}' = 'llm'
  AND steps #>> '{0,provider}' = 'clover_llm'
  AND steps #>> '{0,model}' = 'clover';

