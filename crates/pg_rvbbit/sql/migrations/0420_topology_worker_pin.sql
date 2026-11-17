-- 0420: Pin the excavation worker — hot-swap the worker without restarting
-- calliope-server.
--
-- The topology worker is a thread inside calliope-server, so a code change
-- normally waits for a container restart. When a restart is not welcome (a
-- long chat turn in flight), a side-car worker process with the new code can
-- run inside the same container under its own WAREHOUSE_BUSINESS_TOPOLOGY_
-- WORKER_ID, and the claim function refuses every other worker while the
-- pin holds. The pin is inert unless the pinned worker has registered in the
-- last two minutes (the side-car dies with the next restart, and the
-- restarted in-process worker must not be locked out), and it expires on
-- its own. Operators pin with business_topology_worker_pin(worker_id, hours)
-- and clear with business_topology_worker_unpin().

CREATE OR REPLACE FUNCTION rvbbit.business_topology_worker_pin(
    p_worker_id text,
    p_hours integer DEFAULT 24
) RETURNS jsonb
LANGUAGE plpgsql VOLATILE
AS $fn$
DECLARE
    v_value jsonb;
BEGIN
    IF nullif(btrim(p_worker_id),'') IS NULL THEN
        RAISE EXCEPTION 'worker_id is required';
    END IF;
    v_value := jsonb_build_object(
        'worker_id', btrim(p_worker_id),
        'until', clock_timestamp() + make_interval(hours => least(168, greatest(1, coalesce(p_hours, 24)))),
        'pinned_by', session_user,
        'pinned_at', clock_timestamp());
    UPDATE rvbbit.settings SET value=v_value, updated_at=now() WHERE key='business_topology_worker_pin';
    IF NOT FOUND THEN
        INSERT INTO rvbbit.settings(key, value, updated_at) VALUES ('business_topology_worker_pin', v_value, now());
    END IF;
    RETURN v_value;
END
$fn$;

CREATE OR REPLACE FUNCTION rvbbit.business_topology_worker_unpin()
RETURNS boolean
LANGUAGE plpgsql VOLATILE
AS $fn$
BEGIN
    DELETE FROM rvbbit.settings WHERE key='business_topology_worker_pin';
    RETURN FOUND;
END
$fn$;

-- True when a live pin names a different worker that is still heartbeating.
CREATE OR REPLACE FUNCTION rvbbit._business_topology_claim_blocked_by_pin(p_worker_id text)
RETURNS boolean
LANGUAGE sql STABLE
AS $fn$
    SELECT EXISTS (
        SELECT 1
          FROM rvbbit.settings s
          JOIN rvbbit.business_topology_workflow_workers w
            ON w.worker_id = s.value->>'worker_id'
         WHERE s.key='business_topology_worker_pin'
           AND (s.value->>'until')::timestamptz > clock_timestamp()
           AND w.last_seen_at > clock_timestamp() - interval '120 seconds'
           AND s.value->>'worker_id' <> btrim(p_worker_id)
    );
$fn$;

-- Claim: identical to 0417 plus the pin check.
CREATE OR REPLACE FUNCTION rvbbit.business_topology_claim_workflow(
    p_worker_id text,
    p_lease_seconds integer DEFAULT 300
) RETURNS TABLE (
    run_id uuid,
    run_name text,
    parameters jsonb,
    progress jsonb,
    requested_by text,
    attempts integer
)
LANGUAGE plpgsql VOLATILE
AS $fn$
BEGIN
    IF nullif(btrim(p_worker_id),'') IS NULL THEN
        RAISE EXCEPTION 'worker_id is required';
    END IF;
    IF p_lease_seconds NOT BETWEEN 30 AND 3600 THEN
        RAISE EXCEPTION 'lease_seconds must be between 30 and 3600';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'rvbbit.business-topology-workflow-runner',0
    ));

    UPDATE rvbbit.business_topology_workflow_runs workflow
       SET status='cancelled',phase='cancelled',error=NULL,
           progress=workflow.progress||jsonb_build_object(
               'phase','cancelled','cancel_reconciled_at',clock_timestamp(),
               'cancel_reconciled_reason','worker_lease_expired'
           ),
           worker_id=NULL,lease_expires_at=NULL,
           updated_at=clock_timestamp(),finished_at=clock_timestamp()
     WHERE workflow.status='cancel_requested'
       AND (
           workflow.lease_expires_at IS NULL
           OR workflow.lease_expires_at<clock_timestamp()
       );

    -- A pinned side-car worker owns the queue while it is alive.
    IF rvbbit._business_topology_claim_blocked_by_pin(p_worker_id) THEN
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1 FROM rvbbit.business_topology_workflow_runs
         WHERE status IN ('running','cancel_requested')
           AND NOT (
               status='running' AND lease_expires_at<clock_timestamp()
           )
    ) THEN
        RETURN;
    END IF;

    RETURN QUERY
    WITH picked AS (
        SELECT workflow.run_id
          FROM rvbbit.business_topology_workflow_runs workflow
         WHERE (
                workflow.status='queued'
                AND (workflow.not_before IS NULL OR workflow.not_before<=clock_timestamp())
            )
            OR (
                workflow.status='running'
                AND workflow.lease_expires_at<clock_timestamp()
            )
         ORDER BY CASE WHEN workflow.status='running' THEN 0 ELSE 1 END,
                  workflow.requested_at
         LIMIT 1
         FOR UPDATE SKIP LOCKED
    ), updated AS (
        UPDATE rvbbit.business_topology_workflow_runs workflow
           SET status='running',
               phase=CASE WHEN workflow.status='queued' THEN 'claimed' ELSE 'resuming' END,
               worker_id=btrim(p_worker_id),
               lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
               attempts=workflow.attempts+1,
               started_at=coalesce(workflow.started_at,clock_timestamp()),
               updated_at=clock_timestamp(),error=NULL,not_before=NULL,
               progress=workflow.progress||jsonb_build_object(
                   'phase',CASE WHEN workflow.status='queued' THEN 'claimed' ELSE 'resuming' END,
                   'worker_id',btrim(p_worker_id),'claimed_at',clock_timestamp()
               )
          FROM picked
         WHERE workflow.run_id=picked.run_id
         RETURNING workflow.*
    )
    SELECT updated.run_id,updated.run_name,updated.parameters,updated.progress,
           updated.requested_by,updated.attempts
      FROM updated;
END
$fn$;
