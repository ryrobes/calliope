-- 0440_meaning_narrative — Meaning's own narrative (docs/MEANING_PLAN.md Tier 1 + Tier 2).
--
-- Two strong-model calls, made by services/calliope/meaning_jobs.py:
--   recognition — once per business/derived source: what product this is, what
--                 each table is called by people, its object role, package, one
--                 line, expected joins (verified here by the value probe before
--                 belief), standard questions.
--   domain      — once per unit (a verified join cluster inside one source,
--                 hubs like `user` excluded): name, what it is about, questions,
--                 candidate metrics. Jev reviews every domain (makes sense?
--                 kind?) before it is marked usable.
-- Everything lands beside the old excavator's output, never over it, so the two
-- can be read side by side in the Meaning window.

ALTER TABLE rvbbit.meaning_relations
    ADD COLUMN IF NOT EXISTS narrative     text,
    ADD COLUMN IF NOT EXISTS package       text,
    ADD COLUMN IF NOT EXISTS object_role   text,
    ADD COLUMN IF NOT EXISTS recognized_at timestamptz;

CREATE TABLE IF NOT EXISTS rvbbit.meaning_recognitions (
    schema_name     text PRIMARY KEY,
    source_system   text,
    fingerprint     text,                      -- schema fingerprint the output was made for
    packet          jsonb,
    output          jsonb,
    verification    jsonb,
    model           text,
    tokens_in       int,
    tokens_out      int,
    cost_usd        numeric(12,6) NOT NULL DEFAULT 0,
    status          text NOT NULL DEFAULT 'pending',   -- pending | done | error
    error           text,
    attempts        int NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rvbbit.meaning_domains (
    domain_key          text PRIMARY KEY,      -- meaning:<schema>:<hash of members>
    schema_name         text NOT NULL,
    source_system       text,
    name                text,
    about               text,
    questions           jsonb NOT NULL DEFAULT '[]'::jsonb,
    candidate_metrics   jsonb NOT NULL DEFAULT '[]'::jsonb,
    members             text[] NOT NULL DEFAULT '{}',
    member_fingerprint  text,
    packet              jsonb,
    output              jsonb,
    model               text,
    tokens_in           int,
    tokens_out          int,
    cost_usd            numeric(12,6) NOT NULL DEFAULT 0,
    review              jsonb,                 -- Jev: makes_sense, kind
    usable              boolean NOT NULL DEFAULT false,
    status              text NOT NULL DEFAULT 'pending',   -- pending | done | error
    error               text,
    attempts            int NOT NULL DEFAULT 0,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS meaning_domains_schema_idx ON rvbbit.meaning_domains (schema_name, usable);

-- ---------------------------------------------------------------------------
-- Recognition: schema fingerprint and packet.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_recognition_fingerprint(p_schema text) RETURNS text
LANGUAGE sql STABLE AS $fn$
    SELECT md5(coalesce(string_agg(m.rel_name || ':' || coalesce(m.n_columns::text, '?'), ',' ORDER BY m.rel_name), ''))
      FROM rvbbit.meaning_relations m
     WHERE m.schema_name = p_schema AND m.status = 'judged'
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_recognition_packet(p_schema text, p_max_relations int DEFAULT 220)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    v_source record;
    v_rels   jsonb;
    v_total  int;
BEGIN
    SELECT bs.source_system, bs.role, bs.adapter INTO v_source FROM rvbbit.business_sources bs WHERE bs.schema_name = p_schema;
    SELECT count(*) INTO v_total FROM rvbbit.meaning_relations m WHERE m.schema_name = p_schema AND m.status = 'judged';
    SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
               'table', m.rel_name,
               'rows', m.n_rows,
               'columns', m.n_columns,
               'custom_fields', nullif(m.custom_fields, 0),
               'kind', m.kind,
               'keys', CASE WHEN cardinality(m.entity_keys) > 0 THEN to_jsonb(m.entity_keys[1:3]) END,
               'time', m.time_column,
               'columns_sample', (SELECT jsonb_agg(c->>'name') FROM (SELECT c FROM jsonb_array_elements(rvbbit._jsonb_arr(m.profile->'column_names')) c LIMIT 22) x),
               'categories', (SELECT jsonb_agg(jsonb_build_object(c->>'name', c->'top')) FROM (SELECT c FROM jsonb_array_elements(rvbbit._jsonb_arr(m.profile->'categorical')) c LIMIT 3) x),
               'measures', (SELECT jsonb_agg(v) FROM (SELECT v FROM jsonb_array_elements_text(rvbbit._jsonb_arr(m.profile->'measures')) v LIMIT 6) x),
               'references', (SELECT jsonb_agg(c.col_name || '→' || c.ref_target) FROM (SELECT col_name, ref_target FROM rvbbit.meaning_columns c
                                  WHERE c.relation = m.relation AND c.role = 'reference' AND c.ref_target IS NOT NULL ORDER BY c.null_frac NULLS LAST LIMIT 6) c),
               'touches_30d', (m.usage->>'touches_30d')::int))
             ORDER BY coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST), '[]'::jsonb)
      INTO v_rels
      FROM (SELECT * FROM rvbbit.meaning_relations m WHERE m.schema_name = p_schema AND m.status = 'judged'
             ORDER BY coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST LIMIT p_max_relations) m;
    RETURN jsonb_strip_nulls(jsonb_build_object(
        'schema', p_schema,
        'source_system', coalesce(v_source.source_system, p_schema),
        'role', v_source.role,
        'adapter', v_source.adapter,
        'relations_total', v_total,
        'relations_shown', jsonb_array_length(v_rels),
        'relations', v_rels));
