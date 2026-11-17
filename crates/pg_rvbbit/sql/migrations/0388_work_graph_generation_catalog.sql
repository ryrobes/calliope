-- 0388: separate the detailed AS-OF timeline from its ordinary SQL catalog.
--
-- 0387 temporarily reused work_graph_snapshots for the route-keyed RVBBIT
-- table. That made detailed time travel possible, but removed the ordinary
-- Postgres relation used to chart counts across capture times. Keep the two
-- concerns explicit:
--
--   work_graph_timeline  - current route state plus retained RVBBIT generations
--   work_graph_snapshots - one compact, heap-backed summary per capture
--
-- The raw hourly rows written by 0386 remain preserved in
-- work_graph_snapshots_0386 and seed the compact catalog once.

DO $work_graph_relation_names$
BEGIN
    IF to_regclass('rvbbit.work_graph_timeline') IS NULL THEN
        IF to_regclass('rvbbit.work_graph_snapshots') IS NULL
           OR NOT rvbbit.is_rvbbit_table('rvbbit.work_graph_snapshots'::regclass)
        THEN
            RAISE EXCEPTION
                'cannot create work graph catalog: accelerated rvbbit.work_graph_snapshots is missing';
        END IF;

        ALTER TABLE rvbbit.work_graph_snapshots
            RENAME TO work_graph_timeline;
    END IF;

    IF NOT rvbbit.is_rvbbit_table('rvbbit.work_graph_timeline'::regclass) THEN
        RAISE EXCEPTION 'rvbbit.work_graph_timeline is not RVBBIT-backed';
    END IF;

    -- A table rename does not rename its primary-key constraint or index.
    -- Free the canonical snapshots name before creating the heap catalog.
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'rvbbit.work_graph_timeline'::regclass
          AND conname = 'work_graph_snapshots_pkey'
    ) THEN
        ALTER TABLE rvbbit.work_graph_timeline
            RENAME CONSTRAINT work_graph_snapshots_pkey
            TO work_graph_timeline_pkey;
    END IF;
END
$work_graph_relation_names$;

CREATE TABLE rvbbit.work_graph_snapshots (
    snapshot_at                            timestamptz PRIMARY KEY,
    capture_kind                          text NOT NULL,
    generation                            bigint,
    generation_committed_at               timestamptz,
    total_items                           bigint NOT NULL,
    active_items                          bigint,
    hidden_items                          bigint,
    changed_routes                        bigint,
    deleted_routes                        bigint,
    rows_written                          bigint,
    row_groups_written                    integer,
    visible_rows_estimate                 bigint,
    tombstones_visible                    bigint,
    counts_by_column_state                jsonb NOT NULL DEFAULT '{}'::jsonb,
    counts_by_item_kind                   jsonb NOT NULL DEFAULT '{}'::jsonb,
    counts_by_item_kind_and_column_state  jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT work_graph_snapshots_capture_kind_check
        CHECK (capture_kind IN ('legacy_hourly', 'rvbbit_generation'))
);

CREATE INDEX work_graph_snapshots_generation_idx
    ON rvbbit.work_graph_snapshots (generation DESC)
    WHERE generation IS NOT NULL;

COMMENT ON TABLE rvbbit.work_graph_snapshots IS
    'Heap-backed table of contents for work_graph timeline captures; one compact count summary per capture.';
COMMENT ON TABLE rvbbit.work_graph_timeline IS
    'Route-keyed work_graph state whose retained RVBBIT generations provide detailed AS-OF history.';

