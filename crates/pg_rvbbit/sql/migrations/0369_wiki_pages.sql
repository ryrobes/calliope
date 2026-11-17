-- 0369: Knowledge Loop phase 2 — wiki pages, the narrative overflow.
--
-- A small durable store for what subject/predicate/object can't hold:
-- procedures, gotchas, tribal caveats, per-agent operational patterns
-- (docs/KNOWLEDGE_LOOP_PLAN.md §4d). Pages are per-author-authored and
-- org-shared; they ride the adoption ladder (adopt-first: a written page
-- is provisional immediately); pages that link semantic-assertion
-- fingerprints inherit the Evidence Lab's verdicts through the watch
-- machinery — a contradicted claim flags the page, and the gardener owns
-- resolution (rewrite with fresh evidence or retire). Adopted pages are
-- discoverable through capability_search via the cap_wiki projection.

CREATE TABLE IF NOT EXISTS rvbbit.wiki_pages (
    page_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL UNIQUE,
    title text NOT NULL,
    body text NOT NULL,
    author_kind text NOT NULL DEFAULT 'agent',
    author_key text NOT NULL,
    subject_kind text,
    subject_key text,
    tags text[] NOT NULL DEFAULT '{}',
    claim_fingerprints text[] NOT NULL DEFAULT '{}',
    sources jsonb NOT NULL DEFAULT '[]'::jsonb,
    adoption_state text NOT NULL DEFAULT 'auto_provisional',
    adoption_state_changed_at timestamptz,
    flagged_at timestamptz,
    flag_reason text,
    revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT wiki_pages_slug_check CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,158}$'),
    CONSTRAINT wiki_pages_author_kind_check CHECK (
        author_kind IN ('agent','dream','human')
    ),
    CONSTRAINT wiki_pages_subject_kind_check CHECK (
        subject_kind IS NULL OR subject_kind IN ('person','domain','agent','source')
    ),
    CONSTRAINT wiki_pages_adoption_state_check CHECK (
        adoption_state IN ('auto_provisional','affirmed','demoted','retired')
    ),
    CONSTRAINT wiki_pages_body_check CHECK (
        length(body) BETWEEN 1 AND 40000
    ),
    CONSTRAINT wiki_pages_sources_check CHECK (jsonb_typeof(sources)='array')
);
CREATE INDEX IF NOT EXISTS wiki_pages_author_idx
    ON rvbbit.wiki_pages (author_kind, author_key);
CREATE INDEX IF NOT EXISTS wiki_pages_state_idx
    ON rvbbit.wiki_pages (adoption_state, updated_at DESC);

