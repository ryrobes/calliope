-- 0451: the Resolution Ledger's miner (docs/RESOLUTION_LEDGER_PLAN.md §5b/§5c).
--
-- Phase 1 shipped the ledger and the teach verb; the `observed` tier sat in the
-- schema with nothing writing to it. This is the writer. A nightly bounded pass
-- reads what people said (turn text) against what those turns actually touched
-- (pins, governed metrics, source relations from the turn receipts), scores
-- (term → referent) pairs, and:
--   • writes/refreshes `observed` bindings above a noise floor — ranking prior
--     only, never injected (the readers already filter to stipulated/affirmed);
--   • records a `proposed` finding when the plan's thresholds are crossed
--     (mentions ≥5, ≥2 people, dominant referent ≥70%, 60-day window); the
--     worker turns that into an Awaiting You card, and a human's approval is
--     what makes it `affirmed`. Repetition earns a proposal, never tenure.
-- Never touches a stipulated or affirmed row. Findings are the miner's own
-- ledger: a set-aside suppresses re-proposal for 90 days.

-- ── findings: the miner's working ledger ──────────────────────────────────
CREATE TABLE IF NOT EXISTS rvbbit.calliope_term_binding_findings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    term_norm text NOT NULL,
    scope text NOT NULL DEFAULT 'company',
    referent_key text NOT NULL,
    referent jsonb NOT NULL,
    verdict text NOT NULL CHECK (verdict IN ('proposed','affirmed','declined','expired')),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    observation_id uuid,
    decision_id uuid,
    owner_email text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    resolution text,
    actor text
);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_term_binding_findings_open_idx
    ON rvbbit.calliope_term_binding_findings (term_norm, scope, referent_key)
    WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS calliope_term_binding_findings_decision_idx
    ON rvbbit.calliope_term_binding_findings (decision_id) WHERE decision_id IS NOT NULL;
COMMENT ON TABLE rvbbit.calliope_term_binding_findings IS
  'Resolution Ledger miner: one open row per (term, referent) the miner is proposing or has decided; declined suppresses re-proposal for 90 days.';

-- ── what people say: unigrams + bigrams, stoplisted ───────────────────────
CREATE OR REPLACE FUNCTION rvbbit.term_binding_terms(p_text text)
RETURNS text[] LANGUAGE sql IMMUTABLE AS $fn$
    WITH stop AS (
        SELECT ARRAY['the','a','an','and','or','of','for','to','in','on','by','with','vs','versus','how','many','much',
            'what','which','who','when','where','why','is','are','was','were','do','does','did','me','my','our','we',
            'this','that','these','those','last','next','per','from','at','as','it','its','be','can','you','show',
            'give','tell','get','about','over','into','all','any','total','number','count','sum','rate','average',
            'avg','month','week','year','day','quarter','months','weeks','years','days','quarters','compare',
            'compared','between','than','more','less','top','bottom','best','worst','up','down','so','if','not','no',
            'yes','ok','please','also','just','now','today','yesterday','tomorrow','then','there','here','out','off',
            'one','two','three','has','have','had','been','will','would','could','should','like','want','need','see',
            'look','know','think','going','trend','trending','change','changed','growth','grew','fell','rose',
            'across','each','every','some','most','few','table','data','query','sql','chart','dashboard','build',
            'make','create','add','put','use','using','run','let','lets','hey','hi','thanks','thank','okay','yeah',
            'again','still','only','same','other','another','new','old','first','second','list','find','searching',
            'search','value','values','numbers','number','report','reports','stage','pin','pinned','callie','calliope',
            -- time words: a month or a period is when, never what
            'january','february','march','april','may','june','july','august','september','october','november','december',
            'jan','feb','mar','apr','jun','jul','aug','sep','sept','oct','nov','dec',
            'monday','tuesday','wednesday','thursday','friday','saturday','sunday','weekend','weekday',
            'ago','since','through','until','before','after','during','within','recent','recently','early','late',
            'ytd','mtd','qtd','wtd','yoy','mom','wow','q1','q2','q3','q4','h1','h2','fy','annual','annually',
            'daily','weekly','monthly','quarterly','yearly','hourly','period','periods','range','date','dates','time','times',
            -- pronouns and glue: a sentence's scaffolding, never its subject
            'they','them','their','theirs','she','her','hers','him','his','ours','yours','mine','myself','yourself',
            'itself','themselves','something','anything','everything','nothing','someone','anyone','everyone',
            'because','while','though','although','whether','either','neither','both','such','very','really','quite',
            'much','many','lot','lots','kind','sort','way','ways','thing','things','stuff','etc','ie','eg','via'
        ]::text[] AS words
    ),
    w AS (
        SELECT x.ord, x.tok
          FROM unnest(regexp_split_to_array(
                   regexp_replace(lower(left(coalesce(p_text, ''), 2000)), '[^a-z0-9\s]+', ' ', 'g'), '\s+'))
               WITH ORDINALITY AS x(tok, ord), stop
         WHERE x.tok <> '' AND length(x.tok) >= 3 AND x.tok !~ '^[0-9]+$' AND NOT (x.tok = ANY(stop.words))
    )
    SELECT coalesce(array_agg(DISTINCT term), '{}'::text[])
      FROM (
        SELECT tok AS term FROM w
        UNION ALL
        SELECT a.tok || ' ' || b.tok FROM w a JOIN w b ON b.ord = a.ord + 1
      ) s