-- Preserve the observable hourly history while avoiding another full copy.
WITH legacy AS MATERIALIZED (
    SELECT
        snapshot_at,
        coalesce(item_kind, 'unknown') AS item_kind,
        coalesce(column_state, 'unknown') AS column_state
    FROM rvbbit.work_graph_snapshots_0386
),
totals AS (
    SELECT snapshot_at, count(*)::bigint AS total_items
    FROM legacy
    GROUP BY snapshot_at
),
column_counts AS (
    SELECT snapshot_at, jsonb_object_agg(column_state, item_count) AS counts
    FROM (
        SELECT snapshot_at, column_state, count(*)::bigint AS item_count
        FROM legacy
        GROUP BY snapshot_at, column_state
    ) grouped
    GROUP BY snapshot_at
),
kind_counts AS (
    SELECT snapshot_at, jsonb_object_agg(item_kind, item_count) AS counts
    FROM (
        SELECT snapshot_at, item_kind, count(*)::bigint AS item_count
        FROM legacy
        GROUP BY snapshot_at, item_kind
    ) grouped
    GROUP BY snapshot_at
),
kind_state_counts AS (
    SELECT snapshot_at, jsonb_object_agg(item_kind, state_counts) AS counts
    FROM (
        SELECT snapshot_at, item_kind,
               jsonb_object_agg(column_state, item_count) AS state_counts
        FROM (
            SELECT snapshot_at, item_kind, column_state,
                   count(*)::bigint AS item_count
            FROM legacy
            GROUP BY snapshot_at, item_kind, column_state
        ) grouped
        GROUP BY snapshot_at, item_kind
    ) nested
    GROUP BY snapshot_at
)
INSERT INTO rvbbit.work_graph_snapshots (
    snapshot_at,
    capture_kind,
    total_items,
    counts_by_column_state,
    counts_by_item_kind,
    counts_by_item_kind_and_column_state
)
SELECT
    totals.snapshot_at,
    'legacy_hourly',
    totals.total_items,
    coalesce(column_counts.counts, '{}'::jsonb),
    coalesce(kind_counts.counts, '{}'::jsonb),
    coalesce(kind_state_counts.counts, '{}'::jsonb)
FROM totals
LEFT JOIN column_counts USING (snapshot_at)
LEFT JOIN kind_counts USING (snapshot_at)
LEFT JOIN kind_state_counts USING (snapshot_at)
ON CONFLICT (snapshot_at) DO NOTHING;

CREATE OR REPLACE VIEW rvbbit.work_graph_snapshot_counts AS
SELECT
    snapshot_at,
    generation,
    capture_kind,
    'all'::text AS grouping_level,
    NULL::text AS item_kind,
    NULL::text AS column_state,
    total_items AS item_count
FROM rvbbit.work_graph_snapshots

UNION ALL

SELECT
    snapshot_at,
    generation,
    capture_kind,
    'item_kind'::text,
    counts.key,
    NULL::text,
    counts.value::bigint
FROM rvbbit.work_graph_snapshots
CROSS JOIN LATERAL jsonb_each_text(counts_by_item_kind) AS counts

UNION ALL

SELECT
    snapshot_at,
    generation,
    capture_kind,
    'column_state'::text,
    NULL::text,
    counts.key,
    counts.value::bigint
FROM rvbbit.work_graph_snapshots
CROSS JOIN LATERAL jsonb_each_text(counts_by_column_state) AS counts

UNION ALL

SELECT
    snapshot_at,
    generation,
    capture_kind,
    'item_kind_column_state'::text,
    kinds.key,
    states.key,
    states.value::bigint
FROM rvbbit.work_graph_snapshots
CROSS JOIN LATERAL jsonb_each(counts_by_item_kind_and_column_state) AS kinds
CROSS JOIN LATERAL jsonb_each_text(kinds.value) AS states;

COMMENT ON VIEW rvbbit.work_graph_snapshot_counts IS
    'Normalized count buckets from work_graph_snapshots for ordinary GROUP BY and chart queries.';

