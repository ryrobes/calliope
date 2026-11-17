-- 0387: production work graph plus retained, route-keyed RVBBIT timeline.
--
-- The evolved work_graph v1.1 definition was promoted from the test appliance
-- on 2026-09-04. 0386's explicit hourly heap copies are preserved under a
-- legacy name; the canonical table below instead keeps one current row per
-- route while RVBBIT's retained cumulative generations hold the history.
--
-- IMPORTANT: rebuild_acceleration() is deliberately not used on the heartbeat:
-- it folds and retires prior row groups. The top-level procedure commits the
-- merge first, then incrementally refreshes in a new transaction so UPDATE and
-- DELETE tombstones plus new row generations remain available to AS-OF reads.

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
            ARRAY['calliope']::text[]
            || CASE WHEN s.specialist_key IS NULL THEN ARRAY[]::text[] ELSE ARRAY[s.specialist_key] END
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
FROM rvbbit.duty_cycle_health h;

-- Preserve the short-lived 0386 pilot data rather than destructively replacing
-- it. Renaming its indexes frees the canonical names for the new relation.
DO $convert_0386$
BEGIN
    IF to_regclass('rvbbit.work_graph_snapshots') IS NOT NULL
       AND NOT rvbbit.is_rvbbit_table('rvbbit.work_graph_snapshots'::regclass)
    THEN
        IF to_regclass('rvbbit.work_graph_snapshots_0386') IS NOT NULL THEN
            RAISE EXCEPTION
                'cannot promote work_graph_snapshots: rvbbit.work_graph_snapshots_0386 already exists';
        END IF;

        ALTER TABLE rvbbit.work_graph_snapshots
            RENAME TO work_graph_snapshots_0386;

        IF to_regclass('rvbbit.work_graph_snapshots_pkey') IS NOT NULL THEN
            ALTER INDEX rvbbit.work_graph_snapshots_pkey
                RENAME TO work_graph_snapshots_0386_pkey;
        END IF;
        IF to_regclass('rvbbit.work_graph_snapshots_item_idx') IS NOT NULL THEN
            ALTER INDEX rvbbit.work_graph_snapshots_item_idx
                RENAME TO work_graph_snapshots_0386_item_idx;
        END IF;

        COMMENT ON TABLE rvbbit.work_graph_snapshots_0386 IS
            'Superseded 0386 append-only hourly captures, preserved by migration 0387.';
    END IF;
END
$convert_0386$;

CREATE TABLE IF NOT EXISTS rvbbit.work_graph_snapshots (
    snapshot_at         timestamptz NOT NULL,
    item_id             text NOT NULL,
    item_kind           text,
    title               text,
    column_state        text,
    detail_state        text,
    owner_email         text,
    agents              text[],
    session_id          uuid,
    started_at          timestamptz,
    updated_at          timestamptz,
    ref                  jsonb,
    summary              text,
    parent_item_id       text,
    last_activity_at     timestamptz,
    active_now           boolean,
    state_reason         text,
    default_hidden       boolean,
    attention_rank       integer,
    surface_count        bigint,
    display_title        text,
    idle_seconds         bigint,
    child_thread_count   bigint,
    active_child_count   bigint,
    related_signal_count bigint,
    route                text PRIMARY KEY
) USING rvbbit;

COMMENT ON TABLE rvbbit.work_graph_snapshots IS
    'Current work_graph materialization keyed by route; historical states live in retained RVBBIT generations for AS-OF queries.';
COMMENT ON COLUMN rvbbit.work_graph_snapshots.snapshot_at IS
    'Last time this route changed in the materialized work graph.';

DO $work_graph_policy$
BEGIN
    IF NOT rvbbit.is_rvbbit_table('rvbbit.work_graph_snapshots'::regclass) THEN
        RAISE EXCEPTION 'rvbbit.work_graph_snapshots is not registered for RVBBIT acceleration';
    END IF;

    PERFORM rvbbit.set_acceleration_storage_policy(
        'rvbbit.work_graph_snapshots'::regclass,
        generation_semantics => 'cumulative',
        history_policy => 'retained'
    );
END
$work_graph_policy$;