$fn$;
COMMENT ON FUNCTION rvbbit.term_binding_terms(text) IS 'Candidate business terms in a message: stoplisted unigrams and adjacent bigrams, lowercased.';

-- ── candidates: term × referent co-occurrence over the window ─────────────
-- Referents are handles (plan §3). Weight says how clean the signal is:
-- a pin or inline reference is a labelled choice (1.0); a governed metric the
-- turn actually used is strong (0.6); a source relation it read is weaker
-- (0.4). Outcome and the receipt's feedback signal scale each turn.
CREATE OR REPLACE FUNCTION rvbbit.term_binding_candidates(p_days int DEFAULT 60)
RETURNS TABLE (term_norm text, referent_key text, referent jsonb, mentions bigint, distinct_users bigint,
               distinct_threads bigint, weight numeric, dominance numeric, first_seen timestamptz,
               last_seen timestamptz, sample_turn_ids uuid[], users text[])
LANGUAGE sql STABLE AS $fn$
    WITH turns AS (
        SELECT t.id AS turn_id, t.session_id, lower(coalesce(t.author_email, '')) AS who,
               coalesce(t.completed_at, t.created_at) AS at,
               rvbbit.term_binding_terms(t.user_message) AS terms,
               t.object_refs,
               r.used_metrics, r.used_source_relations,
               CASE coalesce(r.outcome, 'complete') WHEN 'complete' THEN 1.0 WHEN 'success' THEN 1.0
                    WHEN 'partial' THEN 0.5 WHEN 'failed' THEN 0.2 WHEN 'running' THEN 0.3 ELSE 0.6 END
             * CASE r.feedback_signal WHEN 'affirmation' THEN 1.3 WHEN 'possible_correction' THEN 0.5
                    WHEN 'possible_contradiction' THEN 0.3 ELSE 1.0 END AS turn_factor
          FROM rvbbit.calliope_turns t
          LEFT JOIN rvbbit.business_context_turn_receipts r ON r.turn_id = t.id
         WHERE coalesce(t.completed_at, t.created_at) > now() - make_interval(days => greatest(p_days, 1))
           AND t.status IN ('complete', 'partial')
           AND length(coalesce(t.user_message, '')) BETWEEN 4 AND 2000
           AND coalesce(t.turn_kind, 'chat') IN ('chat', 'metric')
    ),
    refs AS (
        -- (a) explicit choices the person made in the composer: pins and inline objects
        SELECT tr.turn_id, tr.session_id, tr.who, tr.at, tr.terms,
               CASE o->>'kind' WHEN 'metric' THEN 'metric:' || (o->>'ref_id')
                               WHEN 'artifact' THEN 'artifact:' || (o->>'ref_id')
                               ELSE (o->>'kind') || ':' || (o->>'ref_id') END AS referent_key,
               CASE o->>'kind' WHEN 'metric' THEN jsonb_build_object('kind','metric','name', o->>'ref_id')
                               WHEN 'artifact' THEN jsonb_build_object('kind','artifact','slug', o->>'ref_id')
                               ELSE jsonb_build_object('kind', o->>'kind', 'ref_id', o->>'ref_id') END AS referent,
               1.0 * tr.turn_factor AS w,
               lower(replace(o->>'ref_id', '_', ' ')) AS own_name
          FROM turns tr
          CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(tr.object_refs) = 'array' THEN tr.object_refs ELSE '[]'::jsonb END) o
         WHERE (o->>'kind') IN ('metric','artifact','relation','project','thing') AND coalesce(o->>'ref_id','') <> ''
        UNION ALL
        -- (b) governed metrics the turn actually used
        SELECT tr.turn_id, tr.session_id, tr.who, tr.at, tr.terms,
               'metric:' || m.name, jsonb_build_object('kind','metric','name', m.name), 0.6 * tr.turn_factor,
               lower(replace(m.name, '_', ' '))
          FROM turns tr
          CROSS JOIN LATERAL (
              SELECT DISTINCT coalesce(nullif(u->>'governed_metric_name',''), nullif(u->>'metric_key','')) AS name
                FROM jsonb_array_elements(CASE WHEN jsonb_typeof(tr.used_metrics) = 'array' THEN tr.used_metrics ELSE '[]'::jsonb END) u) m
         WHERE m.name IS NOT NULL
        UNION ALL
        -- (c) source relations the turn read
        SELECT tr.turn_id, tr.session_id, tr.who, tr.at, tr.terms,
               'relation:' || rel, jsonb_build_object('kind','relation','schema', split_part(rel,'.',1), 'relation', split_part(rel,'.',2)),
               0.4 * tr.turn_factor,
               lower(replace(split_part(rel,'.',2), '_', ' '))
          FROM turns tr
          CROSS JOIN LATERAL unnest(coalesce(tr.used_source_relations, '{}'::text[])) AS rel
         WHERE rel LIKE '%.%'
    ),
    pairs AS (
        SELECT term, rf.referent_key, rf.referent, rf.turn_id, rf.session_id, rf.who, rf.at, rf.w
          FROM refs rf
          CROSS JOIN LATERAL unnest(rf.terms) AS term
         WHERE term <> rf.own_name                      -- saying the referent's own name teaches nothing
           AND term <> replace(rf.own_name, ' ', '')
    ),
    scored AS (
        SELECT term AS term_norm, referent_key, (array_agg(referent))[1] AS referent,
               count(DISTINCT turn_id) AS mentions,
               count(DISTINCT who) FILTER (WHERE who <> '') AS distinct_users,
               count(DISTINCT session_id) AS distinct_threads,
               round(sum(w)::numeric, 3) AS weight,
               min(at) AS first_seen, max(at) AS last_seen,
               (array_agg(DISTINCT turn_id))[1:5] AS sample_turn_ids,
               array_remove(array_agg(DISTINCT who), '') AS users
          FROM pairs
         GROUP BY term, referent_key
    )
    SELECT s.term_norm, s.referent_key, s.referent, s.mentions, s.distinct_users, s.distinct_threads, s.weight,
           round((s.weight / nullif(sum(s.weight) OVER (PARTITION BY s.term_norm), 0))::numeric, 3) AS dominance,
           s.first_seen, s.last_seen, s.sample_turn_ids, s.users
      FROM scored s
