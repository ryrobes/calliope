-- Public demo fixture. No model call.
-- Apply crates/pg_rvbbit/sql/migrations/0509_calliope_demo_guest.sql first.
-- The canonical statement below must stay identical to demo_mode.SAMPLE_SQL
-- after demo_mode.prepare_sql. scripts/demo/seed_public_dashboard.py is the
-- same fixture and refreshes the allowlist through that function.

SELECT rvbbit.configure_demo_guest(ARRAY['public_demo']);

CREATE TABLE IF NOT EXISTS public_demo.orders (
    id int PRIMARY KEY,
    region text NOT NULL,
    amount numeric NOT NULL
);
TRUNCATE public_demo.orders;
INSERT INTO public_demo.orders (id, region, amount) VALUES
    (1, 'North', 120),
    (2, 'North', 80),
    (3, 'South', 50),
    (4, 'East', 200);
SELECT rvbbit.configure_demo_guest(ARRAY['public_demo']);

INSERT INTO rvbbit.dashboards
    (slug, name, description, owner_email, status, latest_version, app_kind, demo_public)
SELECT
    'public-demo-revenue',
    'Regional revenue',
    'A small public demo dashboard. No model was called.',
    'demo-seed@calliope.local',
    'live',
    1,
    'dashboard',
    true
WHERE NOT EXISTS (
    SELECT 1 FROM rvbbit.dashboards WHERE slug = 'public-demo-revenue'
);

INSERT INTO rvbbit.dashboard_versions (dashboard_id, version, html, kind, created_by, notes)
SELECT d.id, 1, $html$<!doctype html><html><head><title>Regional revenue</title></head><body><h1>Regional revenue</h1><pre id=chart></pre><script>rvbbitQuery('SELECT region, SUM(amount) AS revenue FROM public_demo.orders GROUP BY region ORDER BY revenue DESC').then(function(result){document.getElementById('chart').textContent=JSON.stringify(result.rows);});</script></body></html>$html$,
       'live', 'demo-seed', 'public demo fixture'
FROM rvbbit.dashboards d
WHERE d.slug = 'public-demo-revenue'
  AND NOT EXISTS (
      SELECT 1 FROM rvbbit.dashboard_versions v
      WHERE v.dashboard_id = d.id AND v.version = 1
  );

INSERT INTO rvbbit.demo_query_allowlist (dashboard_id, version, sql_hash, canonical_sql)
SELECT d.id, 1,
       encode(sha256('SELECT region, SUM(amount) AS revenue FROM public_demo.orders GROUP BY region ORDER BY revenue DESC'::bytea), 'hex'),
       'SELECT region, SUM(amount) AS revenue FROM public_demo.orders GROUP BY region ORDER BY revenue DESC'
FROM rvbbit.dashboards d
WHERE d.slug = 'public-demo-revenue'
ON CONFLICT (dashboard_id, version, sql_hash) DO NOTHING;

INSERT INTO rvbbit.demo_build_provenance
    (dashboard_id, version, thread_id, models, token_cost, token_count, wall_ms, steps)
SELECT d.id, 1, NULL, '["fixture"]'::jsonb, 0, 0, 0, '[{"tool":"publish_dashboard","count":1}]'::jsonb
FROM rvbbit.dashboards d
WHERE d.slug = 'public-demo-revenue'
ON CONFLICT (dashboard_id, version) DO UPDATE
    SET models = EXCLUDED.models,
        token_cost = EXCLUDED.token_cost,
        token_count = EXCLUDED.token_count,
        wall_ms = EXCLUDED.wall_ms,
        steps = EXCLUDED.steps,
        thread_id = NULL,
        recorded_at = now();

UPDATE rvbbit.dashboards
   SET demo_public = true, archived = false, app_kind = 'dashboard', updated_at = now()
 WHERE slug = 'public-demo-revenue';
