-- 0458: Meaning accumulates. Published dossiers survive refreshes; maps only add.
-- Existing keys are durable identities. Explicit merges record aliases and re-home
-- associations; the model cannot delete/rename/reassign an existing subject.
CREATE TABLE IF NOT EXISTS rvbbit.meaning_domain_versions (
    revision bigserial PRIMARY KEY, domain_key text NOT NULL,
    published_at timestamptz NOT NULL DEFAULT now(), snapshot jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS meaning_domain_versions_key_idx
 ON rvbbit.meaning_domain_versions(domain_key,revision DESC);
CREATE TABLE IF NOT EXISTS rvbbit.meaning_subject_aliases (
    old_key text PRIMARY KEY, subject_key text NOT NULL, reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), CHECK(old_key<>subject_key)
);
CREATE TABLE IF NOT EXISTS rvbbit.meaning_map_sources (
    schema_name text PRIMARY KEY, map_id bigint, incorporated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO rvbbit.meaning_map_sources(schema_name,map_id)
 SELECT schema_name,(SELECT max(map_id) FROM rvbbit.meaning_company_map WHERE status='done')
 FROM rvbbit.meaning_recognitions WHERE status='done'
 AND EXISTS(SELECT 1 FROM rvbbit.meaning_domains WHERE origin='map') ON CONFLICT DO NOTHING;
ALTER TABLE rvbbit.meaning_domains ADD COLUMN IF NOT EXISTS narrative_fingerprint text;

CREATE OR REPLACE FUNCTION rvbbit.meaning_subject_fingerprint(p_key text)
RETURNS text LANGUAGE sql STABLE AS $fn$
 SELECT md5(jsonb_build_object(
   'tables',(SELECT jsonb_agg(jsonb_build_object('table',m.relation,'name',m.business_name,'what',m.what,'narrative',m.narrative,
      'columns',(SELECT jsonb_agg(jsonb_build_array(c.col_name,c.data_type,c.basis) ORDER BY c.col_name)
       FROM rvbbit.meaning_columns c WHERE c.relation=m.relation)) ORDER BY m.relation)
      FROM rvbbit.meaning_relations m WHERE m.relation=ANY(d.members)),
   'labels',(SELECT jsonb_agg(jsonb_build_array(e.relation,e.name,e.question,e.columns) ORDER BY e.relation,e.name) FROM rvbbit.meaning_enrichments e WHERE e.subject_key=d.domain_key AND e.status IN ('published','promoted')),
   'stated',(SELECT jsonb_agg(jsonb_build_array(o.relation,o.col_name,o.field,o.value)
       ORDER BY o.relation,o.col_name,o.field,o.override_id)
       FROM rvbbit.meaning_overrides o WHERE o.relation=ANY(d.members) AND o.retired_at IS NULL)
 )::text) FROM rvbbit.meaning_domains d WHERE d.domain_key=p_key
$fn$;
UPDATE rvbbit.meaning_domains SET narrative_fingerprint=rvbbit.meaning_subject_fingerprint(domain_key)
 WHERE narrative_fingerprint IS NULL AND status='done';

CREATE OR REPLACE FUNCTION rvbbit.meaning_publish_subject(p_key text)
RETURNS bigint LANGUAGE plpgsql AS $fn$
DECLARE d rvbbit.meaning_domains%ROWTYPE; v bigint;
BEGIN
 SELECT * INTO d FROM rvbbit.meaning_domains WHERE domain_key=p_key FOR UPDATE;
 IF NOT FOUND OR d.status<>'done' OR NOT d.usable THEN
   RAISE EXCEPTION 'Subject % has not passed publication review',p_key;
 END IF;
 INSERT INTO rvbbit.meaning_domain_versions(domain_key,snapshot) VALUES(p_key,to_jsonb(d)) RETURNING revision INTO v;
 RETURN v;
END $fn$;
-- Preserve the currently visible baseline, including any older provisional areas.
INSERT INTO rvbbit.meaning_domain_versions(domain_key,snapshot)
 SELECT d.domain_key,to_jsonb(d) FROM rvbbit.meaning_domains d
 WHERE origin='map' AND status='done'
 AND NOT EXISTS(SELECT 1 FROM rvbbit.meaning_domain_versions v WHERE v.domain_key=d.domain_key);
CREATE OR REPLACE VIEW rvbbit.meaning_published_domains AS
 SELECT (jsonb_populate_record(NULL::rvbbit.meaning_domains,v.snapshot)).*
 FROM (SELECT DISTINCT ON(domain_key) domain_key,snapshot
       FROM rvbbit.meaning_domain_versions ORDER BY domain_key,revision DESC) v
 WHERE NOT EXISTS(SELECT 1 FROM rvbbit.meaning_subject_aliases a WHERE a.old_key=v.domain_key);

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
      FROM rvbbit.meaning_published_domains d WHERE d.origin = 'map' AND d.status = 'done'
$fn$;

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
    PERFORM pg_advisory_xact_lock(hashtext('rvbbit.meaning.publication'));
    IF jsonb_array_length(rvbbit._jsonb_arr(p_output->'subjects'))=0 THEN
        RAISE EXCEPTION 'A map must contain subjects';
    END IF;
    FOR v_subj IN SELECT * FROM jsonb_array_elements(rvbbit._jsonb_arr(p_output->'subjects')) LOOP
        v_key := 'subject:' || regexp_replace(lower(coalesce(v_subj->>'key', v_subj->>'name', 'subject')), '[^a-z0-9]+', '_', 'g');
        CONTINUE WHEN EXISTS(SELECT 1 FROM rvbbit.meaning_subject_aliases WHERE old_key=v_key);
        CONTINUE WHEN v_key = ANY(v_seen) OR nullif(btrim(v_subj->>'name'), '') IS NULL;
        v_seen := array_append(v_seen, v_key);
        v_members := '{}'; v_core := '{}'; v_roles := '{}'::jsonb;
        FOR v_member IN SELECT * FROM jsonb_array_elements(rvbbit._jsonb_arr(v_subj->'members')) LOOP
            v_rel := lower(coalesce(v_member->>'table', v_member #>> '{}', ''));
            CONTINUE WHEN v_rel = '' OR position('.' IN v_rel) = 0;
            CONTINUE WHEN NOT EXISTS (SELECT 1 FROM rvbbit.meaning_relations m WHERE m.relation = v_rel AND m.status = 'judged');
            CONTINUE WHEN v_rel = ANY(v_members);
            -- Existing assignments, including stated ones, are never stolen by a map.
            CONTINUE WHEN EXISTS(SELECT 1 FROM rvbbit.meaning_relations m WHERE m.relation=v_rel
              AND (m.domain_key IS NOT NULL OR m.placed_by='override'
                   OR rvbbit.meaning_overrides_for(m.relation,NULL) ? 'domain_key'));

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
        CONTINUE WHEN cardinality(v_members)=0;
        INSERT INTO rvbbit.meaning_domains (domain_key, schema_name, source_system, name, about, questions, members, core, roles, member_fingerprint,
                                            origin, map_id, status, usable, attempts, updated_at)
        VALUES (v_key, coalesce(split_part(v_members[1], '.', 1), 'company'), 'company',
                left(btrim(v_subj->>'name'), 120), left(btrim(v_subj->>'about'), 1200),
                rvbbit._jsonb_arr(v_subj->'questions'), v_members, v_core, v_roles,
                md5(array_to_string(v_members, ',')), 'map', p_map_id, 'placed', false, 0, now())
        ON CONFLICT(domain_key) DO UPDATE SET
          members=rvbbit.meaning_domains.members || EXCLUDED.members,
          roles=rvbbit.meaning_domains.roles || EXCLUDED.roles,
          updated_at=now(); -- existing dossier, key, name and publication remain intact

        v_n_subj := v_n_subj + 1;
    END LOOP;
    UPDATE rvbbit.meaning_company_map SET subjects = v_n_subj, placed = v_placed WHERE map_id = p_map_id;
    INSERT INTO rvbbit.meaning_map_sources(schema_name,map_id)
      SELECT src->>'schema',p_map_id FROM rvbbit.meaning_company_map cm,
        LATERAL jsonb_array_elements(rvbbit._jsonb_arr(cm.packet->'sources')) src
      WHERE cm.map_id=p_map_id AND src->>'schema' IS NOT NULL
      ON CONFLICT(schema_name) DO NOTHING;
    RETURN jsonb_build_object('subjects', v_n_subj, 'placed', v_placed);
END $fn$;


CREATE OR REPLACE FUNCTION rvbbit.meaning_narrative_queue_base(p_max int DEFAULT 20)
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
               CASE WHEN NOT EXISTS (SELECT 1 FROM rvbbit.meaning_company_map WHERE status = 'done') THEN 'new' ELSE 'new_source' END AS reason,
               1e9::numeric AS priority
         WHERE NOT EXISTS (SELECT 1 FROM recog)
           AND EXISTS (SELECT 1 FROM rvbbit.meaning_recognitions r WHERE r.status='done'
             AND NOT EXISTS(SELECT 1 FROM rvbbit.meaning_map_sources s WHERE s.schema_name=r.schema_name))
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
                OR (d.status = 'done' AND d.narrative_fingerprint IS DISTINCT FROM rvbbit.meaning_subject_fingerprint(d.domain_key) AND d.dossier_at < now() - interval '6 hours'))
    )
    SELECT * FROM (SELECT * FROM recog UNION ALL SELECT * FROM map UNION ALL SELECT * FROM dossiers) q
     ORDER BY CASE q.kind WHEN 'recognition' THEN 0 WHEN 'map' THEN 1 ELSE 2 END, q.priority DESC NULLS LAST
     LIMIT p_max;
END $fn$;


CREATE OR REPLACE FUNCTION rvbbit.meaning_reconcile_subjects() RETURNS jsonb
LANGUAGE sql AS $fn$ SELECT jsonb_build_object('folded',0,'policy','explicit_merge_required') $fn$;

-- A known orphan can be repaired without guessing its successor: follow the
-- current placement of its own relation. Sentinels and ambiguous/unplaced rows
-- stay for review rather than being assigned by fuzzy name similarity.
CREATE TABLE IF NOT EXISTS rvbbit.meaning_association_repairs (
 id bigserial PRIMARY KEY,enrichment_id bigint,old_key text,new_key text,
 reason text NOT NULL,created_at timestamptz NOT NULL DEFAULT now()
);
WITH repair AS (
 SELECT e.id,e.subject_key AS old_key,m.domain_key AS new_key
 FROM rvbbit.meaning_enrichments e JOIN rvbbit.meaning_relations m ON m.relation=e.relation
 JOIN rvbbit.meaning_domains d ON d.domain_key=m.domain_key
 WHERE NOT EXISTS(SELECT 1 FROM rvbbit.meaning_domains old WHERE old.domain_key=e.subject_key)
), logged AS (
 INSERT INTO rvbbit.meaning_association_repairs(enrichment_id,old_key,new_key,reason)
 SELECT id,old_key,new_key,'Followed current placement of enrichment relation' FROM repair
 RETURNING enrichment_id,new_key
)
UPDATE rvbbit.meaning_enrichments e SET subject_key=l.new_key FROM logged l WHERE e.id=l.enrichment_id;

-- Append-only per-attempt accounting. Reservations are durable, so crashes do
-- not erase costs or free a budget reservation for an unknown provider outcome.
CREATE TABLE IF NOT EXISTS rvbbit.meaning_model_calls (
 event_id bigserial PRIMARY KEY, call_id uuid NOT NULL, event text NOT NULL
 CHECK(event IN ('reserved','charged','failed','legacy')),
 kind text NOT NULL, work_key text NOT NULL, reason text NOT NULL,
 model text, tokens_in bigint, tokens_out bigint,
 cost_usd numeric NOT NULL CHECK(cost_usd>=0), created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(call_id,event)
);
CREATE INDEX IF NOT EXISTS meaning_model_calls_time_idx ON rvbbit.meaning_model_calls(created_at);
CREATE OR REPLACE FUNCTION rvbbit.meaning_model_spend(p_window interval DEFAULT interval '24 hours')
RETURNS numeric LANGUAGE sql STABLE AS $fn$
 SELECT coalesce(sum(cost_usd),0) FROM (
   SELECT DISTINCT ON(call_id) cost_usd FROM rvbbit.meaning_model_calls
   WHERE created_at>now()-p_window ORDER BY call_id,event_id DESC
 ) x
$fn$;
CREATE OR REPLACE FUNCTION rvbbit.meaning_narrative_spend(p_window interval DEFAULT interval '24 hours')
RETURNS numeric LANGUAGE sql STABLE AS $fn$ SELECT rvbbit.meaning_model_spend(p_window) $fn$;
CREATE OR REPLACE FUNCTION rvbbit.meaning_reserve_model_call(
 p_kind text,p_key text,p_reason text,p_model text,p_budget numeric,p_reserve numeric)
RETURNS uuid LANGUAGE plpgsql AS $fn$
DECLARE v uuid:=gen_random_uuid();
BEGIN
 PERFORM pg_advisory_xact_lock(hashtext('rvbbit.meaning.model_budget'));
 IF p_reserve<=0 OR p_budget<=0 OR rvbbit.meaning_model_spend()+p_reserve>p_budget THEN RETURN NULL; END IF;
 INSERT INTO rvbbit.meaning_model_calls(call_id,event,kind,work_key,reason,model,cost_usd)
 VALUES(v,'reserved',p_kind,p_key,p_reason,p_model,p_reserve);
 RETURN v;
END $fn$;
-- Legacy records are a conservative opening balance, not a reconstruction of
-- deleted history. Recognition costs may include earlier accumulated calls.
INSERT INTO rvbbit.meaning_model_calls(call_id,event,kind,work_key,reason,cost_usd,created_at)
 SELECT gen_random_uuid(),'legacy',kind,key,'Opening balance from surviving records',cost,at FROM (
  SELECT 'recognition' AS kind,schema_name AS key,cost_usd AS cost,updated_at AS at FROM rvbbit.meaning_recognitions
  UNION ALL SELECT 'dossier',domain_key,cost_usd,updated_at FROM rvbbit.meaning_domains
  UNION ALL SELECT 'map',map_id::text,cost_usd,created_at FROM rvbbit.meaning_company_map
 ) x WHERE cost>0 AND at>now()-interval '24 hours'
 AND NOT EXISTS(SELECT 1 FROM rvbbit.meaning_model_calls WHERE event='legacy');

-- Retirement is an explicit, audited merge, never an automatic model side effect.
CREATE OR REPLACE FUNCTION rvbbit.meaning_merge_subjects(p_from text,p_into text,p_reason text)
RETURNS jsonb LANGUAGE plpgsql AS $fn$
DECLARE src rvbbit.meaning_domains%ROWTYPE; dst rvbbit.meaning_domains%ROWTYPE; n int;
BEGIN
 PERFORM pg_advisory_xact_lock(hashtext('rvbbit.meaning.publication'));
 IF p_from=p_into OR nullif(btrim(p_reason),'') IS NULL THEN
   RAISE EXCEPTION 'Merge requires distinct subjects and a reason';
 END IF;
 SELECT * INTO src FROM rvbbit.meaning_domains WHERE domain_key=p_from FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Unknown subject %',p_from; END IF;
 SELECT * INTO dst FROM rvbbit.meaning_domains WHERE domain_key=p_into FOR UPDATE;
 IF NOT FOUND OR EXISTS(SELECT 1 FROM rvbbit.meaning_subject_aliases WHERE old_key IN(p_from,p_into)) THEN
   RAISE EXCEPTION 'Merge requires two current subject identities';
 END IF;
 UPDATE rvbbit.meaning_domains SET
   members=ARRAY(SELECT DISTINCT unnest(src.members||dst.members) ORDER BY 1),
   core=ARRAY(SELECT DISTINCT unnest(src.core||dst.core) ORDER BY 1),
   roles=src.roles||dst.roles, status='placed',attempts=0,updated_at=now()
 WHERE domain_key=p_into;
 UPDATE rvbbit.meaning_relations SET domain_key=p_into WHERE domain_key=p_from;
 INSERT INTO rvbbit.meaning_association_repairs(enrichment_id,old_key,new_key,reason)
 SELECT id,p_from,p_into,p_reason FROM rvbbit.meaning_enrichments WHERE subject_key=p_from;
 UPDATE rvbbit.meaning_enrichments SET subject_key=p_into WHERE subject_key=p_from;
 GET DIAGNOSTICS n=ROW_COUNT;
 -- Preserve stated placement history while transferring the active assertion.
 WITH retired AS (
   UPDATE rvbbit.meaning_overrides SET retired_at=now()
   WHERE field='domain_key' AND value=to_jsonb(p_from) AND retired_at IS NULL RETURNING *
 ) INSERT INTO rvbbit.meaning_overrides(relation,col_name,field,value,reason,actor)
 SELECT relation,col_name,field,to_jsonb(p_into),'Subject merge: '||p_reason,actor FROM retired;
 UPDATE rvbbit.meaning_subject_aliases SET subject_key=p_into WHERE subject_key=p_from;
 INSERT INTO rvbbit.meaning_subject_aliases(old_key,subject_key,reason) VALUES(p_from,p_into,p_reason);
 UPDATE rvbbit.meaning_domains SET status='folded',updated_at=now() WHERE domain_key=p_from;
 RETURN jsonb_build_object('from',p_from,'into',p_into,'enrichments_moved',n);
END $fn$;