$fn$;
COMMENT ON FUNCTION rvbbit.term_binding_candidates(int) IS 'Miner working set: (term, referent) pairs from turn text × what those turns pinned, measured or read; dominance = this referent''s share of the term''s weight.';

-- ── the miner ──────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.term_binding_mine(
    p_days int DEFAULT 60, p_min_mentions int DEFAULT 5, p_min_users int DEFAULT 2,
    p_min_dominance numeric DEFAULT 0.70, p_noise_mentions int DEFAULT 3, p_apply boolean DEFAULT false)
RETURNS TABLE (term_norm text, referent_key text, action text, detail jsonb)
LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    c record;
    v_live record;
    v_conf numeric;
    v_evidence jsonb;
    v_finding record;
    v_seen text[] := '{}';
BEGIN
    FOR c IN SELECT * FROM rvbbit.term_binding_candidates(p_days) WHERE mentions >= p_noise_mentions LOOP
        v_conf := round(least(0.95, coalesce(c.dominance, 0) * least(1.0, c.mentions / 10.0))::numeric, 3);
        v_evidence := jsonb_build_object(
            'mentions', c.mentions, 'distinct_users', c.distinct_users, 'distinct_threads', c.distinct_threads,
            'dominance', c.dominance, 'weight', c.weight, 'window_days', p_days,
            'first_seen', c.first_seen, 'last_seen', c.last_seen,
            'sample_turn_ids', to_jsonb(c.sample_turn_ids), 'users', to_jsonb(c.users), 'mined_at', now());
        SELECT * INTO v_live FROM rvbbit.calliope_term_bindings b
         WHERE b.term_norm = c.term_norm AND b.scope = 'company' AND b.invalidated_at IS NULL;
        -- A person's word or an affirmed proposal owns the term: the miner never competes with it.
        IF FOUND AND v_live.status IN ('stipulated', 'affirmed') THEN
            term_norm := c.term_norm; referent_key := c.referent_key; action := 'held_by_' || v_live.status;
            detail := jsonb_build_object('referent', v_live.referent); RETURN NEXT; CONTINUE;
        END IF;
        v_seen := v_seen || (c.term_norm || '|' || c.referent_key);
        -- observed tier: ranking prior only; one live row per term, so only the dominant referent may hold it
        IF c.dominance >= 0.5 THEN
            IF FOUND AND v_live.status = 'observed' THEN
                IF v_live.referent::text IS DISTINCT FROM c.referent::text THEN
                    IF p_apply THEN
                        UPDATE rvbbit.calliope_term_bindings SET invalidated_at = now(), updated_at = now(),
                               invalidated_reason = 'miner: dominant referent changed to ' || c.referent_key
                         WHERE id = v_live.id;
                        INSERT INTO rvbbit.calliope_term_bindings (term_norm, scope, referent, status, confidence, evidence)
                        VALUES (c.term_norm, 'company', c.referent, 'observed', v_conf, v_evidence);
                    END IF;
                    action := 'observed_replaced';
                ELSE
                    IF p_apply THEN
                        UPDATE rvbbit.calliope_term_bindings SET confidence = v_conf, evidence = v_evidence, updated_at = now()
                         WHERE id = v_live.id;
                    END IF;
                    action := 'observed_refreshed';
                END IF;
            ELSIF NOT FOUND THEN
                IF p_apply THEN
                    INSERT INTO rvbbit.calliope_term_bindings (term_norm, scope, referent, status, confidence, evidence)
                    VALUES (c.term_norm, 'company', c.referent, 'observed', v_conf, v_evidence);
                END IF;
                action := 'observed';
            END IF;
            term_norm := c.term_norm; referent_key := c.referent_key;
            detail := jsonb_build_object('confidence', v_conf, 'mentions', c.mentions, 'dominance', c.dominance);
            RETURN NEXT;
        END IF;
        -- proposal: thresholds crossed, and nobody has decided this pair recently
        IF c.mentions >= p_min_mentions AND c.distinct_users >= p_min_users AND c.dominance >= p_min_dominance THEN
            SELECT * INTO v_finding FROM rvbbit.calliope_term_binding_findings f
             WHERE f.term_norm = c.term_norm AND f.scope = 'company' AND f.referent_key = c.referent_key
             ORDER BY (f.resolved_at IS NULL) DESC, f.updated_at DESC LIMIT 1;
            IF FOUND AND v_finding.resolved_at IS NULL THEN
                IF p_apply THEN
                    UPDATE rvbbit.calliope_term_binding_findings SET evidence = v_evidence, updated_at = now()
                     WHERE id = v_finding.id;
                END IF;
                action := 'proposal_pending';
            ELSIF FOUND AND v_finding.verdict = 'declined' AND v_finding.resolved_at > now() - interval '90 days' THEN
                action := 'proposal_suppressed';
            ELSE
                IF p_apply THEN
                    INSERT INTO rvbbit.calliope_term_binding_findings (term_norm, scope, referent_key, referent, verdict, evidence, owner_email)
                    VALUES (c.term_norm, 'company', c.referent_key, c.referent, 'proposed', v_evidence,
                            (SELECT u FROM unnest(c.users) u GROUP BY u ORDER BY count(*) DESC, u LIMIT 1));
                END IF;
                action := 'proposed';
            END IF;
            term_norm := c.term_norm; referent_key := c.referent_key;
            detail := jsonb_build_object('mentions', c.mentions, 'users', c.distinct_users, 'dominance', c.dominance);
            RETURN NEXT;
        END IF;
    END LOOP;
    -- observed rows whose evidence faded below the noise floor no longer earn a prior
    FOR v_live IN
        SELECT b.* FROM rvbbit.calliope_term_bindings b
         WHERE b.status = 'observed' AND b.invalidated_at IS NULL AND b.scope = 'company'
           AND NOT ((b.term_norm || '|' || rvbbit._term_binding_referent_key(b.referent)) = ANY (v_seen))
    LOOP
        IF p_apply THEN
            UPDATE rvbbit.calliope_term_bindings SET invalidated_at = now(), updated_at = now(),
                   invalidated_reason = 'miner: evidence faded below the noise floor'
             WHERE id = v_live.id;
        END IF;
        term_norm := v_live.term_norm; referent_key := rvbbit._term_binding_referent_key(v_live.referent);
        action := 'observed_faded'; detail := '{}'::jsonb; RETURN NEXT;
    END LOOP;
    -- open proposals the window no longer supports expire quietly
    IF p_apply THEN
        UPDATE rvbbit.calliope_term_binding_findings f
           SET verdict = 'expired', resolved_at = now(), resolution = 'evidence faded', updated_at = now()
         WHERE f.resolved_at IS NULL AND f.verdict = 'proposed' AND f.decision_id IS NULL
           AND NOT ((f.term_norm || '|' || f.referent_key) = ANY (v_seen));
    END IF;
