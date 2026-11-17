-- 0389: enforce the Work Inbox's global active-dedupe contract.
--
-- The original table constraint scopes uniqueness to owner and source, while
-- producers treat a non-null dedupe_key as globally unique among actionable
-- unread/seen items. Repair any historical duplicates deterministically before
-- making that producer assumption a database invariant. Completed and
-- dismissed history remains unconstrained.

WITH ranked_active AS (
    SELECT
        id,
        row_number() OVER (
            PARTITION BY dedupe_key
            ORDER BY created_at ASC, id ASC
        ) AS duplicate_rank
    FROM rvbbit.calliope_work_items
    WHERE dedupe_key IS NOT NULL
      AND state IN ('unread', 'seen')
)
DELETE FROM rvbbit.calliope_work_items AS work_item
USING ranked_active
WHERE work_item.id = ranked_active.id
  AND ranked_active.duplicate_rank > 1;

CREATE UNIQUE INDEX IF NOT EXISTS work_items_active_dedupe_idx
    ON rvbbit.calliope_work_items (dedupe_key)
    WHERE dedupe_key IS NOT NULL
      AND state IN ('unread', 'seen');

COMMENT ON INDEX rvbbit.work_items_active_dedupe_idx IS
    'At most one unread/seen Work Inbox item may own a non-null global dedupe key; terminal history may repeat it.';
