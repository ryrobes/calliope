-- 0411: durable human input, independent of any browser or chat rendering.
-- Existing Stage forms remain historical artifacts; only newly inserted forms
-- enter this lifecycle. No inference/backfill of old answers or abandoned asks.
CREATE TABLE IF NOT EXISTS rvbbit.calliope_input_requests (
    id uuid PRIMARY KEY REFERENCES rvbbit.calliope_surfaces(id) ON DELETE CASCADE,
    session_id uuid NOT NULL REFERENCES rvbbit.calliope_sessions(id) ON DELETE CASCADE,
    turn_id uuid NOT NULL REFERENCES rvbbit.calliope_turns(id) ON DELETE CASCADE,
    owner_email text NOT NULL,
    request jsonb NOT NULL CHECK (jsonb_typeof(request)='object'),
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','answered','cancelled')),
    answers jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(answers)='object'),
    revision integer NOT NULL DEFAULT 1 CHECK (revision>0),
    answered_by text,
    answered_at timestamptz,
    closed_by text,
    closed_at timestamptz,
    delivery_message_id uuid UNIQUE REFERENCES rvbbit.calliope_agent_messages(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((status='answered' AND answered_by IS NOT NULL AND answered_at IS NOT NULL)
        OR (status<>'answered' AND answered_by IS NULL AND answered_at IS NULL)),
    CHECK ((status='open' AND closed_at IS NULL) OR (status<>'open' AND closed_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS calliope_input_requests_open_owner_idx
    ON rvbbit.calliope_input_requests(owner_email,created_at) WHERE status='open';
CREATE INDEX IF NOT EXISTS calliope_input_requests_session_idx
    ON rvbbit.calliope_input_requests(session_id,created_at);

CREATE OR REPLACE FUNCTION rvbbit.capture_calliope_input_request()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.kind='input' AND jsonb_typeof(NEW.payload->'fields')='array'
       AND jsonb_array_length(NEW.payload->'fields')>0 THEN
        INSERT INTO rvbbit.calliope_input_requests(id,session_id,turn_id,owner_email,request)
        SELECT NEW.id,NEW.session_id,NEW.turn_id,lower(s.owner_email),NEW.payload
        FROM rvbbit.calliope_sessions s WHERE s.id=NEW.session_id
        ON CONFLICT (id) DO NOTHING;
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION rvbbit.project_calliope_input_state()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE rvbbit.calliope_surfaces SET payload=jsonb_set(payload,'{request_state}',
        jsonb_build_object('id',NEW.id,'status',NEW.status,'revision',NEW.revision,
          'answers',NEW.answers,'answered_by',NEW.answered_by,'answered_at',NEW.answered_at,
          'closed_at',NEW.closed_at,'delivery_message_id',NEW.delivery_message_id))
    WHERE id=NEW.id;
    UPDATE rvbbit.calliope_sessions SET updated_at=clock_timestamp() WHERE id=NEW.session_id;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS calliope_input_capture ON rvbbit.calliope_surfaces;
CREATE TRIGGER calliope_input_capture AFTER INSERT ON rvbbit.calliope_surfaces
    FOR EACH ROW WHEN (NEW.kind='input') EXECUTE FUNCTION rvbbit.capture_calliope_input_request();
DROP TRIGGER IF EXISTS calliope_input_state ON rvbbit.calliope_input_requests;
CREATE TRIGGER calliope_input_state AFTER INSERT OR UPDATE ON rvbbit.calliope_input_requests
    FOR EACH ROW EXECUTE FUNCTION rvbbit.project_calliope_input_state();

-- Work Graph projection: the same canonical requests feed live Floor and snapshots.
CREATE OR REPLACE VIEW rvbbit.work_graph AS
/* work_graph v1.1: parent-piece semantics; all source timestamps explicitly qualified */
WITH latest_turn AS (
    SELECT DISTINCT ON (t.session_id)
        t.session_id,
        left(regexp_replace(COALESCE(NULLIF(btrim(t.assistant_message), ''), NULLIF(btrim(t.user_message), '')), E'[\n\r]+', ' ', 'g'), 300) AS summary,
        left(regexp_replace(NULLIF(btrim(t.user_message), ''), E'[\n\r]+', ' ', 'g'), 96) AS fallback_title
    FROM rvbbit.calliope_turns t
    ORDER BY t.session_id, t.ordinal DESC
),
turn_activity AS (
    SELECT
        t.session_id,
        max(COALESCE(t.heartbeat_at, t.completed_at, t.created_at)) AS last_turn_at,
        bool_or(t.status = 'running' AND (t.lease_expires_at IS NULL OR t.lease_expires_at > now())) AS active_turn
    FROM rvbbit.calliope_turns t
    GROUP BY t.session_id
),
thread_rows AS (
    SELECT
        th.source_session_id AS session_id,
        th.id,
        COALESCE(sp.specialist_key, 'specialist') AS specialist_key,
        th.status,
        th.round_count,
        th.updated_at,
        left(regexp_replace(th.objective, E'[\n\r]+', ' ', 'g'), 120) AS objective,
        row_number() OVER (PARTITION BY th.source_session_id ORDER BY th.updated_at DESC) AS recency_rank
    FROM rvbbit.calliope_specialist_threads th
    LEFT JOIN rvbbit.calliope_specialists sp ON sp.id = th.specialist_id
    WHERE th.source_session_id IS NOT NULL
),
thread_rollup AS (
    SELECT
        tr.session_id,
        count(*) AS child_thread_count,
        count(*) FILTER (WHERE tr.status = 'open') AS open_child_thread_count,
        max(tr.updated_at) AS last_child_thread_at,
        array_agg(DISTINCT tr.specialist_key) AS child_agents,
        jsonb_agg(
            jsonb_build_object(
                'id', tr.id,
                'agent', tr.specialist_key,
                'status', tr.status,
                'rounds', tr.round_count,
                'title', tr.objective,
                'updated_at', tr.updated_at
            ) ORDER BY tr.updated_at DESC
        ) FILTER (WHERE tr.recency_rank <= 3) AS child_threads
    FROM thread_rows tr
    GROUP BY tr.session_id
),
specialist_activity AS (
    SELECT
        r.source_session_id AS session_id,
        max(COALESCE(r.heartbeat_at, r.completed_at, r.started_at, r.created_at)) AS last_specialist_at,
        bool_or(r.status IN ('queued', 'running') AND (r.lease_expires_at IS NULL OR r.lease_expires_at > now())) AS active_specialist,
        count(DISTINCT r.thread_id) FILTER (
            WHERE r.status IN ('queued', 'running')
              AND (r.lease_expires_at IS NULL OR r.lease_expires_at > now())
        ) AS active_child_count
    FROM rvbbit.calliope_specialist_runs r
    WHERE r.source_session_id IS NOT NULL
    GROUP BY r.source_session_id
),
work_signal_rollup AS (
    SELECT
        wi.session_id,
        count(*) FILTER (WHERE wi.state IN ('unread', 'seen')) AS related_signal_count
    FROM rvbbit.calliope_work_items wi
    WHERE wi.session_id IS NOT NULL
    GROUP BY wi.session_id
),
surface_rollup AS (
    SELECT sf.session_id, count(*) AS surface_count, max(sf.created_at) AS last_surface_at
    FROM rvbbit.calliope_surfaces sf
    GROUP BY sf.session_id
),
chat_base AS (
    SELECT
        s.*,
        lt.summary,
        lt.fallback_title,
        COALESCE(ta.active_turn, false) AS active_turn,
        COALESCE(sa.active_specialist, false) AS active_specialist,
        COALESCE(sa.active_child_count, 0) AS active_child_count,
        GREATEST(
            s.updated_at,
            COALESCE(ta.last_turn_at, s.updated_at),
            COALESCE(tr.last_child_thread_at, s.updated_at),
            COALESCE(sa.last_specialist_at, s.updated_at),
            COALESCE(sr.last_surface_at, s.updated_at)
        ) AS last_activity_at,
        COALESCE(tr.child_thread_count, 0) AS child_thread_count,
        COALESCE(tr.open_child_thread_count, 0) AS open_child_thread_count,
        COALESCE(tr.child_agents, ARRAY[]::text[]) AS child_agents,
        COALESCE(tr.child_threads, '[]'::jsonb) AS child_threads,
        tr.last_child_thread_at,
        COALESCE(sr.surface_count, 0) AS surface_count,
        COALESCE(wsr.related_signal_count, 0) AS related_signal_count
    FROM rvbbit.calliope_sessions s
    LEFT JOIN latest_turn lt ON lt.session_id = s.id
    LEFT JOIN turn_activity ta ON ta.session_id = s.id
    LEFT JOIN thread_rollup tr ON tr.session_id = s.id
    LEFT JOIN specialist_activity sa ON sa.session_id = s.id
    LEFT JOIN surface_rollup sr ON sr.session_id = s.id
    LEFT JOIN work_signal_rollup wsr ON wsr.session_id = s.id
)
SELECT
    'chat:' || s.id::text AS item_id,
    'chat'::text AS item_kind,
    COALESCE(NULLIF(NULLIF(btrim(s.title), ''), 'New inquiry'), s.fallback_title, 'Untitled conversation') AS title,
    CASE
        WHEN s.archived THEN 'archived'
        WHEN s.active_turn OR s.active_specialist THEN 'in_motion'
        WHEN s.last_activity_at > now() - interval '30 minutes' THEN 'in_motion'
        ELSE 'done'
    END AS column_state,
    CASE WHEN s.archived THEN 'archived' ELSE 'session' END AS detail_state,
    lower(s.owner_email) AS owner_email,
    ARRAY(
        SELECT DISTINCT a
        FROM unnest(
            ARRAY[COALESCE(NULLIF(btrim(s.specialist_key), ''), 'calliope')]::text[]
            || s.child_agents
        ) AS a
    ) AS agents,
    s.id AS session_id,
    s.created_at AS started_at,
    s.last_activity_at AS updated_at,
    jsonb_build_object(
        'team_mode', s.team_mode,
        'purpose', s.purpose,
        'child_thread_count', s.child_thread_count,
        'open_child_thread_count', s.open_child_thread_count,
        'child_threads', s.child_threads,
        'last_child_activity_at', s.last_child_thread_at
    ) AS ref,
    s.summary,
    NULL::text AS parent_item_id,
    s.last_activity_at,
    (s.active_turn OR s.active_specialist) AS active_now,
    CASE
        WHEN s.archived THEN 'Archived by owner'
        WHEN s.active_turn THEN 'Calliope is working now'
        WHEN s.active_specialist THEN 'A specialist is working now'
        WHEN s.last_activity_at > now() - interval '30 minutes' THEN 'Recently active'
        ELSE 'Idle for 30 minutes; reopen anytime'
    END AS state_reason,
    s.archived AS default_hidden,
    CASE
        WHEN s.active_turn OR s.active_specialist THEN 100
        WHEN s.last_activity_at > now() - interval '30 minutes' THEN 80
        ELSE 20
    END::integer AS attention_rank,
    s.surface_count::bigint AS surface_count,
    COALESCE(NULLIF(NULLIF(btrim(s.title), ''), 'New inquiry'), s.fallback_title, 'Untitled conversation') AS display_title,
    GREATEST(0, floor(extract(epoch FROM (now() - s.last_activity_at))))::bigint AS idle_seconds,
    s.child_thread_count::bigint AS child_thread_count,
    s.active_child_count::bigint AS active_child_count,
    s.related_signal_count::bigint AS related_signal_count,
    ('chat:' || s.id::text)::text AS route
FROM chat_base s

UNION ALL

SELECT
    'specialist:' || th.id::text,
    'specialist_thread'::text,
    COALESCE(NULLIF(left(btrim(th.objective), 160), ''), 'Standalone specialist thread'),
    CASE
        WHEN th.status <> 'open' THEN 'done'
        WHEN th.updated_at > now() - interval '30 minutes' THEN 'in_motion'
        ELSE 'done'
    END,
    th.status,
    lower(th.owner_email),
    ARRAY[COALESCE(sp.specialist_key, 'specialist')],
    NULL::uuid,
    th.created_at,
    th.updated_at,
    jsonb_build_object('rounds', th.round_count, 'root_turn_id', th.root_turn_id),
    left(regexp_replace(th.objective, E'[\n\r]+', ' ', 'g'), 300),
    NULL::text,
    th.updated_at,
    false,
    CASE WHEN th.status = 'open' AND th.updated_at > now() - interval '30 minutes' THEN 'Standalone specialist recently active' ELSE 'Standalone specialist idle; reopen anytime' END,
    false,
    CASE WHEN th.status = 'open' AND th.updated_at > now() - interval '30 minutes' THEN 70 ELSE 20 END::integer,
    0::bigint,
    COALESCE(NULLIF(left(btrim(th.objective), 160), ''), 'Standalone specialist thread'),
    GREATEST(0, floor(extract(epoch FROM (now() - th.updated_at))))::bigint,
    0::bigint,
    0::bigint,
    0::bigint,
    ('specialist:' || th.id::text)::text
FROM rvbbit.calliope_specialist_threads th
LEFT JOIN rvbbit.calliope_specialists sp ON sp.id = th.specialist_id
WHERE th.source_session_id IS NULL

UNION ALL

SELECT
    'workflow:' || w.id::text,
    'workflow_run'::text,
    COALESCE(NULLIF(left(btrim(w.result_summary), 160), ''), 'Workflow run'),
    CASE WHEN w.completed_at IS NULL THEN 'in_motion' ELSE 'done' END,
    COALESCE(w.status, 'unknown'),
    lower(w.owner_email),
    ARRAY['calliope']::text[],
    w.session_id,
    w.started_at,
    COALESCE(w.completed_at, w.started_at),
    jsonb_build_object('trigger', w.trigger_kind, 'workflow_id', w.workflow_id, 'steps', w.steps),
    left(w.result_summary, 300),
    CASE WHEN w.session_id IS NULL THEN NULL ELSE 'chat:' || w.session_id::text END,
    COALESCE(w.completed_at, w.started_at),
    (w.completed_at IS NULL AND w.status IN ('queued', 'running')),
    CASE WHEN w.completed_at IS NULL THEN 'Workflow is running' ELSE 'Workflow completed' END,
    false,
    CASE WHEN w.completed_at IS NULL THEN 80 ELSE 20 END::integer,
    0::bigint,
    COALESCE(NULLIF(left(btrim(w.result_summary), 160), ''), 'Workflow run'),
    CASE WHEN COALESCE(w.completed_at, w.started_at) IS NULL THEN NULL ELSE GREATEST(0, floor(extract(epoch FROM (now() - COALESCE(w.completed_at, w.started_at)))))::bigint END,
    0::bigint,
    0::bigint,
    0::bigint,
    ('workflow:' || w.id::text)::text
FROM rvbbit.calliope_workflow_runs w

UNION ALL

SELECT
    'work_item:' || wi.id::text,
    'work_item'::text,
    wi.title,
    CASE WHEN wi.state IN ('unread', 'seen') THEN 'awaiting_you' ELSE 'done' END,
    wi.state,
    lower(wi.owner_email),
    ARRAY['calliope']::text[],
    wi.session_id,
    wi.created_at,
    COALESCE(wi.updated_at, wi.created_at),
    jsonb_build_object('kind', wi.kind, 'source', wi.source, 'source_ref', wi.source_ref, 'urgency', wi.urgency, 'action_prompt', wi.action_prompt),
    left(wi.summary, 300),
    CASE WHEN wi.session_id IS NULL THEN NULL ELSE 'chat:' || wi.session_id::text END,
    COALESCE(wi.updated_at, wi.created_at),
    false,
    CASE
        WHEN wi.state = 'unread' AND wi.kind = 'blocked' THEN 'Unread blocker'
        WHEN wi.state = 'unread' AND wi.kind = 'result' THEN 'Unread result'
        WHEN wi.state = 'unread' THEN 'Unread signal'
        WHEN wi.state = 'seen' THEN 'Seen; action still open'
        ELSE 'Dismissed'
    END,
    false,
    CASE
        WHEN wi.state NOT IN ('unread', 'seen') THEN 20
        WHEN wi.urgency = 'critical' THEN 100
        WHEN wi.urgency = 'high' OR wi.kind = 'blocked' THEN 95
        WHEN wi.kind = 'result' THEN 80
        ELSE 70
    END::integer,
    0::bigint,
    wi.title,
    GREATEST(0, floor(extract(epoch FROM (now() - COALESCE(wi.updated_at, wi.created_at)))))::bigint,
    0::bigint,
    0::bigint,
    0::bigint,
    ('work_item:' || wi.id::text)::text
FROM rvbbit.calliope_work_items wi

UNION ALL

SELECT
    'decision:' || d.id::text,
    'decision'::text,
    left(d.question, 160),
    CASE WHEN d.status = 'open' THEN 'awaiting_you' ELSE 'done' END,
    d.status,
    lower(d.owner_email),
    ARRAY['calliope']::text[],
    d.session_id,
    d.created_at,
    COALESCE(d.answered_at, d.updated_at, d.created_at),
    jsonb_build_object('kind', d.decision_kind, 'urgency', d.urgency, 'recommended', d.recommended_option),
    left(d.summary, 300),
    CASE WHEN d.session_id IS NULL THEN NULL ELSE 'chat:' || d.session_id::text END,
    COALESCE(d.answered_at, d.updated_at, d.created_at),
    false,
    CASE WHEN d.status = 'open' THEN 'Decision waiting for you' ELSE 'Decision answered' END,
    false,
    CASE WHEN d.status = 'open' THEN 100 ELSE 20 END::integer,
    0::bigint,
    left(d.question, 160),
    GREATEST(0, floor(extract(epoch FROM (now() - COALESCE(d.answered_at, d.updated_at, d.created_at)))))::bigint,
    0::bigint,
    0::bigint,
    0::bigint,
    ('decision:' || d.id::text)::text
FROM rvbbit.calliope_decision_requests d

UNION ALL

SELECT
    'dream:' || o.id::text,
    'dream'::text,
    o.title,
    CASE WHEN o.state <> 'active' THEN 'done' WHEN o.urgency >= 0.75 THEN 'awaiting_you' ELSE 'noticed' END,
    o.state,
    lower(o.owner_email),
    ARRAY['calliope']::text[],
    NULL::uuid,
    o.created_at,
    o.updated_at,
    jsonb_build_object('kind', o.kind, 'urgency', o.urgency, 'confidence', o.confidence, 'signals', o.signal_count),
    left(o.summary, 300),
    NULL::text,
    o.updated_at,
    false,
    CASE WHEN o.state = 'active' AND o.urgency >= 0.75 THEN 'High-priority observation' WHEN o.state = 'active' THEN 'Low-priority observation' ELSE 'Observation resolved' END,
    (o.state = 'active' AND o.urgency < 0.75),
    CASE WHEN o.state = 'active' THEN floor(o.urgency * 100)::integer ELSE 20 END,
    0::bigint,
    o.title,
    GREATEST(0, floor(extract(epoch FROM (now() - o.updated_at))))::bigint,
    0::bigint,
    0::bigint,
    0::bigint,
    ('dream:' || o.id::text)::text
FROM rvbbit.calliope_observations o

UNION ALL

SELECT
    'playbook:' || p.id::text,
    'playbook'::text,
    COALESCE(NULLIF(btrim(p.capability_name), ''), 'Playbook'),
    CASE WHEN p.archived THEN 'archived' WHEN p.approved_version IS NULL THEN 'awaiting_you' ELSE 'standing' END,
    CASE WHEN p.archived THEN 'archived' WHEN p.approved_version IS NULL THEN 'draft' ELSE 'approved v' || p.approved_version END,
    lower(p.owner_email),
    ARRAY['calliope']::text[],
    p.source_session_id,
    p.created_at,
    p.updated_at,
    jsonb_build_object('kind', p.playbook_kind, 'capability_kind', p.capability_kind, 'source_session_id', p.source_session_id),
    NULL::text,
    NULL::text,
    p.updated_at,
    false,
    CASE WHEN p.archived THEN 'Archived playbook' WHEN p.approved_version IS NULL THEN 'Draft awaiting review' ELSE 'Approved and available' END,
    p.archived,
    CASE WHEN p.approved_version IS NULL THEN 75 WHEN p.archived THEN 10 ELSE 40 END::integer,
    0::bigint,
    COALESCE(NULLIF(btrim(p.capability_name), ''), 'Playbook'),
    GREATEST(0, floor(extract(epoch FROM (now() - p.updated_at))))::bigint,
    0::bigint,
    0::bigint,
    0::bigint,
    ('playbook:' || p.id::text)::text
FROM rvbbit.calliope_playbooks p

UNION ALL

SELECT
    'overlay:' || ov.overlay_id::text,
    'overlay'::text,
    ov.agent_key || ' · ' || ov.target_kind || ' overlay (' || ov.kind || ')',
    CASE ov.adoption_state WHEN 'proposed' THEN 'awaiting_you' WHEN 'auto_provisional' THEN 'standing' WHEN 'affirmed' THEN 'standing' ELSE 'done' END,
    ov.adoption_state,
    NULL::text,
    ARRAY[ov.agent_key],
    NULL::uuid,
    ov.created_at,
    ov.updated_at,
    jsonb_build_object('target', ov.target_key, 'section', ov.section, 'rationale', left(ov.rationale, 300)),
    left(ov.rationale, 300),
    NULL::text,
    ov.updated_at,
    false,
    CASE WHEN ov.adoption_state = 'proposed' THEN 'Overlay awaiting review' WHEN ov.adoption_state IN ('auto_provisional', 'affirmed') THEN 'Overlay is available' ELSE 'Overlay inactive' END,
    false,
    CASE WHEN ov.adoption_state = 'proposed' THEN 75 WHEN ov.adoption_state IN ('auto_provisional', 'affirmed') THEN 40 ELSE 20 END::integer,
    0::bigint,
    ov.agent_key || ' · ' || ov.target_kind || ' overlay (' || ov.kind || ')',
    GREATEST(0, floor(extract(epoch FROM (now() - ov.updated_at))))::bigint,
    0::bigint,
    0::bigint,
    0::bigint,
    ('overlay:' || ov.overlay_id::text)::text
FROM rvbbit.agent_overlays ov

UNION ALL

SELECT
    'duty_cycle:' || h.profile_key || ':' || h.job_id,
    'duty_cycle'::text,
    COALESCE(h.name, h.job_id),
    CASE WHEN NOT COALESCE(h.enabled, true) THEN 'archived' ELSE 'standing' END,
    COALESCE(h.last_status, 'never ran'),
    NULL::text,
    ARRAY[h.profile_key],
    NULL::uuid,
    h.last_run_at,
    h.last_run_at,
    jsonb_build_object('schedule', h.schedule, 'streak', h.failure_streak, 'failures_14', h.failures_14, 'pinned', h.pinned, 'drift', h.drift_skipped),
    left(h.last_error, 300),
    NULL::text,
    h.last_run_at,
    false,
    CASE WHEN NOT COALESCE(h.enabled, true) THEN 'Duty cycle disabled' WHEN h.failure_streak >= 2 THEN 'Enabled; failure signal projected' ELSE 'Enabled and waiting' END,
    NOT COALESCE(h.enabled, true),
    CASE WHEN h.failure_streak >= 2 THEN 65 WHEN NOT COALESCE(h.enabled, true) THEN 10 ELSE 40 END::integer,
    0::bigint,
    COALESCE(h.name, h.job_id),
    CASE WHEN h.last_run_at IS NULL THEN NULL ELSE GREATEST(0, floor(extract(epoch FROM (now() - h.last_run_at))))::bigint END,
    0::bigint,
    0::bigint,
    0::bigint,
    ('duty_cycle:' || h.profile_key || ':' || h.job_id)::text
FROM rvbbit.duty_cycle_health h

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
JOIN rvbbit.calliope_sessions s ON s.id=r.session_id;
