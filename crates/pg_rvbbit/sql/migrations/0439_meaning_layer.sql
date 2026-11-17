-- 0439_meaning_layer — Meaning: what the data is, in one layer (docs/MEANING_PLAN.md).
--
-- The floor of understanding for every relation and column the source map
-- admits: what it is, how it relates, whether it deserves metrics, whether it
-- drifts. Deterministic facts come from the catalog fingerprints; the
-- classification-shaped judgments (relation kind, time grain, worthiness,
-- plumbing, PII, column roles) come from Jev (rvbbit.clover_decide, memoized),
-- six questions per relation and two per column, batched eight at a time.
-- Nothing here is canon: cards serve provisionally with confidence and
-- provenance, and rvbbit.meaning_overrides lets a person state otherwise.
--
-- Sweep: CALL rvbbit.meaning_sweep(120) every ten minutes (schedule_meaning_sweep).
-- Read:  rvbbit.meaning_card(schema, rel), rvbbit.meaning_column_card(schema, rel, col),
--        rvbbit.meaning_overview, rvbbit.meaning_status().

CREATE TABLE IF NOT EXISTS rvbbit.meaning_runs (
    run_id          bigserial PRIMARY KEY,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    source          text NOT NULL DEFAULT 'cron',
    budget_seconds  int,
    relations_done  int NOT NULL DEFAULT 0,
    columns_done    int NOT NULL DEFAULT 0,
    links_done      int NOT NULL DEFAULT 0,
    errors          int NOT NULL DEFAULT 0,
    cost_usd        numeric(12,6) NOT NULL DEFAULT 0,
    notes           jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS rvbbit.meaning_relations (
    relation        text PRIMARY KEY,                 -- lower(schema.rel)
    schema_name     text NOT NULL,
    rel_name        text NOT NULL,
    source_system   text,
    source_role     text,
    fingerprint     text,                             -- md5 of the judged profile
    profile         jsonb,                            -- the state Jev saw
    kind            text,                             -- Jev choice
    kind_p          numeric(4,3),
    kind_probs      jsonb,
    time_grain      text,
    time_column     text,
    entity_keys     text[] NOT NULL DEFAULT '{}',
    custom_fields   int NOT NULL DEFAULT 0,
    n_rows          bigint,
    n_columns       int,
    metric_worthy   numeric(4,3),
    plumbing        numeric(4,3),
    has_pii         numeric(4,3),
    customized      numeric(4,3),
    what            text,                             -- one-line description (template until Tier 2 prose)
    business_name   text,                             -- recognition / override
    domain_key      text,
    confidence      numeric(4,3),
    drift           jsonb,
    usage           jsonb,
    status          text NOT NULL DEFAULT 'pending',  -- pending | judged | error
    error           text,
    attempts        int NOT NULL DEFAULT 0,
    judged_at       timestamptz,
    judge_model     text,
    cost_usd        numeric(12,8) NOT NULL DEFAULT 0,
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS meaning_relations_schema_idx ON rvbbit.meaning_relations (schema_name, rel_name);
CREATE INDEX IF NOT EXISTS meaning_relations_status_idx ON rvbbit.meaning_relations (status, judged_at);

CREATE TABLE IF NOT EXISTS rvbbit.meaning_columns (
    relation        text NOT NULL,
    col_name        text NOT NULL,
    fingerprint     text,
    profile         jsonb,
    data_type       text,
    role            text,                             -- identifier | reference | time | measure | category | flag | free_text | structured_blob | unused
    role_p          numeric(4,3),
    role_probs      jsonb,
    ref_target      text,                             -- best verified join target (relation)
    pii             numeric(4,3),
    ndv             bigint,
    null_frac       numeric(6,5),
    top_values      jsonb,
    what            text,
    basis           text,                             -- rule | jev | override
    judged_at       timestamptz,
    judge_model     text,
    cost_usd        numeric(12,8) NOT NULL DEFAULT 0,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (relation, col_name)
);

CREATE TABLE IF NOT EXISTS rvbbit.meaning_links (
    from_relation   text NOT NULL,
    from_column     text NOT NULL,
    to_relation     text NOT NULL,
    to_column       text NOT NULL,
    basis           text NOT NULL,                    -- fk | naming | recognition | override
    state           text NOT NULL,                    -- verified | unverified | contradicted
    match_rate      numeric(5,4),
    sampled         int,
    confidence      numeric(4,3),
    evidence        jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (from_relation, from_column, to_relation, to_column)
);
CREATE INDEX IF NOT EXISTS meaning_links_to_idx ON rvbbit.meaning_links (to_relation);

CREATE TABLE IF NOT EXISTS rvbbit.meaning_overrides (
    override_id     bigserial PRIMARY KEY,
    relation        text NOT NULL,
    col_name        text,                             -- NULL = the relation itself
    field           text NOT NULL,                    -- kind | business_name | what | domain_key | ignore | role | ref_target | pii
    value           jsonb NOT NULL,
    reason          text,
    actor           text NOT NULL DEFAULT current_user,
    created_at      timestamptz NOT NULL DEFAULT now(),
    retired_at      timestamptz
);
CREATE INDEX IF NOT EXISTS meaning_overrides_rel_idx ON rvbbit.meaning_overrides (relation, col_name) WHERE retired_at IS NULL;

-- ---------------------------------------------------------------------------
-- Fingerprints: the latest catalog snapshot for a relation and its columns.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_latest_fingerprints(p_schema text, p_rel text)
RETURNS TABLE (kind text, col_name text, run_id bigint, captured_at timestamptz, fingerprint jsonb)
LANGUAGE sql STABLE AS $fn$
    SELECT DISTINCT ON (s.obj_key) s.kind, s.col_name, s.run_id, s.captured_at, s.fingerprint
      FROM rvbbit.catalog_snapshots s
     WHERE s.graph_id = 'db_catalog'
       AND s.schema_name = p_schema AND s.rel_name = p_rel
       AND (s.obj_key = p_schema || '.' || p_rel OR s.obj_key LIKE p_schema || '.' || p_rel || '.%')
     ORDER BY s.obj_key, s.run_id DESC
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_fingerprint(p jsonb) RETURNS text
LANGUAGE sql IMMUTABLE AS $fn$ SELECT md5(coalesce(p::text, '')) $fn$;

-- Active overrides for a relation (col_name NULL) or column, as {field: value}.
CREATE OR REPLACE FUNCTION rvbbit.meaning_overrides_for(p_relation text, p_col text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(jsonb_object_agg(o.field, o.value ORDER BY o.created_at), '{}'::jsonb)
      FROM (SELECT DISTINCT ON (field) field, value, created_at
              FROM rvbbit.meaning_overrides
             WHERE relation = lower(p_relation) AND retired_at IS NULL
               AND col_name IS NOT DISTINCT FROM p_col
             ORDER BY field, created_at DESC) o
$fn$;

-- ---------------------------------------------------------------------------
-- Profiles: the compact structured state a judge sees.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_relation_profile(p_schema text, p_rel text)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    v_rel      text := lower(p_schema || '.' || p_rel);
    v_table    jsonb;
    v_cols     jsonb;           -- [{name,type,ndv,seen,nf,pk,fk,ord,top,min,max}]
    v_keys     jsonb;
    v_times    jsonb;
    v_cats     jsonb;
    v_measures jsonb;
    v_names    jsonb;
    v_custom   int;
    v_usage    jsonb;
    v_source   record;
    v_stated   jsonb;
BEGIN
    SELECT f.fingerprint INTO v_table
      FROM rvbbit.meaning_latest_fingerprints(p_schema, p_rel) f WHERE f.kind = 'db_table';
    IF v_table IS NULL THEN RETURN NULL; END IF;

    SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
             'name', f.col_name, 'type', f.fingerprint->>'data_type',
             'ndv', nullif(f.fingerprint->>'ndv','')::bigint, 'seen', nullif(f.fingerprint->>'n_seen','')::bigint,
             'nf', coalesce(nullif(f.fingerprint->>'null_frac','')::numeric, 0),
             'pk', nullif(coalesce((f.fingerprint->>'is_pk')::boolean, false), false),
             'fk', f.fingerprint->>'fk_target',
             'ord', coalesce(nullif(f.fingerprint->>'ordinal','')::int, 0),
             'top', (SELECT jsonb_agg(e->>'value' ORDER BY (e->>'n')::bigint DESC)
                       FROM jsonb_array_elements(rvbbit._jsonb_arr(f.fingerprint->'example_values')) WITH ORDINALITY x(e, i) WHERE i <= 5),
             'min', left(f.fingerprint->>'min', 40), 'max', left(f.fingerprint->>'max', 40)))
             ORDER BY coalesce(nullif(f.fingerprint->>'ordinal','')::int, 0)), '[]'::jsonb)
      INTO v_cols
      FROM rvbbit.meaning_latest_fingerprints(p_schema, p_rel) f
     WHERE f.kind = 'db_column';

    SELECT count(*) FILTER (WHERE c->>'name' ~* '_c$') INTO v_custom FROM jsonb_array_elements(v_cols) c;

    -- keys: declared pk, or id-like columns whose distinct count is (nearly) the sample
    SELECT coalesce(jsonb_agg(c->>'name' ORDER BY (c->>'ord')::int), '[]'::jsonb) INTO v_keys
      FROM jsonb_array_elements(v_cols) c
     WHERE coalesce((c->>'pk')::boolean, false)
        OR ((c->>'name') ~* '(^|_)id$' AND coalesce((c->>'seen')::bigint, 0) > 0
            AND coalesce((c->>'ndv')::bigint, 0) >= (c->>'seen')::bigint * 0.95 AND (c->>'nf')::numeric < 0.05);

    SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object('name', c->>'name', 'type', c->>'type',
                    'null_pct', round((c->>'nf')::numeric * 100), 'min', c->>'min', 'max', c->>'max')) ORDER BY (c->>'ord')::int), '[]'::jsonb)
      INTO v_times
      FROM (SELECT c FROM jsonb_array_elements(v_cols) c WHERE (c->>'type') ~* '^(timestamp|date)' ORDER BY (c->>'ord')::int LIMIT 8) t;

    SELECT coalesce(jsonb_agg(jsonb_build_object('name', c->>'name', 'ndv', (c->>'ndv')::bigint,
                    'null_pct', round((c->>'nf')::numeric * 100), 'top', c->'top') ORDER BY (c->>'ndv')::bigint), '[]'::jsonb)
      INTO v_cats
      FROM (SELECT c FROM jsonb_array_elements(v_cols) c
             WHERE (c->>'type') ~* '^(character|text|varchar)' AND (c->>'ndv')::bigint BETWEEN 2 AND 40 AND c ? 'top'
               AND NOT ((c->>'name') ~* '(^|_)id(_c)?$|_key$|uuid|guid|token')
             ORDER BY CASE WHEN (c->>'name') ~* '(status|stage|type|source|category|kind|state|reason|channel|segment|tier|priority|level)' THEN 0 ELSE 1 END,
                      (c->>'nf')::numeric, (c->>'ndv')::bigint LIMIT 6) t;

    SELECT coalesce(jsonb_agg(c->>'name' ORDER BY (c->>'ord')::int), '[]'::jsonb) INTO v_measures
      FROM (SELECT c FROM jsonb_array_elements(v_cols) c
             WHERE (c->>'type') ~* '^(numeric|double|real|integer|bigint|smallint|money|decimal)'
               AND NOT ((c->>'name') ~* '(^|_)id$') AND coalesce((c->>'ndv')::bigint, 0) > 5 AND (c->>'nf')::numeric < 0.98
             ORDER BY (c->>'ord')::int LIMIT 12) t;

    SELECT coalesce(jsonb_agg((c->>'name') || ':' || regexp_replace(coalesce(c->>'type',''), '\(.*\)', '') ORDER BY (c->>'ord')::int), '[]'::jsonb)
      INTO v_names
      FROM (SELECT c FROM jsonb_array_elements(v_cols) c ORDER BY (c->>'ord')::int LIMIT 60) t;

    SELECT jsonb_strip_nulls(jsonb_build_object('touches_30d', u.touches_30d, 'users', u.users, 'last_touch', u.last_touch))
      INTO v_usage FROM rvbbit.usage_snapshot u WHERE u.object = v_rel;

    SELECT bs.source_system, bs.role, bs.adapter INTO v_source
      FROM rvbbit.business_sources bs WHERE bs.schema_name = p_schema;

    v_stated := rvbbit.meaning_overrides_for(v_rel, NULL);

    RETURN jsonb_strip_nulls(jsonb_build_object(
        'relation', v_rel,
        'source_system', coalesce(v_source.source_system, p_schema),
        'source_role', v_source.role,
        'adapter', v_source.adapter,
        'rows', nullif(v_table->>'n_rows','')::bigint,
        'columns', nullif(v_table->>'n_columns','')::int,
        'sampled_rows', nullif(v_table->>'n_sampled','')::bigint,
        'custom_fields', v_custom,
        'keys', v_keys,
        'time_columns', v_times,
        'categorical', v_cats,
        'measures', v_measures,
        'column_names', v_names,
        'usage', v_usage,
        'stated_by_people', CASE WHEN v_stated = '{}'::jsonb THEN NULL ELSE v_stated END));