END $fn$;
COMMENT ON FUNCTION rvbbit.term_binding_mine(int,int,int,numeric,int,boolean) IS
  'Nightly miner: writes observed bindings above the noise floor, records proposals at the plan''s thresholds, never touches stipulated/affirmed rows.';

CREATE OR REPLACE FUNCTION rvbbit._term_binding_referent_key(p_referent jsonb)
RETURNS text LANGUAGE sql IMMUTABLE AS $fn$
    SELECT CASE p_referent->>'kind'
             WHEN 'metric' THEN 'metric:' || (p_referent->>'name')
             WHEN 'artifact' THEN 'artifact:' || (p_referent->>'slug')
             WHEN 'relation' THEN 'relation:' || (p_referent->>'schema') || '.' || (p_referent->>'relation')
             ELSE coalesce(p_referent->>'kind','?') || ':' || coalesce(p_referent->>'ref_id', p_referent->>'name', p_referent->>'slug', '') END
$fn$;

-- ── the court's two verbs ──────────────────────────────────────────────────
-- Approve: the open proposal becomes an affirmed binding (replacing any
-- observed row for the term), receipted with who approved it.
CREATE OR REPLACE FUNCTION rvbbit.term_binding_affirm(p_finding_id uuid, p_by text, p_note text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE f record; g text;
BEGIN
    SELECT * INTO f FROM rvbbit.calliope_term_binding_findings WHERE id = p_finding_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('status', 'not_found'); END IF;
    IF f.resolved_at IS NOT NULL THEN RETURN jsonb_build_object('status', 'already_' || f.verdict); END IF;
    UPDATE rvbbit.calliope_term_bindings SET invalidated_at = now(), updated_at = now(),
           invalidated_reason = 'superseded: affirmed by ' || coalesce(p_by, 'operator')
     WHERE term_norm = f.term_norm AND scope = f.scope AND invalidated_at IS NULL AND status <> 'stipulated';
    IF EXISTS (SELECT 1 FROM rvbbit.calliope_term_bindings WHERE term_norm = f.term_norm AND scope = f.scope AND invalidated_at IS NULL) THEN
        UPDATE rvbbit.calliope_term_binding_findings SET verdict = 'declined', resolved_at = now(), updated_at = now(),
               resolution = 'a stipulated binding already holds this term', actor = p_by WHERE id = f.id;
        RETURN jsonb_build_object('status', 'held_by_stipulated');
    END IF;
    g := coalesce(nullif(p_note, ''), format('"%s" means %s here (affirmed from use: %s mentions by %s people).',
              f.term_norm, f.referent_key, f.evidence->>'mentions', f.evidence->>'distinct_users'));
    INSERT INTO rvbbit.calliope_term_bindings (term_norm, scope, referent, status, confidence, gloss, evidence, taught_by)
    VALUES (f.term_norm, f.scope, f.referent, 'affirmed', (f.evidence->>'dominance')::numeric, g,
            f.evidence || jsonb_build_object('affirmed_by', p_by, 'affirmed_at', now()), p_by);
    UPDATE rvbbit.calliope_term_binding_findings SET verdict = 'affirmed', resolved_at = now(), updated_at = now(),
           resolution = 'approved', actor = p_by WHERE id = f.id;
    RETURN jsonb_build_object('status', 'affirmed', 'term', f.term_norm, 'referent', f.referent);
END $fn$;

-- Set aside: the miner stops asking about this pair for 90 days; any observed
-- row for the term is invalidated so nothing keeps nudging the ranking.
CREATE OR REPLACE FUNCTION rvbbit.term_binding_decline(p_finding_id uuid, p_by text, p_note text DEFAULT NULL)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE f record;
BEGIN
    SELECT * INTO f FROM rvbbit.calliope_term_binding_findings WHERE id = p_finding_id;
    IF NOT FOUND THEN RETURN jsonb_build_object('status', 'not_found'); END IF;
    IF f.resolved_at IS NOT NULL THEN RETURN jsonb_build_object('status', 'already_' || f.verdict); END IF;
    UPDATE rvbbit.calliope_term_bindings SET invalidated_at = now(), updated_at = now(),
           invalidated_reason = 'set aside by ' || coalesce(p_by, 'operator') || coalesce(': ' || nullif(p_note,''), '')
     WHERE term_norm = f.term_norm AND scope = f.scope AND invalidated_at IS NULL AND status = 'observed'
       AND rvbbit._term_binding_referent_key(referent) = f.referent_key;
    UPDATE rvbbit.calliope_term_binding_findings SET verdict = 'declined', resolved_at = now(), updated_at = now(),
           resolution = coalesce(nullif(p_note,''), 'set aside'), actor = p_by WHERE id = f.id;
    RETURN jsonb_build_object('status', 'declined', 'term', f.term_norm);
END $fn$;

-- ── the label promoter had never been called; its first caller found the bug ──
-- `WHERE sessions >= p_min_sessions` collided with the function's own RETURNS
-- TABLE column `sessions`, so plpgsql refused it as ambiguous the first time
-- the tick actually ran it. Same body as 0449, source aliased.
CREATE OR REPLACE FUNCTION rvbbit.meaning_enrich_promote_by_use(
    p_min_sessions int DEFAULT 2, p_days int DEFAULT 30, p_apply boolean DEFAULT false,
    p_budget_usd numeric DEFAULT 2.00)
RETURNS TABLE(enrichment_id bigint, view_name text, sessions bigint, action text, detail jsonb)
LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE u record; v_run jsonb;
BEGIN
    FOR u IN SELECT x.* FROM rvbbit.meaning_enrich_usage(p_days) x WHERE x.sessions >= p_min_sessions LOOP
        IF NOT EXISTS (SELECT 1 FROM rvbbit.meaning_enrichments e WHERE e.id = u.enrichment_id AND e.status = 'published') THEN
            CONTINUE;
        END IF;
        IF NOT p_apply THEN
            RETURN QUERY SELECT u.enrichment_id, u.view_name, u.sessions, 'would_promote'::text, '{}'::jsonb;
            CONTINUE;
        END IF;
        UPDATE rvbbit.meaning_enrichments e SET status = 'promoted', updated_at = now() WHERE e.id = u.enrichment_id;
        v_run := rvbbit.meaning_enrich_materialize(u.enrichment_id, 1000000, p_budget_usd);
        PERFORM rvbbit.meaning_enrich_publish(u.enrichment_id);
        PERFORM rvbbit._meaning_enrich_log('enrich_promoted', NULL,
                    jsonb_build_object('id', u.enrichment_id, 'sessions', u.sessions, 'run', v_run));
        RETURN QUERY SELECT u.enrichment_id, u.view_name, u.sessions, 'promoted'::text, v_run;
    END LOOP;
END $fn$;

-- ── taught facts are not tweets: a caveat may run to a paragraph ────────────
-- teach_meaning cut caveat text and succession notes at 600 characters (0443).
-- A finding a person dictates in chat is often longer than that, and losing
-- its tail silently is worse than any length. Same body as 0443; caps raised
-- to 4000 (caveat) and 2000 (succession note). The server side raised its
-- reason cap to 4000 in the same change.
CREATE OR REPLACE FUNCTION rvbbit.meaning_state(p_relation text, p_field text, p_value jsonb, p_reason text DEFAULT NULL, p_col text DEFAULT NULL, p_actor text DEFAULT NULL)
RETURNS bigint LANGUAGE plpgsql AS $fn$
DECLARE v_id bigint; v_sup text;
BEGIN
    IF p_field NOT IN ('kind', 'business_name', 'what', 'domain_key', 'ignore', 'role', 'ref_target', 'pii', 'succession', 'caveat') THEN
        RAISE EXCEPTION 'meaning_state: unknown field %', p_field;
    END IF;
    IF p_field = 'succession' THEN
        IF p_col IS NOT NULL THEN RAISE EXCEPTION 'meaning_state: succession is a relation-level statement'; END IF;
        IF jsonb_typeof(p_value) <> 'object' OR jsonb_typeof(p_value->'supersedes') <> 'array' OR jsonb_array_length(p_value->'supersedes') = 0 THEN
            RAISE EXCEPTION 'meaning_state: succession needs {supersedes: [schema.table, …], effective_from: YYYY-MM-DD, federation?: switch|union|mapped, note?}';
        END IF;
        IF p_value->>'effective_from' IS NULL OR (p_value->>'effective_from')::date IS NULL THEN
            RAISE EXCEPTION 'meaning_state: succession needs effective_from as a date';
        END IF;
        IF coalesce(p_value->>'federation', 'switch') NOT IN ('switch', 'union', 'mapped') THEN
            RAISE EXCEPTION 'meaning_state: federation must be switch, union or mapped';
        END IF;
        FOR v_sup IN SELECT lower(x) FROM jsonb_array_elements_text(p_value->'supersedes') x LOOP
            IF v_sup = lower(p_relation) THEN RAISE EXCEPTION 'meaning_state: a relation cannot supersede itself'; END IF;
        END LOOP;
        p_value := jsonb_strip_nulls(jsonb_build_object(
            'supersedes', (SELECT jsonb_agg(lower(x)) FROM jsonb_array_elements_text(p_value->'supersedes') x),
            'effective_from', (p_value->>'effective_from')::date,
            'federation', coalesce(p_value->>'federation', 'switch'),
            'note', nullif(left(p_value->>'note', 2000), '')));
    ELSIF p_field = 'caveat' THEN
        IF jsonb_typeof(p_value) = 'string' THEN p_value := jsonb_build_object('text', p_value #>> '{}'); END IF;
        IF jsonb_typeof(p_value) <> 'object' OR nullif(trim(p_value->>'text'), '') IS NULL THEN
            RAISE EXCEPTION 'meaning_state: caveat needs {text, as_of?: YYYY-MM-DD}';
        END IF;
        p_value := jsonb_strip_nulls(jsonb_build_object('text', left(trim(p_value->>'text'), 4000),
                                                        'as_of', CASE WHEN p_value->>'as_of' IS NOT NULL THEN (p_value->>'as_of')::date END));
    END IF;
    -- caveats accumulate (each is its own statement); every other field replaces its predecessor
    IF p_field <> 'caveat' THEN
        UPDATE rvbbit.meaning_overrides SET retired_at = now()
         WHERE relation = lower(p_relation) AND col_name IS NOT DISTINCT FROM p_col AND field = p_field AND retired_at IS NULL;
    END IF;
    INSERT INTO rvbbit.meaning_overrides (relation, col_name, field, value, reason, actor)
    VALUES (lower(p_relation), p_col, p_field, p_value, p_reason, coalesce(p_actor, current_user)) RETURNING override_id INTO v_id;
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

-- Retire one caveat by its override id (caveats accumulate, so they retire one at a time).

-- ── the daily tick learns to promote labels and to mine, with a bar that fits the team ──
DROP FUNCTION IF EXISTS rvbbit.meaning_desire_path_tick(int, int);
CREATE OR REPLACE FUNCTION rvbbit.meaning_desire_path_tick(
    p_min_sessions int DEFAULT 3, p_prune_days int DEFAULT 60, p_label_budget_usd numeric DEFAULT 2.00)
RETURNS jsonb LANGUAGE plpgsql VOLATILE AS $fn$
DECLARE
    v_reg jsonb; v_prom jsonb; v_prune jsonb; v_labels jsonb; v_mine jsonb; v_out jsonb;
    v_threads bigint; v_bar int; v_label_bar int;
BEGIN
    -- The bar scales with how many threads there were to use anything in: a
    -- three-thread bar on a four-thread month promotes nothing forever.
    SELECT count(DISTINCT coalesce(sr.source_session_id::text, cs.id::text, m.session_id)) INTO v_threads
      FROM rvbbit.hermes_messages m
      LEFT JOIN LATERAL (SELECT x.source_session_id FROM rvbbit.calliope_specialist_runs x WHERE x.hermes_session_id = m.session_id LIMIT 1) sr ON true
      LEFT JOIN LATERAL (SELECT x.id FROM rvbbit.calliope_sessions x WHERE x.hermes_session_id = m.session_id LIMIT 1) cs ON true
     WHERE m.ts > now() - interval '30 days' AND m.tool_calls IS NOT NULL;
    v_bar := greatest(2, least(p_min_sessions, ceil(coalesce(v_threads, 0) * 0.25)::int));
    v_label_bar := greatest(2, least(2, v_bar));
    SELECT coalesce(jsonb_object_agg(a, n), '{}'::jsonb) INTO v_reg
      FROM (SELECT action AS a, count(*) AS n FROM rvbbit.meaning_register_implied_metrics(NULL, true) GROUP BY 1) x;
    SELECT coalesce(jsonb_object_agg(a, n), '{}'::jsonb) INTO v_prom
      FROM (SELECT action AS a, count(*) AS n FROM rvbbit.meaning_promote_by_use(v_bar, 30, true) GROUP BY 1) x;
    SELECT coalesce(jsonb_object_agg(a, n), '{}'::jsonb) INTO v_prune
      FROM (SELECT action AS a, count(*) AS n FROM rvbbit.meaning_prune_implied(p_prune_days, true) GROUP BY 1) x;
    BEGIN
        SELECT coalesce(jsonb_object_agg(a, n), '{}'::jsonb) INTO v_labels
          FROM (SELECT action AS a, count(*) AS n FROM rvbbit.meaning_enrich_promote_by_use(v_label_bar, 30, true, p_label_budget_usd) GROUP BY 1) x;
    EXCEPTION WHEN OTHERS THEN
        v_labels := jsonb_build_object('error', left(SQLERRM, 200));
    END;
    BEGIN
        SELECT coalesce(jsonb_object_agg(a, n), '{}'::jsonb) INTO v_mine
          FROM (SELECT action AS a, count(*) AS n FROM rvbbit.term_binding_mine(60, 5, 2, 0.70, 3, true) GROUP BY 1) x;
    EXCEPTION WHEN OTHERS THEN
        v_mine := jsonb_build_object('error', left(SQLERRM, 200));
    END;
    v_out := jsonb_build_object('registered', v_reg, 'promoted', v_prom, 'retired', v_prune,
                                'labels', v_labels, 'terms', v_mine,
                                'bar', jsonb_build_object('threads_30d', v_threads, 'metric_sessions', v_bar, 'label_sessions', v_label_bar),
                                'at', now());
    INSERT INTO rvbbit.meaning_desire_path_log (action, detail) VALUES ('tick', v_out);
    RETURN v_out;
END $fn$;
COMMENT ON FUNCTION rvbbit.meaning_desire_path_tick(int, int, numeric) IS
  'Daily: register dossier candidates as implied metrics, promote what is used (bar scales with active threads, floor 2), promote used label dimensions, mine term bindings, retire the silent. Every step logs to meaning_desire_path_log.';

-- ── the narrative queue hands the worker the court's paperwork ─────────────
-- A `ledger` item appears when proposals await their Awaiting You card or an
-- answered card awaits its verb. It costs no model budget.
DO $$
BEGIN
    IF to_regprocedure('rvbbit.meaning_narrative_queue_enrich_base(int)') IS NULL THEN
        EXECUTE 'ALTER FUNCTION rvbbit.meaning_narrative_queue(int) RENAME TO meaning_narrative_queue_enrich_base';
    END IF;
END $$;
CREATE OR REPLACE FUNCTION rvbbit.meaning_narrative_queue(p_max int DEFAULT 20)
RETURNS TABLE (kind text, key text, schema_name text, members text[], member_fingerprint text, reason text, priority numeric)
LANGUAGE sql STABLE AS $fn$
    SELECT q.kind, q.key, q.schema_name, q.members, q.member_fingerprint, q.reason, q.priority
      FROM (
        SELECT * FROM rvbbit.meaning_narrative_queue_enrich_base(p_max)
        UNION ALL
        SELECT 'ledger'::text, 'term-bindings', NULL::text, '{}'::text[],
               md5(count(*)::text || max(f.updated_at)::text), 'court', 1000::numeric
          FROM rvbbit.calliope_term_binding_findings f
          LEFT JOIN rvbbit.calliope_decision_requests d ON d.id = f.decision_id
         WHERE f.resolved_at IS NULL AND f.verdict = 'proposed'
           AND (f.decision_id IS NULL OR d.status IN ('answered'))
        HAVING count(*) > 0
      ) q
     ORDER BY CASE q.kind WHEN 'ledger' THEN -1 WHEN 'recognition' THEN 0 WHEN 'map' THEN 1 WHEN 'dossier' THEN 2 ELSE 3 END,
              q.priority DESC NULLS LAST
     LIMIT p_max
$fn$;

-- ── status for DataRabbit and the curious ──────────────────────────────────
CREATE OR REPLACE FUNCTION rvbbit.term_binding_status()
RETURNS jsonb LANGUAGE sql STABLE AS $fn$
    SELECT jsonb_build_object(
        'bindings', (SELECT jsonb_object_agg(status, n) FROM (SELECT status, count(*) AS n FROM rvbbit.calliope_term_bindings WHERE invalidated_at IS NULL GROUP BY 1) s),
        'findings', (SELECT jsonb_object_agg(verdict, n) FROM (SELECT verdict, count(*) AS n FROM rvbbit.calliope_term_binding_findings GROUP BY 1) s),
        'open_proposals', (SELECT count(*) FROM rvbbit.calliope_term_binding_findings WHERE resolved_at IS NULL),
        'awaiting_card', (SELECT count(*) FROM rvbbit.calliope_term_binding_findings WHERE resolved_at IS NULL AND decision_id IS NULL),
        'last_mine', (SELECT max((detail->'terms')::text) FROM rvbbit.meaning_desire_path_log WHERE action = 'tick' AND detail ? 'terms'))
$fn$;
