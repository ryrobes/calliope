-- 0423: pin the excavation judge in SQL.
--
-- business_topology_jobs._model_routes reads the judgment route from the
-- run's parameters first, then env, then the Hermes default. On the pilot
-- (2026-09-10 13:15 UTC) the Hermes default model was flipped to gpt-6-astra
-- on the openai-codex provider, which has no direct backend, so judgment
-- silently fell back to a Hermes session with tools and memory on and wrote
-- 13 documents into the user-facing Hindsight bank. A BEFORE INSERT trigger
-- that stamps the judgment route onto every new workflow run keeps the judge
-- where the operator put it, regardless of what Hermes is set to that hour.
--
-- The migration installs the table and trigger only. An installation opts
-- in by inserting rows:
--   INSERT INTO rvbbit.business_topology_route_defaults (key, value) VALUES
--     ('judgment_backend', 'openrouter'), ('judgment_model', 'google/gemini-3.8-flash');
-- Change the judge with an UPDATE; delete the rows to fall back to env/Hermes.
-- Explicit run parameters always win over these defaults.

CREATE TABLE IF NOT EXISTS rvbbit.business_topology_route_defaults (
    key text PRIMARY KEY
        CHECK (key IN ('judgment_backend', 'judgment_model', 'assembly_backend', 'assembly_model')),
    value text NOT NULL,
    note text,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION rvbbit.business_topology_apply_route_defaults()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    defaults jsonb;
BEGIN
    SELECT coalesce(jsonb_object_agg(key, value), '{}'::jsonb)
      INTO defaults
      FROM rvbbit.business_topology_route_defaults;
    IF defaults = '{}'::jsonb THEN
        RETURN NEW;
    END IF;
    -- jsonb || keeps the right-hand value on conflict: explicit parameters win.
    NEW.parameters := defaults || coalesce(NEW.parameters, '{}'::jsonb);
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS business_topology_route_defaults ON rvbbit.business_topology_workflow_runs;
CREATE TRIGGER business_topology_route_defaults
    BEFORE INSERT ON rvbbit.business_topology_workflow_runs
    FOR EACH ROW EXECUTE FUNCTION rvbbit.business_topology_apply_route_defaults();
