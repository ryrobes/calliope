-- 0337: mirror Hermes' kanban boards into queryable tables.
--
-- The Hermes kanban is a multi-agent work dispatcher (claim locks, typed
-- blocked routing, per-card model/skills/effort overrides, per-task runs and
-- events) whose state lives in per-board sqlite files on the Hermes volume:
--   <root>/kanban.db                      (the default board)
--   <root>/kanban/boards/<slug>/kanban.db (named boards)
-- The Calliope server already mounts that volume read-only (0334's session
-- mirror); kanban_mirror.py upserts these tables on a timer so the Desk
-- shell's Board view, Today's decision queue (blocked/triage cards), and
-- Calliope herself read work state with plain SQL. Rows are never pruned:
-- a board removed upstream keeps its last mirrored state, with
-- hermes_kanban_boards.synced_at showing staleness.

CREATE TABLE IF NOT EXISTS rvbbit.hermes_kanban_boards (
    board        text PRIMARY KEY,
    display_name text,
    db_path      text,
    task_count   int,
    synced_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rvbbit.hermes_kanban_tasks (
    board                text NOT NULL,
    id                   text NOT NULL,
    title                text,
    body                 text,
    assignee             text,
    status               text,
    priority             int,
    created_by           text,
    created_at           timestamptz,
    started_at           timestamptz,
    completed_at         timestamptz,
    tenant               text,
    project_id           text,
    result               text,
    idempotency_key      text,
    consecutive_failures int,
    last_failure_error   text,
    max_runtime_seconds  int,
    last_heartbeat_at    timestamptz,
    current_run_id       bigint,
    workflow_template_id text,
    current_step_key     text,
    skills               jsonb,
    model_override       text,
    provider_override    text,
    reasoning_effort     text,
    max_retries          int,
    goal_mode            boolean,
    goal_max_turns       int,
    session_id           text,
    block_kind           text,
    block_recurrences    int,
    synced_at            timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (board, id)
);
CREATE INDEX IF NOT EXISTS hermes_kanban_tasks_status_idx
    ON rvbbit.hermes_kanban_tasks (status);
CREATE INDEX IF NOT EXISTS hermes_kanban_tasks_assignee_idx
    ON rvbbit.hermes_kanban_tasks (assignee);
CREATE INDEX IF NOT EXISTS hermes_kanban_tasks_session_idx
    ON rvbbit.hermes_kanban_tasks (session_id) WHERE session_id IS NOT NULL;

-- One row per dispatcher claim; retries append rows. Claim internals stay
-- on the run (not the task) upstream, and we carry them for forensics.
CREATE TABLE IF NOT EXISTS rvbbit.hermes_kanban_runs (
    board               text NOT NULL,
    id                  bigint NOT NULL,
    task_id             text NOT NULL,
    profile             text,
    step_key            text,
    status              text,
    outcome             text,
    worker_pid          int,
    max_runtime_seconds int,
    last_heartbeat_at   timestamptz,
    started_at          timestamptz,
    ended_at            timestamptz,
    summary             text,
    metadata            jsonb,
    error               text,
    synced_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (board, id)
);
CREATE INDEX IF NOT EXISTS hermes_kanban_runs_task_idx
    ON rvbbit.hermes_kanban_runs (board, task_id);

-- Append-only upstream: mirrored with ON CONFLICT DO NOTHING.
CREATE TABLE IF NOT EXISTS rvbbit.hermes_kanban_events (
    board      text NOT NULL,
    id         bigint NOT NULL,
    task_id    text NOT NULL,
    run_id     bigint,
    kind       text,
    payload    jsonb,
    created_at timestamptz,
    synced_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (board, id)
);
CREATE INDEX IF NOT EXISTS hermes_kanban_events_task_idx
    ON rvbbit.hermes_kanban_events (board, task_id);

CREATE TABLE IF NOT EXISTS rvbbit.hermes_kanban_comments (
    board      text NOT NULL,
    id         bigint NOT NULL,
    task_id    text NOT NULL,
    author     text,
    body       text,
    created_at timestamptz,
    synced_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (board, id)
);

CREATE TABLE IF NOT EXISTS rvbbit.hermes_kanban_links (
    board     text NOT NULL,
    parent_id text NOT NULL,
    child_id  text NOT NULL,
    synced_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (board, parent_id, child_id)
);
