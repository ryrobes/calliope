-- 0354: make cancellation and semantic-family retries operationally durable.
--
-- A cancelled run normally transitions when its owning worker observes the
-- request.  If that container is replaced first, the old cancel_requested row
-- used to block the one-active-run gate forever.  Claiming now reconciles an
-- expired cancellation lease before considering queued or resumable work.

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
         WHERE workflow.status='queued'
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
               updated_at=clock_timestamp(),error=NULL,
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

COMMENT ON FUNCTION rvbbit.business_topology_claim_workflow(text,integer) IS
    'Claims or resumes one topology run and first cancels orphaned cancel_requested work whose worker lease expired.';