END $fn$;

-- Apply a recognition output: names, roles, one-liners, expected joins (probed).
CREATE OR REPLACE FUNCTION rvbbit.meaning_apply_recognition(p_schema text, p_output jsonb)
RETURNS jsonb LANGUAGE plpgsql AS $fn$
DECLARE
    v_obj     jsonb;
    v_rel     text;
    v_applied int := 0;
    v_join    jsonb;
    v_from    text; v_to text; v_col text; v_tocol text;
    v_probe   jsonb; v_rate numeric; v_state text;
    v_joins   jsonb := '{"verified":0,"unverified":0,"contradicted":0,"skipped":0}'::jsonb;
    v_stated  jsonb;
BEGIN
    FOR v_obj IN SELECT * FROM jsonb_array_elements(rvbbit._jsonb_arr(p_output->'objects')) LOOP
        v_rel := lower(p_schema || '.' || regexp_replace(coalesce(v_obj->>'table', v_obj->>'relation', ''), '^' || p_schema || '\.', ''));
        CONTINUE WHEN v_rel = lower(p_schema || '.');
        v_stated := rvbbit.meaning_overrides_for(v_rel, NULL);
        UPDATE rvbbit.meaning_relations m SET
            business_name = CASE WHEN v_stated ? 'business_name' THEN m.business_name ELSE coalesce(nullif(btrim(v_obj->>'business_name'), ''), m.business_name) END,
            narrative     = coalesce(nullif(btrim(v_obj->>'one_line'), ''), m.narrative),
            package       = nullif(btrim(v_obj->>'package'), ''),
            object_role   = nullif(btrim(v_obj->>'role'), ''),
            recognized_at = now(), updated_at = now()
         WHERE m.relation = v_rel;
        IF FOUND THEN v_applied := v_applied + 1; END IF;
    END LOOP;

    FOR v_join IN SELECT * FROM jsonb_array_elements(rvbbit._jsonb_arr(p_output->'expected_joins')) LOOP
        v_from := lower(p_schema || '.' || regexp_replace(coalesce(v_join->>'from', ''), '^' || p_schema || '\.', ''));
        v_to   := lower(p_schema || '.' || regexp_replace(coalesce(v_join->>'to', ''), '^' || p_schema || '\.', ''));
        v_col  := lower(coalesce(v_join->>'column', v_join->>'from_column', ''));
        v_tocol := lower(coalesce(nullif(v_join->>'to_column', ''), 'id'));
        IF v_col = '' OR NOT EXISTS (SELECT 1 FROM rvbbit.meaning_relations WHERE relation = v_from)
           OR NOT EXISTS (SELECT 1 FROM rvbbit.meaning_relations WHERE relation = v_to)
           OR NOT EXISTS (SELECT 1 FROM rvbbit.meaning_columns WHERE relation = v_from AND col_name = v_col) THEN
            v_joins := jsonb_set(v_joins, '{skipped}', to_jsonb((v_joins->>'skipped')::int + 1));
            CONTINUE;
        END IF;
        -- the model is allowed to be confident, not believed: probe before it counts
        v_probe := rvbbit.meaning_probe_join(v_from, v_col, v_to, v_tocol, 200);
        v_rate := (v_probe->>'match_rate')::numeric;
        v_state := CASE WHEN v_rate IS NULL THEN 'unverified' WHEN v_rate >= 0.5 THEN 'verified'
                        WHEN v_rate < 0.2 THEN 'contradicted' ELSE 'unverified' END;
        INSERT INTO rvbbit.meaning_links AS l (from_relation, from_column, to_relation, to_column, basis, state, match_rate, sampled, confidence, evidence, updated_at)
        VALUES (v_from, v_col, v_to, v_tocol, 'recognition', v_state, v_rate, (v_probe->>'sampled')::int,
                round(coalesce(v_rate, 0.4), 3), coalesce(v_probe, '{}'::jsonb) || jsonb_build_object('claimed_by', 'recognition'), now())
        ON CONFLICT (from_relation, from_column, to_relation, to_column) DO UPDATE SET
            state = EXCLUDED.state, match_rate = EXCLUDED.match_rate, sampled = EXCLUDED.sampled,
            confidence = greatest(l.confidence, EXCLUDED.confidence),
            evidence = l.evidence || jsonb_build_object('also_claimed_by', 'recognition'), updated_at = now();
        v_joins := jsonb_set(v_joins, ARRAY[v_state], to_jsonb((v_joins->>v_state)::int + 1));
        IF v_state = 'verified' THEN
            UPDATE rvbbit.meaning_columns SET ref_target = coalesce(ref_target, v_to), role = coalesce(role, 'reference'), updated_at = now()
             WHERE relation = v_from AND col_name = v_col;
        END IF;
    END LOOP;
    RETURN jsonb_build_object('objects_applied', v_applied, 'joins', v_joins);