CREATE OR REPLACE FUNCTION rvbbit.work_graph_snapshot_tick()
RETURNS jsonb
LANGUAGE plpgsql
AS $work_graph_merge$
DECLARE
    v_stamp timestamptz := clock_timestamp();
    v_upserted integer := 0;
    v_deleted integer := 0;
    v_rows integer := 0;
BEGIN
    -- Keep manual invocations and a delayed cron run from overlapping.
    PERFORM pg_advisory_xact_lock(hashtextextended('rvbbit.work_graph_snapshot_tick', 0));

    WITH applied AS (
        INSERT INTO rvbbit.work_graph_snapshots AS snap (
            snapshot_at,
            item_id,
            item_kind,
            title,
            column_state,
            detail_state,
            owner_email,
            agents,
            session_id,
            started_at,
            updated_at,
            ref,
            summary,
            parent_item_id,
            last_activity_at,
            active_now,
            state_reason,
            default_hidden,
            attention_rank,
            surface_count,
            display_title,
            idle_seconds,
            child_thread_count,
            active_child_count,
            related_signal_count,
            route
        )
        SELECT
            v_stamp,
            graph.item_id,
            graph.item_kind,
            graph.title,
            graph.column_state,
            graph.detail_state,
            graph.owner_email,
            graph.agents,
            graph.session_id,
            graph.started_at,
            graph.updated_at,
            graph.ref,
            graph.summary,
            graph.parent_item_id,
            graph.last_activity_at,
            graph.active_now,
            graph.state_reason,
            graph.default_hidden,
            graph.attention_rank,
            graph.surface_count,
            graph.display_title,
            graph.idle_seconds,
            graph.child_thread_count,
            graph.active_child_count,
            graph.related_signal_count,
            graph.route
        FROM rvbbit.work_graph AS graph
        ON CONFLICT (route) DO UPDATE
        SET snapshot_at = EXCLUDED.snapshot_at,
            item_id = EXCLUDED.item_id,
            item_kind = EXCLUDED.item_kind,
            title = EXCLUDED.title,
            column_state = EXCLUDED.column_state,
            detail_state = EXCLUDED.detail_state,
            owner_email = EXCLUDED.owner_email,
            agents = EXCLUDED.agents,
            session_id = EXCLUDED.session_id,
            started_at = EXCLUDED.started_at,
            updated_at = EXCLUDED.updated_at,
            ref = EXCLUDED.ref,
            summary = EXCLUDED.summary,
            parent_item_id = EXCLUDED.parent_item_id,
            last_activity_at = EXCLUDED.last_activity_at,
            active_now = EXCLUDED.active_now,
            state_reason = EXCLUDED.state_reason,
            default_hidden = EXCLUDED.default_hidden,
            attention_rank = EXCLUDED.attention_rank,
            surface_count = EXCLUDED.surface_count,
            display_title = EXCLUDED.display_title,
            idle_seconds = EXCLUDED.idle_seconds,
            child_thread_count = EXCLUDED.child_thread_count,
            active_child_count = EXCLUDED.active_child_count,
            related_signal_count = EXCLUDED.related_signal_count
        WHERE ROW(
            snap.item_id,
            snap.item_kind,
            snap.title,
            snap.column_state,
            snap.detail_state,
            snap.owner_email,
            snap.agents,
            snap.session_id,
            snap.started_at,
            snap.updated_at,
            snap.ref,
            snap.summary,
            snap.parent_item_id,
            snap.last_activity_at,
            snap.active_now,
            snap.state_reason,
            snap.default_hidden,
            snap.attention_rank,
            snap.surface_count,
            snap.display_title,
            snap.idle_seconds,
            snap.child_thread_count,
            snap.active_child_count,
            snap.related_signal_count
        ) IS DISTINCT FROM ROW(
            EXCLUDED.item_id,
            EXCLUDED.item_kind,
            EXCLUDED.title,
            EXCLUDED.column_state,
            EXCLUDED.detail_state,
            EXCLUDED.owner_email,
            EXCLUDED.agents,
            EXCLUDED.session_id,
            EXCLUDED.started_at,
            EXCLUDED.updated_at,
            EXCLUDED.ref,
            EXCLUDED.summary,
            EXCLUDED.parent_item_id,
            EXCLUDED.last_activity_at,
            EXCLUDED.active_now,
            EXCLUDED.state_reason,
            EXCLUDED.default_hidden,
            EXCLUDED.attention_rank,
            EXCLUDED.surface_count,
            EXCLUDED.display_title,
            EXCLUDED.idle_seconds,
            EXCLUDED.child_thread_count,
            EXCLUDED.active_child_count,
            EXCLUDED.related_signal_count
        )
        RETURNING 1
    )
    SELECT count(*)::integer INTO v_upserted FROM applied;

    DELETE FROM rvbbit.work_graph_snapshots AS snap
    WHERE NOT EXISTS (
        SELECT 1
        FROM rvbbit.work_graph AS graph
        WHERE graph.route = snap.route
    );
    GET DIAGNOSTICS v_deleted = ROW_COUNT;

    SELECT count(*)::integer INTO v_rows
    FROM rvbbit.work_graph_snapshots;

    RETURN jsonb_build_object(
        'status', 'merged',
        'snapshot_at', v_stamp,
        'upserted', v_upserted,
        'deleted', v_deleted,
        'rows', v_rows
    );
