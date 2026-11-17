-- 0505: the Team page reads assistant messages by session without a heap walk.
--
-- The Team roster's tool-activity query (calliope.py tool_activity) joins every
-- visible session to rvbbit.hermes_messages on (session_id, store) and keeps
-- role = 'assistant' rows from the last 30 days. hermes_messages_session_idx
-- (session_id, id) made it read every message of each session from the heap and
-- discard most: on the AWS pilot (3.5 GB, 689k rows) 4.4 s warm and 7-21 s cold.
-- With this partial index: 0.30 s warm, 2.1 s cold (2026-11-11).
--
-- Migrations run inside one transaction, so this cannot be CONCURRENTLY: a
-- fresh build blocks writes to hermes_messages while it runs (seconds on a
-- typical install; the index is small, 7.5 MB on the pilot). To avoid that on
-- a large install, build it first by hand with the same name:
--   CREATE INDEX CONCURRENTLY IF NOT EXISTS hermes_messages_assistant_session_idx
--       ON rvbbit.hermes_messages (session_id, store, ts) WHERE role = 'assistant';
-- and this migration becomes a no-op.

CREATE INDEX IF NOT EXISTS hermes_messages_assistant_session_idx
    ON rvbbit.hermes_messages (session_id, store, ts)
    WHERE role = 'assistant';
