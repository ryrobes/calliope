-- 0393: honest replay accounting for the Hermes session overview.
--
-- The original replayed_user_messages (0334) counted duplicate user-row
-- CONTENT per session (rows minus distinct sha256). Forensics on the live
-- mirror showed that conflates three unrelated phenomena:
--   1. Version tombstones — compaction rewrites re-materialize surviving
--      rows and keep superseded copies (same content, same timestamp,
--      compacted/inactive). Bookkeeping, not traffic: nothing was re-sent.
--   2. True wire re-sends — same content at different timestamps. Almost
--      entirely machinery, in three species:
--        redelivery — the specialist-collaboration transport re-ships the
--                     full thread context each round (the costly one);
--        recurrence — persistent duty-cycle sessions re-invoking the same
--                     skill preamble (expected, benign);
--        recovery   — "continue exactly" retries after stream cuts /
--                     output-limit truncation (working as designed).
--   3. Actual human double-sends: two, all-time, across every session.
--
-- replayed_user_messages now means "true wire re-sends" (distinct moments
-- minus one, summed over duplicate content groups), and new columns expose
-- the split so the forensics UI can name each species instead of implying
-- users are mashing send.

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
), user_dups AS (
    SELECT session_id,
           sum(copies - moments)::bigint AS versioned_user_rows,
           sum(moments - 1)::bigint      AS wire_replays,
           (sum(moments - 1) FILTER (WHERE species = 'redelivery'))::bigint AS redelivery_replays,
           (sum(moments - 1) FILTER (WHERE species = 'recurrence'))::bigint AS recurrence_replays,
           (sum(moments - 1) FILTER (WHERE species = 'recovery'))::bigint   AS recovery_replays,
           (sum(moments - 1) FILTER (WHERE species = 'other'))::bigint      AS other_replays
      FROM (
        SELECT session_id, content_sha256,
               count(*)           AS copies,
               count(DISTINCT ts) AS moments,
               CASE
                   WHEN max(content) LIKE 'Calliope is speaking with %'
                     OR max(content) LIKE 'json:%Calliope is speaking with%'
                     OR max(content) LIKE 'Calliope delegated this bounded assignment%'
                       THEN 'redelivery'
                   WHEN max(content) LIKE '[IMPORTANT: The user has invoked%'
                       THEN 'recurrence'
                   WHEN max(content) LIKE '[System:%Continue exactly%'
                       THEN 'recovery'
                   ELSE 'other'
               END AS species
          FROM rvbbit.hermes_messages
         WHERE role = 'user'
         GROUP BY session_id, content_sha256
        HAVING count(*) > 1
      ) grouped
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
       coalesce(user_dups.wire_replays, 0)     AS replayed_user_messages,
       msgs.tool_result_chars,
       msgs.largest_payload_chars,
       usage.main_input_tokens,
       usage.aux_input_tokens,
       usage.aux_calls,
       s.store,
       coalesce(user_dups.versioned_user_rows, 0) AS versioned_user_rows,
       coalesce(user_dups.redelivery_replays, 0)  AS redelivery_replays,
       coalesce(user_dups.recurrence_replays, 0)  AS recurrence_replays,
       coalesce(user_dups.recovery_replays, 0)    AS recovery_replays,
       coalesce(user_dups.other_replays, 0)       AS other_replays
  FROM rvbbit.hermes_sessions s
  LEFT JOIN rvbbit.calliope_sessions cs ON cs.hermes_session_id = s.id
  LEFT JOIN calls     ON calls.session_id     = s.id
  LEFT JOIN events    ON events.session_id    = s.id
  LEFT JOIN msgs      ON msgs.session_id      = s.id
  LEFT JOIN user_dups ON user_dups.session_id = s.id
  LEFT JOIN usage     ON usage.session_id     = s.id;
COMMENT ON VIEW rvbbit.hermes_session_overview IS
  'Per-session Hermes forensics rollup. replayed_user_messages counts true '
  'wire re-sends (same user content at distinct timestamps); the *_replays '
  'columns split them by species (redelivery = collaboration transport, '
  'recurrence = repeated skill invocations, recovery = continue-after-cut), '
  'and versioned_user_rows counts compaction-rewrite tombstones that were '
  'never re-sent at all.';