END $fn$;

-- ---------------------------------------------------------------------------
-- Units: verified join clusters inside one source, hubs excluded.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_units(p_schema text, p_max_members int DEFAULT 12)
RETURNS TABLE (unit_key text, members text[], member_fingerprint text)
LANGUAGE sql STABLE AS $fn$
    -- Connected components over verified joins inside one source. Hub targets
    -- (user, record type: in-degree above max(8, 20 % of the source) and
    -- plumbing-flagged relations do not carry edges, so Salesforce does not
    -- collapse into one unit through owner_id. Pure SQL: a STABLE function
    -- cannot create temp tables, so the closure is a recursive CTE.
    WITH RECURSIVE nodes AS (
        SELECT m.relation FROM rvbbit.meaning_relations m WHERE m.schema_name = p_schema AND m.status = 'judged'
    ), hub AS (
        SELECT greatest(8, (count(*) * 0.2)::int) AS limit_in FROM nodes
    ), raw_edges AS (
        SELECT l.from_relation AS a, l.to_relation AS b
          FROM rvbbit.meaning_links l
          JOIN rvbbit.meaning_relations x ON x.relation = l.from_relation
          JOIN rvbbit.meaning_relations y ON y.relation = l.to_relation
         WHERE l.state = 'verified' AND x.schema_name = p_schema AND y.schema_name = p_schema
           AND coalesce(x.plumbing, 0) < 0.6 AND coalesce(y.plumbing, 0) < 0.6
           AND coalesce(x.kind, '') <> 'system_plumbing' AND coalesce(y.kind, '') <> 'system_plumbing'
           AND (SELECT count(DISTINCT z.from_relation) FROM rvbbit.meaning_links z WHERE z.to_relation = l.to_relation AND z.state = 'verified')
               <= (SELECT limit_in FROM hub)
    ), edges AS (
        SELECT a, b FROM raw_edges UNION SELECT b, a FROM raw_edges
    ), reach AS (
        SELECT n.relation AS start, n.relation AS node FROM nodes n
        UNION
        SELECT r.start, e.b FROM reach r JOIN edges e ON e.a = r.node
    ), comp AS (
        SELECT r.start AS relation, min(r.node) AS label FROM reach r GROUP BY r.start
    ), grouped AS (
        SELECT c.label,
               array_agg(c.relation ORDER BY coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST) AS rels,
               bool_or(coalesce(m.metric_worthy, 0) >= 0.6) AS worthy, max(m.n_rows) AS max_rows, count(*) AS n
          FROM comp c JOIN rvbbit.meaning_relations m ON m.relation = c.relation
         GROUP BY c.label
    )
    SELECT 'meaning:' || p_schema || ':' || left(md5(array_to_string(g.rels[1:p_max_members], ',')), 12),
           g.rels[1:p_max_members],
           md5(array_to_string(g.rels[1:p_max_members], ','))
      FROM grouped g
     WHERE g.n >= 2 OR (g.worthy AND coalesce(g.max_rows, 0) >= 1000)
     ORDER BY g.n DESC, g.max_rows DESC NULLS LAST
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_unit_packet(p_schema text, p_members text[])
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'schema', p_schema,
        'source_system', (SELECT coalesce(bs.source_system, p_schema) FROM rvbbit.business_sources bs WHERE bs.schema_name = p_schema),
        'recognized_as', (SELECT r.output->'product' FROM rvbbit.meaning_recognitions r WHERE r.schema_name = p_schema AND r.status = 'done'),
        'tables', (SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                        'table', m.rel_name, 'name', m.business_name, 'one_line', m.narrative, 'role', m.object_role, 'package', m.package,
                        'kind', m.kind, 'rows', m.n_rows, 'keys', CASE WHEN cardinality(m.entity_keys) > 0 THEN to_jsonb(m.entity_keys[1:3]) END,
                        'time', m.time_column,
                        'categories', (SELECT jsonb_agg(jsonb_build_object(c->>'name', c->'top')) FROM (SELECT c FROM jsonb_array_elements(rvbbit._jsonb_arr(m.profile->'categorical')) c LIMIT 4) x),
                        'measures', (SELECT jsonb_agg(v) FROM (SELECT v FROM jsonb_array_elements_text(rvbbit._jsonb_arr(m.profile->'measures')) v LIMIT 8) x),
                        'joins', (SELECT jsonb_agg(l.from_column || '→' || split_part(l.to_relation, '.', 2)) FROM (SELECT from_column, to_relation FROM rvbbit.meaning_links l
                                     WHERE l.from_relation = m.relation AND l.state = 'verified' AND l.to_relation = ANY(p_members) LIMIT 8) l)))
                     ORDER BY array_position(p_members, m.relation))
                     FROM rvbbit.meaning_relations m WHERE m.relation = ANY(p_members))))
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_apply_domain(
    p_domain_key text, p_schema text, p_members text[], p_fingerprint text,
    p_output jsonb, p_packet jsonb, p_model text, p_tokens_in int, p_tokens_out int, p_cost numeric)
