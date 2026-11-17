-- 0380: the work graph — every unit of work in the appliance, one shape.
--
-- The canonical substrate for the Floor/Stage/Gallery model: chats,
-- specialist threads, workflow runs, work items, decisions, dreams,
-- playbooks, behavior overlays, and standing duty cycles projected into a
-- single item vocabulary. Read-only; the sources stay authoritative.
-- `column_state` is the fixed-column doctrine (pieces are born, not filed):
--   noticed      — born, not demanding (low-urgency dreams, FYI signals);
--                  ambient, guilt-free, self-decaying
--   in_motion    — agents or humans actively working (incl. live chats)
--   awaiting_you — the ONLY state that requires a human
--   standing     — durable and armed (approved playbooks, enabled duty
--                  cycles); standing pieces SPAWN in_motion pulses when
--                  they fire and are never 'done'
--   done         — finished; the Gallery holds what it produced
--   archived     — explicitly put away
-- `detail_state` carries the source's native state for iteration/debugging.
-- Expect this view to be replaced by follow-up migrations as the Floor
-- mockups shake out the mappings; that is its job. Some pilot installs already
-- carry a later, wider work_graph definition even though this seed migration
-- was not recorded. Never replace that evolved view with the seed shape.

DO $work_graph_seed$
BEGIN
IF to_regclass('rvbbit.work_graph') IS NULL THEN
EXECUTE $view$
CREATE VIEW rvbbit.work_graph AS

-- Chats: every session is a mini-project. Live turn => in motion.
SELECT
    'chat:' || s.id::text                       AS item_id,
    'chat'                                      AS item_kind,
    coalesce(nullif(btrim(s.title), ''), 'Untitled conversation') AS title,
    CASE
        WHEN s.archived THEN 'archived'
        WHEN EXISTS (SELECT 1 FROM rvbbit.calliope_turns t
                      WHERE t.session_id = s.id AND t.status = 'running')
            THEN 'in_motion'
        WHEN s.updated_at > now() - interval '30 minutes' THEN 'in_motion'
        ELSE 'done'
    END                                         AS column_state,
    CASE WHEN s.archived THEN 'archived' ELSE 'session' END AS detail_state,
    lower(s.owner_email)                        AS owner_email,
    CASE WHEN s.specialist_key IS NOT NULL
         THEN ARRAY['calliope', s.specialist_key]
         ELSE ARRAY['calliope'] END             AS agents,
    s.id                                        AS session_id,
    s.created_at                                AS started_at,
    s.updated_at                                AS updated_at,
    jsonb_build_object('team_mode', s.team_mode, 'purpose', s.purpose) AS ref
FROM rvbbit.calliope_sessions s

UNION ALL
-- Specialist threads: agent-led workstreams, spectatable.
SELECT
    'specialist:' || th.id::text,
    'specialist_thread',
    coalesce(nullif(btrim(th.objective), ''), 'Specialist thread'),
    CASE th.status
        WHEN 'open' THEN 'in_motion'
        ELSE 'done'
    END,
    th.status,
    lower(th.owner_email),
    ARRAY[coalesce(sp.specialist_key, 'specialist')],
    th.source_session_id,
    th.created_at,
    th.updated_at,
    jsonb_build_object('rounds', th.round_count, 'root_turn_id', th.root_turn_id)
FROM rvbbit.calliope_specialist_threads th
LEFT JOIN rvbbit.calliope_specialists sp ON sp.id = th.specialist_id

UNION ALL
-- Workflow runs.
SELECT
    'workflow:' || w.id::text,
    'workflow_run',
    coalesce(nullif(btrim(w.result_summary), ''), 'Workflow run'),
    CASE
        WHEN w.completed_at IS NULL THEN 'in_motion'
        ELSE 'done'
    END,
    coalesce(w.status, 'unknown'),
    lower(w.owner_email),
    ARRAY['calliope'],
    w.session_id,
    w.started_at,
    coalesce(w.completed_at, w.started_at),
    jsonb_build_object('trigger', w.trigger_kind, 'workflow_id', w.workflow_id)
FROM rvbbit.calliope_workflow_runs w

UNION ALL
-- Work inbox items (incl. duty-cycle alerts): the classic awaiting-you card.
SELECT
    'work_item:' || wi.id::text,
    'work_item',
    wi.title,
    CASE
        WHEN wi.state IN ('unread', 'seen') THEN 'awaiting_you'
        ELSE 'done'
    END,
    wi.state,
    lower(wi.owner_email),
    ARRAY['calliope'],
    wi.session_id,
    wi.created_at,
    coalesce(wi.updated_at, wi.created_at),
    jsonb_build_object('kind', wi.kind, 'source', wi.source, 'urgency', wi.urgency,
                       'action_prompt', wi.action_prompt)
