-- 0422 replaced work_graph with the whole-thread-summary definition but omitted
-- the input-request branch introduced by 0411. Restore that branch onto the
-- installed view instead of replaying an older full definition and losing its
-- later improvements. Source requests, answers and historical frames stay intact.
DO $input_requests$
DECLARE current_view text;
BEGIN
    -- Use the view's relation dependency, not a text match or the presence of
    -- an open request, so replay is safe even with an empty input queue.
    IF NOT EXISTS (
        SELECT 1 FROM pg_rewrite r
        JOIN pg_depend d ON d.classid='pg_rewrite'::regclass AND d.objid=r.oid
        WHERE r.ev_class='rvbbit.work_graph'::regclass
          AND d.refclassid='pg_class'::regclass
          AND d.refobjid='rvbbit.calliope_input_requests'::regclass
    ) THEN
        current_view := pg_get_viewdef('rvbbit.work_graph'::regclass, true);
        EXECUTE 'CREATE OR REPLACE VIEW rvbbit.work_graph AS '
            || rtrim(current_view, E';\n ') || $branch$
UNION ALL
SELECT
    'input_request:' || r.id::text,
    'input_request'::text,
    left(r.request->>'title',160),
    CASE WHEN r.status='open' THEN 'awaiting_you' ELSE 'done' END,
    r.status,
    r.owner_email,
    ARRAY[COALESCE(NULLIF(s.specialist_key,''),'calliope')]::text[],
    r.session_id,
    r.created_at,
    r.updated_at,
    jsonb_build_object('kind','choice','urgency','normal','action_prompt',r.request->>'prompt',
        'input_request_id',r.id,'revision',r.revision,'answers',r.answers),
    left(r.request->>'prompt',300),
    'chat:' || r.session_id::text,
    r.updated_at,
    false,
    CASE r.status WHEN 'open' THEN 'Question waiting for your answer'
      WHEN 'answered' THEN 'Answer recorded' ELSE 'Question cancelled' END,
    false,
    CASE WHEN r.status='open' THEN 110 ELSE 20 END::integer,
    0::bigint,
    r.request->>'title',
    GREATEST(0,floor(extract(epoch FROM (now()-r.updated_at))))::bigint,
    0::bigint,0::bigint,0::bigint,
    'input_request:' || r.id::text
FROM rvbbit.calliope_input_requests r
JOIN rvbbit.calliope_sessions s ON s.id=r.session_id
$branch$;
    END IF;
END $input_requests$;