END $fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_column_profile_from(p_fp jsonb, p_schema text, p_rel text, p_col text, p_table_kind text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'column', p_col,
        'table', lower(p_schema || '.' || p_rel),
        'table_kind', p_table_kind,
        'type', p_fp->>'data_type',
        'ndv', nullif(p_fp->>'ndv','')::bigint,
        'sampled', nullif(p_fp->>'n_seen','')::bigint,
        'null_pct', round(nullif(p_fp->>'null_frac','')::numeric * 100),
        'is_pk', nullif((p_fp->>'is_pk')::boolean, false),
        'fk_target', p_fp->>'fk_target',
        'min', left(p_fp->>'min', 40), 'max', left(p_fp->>'max', 40),
        'top', (SELECT jsonb_agg(jsonb_build_object('v', left(e->>'value', 60), 'n', (e->>'n')::bigint) ORDER BY (e->>'n')::bigint DESC)
                  FROM jsonb_array_elements(rvbbit._jsonb_arr(p_fp->'example_values')) WITH ORDINALITY x(e, i) WHERE i <= 5),
        'stated_by_people', nullif(rvbbit.meaning_overrides_for(lower(p_schema || '.' || p_rel), p_col), '{}'::jsonb)))
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_column_profile(p_schema text, p_rel text, p_col text, p_table_kind text DEFAULT NULL)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT rvbbit.meaning_column_profile_from(f.fingerprint, p_schema, p_rel, p_col, p_table_kind)
      FROM rvbbit.meaning_latest_fingerprints(p_schema, p_rel) f
     WHERE f.kind = 'db_column' AND f.col_name = p_col
$fn$;


-- ---------------------------------------------------------------------------
-- Questions (Jev contract). Constant JSON; edits here invalidate the memo cache.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_relation_questions() RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $fn$
SELECT $j${
 "kind": {"type": "choice", "instructions": "What kind of table is this? Judge from the row mass, keys, time columns, categorical columns, measures and the source system.",
   "criteria": {
     "event_log": "append-only records of things that happened, one row per event with its own timestamp",
     "transactional_fact": "business activity or transaction rows carrying measures (orders, payments, ad delivery by day, sessions)",
     "entity_master": "one row per business object such as a lead, account, course, user or campaign, with its attributes",
     "history_or_snapshot": "versioned or point-in-time copies of another object (history, audit, changes, field tracking)",
     "aggregate_or_cube": "a pre-aggregated or modeled rollup built from other tables",
     "staging_or_raw": "raw or staging landing data from an ingestion pipeline, not yet modeled",
     "system_plumbing": "operational metadata about pipelines, connectors, jobs, sync state or the platform itself",
     "lookup_or_config": "a small reference list, picklist or configuration table"}},
 "time_grain": {"type": "choice", "instructions": "How is time represented in the rows?",
   "criteria": {
     "none": "no meaningful event or period time",
     "per_row_timestamp": "each row carries the time it happened, was created or was changed",
     "daily_period": "rows are daily buckets or daily stats",
     "monthly_period": "rows are monthly or longer period buckets"}},
 "metric_worthy": {"type": "noul", "instructions": "A business analyst would want standing KPIs or metrics computed from this table: it records business activity, objects or outcomes rather than system plumbing."},
 "plumbing": {"type": "noul", "instructions": "This table is internal system, pipeline or platform plumbing rather than the company's business data."},
 "has_pii": {"type": "noul", "instructions": "Rows contain personal data about individual people such as names, emails, phone numbers, addresses or personal identifiers."},
 "customized": {"type": "noul", "instructions": "The table carries many customer-specific custom fields (for example Salesforce *_c columns) beyond the vendor's standard schema."}
}$j$::jsonb
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_column_questions() RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $fn$
SELECT $j${
 "role": {"type": "choice", "instructions": "What role does this column play in its table? Judge from the name, type, distinct count, null share and example values.",
   "criteria": {
     "identifier": "a primary or surrogate key that uniquely identifies the row",
     "reference": "a key pointing at another table or object (foreign key, owner id, parent id, account id)",
     "time": "a date or timestamp marking when something happened or changed",
     "measure": "a numeric quantity that is meaningful to sum or average (amount, count, cost, score, duration)",
     "category": "a low-cardinality label, status, type or code used to group or filter rows",
     "flag": "a boolean or yes/no indicator",
     "free_text": "human-written names, titles, notes, descriptions, emails, phone numbers or addresses",
     "structured_blob": "json, xml, or another serialized payload",
     "unused": "a column that is entirely null or holds a single constant value"}},
 "pii": {"type": "noul", "instructions": "Values identify or describe an individual person: a name, email, phone number, street address, or personal identifier."}
}$j$::jsonb
$fn$;

