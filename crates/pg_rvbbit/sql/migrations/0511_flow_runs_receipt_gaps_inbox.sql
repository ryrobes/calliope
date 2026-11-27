-- 0511: flow-run grouping, cache-hit breadcrumbs, MCP receipts, and an
-- inbound queue. Additive only: new nullable columns, new tables, new
-- functions and views. Nothing existing changes meaning.
--
-- 1. Flow grouping. receipts, cost_events, mcp_invocations get run_id/flow_id.
--    They default from the session/transaction settings rvbbit.run_id and
--    rvbbit.flow_id (set by rvbbit.begin_run), so every receipt written inside
--    one flow run - including nested operator calls - groups together without
--    any writer having to pass them. The extension's own Rust writers also
--    capture them at call time so delayed (queued) receipts keep the run of the
--    call, not the run of whoever later drains the queue.
-- 2. Cache-hit breadcrumbs go to rvbbit.receipt_hits, NOT rvbbit.receipts.
--    receipts doubles as the L2 cache and is counted by many views and UIs
--    (decision_costs, receipt_cost_audit, the cost windows), so a hit row there
--    would inflate call counts. A hit costs nothing; it is a pointer to the
--    receipt that did the work. Controlled by rvbbit.cache_hit_receipts:
--      'run' (default) - log hits only while a run is active (rvbbit.run_id set)
--      'on'            - log every hit (one small INSERT per hit)
--      'off'           - never
-- 3. Direct rvbbit.mcp_call / mcp_rows now also write a receipt (operator
--    'mcp_call' / 'mcp_rows', model '<server>.<tool>') whose cost event carries
--    the receipt_id; mcp_invocations.receipt_id links back. Disable with
--    rvbbit.mcp_receipts = off.
-- 4. rvbbit.inbox: SQL-only inbound queue with idempotent put, SKIP LOCKED
--    claims, routing, retries with backoff, and NOTIFY rvbbit_inbox.

-- ---- 1. run / flow context ------------------------------------------------

CREATE OR REPLACE FUNCTION rvbbit.current_run_id()
RETURNS uuid
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $fn$
    SELECT CASE
        WHEN s ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        THEN s::uuid
    END
    FROM (SELECT nullif(btrim(current_setting('rvbbit.run_id', true)), '') AS s) setting
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.current_flow_id()
RETURNS text
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $fn$
    SELECT nullif(btrim(current_setting('rvbbit.flow_id', true)), '')
$fn$;

COMMENT ON FUNCTION rvbbit.current_run_id() IS
    'The active flow run id (setting rvbbit.run_id), or NULL. A malformed value reads as NULL rather than failing the write it defaults.';
COMMENT ON FUNCTION rvbbit.current_flow_id() IS
    'The active flow id (setting rvbbit.flow_id), or NULL.';

-- One row per flow run started with rvbbit.begin_run. Optional: receipts group
-- by run_id whether or not a row exists here (set_config works too).
CREATE TABLE IF NOT EXISTS rvbbit.flow_runs (
    run_id      uuid PRIMARY KEY,
    flow_id     text,
    inbox_id    bigint,
    status      text NOT NULL DEFAULT 'running',
    meta        jsonb,
    caller      text,
    started_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    ended_at    timestamptz,
    CONSTRAINT flow_runs_status_check
        CHECK (status IN ('running', 'done', 'failed', 'needs_human', 'abandoned'))
);
CREATE INDEX IF NOT EXISTS flow_runs_flow_time_idx
    ON rvbbit.flow_runs (flow_id, started_at DESC);
CREATE INDEX IF NOT EXISTS flow_runs_inbox_idx
    ON rvbbit.flow_runs (inbox_id) WHERE inbox_id IS NOT NULL;

