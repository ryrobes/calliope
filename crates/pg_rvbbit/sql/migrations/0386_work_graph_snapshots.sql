-- 0386: work-graph snapshots — the Floor's film reel.
--
-- Hourly captures of rvbbit.work_graph so the Floor can replay itself:
-- the activity timeline and the AS-OF scrubber both read from here. The
-- recorder must precede the replay — history not captured is history the
-- scrubber can never show — so this ships before the UI that uses it.
-- Append-only, ~600 rows/hour, 30-day retention. Registration rides the
-- operator's in-flight migrations.rs chain, same as 0382.

CREATE TABLE IF NOT EXISTS rvbbit.work_graph_snapshots (
    snapshot_at  timestamptz NOT NULL,
    item_id      text NOT NULL,
    item_kind    text,
    title        text,
    column_state text,
    detail_state text,
    owner_email  text,
    agents       text[],
    session_id   uuid,
    started_at   timestamptz,
    updated_at   timestamptz,
    ref          jsonb,
    PRIMARY KEY (snapshot_at, item_id)
);
CREATE INDEX IF NOT EXISTS work_graph_snapshots_item_idx
    ON rvbbit.work_graph_snapshots (item_id, snapshot_at DESC);

CREATE OR REPLACE FUNCTION rvbbit.work_graph_snapshot_tick()
RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
    captured integer;
    pruned integer;
    stamp timestamptz := date_trunc('hour', clock_timestamp());
BEGIN
    INSERT INTO rvbbit.work_graph_snapshots
        (snapshot_at, item_id, item_kind, title, column_state, detail_state,
         owner_email, agents, session_id, started_at, updated_at, ref)
    SELECT stamp, item_id, item_kind, title, column_state, detail_state,
           owner_email, agents, session_id, started_at, updated_at, ref
    FROM rvbbit.work_graph
    ON CONFLICT (snapshot_at, item_id) DO NOTHING;
    GET DIAGNOSTICS captured = ROW_COUNT;

    DELETE FROM rvbbit.work_graph_snapshots
     WHERE snapshot_at < now() - interval '30 days';
    GET DIAGNOSTICS pruned = ROW_COUNT;

    RETURN jsonb_build_object('snapshot_at', stamp,
                              'captured', captured, 'pruned', pruned);
END $$;
