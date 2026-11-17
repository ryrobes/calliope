-- 0395_hindsight_curate_wrapper
--
-- SQL wrapper for the Hindsight curation lane (sibling of 0106's wrappers).
-- rvbbit.hindsight_curate soft-retires a stale world/experience fact
-- (state 'invalidated': excluded from recall/consolidation/graph, archived,
-- reversible) or restores it (state 'valid'), with the reason recorded in
-- Hindsight's audit trail. This is the operator/agent nerve for the curation
-- endpoint that shipped with the engine but whose only caller was the
-- managed service's control plane. Doctrine: invalidate-with-reason, never
-- edit memory text — corrections re-enter through normal retention.

CREATE OR REPLACE FUNCTION rvbbit.hindsight_curate(
    bank_id text,
    memory_id text,
    state text,
    reason text DEFAULT '',
    service_name text DEFAULT ''
) RETURNS jsonb
STRICT VOLATILE
LANGUAGE c
AS '$libdir/pg_rvbbit', 'hindsight_curate_wrapper';
