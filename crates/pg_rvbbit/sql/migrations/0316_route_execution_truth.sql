-- 0316: distinguish routing intent from the engine that actually returned rows.
--
-- candidate remains the router's requested path. executed_engine is now also
-- populated for local execution; native_fallback means the external engine
-- rejected or could not serve the request and PostgreSQL/RVBBIT returned the
-- exact result instead. Keep the rejection reason so the Routing UI can show
-- successful correctness fallbacks without claiming they were Duck successes.

ALTER TABLE IF EXISTS rvbbit.route_executions
    ADD COLUMN IF NOT EXISTS fallback_reason text;

COMMENT ON COLUMN rvbbit.route_executions.executed_engine IS
    'Engine that actually returned rows. NULL on legacy rows; native_fallback means exact PostgreSQL/RVBBIT fail-open.';

COMMENT ON COLUMN rvbbit.route_executions.fallback_reason IS
    'External-engine rejection or transport reason when executed_engine is native_fallback.';