CREATE OR REPLACE FUNCTION rvbbit.work_graph_snapshot_tick()
RETURNS jsonb
LANGUAGE plpgsql
AS $work_graph_merge$
DECLARE
    v_stamp timestamptz := clock_timestamp();
    v_upserted integer := 0;
    v_deleted integer := 0;
    v_rows integer := 0;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rvbbit.work_graph_snapshot_tick', 0));

    WITH applied AS (
        INSERT INTO rvbbit.work_graph_timeline AS snap (
            snapshot_at,
            item_id,
            item_kind,
            title,
            column_state,
            detail_state,
            owner_email,
            agents,
            session_id,
            started_at,
            updated_at,
            ref,
            summary,
            parent_item_id,
            last_activity_at,
            active_now,
            state_reason,
            default_hidden,
            attention_rank,
            surface_count,
            display_title,
            idle_seconds,
            child_thread_count,
            active_child_count,
            related_signal_count,
            route
        )
        SELECT
            v_stamp,
            graph.item_id,
            graph.item_kind,
            graph.title,
            graph.column_state,
            graph.detail_state,
            graph.owner_email,
            graph.agents,
            graph.session_id,
            graph.started_at,
            graph.updated_at,
            graph.ref,
            graph.summary,
            graph.parent_item_id,
            graph.last_activity_at,
            graph.active_now,
            graph.state_reason,
            graph.default_hidden,
            graph.attention_rank,
            graph.surface_count,
            graph.display_title,
            graph.idle_seconds,
            graph.child_thread_count,
            graph.active_child_count,
            graph.related_signal_count,
            graph.route
        FROM rvbbit.work_graph AS graph
        ON CONFLICT (route) DO UPDATE
        SET snapshot_at = EXCLUDED.snapshot_at,
            item_id = EXCLUDED.item_id,
            item_kind = EXCLUDED.item_kind,
            title = EXCLUDED.title,
            column_state = EXCLUDED.column_state,
            detail_state = EXCLUDED.detail_state,
            owner_email = EXCLUDED.owner_email,
            agents = EXCLUDED.agents,
            session_id = EXCLUDED.session_id,
            started_at = EXCLUDED.started_at,
            updated_at = EXCLUDED.updated_at,
            ref = EXCLUDED.ref,
            summary = EXCLUDED.summary,
            parent_item_id = EXCLUDED.parent_item_id,
            last_activity_at = EXCLUDED.last_activity_at,
            active_now = EXCLUDED.active_now,
            state_reason = EXCLUDED.state_reason,
            default_hidden = EXCLUDED.default_hidden,
            attention_rank = EXCLUDED.attention_rank,
            surface_count = EXCLUDED.surface_count,
            display_title = EXCLUDED.display_title,
            idle_seconds = EXCLUDED.idle_seconds,
            child_thread_count = EXCLUDED.child_thread_count,
            active_child_count = EXCLUDED.active_child_count,
            related_signal_count = EXCLUDED.related_signal_count
        WHERE ROW(
            snap.item_id,
            snap.item_kind,
            snap.title,
            snap.column_state,
            snap.detail_state,
            snap.owner_email,
            snap.agents,
            snap.session_id,
            snap.started_at,
            snap.updated_at,
            snap.ref,
            snap.summary,
            snap.parent_item_id,
            snap.last_activity_at,
            snap.active_now,
            snap.state_reason,
            snap.default_hidden,
            snap.attention_rank,
            snap.surface_count,
            snap.display_title,
            snap.child_thread_count,
            snap.active_child_count,
            snap.related_signal_count
        ) IS DISTINCT FROM ROW(
            EXCLUDED.item_id,
            EXCLUDED.item_kind,
            EXCLUDED.title,
            EXCLUDED.column_state,
            EXCLUDED.detail_state,
            EXCLUDED.owner_email,
            EXCLUDED.agents,
            EXCLUDED.session_id,
            EXCLUDED.started_at,
            EXCLUDED.updated_at,
            EXCLUDED.ref,
            EXCLUDED.summary,
            EXCLUDED.parent_item_id,
            EXCLUDED.last_activity_at,
            EXCLUDED.active_now,
            EXCLUDED.state_reason,
            EXCLUDED.default_hidden,
            EXCLUDED.attention_rank,
            EXCLUDED.surface_count,
            EXCLUDED.display_title,
            EXCLUDED.child_thread_count,
            EXCLUDED.active_child_count,
            EXCLUDED.related_signal_count
        )
        RETURNING 1
    )
    SELECT count(*)::integer INTO v_upserted FROM applied;

    DELETE FROM rvbbit.work_graph_timeline AS snap
    WHERE NOT EXISTS (
        SELECT 1
        FROM rvbbit.work_graph AS graph
        WHERE graph.route = snap.route
    );
    GET DIAGNOSTICS v_deleted = ROW_COUNT;

    SELECT count(*)::integer INTO v_rows
    FROM rvbbit.work_graph_timeline;

    RETURN jsonb_build_object(
        'status', 'merged',
        'snapshot_at', v_stamp,
        'upserted', v_upserted,
        'deleted', v_deleted,
        'rows', v_rows
    );
END
$work_graph_merge$;

COMMENT ON FUNCTION rvbbit.work_graph_snapshot_tick() IS
    'Merges real route-state changes into work_graph_timeline; volatile idle_seconds alone does not create a generation.';

