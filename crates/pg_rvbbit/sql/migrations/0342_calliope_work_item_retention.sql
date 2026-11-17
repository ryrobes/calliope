-- 0342: Work Inbox retention
--
-- The Inbox is an attention surface, not an append-only activity log. Durable
-- goals and real blockers remain until resolved, while routine receipts and
-- suggestions age out. An explicit user stop is never actionable work.

ALTER TABLE rvbbit.calliope_work_items
    ADD COLUMN IF NOT EXISTS expires_at timestamptz;

CREATE INDEX IF NOT EXISTS calliope_work_items_expires_idx
    ON rvbbit.calliope_work_items (expires_at)
    WHERE state IN ('unread','seen') AND expires_at IS NOT NULL;

DELETE FROM rvbbit.calliope_work_items wi
USING rvbbit.calliope_turns t
WHERE wi.source = 'calliope_turn'
  AND wi.source_ref = t.id::text
  AND t.status = 'interrupted'
  AND t.error = 'Stopped by you.';

UPDATE rvbbit.calliope_work_items
SET expires_at = created_at + CASE
        WHEN kind = 'result' THEN interval '7 days'
        ELSE interval '14 days'
    END
WHERE expires_at IS NULL
  AND kind IN ('result','suggestion');

UPDATE rvbbit.calliope_work_items
SET expires_at = created_at + interval '3 days'
WHERE expires_at IS NULL
  AND source = 'calliope_turn';

UPDATE rvbbit.calliope_work_items
SET state = 'dismissed',
    updated_at = now(),
    resolved_at = coalesce(resolved_at,now())
WHERE state IN ('unread','seen')
  AND expires_at IS NOT NULL
  AND expires_at <= now();

COMMENT ON COLUMN rvbbit.calliope_work_items.expires_at IS
    'Optional attention-window expiry. Expired open items are automatically dismissed; durable goals and genuine blockers may leave this null.';