-- ---------------------------------------------------------------------------
-- Plain-English "what" from facts (until Tier 2 prose lands).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_what_sentence(p_profile jsonb, p_kind text, p_time_column text, p_business_name text)
RETURNS text LANGUAGE plpgsql IMMUTABLE AS $fn$
DECLARE
    v_rel   text := p_profile->>'relation';
    v_name  text := coalesce(p_business_name, rvbbit.meaning_display_name(split_part(v_rel, '.', 2)));
    v_src   text := coalesce(p_profile->>'source_system', split_part(v_rel, '.', 1));
    v_rows  bigint := nullif(p_profile->>'rows','')::bigint;
    v_cols  int := nullif(p_profile->>'columns','')::int;
    v_cust  int := coalesce(nullif(p_profile->>'custom_fields','')::int, 0);
    v_keys  text := (SELECT string_agg(k, ', ') FROM (SELECT jsonb_array_elements_text(rvbbit._jsonb_arr(p_profile->'keys')) k LIMIT 3) x);
    v_cat   jsonb := (p_profile->'categorical')->0;
    v_kind  text;
    v_out   text;
BEGIN
    v_kind := CASE p_kind
        WHEN 'entity_master'       THEN format('one row per %s', lower(v_name))
        WHEN 'event_log'           THEN format('a log of %s events', lower(v_name))
        WHEN 'transactional_fact'  THEN format('%s activity rows with measures', v_name)
        WHEN 'history_or_snapshot' THEN format('a history of changes to %s', lower(regexp_replace(v_name, ' History$', '')))
        WHEN 'aggregate_or_cube'   THEN format('a pre-aggregated rollup (%s)', v_name)
        WHEN 'staging_or_raw'      THEN format('raw landed %s data', lower(v_name))
        WHEN 'system_plumbing'     THEN format('system plumbing (%s)', v_name)
        WHEN 'lookup_or_config'    THEN format('a reference list of %s', lower(v_name))
        ELSE v_name END;
    v_out := initcap(left(v_kind, 1)) || substr(v_kind, 2) || ' from ' || v_src;
    IF v_rows IS NOT NULL THEN
        v_out := v_out || format(' (%s rows', to_char(v_rows, 'FM999,999,999,999'));
        IF v_cols IS NOT NULL THEN v_out := v_out || format(', %s columns', v_cols); END IF;
        IF v_cust > 0 THEN v_out := v_out || format(', %s custom fields', v_cust); END IF;
        v_out := v_out || ')';
    END IF;
    v_out := v_out || '.';
    IF v_keys IS NOT NULL THEN v_out := v_out || ' Keyed by ' || v_keys || '.'; END IF;
    IF p_time_column IS NOT NULL THEN v_out := v_out || ' Time: ' || p_time_column || '.'; END IF;
    IF v_cat IS NOT NULL THEN
        v_out := v_out || format(' %s has %s values (%s).', v_cat->>'name', v_cat->>'ndv',
                    (SELECT string_agg(v, ', ') FROM (SELECT jsonb_array_elements_text(rvbbit._jsonb_arr(v_cat->'top')) v LIMIT 4) t));
    END IF;
    RETURN v_out;
END $fn$;

-- Deterministic display name until recognition or a person names it: strip
-- custom-object suffixes (_c, __c, _hd), title-case, and name known package
-- prefixes ("SMS Magic: Conversation") instead of leaking them as words.
CREATE OR REPLACE FUNCTION rvbbit.meaning_display_name(p_rel text) RETURNS text
LANGUAGE sql IMMUTABLE AS $fn$
    WITH pk AS (
        SELECT * FROM (VALUES
            ('smagicinteract_', 'SMS Magic'), ('nbavs_', 'NBAVS'), ('kruvi_', 'Kruvi'), ('mert_mert_', 'Mert'),
            ('et4ae5_', 'Marketing Cloud'), ('pi_', 'Pardot'), ('stg_', 'staging'), ('int_', 'intermediate'),
            ('fct_', 'fact'), ('dim_', 'dimension'), ('mart_', 'mart'), ('raw_', 'raw')) v(prefix, label)
    ), m AS (
        SELECT pk.label, substr(lower(p_rel), length(pk.prefix) + 1) AS rest
          FROM pk WHERE lower(p_rel) LIKE pk.prefix || '%' ORDER BY length(pk.prefix) DESC LIMIT 1
    )
    SELECT CASE WHEN m.label IS NOT NULL AND m.label NOT IN ('staging','intermediate','fact','dimension','mart','raw')
                THEN m.label || ': ' ELSE '' END
           || initcap(regexp_replace(replace(regexp_replace(coalesce(m.rest, lower(p_rel)), '(__c|_c|_hd|_history|_feed)$', ''), '_', ' '), '\s+', ' ', 'g'))
           || CASE WHEN lower(p_rel) ~ '_history$' THEN ' History' WHEN lower(p_rel) ~ '_feed$' THEN ' Feed' ELSE '' END
      FROM (SELECT 1) one LEFT JOIN m ON true
$fn$;

-- Deterministic time column pick: prefer created/occurred/event over updated/modified/synced.
CREATE OR REPLACE FUNCTION rvbbit.meaning_pick_time_column(p_profile jsonb) RETURNS text
LANGUAGE sql IMMUTABLE AS $fn$
    SELECT t->>'name'
      FROM jsonb_array_elements(rvbbit._jsonb_arr(p_profile->'time_columns')) t
     WHERE coalesce((t->>'null_pct')::numeric, 100) < 50
     ORDER BY CASE
                WHEN t->>'name' ~* '(created|occurred|event|activity|transaction|start|^date$|_date$|^day$|^time_stamp$)' THEN 0
                WHEN t->>'name' ~* '(updated|modified|last_)' THEN 2
                WHEN t->>'name' ~* '(synced|_dlt_|fivetran|loaded)' THEN 4
                ELSE 1 END,
              coalesce((t->>'null_pct')::numeric, 100)
     LIMIT 1
$fn$;

-- ---------------------------------------------------------------------------
-- Judge one relation (Jev, six questions, memoized).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_judge_relation(p_schema text, p_rel text)
RETURNS jsonb LANGUAGE plpgsql AS $fn$
DECLARE
    v_rel     text := lower(p_schema || '.' || p_rel);
    v_profile jsonb;
    v_fp      text;
    v_ans     jsonb;
    v_a       jsonb;
    v_kind    text;
    v_kind_p  numeric;
    v_time    text;
    v_cost    numeric := 0;
    v_stated  jsonb;
    v_conf    numeric;
    v_keys    text[];