-- Start (or join) a flow run. Sets rvbbit.run_id / rvbbit.flow_id for the
-- session (default) or only the current transaction (txn_local => true; the
-- safe choice on pooled connections). Returns the run id.
CREATE OR REPLACE FUNCTION rvbbit.begin_run(
    flow_id   text    DEFAULT NULL,
    run_id    uuid    DEFAULT NULL,
    txn_local boolean DEFAULT false,
    inbox_id  bigint  DEFAULT NULL,
    meta      jsonb   DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql
AS $fn$
DECLARE
    rid uuid := coalesce(begin_run.run_id, gen_random_uuid());
    fid text := nullif(btrim(begin_run.flow_id), '');
BEGIN
    PERFORM set_config('rvbbit.run_id', rid::text, txn_local);
    PERFORM set_config('rvbbit.flow_id', coalesce(fid, ''), txn_local);
    INSERT INTO rvbbit.flow_runs AS fr (run_id, flow_id, inbox_id, meta, caller)
    VALUES (rid, fid, begin_run.inbox_id, begin_run.meta,
            nullif(current_setting('rvbbit.request_user', true), ''))
    ON CONFLICT ON CONSTRAINT flow_runs_pkey DO UPDATE
        SET flow_id  = coalesce(EXCLUDED.flow_id, fr.flow_id),
            inbox_id = coalesce(EXCLUDED.inbox_id, fr.inbox_id),
            meta     = coalesce(EXCLUDED.meta, fr.meta),
            status   = 'running',
            ended_at = NULL;
    RETURN rid;
END
$fn$;

-- Close the active run (or a named one) and clear the settings.
CREATE OR REPLACE FUNCTION rvbbit.end_run(
    status text DEFAULT 'done',
    run_id uuid DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql
AS $fn$
DECLARE
    rid uuid := coalesce(end_run.run_id, rvbbit.current_run_id());
BEGIN
    IF rid IS NOT NULL THEN
        UPDATE rvbbit.flow_runs fr
           SET status = end_run.status, ended_at = clock_timestamp()
         WHERE fr.run_id = rid;
    END IF;
    IF end_run.run_id IS NULL OR end_run.run_id = rvbbit.current_run_id() THEN
        PERFORM set_config('rvbbit.run_id', '', false);
        PERFORM set_config('rvbbit.flow_id', '', false);
    END IF;
    RETURN rid;
END
$fn$;

ALTER TABLE rvbbit.receipts
    ADD COLUMN IF NOT EXISTS run_id uuid DEFAULT rvbbit.current_run_id();
ALTER TABLE rvbbit.receipts
    ADD COLUMN IF NOT EXISTS flow_id text DEFAULT rvbbit.current_flow_id();
ALTER TABLE rvbbit.cost_events
    ADD COLUMN IF NOT EXISTS run_id uuid DEFAULT rvbbit.current_run_id();
ALTER TABLE rvbbit.cost_events
    ADD COLUMN IF NOT EXISTS flow_id text DEFAULT rvbbit.current_flow_id();
ALTER TABLE rvbbit.mcp_invocations
    ADD COLUMN IF NOT EXISTS run_id uuid DEFAULT rvbbit.current_run_id();
ALTER TABLE rvbbit.mcp_invocations
    ADD COLUMN IF NOT EXISTS flow_id text DEFAULT rvbbit.current_flow_id();
ALTER TABLE rvbbit.mcp_invocations
    ADD COLUMN IF NOT EXISTS receipt_id uuid;

-- A cost event belongs to the run of the call it prices, not to whichever
-- session appends it. Settlement paths (settle_decision_costs, OpenRouter
-- reconcile, delayed-receipt flush) append later events from other sessions,
-- so take run/flow from the receipt, else from the first event of the same
-- cost_request_id, else keep the session default.
CREATE OR REPLACE FUNCTION rvbbit._cost_event_run_context()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    r_run uuid;
    r_flow text;
    found_ctx boolean := false;
BEGIN
    IF NEW.receipt_id IS NOT NULL THEN
        SELECT r.run_id, r.flow_id, true INTO r_run, r_flow, found_ctx
          FROM rvbbit.receipts r WHERE r.receipt_id = NEW.receipt_id;
    END IF;
    IF NOT coalesce(found_ctx, false) THEN
        SELECT c.run_id, c.flow_id, true INTO r_run, r_flow, found_ctx
          FROM rvbbit.cost_events c
         WHERE c.cost_request_id = NEW.cost_request_id
         ORDER BY c.event_id
         LIMIT 1;
    END IF;
    IF coalesce(found_ctx, false) THEN
        NEW.run_id := r_run;
        NEW.flow_id := r_flow;
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS cost_events_run_context ON rvbbit.cost_events;
CREATE TRIGGER cost_events_run_context
    BEFORE INSERT ON rvbbit.cost_events
    FOR EACH ROW EXECUTE FUNCTION rvbbit._cost_event_run_context();

-- cost_latest / cost_pending are SELECT * views: re-expand them so the new
-- columns show up (appended; existing column order is unchanged).
CREATE OR REPLACE VIEW rvbbit.cost_latest AS
SELECT DISTINCT ON (cost_request_id) *
FROM rvbbit.cost_events
ORDER BY cost_request_id, event_id DESC;

CREATE OR REPLACE VIEW rvbbit.cost_pending AS
SELECT *
FROM rvbbit.cost_latest
WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS receipts_run_idx
    ON rvbbit.receipts (run_id, invocation_at) WHERE run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS cost_events_run_idx
    ON rvbbit.cost_events (run_id, event_id DESC) WHERE run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS mcp_invocations_run_idx
    ON rvbbit.mcp_invocations (run_id) WHERE run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS mcp_invocations_receipt_idx
    ON rvbbit.mcp_invocations (receipt_id) WHERE receipt_id IS NOT NULL;

COMMENT ON COLUMN rvbbit.receipts.run_id IS
    'Flow run this call belonged to (rvbbit.begin_run / setting rvbbit.run_id), when any.';
COMMENT ON COLUMN rvbbit.receipts.flow_id IS
    'Flow (decision flow / router / user flow) name for the run, when any.';
COMMENT ON COLUMN rvbbit.mcp_invocations.receipt_id IS
    'Receipt written for a direct rvbbit.mcp_call / mcp_rows call. NULL for calls made inside an operator pipeline (the operator''s receipt carries them in sub_calls) and for cache hits.';

-- ---- 2. cache-hit breadcrumbs ---------------------------------------------

CREATE TABLE IF NOT EXISTS rvbbit.receipt_hits (
    hit_id             bigserial PRIMARY KEY,
    operator           text NOT NULL,
    cache_layer        text NOT NULL,
    inputs_hash        bytea,
    source_receipt_id  uuid,
    query_id           uuid,
    run_id             uuid DEFAULT rvbbit.current_run_id(),
    flow_id            text DEFAULT rvbbit.current_flow_id(),
    caller             text,
    hit_at             timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT receipt_hits_layer_check CHECK (cache_layer IN ('l1', 'l2', 'mcp'))
);
CREATE INDEX IF NOT EXISTS receipt_hits_run_idx
    ON rvbbit.receipt_hits (run_id, hit_at) WHERE run_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS receipt_hits_query_idx
    ON rvbbit.receipt_hits (query_id) WHERE query_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS receipt_hits_operator_time_idx
    ON rvbbit.receipt_hits (operator, hit_at);

COMMENT ON TABLE rvbbit.receipt_hits IS
    'Zero-cost breadcrumbs for semantic-cache hits (L1 in-memory, L2 receipts, MCP result cache). Kept out of rvbbit.receipts so cost/call views are unchanged. Logged per rvbbit.cache_hit_receipts: run (default: only inside a flow run), on, off.';
COMMENT ON COLUMN rvbbit.receipt_hits.source_receipt_id IS
    'Receipt that produced the cached answer when the cache layer knew it (L2). For L1/MCP hits resolve it through inputs_hash; rvbbit.run_trail does.';

-- Everything that happened in a run, in order: real calls (receipts, which now
-- include direct MCP calls) and cache hits. Cost on hits is always 0.
CREATE OR REPLACE VIEW rvbbit.run_trail AS
SELECT r.run_id,
       r.flow_id,
       'call'::text           AS kind,
       r.operator,
       r.model,
       NULL::text             AS cache_layer,
       r.receipt_id,
       r.receipt_id           AS source_receipt_id,
       r.query_id,
       r.cost_usd,
       r.latency_ms,
       r.error,
       r.invocation_at        AS at
  FROM rvbbit.receipts r
 WHERE r.run_id IS NOT NULL
UNION ALL
SELECT h.run_id,
       h.flow_id,
       'cache_hit'::text,
       h.operator,
       src.model,
       h.cache_layer,
       NULL::uuid,
       coalesce(h.source_receipt_id, src.receipt_id),
       h.query_id,
       0::numeric(12, 6),
       0,
       NULL::text,
       h.hit_at
  FROM rvbbit.receipt_hits h
  LEFT JOIN LATERAL (
        SELECT s.receipt_id, s.model
          FROM rvbbit.receipts s
         WHERE (h.source_receipt_id IS NOT NULL AND s.receipt_id = h.source_receipt_id)
            OR (h.source_receipt_id IS NULL AND h.inputs_hash IS NOT NULL
                AND s.inputs_hash = h.inputs_hash AND s.error IS NULL
                AND s.invocation_at <= h.hit_at)
         ORDER BY s.invocation_at DESC
         LIMIT 1
  ) src ON true
 WHERE h.run_id IS NOT NULL;

COMMENT ON VIEW rvbbit.run_trail IS
    'Per-run timeline: receipts (kind=call, includes direct MCP calls) and cache-hit breadcrumbs (kind=cache_hit, cost 0, source_receipt_id = the receipt that did the work).';

CREATE OR REPLACE VIEW rvbbit.run_summary AS
WITH calls AS (
    SELECT run_id, max(flow_id) AS flow_id, count(*) AS calls,
           count(*) FILTER (WHERE error IS NOT NULL) AS errors,
           sum(latency_ms) AS call_latency_ms,
           min(invocation_at) AS first_at, max(invocation_at) AS last_at
      FROM rvbbit.receipts WHERE run_id IS NOT NULL GROUP BY run_id
), hits AS (
    SELECT run_id, max(flow_id) AS flow_id, count(*) AS cache_hits,
           min(hit_at) AS first_at, max(hit_at) AS last_at
      FROM rvbbit.receipt_hits WHERE run_id IS NOT NULL GROUP BY run_id
), costs AS (
    SELECT run_id,
           coalesce(sum(cost_usd) FILTER (WHERE status <> 'error'), 0)::numeric(18, 9) AS cost_usd,
           count(*) FILTER (WHERE status = 'pending') AS pending_costs
      FROM rvbbit.cost_latest WHERE run_id IS NOT NULL GROUP BY run_id
), ids AS (
    SELECT run_id FROM calls UNION SELECT run_id FROM hits
    UNION SELECT run_id FROM rvbbit.flow_runs
)
SELECT ids.run_id,
       coalesce(fr.flow_id, calls.flow_id, hits.flow_id) AS flow_id,
       fr.status,
       fr.inbox_id,
       coalesce(calls.calls, 0)        AS calls,
       coalesce(calls.errors, 0)       AS errors,
       coalesce(hits.cache_hits, 0)    AS cache_hits,
       coalesce(costs.cost_usd, 0)::numeric(18, 9) AS cost_usd,
       coalesce(costs.pending_costs, 0) AS pending_costs,
       coalesce(calls.call_latency_ms, 0) AS call_latency_ms,
       coalesce(fr.started_at, least(calls.first_at, hits.first_at)) AS started_at,
       coalesce(fr.ended_at, greatest(calls.last_at, hits.last_at))  AS ended_at
  FROM ids
  LEFT JOIN rvbbit.flow_runs fr USING (run_id)
  LEFT JOIN calls USING (run_id)
  LEFT JOIN hits USING (run_id)
  LEFT JOIN costs USING (run_id);

COMMENT ON VIEW rvbbit.run_summary IS
    'One row per flow run: real calls, cache hits, settled+estimated cost from the ledger, timing.';

-- ---- 4. inbound queue -------------------------------------------------------

CREATE TABLE IF NOT EXISTS rvbbit.inbox (
    id            bigserial PRIMARY KEY,
    source        text NOT NULL,
    received_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    payload       jsonb NOT NULL,
    dedupe_key    text,
    status        text NOT NULL DEFAULT 'new',
    routed_flow   text,
    route_detail  jsonb,
    run_id        uuid,
    attempts      int NOT NULL DEFAULT 0,
    max_attempts  int NOT NULL DEFAULT 5,
    available_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    claimed_by    text,
    lease_until   timestamptz,
    result        jsonb,
    error         text,
    updated_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT inbox_status_check
        CHECK (status IN ('new', 'routed', 'processing', 'done', 'failed', 'needs_human')),
    CONSTRAINT inbox_source_dedupe_key UNIQUE (source, dedupe_key)
);
CREATE INDEX IF NOT EXISTS inbox_ready_idx
    ON rvbbit.inbox (status, available_at, id)
    WHERE status IN ('new', 'routed', 'processing');
CREATE INDEX IF NOT EXISTS inbox_flow_ready_idx
    ON rvbbit.inbox (routed_flow, status, available_at, id)
    WHERE status IN ('routed', 'processing');
CREATE INDEX IF NOT EXISTS inbox_source_time_idx
    ON rvbbit.inbox (source, received_at DESC);

COMMENT ON TABLE rvbbit.inbox IS
    'Inbound work queue. Writers call rvbbit.inbox_put (idempotent per source+dedupe_key). A system router claims status=new rows and routes them to a user flow (inbox_route); flow workers claim status=routed rows for their flow and finish with inbox_complete / inbox_fail / inbox_needs_human. NOTIFY channel: rvbbit_inbox.';

CREATE OR REPLACE FUNCTION rvbbit._inbox_notify()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    PERFORM pg_notify('rvbbit_inbox', json_build_object(
        'id', NEW.id, 'source', NEW.source, 'status', NEW.status,
        'routed_flow', NEW.routed_flow)::text);
    RETURN NULL;
END
$fn$;

DROP TRIGGER IF EXISTS inbox_notify_insert ON rvbbit.inbox;
CREATE TRIGGER inbox_notify_insert
    AFTER INSERT ON rvbbit.inbox
    FOR EACH ROW EXECUTE FUNCTION rvbbit._inbox_notify();
DROP TRIGGER IF EXISTS inbox_notify_routed ON rvbbit.inbox;
CREATE TRIGGER inbox_notify_routed
    AFTER UPDATE OF status ON rvbbit.inbox
    FOR EACH ROW
    WHEN (NEW.status IN ('routed', 'needs_human') AND OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION rvbbit._inbox_notify();

-- Put one item. Idempotent: the same (source, dedupe_key) returns the
-- existing id and changes nothing. A NULL dedupe_key always inserts.
CREATE OR REPLACE FUNCTION rvbbit.inbox_put(
    source       text,
    payload      jsonb,
    dedupe_key   text        DEFAULT NULL,
    available_at timestamptz DEFAULT NULL,
    max_attempts int         DEFAULT 5
) RETURNS bigint
LANGUAGE plpgsql
AS $fn$
DECLARE
    new_id bigint;
    src text := nullif(btrim(inbox_put.source), '');
BEGIN
    IF src IS NULL THEN
        RAISE EXCEPTION 'rvbbit.inbox_put: source is required';
    END IF;
    IF inbox_put.payload IS NULL THEN
        RAISE EXCEPTION 'rvbbit.inbox_put: payload is required';
    END IF;
    INSERT INTO rvbbit.inbox (source, payload, dedupe_key, available_at, max_attempts)
    VALUES (src, inbox_put.payload, inbox_put.dedupe_key,
            coalesce(inbox_put.available_at, clock_timestamp()),
            greatest(coalesce(inbox_put.max_attempts, 5), 1))
    ON CONFLICT ON CONSTRAINT inbox_source_dedupe_key DO NOTHING
    RETURNING id INTO new_id;
    IF new_id IS NULL THEN
        SELECT i.id INTO new_id FROM rvbbit.inbox i
         WHERE i.source = src AND i.dedupe_key = inbox_put.dedupe_key;
    END IF;
    RETURN new_id;
END
$fn$;

-- Claim up to n ready items with FOR UPDATE SKIP LOCKED, so any number of
-- routers/workers can poll concurrently without double-processing.
--   stage 'new'    - unrouted items (the router's queue)
--   stage 'routed' - routed items, optionally only for one flow
-- source narrows either stage to one inbound source.
-- A claimed row moves to 'processing', gets attempts+1, a lease, and a fresh
-- run_id (one per attempt; pass it to rvbbit.begin_run). Rows whose lease ran
-- out (a worker died) are claimable again at the same stage.
CREATE OR REPLACE FUNCTION rvbbit.inbox_claim(
    stage  text     DEFAULT 'new',
    flow   text     DEFAULT NULL,
    n      int      DEFAULT 1,
    worker text     DEFAULT NULL,
    lease  interval DEFAULT interval '5 minutes',
    source text     DEFAULT NULL
) RETURNS SETOF rvbbit.inbox
LANGUAGE plpgsql
AS $fn$
DECLARE
    now_ts timestamptz := clock_timestamp();
BEGIN
    IF inbox_claim.stage NOT IN ('new', 'routed') THEN
        RAISE EXCEPTION 'rvbbit.inbox_claim: stage must be new or routed, got %', inbox_claim.stage;
    END IF;
    RETURN QUERY
    WITH picked AS (
        SELECT i.id
          FROM rvbbit.inbox i
         WHERE i.available_at <= now_ts
           AND (inbox_claim.flow IS NULL OR i.routed_flow = inbox_claim.flow)
           AND (inbox_claim.source IS NULL OR i.source = inbox_claim.source)
           AND (
                i.status = inbox_claim.stage
                OR (i.status = 'processing' AND i.lease_until < now_ts
                    AND (i.routed_flow IS NULL) = (inbox_claim.stage = 'new'))
           )
         ORDER BY i.available_at, i.id
         LIMIT greatest(coalesce(inbox_claim.n, 1), 1)
         FOR UPDATE SKIP LOCKED
    )
    UPDATE rvbbit.inbox i
       SET status      = 'processing',
           attempts    = i.attempts + 1,
           claimed_by  = inbox_claim.worker,
           lease_until = now_ts + coalesce(inbox_claim.lease, interval '5 minutes'),
           run_id      = gen_random_uuid(),
           error       = NULL,
           updated_at  = now_ts
      FROM picked
     WHERE i.id = picked.id
    RETURNING i.*;
END
$fn$;

-- Router verdict: send the item to a user flow. detail can hold the router's
-- decision trace (e.g. the rvbbit.decision_tree result).
CREATE OR REPLACE FUNCTION rvbbit.inbox_route(
    id     bigint,
    flow   text,
    detail jsonb DEFAULT NULL
) RETURNS rvbbit.inbox
LANGUAGE plpgsql
AS $fn$
DECLARE
    row_out rvbbit.inbox;
BEGIN
    IF nullif(btrim(inbox_route.flow), '') IS NULL THEN
        RAISE EXCEPTION 'rvbbit.inbox_route: flow is required (use inbox_needs_human for unroutable items)';
    END IF;
    UPDATE rvbbit.inbox i
       SET status = 'routed', routed_flow = inbox_route.flow,
           route_detail = coalesce(inbox_route.detail, i.route_detail),
           attempts = 0, claimed_by = NULL, lease_until = NULL,
           available_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE i.id = inbox_route.id AND i.status IN ('new', 'processing', 'needs_human')
    RETURNING i.* INTO row_out;
    IF row_out.id IS NULL THEN
        RAISE EXCEPTION 'rvbbit.inbox_route: item % not found or already %', inbox_route.id,
            (SELECT status FROM rvbbit.inbox WHERE rvbbit.inbox.id = inbox_route.id);
    END IF;
    RETURN row_out;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.inbox_complete(
    id     bigint,
    result jsonb DEFAULT NULL
) RETURNS rvbbit.inbox
LANGUAGE plpgsql
AS $fn$
DECLARE
    row_out rvbbit.inbox;
BEGIN
    UPDATE rvbbit.inbox i
       SET status = 'done', result = coalesce(inbox_complete.result, i.result),
           error = NULL, claimed_by = NULL, lease_until = NULL,
           updated_at = clock_timestamp()
     WHERE i.id = inbox_complete.id AND i.status <> 'failed'
    RETURNING i.* INTO row_out;
    IF row_out.id IS NULL THEN
        RAISE EXCEPTION 'rvbbit.inbox_complete: item % not found or failed', inbox_complete.id;
    END IF;
    RETURN row_out;
END
$fn$;

-- Record a failure. With retry (default) the item goes back to its stage
-- ('new' if unrouted, else 'routed') after an exponential backoff
-- (backoff * 2^(attempts-1)), until max_attempts; then it is 'failed'.
CREATE OR REPLACE FUNCTION rvbbit.inbox_fail(
    id      bigint,
    error   text,
    retry   boolean  DEFAULT true,
    backoff interval DEFAULT interval '30 seconds'
) RETURNS rvbbit.inbox
LANGUAGE plpgsql
AS $fn$
DECLARE
    row_out rvbbit.inbox;
BEGIN
    UPDATE rvbbit.inbox i
       SET status = CASE
                       WHEN inbox_fail.retry AND i.attempts < i.max_attempts
                       THEN CASE WHEN i.routed_flow IS NULL THEN 'new' ELSE 'routed' END
                       ELSE 'failed'
                    END,
           available_at = clock_timestamp()
               + coalesce(inbox_fail.backoff, interval '0')
                 * power(2, greatest(i.attempts - 1, 0))::int,
           error = inbox_fail.error, claimed_by = NULL, lease_until = NULL,
           updated_at = clock_timestamp()
     WHERE i.id = inbox_fail.id AND i.status NOT IN ('done', 'failed')
    RETURNING i.* INTO row_out;
    IF row_out.id IS NULL THEN
        RAISE EXCEPTION 'rvbbit.inbox_fail: item % not found or already finished', inbox_fail.id;
    END IF;
    RETURN row_out;
END
$fn$;

-- Park an item for a person (low router confidence, an approval, ...).
CREATE OR REPLACE FUNCTION rvbbit.inbox_needs_human(
    id     bigint,
    reason text  DEFAULT NULL,
    detail jsonb DEFAULT NULL
) RETURNS rvbbit.inbox
LANGUAGE plpgsql
AS $fn$
DECLARE
    row_out rvbbit.inbox;
BEGIN
    UPDATE rvbbit.inbox i
       SET status = 'needs_human', error = inbox_needs_human.reason,
           route_detail = coalesce(inbox_needs_human.detail, i.route_detail),
           claimed_by = NULL, lease_until = NULL, updated_at = clock_timestamp()
     WHERE i.id = inbox_needs_human.id AND i.status NOT IN ('done', 'failed')
    RETURNING i.* INTO row_out;
    IF row_out.id IS NULL THEN
        RAISE EXCEPTION 'rvbbit.inbox_needs_human: item % not found or already finished', inbox_needs_human.id;
    END IF;
    RETURN row_out;
END
$fn$;

-- Put a failed / parked / done item back in a queue (a person resolved it, or
-- a fixed flow should replay it). Resets attempts.
CREATE OR REPLACE FUNCTION rvbbit.inbox_requeue(
    id   bigint,
    flow text DEFAULT NULL
) RETURNS rvbbit.inbox
LANGUAGE plpgsql
AS $fn$
DECLARE
    row_out rvbbit.inbox;
BEGIN
    UPDATE rvbbit.inbox i
       SET routed_flow = coalesce(inbox_requeue.flow, i.routed_flow),
           status = CASE WHEN coalesce(inbox_requeue.flow, i.routed_flow) IS NULL
                         THEN 'new' ELSE 'routed' END,
           attempts = 0, error = NULL, claimed_by = NULL, lease_until = NULL,
           available_at = clock_timestamp(), updated_at = clock_timestamp()
     WHERE i.id = inbox_requeue.id
    RETURNING i.* INTO row_out;
    IF row_out.id IS NULL THEN
        RAISE EXCEPTION 'rvbbit.inbox_requeue: item % not found', inbox_requeue.id;
    END IF;
    RETURN row_out;
END
$fn$;

CREATE OR REPLACE VIEW rvbbit.inbox_stats AS
SELECT source,
       routed_flow,
       status,
       count(*)              AS items,
       min(received_at)      AS oldest_received_at,
       max(updated_at)       AS last_updated_at
  FROM rvbbit.inbox
 GROUP BY source, routed_flow, status;