RETURNS void LANGUAGE plpgsql AS $fn$
DECLARE v_rel text;
BEGIN
    INSERT INTO rvbbit.meaning_domains AS d (domain_key, schema_name, source_system, name, about, questions, candidate_metrics, members, member_fingerprint,
                                             packet, output, model, tokens_in, tokens_out, cost_usd, status, error, attempts, updated_at)
    VALUES (p_domain_key, p_schema, (SELECT coalesce(bs.source_system, p_schema) FROM rvbbit.business_sources bs WHERE bs.schema_name = p_schema),
            left(btrim(p_output->>'name'), 120), left(btrim(p_output->>'about'), 1200),
            rvbbit._jsonb_arr(p_output->'questions'), rvbbit._jsonb_arr(p_output->'candidate_metrics'),
            p_members, p_fingerprint, p_packet, p_output, p_model, p_tokens_in, p_tokens_out, coalesce(p_cost, 0), 'done', NULL, 1, now())
    ON CONFLICT (domain_key) DO UPDATE SET
        name = EXCLUDED.name, about = EXCLUDED.about, questions = EXCLUDED.questions, candidate_metrics = EXCLUDED.candidate_metrics,
        members = EXCLUDED.members, member_fingerprint = EXCLUDED.member_fingerprint, packet = EXCLUDED.packet, output = EXCLUDED.output,
        model = EXCLUDED.model, tokens_in = EXCLUDED.tokens_in, tokens_out = EXCLUDED.tokens_out, cost_usd = d.cost_usd + EXCLUDED.cost_usd,
        status = 'done', error = NULL, attempts = d.attempts + 1, review = NULL, usable = false, updated_at = now();
    FOREACH v_rel IN ARRAY p_members LOOP
        UPDATE rvbbit.meaning_relations m SET domain_key = p_domain_key, updated_at = now()
         WHERE m.relation = v_rel AND NOT (rvbbit.meaning_overrides_for(v_rel, NULL) ? 'domain_key');
    END LOOP;
