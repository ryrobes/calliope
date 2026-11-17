-- Update shipped model defaults without changing custom models or historical receipts.
-- OpenRouter's wire ID is openai/gpt-6-luna; the provider-qualified label is
-- openrouter/openai/gpt-6-luna. A managed Clover route owns its upstream choice.
DO $$
DECLARE f record;
BEGIN
  FOR f IN SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='rvbbit' AND p.proname IN ('create_operator','set_semantic_model','set_cube_model')
  LOOP
    EXECUTE replace(pg_get_functiondef(f.oid),'openai/gpt-5.4-mini','openai/gpt-6-luna');
  END LOOP;
END $$;

UPDATE rvbbit.operators SET model=CASE WHEN model LIKE 'openrouter/%'
  THEN 'openrouter/openai/gpt-6-luna' ELSE 'openai/gpt-6-luna' END
WHERE model IN ('openai/gpt-5.4-mini','openai/gpt-5.6-luna',
  'openrouter/openai/gpt-5.4-mini','openrouter/openai/gpt-5.6-luna');

-- Exact JSON string values only, covering explicit step and per-call defaults.
UPDATE rvbbit.operators SET
 steps=replace(replace(steps::text,'"openai/gpt-5.4-mini"','"openai/gpt-6-luna"'),
  '"openai/gpt-5.6-luna"','"openai/gpt-6-luna"')::jsonb,
 opts_default=replace(replace(opts_default::text,'"openai/gpt-5.4-mini"','"openai/gpt-6-luna"'),
  '"openai/gpt-5.6-luna"','"openai/gpt-6-luna"')::jsonb
WHERE coalesce(steps::text,'') LIKE '%"openai/gpt-5.4-mini"%'
   OR coalesce(steps::text,'') LIKE '%"openai/gpt-5.6-luna"%'
   OR opts_default::text LIKE '%"openai/gpt-5.4-mini"%'
   OR opts_default::text LIKE '%"openai/gpt-5.6-luna"%';