END
$work_graph_merge$;

COMMENT ON FUNCTION rvbbit.work_graph_snapshot_tick() IS
    'Merges rvbbit.work_graph into the route-keyed current materialization. The cron procedure commits this result before publishing its RVBBIT delta.';

CREATE OR REPLACE PROCEDURE rvbbit.work_graph_snapshot_run()
LANGUAGE plpgsql
AS $work_graph_run$
DECLARE
    v_merge jsonb;
    v_refresh jsonb;
    v_delete_generation bigint := 0;
    v_published_generation bigint := 0;
BEGIN
    SELECT rvbbit.work_graph_snapshot_tick() INTO v_merge;

    -- refresh_acceleration cannot export rows written by its own transaction.
    -- A top-level CALL permits this real boundary; pg_cron invokes it that way.
    COMMIT AND CHAIN;

    SELECT rvbbit.refresh_acceleration(
        'rvbbit.work_graph_snapshots'::regclass,
        false
    ) INTO v_refresh;

    -- A delete-only delta writes tombstones but no Parquet row group, so the
    -- generic refresher has no generation row to timestamp. Publish a zero-row
    -- timeline marker at the tombstone generation; otherwise AS OF TIMESTAMP
    -- after a pure deletion would resolve to the preceding visible state.
    SELECT coalesce(max(deleted_generation), 0)
      INTO v_delete_generation
      FROM rvbbit.delete_log
     WHERE table_oid = 'rvbbit.work_graph_snapshots'::regclass;

    SELECT coalesce(max(generation), 0)
      INTO v_published_generation
      FROM rvbbit.generations
     WHERE table_oid = 'rvbbit.work_graph_snapshots'::regclass;

    IF v_delete_generation > v_published_generation THEN
        INSERT INTO rvbbit.generations (
            table_oid, generation, n_rows, n_row_groups
        ) VALUES (
            'rvbbit.work_graph_snapshots'::regclass,
            v_delete_generation,
            0,
            0
        )
        ON CONFLICT (table_oid, generation) DO NOTHING;

        UPDATE rvbbit.tables
           SET next_generation = greatest(next_generation, v_delete_generation + 1)
         WHERE table_oid = 'rvbbit.work_graph_snapshots'::regclass;

        UPDATE rvbbit.acceleration_state
           SET last_refresh_generation = greatest(
                   coalesce(last_refresh_generation, 0),
                   v_delete_generation
               ),
               updated_at = clock_timestamp()
         WHERE table_oid = 'rvbbit.work_graph_snapshots'::regclass;

        v_refresh := v_refresh || jsonb_build_object(
            'delete_generation', v_delete_generation,
            'delete_generation_rows', 0
        );
    END IF;

    RAISE LOG 'work graph snapshot merge %, RVBBIT refresh %', v_merge, v_refresh;
END
$work_graph_run$;

COMMENT ON PROCEDURE rvbbit.work_graph_snapshot_run() IS
    'Top-level 15-minute work-graph capture: route merge, commit, then retained cumulative RVBBIT delta publication.';
