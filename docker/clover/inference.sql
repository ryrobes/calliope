-- Shared model selection for a self-hosted installation. Re-applied after the
-- managed catalog so reinstalling SQL operators cannot restore old defaults.
CREATE TABLE IF NOT EXISTS rvbbit.inference_settings (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    profile jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION rvbbit.inference_model(capability text)
RETURNS text LANGUAGE sql STABLE AS $$
    SELECT coalesce((SELECT profile->>(CASE capability WHEN 'decisions' THEN 'decision_model'
        WHEN 'rerank' THEN 'rerank_model' ELSE 'embedding_model' END)
        FROM rvbbit.inference_settings WHERE singleton),
        CASE WHEN capability='decisions' THEN 'typesafe/jev-1.13' END)
$$;

CREATE OR REPLACE FUNCTION rvbbit.apply_inference_profile(p jsonb)
RETURNS jsonb LANGUAGE plpgsql AS $fn$
DECLARE old_profile jsonb; m text; f record; body text;
BEGIN
    IF jsonb_typeof(p) IS DISTINCT FROM 'object'
       OR coalesce(p->>'embedding_model','')='' OR coalesce(p->>'decision_model','')=''
       OR coalesce(p->>'rerank_model','')='' OR NOT coalesce((p->>'embedding_dimensions')::int,0) BETWEEN 1 AND 4096 THEN
        RAISE EXCEPTION 'Incomplete inference profile';
    END IF;
    SELECT profile INTO old_profile FROM rvbbit.inference_settings WHERE singleton FOR UPDATE;
    IF old_profile IS DISTINCT FROM p THEN
        -- The extension caches by specialist and text. Purge when that
        -- specialist's vector space changes; persistent corpora are rebuilt
        -- explicitly by reembed.py, never silently compared across spaces.
        IF old_profile->>'embedding_model' IS DISTINCT FROM p->>'embedding_model'
           OR old_profile->>'embedding_dimensions' IS DISTINCT FROM p->>'embedding_dimensions'
           OR old_profile->>'embedding_encoding' IS DISTINCT FROM p->>'embedding_encoding' THEN
            DELETE FROM rvbbit.embedding_cache WHERE specialist='embed';
        END IF;
        INSERT INTO rvbbit.inference_settings(singleton,profile) VALUES(true,p)
        ON CONFLICT(singleton) DO UPDATE SET profile=excluded.profile,updated_at=now();
    END IF;
    UPDATE rvbbit.backends SET transport_opts=coalesce(transport_opts,'{}'::jsonb)
        || jsonb_build_object('model','embed','dimensions',(p->>'embedding_dimensions')::int,
                             'embedding_profile',p->>'embedding_model')
        WHERE name='embed';
    m := p->>'decision_model';
    PERFORM rvbbit.create_operator('clover_decide',ARRAY['state','questions'],'jsonb',
        op_arg_types=>ARRAY['jsonb','jsonb'],op_model=>m,op_parser=>'json',
        op_description=>'Typed decision using the installation model. Returns answers, scores, model, contract and usage.',
        op_steps=>jsonb_build_array(jsonb_build_object('name','decision','kind','specialist','specialist','decisions',
            'inputs',jsonb_build_object('model',m,'contract','clover-decisions-v1','state','{{state}}','questions','{{questions}}'))));
    PERFORM rvbbit.create_operator('clover_decide_model',ARRAY['state','questions','model'],'jsonb',
        op_arg_types=>ARRAY['jsonb','jsonb','text'],op_model=>'openrouter-decisions',op_parser=>'json',
        op_description=>'Evaluate the same typed decision with an explicit model. Model participates in receipt identity.',
        op_steps=>'[{"name":"decision","kind":"specialist","specialist":"decisions","inputs":{"model":"{{model}}","contract":"clover-decisions-v1","state":"{{state}}","questions":"{{questions}}"}}]'::jsonb);
    -- These batch paths bypass the scalar operator. Preserve their deployed
    -- definitions while replacing the one historical model literal.
    FOR f IN SELECT oid FROM pg_proc WHERE pronamespace='rvbbit'::regnamespace
        AND proname IN ('meaning_judge_columns','meaning_assign_unplaced') LOOP
        body := pg_get_functiondef(f.oid);
        IF position('''typesafe/jev-1.13''' IN body)>0 THEN
            EXECUTE replace(body,'''typesafe/jev-1.13''','rvbbit.inference_model(''decisions'')');
        END IF;
    END LOOP;
    UPDATE rvbbit.operators SET model=p->>'rerank_model', updated_at=now()
        WHERE name IN ('clover_relevance','clover_means');
    IF to_regprocedure('rvbbit.hindsight_embedding_env(text)') IS NOT NULL THEN
        body := pg_get_functiondef('rvbbit.hindsight_embedding_env(text)'::regprocedure);
        EXECUTE replace(body, 'coalesce(src.source_model, src.transport_opts->>''model'')',
                             'coalesce(src.transport_opts->>''model'', src.source_model)');
    END IF;
    PERFORM rvbbit.flush_cache();
    PERFORM rvbbit.reload_backends();
    RETURN p;
END
$fn$;
