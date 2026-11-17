-- 0462: A metric is scalar. A breakdown is a named, purposeful grouped analysis.
-- Preserve raw model output and every old publication; append a corrected publication.
ALTER TABLE rvbbit.meaning_domains ADD COLUMN IF NOT EXISTS candidate_breakdowns jsonb NOT NULL DEFAULT '[]';

CREATE OR REPLACE FUNCTION rvbbit.meaning_classify_ideas(p_metrics jsonb, p_breakdowns jsonb)
RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $fn$
DECLARE item jsonb; metrics jsonb := '[]'; breakdowns jsonb := '[]';
        grain text; formula text; grouped boolean; dimensions jsonb;
BEGIN
    -- New proposals declare their structure explicitly. Incomplete breakdowns
    -- never become a misleading metric merely because their structure is missing.
    FOR item IN SELECT * FROM jsonb_array_elements(rvbbit._jsonb_arr(p_breakdowns)) LOOP
        IF nullif(btrim(item->>'name'),'') IS NOT NULL
           AND nullif(btrim(item->>'purpose'),'') IS NOT NULL
           AND jsonb_array_length(rvbbit._jsonb_arr(item->'measures')) > 0
           AND jsonb_array_length(rvbbit._jsonb_arr(item->'dimensions')) > 0 THEN
            breakdowns := breakdowns || jsonb_build_array(item);
        END IF;
    END LOOP;
    FOR item IN SELECT * FROM jsonb_array_elements(rvbbit._jsonb_arr(p_metrics)) LOOP
        CONTINUE WHEN nullif(btrim(item->>'name'),'') IS NULL;
        grain := coalesce(item->>'grain',''); formula := coalesce(item->>'formula','');
        dimensions := rvbbit._jsonb_arr(item->'dimensions');
        -- Legacy prose often specifies grouping only in the name or grain.
        -- Optional slicing does not turn a scalar metric into a grouped result.
        grouped := jsonb_array_length(dimensions)>0
          OR formula ~* '\m(group(ed)? by|broken down by|split by)\M'
          OR coalesce(item->>'name','') ~* '\sby\s'
          OR (grain<>'' AND grain !~* '^(one (value|number)|scalar|selected scope|all |across rows|overall|whole |entire |total for)'
              AND grain !~* 'groupable by'
              AND (coalesce(item->>'why','') ~* '\m(compares?|comparison|across|at each|for each|distribution|over time)\M'
                   OR coalesce(item->>'name','') ~* '\mover time\M'));
        IF grouped THEN
            IF jsonb_array_length(dimensions)=0 THEN
                -- Keep the original grain intact (including alternative groupings
                -- and separate populations); do not guess a Cartesian product.
                dimensions := jsonb_build_array(coalesce(nullif(grain,''),
                    nullif(substring(formula from '(?i)(?:group(?:ed)? by|broken down by|split by)\s+(.+)'),''),
                    substring(item->>'name' from '(?i)\sby\s+(.+)')));
            END IF;
            IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(breakdowns) b WHERE b->>'name'=item->>'name') THEN
                breakdowns := breakdowns || jsonb_build_array(jsonb_build_object(
                    'name',item->>'name',
                    'purpose',coalesce(nullif(item->>'why',''),'Compare ' || (item->>'name') || ' across the stated groups.'),
                    'measures',jsonb_build_array(formula), 'dimensions',dimensions, 'grain',grain,
                    'caveats','Reclassified from a metric idea; validate the calculation and grouping before saving.',
                    'legacy_metric_idea',item));
            END IF;
        ELSE
            IF grain ~* 'groupable by' THEN
                item := item || jsonb_build_object('grain','One value for the selected scope and period',
                                                   'previous_grain',grain);
            END IF;
            metrics := metrics || jsonb_build_array(item);
        END IF;
    END LOOP;
    RETURN jsonb_build_object('candidate_metrics',metrics,'candidate_breakdowns',breakdowns);
END $fn$;

-- Both domain and dossier writers already persist the complete model output.
-- Enforce the same contract for those writers and direct proposal updates.
CREATE OR REPLACE FUNCTION rvbbit.meaning_store_ideas() RETURNS trigger LANGUAGE plpgsql AS $fn$
DECLARE ideas jsonb; fresh_output boolean;
BEGIN
    fresh_output := TG_OP='INSERT';
    IF TG_OP='UPDATE' THEN fresh_output := NEW.output IS DISTINCT FROM OLD.output; END IF;
    IF fresh_output AND NEW.output ? 'candidate_metrics' THEN
        NEW.candidate_metrics := rvbbit._jsonb_arr(NEW.output->'candidate_metrics');
    END IF;
    IF fresh_output AND NEW.output ? 'candidate_breakdowns' THEN
        NEW.candidate_breakdowns := rvbbit._jsonb_arr(NEW.output->'candidate_breakdowns');
    END IF;
    ideas := rvbbit.meaning_classify_ideas(NEW.candidate_metrics,NEW.candidate_breakdowns);
    NEW.candidate_metrics := ideas->'candidate_metrics';
    NEW.candidate_breakdowns := ideas->'candidate_breakdowns';
    RETURN NEW;
END $fn$;
DROP TRIGGER IF EXISTS meaning_store_ideas ON rvbbit.meaning_domains;
CREATE TRIGGER meaning_store_ideas BEFORE INSERT OR UPDATE OF candidate_metrics,candidate_breakdowns,output
 ON rvbbit.meaning_domains FOR EACH ROW EXECUTE FUNCTION rvbbit.meaning_store_ideas();

