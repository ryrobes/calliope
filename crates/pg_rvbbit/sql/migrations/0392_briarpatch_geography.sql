-- 0392: Briarpatch geography — the abstract map becomes addressable.
--
-- Three pieces that link the explicit (pieces of work) to the abstract
-- (the Briarpatch's territory):
--   1. calliope_sketches.files — an Excalidraw binary-file store so the
--      briarpatch projector can place avatar thumbnails (server-managed;
--      the editor never sends files back, so the column is authoritative).
--   2. work_session_domains — which governed domains a session's SQL
--      actually touched (populated by the work_geography worker: sqlglot
--      table extraction over mirrored tool calls, intersected with
--      business_value_graph_summary.source_relations — the catalog is the
--      validator, not the parser).
--   3. briarpatch_anchors — the sticky coordinates of every machine
--      element on the Briarpatch sketch, exposed as a queryable relation.
--      Stable bp-* ids + sticky homes mean a domain cluster has a
--      permanent address: joinable, linkable (?focus=<element_id>), and
--      usable by any surface that wants to point at the territory.

-- Fresh-install prerequisite: older installations already have this table
-- from the web service. Migrations must also work before the first web boot.
-- This idempotent baseline does not change installations that recorded 0392.
CREATE TABLE IF NOT EXISTS rvbbit.calliope_sketches (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL UNIQUE
        REFERENCES rvbbit.calliope_sessions(id) ON DELETE CASCADE,
    owner_email text NOT NULL,
    title text NOT NULL,
    revision integer NOT NULL DEFAULT 1,
    elements jsonb NOT NULL DEFAULT '[]'::jsonb,
    app_state jsonb NOT NULL DEFAULT '{}'::jsonb,
    preview_path text,
    preview_revision integer,
    last_actor text NOT NULL DEFAULT 'calliope',
    last_actor_email text,
    last_operation_count integer NOT NULL DEFAULT 0,
    last_change_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calliope_sketches_revision_check CHECK (revision >= 1),
    CONSTRAINT calliope_sketches_actor_check
        CHECK (last_actor IN ('calliope','human','undo')),
    CONSTRAINT calliope_sketches_elements_check
        CHECK (jsonb_typeof(elements)='array'),
    CONSTRAINT calliope_sketches_app_state_check
        CHECK (jsonb_typeof(app_state)='object'),
    CONSTRAINT calliope_sketches_change_summary_check
        CHECK (jsonb_typeof(last_change_summary)='object')
);
CREATE INDEX IF NOT EXISTS calliope_sketches_owner_updated_idx
    ON rvbbit.calliope_sketches (owner_email,updated_at DESC);

ALTER TABLE rvbbit.calliope_sketches
    ADD COLUMN IF NOT EXISTS files jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS rvbbit.work_session_domains (
    session_id     uuid NOT NULL,
    domain_key     text NOT NULL,
    evidence_count integer NOT NULL DEFAULT 0,
    relations      text[] NOT NULL DEFAULT '{}',
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, domain_key)
);
CREATE INDEX IF NOT EXISTS work_session_domains_domain_idx
    ON rvbbit.work_session_domains (domain_key);

CREATE OR REPLACE VIEW rvbbit.briarpatch_anchors AS
SELECT sk.id AS sketch_id,
       el->>'id' AS element_id,
       CASE
           WHEN el->>'id' LIKE 'bp-dom-%'   THEN 'domain'
           WHEN el->>'id' LIKE 'bp-src-%'   THEN 'source'
           WHEN el->>'id' LIKE 'bp-agent-%' THEN 'agent'
           WHEN el->>'id' = 'bp-hub'        THEN 'hub'
           WHEN el->>'id' = 'bp-wilds'      THEN 'wilds'
       END AS anchor_kind,
       CASE
           WHEN el->>'id' LIKE 'bp-dom-%'   THEN substring(el->>'id' FROM 8)
           WHEN el->>'id' LIKE 'bp-src-%'   THEN substring(el->>'id' FROM 8)
           WHEN el->>'id' LIKE 'bp-agent-%' THEN substring(el->>'id' FROM 10)
       END AS anchor_key,
       (el->>'x')::numeric      AS x,
       (el->>'y')::numeric      AS y,
       (el->>'width')::numeric  AS width,
       (el->>'height')::numeric AS height,
       sk.revision,
       sk.updated_at
FROM rvbbit.calliope_sketches sk
CROSS JOIN LATERAL jsonb_array_elements(sk.elements) el
WHERE sk.title = 'The Briarpatch'
  AND coalesce(el->>'isDeleted', 'false') <> 'true'
  AND el->>'type' IN ('rectangle', 'ellipse', 'diamond', 'image')
  AND (el->>'id' LIKE 'bp-dom-%'
       OR el->>'id' LIKE 'bp-src-%'
       OR (el->>'id' LIKE 'bp-agent-%'
           AND el->>'id' NOT LIKE '%-ring'
           AND el->>'id' NOT LIKE '%-name')
       OR el->>'id' IN ('bp-hub', 'bp-wilds'));

-- One-join surface for the Floor: piece -> domain -> territory coords.
-- work_graph evolves per install (0382's seed guard), so create this
-- defensively: every column referenced exists in the seed shape and all
-- known evolved shapes, but a future evolved view must not brick the
-- migration chain.
DO $geo$
BEGIN
    EXECUTE $view$
        CREATE OR REPLACE VIEW rvbbit.work_graph_geography AS
        SELECT wg.item_id,
               wg.item_kind,
               wg.title,
               wg.column_state,
               wg.session_id,
               wsd.domain_key,
               wsd.evidence_count,
               wsd.relations,
               a.sketch_id,
               a.element_id,
               a.x, a.y, a.width, a.height
        FROM rvbbit.work_graph wg
        JOIN rvbbit.work_session_domains wsd ON wsd.session_id = wg.session_id
        LEFT JOIN rvbbit.briarpatch_anchors a
               ON a.anchor_kind = 'domain' AND a.anchor_key = wsd.domain_key
    $view$;
EXCEPTION WHEN others THEN
    RAISE NOTICE 'work_graph_geography skipped (work_graph shape mismatch): %', SQLERRM;
END
$geo$;
