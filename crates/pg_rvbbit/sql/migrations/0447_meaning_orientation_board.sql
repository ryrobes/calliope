-- 0447_meaning_orientation_board — the first rung of a "bread board", for free.
--
-- Ryan's receipt (chat 3f31c07e, 2026-09-20): asked for a board of an area's top-level
-- concerns, Callie called meaning_tree and spent a 189k-token turn rephrasing it into six
-- text cells — a question as the heading, a one-line scope, "narrow by" dimensions. Every
-- word was already in the dossier. The second board (live charts, a trust note) is where
-- the model earns its keep. So the ladder is three rungs and only the last one costs:
--   1. a pin: one of the subjects Meaning found (rvbbit.meaning_areas)
--   2. the orientation board: this projection over the tree, published in milliseconds,
--      the same for everyone (rvbbit.meaning_orientation_board)
--   3. the data board: Callie's turn, seeded by the pin
--
-- Read: rvbbit.meaning_areas(), rvbbit.meaning_orientation_board(subject_key).

-- 4,477,653 → 4.5M for a tile or a cell.
CREATE OR REPLACE FUNCTION rvbbit._meaning_compact_count(p bigint)
RETURNS text LANGUAGE sql IMMUTABLE AS $fn$
    SELECT CASE WHEN p IS NULL THEN '' WHEN p >= 1000000 THEN round(p / 1000000.0, 1)::text || 'M'
                WHEN p >= 1000 THEN round(p / 1000.0)::text || 'k' ELSE p::text END
$fn$;

-- The subjects as Floor tiles: what Meaning found, how big, what it answers.
CREATE OR REPLACE FUNCTION rvbbit.meaning_areas()
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
               'key', d.domain_key, 'name', d.name, 'about', left(d.about, 280),
               'tables', cardinality(d.members), 'usable', d.usable,
               'areas', (SELECT count(*) FROM jsonb_array_elements(rvbbit._jsonb_arr(d.areas))),
               'questions', (SELECT jsonb_agg(q) FROM (SELECT q FROM jsonb_array_elements_text(rvbbit._jsonb_arr(d.questions)) q LIMIT 3) x),
               'nouns', (SELECT jsonb_agg(coalesce(m.business_name, rvbbit.meaning_display_name(m.rel_name)) ORDER BY array_position(d.core, m.relation))
                           FROM (SELECT * FROM rvbbit.meaning_relations m WHERE m.relation = ANY(d.core) ORDER BY array_position(d.core, m.relation) LIMIT 4) m),
               'rows', (SELECT sum(m.n_rows) FROM rvbbit.meaning_relations m WHERE m.relation = ANY(d.members) AND m.kind IN ('entity_master', 'event_log', 'transactional_fact')),
               'metrics', (SELECT count(*) FROM jsonb_array_elements(rvbbit._jsonb_arr(d.candidate_metrics))),
               'updated_at', d.updated_at))
             ORDER BY cardinality(d.members) DESC), '[]'::jsonb)
      FROM rvbbit.meaning_domains d WHERE d.origin = 'map' AND d.status = 'done'
$fn$;
COMMENT ON FUNCTION rvbbit.meaning_areas() IS 'The business areas Meaning found (subjects), as tiles: name, about, size, a few questions and core nouns.';

-- The orientation board: areas → text cells with what they are and how to narrow them,
-- plus the questions, the callable metrics and the gaps the dossier already knows.
CREATE OR REPLACE FUNCTION rvbbit.meaning_orientation_board(p_subject_key text)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    t        jsonb := rvbbit.meaning_tree(p_subject_key, 40);
    cells    jsonb := '[]'::jsonb;
    a        jsonb;
    n        int := 0;
    v_dims   text;
    v_nouns  text;
    v_q      text;
    v_m      text;
    v_gaps   text;
