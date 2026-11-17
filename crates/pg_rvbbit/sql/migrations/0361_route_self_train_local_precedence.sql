-- 0361: make local self-training capable of correcting shipped auto overlays,
-- and retain the evidence needed to separate cold-start cost from steady state.

ALTER TABLE rvbbit.route_observations
    ADD COLUMN IF NOT EXISTS executed_engine text;
ALTER TABLE rvbbit.route_observations
    ADD COLUMN IF NOT EXISTS cold_ms double precision;
ALTER TABLE rvbbit.route_observations
    ADD COLUMN IF NOT EXISTS warm_ms double precision;
ALTER TABLE rvbbit.route_observations
    ADD COLUMN IF NOT EXISTS sample_count integer NOT NULL DEFAULT 1;

COMMENT ON COLUMN rvbbit.route_observations.executed_engine IS
    'Engine that actually returned rows during an optimizer replay; candidate remains routing intent.';
COMMENT ON COLUMN rvbbit.route_observations.cold_ms IS
    'First execution latency before optimizer warm samples.';
COMMENT ON COLUMN rvbbit.route_observations.warm_ms IS
    'Median latency of optimizer warm samples; used as the steady-state training label.';

-- Base-routed shapes remain candidates. In addition, locally revalidate active
-- factory auto overlays after the normal cooldown. Manual and locally-tested
-- pins are intentionally excluded.
CREATE OR REPLACE VIEW rvbbit.route_optimization_candidates AS
SELECT e.shape_key,
       e.shape_family,
       count(*)                                               AS executions,
       round(avg(e.elapsed_ms)::numeric, 2)                   AS avg_ms,
       round((count(*) * avg(e.elapsed_ms))::numeric, 2)      AS potential_ms,
       max(e.executed_at)                                     AS last_seen
FROM rvbbit.route_executions e
LEFT JOIN rvbbit.route_overlay o ON o.shape_key = e.shape_key AND o.enabled
WHERE e.executed_at > now() - interval '1 day'
  AND e.status = 'ok'
  AND e.shape_key IS NOT NULL
  AND e.shape_key <> ''
  AND e.route_source <> 'forced'
  AND (
        (e.route_source NOT IN ('overlay', 'overlay-auto', 'overlay-local') AND o.shape_key IS NULL)
        OR
        (e.route_source IN ('overlay', 'overlay-auto') AND o.source = 'auto')
      )
GROUP BY e.shape_key, e.shape_family
ORDER BY potential_ms DESC NULLS LAST;

COMMENT ON VIEW rvbbit.route_optimization_candidates IS
    'Hot base-routed shapes plus active factory-auto overlays due for local revalidation; manual and tested pins are excluded.';
