-- 0365: Make orphan-file safety checks cheap under a large deferred-unlink
-- backlog.  The reaper probes these catalogs by exact physical path before it
-- unlinks anything; their existing indexes begin with table/layout identity
-- and cannot serve that lookup.

CREATE INDEX IF NOT EXISTS row_groups_path_idx
    ON rvbbit.row_groups (path);

CREATE INDEX IF NOT EXISTS row_group_variants_path_idx
    ON rvbbit.row_group_variants (path);

CREATE INDEX IF NOT EXISTS text_dictionaries_path_idx
    ON rvbbit.text_dictionaries (path);
