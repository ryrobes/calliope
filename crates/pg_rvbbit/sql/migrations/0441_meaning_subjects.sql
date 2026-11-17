-- 0441_meaning_subjects — subjects instead of islands (docs/MEANING_PLAN.md, slice 4a, second pass).
--
-- The first narrative pass described one domain per verified-join cluster
-- inside one source and produced 139 single-table "domains": per-source
-- clustering cannot see that Salesforce leads, the marts' lead funnel and the
-- ad platforms are one subject, a hub rule cut legitimate parents (Meta's
-- ad_history), a "worthy singleton" rule let any big table stand alone, and a
-- kind gate rejected whole subjects because some member tables are
-- configuration. This pass goes top-down:
--
--   map      one strong-model call over every recognized source at once →
--            the company's subjects (≤ 15), cross-source, with the objects it
--            can place and a role per object (core | support | configuration);
--   assign   every remaining admitted table gets a Jev Choice question
--            (subject names + "unclear") so nothing is an island by default;
--   dossier  one strong-model call per subject with the full membership →
--            description, sub-areas, the core spine, per-table roles,
--            questions, candidate metrics, gaps; Jev checks makes_sense only;
--   reconcile subjects without a core table fold into the subject they share
--            the most verified joins with.
--
-- rvbbit.meaning_domains keeps holding subjects (origin = 'map'); the old
-- cluster rows are cleared at the end of this migration.

ALTER TABLE rvbbit.meaning_domains
    ADD COLUMN IF NOT EXISTS origin      text NOT NULL DEFAULT 'cluster',   -- cluster | map
    ADD COLUMN IF NOT EXISTS core        text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS roles       jsonb NOT NULL DEFAULT '{}'::jsonb, -- relation → core | support | configuration | plumbing
    ADD COLUMN IF NOT EXISTS areas       jsonb NOT NULL DEFAULT '[]'::jsonb, -- [{name, about, tables[]}]
    ADD COLUMN IF NOT EXISTS gaps        jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS map_id      bigint,
    ADD COLUMN IF NOT EXISTS dossier_at  timestamptz;

ALTER TABLE rvbbit.meaning_relations
    ADD COLUMN IF NOT EXISTS subject_role text,          -- core | support | configuration | plumbing
    ADD COLUMN IF NOT EXISTS placed_by    text;          -- map | jev | dossier | override

CREATE TABLE IF NOT EXISTS rvbbit.meaning_company_map (
    map_id          bigserial PRIMARY KEY,
    fingerprint     text,                   -- of the recognitions it was built from
    packet          jsonb,
    output          jsonb,
    model           text,
    tokens_in       int,
    tokens_out      int,
    cost_usd        numeric(12,6) NOT NULL DEFAULT 0,
    subjects        int,
    placed          int,
    status          text NOT NULL DEFAULT 'pending',   -- pending | done | error
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- The company map: packet, fingerprint, apply.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_map_fingerprint() RETURNS text
LANGUAGE sql STABLE AS $fn$
    SELECT md5(coalesce(string_agg(r.schema_name || ':' || r.fingerprint, ',' ORDER BY r.schema_name), ''))
      FROM rvbbit.meaning_recognitions r WHERE r.status = 'done'
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_map_packet(p_objects_per_source int DEFAULT 30)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_build_object(
        'sources', (SELECT jsonb_agg(src ORDER BY src->'rows_total' DESC NULLS LAST) FROM (
            SELECT jsonb_strip_nulls(jsonb_build_object(
                'schema', r.schema_name,
                'product', r.output->>'product',
                'role', bs.role,
                'standard_questions', (SELECT jsonb_agg(q) FROM (SELECT q FROM jsonb_array_elements_text(rvbbit._jsonb_arr(r.output->'standard_questions')) q LIMIT 6) x),
                'tables_total', (SELECT count(*) FROM rvbbit.meaning_relations m WHERE m.schema_name = r.schema_name AND m.status = 'judged'),
                'rows_total', (SELECT sum(m.n_rows) FROM rvbbit.meaning_relations m WHERE m.schema_name = r.schema_name AND m.status = 'judged'),
                'objects', (SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                                'table', m.rel_name, 'name', m.business_name, 'role', m.object_role, 'package', m.package,
                                'what', left(m.narrative, 140), 'rows', m.n_rows, 'kind', m.kind,
                                'refs', (SELECT jsonb_agg(DISTINCT split_part(l.to_relation, '.', 2)) FROM rvbbit.meaning_links l
                                          WHERE l.from_relation = m.relation AND l.state = 'verified' AND l.to_relation <> m.relation)))
                               ORDER BY coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST)
                             FROM (SELECT * FROM rvbbit.meaning_relations m
                                    WHERE m.schema_name = r.schema_name AND m.status = 'judged'
                                      AND coalesce(m.plumbing, 0) < 0.7 AND coalesce(m.kind, '') <> 'system_plumbing'
                                      AND NOT (m.rel_name = ANY (SELECT jsonb_array_elements_text(rvbbit._jsonb_arr(r.output->'ignore'))))
                                    ORDER BY coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST
                                    LIMIT p_objects_per_source) m)))
              AS src
              FROM rvbbit.meaning_recognitions r
              LEFT JOIN rvbbit.business_sources bs ON bs.schema_name = r.schema_name
             WHERE r.status = 'done') s),
        'cross_source_joins', (SELECT jsonb_agg(j) FROM (
            SELECT DISTINCT split_part(l.from_relation, '.', 1) || '.' || split_part(l.from_relation, '.', 2) || ' → ' || l.to_relation AS j
              FROM rvbbit.meaning_links l WHERE l.state = 'verified' AND split_part(l.from_relation, '.', 1) <> split_part(l.to_relation, '.', 1) LIMIT 60) x))