CREATE OR REPLACE FUNCTION rvbbit.work_graph_snapshot_catalog_tick(
    p_snapshot_at timestamptz,
    p_generation bigint,
    p_merge jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
AS $work_graph_catalog$
DECLARE
    v_pruned integer := 0;
    v_total bigint := 0;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('rvbbit.work_graph_snapshot_catalog_tick', 0));

    WITH graph AS MATERIALIZED (
        SELECT
            coalesce(item_kind, 'unknown') AS item_kind,
            coalesce(column_state, 'unknown') AS column_state,
            active_now,
            default_hidden
        FROM rvbbit.work_graph_timeline
    ),
    totals AS (
        SELECT
            count(*)::bigint AS total_items,
            count(*) FILTER (WHERE active_now)::bigint AS active_items,
            count(*) FILTER (WHERE default_hidden)::bigint AS hidden_items
        FROM graph
    ),
    column_counts AS (
        SELECT coalesce(jsonb_object_agg(column_state, item_count), '{}'::jsonb) AS counts
        FROM (
            SELECT column_state, count(*)::bigint AS item_count
            FROM graph
            GROUP BY column_state
        ) grouped
    ),
    kind_counts AS (
        SELECT coalesce(jsonb_object_agg(item_kind, item_count), '{}'::jsonb) AS counts
        FROM (
            SELECT item_kind, count(*)::bigint AS item_count
            FROM graph
            GROUP BY item_kind
        ) grouped
    ),
    kind_state_counts AS (
        SELECT coalesce(jsonb_object_agg(item_kind, state_counts), '{}'::jsonb) AS counts
        FROM (
            SELECT item_kind, jsonb_object_agg(column_state, item_count) AS state_counts
            FROM (
                SELECT item_kind, column_state, count(*)::bigint AS item_count
                FROM graph
                GROUP BY item_kind, column_state
            ) grouped
            GROUP BY item_kind
        ) nested
    ),
    generation_state AS (
        SELECT
            g.committed_at,
            g.n_rows,
            g.n_row_groups,
            timeline.visible_rows_estimate,
            timeline.tombstones_visible
        FROM rvbbit.generations g
        LEFT JOIN rvbbit.time_travel_timeline('rvbbit.work_graph_timeline'::regclass) timeline
          ON timeline.generation = g.generation
        WHERE g.table_oid = 'rvbbit.work_graph_timeline'::regclass
          AND g.generation = p_generation
    ),
    recorded AS (
        INSERT INTO rvbbit.work_graph_snapshots (
            snapshot_at,
            capture_kind,
            generation,
            generation_committed_at,
            total_items,
            active_items,
            hidden_items,
            changed_routes,
            deleted_routes,
            rows_written,
            row_groups_written,
            visible_rows_estimate,
            tombstones_visible,
            counts_by_column_state,
            counts_by_item_kind,
            counts_by_item_kind_and_column_state
        )
        SELECT
            p_snapshot_at,
            'rvbbit_generation',
            p_generation,
            generation_state.committed_at,
            totals.total_items,
            totals.active_items,
            totals.hidden_items,
            coalesce((p_merge ->> 'upserted')::bigint, 0),
            coalesce((p_merge ->> 'deleted')::bigint, 0),
            coalesce(generation_state.n_rows, 0),
            coalesce(generation_state.n_row_groups, 0),
            coalesce(generation_state.visible_rows_estimate, totals.total_items),
            coalesce(generation_state.tombstones_visible, 0),
            column_counts.counts,
            kind_counts.counts,
            kind_state_counts.counts
        FROM totals
        CROSS JOIN column_counts
        CROSS JOIN kind_counts
        CROSS JOIN kind_state_counts
        LEFT JOIN generation_state ON true
        ON CONFLICT (snapshot_at) DO UPDATE
        SET capture_kind = EXCLUDED.capture_kind,
            generation = EXCLUDED.generation,
            generation_committed_at = EXCLUDED.generation_committed_at,
            total_items = EXCLUDED.total_items,
            active_items = EXCLUDED.active_items,
            hidden_items = EXCLUDED.hidden_items,
            changed_routes = EXCLUDED.changed_routes,
            deleted_routes = EXCLUDED.deleted_routes,
            rows_written = EXCLUDED.rows_written,
            row_groups_written = EXCLUDED.row_groups_written,
            visible_rows_estimate = EXCLUDED.visible_rows_estimate,
            tombstones_visible = EXCLUDED.tombstones_visible,
            counts_by_column_state = EXCLUDED.counts_by_column_state,
            counts_by_item_kind = EXCLUDED.counts_by_item_kind,
            counts_by_item_kind_and_column_state = EXCLUDED.counts_by_item_kind_and_column_state
        RETURNING total_items
    )
    SELECT total_items INTO v_total FROM recorded;

    DELETE FROM rvbbit.work_graph_snapshots
    WHERE snapshot_at < now() - interval '30 days';
    GET DIAGNOSTICS v_pruned = ROW_COUNT;

    RETURN jsonb_build_object(
        'status', 'recorded',
        'snapshot_at', p_snapshot_at,
        'generation', p_generation,
        'total_items', v_total,
        'pruned', v_pruned
    );