-- The working dossier and the published dossier can differ during refresh.
-- Classify each independently, so a failed draft cannot replace published prose.
UPDATE rvbbit.meaning_domains SET candidate_metrics=candidate_metrics;
INSERT INTO rvbbit.meaning_domain_versions(domain_key,snapshot)
 SELECT domain_key,snapshot || ideas FROM (
   SELECT domain_key,snapshot,rvbbit.meaning_classify_ideas(snapshot->'candidate_metrics',snapshot->'candidate_breakdowns') AS ideas
   FROM (SELECT DISTINCT ON(domain_key) domain_key,snapshot
         FROM rvbbit.meaning_domain_versions ORDER BY domain_key,revision DESC) latest
 ) classified WHERE snapshot->'candidate_metrics' IS DISTINCT FROM ideas->'candidate_metrics'
 OR coalesce(snapshot->'candidate_breakdowns','[]') IS DISTINCT FROM ideas->'candidate_breakdowns';

CREATE OR REPLACE VIEW rvbbit.meaning_published_domains AS
 SELECT (jsonb_populate_record(NULL::rvbbit.meaning_domains,v.snapshot)).*
 FROM (SELECT DISTINCT ON(domain_key) domain_key,snapshot
       FROM rvbbit.meaning_domain_versions ORDER BY domain_key,revision DESC) v
 WHERE NOT EXISTS(SELECT 1 FROM rvbbit.meaning_subject_aliases a WHERE a.old_key=v.domain_key);

CREATE OR REPLACE FUNCTION rvbbit.meaning_tree(p_subject_key text, p_max_nouns int DEFAULT 40)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $fn$
DECLARE
    d        rvbbit.meaning_domains%ROWTYPE;
    v_nouns  text[];
    v_areas  jsonb := '[]'::jsonb;
    v_area   jsonb;
    v_placed text[] := '{}';
    v_in     text[];
    v_concepts jsonb;
    v_lenses jsonb;
BEGIN
    SELECT * INTO d FROM rvbbit.meaning_domains WHERE domain_key = p_subject_key;
    IF NOT FOUND THEN RETURN NULL; END IF;
    v_nouns := rvbbit.meaning_tree_nouns(p_subject_key, p_max_nouns);
    FOR v_area IN SELECT * FROM jsonb_array_elements(d.areas) LOOP
        SELECT coalesce(array_agg(t), '{}') INTO v_in
          FROM jsonb_array_elements_text(rvbbit._jsonb_arr(v_area->'tables')) t WHERE t = ANY(v_nouns) AND NOT (t = ANY(v_placed));
        CONTINUE WHEN cardinality(v_in) = 0;
        v_placed := v_placed || v_in;
        v_areas := v_areas || jsonb_build_object(
            'name', v_area->>'name', 'about', v_area->>'about',
            'nouns', (SELECT jsonb_agg(rvbbit.meaning_noun(r) ORDER BY array_position(v_nouns, r)) FROM unnest(v_in) r),
            'other_tables', (SELECT count(*) FROM jsonb_array_elements_text(rvbbit._jsonb_arr(v_area->'tables')) t WHERE NOT (t = ANY(v_in))));
    END LOOP;
    IF EXISTS (SELECT 1 FROM unnest(v_nouns) r WHERE NOT (r = ANY(v_placed))) THEN
        v_areas := v_areas || jsonb_build_object(
            'name', 'Other', 'nouns', (SELECT jsonb_agg(rvbbit.meaning_noun(r) ORDER BY array_position(v_nouns, r)) FROM unnest(v_nouns) r WHERE NOT (r = ANY(v_placed))));
    END IF;
    v_concepts := rvbbit.meaning_concepts(p_subject_key, p_max_nouns);
    v_lenses := rvbbit.meaning_subject_lenses(p_subject_key);
    RETURN jsonb_strip_nulls(jsonb_build_object(
        'subject', d.name, 'key', d.domain_key, 'about', d.about,
        'tables', cardinality(d.members), 'nouns', cardinality(v_nouns),
        'roles', (SELECT jsonb_object_agg(v, n) FROM (SELECT value AS v, count(*) AS n FROM jsonb_each_text(d.roles) GROUP BY 1) x),
        'questions', d.questions,
        'metrics', rvbbit.meaning_candidate_metrics(p_subject_key),
        'breakdowns', coalesce(d.candidate_breakdowns, '[]'::jsonb),
        'concepts', nullif(v_concepts, '[]'::jsonb),
        'folded', (SELECT sum((c->>'tables')::int) FROM jsonb_array_elements(v_concepts) c),
        'derived', nullif(rvbbit.meaning_subject_derived(p_subject_key, p_max_nouns), '[]'::jsonb),
        'lenses', nullif(v_lenses, '[]'::jsonb),
        'gaps', d.gaps,
        'usable', d.usable,
        'areas', v_areas));
END $fn$;
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
               'breakdowns', jsonb_array_length(rvbbit._jsonb_arr(d.candidate_breakdowns)),
               'updated_at', d.updated_at))
             ORDER BY cardinality(d.members) DESC), '[]'::jsonb)
      FROM rvbbit.meaning_published_domains d WHERE d.origin = 'map' AND d.status = 'done'
$fn$;