BEGIN
    IF t IS NULL THEN RETURN NULL; END IF;
    cells := cells || jsonb_build_object(
        'kind', 'text', 'span', 12, 'eyebrow', 'START HERE',
        'heading', t->>'subject',
        'body', left(coalesce(t->>'about', ''), 1200),
        'aside', format('%s tables · %s nouns · areas Callie found in your data; provisional until someone confirms them',
                        coalesce(t->>'tables', '?'), coalesce(t->>'nouns', '?')));
    FOR a IN SELECT x FROM jsonb_array_elements(rvbbit._jsonb_arr(t->'areas')) x LIMIT 8 LOOP
        n := n + 1;
        -- the dimensions people narrow by, from the area's nouns (facets first)
        SELECT string_agg(dim, ' · ' ORDER BY rank, dim)
          INTO v_dims
          FROM (SELECT dim, min(rank) AS rank
                  FROM (SELECT lower(replace(regexp_replace(d->>'name', '_c$', ''), '_', ' ')) AS dim,
                               CASE WHEN d->>'name' ~* '(status|stage|type|source|channel|campaign|campus|program|location|region|owner|vendor)' THEN 0 ELSE 1 END AS rank
                          FROM jsonb_array_elements(rvbbit._jsonb_arr(a->'nouns')) nn,
                               jsonb_array_elements(rvbbit._jsonb_arr(nn->'dimensions')) d) raw
                 GROUP BY dim
                 ORDER BY min(rank), dim
                 LIMIT 6) x;
        SELECT string_agg(coalesce(nn->>'name', nn->>'relation') || CASE WHEN (nn->>'count')::bigint > 0 THEN ' (' || rvbbit._meaning_compact_count((nn->>'count')::bigint) || ')' ELSE '' END, ', ' ORDER BY (nn->>'count')::bigint DESC NULLS LAST)
          INTO v_nouns
          FROM (SELECT nn FROM jsonb_array_elements(rvbbit._jsonb_arr(a->'nouns')) nn LIMIT 4) y;
        cells := cells || jsonb_strip_nulls(jsonb_build_object(
            'kind', 'text', 'span', 4, 'eyebrow', n || ' · AREA',
            'heading', left(a->>'name', 160),
            'body', left(coalesce(nullif(a->>'about', ''), '') || CASE WHEN v_nouns IS NOT NULL THEN ' Nouns: ' || v_nouns || '.' ELSE '' END, 1200),
            'aside', CASE WHEN v_dims IS NOT NULL THEN left('Narrow by: ' || v_dims, 160) END));
    END LOOP;
    SELECT string_agg(q, ' · ') INTO v_q FROM (SELECT q FROM jsonb_array_elements_text(rvbbit._jsonb_arr(t->'questions')) q LIMIT 6) x;
    IF v_q IS NOT NULL THEN
        cells := cells || jsonb_build_object('kind', 'text', 'span', 6, 'eyebrow', 'QUESTIONS THIS AREA ANSWERS',
                                             'heading', 'Ask any of these to start', 'body', left(v_q, 1200));
    END IF;
    SELECT string_agg((m->>'name') || CASE WHEN m->>'status' IS NOT NULL THEN ' (' || (m->>'status') || ')' ELSE '' END, ' · ')
      INTO v_m FROM (SELECT m FROM jsonb_array_elements(rvbbit._jsonb_arr(t->'metrics')) m LIMIT 8) x;
    IF v_m IS NOT NULL THEN
        cells := cells || jsonb_build_object('kind', 'text', 'span', 6, 'eyebrow', 'NUMBERS CALLIE CAN GIVE',
                                             'heading', 'Metrics this area implies', 'body', left(v_m, 1200),
                                             'aside', 'implied = computed from the data, not yet confirmed by a person');
    END IF;
    SELECT string_agg(g, ' · ') INTO v_gaps FROM (SELECT g FROM jsonb_array_elements_text(rvbbit._jsonb_arr(t->'gaps')) g LIMIT 3) x;
    IF v_gaps IS NOT NULL THEN
        cells := cells || jsonb_build_object('kind', 'text', 'span', 12, 'eyebrow', 'KNOWN GAPS',
                                             'heading', 'Keep these in mind', 'body', left(v_gaps, 1200));
    END IF;
    RETURN jsonb_build_object(
        'title', left((t->>'subject') || ' — Where to start', 160),
        'subtitle', 'An orientation board over the areas Meaning found. Pick a cell and ask.',
        'meta', 'orientation',
        'cells', cells);
END $fn$;
COMMENT ON FUNCTION rvbbit.meaning_orientation_board(text) IS 'The first rung of a bread board: the subject''s areas as text cells (what, nouns, narrow by), its questions, callable metrics and gaps. Deterministic; no model call.';