$fn$;

-- Apply a map: replace map-origin subjects, place the objects it named.
CREATE OR REPLACE FUNCTION rvbbit.meaning_apply_map(p_map_id bigint, p_output jsonb)
RETURNS jsonb LANGUAGE plpgsql AS $fn$
DECLARE
    v_subj    jsonb;
    v_key     text;
    v_member  jsonb;
    v_rel     text;
    v_role    text;
    v_members text[];
    v_core    text[];
    v_roles   jsonb;
    v_n_subj  int := 0;
    v_placed  int := 0;
    v_seen    text[] := '{}';
BEGIN
    -- clean slate for map-origin subjects and everything placed by an earlier map / cluster pass
    UPDATE rvbbit.meaning_relations m SET domain_key = NULL, subject_role = NULL, placed_by = NULL
     WHERE m.placed_by IS DISTINCT FROM 'override' AND NOT (rvbbit.meaning_overrides_for(m.relation, NULL) ? 'domain_key');
    DELETE FROM rvbbit.meaning_domains WHERE origin IN ('map', 'cluster');

    FOR v_subj IN SELECT * FROM jsonb_array_elements(rvbbit._jsonb_arr(p_output->'subjects')) LOOP
        v_key := 'subject:' || regexp_replace(lower(coalesce(v_subj->>'key', v_subj->>'name', 'subject')), '[^a-z0-9]+', '_', 'g');
        CONTINUE WHEN v_key = ANY(v_seen) OR nullif(btrim(v_subj->>'name'), '') IS NULL;
        v_seen := array_append(v_seen, v_key);
        v_members := '{}'; v_core := '{}'; v_roles := '{}'::jsonb;
        FOR v_member IN SELECT * FROM jsonb_array_elements(rvbbit._jsonb_arr(v_subj->'members')) LOOP
            v_rel := lower(coalesce(v_member->>'table', v_member #>> '{}', ''));
            CONTINUE WHEN v_rel = '' OR position('.' IN v_rel) = 0;
            CONTINUE WHEN NOT EXISTS (SELECT 1 FROM rvbbit.meaning_relations m WHERE m.relation = v_rel AND m.status = 'judged');
            CONTINUE WHEN v_rel = ANY(v_members);
            v_role := CASE lower(coalesce(v_member->>'role', 'core')) WHEN 'support' THEN 'support' WHEN 'configuration' THEN 'configuration'
                           WHEN 'plumbing' THEN 'plumbing' ELSE 'core' END;
            v_members := array_append(v_members, v_rel);
            IF v_role = 'core' THEN v_core := array_append(v_core, v_rel); END IF;
            v_roles := v_roles || jsonb_build_object(v_rel, v_role);
            UPDATE rvbbit.meaning_relations m SET domain_key = v_key, subject_role = v_role, placed_by = 'map', updated_at = now()
             WHERE m.relation = v_rel AND m.placed_by IS DISTINCT FROM 'override'
               AND NOT (rvbbit.meaning_overrides_for(m.relation, NULL) ? 'domain_key');
            IF FOUND THEN v_placed := v_placed + 1; END IF;
        END LOOP;
        INSERT INTO rvbbit.meaning_domains (domain_key, schema_name, source_system, name, about, questions, members, core, roles, member_fingerprint,
                                            origin, map_id, status, usable, attempts, updated_at)
        VALUES (v_key, coalesce(split_part(v_members[1], '.', 1), 'company'), 'company',
                left(btrim(v_subj->>'name'), 120), left(btrim(v_subj->>'about'), 1200),
                rvbbit._jsonb_arr(v_subj->'questions'), v_members, v_core, v_roles,
                md5(array_to_string(v_members, ',')), 'map', p_map_id, 'placed', false, 0, now());
        v_n_subj := v_n_subj + 1;
    END LOOP;
    UPDATE rvbbit.meaning_company_map SET subjects = v_n_subj, placed = v_placed WHERE map_id = p_map_id;
    RETURN jsonb_build_object('subjects', v_n_subj, 'placed', v_placed);
END $fn$;

-- ---------------------------------------------------------------------------
-- Assignment: every unplaced admitted table picks a subject through Jev.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_subject_choices() RETURNS jsonb
LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(jsonb_object_agg(d.domain_key, left(coalesce(d.name, '') || ': ' || coalesce(d.about, ''), 240)), '{}'::jsonb)
      FROM (SELECT * FROM rvbbit.meaning_domains WHERE origin = 'map' ORDER BY cardinality(members) DESC LIMIT 15) d
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_assign_unplaced(p_max int DEFAULT 400)
RETURNS jsonb LANGUAGE plpgsql AS $fn$
DECLARE
    v_choices jsonb := rvbbit.meaning_subject_choices();
    v_q       jsonb;
    v_rows    text[];
    v_chunk   text[];
    v_i       int;
    v_j       int;
    v_batch   jsonb;
    v_ans     jsonb;
    v_state   jsonb;
    v_rel     text;
    v_pick    text;
    v_p       numeric;
    v_done    int := 0;
    v_unclear int := 0;
    v_cost    numeric := 0;
BEGIN
    IF v_choices = '{}'::jsonb THEN RETURN jsonb_build_object('status', 'no_subjects'); END IF;
    PERFORM rvbbit.meaning_assign_by_kin();
    v_q := jsonb_build_object('subject', jsonb_build_object(
        'type', 'choice',
        'instructions', 'Which of the company''s subjects does this table belong to? Judge from what one row is, the table''s name, its source system and what it joins to. Pick unclear only when none fits.',
        'criteria', v_choices || jsonb_build_object('unclear', 'none of these subjects: system plumbing, an unrelated area, or impossible to tell')));
    SELECT coalesce(array_agg(m.relation ORDER BY coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST), '{}')
      INTO v_rows
      FROM (SELECT * FROM rvbbit.meaning_relations m
             WHERE m.status = 'judged' AND m.domain_key IS NULL AND m.placed_by IS DISTINCT FROM 'override'
               AND coalesce(m.plumbing, 0) < 0.7 AND coalesce(m.kind, '') <> 'system_plumbing'
               -- a table the judge already called unclear is not re-asked for a day
               AND NOT (coalesce(m.placed_by, '') = 'jev:unclear' AND m.updated_at > now() - interval '24 hours')
             ORDER BY coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST
             LIMIT p_max) m;
    IF cardinality(v_rows) = 0 THEN RETURN jsonb_build_object('status', 'nothing_unplaced'); END IF;

    FOR v_i IN 1 .. cardinality(v_rows) BY 8 LOOP
        v_chunk := v_rows[v_i : v_i + 7];
        v_batch := NULL;
        IF to_regprocedure('rvbbit.call_specialist_batch(text,jsonb)') IS NOT NULL THEN
            BEGIN
                SELECT rvbbit.call_specialist_batch('decisions', jsonb_agg(
                           jsonb_build_object('model', 'typesafe/jev-1.13', 'contract', 'clover-decisions-v1',
                                              'state', rvbbit.meaning_assign_state(c.rel), 'questions', v_q) ORDER BY c.i))
                  INTO v_batch FROM unnest(v_chunk) WITH ORDINALITY AS c(rel, i);
                IF jsonb_typeof(v_batch) <> 'array' OR jsonb_array_length(v_batch) <> cardinality(v_chunk) THEN v_batch := NULL; END IF;
            EXCEPTION WHEN OTHERS THEN
                RAISE NOTICE 'meaning: assignment batch failed (%); scalar fallback', left(SQLERRM, 160);
                v_batch := NULL;
            END;
        END IF;
        FOR v_j IN 1 .. cardinality(v_chunk) LOOP
            v_rel := v_chunk[v_j];
            IF v_batch IS NOT NULL THEN v_ans := v_batch -> (v_j - 1);
            ELSE v_ans := rvbbit.clover_decide(rvbbit.meaning_assign_state(v_rel), v_q); END IF;
            CONTINUE WHEN v_ans IS NULL OR v_ans->'answers' IS NULL;
            v_cost := v_cost + coalesce((v_ans #>> '{usage,cost}')::numeric, 0);
            v_pick := v_ans #>> '{answers,subject,choice}';
            v_p := (v_ans #> '{answers,subject,probabilities}' ->> v_pick)::numeric;
            IF v_pick IS NULL OR v_pick = 'unclear' OR coalesce(v_p, 0) < 0.35 THEN
                v_unclear := v_unclear + 1;
                UPDATE rvbbit.meaning_relations SET placed_by = 'jev:unclear', updated_at = now() WHERE relation = v_rel;
                CONTINUE;
            END IF;
            UPDATE rvbbit.meaning_relations SET domain_key = v_pick, subject_role = 'support', placed_by = 'jev', updated_at = now() WHERE relation = v_rel;
            UPDATE rvbbit.meaning_domains SET members = array_append(members, v_rel), roles = roles || jsonb_build_object(v_rel, 'support'),
                   member_fingerprint = md5(array_to_string(array_append(members, v_rel), ',')), updated_at = now()
             WHERE domain_key = v_pick AND NOT (v_rel = ANY(members));
            v_done := v_done + 1;
        END LOOP;
    END LOOP;
    RETURN jsonb_build_object('assigned', v_done, 'unclear', v_unclear, 'cost_usd', round(v_cost, 6));
END $fn$;

-- Deterministic placement before the judge: a table named after a placed table
-- (lead_history, lead_feed, lead_share, transaction_c_hd) follows it, and a
-- table whose verified joins point mostly at one subject joins that subject.
CREATE OR REPLACE FUNCTION rvbbit.meaning_assign_by_kin() RETURNS jsonb
LANGUAGE plpgsql AS $fn$
DECLARE
    v_sibling int := 0;
    v_joins   int := 0;
    r         record;
BEGIN
    FOR r IN
        SELECT m.relation, b.domain_key
          FROM rvbbit.meaning_relations m
          JOIN LATERAL (
                SELECT b.domain_key FROM rvbbit.meaning_relations b
                 WHERE b.schema_name = m.schema_name AND b.domain_key IS NOT NULL
                   AND b.rel_name = regexp_replace(m.rel_name, '_(history|feed|share|hd|changeevent|change_event)$', '')
                   AND b.rel_name <> m.rel_name
                 LIMIT 1) b ON true
         WHERE m.status = 'judged' AND m.domain_key IS NULL AND m.placed_by IS DISTINCT FROM 'override'
           AND m.rel_name ~ '_(history|feed|share|hd|changeevent|change_event)$'
    LOOP
        UPDATE rvbbit.meaning_relations SET domain_key = r.domain_key, subject_role = 'support', placed_by = 'kin:sibling', updated_at = now() WHERE relation = r.relation;
        UPDATE rvbbit.meaning_domains SET members = array_append(members, r.relation), roles = roles || jsonb_build_object(r.relation, 'support'), updated_at = now()
         WHERE domain_key = r.domain_key AND NOT (r.relation = ANY(members));
        v_sibling := v_sibling + 1;
    END LOOP;
    FOR r IN
        SELECT x.relation, x.domain_key
          FROM (SELECT m.relation, o.domain_key, count(*) AS n,
                       sum(count(*)) OVER (PARTITION BY m.relation) AS total
                  FROM rvbbit.meaning_relations m
                  JOIN rvbbit.meaning_links l ON l.from_relation = m.relation AND l.state = 'verified'
                  JOIN rvbbit.meaning_relations o ON o.relation = l.to_relation AND o.domain_key IS NOT NULL
                 WHERE m.status = 'judged' AND m.domain_key IS NULL AND m.placed_by IS DISTINCT FROM 'override'
                   AND coalesce(m.plumbing, 0) < 0.7
                 GROUP BY m.relation, o.domain_key) x
         WHERE x.n::numeric / x.total >= 0.6
    LOOP
        UPDATE rvbbit.meaning_relations SET domain_key = r.domain_key, subject_role = 'support', placed_by = 'kin:joins', updated_at = now() WHERE relation = r.relation AND domain_key IS NULL;
        IF FOUND THEN
            UPDATE rvbbit.meaning_domains SET members = array_append(members, r.relation), roles = roles || jsonb_build_object(r.relation, 'support'), updated_at = now()
             WHERE domain_key = r.domain_key AND NOT (r.relation = ANY(members));
            v_joins := v_joins + 1;
        END IF;
    END LOOP;
    RETURN jsonb_build_object('siblings', v_sibling, 'by_joins', v_joins);
END $fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_assign_state(p_relation text) RETURNS jsonb
LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'table', m.relation, 'name', m.business_name, 'what', coalesce(m.narrative, m.what), 'kind', m.kind, 'package', m.package,
        'source', m.source_system, 'rows', m.n_rows,
        'joins_to', (SELECT jsonb_agg(DISTINCT l.to_relation) FROM rvbbit.meaning_links l WHERE l.from_relation = m.relation AND l.state = 'verified'),
        'categories', (SELECT jsonb_agg(jsonb_build_object(c->>'name', c->'top')) FROM (SELECT c FROM jsonb_array_elements(rvbbit._jsonb_arr(m.profile->'categorical')) c LIMIT 2) x)))
      FROM rvbbit.meaning_relations m WHERE m.relation = p_relation
$fn$;

-- ---------------------------------------------------------------------------
-- Dossiers: one call per subject with its whole membership.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_subject_packet(p_key text, p_max_tables int DEFAULT 90)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'subject', d.name, 'about_from_map', d.about, 'questions_from_map', d.questions,
        'tables_total', cardinality(d.members),
        'tables', (SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
                        'table', m.relation, 'name', m.business_name, 'what', left(coalesce(m.narrative, m.what), 200),
                        'role_from_map', d.roles->>m.relation, 'placed_by', m.placed_by,
                        'kind', m.kind, 'rows', m.n_rows, 'time', m.time_column,
                        'keys', CASE WHEN cardinality(m.entity_keys) > 0 THEN to_jsonb(m.entity_keys[1:2]) END,
                        'categories', (SELECT jsonb_agg(jsonb_build_object(c->>'name', c->'top')) FROM (SELECT c FROM jsonb_array_elements(rvbbit._jsonb_arr(m.profile->'categorical')) c LIMIT 3) x),
                        'measures', (SELECT jsonb_agg(v) FROM (SELECT v FROM jsonb_array_elements_text(rvbbit._jsonb_arr(m.profile->'measures')) v LIMIT 6) x),
                        'joins', (SELECT jsonb_agg(l.from_column || '→' || l.to_relation) FROM (SELECT from_column, to_relation FROM rvbbit.meaning_links l
                                     WHERE l.from_relation = m.relation AND l.state = 'verified' AND l.to_relation = ANY(d.members) LIMIT 6) l)))
                     ORDER BY (d.roles->>m.relation = 'core') DESC, coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST)
                     FROM (SELECT * FROM rvbbit.meaning_relations m WHERE m.relation = ANY(d.members)
                            ORDER BY (d.roles->>m.relation = 'core') DESC, coalesce((m.usage->>'touches_30d')::int, 0) DESC, m.n_rows DESC NULLS LAST
                            LIMIT p_max_tables) m)))
      FROM rvbbit.meaning_domains d WHERE d.domain_key = p_key
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_apply_dossier(
    p_key text, p_output jsonb, p_packet jsonb, p_model text, p_tokens_in int, p_tokens_out int, p_cost numeric)