BEGIN
    v_profile := rvbbit.meaning_relation_profile(p_schema, p_rel);
    IF v_profile IS NULL THEN
        RETURN jsonb_build_object('status', 'no_fingerprint');
    END IF;
    v_fp := rvbbit.meaning_fingerprint(v_profile);
    v_stated := rvbbit.meaning_overrides_for(v_rel, NULL);

    v_ans := rvbbit.clover_decide(v_profile, rvbbit.meaning_relation_questions());
    IF v_ans IS NULL OR v_ans->'answers' IS NULL THEN
        INSERT INTO rvbbit.meaning_relations AS m (relation, schema_name, rel_name, source_system, source_role, profile, fingerprint, status, error, attempts, updated_at)
        VALUES (v_rel, p_schema, p_rel, v_profile->>'source_system', v_profile->>'source_role', v_profile, NULL, 'error', 'judge returned no answers', 1, now())
        ON CONFLICT (relation) DO UPDATE SET status = 'error', error = EXCLUDED.error, attempts = m.attempts + 1, profile = EXCLUDED.profile, updated_at = now();
        RETURN jsonb_build_object('status', 'error');
    END IF;
    v_a := v_ans->'answers';
    v_cost := coalesce((v_ans #>> '{usage,cost}')::numeric, 0);
    v_kind := coalesce(v_stated->>'kind', v_a #>> '{kind,choice}');
    v_kind_p := CASE WHEN v_stated ? 'kind' THEN 1.0
                     ELSE coalesce((v_a #> '{kind,probabilities}' ->> (v_a #>> '{kind,choice}'))::numeric, 0) END;
    v_time := rvbbit.meaning_pick_time_column(v_profile);
    SELECT coalesce(array_agg(k), '{}') INTO v_keys FROM jsonb_array_elements_text(rvbbit._jsonb_arr(v_profile->'keys')) k;
    -- confidence: how sure the kind is, damped when the judge and the map disagree about plumbing
    v_conf := round(least(1.0, v_kind_p * (1 - 0.5 * abs(coalesce((v_a #>> '{plumbing,noul}')::numeric, 0)
                                       - CASE WHEN v_profile->>'source_role' = 'plumbing' THEN 1 ELSE 0 END)))::numeric, 3);

    INSERT INTO rvbbit.meaning_relations AS m (
        relation, schema_name, rel_name, source_system, source_role, fingerprint, profile,
        kind, kind_p, kind_probs, time_grain, time_column, entity_keys, custom_fields, n_rows, n_columns,
        metric_worthy, plumbing, has_pii, customized, what, business_name, domain_key, confidence,
        usage, status, error, attempts, judged_at, judge_model, cost_usd, updated_at)
    VALUES (
        v_rel, p_schema, p_rel, v_profile->>'source_system', v_profile->>'source_role', v_fp, v_profile,
        v_kind, v_kind_p, v_a #> '{kind,probabilities}', v_a #>> '{time_grain,choice}', v_time, v_keys,
        coalesce((v_profile->>'custom_fields')::int, 0), (v_profile->>'rows')::bigint, (v_profile->>'columns')::int,
        (v_a #>> '{metric_worthy,noul}')::numeric, (v_a #>> '{plumbing,noul}')::numeric,
        (v_a #>> '{has_pii,noul}')::numeric, (v_a #>> '{customized,noul}')::numeric,
        coalesce(v_stated->>'what', rvbbit.meaning_what_sentence(v_profile, v_kind, v_time, v_stated->>'business_name')),
        v_stated->>'business_name', v_stated->>'domain_key', v_conf,
        v_profile->'usage', 'judged', NULL, 1, now(), v_ans->>'model', v_cost, now())
    ON CONFLICT (relation) DO UPDATE SET
        source_system = EXCLUDED.source_system, source_role = EXCLUDED.source_role,
        fingerprint = EXCLUDED.fingerprint, profile = EXCLUDED.profile,
        kind = EXCLUDED.kind, kind_p = EXCLUDED.kind_p, kind_probs = EXCLUDED.kind_probs,
        time_grain = EXCLUDED.time_grain, time_column = EXCLUDED.time_column, entity_keys = EXCLUDED.entity_keys,
        custom_fields = EXCLUDED.custom_fields, n_rows = EXCLUDED.n_rows, n_columns = EXCLUDED.n_columns,
        metric_worthy = EXCLUDED.metric_worthy, plumbing = EXCLUDED.plumbing, has_pii = EXCLUDED.has_pii,
        customized = EXCLUDED.customized, what = EXCLUDED.what,
        business_name = coalesce(EXCLUDED.business_name, m.business_name),
        domain_key = coalesce(EXCLUDED.domain_key, m.domain_key),
        confidence = EXCLUDED.confidence, usage = EXCLUDED.usage,
        status = 'judged', error = NULL, attempts = m.attempts + 1, judged_at = now(),
        judge_model = EXCLUDED.judge_model, cost_usd = m.cost_usd + EXCLUDED.cost_usd, updated_at = now();
    RETURN jsonb_build_object('status', 'judged', 'kind', v_kind, 'cost_usd', v_cost);
END $fn$;

-- ---------------------------------------------------------------------------
-- Columns: rules first, Jev for the rest (batched through prewarm_operator).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_column_rule(p jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $fn$
    SELECT CASE
        WHEN coalesce((p->>'null_pct')::numeric, 0) >= 99.5 OR coalesce((p->>'ndv')::bigint, 0) = 0
             THEN jsonb_build_object('role', 'unused', 'p', 1.0)
        WHEN coalesce((p->>'ndv')::bigint, 0) = 1 AND coalesce((p->>'sampled')::bigint, 0) > 20
             THEN jsonb_build_object('role', 'unused', 'p', 0.95)
        WHEN (p->>'type') ~* '^bool' THEN jsonb_build_object('role', 'flag', 'p', 1.0)
        WHEN (p->>'type') ~* '^(json|jsonb|xml|bytea)' THEN jsonb_build_object('role', 'structured_blob', 'p', 1.0)
        WHEN (p->>'type') ~* '^(timestamp|date)' THEN jsonb_build_object('role', 'time', 'p', 1.0)
        WHEN coalesce((p->>'is_pk')::boolean, false) OR lower(p->>'column') = 'id'
             THEN jsonb_build_object('role', 'identifier', 'p', 1.0)
        WHEN p->>'fk_target' IS NOT NULL THEN jsonb_build_object('role', 'reference', 'p', 1.0)
        WHEN (p->>'column') ~* '(^|_)id(_c)?$' AND coalesce((p->>'ndv')::bigint, 0) > 1
             THEN jsonb_build_object('role', 'reference', 'p', 0.9)
        -- Salesforce record ids (15/18-char case-sensitive keys) in an 18-char varchar are references
        -- even when the column is named after the object (region_c, associated_course_c, who_id)
        WHEN (p->>'type') ~* '^character varying\(18\)$' AND coalesce((p->>'ndv')::bigint, 0) > 1
             AND (SELECT bool_and((t->>'v') ~ '^[a-zA-Z0-9]{15}([a-zA-Z0-9]{3})?$')
                    FROM jsonb_array_elements(rvbbit._jsonb_arr(p->'top')) t)
             THEN jsonb_build_object('role', 'reference', 'p', 0.85)
        WHEN (p->>'column') ~* '(^|_)(fivetran|dlt)_' THEN jsonb_build_object('role', 'unused', 'p', 0.9)
        ELSE NULL END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_judge_columns(p_schema text, p_rel text, p_max int DEFAULT 400)
RETURNS jsonb LANGUAGE plpgsql AS $fn$
DECLARE
    v_rel     text := lower(p_schema || '.' || p_rel);
    v_kind    text;
    v_f       record;
    v_prof    jsonb;
    v_fp      text;
    v_rule    jsonb;
    v_pending text[] := '{}';
    v_done    int := 0;
    v_jev     int := 0;
    v_cost    numeric := 0;
    v_ans     jsonb;
    v_a       jsonb;
    v_col     text;
    v_stated  jsonb;
    v_chunk   text[];
    v_i       int;
    v_j       int;
    v_batch   jsonb;
    v_profiles jsonb := '{}'::jsonb;
BEGIN
    SELECT kind INTO v_kind FROM rvbbit.meaning_relations WHERE relation = v_rel;
    FOR v_f IN
        SELECT f.col_name, f.fingerprint FROM rvbbit.meaning_latest_fingerprints(p_schema, p_rel) f
         WHERE f.kind = 'db_column' ORDER BY nullif(f.fingerprint->>'ordinal','')::int LIMIT p_max
    LOOP
        v_prof := rvbbit.meaning_column_profile_from(v_f.fingerprint, p_schema, p_rel, v_f.col_name, v_kind);
        CONTINUE WHEN v_prof IS NULL;
        v_profiles := v_profiles || jsonb_build_object(v_f.col_name, v_prof);
        v_fp := rvbbit.meaning_fingerprint(v_prof);
        IF EXISTS (SELECT 1 FROM rvbbit.meaning_columns c WHERE c.relation = v_rel AND c.col_name = v_f.col_name AND c.fingerprint = v_fp AND c.role IS NOT NULL) THEN
            CONTINUE;
        END IF;
        v_stated := rvbbit.meaning_overrides_for(v_rel, v_f.col_name);
        v_rule := CASE WHEN v_stated ? 'role' THEN jsonb_build_object('role', v_stated->>'role', 'p', 1.0)
                       ELSE rvbbit.meaning_column_rule(v_prof) END;
        INSERT INTO rvbbit.meaning_columns AS c (relation, col_name, fingerprint, profile, data_type, role, role_p, ref_target, pii, ndv, null_frac, top_values, basis, judged_at, judge_model, updated_at)
        VALUES (v_rel, v_f.col_name, v_fp, v_prof, v_prof->>'type', v_rule->>'role', (v_rule->>'p')::numeric,
                v_stated->>'ref_target', (v_stated->>'pii')::numeric,
                (v_prof->>'ndv')::bigint, coalesce((v_prof->>'null_pct')::numeric, 0) / 100, v_prof->'top',
                CASE WHEN v_stated ? 'role' THEN 'override' WHEN v_rule IS NOT NULL THEN 'rule' ELSE 'pending' END,
                CASE WHEN v_rule IS NOT NULL THEN now() END, CASE WHEN v_rule IS NOT NULL THEN 'rule' END, now())
        ON CONFLICT (relation, col_name) DO UPDATE SET
            fingerprint = EXCLUDED.fingerprint, profile = EXCLUDED.profile, data_type = EXCLUDED.data_type,
            role = coalesce(EXCLUDED.role, c.role), role_p = coalesce(EXCLUDED.role_p, c.role_p),
            ref_target = coalesce(EXCLUDED.ref_target, c.ref_target), pii = coalesce(EXCLUDED.pii, c.pii),
            ndv = EXCLUDED.ndv, null_frac = EXCLUDED.null_frac, top_values = EXCLUDED.top_values,
            basis = EXCLUDED.basis, judged_at = coalesce(EXCLUDED.judged_at, c.judged_at),
            judge_model = coalesce(EXCLUDED.judge_model, c.judge_model), updated_at = now();
        v_done := v_done + 1;
        IF v_rule IS NULL THEN v_pending := v_pending || v_f.col_name; END IF;
    END LOOP;

    -- Jev for the ambiguous residue: one decisions call per eight columns
    -- (call_specialist_batch), falling back to memoized scalar calls when the
    -- batch binding is missing or a chunk fails.
    IF array_length(v_pending, 1) > 0 THEN
        FOR v_i IN 1 .. array_length(v_pending, 1) BY 8 LOOP
            v_chunk := v_pending[v_i : v_i + 7];
            v_batch := NULL;
            IF to_regprocedure('rvbbit.call_specialist_batch(text,jsonb)') IS NOT NULL THEN
                BEGIN
                    SELECT rvbbit.call_specialist_batch('decisions', jsonb_agg(
                               jsonb_build_object('model', 'typesafe/jev-1.13', 'contract', 'clover-decisions-v1',
                                                  'state', v_profiles -> c.col,
                                                  'questions', rvbbit.meaning_column_questions()) ORDER BY c.i))
                      INTO v_batch
                      FROM unnest(v_chunk) WITH ORDINALITY AS c(col, i);
                    IF jsonb_typeof(v_batch) <> 'array' OR jsonb_array_length(v_batch) <> array_length(v_chunk, 1) THEN
                        v_batch := NULL;
                    END IF;
                EXCEPTION WHEN OTHERS THEN
                    RAISE NOTICE 'meaning: batch judge failed for %.% (%); falling back to scalar', p_schema, p_rel, left(SQLERRM, 160);
                    v_batch := NULL;
                END;
            END IF;
            FOR v_j IN 1 .. array_length(v_chunk, 1) LOOP
                v_col := v_chunk[v_j];
                IF v_batch IS NOT NULL THEN
                    v_ans := v_batch -> (v_j - 1);
                ELSE
                    v_ans := rvbbit.clover_decide(v_profiles -> v_col, rvbbit.meaning_column_questions());
                END IF;
                IF v_ans IS NULL OR v_ans->'answers' IS NULL THEN CONTINUE; END IF;
                v_a := v_ans->'answers';
                v_cost := v_cost + coalesce((v_ans #>> '{usage,cost}')::numeric, 0);
                v_jev := v_jev + 1;
                UPDATE rvbbit.meaning_columns SET
                    role = v_a #>> '{role,choice}',
                    role_p = (v_a #> '{role,probabilities}' ->> (v_a #>> '{role,choice}'))::numeric,
                    role_probs = v_a #> '{role,probabilities}',
                    pii = coalesce(pii, (v_a #>> '{pii,noul}')::numeric),
                    basis = 'jev', judged_at = now(), judge_model = v_ans->>'model',
                    cost_usd = cost_usd + coalesce((v_ans #>> '{usage,cost}')::numeric, 0), updated_at = now()
                 WHERE relation = v_rel AND col_name = v_col;
            END LOOP;
        END LOOP;
    END IF;
    RETURN jsonb_build_object('columns', v_done, 'jev', v_jev, 'cost_usd', v_cost);
END $fn$;

-- ---------------------------------------------------------------------------
-- Links: how does it relate? fk → naming → verified by a bounded value probe.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_probe_join(p_from text, p_from_col text, p_to text, p_to_col text, p_sample int DEFAULT 200)
RETURNS jsonb LANGUAGE plpgsql AS $fn$
DECLARE
    v_from_schema text := split_part(p_from, '.', 1);
    v_from_rel    text := split_part(p_from, '.', 2);
    v_to_schema   text := split_part(p_to, '.', 1);
    v_to_rel      text := split_part(p_to, '.', 2);
    v_n           int;
    v_hit         int;
BEGIN
    IF to_regclass(format('%I.%I', v_from_schema, v_from_rel)) IS NULL OR to_regclass(format('%I.%I', v_to_schema, v_to_rel)) IS NULL THEN
        RETURN NULL;
    END IF;
    BEGIN
        PERFORM set_config('statement_timeout', '4000', true);
        EXECUTE format(
            'WITH s AS (SELECT DISTINCT %1$I::text AS v FROM %2$I.%3$I WHERE %1$I IS NOT NULL LIMIT %6$s) '
            'SELECT count(*), count(*) FILTER (WHERE s.v IN (SELECT t.%7$I::text FROM %4$I.%5$I t WHERE t.%7$I IS NOT NULL)) FROM s',
            p_from_col, v_from_schema, v_from_rel, v_to_schema, v_to_rel, p_sample, p_to_col)
        INTO v_n, v_hit;
    EXCEPTION WHEN OTHERS THEN
        RETURN jsonb_build_object('error', left(SQLERRM, 200));
    END;
    RETURN jsonb_build_object('sampled', v_n, 'matched', v_hit,
                              'match_rate', CASE WHEN coalesce(v_n, 0) > 0 THEN round(v_hit::numeric / v_n, 4) ELSE NULL END);
END $fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_infer_links(p_schema text, p_rel text, p_max_columns int DEFAULT 10)
RETURNS int LANGUAGE plpgsql AS $fn$
DECLARE
    v_rel    text := lower(p_schema || '.' || p_rel);
    v_c      record;
    v_t      record;
    v_base   text;
    v_probe  jsonb;
    v_rate   numeric;
    v_state  text;
    v_basis  text;
    v_n      int := 0;
    v_tocol  text;
BEGIN
    FOR v_c IN
        SELECT c.col_name, c.profile->>'fk_target' AS fk_target
          FROM rvbbit.meaning_columns c
         WHERE c.relation = v_rel AND c.role = 'reference'
         ORDER BY (c.profile->>'fk_target') IS NOT NULL DESC, c.null_frac NULLS LAST, c.col_name
         LIMIT p_max_columns
    LOOP
        v_base := lower(regexp_replace(regexp_replace(v_c.col_name, '_c$', '', 'i'), '_?id$', '', 'i'));
        CONTINUE WHEN v_base = '';
        FOR v_t IN
            SELECT DISTINCT r.relation, r.schema_name, r.rel_name,
                   CASE WHEN r.fk_match THEN 'fk' ELSE 'naming' END AS basis
              FROM (
                SELECT m.relation, m.schema_name, m.rel_name,
                       (v_c.fk_target IS NOT NULL AND lower(v_c.fk_target) IN (m.relation, m.rel_name)) AS fk_match
                  FROM rvbbit.meaning_relations m
                 WHERE m.relation <> v_rel
                   AND (
                        (v_c.fk_target IS NOT NULL AND lower(v_c.fk_target) IN (m.relation, m.rel_name))
                     OR (m.schema_name = p_schema AND lower(m.rel_name) IN (
                            v_base, v_base || 's', rtrim(v_base, 's'), v_base || '_c',
                            split_part(v_base, '_', array_length(string_to_array(v_base, '_'), 1)),
                            CASE WHEN v_base IN ('owner', 'created_by', 'last_modified_by', 'creator', 'modified_by') THEN 'user' END,
                            CASE WHEN v_base IN ('owner', 'created_by', 'last_modified_by') THEN 'users' END))
                   )) r
             ORDER BY basis LIMIT 2
        LOOP
            EXIT WHEN v_n >= 16;
            v_tocol := coalesce((SELECT k FROM unnest((SELECT entity_keys FROM rvbbit.meaning_relations WHERE relation = v_t.relation)) k WHERE k = 'id' LIMIT 1),
                                (SELECT entity_keys[1] FROM rvbbit.meaning_relations WHERE relation = v_t.relation), 'id');
            v_probe := rvbbit.meaning_probe_join(v_rel, v_c.col_name, v_t.relation, v_tocol, 200);
            v_rate := (v_probe->>'match_rate')::numeric;
            v_state := CASE WHEN v_rate IS NULL THEN 'unverified'
                            WHEN v_rate >= 0.5 THEN 'verified'
                            WHEN v_rate < 0.2 THEN 'contradicted' ELSE 'unverified' END;
            INSERT INTO rvbbit.meaning_links AS l (from_relation, from_column, to_relation, to_column, basis, state, match_rate, sampled, confidence, evidence, updated_at)
            VALUES (v_rel, v_c.col_name, v_t.relation, v_tocol, v_t.basis, v_state, v_rate, (v_probe->>'sampled')::int,
                    round(coalesce(v_rate, CASE WHEN v_t.basis = 'fk' THEN 0.8 ELSE 0.3 END), 3), coalesce(v_probe, '{}'::jsonb), now())
            ON CONFLICT (from_relation, from_column, to_relation, to_column) DO UPDATE SET
                basis = EXCLUDED.basis, state = EXCLUDED.state, match_rate = EXCLUDED.match_rate, sampled = EXCLUDED.sampled,
                confidence = EXCLUDED.confidence, evidence = EXCLUDED.evidence, updated_at = now();
            v_n := v_n + 1;
            IF v_state = 'verified' THEN
                UPDATE rvbbit.meaning_columns SET ref_target = coalesce(ref_target, v_t.relation), updated_at = now()
                 WHERE relation = v_rel AND col_name = v_c.col_name;
            END IF;
        END LOOP;
    END LOOP;
    RETURN v_n;
END $fn$;

-- ---------------------------------------------------------------------------
-- Drift: the last two fingerprints of the relation, summarized.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_relation_drift(p_schema text, p_rel text)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    v_key   text := p_schema || '.' || p_rel;
    v_a     jsonb; v_b jsonb;
    v_ta    timestamptz; v_tb timestamptz;
    v_rows_a bigint; v_rows_b bigint;
    v_pct   numeric;
    v_flags text[] := '{}';
    v_null_spikes int := 0;
    v_ndv_shifts  int := 0;
    v_sev   numeric := 0;
BEGIN
    SELECT s.fingerprint, s.captured_at INTO v_b, v_tb
      FROM rvbbit.catalog_snapshots s WHERE s.graph_id = 'db_catalog' AND s.obj_key = v_key AND s.kind = 'db_table'
     ORDER BY s.run_id DESC LIMIT 1;
    IF v_b IS NULL THEN RETURN NULL; END IF;
    SELECT s.fingerprint, s.captured_at INTO v_a, v_ta
      FROM rvbbit.catalog_snapshots s WHERE s.graph_id = 'db_catalog' AND s.obj_key = v_key AND s.kind = 'db_table'
     ORDER BY s.run_id DESC OFFSET 1 LIMIT 1;
    v_rows_b := nullif(v_b->>'n_rows','')::bigint;
    IF v_a IS NULL THEN
        RETURN jsonb_strip_nulls(jsonb_build_object('rows', v_rows_b, 'observed_at', v_tb, 'runs', 1, 'flags', '[]'::jsonb, 'severity', 0));
    END IF;
    v_rows_a := nullif(v_a->>'n_rows','')::bigint;
    IF coalesce(v_rows_a, 0) > 0 THEN
        v_pct := round((v_rows_b - v_rows_a)::numeric * 100 / v_rows_a, 2);
        IF v_pct >= 20 THEN v_flags := array_append(v_flags, 'rows_up'); v_sev := greatest(v_sev, least(1, v_pct / 100)); END IF;
        IF v_pct <= -10 THEN v_flags := array_append(v_flags, 'rows_down'); v_sev := greatest(v_sev, least(1, -v_pct / 50)); END IF;
    ELSIF coalesce(v_rows_b, 0) > 0 THEN
        v_flags := array_append(v_flags, 'rows_appeared'); v_sev := greatest(v_sev, 0.5);
    END IF;
    -- column-level: null share jumps and distinct-count swings between the same two runs
    WITH cols AS (
        SELECT s.obj_key, s.run_id, nullif(s.fingerprint->>'null_frac','')::numeric AS nf, nullif(s.fingerprint->>'ndv','')::bigint AS ndv,
               row_number() OVER (PARTITION BY s.obj_key ORDER BY s.run_id DESC) AS rn
          FROM rvbbit.catalog_snapshots s
         WHERE s.graph_id = 'db_catalog' AND s.kind = 'db_column' AND s.schema_name = p_schema AND s.rel_name = p_rel
           AND s.obj_key LIKE v_key || '.%'
    ), pairs AS (
        SELECT b.obj_key, a.nf AS nf_a, b.nf AS nf_b, a.ndv AS ndv_a, b.ndv AS ndv_b
          FROM cols b JOIN cols a ON a.obj_key = b.obj_key AND a.rn = 2 WHERE b.rn = 1
    )
    SELECT count(*) FILTER (WHERE nf_b - nf_a >= 0.1), count(*) FILTER (WHERE ndv_a > 10 AND abs(ndv_b - ndv_a) >= ndv_a * 0.5)
      INTO v_null_spikes, v_ndv_shifts FROM pairs;
    IF v_null_spikes > 0 THEN v_flags := array_append(v_flags, 'null_spike'); v_sev := greatest(v_sev, 0.4); END IF;
    IF v_ndv_shifts > 0 THEN v_flags := array_append(v_flags, 'ndv_shift'); v_sev := greatest(v_sev, 0.3); END IF;
    RETURN jsonb_strip_nulls(jsonb_build_object(
        'rows', v_rows_b, 'rows_before', v_rows_a, 'rows_pct', v_pct,
        'observed_at', v_tb, 'compared_to', v_ta, 'runs', 2,
        'null_spikes', nullif(v_null_spikes, 0), 'ndv_shifts', nullif(v_ndv_shifts, 0),
        'flags', to_jsonb(v_flags), 'severity', round(v_sev, 2)));
END $fn$;

-- ---------------------------------------------------------------------------
-- Candidates and the sweep.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_candidates(p_max int DEFAULT 100)
RETURNS TABLE (schema_name text, rel_name text, reason text, priority numeric)
LANGUAGE sql STABLE AS $fn$
    WITH latest AS (
        SELECT DISTINCT ON (s.obj_key) s.schema_name, s.rel_name, s.captured_at, nullif(s.fingerprint->>'n_rows','')::bigint AS n_rows
          FROM rvbbit.catalog_snapshots s
         WHERE s.graph_id = 'db_catalog' AND s.kind = 'db_table'
         ORDER BY s.obj_key, s.run_id DESC
    ), admitted AS (
        SELECT l.* FROM latest l
         WHERE rvbbit._business_topology_relation_allowed(l.schema_name, l.rel_name)
           AND NOT EXISTS (SELECT 1 FROM rvbbit.meaning_overrides o
                            WHERE o.relation = lower(l.schema_name || '.' || l.rel_name) AND o.col_name IS NULL
                              AND o.field = 'ignore' AND o.retired_at IS NULL AND o.value = 'true'::jsonb)
    )
    SELECT a.schema_name, a.rel_name,
           CASE WHEN m.relation IS NULL THEN 'new'
                WHEN m.status = 'error' THEN 'retry'
                WHEN a.captured_at > m.judged_at + interval '20 hours' THEN 'refreshed'
                ELSE 'columns_pending' END AS reason,
           (coalesce(u.touches_30d, 0) * 10 + log(coalesce(a.n_rows, 0) + 1))::numeric AS priority
      FROM admitted a
      LEFT JOIN rvbbit.meaning_relations m ON m.relation = lower(a.schema_name || '.' || a.rel_name)
      LEFT JOIN rvbbit.usage_snapshot u ON u.object = lower(a.schema_name || '.' || a.rel_name)
     WHERE m.relation IS NULL
        OR (m.status = 'error' AND m.attempts < 5 AND m.updated_at < now() - interval '1 hour')
        OR (m.status = 'judged' AND a.captured_at > m.judged_at + interval '20 hours')
        OR (m.status = 'judged' AND m.updated_at < now() - interval '30 minutes'
            AND EXISTS (SELECT 1 FROM rvbbit.meaning_columns c WHERE c.relation = m.relation AND c.role IS NULL))
     ORDER BY (m.relation IS NULL) DESC, priority DESC
     LIMIT p_max
$fn$;

CREATE OR REPLACE PROCEDURE rvbbit.meaning_sweep(
    p_budget_seconds int DEFAULT 120,
    p_max_relations  int DEFAULT 100,
    p_max_columns    int DEFAULT 400,
    p_source         text DEFAULT 'cron')
LANGUAGE plpgsql AS $fn$
DECLARE
    v_t0     timestamptz := clock_timestamp();
    v_run    bigint;
    v_c      record;
    v_r      jsonb;
    v_cols   jsonb;
    v_links  int;
    v_rels   int := 0;
    v_ncols  int := 0;
    v_nlinks int := 0;
    v_errors int := 0;
    v_cost   numeric := 0;
    v_rel    text;
BEGIN
    -- One sweep at a time: the cron tick and a manual call otherwise judge the
    -- same candidates twice (session lock; it survives the per-relation COMMITs).
    IF NOT pg_try_advisory_lock(hashtext('rvbbit.meaning_sweep')) THEN
        RAISE NOTICE 'meaning_sweep: another sweep is running; skipping';
        RETURN;
    END IF;
    INSERT INTO rvbbit.meaning_runs (source, budget_seconds) VALUES (p_source, p_budget_seconds) RETURNING run_id INTO v_run;
    COMMIT;
    FOR v_c IN SELECT * FROM rvbbit.meaning_candidates(p_max_relations) LOOP
        EXIT WHEN extract(epoch FROM clock_timestamp() - v_t0) > p_budget_seconds;
        v_rel := lower(v_c.schema_name || '.' || v_c.rel_name);
        BEGIN
            v_r := rvbbit.meaning_judge_relation(v_c.schema_name, v_c.rel_name);
            IF v_r->>'status' = 'judged' THEN
                v_rels := v_rels + 1;
                v_cost := v_cost + coalesce((v_r->>'cost_usd')::numeric, 0);
                v_cols := rvbbit.meaning_judge_columns(v_c.schema_name, v_c.rel_name, p_max_columns);
                v_ncols := v_ncols + coalesce((v_cols->>'columns')::int, 0);
                v_cost := v_cost + coalesce((v_cols->>'cost_usd')::numeric, 0);
                v_links := rvbbit.meaning_infer_links(v_c.schema_name, v_c.rel_name);
                v_nlinks := v_nlinks + coalesce(v_links, 0);
                UPDATE rvbbit.meaning_relations SET drift = rvbbit.meaning_relation_drift(v_c.schema_name, v_c.rel_name), updated_at = now()
                 WHERE relation = v_rel;
            ELSE
                v_errors := v_errors + 1;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            v_errors := v_errors + 1;
            INSERT INTO rvbbit.meaning_relations AS m (relation, schema_name, rel_name, status, error, attempts, updated_at)
            VALUES (v_rel, v_c.schema_name, v_c.rel_name, 'error', left(SQLERRM, 400), 1, now())
            ON CONFLICT (relation) DO UPDATE SET status = 'error', error = EXCLUDED.error, attempts = m.attempts + 1, updated_at = now();
        END;
        COMMIT;
    END LOOP;
    UPDATE rvbbit.meaning_runs SET finished_at = now(), relations_done = v_rels, columns_done = v_ncols, links_done = v_nlinks,
           errors = v_errors, cost_usd = v_cost,
           notes = jsonb_build_object('elapsed_s', round(extract(epoch FROM clock_timestamp() - v_t0)))
     WHERE run_id = v_run;
    COMMIT;
    PERFORM pg_advisory_unlock(hashtext('rvbbit.meaning_sweep'));
    RAISE NOTICE 'meaning_sweep: % relations, % columns, % links, % errors, $% in % s',
        v_rels, v_ncols, v_nlinks, v_errors, round(v_cost, 4), round(extract(epoch FROM clock_timestamp() - v_t0));
END $fn$;

-- ---------------------------------------------------------------------------
-- Cards: what the agent and the person read. Overrides win; provenance shown.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_card(p_schema text, p_rel text)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    v_rel    text := lower(p_schema || '.' || p_rel);
    m        rvbbit.meaning_relations%ROWTYPE;
    v_over   jsonb;
    v_cols   jsonb;
    v_roles  jsonb;
    v_joins  jsonb;
    v_joined jsonb;
    v_refs   jsonb;
    v_pii    jsonb;
    v_deserves text;
BEGIN
    SELECT * INTO m FROM rvbbit.meaning_relations WHERE relation = v_rel;
    IF NOT FOUND THEN RETURN NULL; END IF;
    v_over := rvbbit.meaning_overrides_for(v_rel, NULL);

    SELECT jsonb_object_agg(role, n) INTO v_roles
      FROM (SELECT coalesce(role, 'pending') AS role, count(*) AS n FROM rvbbit.meaning_columns WHERE relation = v_rel GROUP BY 1) r;
    SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object('name', c.col_name, 'role', c.role, 'ref', c.ref_target,
                        'values', CASE WHEN c.role = 'category' THEN (SELECT jsonb_agg(t->>'v') FROM jsonb_array_elements(rvbbit._jsonb_arr(c.top_values)) WITH ORDINALITY x(t, i) WHERE i <= 4) END))
                     ORDER BY CASE c.role WHEN 'identifier' THEN 0 WHEN 'time' THEN 1 WHEN 'measure' THEN 2 WHEN 'category' THEN 3 WHEN 'reference' THEN 4 ELSE 5 END, c.col_name)
      INTO v_cols
      FROM (SELECT * FROM (
                SELECT c.*, row_number() OVER (PARTITION BY c.role ORDER BY
                           CASE WHEN c.col_name ~* '(^|_)(fivetran|dlt|system_modstamp|last_viewed|last_referenced)' THEN 1 ELSE 0 END,
                           CASE WHEN c.col_name ~* '(created|occurred|status|stage|type|source|amount|name|email)' THEN 0 ELSE 1 END,
                           c.null_frac NULLS LAST, c.col_name) AS rn
                  FROM rvbbit.meaning_columns c
                 WHERE c.relation = v_rel AND c.role IN ('identifier', 'time', 'measure', 'category', 'reference')) r
             WHERE r.rn <= 4) c;
    SELECT jsonb_agg(c.col_name ORDER BY c.pii DESC) INTO v_pii
      FROM (SELECT * FROM rvbbit.meaning_columns c WHERE c.relation = v_rel AND c.pii >= 0.6 ORDER BY c.pii DESC LIMIT 8) c;

    SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object('via', l.from_column, 'to', l.to_relation, 'on', l.to_column, 'state', l.state,
                        'match', l.match_rate, 'basis', l.basis)) ORDER BY l.state = 'verified' DESC, l.match_rate DESC NULLS LAST)
      INTO v_joins
      FROM (SELECT * FROM rvbbit.meaning_links l WHERE l.from_relation = v_rel AND l.state <> 'contradicted' ORDER BY l.state = 'verified' DESC, l.match_rate DESC NULLS LAST LIMIT 12) l;
    SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object('from', l.from_relation, 'via', l.from_column, 'state', l.state, 'match', l.match_rate))
                     ORDER BY l.state = 'verified' DESC, l.match_rate DESC NULLS LAST)
      INTO v_joined
      FROM (SELECT * FROM rvbbit.meaning_links l WHERE l.to_relation = v_rel AND l.state = 'verified' ORDER BY l.match_rate DESC NULLS LAST LIMIT 12) l;

    IF to_regclass('rvbbit.relation_refs') IS NOT NULL THEN
        SELECT jsonb_strip_nulls(jsonb_build_object(
                 'metrics', (SELECT jsonb_agg(ref_name ORDER BY ref_name) FROM (SELECT ref_name FROM rvbbit.relation_refs WHERE relation = v_rel AND ref_kind = 'metric' LIMIT 8) x),
                 'cubes', (SELECT jsonb_agg(ref_name ORDER BY ref_name) FROM (SELECT ref_name FROM rvbbit.relation_refs WHERE relation = v_rel AND ref_kind = 'cube' LIMIT 8) x),
                 'dashboards', (SELECT jsonb_agg(ref_name) FROM (SELECT ref_name FROM rvbbit.relation_refs WHERE relation = v_rel AND ref_kind = 'dashboard' LIMIT 6) x),
                 'domains', (SELECT jsonb_agg(jsonb_build_object('key', ref_name, 'name', ref_meta->>'name', 'status', ref_meta->>'status'))
                               FROM (SELECT ref_name, ref_meta FROM rvbbit.relation_refs WHERE relation = v_rel AND ref_kind = 'domain' LIMIT 4) x)))
          INTO v_refs;
    END IF;

    v_deserves := CASE
        WHEN v_refs ? 'metrics' OR v_refs ? 'cubes' THEN 'already measured'
        WHEN coalesce(m.plumbing, 0) >= 0.6 OR m.kind IN ('system_plumbing', 'staging_or_raw') THEN 'no: plumbing or raw'
        WHEN m.kind = 'aggregate_or_cube' THEN 'is itself a rollup'
        WHEN coalesce(m.metric_worthy, 0) >= 0.6 AND m.time_column IS NOT NULL THEN 'yes: activity over time'
        WHEN coalesce(m.metric_worthy, 0) >= 0.6 THEN 'yes: counts and breakdowns'
        WHEN coalesce(m.metric_worthy, 0) >= 0.4 THEN 'maybe'
        ELSE 'unlikely' END;

    RETURN jsonb_strip_nulls(jsonb_build_object(
        'relation', v_rel,
        'name', coalesce(v_over->>'business_name', m.business_name, rvbbit.meaning_display_name(m.rel_name)),
        'what', coalesce(v_over->>'what', m.what),
        'kind', coalesce(v_over->>'kind', m.kind),
        'source', jsonb_strip_nulls(jsonb_build_object('system', m.source_system, 'role', m.source_role)),
        'rows', m.n_rows, 'columns', m.n_columns, 'custom_fields', nullif(m.custom_fields, 0),
        'keys', CASE WHEN cardinality(m.entity_keys) > 0 THEN to_jsonb(m.entity_keys) END,
        'time', jsonb_strip_nulls(jsonb_build_object('column', m.time_column, 'grain', m.time_grain)),
        'column_roles', v_roles,
        'notable_columns', v_cols,
        'personal_data', v_pii,
        'relates', jsonb_strip_nulls(jsonb_build_object('joins_to', v_joins, 'joined_by', v_joined)),
        'measured_by', v_refs,
        'deserves_metrics', v_deserves,
        'scores', jsonb_strip_nulls(jsonb_build_object('metric_worthy', m.metric_worthy, 'plumbing', m.plumbing, 'pii', m.has_pii, 'customized', m.customized)),
        'domain', coalesce(v_over->>'domain_key', m.domain_key),
        'drift', m.drift,
        'usage', m.usage,
        'confidence', CASE WHEN v_over <> '{}'::jsonb THEN 1.0 ELSE m.confidence END,
        'provenance', jsonb_strip_nulls(jsonb_build_object(
            'judged_by', m.judge_model, 'judged_at', m.judged_at, 'status', m.status,
            'stated_by_people', CASE WHEN v_over <> '{}'::jsonb THEN v_over END,
            'note', 'provisional: described from structure and a small judge; not affirmed canon'))));
END $fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_column_card(p_schema text, p_rel text, p_col text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'column', c.col_name, 'relation', c.relation, 'type', c.data_type,
        'role', coalesce(o->>'role', c.role), 'role_p', c.role_p,
        'refers_to', coalesce(o->>'ref_target', c.ref_target),
        'joins', (SELECT jsonb_agg(jsonb_build_object('to', l.to_relation, 'on', l.to_column, 'state', l.state, 'match', l.match_rate))
                    FROM rvbbit.meaning_links l WHERE l.from_relation = c.relation AND l.from_column = c.col_name AND l.state <> 'contradicted'),
        'personal_data', coalesce((o->>'pii')::numeric, c.pii),
        'distinct', c.ndv, 'null_pct', round(c.null_frac * 100),
        'top_values', c.top_values,
        'basis', CASE WHEN o <> '{}'::jsonb THEN 'override' ELSE c.basis END,
        'judged_at', c.judged_at))
      FROM rvbbit.meaning_columns c, LATERAL (SELECT rvbbit.meaning_overrides_for(c.relation, c.col_name) AS o) x
     WHERE c.relation = lower(p_schema || '.' || p_rel) AND c.col_name = p_col
$fn$;

CREATE OR REPLACE VIEW rvbbit.meaning_overview AS
SELECT m.relation, m.schema_name, m.rel_name, m.source_system, m.source_role,
       coalesce(o.business_name, m.business_name, rvbbit.meaning_display_name(m.rel_name)) AS name,
       coalesce(o.kind, m.kind) AS kind, m.kind_p, m.what, m.n_rows, m.n_columns, m.custom_fields,
       m.time_column, m.time_grain, m.entity_keys,
       m.metric_worthy, m.plumbing, m.has_pii, m.customized, m.confidence,
       coalesce(o.domain_key, m.domain_key) AS domain_key,
       (m.drift->>'severity')::numeric AS drift_severity, m.drift->'flags' AS drift_flags,
       (m.usage->>'touches_30d')::int AS touches_30d,
       (SELECT count(*) FROM rvbbit.meaning_columns c WHERE c.relation = m.relation) AS columns_judged,
       (SELECT count(*) FROM rvbbit.meaning_links l WHERE l.from_relation = m.relation AND l.state = 'verified') AS verified_joins,
       (SELECT count(*) FROM rvbbit.relation_refs r WHERE r.relation = m.relation AND r.ref_kind IN ('metric','cube')) AS measured_by,
       (o.business_name IS NOT NULL OR o.kind IS NOT NULL OR o.domain_key IS NOT NULL) AS overridden,
       m.status, m.judged_at, m.cost_usd
  FROM rvbbit.meaning_relations m
  LEFT JOIN LATERAL (
        SELECT max(value #>> '{}') FILTER (WHERE field = 'business_name') AS business_name,
               max(value #>> '{}') FILTER (WHERE field = 'kind') AS kind,
               max(value #>> '{}') FILTER (WHERE field = 'domain_key') AS domain_key
          FROM rvbbit.meaning_overrides mo WHERE mo.relation = m.relation AND mo.col_name IS NULL AND mo.retired_at IS NULL) o ON true;

CREATE OR REPLACE FUNCTION rvbbit.meaning_status() RETURNS jsonb
LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_build_object(
        'relations', (SELECT count(*) FROM rvbbit.meaning_relations WHERE status = 'judged'),
        'relations_error', (SELECT count(*) FROM rvbbit.meaning_relations WHERE status = 'error'),
        'admitted', (SELECT count(*) FROM (SELECT DISTINCT ON (s.obj_key) s.schema_name, s.rel_name FROM rvbbit.catalog_snapshots s
                                            WHERE s.graph_id = 'db_catalog' AND s.kind = 'db_table' ORDER BY s.obj_key, s.run_id DESC) t
                       WHERE rvbbit._business_topology_relation_allowed(t.schema_name, t.rel_name)),
        'pending', (SELECT count(*) FROM rvbbit.meaning_candidates(100000)),
        'columns', (SELECT count(*) FROM rvbbit.meaning_columns),
        'columns_by_basis', (SELECT jsonb_object_agg(basis, n) FROM (SELECT coalesce(basis,'?') AS basis, count(*) AS n FROM rvbbit.meaning_columns GROUP BY 1) x),
        'links', (SELECT jsonb_object_agg(state, n) FROM (SELECT state, count(*) AS n FROM rvbbit.meaning_links GROUP BY 1) x),
        'overrides', (SELECT count(*) FROM rvbbit.meaning_overrides WHERE retired_at IS NULL),
        'cost_usd', (SELECT round(coalesce(sum(cost_usd), 0), 4) FROM rvbbit.meaning_runs),
        'last_run', (SELECT jsonb_build_object('run_id', run_id, 'started_at', started_at, 'finished_at', finished_at, 'relations', relations_done,
                                               'columns', columns_done, 'links', links_done, 'errors', errors, 'cost_usd', cost_usd, 'notes', notes)
                       FROM rvbbit.meaning_runs ORDER BY run_id DESC LIMIT 1),
        'kinds', (SELECT jsonb_object_agg(kind, n) FROM (SELECT coalesce(kind,'?') AS kind, count(*) AS n FROM rvbbit.meaning_relations WHERE status = 'judged' GROUP BY 1) x))
$fn$;

-- Overrides: one call for people and for Callie on their say-so.
CREATE OR REPLACE FUNCTION rvbbit.meaning_state(p_relation text, p_field text, p_value jsonb, p_reason text DEFAULT NULL, p_col text DEFAULT NULL, p_actor text DEFAULT NULL)
RETURNS bigint LANGUAGE plpgsql AS $fn$
DECLARE v_id bigint;
BEGIN
    IF p_field NOT IN ('kind', 'business_name', 'what', 'domain_key', 'ignore', 'role', 'ref_target', 'pii') THEN
        RAISE EXCEPTION 'meaning_state: unknown field %', p_field;
    END IF;
    UPDATE rvbbit.meaning_overrides SET retired_at = now()
     WHERE relation = lower(p_relation) AND col_name IS NOT DISTINCT FROM p_col AND field = p_field AND retired_at IS NULL;
    INSERT INTO rvbbit.meaning_overrides (relation, col_name, field, value, reason, actor)
    VALUES (lower(p_relation), p_col, p_field, p_value, p_reason, coalesce(p_actor, current_user)) RETURNING override_id INTO v_id;
    -- the card reflects it immediately; the next judge sees it as a stated fact
    IF p_col IS NULL THEN
        UPDATE rvbbit.meaning_relations SET
            business_name = CASE WHEN p_field = 'business_name' THEN p_value #>> '{}' ELSE business_name END,
            domain_key = CASE WHEN p_field = 'domain_key' THEN p_value #>> '{}' ELSE domain_key END,
            kind = CASE WHEN p_field = 'kind' THEN p_value #>> '{}' ELSE kind END,
            what = CASE WHEN p_field = 'what' THEN p_value #>> '{}' ELSE what END,
            updated_at = now()
         WHERE relation = lower(p_relation);
    ELSE
        UPDATE rvbbit.meaning_columns SET
            role = CASE WHEN p_field = 'role' THEN p_value #>> '{}' ELSE role END,
            ref_target = CASE WHEN p_field = 'ref_target' THEN p_value #>> '{}' ELSE ref_target END,
            pii = CASE WHEN p_field = 'pii' THEN (p_value #>> '{}')::numeric ELSE pii END,
            basis = 'override', updated_at = now()
         WHERE relation = lower(p_relation) AND col_name = p_col;
    END IF;
    RETURN v_id;
END $fn$;

-- Schedule: every ten minutes, two minutes of budget (fresh installs seed this too).
CREATE OR REPLACE FUNCTION rvbbit.schedule_meaning_sweep() RETURNS text
LANGUAGE plpgsql AS $fn$
BEGIN
    IF to_regprocedure('rvbbit._schedule_in_cron_home(text,text,text)') IS NULL THEN
        RETURN 'cron helper missing; schedule rvbbit_meaning_sweep by hand';
    END IF;
    BEGIN
        RETURN rvbbit._schedule_in_cron_home('rvbbit_meaning_sweep', '*/10 * * * *', 'CALL rvbbit.meaning_sweep(120)');
    EXCEPTION WHEN OTHERS THEN
        -- pg_cron lives in the cron database on appliance installs; the seed
        -- scripts schedule this job there. Say so instead of failing the caller.
        RETURN 'not scheduled from this database (' || SQLERRM || '); from the cron database run: '
               || 'SELECT cron.schedule_in_database(''rvbbit_meaning_sweep'',''*/10 * * * *'',''CALL rvbbit.meaning_sweep(120);'',''' || current_database() || ''')';
    END;
END $fn$;

-- Retire one piece of dead weight now: the first-generation topology value
-- fingerprints hold zero rows and 49 MB of dead heap on the pilot.
DO $do$
BEGIN
    IF to_regclass('rvbbit.business_topology_value_fingerprints') IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM rvbbit.business_topology_value_fingerprints LIMIT 1) THEN
        TRUNCATE rvbbit.business_topology_value_fingerprints;
    END IF;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'meaning: value_fingerprints truncate skipped (%)', SQLERRM;
END $do$;

COMMENT ON TABLE rvbbit.meaning_relations IS 'Meaning card per admitted relation: kind, keys, time, worthiness, PII, drift (docs/MEANING_PLAN.md). Provisional; overrides win.';
COMMENT ON TABLE rvbbit.meaning_columns IS 'Meaning card per column: role (rule or Jev), join target, PII.';
COMMENT ON TABLE rvbbit.meaning_links IS 'Join candidates (fk, naming, recognition) verified by a bounded value probe.';
COMMENT ON TABLE rvbbit.meaning_overrides IS 'People state facts about relations and columns; overrides win in cards and are stipulated to judges.';
COMMENT ON FUNCTION rvbbit.meaning_card(text, text) IS 'The served card for a relation: what, kind, keys, time, roles, joins, measured_by, deserves_metrics, drift, usage, confidence, provenance.';