END $fn$;

-- Jev review of a domain narrative: makes sense? what kind? Sets usable.
CREATE OR REPLACE FUNCTION rvbbit.meaning_review_domain(p_domain_key text)
RETURNS jsonb LANGUAGE plpgsql AS $fn$
DECLARE
    d       rvbbit.meaning_domains%ROWTYPE;
    v_state jsonb;
    v_ans   jsonb;
    v_sense numeric; v_kind text;
BEGIN
    SELECT * INTO d FROM rvbbit.meaning_domains WHERE domain_key = p_domain_key;
    IF NOT FOUND OR d.status <> 'done' THEN RETURN NULL; END IF;
    v_state := jsonb_build_object(
        'name', d.name, 'about', d.about, 'questions', d.questions,
        'tables', (SELECT jsonb_agg(jsonb_build_object('table', m.rel_name, 'kind', m.kind, 'rows', m.n_rows, 'name', m.business_name))
                     FROM rvbbit.meaning_relations m WHERE m.relation = ANY(d.members)),
        'candidate_metrics', (SELECT jsonb_agg(c->'name') FROM (SELECT c FROM jsonb_array_elements(d.candidate_metrics) c LIMIT 6) x));
    v_ans := rvbbit.clover_decide(v_state, $j${
      "makes_sense": {"type": "noul", "instructions": "This is a coherent business subject an analyst at the company would recognize by this name, and the description is grounded in the listed tables rather than generic."},
      "kind": {"type": "choice", "instructions": "What is this cluster of tables, really?",
        "criteria": {"domain": "a real business subject area (sales pipeline, enrollment, paid media delivery, support cases)",
                     "configuration": "setup, settings, picklists or metadata about the system itself",
                     "plumbing": "pipeline, sync, log or platform bookkeeping",
                     "fragment": "too small or too mixed to be a subject on its own"}}}$j$::jsonb);
    IF v_ans IS NULL OR v_ans->'answers' IS NULL THEN
        UPDATE rvbbit.meaning_domains SET review = jsonb_build_object('error', 'judge returned no answers', 'at', now()), updated_at = now() WHERE domain_key = p_domain_key;
        RETURN NULL;
    END IF;
    v_sense := (v_ans #>> '{answers,makes_sense,noul}')::numeric;
    v_kind := v_ans #>> '{answers,kind,choice}';
    UPDATE rvbbit.meaning_domains SET
        review = jsonb_build_object('makes_sense', v_sense, 'kind', v_kind, 'kind_probs', v_ans #> '{answers,kind,probabilities}',
                                    'model', v_ans->>'model', 'at', now()),
        usable = (coalesce(v_sense, 0) >= 0.6 AND v_kind = 'domain'),
        updated_at = now()
     WHERE domain_key = p_domain_key;
    RETURN jsonb_build_object('makes_sense', v_sense, 'kind', v_kind, 'usable', (coalesce(v_sense, 0) >= 0.6 AND v_kind = 'domain'));
END $fn$;

-- ---------------------------------------------------------------------------
-- Queue: what the narrative worker should do next.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_narrative_queue(p_max int DEFAULT 20)
RETURNS TABLE (kind text, key text, schema_name text, members text[], member_fingerprint text, reason text, priority numeric)
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    WITH pend AS MATERIALIZED (
        -- one candidates pass for every source; MATERIALIZED, or the planner
        -- inlines the single-use CTE and re-runs it per source (12 × 0.5 s)
        SELECT c.schema_name, count(*) AS n FROM rvbbit.meaning_candidates(100000) c GROUP BY c.schema_name
    ), sources AS MATERIALIZED (
        SELECT bs.schema_name, bs.source_system,
               (SELECT count(*) FROM rvbbit.meaning_relations m WHERE m.schema_name = bs.schema_name AND m.status = 'judged') AS judged,
               coalesce((SELECT n FROM pend WHERE pend.schema_name = bs.schema_name), 0) AS pending,
               (SELECT sum(coalesce((m.usage->>'touches_30d')::int, 0)) FROM rvbbit.meaning_relations m WHERE m.schema_name = bs.schema_name) AS touches,
               EXISTS (SELECT 1 FROM rvbbit.meaning_recognitions r WHERE r.schema_name = bs.schema_name AND r.status = 'done') AS recognized
          FROM rvbbit.business_sources bs
         WHERE bs.role IN ('business', 'derived')
    ), recog AS (
        SELECT 'recognition'::text AS kind, s.schema_name AS key, s.schema_name, NULL::text[] AS members, NULL::text AS member_fingerprint,
               CASE WHEN r.schema_name IS NULL THEN 'new'
                    WHEN r.status = 'error' THEN 'retry'
                    ELSE 'schema_changed' END AS reason,
               (coalesce(s.touches, 0) + s.judged)::numeric AS priority
          FROM sources s
          LEFT JOIN rvbbit.meaning_recognitions r ON r.schema_name = s.schema_name
         WHERE s.judged > 0 AND s.pending = 0          -- the cards are complete for this source
           AND (r.schema_name IS NULL
                OR (r.status = 'error' AND r.attempts < 4 AND r.updated_at < now() - interval '1 hour')
                OR (r.status = 'done' AND r.fingerprint IS DISTINCT FROM rvbbit.meaning_recognition_fingerprint(s.schema_name)
                    AND r.updated_at < now() - interval '6 hours'))
    ), units AS (
        SELECT 'domain'::text AS kind, u.unit_key AS key, s.schema_name, u.members, u.member_fingerprint,
               CASE WHEN d.domain_key IS NULL THEN 'new' WHEN d.status = 'error' THEN 'retry' ELSE 'members_changed' END AS reason,
               (SELECT sum(coalesce((m.usage->>'touches_30d')::int, 0)) + max(m.n_rows) / 1e6 FROM rvbbit.meaning_relations m WHERE m.relation = ANY(u.members))::numeric AS priority
          FROM (SELECT * FROM sources WHERE recognized) s          -- units are computed only for recognized sources
          CROSS JOIN LATERAL rvbbit.meaning_units(s.schema_name) u
          LEFT JOIN rvbbit.meaning_domains d ON d.domain_key = u.unit_key
         WHERE d.domain_key IS NULL
            OR (d.status = 'error' AND d.attempts < 4 AND d.updated_at < now() - interval '1 hour')
            OR (d.status = 'done' AND d.member_fingerprint IS DISTINCT FROM u.member_fingerprint AND d.updated_at < now() - interval '6 hours')
    )
    SELECT * FROM (SELECT * FROM recog UNION ALL SELECT * FROM units) q
     ORDER BY (q.kind = 'recognition') DESC, q.priority DESC NULLS LAST
     LIMIT p_max;
END $fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_narrative_spend(p_window interval DEFAULT interval '24 hours')
RETURNS numeric LANGUAGE sql STABLE AS $fn$
    SELECT coalesce((SELECT sum(cost_usd) FROM rvbbit.meaning_recognitions WHERE updated_at > now() - p_window), 0)
         + coalesce((SELECT sum(cost_usd) FROM rvbbit.meaning_domains WHERE updated_at > now() - p_window), 0)
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_narrative_status() RETURNS jsonb
LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_build_object(
        'sources_recognized', (SELECT count(*) FROM rvbbit.meaning_recognitions WHERE status = 'done'),
        'sources_error', (SELECT count(*) FROM rvbbit.meaning_recognitions WHERE status = 'error'),
        'relations_named', (SELECT count(*) FROM rvbbit.meaning_relations WHERE recognized_at IS NOT NULL),
        'domains', (SELECT count(*) FROM rvbbit.meaning_domains WHERE status = 'done'),
        'domains_usable', (SELECT count(*) FROM rvbbit.meaning_domains WHERE usable),
        'domains_reviewed', (SELECT count(*) FROM rvbbit.meaning_domains WHERE review IS NOT NULL),
        'domains_error', (SELECT count(*) FROM rvbbit.meaning_domains WHERE status = 'error'),
        'queue', (SELECT jsonb_object_agg(kind, n) FROM (SELECT kind, count(*) AS n FROM rvbbit.meaning_narrative_queue(1000) GROUP BY 1) q),
        'spend_24h_usd', rvbbit.meaning_narrative_spend(),
        'spend_total_usd', (SELECT coalesce(sum(cost_usd), 0) FROM rvbbit.meaning_recognitions) + (SELECT coalesce(sum(cost_usd), 0) FROM rvbbit.meaning_domains),
        'last_activity', greatest((SELECT max(updated_at) FROM rvbbit.meaning_recognitions), (SELECT max(updated_at) FROM rvbbit.meaning_domains)))
$fn$;

-- ---------------------------------------------------------------------------
-- Cards read the narrative first; the old excavator's domains stay beside it.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_card(p_schema text, p_rel text)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    v_rel    text := lower(p_schema || '.' || p_rel);
    m        rvbbit.meaning_relations%ROWTYPE;
    d        rvbbit.meaning_domains%ROWTYPE;
    v_over   jsonb;
    v_cols   jsonb;
    v_roles  jsonb;
    v_joins  jsonb;
    v_joined jsonb;
    v_refs   jsonb;
    v_pii    jsonb;
    v_deserves text;
    v_domain jsonb;
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

    IF coalesce(v_over->>'domain_key', m.domain_key) IS NOT NULL THEN
        SELECT * INTO d FROM rvbbit.meaning_domains WHERE domain_key = coalesce(v_over->>'domain_key', m.domain_key);
        IF FOUND THEN
            v_domain := jsonb_strip_nulls(jsonb_build_object(
                'key', d.domain_key, 'name', d.name, 'about', d.about,
                'questions', (SELECT jsonb_agg(q) FROM (SELECT q FROM jsonb_array_elements_text(d.questions) q LIMIT 4) x),
                'candidate_metrics', (SELECT jsonb_agg(jsonb_build_object('name', c->>'name', 'formula', c->>'formula')) FROM (SELECT c FROM jsonb_array_elements(d.candidate_metrics) c LIMIT 4) x),
                'members', d.members, 'usable', d.usable, 'review', d.review - 'kind_probs'));
        END IF;
    END IF;

    RETURN jsonb_strip_nulls(jsonb_build_object(
        'relation', v_rel,
        'name', coalesce(v_over->>'business_name', m.business_name, rvbbit.meaning_display_name(m.rel_name)),
        'what', coalesce(v_over->>'what', m.narrative, m.what),
        'what_from_structure', CASE WHEN m.narrative IS NOT NULL THEN m.what END,
        'kind', coalesce(v_over->>'kind', m.kind),
        'object_role', m.object_role, 'package', m.package,
        'source', jsonb_strip_nulls(jsonb_build_object('system', m.source_system, 'role', m.source_role,
                     'recognized_as', (SELECT r.output->>'product' FROM rvbbit.meaning_recognitions r WHERE r.schema_name = m.schema_name AND r.status = 'done'))),
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
        'domain', v_domain,
        'domain_key', coalesce(v_over->>'domain_key', m.domain_key),
        'drift', m.drift,
        'usage', m.usage,
        'confidence', CASE WHEN v_over <> '{}'::jsonb THEN 1.0 ELSE m.confidence END,
        'provenance', jsonb_strip_nulls(jsonb_build_object(
            'judged_by', m.judge_model, 'judged_at', m.judged_at, 'status', m.status,
            'narrated_at', m.recognized_at,
            'stated_by_people', CASE WHEN v_over <> '{}'::jsonb THEN v_over END,
            'note', 'provisional: described from structure, a small judge' || CASE WHEN m.narrative IS NOT NULL THEN ' and one recognition call' ELSE '' END || '; not affirmed canon'))));
END $fn$;

-- new columns land mid-list; CREATE OR REPLACE VIEW cannot reorder, so recreate (no dependents)
DROP VIEW IF EXISTS rvbbit.meaning_overview;
CREATE VIEW rvbbit.meaning_overview AS
SELECT m.relation, m.schema_name, m.rel_name, m.source_system, m.source_role,
       coalesce(o.business_name, m.business_name, rvbbit.meaning_display_name(m.rel_name)) AS name,
       coalesce(o.kind, m.kind) AS kind, m.kind_p, coalesce(m.narrative, m.what) AS what, m.narrative, m.object_role, m.package,
       m.n_rows, m.n_columns, m.custom_fields,
       m.time_column, m.time_grain, m.entity_keys,
       m.metric_worthy, m.plumbing, m.has_pii, m.customized, m.confidence,
       coalesce(o.domain_key, m.domain_key) AS domain_key,
       d.name AS domain_name, d.usable AS domain_usable,
       (m.drift->>'severity')::numeric AS drift_severity, m.drift->'flags' AS drift_flags,
       (m.usage->>'touches_30d')::int AS touches_30d,
       (SELECT count(*) FROM rvbbit.meaning_columns c WHERE c.relation = m.relation) AS columns_judged,
       (SELECT count(*) FROM rvbbit.meaning_links l WHERE l.from_relation = m.relation AND l.state = 'verified') AS verified_joins,
       (SELECT count(*) FROM rvbbit.relation_refs r WHERE r.relation = m.relation AND r.ref_kind IN ('metric','cube')) AS measured_by,
       (SELECT count(*) FROM rvbbit.relation_refs r WHERE r.relation = m.relation AND r.ref_kind = 'domain') AS old_domains,
       (o.business_name IS NOT NULL OR o.kind IS NOT NULL OR o.domain_key IS NOT NULL) AS overridden,
       m.status, m.judged_at, m.recognized_at, m.cost_usd
  FROM rvbbit.meaning_relations m
  LEFT JOIN LATERAL (
        SELECT max(value #>> '{}') FILTER (WHERE field = 'business_name') AS business_name,
               max(value #>> '{}') FILTER (WHERE field = 'kind') AS kind,
               max(value #>> '{}') FILTER (WHERE field = 'domain_key') AS domain_key
          FROM rvbbit.meaning_overrides mo WHERE mo.relation = m.relation AND mo.col_name IS NULL AND mo.retired_at IS NULL) o ON true
  LEFT JOIN rvbbit.meaning_domains d ON d.domain_key = coalesce(o.domain_key, m.domain_key);

COMMENT ON TABLE rvbbit.meaning_recognitions IS 'One strong-model recognition per source (product, business names, roles, expected joins, standard questions); joins are probed before belief.';
COMMENT ON TABLE rvbbit.meaning_domains IS 'One strong-model narrative per verified join cluster; Jev-reviewed (makes_sense, kind) before usable.';
