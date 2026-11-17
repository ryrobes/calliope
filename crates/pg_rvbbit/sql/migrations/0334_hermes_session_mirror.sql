-- 0334: mirror of the Hermes session store + per-call API accounting.
--
-- Everything needed to explain a Calliope turn's context behaviour used to
-- live in three places nobody could query together: Hermes' sqlite session
-- store (messages, compaction flags, per-task token totals), Hermes'
-- agent.log (the ONLY per-API-call prompt/output/cache/latency numbers) and
-- RVBBIT's own tool activity. The Calliope server, which already mounts the
-- Hermes volume read-only, now mirrors the first two into these system
-- tables (see services/calliope/hermes_mirror.py) so a DataRabbit app and
-- Calliope herself can read a session's tape with plain SQL.
--
-- Mirrors are upserts keyed like the source (message flags flip on
-- compaction, so this is not append-only); log-derived rows are keyed by a
-- hash of the log line so re-reads are idempotent.

CREATE TABLE IF NOT EXISTS rvbbit.hermes_sessions (
    id                                text PRIMARY KEY,
    store                             text NOT NULL DEFAULT 'default',
    source                            text,
    user_id                           text,
    session_key                       text,
    chat_id                           text,
    chat_type                         text,
    thread_id                         text,
    display_name                      text,
    model                             text,
    model_config                      jsonb,
    system_prompt_hash                text,
    parent_session_id                 text,
    profile_name                      text,
    started_at                        timestamptz,
    ended_at                          timestamptz,
    end_reason                        text,
    last_activity_at                  timestamptz,
    message_count                     integer,
    tool_call_count                   integer,
    api_call_count                    integer,
    input_tokens                      bigint,
    output_tokens                     bigint,
    cache_read_tokens                 bigint,
    cache_write_tokens                bigint,
    reasoning_tokens                  bigint,
    billing_provider                  text,
    billing_base_url                  text,
    estimated_cost_usd                double precision,
    actual_cost_usd                   double precision,
    title                             text,
    title_source                      text,
    compression_failure_cooldown_until timestamptz,
    compression_failure_error         text,
    compression_fallback_streak       integer,
    compression_ineffective_count     integer,
    rewind_count                      integer,
    archived                          boolean,
    pinned                            boolean,
    raw                               jsonb,
    synced_at                         timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE rvbbit.hermes_sessions IS
    'Mirror of Hermes'' sqlite `sessions` (system_prompt body omitted) from every store: `default` = /opt/data/state.db, otherwise a multiplex profile (dashboard-studio, …) = /opt/data/profiles/<store>/state.db. Join rvbbit.calliope_sessions.hermes_session_id = id for the notebook.';
ALTER TABLE rvbbit.hermes_sessions ADD COLUMN IF NOT EXISTS store text NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS hermes_sessions_activity_idx ON rvbbit.hermes_sessions (last_activity_at DESC);
CREATE INDEX IF NOT EXISTS hermes_sessions_source_idx ON rvbbit.hermes_sessions (source, profile_name);

CREATE TABLE IF NOT EXISTS rvbbit.hermes_messages (
    id                 bigint NOT NULL,
    store              text NOT NULL DEFAULT 'default',
    session_id         text NOT NULL,
    role               text NOT NULL,
    content            text,
    content_chars      integer NOT NULL DEFAULT 0,
    content_sha256     text,
    tool_call_id       text,
    tool_name          text,
    tool_calls         jsonb,
    tool_call_count    integer NOT NULL DEFAULT 0,
    tool_call_names    text[],
    tool_arg_chars     integer NOT NULL DEFAULT 0,
    effect_disposition text,
    ts                 timestamptz NOT NULL,
    token_count        integer,
    finish_reason      text,
    reasoning_chars    integer NOT NULL DEFAULT 0,
    api_content_chars  integer NOT NULL DEFAULT 0,
    observed           boolean,
    active             boolean NOT NULL DEFAULT true,
    compacted          boolean NOT NULL DEFAULT false,
    display_kind       text,
    display_metadata   jsonb,
    synced_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (store, id)
);
-- Upgrade a table created before the store column existed: sqlite ids are
-- per store, so the key has to be (store, id).
ALTER TABLE rvbbit.hermes_messages ADD COLUMN IF NOT EXISTS store text NOT NULL DEFAULT 'default';
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint c
         WHERE c.conrelid = 'rvbbit.hermes_messages'::regclass
           AND c.contype = 'p'
           AND (SELECT array_agg(a.attname::text ORDER BY k.ord)
                  FROM unnest(c.conkey) WITH ORDINALITY k(attnum, ord)
                  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum) = ARRAY['id']
    ) THEN
        ALTER TABLE rvbbit.hermes_messages DROP CONSTRAINT hermes_messages_pkey;
        ALTER TABLE rvbbit.hermes_messages ADD PRIMARY KEY (store, id);
    END IF;