-- Append-only revision history: every superseded version survives.
CREATE TABLE IF NOT EXISTS rvbbit.wiki_page_revisions (
    page_id uuid NOT NULL REFERENCES rvbbit.wiki_pages(page_id) ON DELETE CASCADE,
    revision integer NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    author_kind text NOT NULL,
    author_key text NOT NULL,
    claim_fingerprints text[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (page_id, revision)
);

-- Wiki pages join the operator policy table (0368). Pages need only the
-- flag-resolution backstop; the ladder floors columns are unused for them.
ALTER TABLE rvbbit.knowledge_loop_policy
    ADD COLUMN IF NOT EXISTS retire_after_days integer NOT NULL DEFAULT 14
        CHECK (retire_after_days >= 1);
INSERT INTO rvbbit.knowledge_loop_policy (kind)
VALUES ('wiki_page') ON CONFLICT (kind) DO NOTHING;

-- Verdicts are inherited, never re-probed here: a page that links
-- assertion fingerprints reflects the Evidence Lab's current view of
-- those claims through the watch machinery (0351).
CREATE OR REPLACE VIEW rvbbit.wiki_page_verdicts AS
SELECT page.page_id, page.slug,
       count(assertion.assertion_id) AS linked_claims,
       count(*) FILTER (WHERE state.validation_state='supported') AS supported,
       count(*) FILTER (WHERE state.validation_state='contradicted') AS contradicted,
       count(*) FILTER (WHERE state.validation_state='warning') AS warnings,
       min(state.effective_confidence) AS min_confidence,
       max(state.last_observed_at) AS last_observed_at
  FROM rvbbit.wiki_pages page
  LEFT JOIN rvbbit.business_semantic_assertions assertion
    ON assertion.fingerprint=ANY(page.claim_fingerprints)
  LEFT JOIN rvbbit.business_semantic_assertion_states state
    ON state.assertion_id=assertion.assertion_id
 GROUP BY page.page_id, page.slug;

-- The cap_wiki search document.
CREATE OR REPLACE FUNCTION rvbbit.wiki_page_capability_doc(
    p_page_id uuid
) RETURNS text
LANGUAGE sql STABLE
AS $fn$
    SELECT 'capability wiki_' || page.slug
        || E'\nkind: cap_wiki'
        || E'\ntitle: ' || page.title
        || E'\nsignature: wiki_read_page(''' || page.slug || ''')'
        || E'\nauthor: ' || page.author_kind || ':' || page.author_key
        || E'\nstate: ' || page.adoption_state
        || CASE WHEN page.adoption_state='auto_provisional'
            THEN ' (working assumption — invite correction)' ELSE '' END
        || CASE WHEN array_length(page.tags,1) > 0
            THEN E'\ntags: ' || array_to_string(page.tags,', ') ELSE '' END
        || CASE WHEN verdict.contradicted > 0
            THEN E'\nWARNING: ' || verdict.contradicted
                 || ' linked claim(s) currently contradicted by the Evidence Lab.'
           ELSE '' END
        || E'\n' || left(regexp_replace(page.body,'\s+',' ','g'), 1200)
        || E'\nAccumulated working knowledge. Load the full page with wiki_read_page before relying on details.'
      FROM rvbbit.wiki_pages page
      LEFT JOIN rvbbit.wiki_page_verdicts verdict USING (page_id)
     WHERE page.page_id=p_page_id
       AND page.adoption_state IN ('auto_provisional','affirmed')
$fn$;

-- Project one page into (or out of) the capability index. Mirrors the
-- cap_playbook pattern (0283).
CREATE OR REPLACE FUNCTION rvbbit.sync_wiki_page_capability(
    p_page_id uuid,
    do_embed boolean DEFAULT true
) RETURNS jsonb
LANGUAGE plpgsql
AS $fn$
DECLARE
    rec rvbbit.wiki_pages%ROWTYPE;
    v_doc text;
    v_node bigint;
    v_embedding real[];
    v_existing_doc text;
    v_cap_name text;
BEGIN
    SELECT * INTO rec FROM rvbbit.wiki_pages WHERE page_id=p_page_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('status','missing','page_id',p_page_id);
    END IF;
    v_cap_name := 'wiki_' || rec.slug;
    IF rec.adoption_state NOT IN ('auto_provisional','affirmed') THEN
        DELETE FROM rvbbit.catalog_docs
         WHERE graph_id='rvbbit_capabilities'
           AND kind='cap_wiki'
           AND rel_name=v_cap_name;
        RETURN jsonb_build_object(
            'status',rec.adoption_state,'page_id',p_page_id,'indexed',false
        );
    END IF;

    v_doc := rvbbit.wiki_page_capability_doc(rec.page_id);
    v_node := rvbbit.kg_assert_node(
        'cap_wiki',v_cap_name,
        jsonb_strip_nulls(jsonb_build_object(
            'page_id',rec.page_id,
            'slug',rec.slug,
            'title',rec.title,
            'author_kind',rec.author_kind,
            'author_key',rec.author_key,
            'adoption_state',rec.adoption_state,
            'revision',rec.revision,
            'search_doc',v_doc
        )),
        1.0,'',0.0,'rvbbit_capabilities'
    );

    SELECT d.doc,d.embedding INTO v_existing_doc,v_embedding
      FROM rvbbit.catalog_docs d
     WHERE d.graph_id='rvbbit_capabilities' AND d.node_id=v_node;
    IF v_existing_doc IS DISTINCT FROM v_doc THEN
        v_embedding := NULL;
    END IF;
    IF v_embedding IS NULL AND do_embed THEN
        BEGIN
            v_embedding := rvbbit.embed(v_doc,'','document');
        EXCEPTION WHEN others THEN
            v_embedding := NULL;
        END;
    END IF;

    INSERT INTO rvbbit.catalog_docs
        (node_id,graph_id,kind,schema_name,rel_name,col_name,doc,embedding,embedded_at,updated_at)
    VALUES (
        v_node,'rvbbit_capabilities','cap_wiki',NULL,v_cap_name,NULL,
        v_doc,v_embedding,CASE WHEN v_embedding IS NOT NULL THEN now() END,now()
    )
    ON CONFLICT (graph_id,node_id) DO UPDATE SET
        kind=EXCLUDED.kind,
        schema_name=NULL,
        rel_name=EXCLUDED.rel_name,
        col_name=NULL,
        doc=EXCLUDED.doc,
        embedding=EXCLUDED.embedding,
        embedded_at=EXCLUDED.embedded_at,
        updated_at=now();

    RETURN jsonb_build_object(
        'status','indexed','page_id',rec.page_id,'capability',v_cap_name,
        'embedded',v_embedding IS NOT NULL
    );
END
$fn$;

-- Upsert a page by slug. Adopt-first: a new page is provisional
-- immediately. Rewriting a demoted or flagged page is the gardener's
-- resolution path (decision 4) — it revives to provisional, clears the
-- flag, and archives the prior body to the revision ledger.
CREATE OR REPLACE FUNCTION rvbbit.wiki_write_page(
    p_slug text,
    p_title text,
    p_body text,
    p_author_kind text,
    p_author_key text,
    p_tags text[] DEFAULT '{}',
    p_claim_fingerprints text[] DEFAULT '{}',
    p_sources jsonb DEFAULT '[]'::jsonb,
    p_subject_kind text DEFAULT NULL,
    p_subject_key text DEFAULT NULL
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_page rvbbit.wiki_pages%ROWTYPE;
    v_action text;
BEGIN
    SELECT * INTO v_page FROM rvbbit.wiki_pages WHERE slug=p_slug FOR UPDATE;
    IF FOUND THEN
        IF v_page.adoption_state='retired' THEN
            -- retirement is deliberate; a retired slug needs an operator
            -- (or an explicit revive) rather than a silent overwrite.
            RETURN jsonb_build_object(
                'status','retired','page_id',v_page.page_id,
                'hint','the page was retired; revive it explicitly or pick a new slug'
            );
        END IF;
        INSERT INTO rvbbit.wiki_page_revisions (
            page_id, revision, title, body, author_kind, author_key,
            claim_fingerprints
        ) VALUES (
            v_page.page_id, v_page.revision, v_page.title, v_page.body,
            v_page.author_kind, v_page.author_key, v_page.claim_fingerprints
        ) ON CONFLICT (page_id, revision) DO NOTHING;
        UPDATE rvbbit.wiki_pages SET
            title=p_title, body=p_body,
            author_kind=p_author_kind, author_key=p_author_key,
            tags=coalesce(p_tags,'{}'), claim_fingerprints=coalesce(p_claim_fingerprints,'{}'),
            sources=coalesce(p_sources,'[]'::jsonb),
            subject_kind=coalesce(p_subject_kind, subject_kind),
            subject_key=coalesce(p_subject_key, subject_key),
            adoption_state=CASE WHEN adoption_state='demoted'
                THEN 'auto_provisional' ELSE adoption_state END,
            adoption_state_changed_at=CASE WHEN adoption_state='demoted'
                THEN clock_timestamp() ELSE adoption_state_changed_at END,
            flagged_at=NULL, flag_reason=NULL,
            revision=revision+1, updated_at=clock_timestamp()
         WHERE page_id=v_page.page_id
         RETURNING * INTO v_page;
        v_action := 'revised';
    ELSE
        INSERT INTO rvbbit.wiki_pages (
            slug, title, body, author_kind, author_key, tags,
            claim_fingerprints, sources, subject_kind, subject_key,
            adoption_state, adoption_state_changed_at
        ) VALUES (
            p_slug, p_title, p_body, p_author_kind, p_author_key,
            coalesce(p_tags,'{}'), coalesce(p_claim_fingerprints,'{}'),
            coalesce(p_sources,'[]'::jsonb), p_subject_kind, p_subject_key,
            'auto_provisional', clock_timestamp()
        ) RETURNING * INTO v_page;
        v_action := 'created';
    END IF;

    BEGIN
        PERFORM rvbbit.sync_wiki_page_capability(v_page.page_id, true);
    EXCEPTION WHEN OTHERS THEN NULL; -- indexing must never block the write
    END;

    RETURN jsonb_build_object(
        'status',v_action,'page_id',v_page.page_id,'slug',v_page.slug,
        'revision',v_page.revision,'adoption_state',v_page.adoption_state
    );
END
$fn$;

-- Demote or retire a page (explicit signal, or the operator).
CREATE OR REPLACE FUNCTION rvbbit.wiki_set_page_state(
    p_slug text,
    p_state text,
    p_reason text DEFAULT NULL,
    p_actor text DEFAULT 'knowledge-loop'
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_page rvbbit.wiki_pages%ROWTYPE;
BEGIN
    IF p_state NOT IN ('auto_provisional','affirmed','demoted','retired') THEN
        RAISE EXCEPTION 'invalid wiki page state %',p_state;
    END IF;
    UPDATE rvbbit.wiki_pages SET
        adoption_state=p_state,
        adoption_state_changed_at=clock_timestamp(),
        flagged_at=CASE WHEN p_state IN ('demoted','retired') THEN flagged_at ELSE NULL END,
        flag_reason=CASE WHEN p_state IN ('demoted','retired')
            THEN coalesce(p_reason, flag_reason) ELSE NULL END,
        updated_at=clock_timestamp()
     WHERE slug=p_slug
     RETURNING * INTO v_page;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('status','missing','slug',p_slug);
    END IF;
    PERFORM rvbbit.sync_wiki_page_capability(v_page.page_id, true);
    RETURN jsonb_build_object(
        'status',p_state,'slug',p_slug,'page_id',v_page.page_id,
        'actor',p_actor,'reason',p_reason
    );
END
$fn$;

-- The flag sweep: contradicted linked claims flag a page for the
-- gardener; a flag unresolved past the operator backstop retires it.
CREATE OR REPLACE FUNCTION rvbbit.wiki_flag_tick()
RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_backstop integer;
    v_flagged jsonb := '[]'::jsonb;
    v_retired jsonb := '[]'::jsonb;
    v_row record;
BEGIN
    SELECT retire_after_days INTO v_backstop
      FROM rvbbit.knowledge_loop_policy WHERE kind='wiki_page';
    v_backstop := coalesce(v_backstop, 14);

    FOR v_row IN
        SELECT page.page_id, page.slug, verdict.contradicted
          FROM rvbbit.wiki_pages page
          JOIN rvbbit.wiki_page_verdicts verdict USING (page_id)
         WHERE page.adoption_state IN ('auto_provisional','affirmed')
           AND page.flagged_at IS NULL
           AND verdict.contradicted > 0
         LIMIT 20
    LOOP
        UPDATE rvbbit.wiki_pages SET
            flagged_at=clock_timestamp(),
            flag_reason=v_row.contradicted||' linked claim(s) contradicted by the Evidence Lab',
            updated_at=clock_timestamp()
         WHERE page_id=v_row.page_id;
        PERFORM rvbbit.sync_wiki_page_capability(v_row.page_id, true);
        v_flagged := v_flagged || to_jsonb(v_row.slug);
    END LOOP;

    FOR v_row IN
        SELECT page.page_id, page.slug
          FROM rvbbit.wiki_pages page
         WHERE page.adoption_state IN ('auto_provisional','affirmed')
           AND page.flagged_at IS NOT NULL
           AND page.flagged_at < clock_timestamp() - make_interval(days => v_backstop)
         LIMIT 20
    LOOP
        PERFORM rvbbit.wiki_set_page_state(
            v_row.slug, 'retired',
            'flag unresolved for '||v_backstop||' days (gardener backstop)'
        );
        v_retired := v_retired || to_jsonb(v_row.slug);
    END LOOP;

    RETURN jsonb_build_object(
        'status','complete','flagged',v_flagged,'retired',v_retired
    );
END
$fn$;

-- Fold the wiki sweep into the knowledge-loop tick (0368).
CREATE OR REPLACE FUNCTION rvbbit.knowledge_loop_tick()
RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_demote jsonb; v_affirm jsonb; v_adopt jsonb; v_wiki jsonb;
BEGIN
    IF NOT pg_try_advisory_xact_lock(
        hashtextextended('rvbbit.knowledge-loop-tick',0)
    ) THEN
        RETURN jsonb_build_object('status','busy');
    END IF;
    v_demote := rvbbit.knowledge_loop_demote_tick();
    v_affirm := rvbbit.knowledge_loop_affirm_tick();
    v_adopt := rvbbit.knowledge_loop_auto_adopt_tick();
    v_wiki := rvbbit.wiki_flag_tick();
    RETURN jsonb_build_object(
        'status','complete','demote',v_demote,'affirm',v_affirm,
        'adopt',v_adopt,'wiki',v_wiki
    );
END
$fn$;

COMMENT ON TABLE rvbbit.wiki_pages IS
    'Narrative working knowledge: per-author pages, org-shared, adopt-first, Evidence-Lab-flagged via linked assertion fingerprints, discoverable as cap_wiki when adopted.';
COMMENT ON FUNCTION rvbbit.wiki_write_page(text,text,text,text,text,text[],text[],jsonb,text,text) IS
    'Upsert a wiki page by slug. New pages adopt provisionally at once; rewriting a demoted/flagged page is the gardener resolution path and revives it.';