FROM rvbbit.calliope_work_items wi

UNION ALL
-- Decision requests: questions with options, conversational or draggable.
SELECT
    'decision:' || d.id::text,
    'decision',
    left(d.question, 160),
    CASE d.status WHEN 'open' THEN 'awaiting_you' ELSE 'done' END,
    d.status,
    lower(d.owner_email),
    ARRAY['calliope'],
    d.session_id,
    d.created_at,
    coalesce(d.answered_at, d.updated_at, d.created_at),
    jsonb_build_object('kind', d.decision_kind, 'urgency', d.urgency,
                       'recommended', d.recommended_option)
FROM rvbbit.calliope_decision_requests d

UNION ALL
-- Dreams: urgent ones ask for you; the rest are ambient notices that decay
-- on their own (the retire backstop) — never guilt in the queue.
SELECT
    'dream:' || o.id::text,
    'dream',
    o.title,
    CASE
        WHEN o.state <> 'active' THEN 'done'
        -- urgency is a 0..1 score (live data clusters at 0.5); only the
        -- clearly-urgent tail earns the sacred column.
        WHEN o.urgency >= 0.75 THEN 'awaiting_you'
        ELSE 'noticed'
    END,
    o.state,
    lower(o.owner_email),
    ARRAY['calliope'],
    NULL::uuid,
    o.created_at,
    o.updated_at,
    jsonb_build_object('kind', o.kind, 'urgency', o.urgency,
                       'confidence', o.confidence, 'signals', o.signal_count)
FROM rvbbit.calliope_observations o

UNION ALL
-- Playbooks: drafts await approval; approved ones are standing capability.
SELECT
    'playbook:' || p.id::text,
    'playbook',
    coalesce(nullif(btrim(p.capability_name), ''), 'Playbook'),
    CASE
        WHEN p.archived THEN 'archived'
        WHEN p.approved_version IS NULL THEN 'awaiting_you'
        ELSE 'standing'
    END,
    CASE
        WHEN p.archived THEN 'archived'
        WHEN p.approved_version IS NULL THEN 'draft'
        ELSE 'approved v' || p.approved_version
    END,
    lower(p.owner_email),
    ARRAY['calliope'],
    p.source_session_id,
    p.created_at,
    p.updated_at,
    jsonb_build_object('kind', p.playbook_kind, 'capability_kind', p.capability_kind)
FROM rvbbit.calliope_playbooks p

UNION ALL
-- Behavior overlays: the one kept gate of the Knowledge Loop.
SELECT
    'overlay:' || ov.overlay_id::text,
    'overlay',
    ov.agent_key || ' · ' || ov.target_kind || ' overlay (' || ov.kind || ')',
    CASE ov.adoption_state
        WHEN 'proposed'         THEN 'awaiting_you'
        WHEN 'auto_provisional' THEN 'standing'
        WHEN 'affirmed'         THEN 'standing'
        ELSE 'done'
    END,
    ov.adoption_state,
    NULL,
    ARRAY[ov.agent_key],
    NULL::uuid,
    ov.created_at,
    ov.updated_at,
    jsonb_build_object('target', ov.target_key, 'section', ov.section,
                       'rationale', left(ov.rationale, 300))
FROM rvbbit.agent_overlays ov

UNION ALL
-- Duty cycles: the night shift as standing team members' standing work.
SELECT
    'duty_cycle:' || h.profile_key || ':' || h.job_id,
    'duty_cycle',
    coalesce(h.name, h.job_id),
    CASE
        WHEN NOT coalesce(h.enabled, true) THEN 'archived'
        WHEN h.failure_streak >= 2 THEN 'awaiting_you'
        ELSE 'standing'
    END,
    coalesce(h.last_status, 'never ran'),
    NULL,
    ARRAY[h.profile_key],
    NULL::uuid,
    h.last_run_at,
    h.last_run_at,
    jsonb_build_object('schedule', h.schedule, 'streak', h.failure_streak,
                       'pinned', h.pinned, 'drift', h.drift_skipped)
FROM rvbbit.duty_cycle_health h;
$view$;
END IF;
END
$work_graph_seed$;
