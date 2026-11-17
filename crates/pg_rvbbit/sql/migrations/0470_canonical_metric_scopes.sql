-- Canonical metric history is the default invocation, at one definition version.
-- Preserve every original observation, including ad-hoc parameter evaluations.
-- Explicit defaults and {} are equivalent, just as in metric_sql(). Null is an
-- actual override and must not be stripped. A new definition starts a new series.
CREATE OR REPLACE VIEW rvbbit.metric_default_observations AS
SELECT o.* FROM rvbbit.metric_observations o
JOIN (SELECT DISTINCT ON(name) name,version,coalesce(params,'{}'::jsonb) AS defaults
      FROM rvbbit.metric_defs ORDER BY name,version DESC) d
  ON d.name=o.metric_name AND d.version=o.metric_version
WHERE d.defaults || coalesce(o.params,'{}'::jsonb) = d.defaults;

COMMENT ON VIEW rvbbit.metric_default_observations IS
'Stored observations of the current definition at its canonical defaults. Never recalculates history or combines ad-hoc scopes.';

-- Discovery is allowed to suggest an incomplete business measure. It must not
-- turn a formula into a canonical definition by guessing a date column/window.
-- Keep the scheduler interface and existing objects; canonicalize through the
-- normal definition tools after proving the explicit default scope.
CREATE OR REPLACE FUNCTION rvbbit.meaning_register_implied_metrics(
    p_subject_key text DEFAULT NULL, p_apply boolean DEFAULT false)
RETURNS TABLE(subject_key text, candidate text, metric_name text, action text,
              relation text, value jsonb, note text)
LANGUAGE sql STABLE AS $fn$
    SELECT d.domain_key, c->>'name', rvbbit.meaning_metric_slug(c->>'name'),
           CASE WHEN m.name IS NULL THEN 'needs_canonical_definition' ELSE 'already_registered' END,
           NULL::text, NULL::jsonb,
           'Discovery proposals do not choose scope or materialize metrics. Define a useful default population, time basis, window and unit before registering.'::text
      FROM rvbbit.meaning_domains d
      CROSS JOIN LATERAL jsonb_array_elements(coalesce(d.candidate_metrics,'[]'::jsonb)) c
      LEFT JOIN LATERAL (SELECT name FROM rvbbit.metric_defs
          WHERE name=rvbbit.meaning_metric_slug(c->>'name') ORDER BY version DESC LIMIT 1) m ON true
     WHERE d.origin='map' AND d.status='done'
       AND (p_subject_key IS NULL OR d.domain_key=p_subject_key)
$fn$;

COMMENT ON FUNCTION rvbbit.meaning_register_implied_metrics(text,boolean) IS
'Compatibility discovery report. Never invents scope or creates a metric; explicit canonical definitions are registered through the normal tools.';