RETURNS jsonb LANGUAGE plpgsql AS $fn$
DECLARE
    d       rvbbit.meaning_domains%ROWTYPE;
    v_rel   text;
    v_role  text;
    v_roles jsonb := '{}'::jsonb;
    v_core  text[] := '{}';
    v_pair  record;
BEGIN
    SELECT * INTO d FROM rvbbit.meaning_domains WHERE domain_key = p_key;
    IF NOT FOUND THEN RETURN jsonb_build_object('status', 'missing'); END IF;
    -- per-table roles from the dossier, for members only; unmentioned members keep the map's role
    v_roles := d.roles;
    FOR v_pair IN SELECT key, value FROM jsonb_each_text(CASE WHEN jsonb_typeof(p_output->'roles') = 'object' THEN p_output->'roles' ELSE '{}'::jsonb END) LOOP
        v_rel := lower(v_pair.key);
        CONTINUE WHEN NOT (v_rel = ANY(d.members));
        v_role := CASE lower(v_pair.value) WHEN 'core' THEN 'core' WHEN 'support' THEN 'support' WHEN 'configuration' THEN 'configuration' WHEN 'plumbing' THEN 'plumbing' ELSE NULL END;
        CONTINUE WHEN v_role IS NULL;
        v_roles := v_roles || jsonb_build_object(v_rel, v_role);
    END LOOP;
    SELECT coalesce(array_agg(k), '{}') INTO v_core FROM jsonb_each_text(v_roles) AS e(k, v) WHERE v = 'core';
    UPDATE rvbbit.meaning_domains SET
        about = coalesce(left(btrim(p_output->>'about'), 1600), about),
        questions = CASE WHEN jsonb_array_length(rvbbit._jsonb_arr(p_output->'questions')) > 0 THEN rvbbit._jsonb_arr(p_output->'questions') ELSE questions END,
        candidate_metrics = rvbbit._jsonb_arr(p_output->'candidate_metrics'),
        areas = rvbbit._jsonb_arr(p_output->'areas'),
        gaps = rvbbit._jsonb_arr(p_output->'gaps'),
        roles = v_roles, core = v_core,
        packet = p_packet, output = p_output, model = p_model, tokens_in = p_tokens_in, tokens_out = p_tokens_out,
        cost_usd = cost_usd + coalesce(p_cost, 0), status = 'done', error = NULL, attempts = attempts + 1,
        dossier_at = now(), review = NULL, usable = false, updated_at = now()
     WHERE domain_key = p_key;
    UPDATE rvbbit.meaning_relations m SET subject_role = v_roles->>m.relation, updated_at = now()
     WHERE m.domain_key = p_key AND v_roles ? m.relation AND m.placed_by IS DISTINCT FROM 'override';
    RETURN jsonb_build_object('core', cardinality(v_core), 'members', cardinality(d.members));