END;
$$;
COMMENT ON TABLE rvbbit.hermes_messages IS
    'Mirror of Hermes'' sqlite `messages` (all stores; ids are per store): the full transcript incl. tool results and tool_calls JSON. active/compacted flip on compaction, so rows are re-upserted for recently active sessions.';
CREATE INDEX IF NOT EXISTS hermes_messages_session_idx ON rvbbit.hermes_messages (session_id, id);
CREATE INDEX IF NOT EXISTS hermes_messages_ts_idx ON rvbbit.hermes_messages (ts);
CREATE INDEX IF NOT EXISTS hermes_messages_big_sha_idx ON rvbbit.hermes_messages (content_sha256)
    WHERE content_chars >= 2000;

CREATE TABLE IF NOT EXISTS rvbbit.hermes_model_usage (
    session_id         text NOT NULL,
    store              text NOT NULL DEFAULT 'default',
    model              text NOT NULL,
    billing_provider   text NOT NULL DEFAULT '',
    billing_base_url   text NOT NULL DEFAULT '',
    billing_mode       text NOT NULL DEFAULT '',
    task               text NOT NULL DEFAULT '',
    api_call_count     integer NOT NULL DEFAULT 0,
    input_tokens       bigint NOT NULL DEFAULT 0,
    output_tokens      bigint NOT NULL DEFAULT 0,
    cache_read_tokens  bigint NOT NULL DEFAULT 0,
    cache_write_tokens bigint NOT NULL DEFAULT 0,
    reasoning_tokens   bigint NOT NULL DEFAULT 0,
    estimated_cost_usd double precision NOT NULL DEFAULT 0,
    actual_cost_usd    double precision NOT NULL DEFAULT 0,
    cost_status        text,
    cost_source        text,
    first_seen         timestamptz,
    last_seen          timestamptz,
    synced_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, model, billing_provider, billing_base_url, billing_mode, task)
);
ALTER TABLE rvbbit.hermes_model_usage ADD COLUMN IF NOT EXISTS store text NOT NULL DEFAULT 'default';
COMMENT ON TABLE rvbbit.hermes_model_usage IS
    'Mirror of Hermes'' `session_model_usage`: token totals per session × model × task ('''' = main turn, compression, vision, title_generation, approval).';

CREATE TABLE IF NOT EXISTS rvbbit.hermes_api_calls (
    id                bigserial PRIMARY KEY,
    line_hash         text NOT NULL UNIQUE,
    store             text NOT NULL DEFAULT 'default',
    session_id        text,
    call_no           integer,
    ts                timestamptz NOT NULL,
    model             text,
    provider          text,
    route_provider    text,
    route_model       text,
    input_tokens      bigint,
    output_tokens     bigint,
    total_tokens      bigint,
    cache_read_tokens bigint,
    latency_ms        integer,
    raw               text
);
ALTER TABLE rvbbit.hermes_api_calls ADD COLUMN IF NOT EXISTS store text NOT NULL DEFAULT 'default';
COMMENT ON TABLE rvbbit.hermes_api_calls IS
    'One row per provider request Hermes made, parsed from agent.log "API call #N" lines: real prompt size (input_tokens), cached prefix, output, latency. route_* is the model behind the alias at ingest time (rvbbit.calliope_model_routes).';
CREATE INDEX IF NOT EXISTS hermes_api_calls_session_idx ON rvbbit.hermes_api_calls (session_id, ts);
CREATE INDEX IF NOT EXISTS hermes_api_calls_ts_idx ON rvbbit.hermes_api_calls (ts);

CREATE TABLE IF NOT EXISTS rvbbit.hermes_context_events (
    id         bigserial PRIMARY KEY,
    line_hash  text NOT NULL UNIQUE,
    store      text NOT NULL DEFAULT 'default',
    session_id text,
    ts         timestamptz NOT NULL,
    kind       text NOT NULL,
    details    jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw        text
);
ALTER TABLE rvbbit.hermes_context_events ADD COLUMN IF NOT EXISTS store text NOT NULL DEFAULT 'default';
COMMENT ON TABLE rvbbit.hermes_context_events IS
    'Context-economy events parsed from agent.log: turn_started/turn_ended, compression_started/done/blocked, tool_completed (result chars + seconds), tool_result_truncated, tool_search.';
CREATE INDEX IF NOT EXISTS hermes_context_events_session_idx ON rvbbit.hermes_context_events (session_id, ts);
CREATE INDEX IF NOT EXISTS hermes_context_events_kind_idx ON rvbbit.hermes_context_events (kind, ts);

CREATE TABLE IF NOT EXISTS rvbbit.hermes_mirror_state (
    source     text PRIMARY KEY,
    position   bigint NOT NULL DEFAULT 0,
    identity   text,
    details    jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE rvbbit.hermes_mirror_state IS
    'Ingest watermarks: agent.log byte offset + first-line identity (rotation detection), max mirrored message id, last session sync.';

-- ── Convenience views ───────────────────────────────────────────────────────

-- Every provider call with what changed since the previous call of the same
-- session: prompt growth, cache hit share, and whether it opened a turn.
CREATE OR REPLACE VIEW rvbbit.hermes_session_calls AS
SELECT c.id,
       c.session_id,
       cs.id                                   AS calliope_session_id,
       c.ts,
       c.call_no,
       c.call_no = 1                           AS opens_turn,
       c.model,
       c.route_model,
       c.input_tokens,
       c.output_tokens,
       c.cache_read_tokens,
       CASE WHEN c.input_tokens > 0
            THEN round(100.0 * coalesce(c.cache_read_tokens, 0) / c.input_tokens, 1) END AS cache_pct,
       c.input_tokens - lag(c.input_tokens) OVER w AS input_delta,
       lag(c.ts) OVER w                        AS previous_call_ts,
       c.latency_ms,
       c.store
  FROM rvbbit.hermes_api_calls c
  LEFT JOIN rvbbit.calliope_sessions cs ON cs.hermes_session_id = c.session_id
WINDOW w AS (PARTITION BY c.session_id ORDER BY c.ts, c.id);
COMMENT ON VIEW rvbbit.hermes_session_calls IS
    'agent.log API calls per session with input_delta vs the previous call and cache_pct; call_no=1 opens a Hermes turn (a Calliope hop).';

-- The transcript with a deterministic classification of each row.
CREATE OR REPLACE VIEW rvbbit.hermes_message_ledger AS
SELECT m.id,
       m.session_id,
       cs.id                                   AS calliope_session_id,
       m.ts,
       m.role,
       m.tool_name,
       CASE
           WHEN m.content LIKE '[CONTEXT COMPACTION%'                    THEN 'compaction_summary'
           WHEN m.content LIKE '[visual self-check%'                     THEN 'visual_self_check'
           WHEN m.content LIKE 'Review the conversation above and update the skill library%'
                                                                        THEN 'skill_curator_prompt'
           WHEN m.role = 'user'                                          THEN 'user'
           WHEN m.role = 'assistant' AND m.tool_call_count > 0           THEN 'tool_call'
           WHEN m.role = 'assistant'                                     THEN 'assistant_text'
           WHEN m.role = 'tool' AND m.tool_name LIKE 'mcp__%'            THEN 'mcp_result'
           WHEN m.role = 'tool' AND m.tool_name = 'tool_describe'        THEN 'tool_describe'
           WHEN m.role = 'tool'                                          THEN 'tool_result'
           ELSE m.role
       END                                     AS kind,
       m.content_chars,
       m.tool_arg_chars,
       greatest(m.content_chars, m.tool_arg_chars) AS payload_chars,
       m.tool_call_names,
       m.finish_reason,
       m.active,
       m.compacted,
       m.content_sha256,
       CASE WHEN m.content_chars >= 2000
            THEN count(*) OVER (PARTITION BY m.content_sha256) END AS same_content_seen,
       m.store
  FROM rvbbit.hermes_messages m
  LEFT JOIN rvbbit.calliope_sessions cs ON cs.hermes_session_id = m.session_id;
COMMENT ON VIEW rvbbit.hermes_message_ledger IS
    'hermes_messages with a deterministic kind per row and payload_chars (content or tool-call args, whichever is the payload); same_content_seen counts identical ≥2k-char payloads across the store (re-reads and replays).';

-- One row per session: what happened to its context.
CREATE OR REPLACE VIEW rvbbit.hermes_session_overview AS
WITH calls AS (
    SELECT session_id,
           count(*)                                              AS api_calls,
           max(input_tokens)                                     AS max_input_tokens,
           round(avg(input_tokens))                              AS avg_input_tokens,
           min(input_tokens) FILTER (WHERE call_no = 1)          AS min_turn_open_tokens,
           sum(input_tokens)                                     AS sum_input_tokens,
           sum(output_tokens)                                    AS sum_output_tokens,
           round(100.0 * sum(coalesce(cache_read_tokens, 0)) / nullif(sum(input_tokens), 0), 1) AS cache_pct,
           count(*) FILTER (WHERE call_no = 1)                   AS hops
      FROM rvbbit.hermes_api_calls
     GROUP BY session_id
), events AS (
    SELECT session_id,
           count(*) FILTER (WHERE kind = 'compression_done')      AS compactions,
           count(*) FILTER (WHERE kind = 'compression_blocked')   AS compaction_blocked,
           count(*) FILTER (WHERE kind = 'tool_result_truncated') AS truncated_results,
           max((details ->> 'chars')::bigint) FILTER (WHERE kind = 'tool_completed') AS largest_tool_result_chars
      FROM rvbbit.hermes_context_events
     GROUP BY session_id
), msgs AS (
    SELECT session_id,
           count(*)                                               AS rows_total,
           count(*) FILTER (WHERE active)                         AS rows_active,
           count(*) FILTER (WHERE compacted)                      AS rows_compacted,
           count(*) FILTER (WHERE role = 'user' AND content NOT LIKE '[%') AS user_messages,
           count(DISTINCT content_sha256) FILTER (WHERE role = 'user' AND content NOT LIKE '[%') AS distinct_user_messages,
           sum(content_chars) FILTER (WHERE role = 'tool')        AS tool_result_chars,
           max(greatest(content_chars, tool_arg_chars))           AS largest_payload_chars
      FROM rvbbit.hermes_messages
     GROUP BY session_id
), usage AS (
    SELECT session_id,
           sum(input_tokens) FILTER (WHERE task = '')                  AS main_input_tokens,
           sum(input_tokens) FILTER (WHERE task <> '')                 AS aux_input_tokens,
           jsonb_object_agg(task, api_call_count) FILTER (WHERE task <> '') AS aux_calls
      FROM rvbbit.hermes_model_usage
     GROUP BY session_id
)
SELECT s.id                                    AS session_id,
       cs.id                                   AS calliope_session_id,
       cs.title                                AS calliope_title,
       s.title,
       s.source,
       s.profile_name,
       s.model,
       s.started_at,
       s.last_activity_at,
       s.compression_ineffective_count,
       calls.api_calls,
       calls.hops,
       calls.min_turn_open_tokens              AS base_prompt_tokens,
       calls.avg_input_tokens,
       calls.max_input_tokens,
       calls.sum_input_tokens,
       calls.sum_output_tokens,
       calls.cache_pct,
       events.compactions,
       events.compaction_blocked,
       events.truncated_results,
       events.largest_tool_result_chars,
       msgs.rows_total,
       msgs.rows_active,
       msgs.rows_compacted,
       msgs.user_messages,
       msgs.user_messages - msgs.distinct_user_messages AS replayed_user_messages,
       msgs.tool_result_chars,
       msgs.largest_payload_chars,
       usage.main_input_tokens,
       usage.aux_input_tokens,
       usage.aux_calls,
       s.store
  FROM rvbbit.hermes_sessions s
  LEFT JOIN rvbbit.calliope_sessions cs ON cs.hermes_session_id = s.id
  LEFT JOIN calls  ON calls.session_id  = s.id
  LEFT JOIN events ON events.session_id = s.id
  LEFT JOIN msgs   ON msgs.session_id   = s.id
  LEFT JOIN usage  ON usage.session_id  = s.id;
COMMENT ON VIEW rvbbit.hermes_session_overview IS
    'One row per Hermes session: base_prompt_tokens (smallest turn-opening call), avg/max prompt, cache share, compactions (done/blocked), replayed user messages, largest payload, main vs auxiliary (compression/vision/title/curator) tokens.';
