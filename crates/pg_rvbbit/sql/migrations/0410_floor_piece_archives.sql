-- 0410: "set aside" for Floor notices. Archived-by-hand is its own state:
-- not Done (no work happened) and not deletion (nothing on the Floor ever
-- vanishes without a receipt). One row per piece; who set it aside and when
-- is the receipt, and repeated dismissals double as curation signal for what
-- the Floor should stop minting. Restore keeps the last dismissal on the row
-- rather than erasing it. The evolving rvbbit.work_graph view stays
-- untouched: the live Floor projection overlays this table at read time, and
-- history frames deliberately ignore it so AS-OF views keep showing what the
-- Floor showed at the time. Stickiness is per piece: genuinely new evidence
-- arrives as a new row in its source table, so it mints a new item_id and
-- re-surfaces on its own merits.
CREATE TABLE IF NOT EXISTS rvbbit.floor_piece_archives (
    item_id text PRIMARY KEY,
    archived_by text NOT NULL,
    reason text,
    archived_at timestamptz NOT NULL DEFAULT now(),
    restored_at timestamptz,
    restored_by text
);

CREATE INDEX IF NOT EXISTS floor_piece_archives_active_idx
    ON rvbbit.floor_piece_archives (archived_at DESC)
    WHERE restored_at IS NULL;