END $fn$;

-- Review: does the dossier make sense? No kind gate — roles live on tables now.
CREATE OR REPLACE FUNCTION rvbbit.meaning_review_domain(p_domain_key text)
RETURNS jsonb LANGUAGE plpgsql AS $fn$
DECLARE
    d       rvbbit.meaning_domains%ROWTYPE;
    v_state jsonb;
    v_ans   jsonb;
    v_sense numeric;
    v_usable boolean;
BEGIN
    SELECT * INTO d FROM rvbbit.meaning_domains WHERE domain_key = p_domain_key;
    IF NOT FOUND OR d.status <> 'done' THEN RETURN NULL; END IF;
    v_state := jsonb_build_object(
        'name', d.name, 'about', d.about, 'questions', d.questions,
        'core_tables', (SELECT jsonb_agg(jsonb_build_object('table', m.relation, 'name', m.business_name, 'what', left(m.narrative, 120)))
                          FROM rvbbit.meaning_relations m WHERE m.relation = ANY(d.core)),
        'support_tables', (SELECT count(*) FROM jsonb_each_text(d.roles) WHERE value <> 'core'),
        'candidate_metrics', (SELECT jsonb_agg(c->'name') FROM (SELECT c FROM jsonb_array_elements(d.candidate_metrics) c LIMIT 6) x));
    v_ans := rvbbit.clover_decide(v_state, $j${
      "makes_sense": {"type": "noul", "instructions": "This is a coherent business subject an analyst at this company would recognize by this name; the description is grounded in the listed core tables rather than generic, and the questions are ones the company would actually ask."}}$j$::jsonb);
    IF v_ans IS NULL OR v_ans->'answers' IS NULL THEN
        UPDATE rvbbit.meaning_domains SET review = jsonb_build_object('error', 'judge returned no answers', 'at', now()), updated_at = now() WHERE domain_key = p_domain_key;
        RETURN NULL;
    END IF;
    v_sense := (v_ans #>> '{answers,makes_sense,noul}')::numeric;
    v_usable := coalesce(v_sense, 0) >= 0.5 AND cardinality(d.core) >= 1;
    UPDATE rvbbit.meaning_domains SET
        review = jsonb_build_object('makes_sense', v_sense, 'core_tables', cardinality(d.core), 'model', v_ans->>'model', 'at', now()),
        usable = v_usable, updated_at = now()
     WHERE domain_key = p_domain_key;
    RETURN jsonb_build_object('makes_sense', v_sense, 'core_tables', cardinality(d.core), 'usable', v_usable);
END $fn$;

-- Reconcile: a subject with no core table folds into the subject its members join most.
CREATE OR REPLACE FUNCTION rvbbit.meaning_reconcile_subjects() RETURNS jsonb
LANGUAGE plpgsql AS $fn$
DECLARE
    d       record;
    v_target text;
    v_folded int := 0;
    v_rel   text;
BEGIN
    FOR d IN SELECT * FROM rvbbit.meaning_domains WHERE origin = 'map' AND status = 'done' AND cardinality(core) = 0 LOOP
        SELECT o.domain_key INTO v_target
          FROM rvbbit.meaning_links l
          JOIN rvbbit.meaning_relations o ON o.relation = l.to_relation
         WHERE l.state = 'verified' AND l.from_relation = ANY(d.members)
           AND o.domain_key IS NOT NULL AND o.domain_key <> d.domain_key
           AND EXISTS (SELECT 1 FROM rvbbit.meaning_domains t WHERE t.domain_key = o.domain_key AND cardinality(t.core) > 0)
         GROUP BY o.domain_key ORDER BY count(*) DESC LIMIT 1;
        CONTINUE WHEN v_target IS NULL;
        FOREACH v_rel IN ARRAY d.members LOOP
            UPDATE rvbbit.meaning_relations SET domain_key = v_target, subject_role = coalesce(subject_role, 'support'), placed_by = 'reconcile', updated_at = now()
             WHERE relation = v_rel AND placed_by IS DISTINCT FROM 'override';
            UPDATE rvbbit.meaning_domains SET members = array_append(members, v_rel), roles = roles || jsonb_build_object(v_rel, coalesce(d.roles->>v_rel, 'support')), updated_at = now()
             WHERE domain_key = v_target AND NOT (v_rel = ANY(members));
        END LOOP;
        UPDATE rvbbit.meaning_domains SET status = 'folded', usable = false, error = 'folded into ' || v_target, updated_at = now() WHERE domain_key = d.domain_key;
        v_folded := v_folded + 1;
    END LOOP;
    RETURN jsonb_build_object('folded', v_folded);
END $fn$;

-- ---------------------------------------------------------------------------
-- Queue: map when recognitions changed; dossiers for placed subjects.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION rvbbit.meaning_narrative_queue(p_max int DEFAULT 20)
RETURNS TABLE (kind text, key text, schema_name text, members text[], member_fingerprint text, reason text, priority numeric)
LANGUAGE plpgsql STABLE AS $fn$
BEGIN
    RETURN QUERY
    WITH pend AS MATERIALIZED (
        SELECT c.schema_name, count(*) AS n FROM rvbbit.meaning_candidates(100000) c GROUP BY c.schema_name
    ), sources AS MATERIALIZED (
        SELECT bs.schema_name, bs.source_system,
               (SELECT count(*) FROM rvbbit.meaning_relations m WHERE m.schema_name = bs.schema_name AND m.status = 'judged') AS judged,
               coalesce((SELECT n FROM pend WHERE pend.schema_name = bs.schema_name), 0) AS pending,
               (SELECT sum(coalesce((m.usage->>'touches_30d')::int, 0)) FROM rvbbit.meaning_relations m WHERE m.schema_name = bs.schema_name) AS touches
          FROM rvbbit.business_sources bs
         WHERE bs.role IN ('business', 'derived')
    ), recog AS (
        SELECT 'recognition'::text AS kind, s.schema_name AS key, s.schema_name, NULL::text[] AS members, NULL::text AS member_fingerprint,
               CASE WHEN r.schema_name IS NULL THEN 'new' WHEN r.status = 'error' THEN 'retry' ELSE 'schema_changed' END AS reason,
               (coalesce(s.touches, 0) + s.judged)::numeric AS priority
          FROM sources s
          LEFT JOIN rvbbit.meaning_recognitions r ON r.schema_name = s.schema_name
         WHERE s.judged > 0 AND s.pending = 0
           AND (r.schema_name IS NULL
                OR (r.status = 'error' AND r.attempts < 4 AND r.updated_at < now() - interval '1 hour')
                OR (r.status = 'done' AND r.fingerprint IS DISTINCT FROM rvbbit.meaning_recognition_fingerprint(s.schema_name)
                    AND r.updated_at < now() - interval '6 hours'))
    ), map AS (
        -- one map per set of recognitions; wait until every eligible source is recognized
        SELECT 'map'::text AS kind, 'company'::text AS key, NULL::text AS schema_name, NULL::text[] AS members,
               rvbbit.meaning_map_fingerprint() AS member_fingerprint,
               CASE WHEN NOT EXISTS (SELECT 1 FROM rvbbit.meaning_company_map WHERE status = 'done') THEN 'new' ELSE 'recognitions_changed' END AS reason,
               1e9::numeric AS priority
         WHERE NOT EXISTS (SELECT 1 FROM recog)
           AND EXISTS (SELECT 1 FROM rvbbit.meaning_recognitions WHERE status = 'done')
           AND NOT EXISTS (SELECT 1 FROM rvbbit.meaning_company_map cm
                            WHERE cm.fingerprint = rvbbit.meaning_map_fingerprint()
                              AND (cm.status = 'done' OR (cm.status = 'error' AND cm.created_at > now() - interval '1 hour')))
    ), dossiers AS (
        SELECT 'dossier'::text AS kind, d.domain_key AS key, d.schema_name, d.members, d.member_fingerprint,
               CASE WHEN d.status = 'placed' THEN 'new' WHEN d.status = 'error' THEN 'retry' ELSE 'members_changed' END AS reason,
               (cardinality(d.members) * 10 + coalesce((SELECT sum(coalesce((m.usage->>'touches_30d')::int, 0)) FROM rvbbit.meaning_relations m WHERE m.relation = ANY(d.members)), 0))::numeric AS priority
          FROM rvbbit.meaning_domains d
         WHERE d.origin = 'map'
           AND (d.status = 'placed'
                OR (d.status = 'error' AND d.attempts < 4 AND d.updated_at < now() - interval '1 hour')
                OR (d.status = 'done' AND d.member_fingerprint IS DISTINCT FROM md5(array_to_string(d.members, ',')) AND d.dossier_at < now() - interval '6 hours'))
    )
    SELECT * FROM (SELECT * FROM recog UNION ALL SELECT * FROM map UNION ALL SELECT * FROM dossiers) q
     ORDER BY CASE q.kind WHEN 'recognition' THEN 0 WHEN 'map' THEN 1 ELSE 2 END, q.priority DESC NULLS LAST
     LIMIT p_max;
END $fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_narrative_spend(p_window interval DEFAULT interval '24 hours')
RETURNS numeric LANGUAGE sql STABLE AS $fn$
    SELECT coalesce((SELECT sum(cost_usd) FROM rvbbit.meaning_recognitions WHERE updated_at > now() - p_window), 0)
         + coalesce((SELECT sum(cost_usd) FROM rvbbit.meaning_domains WHERE updated_at > now() - p_window), 0)
         + coalesce((SELECT sum(cost_usd) FROM rvbbit.meaning_company_map WHERE created_at > now() - p_window), 0)
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.meaning_narrative_status() RETURNS jsonb
LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_build_object(
        'sources_recognized', (SELECT count(*) FROM rvbbit.meaning_recognitions WHERE status = 'done'),
        'sources_error', (SELECT count(*) FROM rvbbit.meaning_recognitions WHERE status = 'error'),
        'relations_named', (SELECT count(*) FROM rvbbit.meaning_relations WHERE recognized_at IS NOT NULL),
        'map', (SELECT jsonb_build_object('map_id', map_id, 'subjects', subjects, 'placed', placed, 'model', model, 'cost_usd', cost_usd, 'created_at', created_at, 'status', status)
                  FROM rvbbit.meaning_company_map ORDER BY map_id DESC LIMIT 1),
        'subjects', (SELECT count(*) FROM rvbbit.meaning_domains WHERE origin = 'map' AND status IN ('placed', 'done')),
        'subjects_usable', (SELECT count(*) FROM rvbbit.meaning_domains WHERE origin = 'map' AND usable),
        'subjects_folded', (SELECT count(*) FROM rvbbit.meaning_domains WHERE origin = 'map' AND status = 'folded'),
        'domains', (SELECT count(*) FROM rvbbit.meaning_domains WHERE status = 'done'),
        'domains_usable', (SELECT count(*) FROM rvbbit.meaning_domains WHERE usable),
        'domains_reviewed', (SELECT count(*) FROM rvbbit.meaning_domains WHERE review IS NOT NULL),
        'domains_error', (SELECT count(*) FROM rvbbit.meaning_domains WHERE status = 'error'),
        'placed', (SELECT jsonb_object_agg(coalesce(placed_by, 'unplaced'), n) FROM (SELECT placed_by, count(*) AS n FROM rvbbit.meaning_relations WHERE status = 'judged' GROUP BY 1) x),
        'roles', (SELECT jsonb_object_agg(coalesce(subject_role, 'none'), n) FROM (SELECT subject_role, count(*) AS n FROM rvbbit.meaning_relations WHERE status = 'judged' AND domain_key IS NOT NULL GROUP BY 1) x),
        'queue', (SELECT jsonb_object_agg(kind, n) FROM (SELECT kind, count(*) AS n FROM rvbbit.meaning_narrative_queue(1000) GROUP BY 1) q),
        'spend_24h_usd', rvbbit.meaning_narrative_spend(),
        'spend_total_usd', (SELECT coalesce(sum(cost_usd), 0) FROM rvbbit.meaning_recognitions) + (SELECT coalesce(sum(cost_usd), 0) FROM rvbbit.meaning_domains)
                           + (SELECT coalesce(sum(cost_usd), 0) FROM rvbbit.meaning_company_map),
        'last_activity', greatest((SELECT max(updated_at) FROM rvbbit.meaning_recognitions), (SELECT max(updated_at) FROM rvbbit.meaning_domains)))
$fn$;

-- The card's domain block carries the subject role, areas and gaps.
CREATE OR REPLACE FUNCTION rvbbit.meaning_card_domain(p_relation text, p_domain_key text)
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_strip_nulls(jsonb_build_object(
        'key', d.domain_key, 'name', d.name, 'about', d.about,
        'role', coalesce(d.roles->>p_relation, (SELECT subject_role FROM rvbbit.meaning_relations WHERE relation = p_relation)),
        'questions', (SELECT jsonb_agg(q) FROM (SELECT q FROM jsonb_array_elements_text(d.questions) q LIMIT 5) x),
        'candidate_metrics', (SELECT jsonb_agg(jsonb_build_object('name', c->>'name', 'formula', c->>'formula')) FROM (SELECT c FROM jsonb_array_elements(d.candidate_metrics) c LIMIT 5) x),
        'areas', (SELECT jsonb_agg(jsonb_build_object('name', a->>'name', 'tables', a->'tables')) FROM (SELECT a FROM jsonb_array_elements(d.areas) a WHERE a->'tables' ? p_relation OR a->'tables' ? split_part(p_relation, '.', 2) LIMIT 3) x),
        'gaps', (SELECT jsonb_agg(g) FROM (SELECT g FROM jsonb_array_elements_text(d.gaps) g LIMIT 3) x),
        'members', cardinality(d.members), 'core', cardinality(d.core),
        'usable', d.usable, 'status', d.status, 'review', d.review))
      FROM rvbbit.meaning_domains d WHERE d.domain_key = p_domain_key
$fn$;

-- meaning_card: same as 0440 but the domain block comes from meaning_card_domain.
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
        v_domain := rvbbit.meaning_card_domain(v_rel, coalesce(v_over->>'domain_key', m.domain_key));
    END IF;
    RETURN jsonb_strip_nulls(jsonb_build_object(
        'relation', v_rel,
        'name', coalesce(v_over->>'business_name', m.business_name, rvbbit.meaning_display_name(m.rel_name)),
        'what', coalesce(v_over->>'what', m.narrative, m.what),
        'what_from_structure', CASE WHEN m.narrative IS NOT NULL THEN m.what END,
        'kind', coalesce(v_over->>'kind', m.kind),
        'object_role', m.object_role, 'package', m.package,
        'subject_role', m.subject_role, 'placed_by', m.placed_by,
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

DROP VIEW IF EXISTS rvbbit.meaning_overview;
CREATE VIEW rvbbit.meaning_overview AS
SELECT m.relation, m.schema_name, m.rel_name, m.source_system, m.source_role,
       coalesce(o.business_name, m.business_name, rvbbit.meaning_display_name(m.rel_name)) AS name,
       coalesce(o.kind, m.kind) AS kind, m.kind_p, coalesce(m.narrative, m.what) AS what, m.narrative, m.object_role, m.package,
       m.n_rows, m.n_columns, m.custom_fields,
       m.time_column, m.time_grain, m.entity_keys,
       m.metric_worthy, m.plumbing, m.has_pii, m.customized, m.confidence,
       coalesce(o.domain_key, m.domain_key) AS domain_key,
       d.name AS domain_name, d.usable AS domain_usable, m.subject_role, m.placed_by,
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

-- Clean slate for the first pass's cluster domains (cheap to recreate; Ryan's call).
UPDATE rvbbit.meaning_relations SET domain_key = NULL, subject_role = NULL, placed_by = NULL
 WHERE domain_key LIKE 'meaning:%' AND NOT (rvbbit.meaning_overrides_for(relation, NULL) ? 'domain_key');
DELETE FROM rvbbit.meaning_domains WHERE origin = 'cluster';

COMMENT ON TABLE rvbbit.meaning_company_map IS 'One strong-model call over every recognized source: the company''s subjects (≤ 15), cross-source, with placed objects and roles.';