END
$work_graph_catalog$;

COMMENT ON FUNCTION rvbbit.work_graph_snapshot_catalog_tick(timestamptz, bigint, jsonb) IS
    'Records one compact heap-backed count summary for an RVBBIT work_graph generation.';

CREATE OR REPLACE PROCEDURE rvbbit.work_graph_snapshot_run()
LANGUAGE plpgsql
AS $work_graph_run$
DECLARE
    v_merge jsonb;
    v_refresh jsonb;
    v_catalog jsonb;
    v_delete_generation bigint := 0;
    v_published_generation bigint := 0;
    v_capture_at timestamptz;
BEGIN
    SELECT rvbbit.work_graph_snapshot_tick() INTO v_merge;

    -- The merge must commit before the incremental exporter can see it.
    COMMIT AND CHAIN;

    SELECT rvbbit.refresh_acceleration(
        'rvbbit.work_graph_timeline'::regclass,
        false
    ) INTO v_refresh;

    SELECT coalesce(max(deleted_generation), 0)
      INTO v_delete_generation
      FROM rvbbit.delete_log
     WHERE table_oid = 'rvbbit.work_graph_timeline'::regclass;

    SELECT coalesce(max(generation), 0)
      INTO v_published_generation
      FROM rvbbit.generations
     WHERE table_oid = 'rvbbit.work_graph_timeline'::regclass;

    IF v_delete_generation > v_published_generation THEN
        INSERT INTO rvbbit.generations (
            table_oid, generation, n_rows, n_row_groups
        ) VALUES (
            'rvbbit.work_graph_timeline'::regclass,
            v_delete_generation,
            0,
            0
        )
        ON CONFLICT (table_oid, generation) DO NOTHING;

        UPDATE rvbbit.tables
           SET next_generation = greatest(next_generation, v_delete_generation + 1)
         WHERE table_oid = 'rvbbit.work_graph_timeline'::regclass;

        UPDATE rvbbit.acceleration_state
           SET last_refresh_generation = greatest(
                   coalesce(last_refresh_generation, 0),
                   v_delete_generation
               ),
               updated_at = clock_timestamp()
         WHERE table_oid = 'rvbbit.work_graph_timeline'::regclass;

        v_refresh := v_refresh || jsonb_build_object(
            'delete_generation', v_delete_generation,
            'delete_generation_rows', 0
        );
    END IF;

    SELECT coalesce(max(generation), 0)
      INTO v_published_generation
      FROM rvbbit.generations
     WHERE table_oid = 'rvbbit.work_graph_timeline'::regclass;

    v_capture_at := date_bin(
        '15 minutes',
        clock_timestamp(),
        timestamptz '2000-01-01 00:00:00+00'
    );

    SELECT rvbbit.work_graph_snapshot_catalog_tick(
        v_capture_at,
        v_published_generation,
        v_merge
    ) INTO v_catalog;

    RAISE LOG 'work graph merge %, RVBBIT refresh %, catalog %',
        v_merge, v_refresh, v_catalog;
END
$work_graph_run$;

COMMENT ON PROCEDURE rvbbit.work_graph_snapshot_run() IS
    '15-minute work-graph capture: merge real route changes, publish retained RVBBIT delta, then record ordinary SQL counts.';
