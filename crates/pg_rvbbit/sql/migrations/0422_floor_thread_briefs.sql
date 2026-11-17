-- 0422: Floor thread labels remember the whole thread.
--
-- The Floor's title and status for a chat piece were generated from the
-- latest message alone (work_graph.summary was the last turn, the
-- presentation packet added "latest user context / latest assistant
-- progress", and the hash changed with every turn), so a 20-turn thread that
-- opened with "how many leads came in yesterday?" was titled after its last
-- tangent — "Metrics Tile — hard-refresh to verify formatter fix" — and
-- re-titled again on the next message. Three changes, all in SQL:
--   1. work_graph.summary prefers the whole-thread brief written by the
--      session-synopsis worker (calliope.py; 45–90 words: what the person set
--      out to do, what was established, what is happening now) and falls back
--      to the latest message only when no brief exists yet.
--   2. The presentation packet carries the opening ask, the brief, and the
--      previous title; the prompt names the enduring subject of the whole
--      thread and keeps the previous title verbatim unless the subject changed.
--      The previous title is excluded from the packet hash so a regenerated
--      title cannot trigger its own regeneration.
--   3. The status line may be one or two sentences (≤30 words, 360 chars)
--      instead of an 11-word clause.
-- Changing the prompt changes every packet hash, so cached presentations
-- regenerate lazily (bounded per read, as before).

CREATE OR REPLACE FUNCTION rvbbit.floor_presentation_prompt() RETURNS text
LANGUAGE sql IMMUTABLE AS $prompt$ SELECT 'title string: a specific natural 3-to-8-word noun phrase naming the enduring subject of the WHOLE thread — the project, artifact, dataset, decision, or outcome the person set out to get — taken from the opening ask and the brief, never from the latest message alone. If a previous title is given, return it verbatim unless the thread''s subject has clearly changed. Never a sentence, activity report, or generic Calliope work label. status string: one or two present-tense sentences, at most 30 words, saying what the human needs to do for awaiting_you, the current step and what it serves for in_motion, or the shipped outcome for done. Prefer receipt numbers, actions, and outcomes over vague prose. Do not repeat the title. No markdown.'::text $prompt$;

CREATE OR REPLACE FUNCTION rvbbit.floor_presentation_candidates(p_owner text DEFAULT NULL)
RETURNS TABLE(item_id text,column_state text,lane_rank bigint,source_text text,source_hash text)
LANGUAGE sql STABLE AS $candidates$
WITH ranked AS (
    SELECT w.*,coalesce(src.user_message,latest.user_message) AS user_context, coalesce(src.assistant_message,latest.assistant_message) AS assistant_context,
           opening.user_message AS opening_ask, fp.semantic_title AS previous_title,
           row_number() OVER (PARTITION BY CASE WHEN p_owner IS NULL THEN w.owner_email END,w.column_state  ORDER BY w.attention_rank DESC,w.last_activity_at DESC NULLS LAST) AS lane_rank
    FROM rvbbit.work_graph w
    LEFT JOIN rvbbit.calliope_turns src ON src.id::text=w.ref->>'source_ref'
    LEFT JOIN LATERAL (SELECT t.user_message,t.assistant_message FROM rvbbit.calliope_turns t WHERE w.item_kind='chat' AND t.session_id::text=split_part(w.item_id,':',2) ORDER BY t.ordinal DESC LIMIT 1) latest ON true
    -- The opening ask: the first substantive human message, so the subject of
    -- the thread is always in the packet no matter how far the tail wandered.
    LEFT JOIN LATERAL (SELECT t.user_message FROM rvbbit.calliope_turns t WHERE w.item_kind='chat' AND t.session_id::text=split_part(w.item_id,':',2) AND t.turn_kind<>'sketch_bootstrap' AND length(btrim(coalesce(t.user_message,'')))>=12 ORDER BY t.ordinal LIMIT 1) opening ON true
    LEFT JOIN rvbbit.floor_presentations fp ON fp.item_id=w.item_id
    WHERE (p_owner IS NULL OR (
        (
            w.owner_email IS NULL
            OR lower(w.owner_email) = lower(p_owner)
            OR (
                w.item_kind = 'chat'
                AND EXISTS (
                    SELECT 1
                    FROM rvbbit.calliope_sessions visible_session
                    WHERE visible_session.id::text = split_part(w.item_id, ':', 2)
                      AND rvbbit.calliope_session_can_view(
                          visible_session.id, p_owner, false
                      )
                )
            )
            OR (
                -- Approved playbooks published to everyone (factory canon,
                -- deliberately shared SOPs) are shared furniture: every
                -- Floor shows them in Standing regardless of owner.
                w.item_kind = 'playbook'
                AND EXISTS (
                    SELECT 1
                    FROM rvbbit.calliope_playbooks everyone_pb
                    JOIN rvbbit.capability_access_policies everyone_cap
                      ON everyone_cap.capability_kind = 'cap_playbook'
                     AND everyone_cap.capability_name = everyone_pb.capability_name
                    WHERE everyone_pb.id::text = split_part(w.item_id, ':', 2)
                      AND everyone_pb.approved_version IS NOT NULL
                      AND everyone_cap.visibility = 'everyone'
                )
            )
        )
        AND (
            w.item_kind <> 'chat'
            OR EXISTS (
                SELECT 1
                FROM rvbbit.calliope_turns visible_turn
                WHERE visible_turn.session_id::text = split_part(w.item_id, ':', 2)
                  AND visible_turn.turn_kind IN ('chat', 'agent_message')
            )
        )
    )) AND w.column_state IN ('awaiting_you','in_motion','done')), candidates AS (
    SELECT item_id,column_state,lane_rank,
        -- hash_text: everything the model sees except the previous title.
        concat_ws(E'\n', 'lane='||column_state,
        'kind='||item_kind, 'current title='||coalesce(display_title,''), 'state evidence='||coalesce(state_reason,''),
        'opening ask='||left(regexp_replace(coalesce(opening_ask,''), E'[\n\r]+', ' ', 'g'),700),
        'brief='||left(coalesce(summary,''),1400),
        'action needed='||left(coalesce(ref->>'action_prompt',''),1200),
        'latest user context='||left(coalesce(user_context,''),600),
        'latest assistant progress='||left(coalesce(assistant_context,''),900),
        'active='||active_now,'active collaborators='||active_child_count,
        'signals='||related_signal_count,'artifacts='||surface_count,
        CASE WHEN column_state='in_motion' THEN 'freshness bucket='|| floor(extract(epoch FROM now())/1800)::text END) AS hash_text,
        previous_title
    FROM ranked WHERE (column_state='awaiting_you' AND lane_rank<=20) OR (column_state='in_motion' AND lane_rank<=5) OR (column_state='done' AND lane_rank<=25)
    ORDER BY CASE column_state WHEN 'awaiting_you' THEN 1 WHEN 'in_motion' THEN 2 ELSE 3 END,lane_rank)
SELECT c.item_id,c.column_state,c.lane_rank,
       concat_ws(E'\n', c.hash_text, 'previous title='||coalesce(c.previous_title,'')) AS source_text,
       md5(c.hash_text || rvbbit.floor_presentation_prompt() ||
           jsonb_build_array(o.model,o.system_prompt,o.user_prompt,o.steps)::text) AS source_hash
FROM candidates c CROSS JOIN rvbbit.operators o WHERE o.name='clover_llm_extract'
$candidates$;

CREATE OR REPLACE FUNCTION rvbbit.floor_presentation_refresh(
    p_item_id text,p_source_text text,p_source_hash text)
RETURNS boolean LANGUAGE plpgsql AS $refresh$
DECLARE v_result jsonb; v_title text; v_status text;
BEGIN
    -- Cron and browser-triggered work share one nonblocking lock per item.
    IF NOT pg_try_advisory_xact_lock(hashtextextended('floor-presentation:'||p_item_id,0)) THEN
        RETURN false;
    END IF;
    IF EXISTS (SELECT 1 FROM rvbbit.floor_presentations
        WHERE item_id=p_item_id AND source_hash=p_source_hash
          AND (semantic_title IS NOT NULL OR retry_after>now())) THEN RETURN false; END IF;
    v_result := rvbbit.clover_llm_extract(p_source_text,rvbbit.floor_presentation_prompt());
    v_title := trim(v_result->>'title'); v_status := trim(v_result->>'status');
    IF coalesce(length(v_title),0) NOT BETWEEN 3 AND 90
       OR coalesce(length(v_status),0) NOT BETWEEN 3 AND 360
       OR v_title ~* '^(null|calliope work( needs attention)?|server reboot(ed)? again|transport failure|connection error|parent workstream)$'
    THEN v_title := NULL; v_status := NULL; END IF;
    INSERT INTO rvbbit.floor_presentations(item_id,source_hash,semantic_title,semantic_status,retry_after)
    VALUES(p_item_id,p_source_hash,v_title,v_status,
           CASE WHEN v_title IS NULL THEN clock_timestamp()+interval '5 minutes' END)
    ON CONFLICT(item_id) DO UPDATE SET source_hash=excluded.source_hash,
        semantic_title=excluded.semantic_title,semantic_status=excluded.semantic_status,
        updated_at=clock_timestamp(),retry_after=excluded.retry_after;
    RETURN v_title IS NOT NULL;
END $refresh$;

CREATE OR REPLACE VIEW rvbbit.work_graph AS
/* work_graph v1.2: chat summary prefers the whole-thread brief (0422); v1.1 parent-piece semantics; all source timestamps explicitly qualified */
WITH latest_turn AS (
    /* 0422: the thread brief (calliope_session_synopses, whole-thread) is the
       summary when it exists; the latest message is only the fallback. */
    SELECT DISTINCT ON (t.session_id)
        t.session_id,
        left(COALESCE(
            NULLIF(btrim(sn.synopsis), ''),
            regexp_replace(COALESCE(NULLIF(btrim(t.assistant_message), ''), NULLIF(btrim(t.user_message), '')), E'[\n\r]+', ' ', 'g')
        ), 700) AS summary,
        left(regexp_replace(NULLIF(btrim(t.user_message), ''), E'[\n\r]+', ' ', 'g'), 96) AS fallback_title
    FROM rvbbit.calliope_turns t
    LEFT JOIN rvbbit.calliope_session_synopses sn ON sn.session_id = t.session_id AND sn.status = 'ready'
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
FROM rvbbit.duty_cycle_health h;


-- ── Sidebar and Floor name a thread the same way ─────────────────────────
-- calliope_sessions.title is generated once from the first turns
-- ("Calliope reported that sh.sales has 918,843 rows…") and never revisited.
-- When the Floor produces a semantic title for a chat piece, copy it onto the
-- session — but only over machine titles (provisional/generated/system),
-- never a title a person typed (manual).
CREATE OR REPLACE FUNCTION rvbbit._floor_presentation_title_to_session()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE v_session uuid;
BEGIN
    IF NEW.item_id NOT LIKE 'chat:%' OR nullif(btrim(coalesce(NEW.semantic_title,'')),'') IS NULL THEN
        RETURN NULL;
    END IF;
    IF TG_OP='UPDATE' AND OLD.semantic_title IS NOT DISTINCT FROM NEW.semantic_title THEN
        RETURN NULL;
    END IF;
    BEGIN
        v_session := split_part(NEW.item_id,':',2)::uuid;
    EXCEPTION WHEN OTHERS THEN
        RETURN NULL;
    END;
    UPDATE rvbbit.calliope_sessions s
       SET title=left(btrim(NEW.semantic_title),120), title_source='generated'
     WHERE s.id=v_session
       AND coalesce(s.title_source,'system') IN ('provisional','generated','system')
       AND s.title IS DISTINCT FROM left(btrim(NEW.semantic_title),120);
    RETURN NULL;
END
$fn$;
DROP TRIGGER IF EXISTS floor_presentation_title_to_session ON rvbbit.floor_presentations;
CREATE TRIGGER floor_presentation_title_to_session
AFTER INSERT OR UPDATE OF semantic_title ON rvbbit.floor_presentations
FOR EACH ROW EXECUTE FUNCTION rvbbit._floor_presentation_title_to_session();
