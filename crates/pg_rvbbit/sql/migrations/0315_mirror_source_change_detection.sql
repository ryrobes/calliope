-- 0315: source-side no-change gates for snapshot mirrors.
--
-- A normal dlt snapshot is intentionally TRUNCATE + INSERT. That is the right
-- correctness contract, but it also invalidates an otherwise-current RVBBIT
-- accelerator even when the remote snapshot did not change. Give explicitly
-- append-only sources a cheap preflight: count plus primary-key bounds travel
-- over the wire, and the full dlt load is skipped when that signature and the
-- local destination identity still match the last published snapshot.
--
-- This is opt-in. It must never be inferred from the presence of a primary key:
-- UPDATEs can preserve count and key bounds. Mutable sources remain `always`
-- unless they use incremental_upsert or a future exact source-token strategy.

ALTER TABLE rvbbit.mirror_tables
    ADD COLUMN IF NOT EXISTS change_detection text NOT NULL DEFAULT 'always';

ALTER TABLE rvbbit.mirror_tables
    DROP CONSTRAINT IF EXISTS mirror_tables_change_detection_check;
ALTER TABLE rvbbit.mirror_tables
    ADD CONSTRAINT mirror_tables_change_detection_check
    CHECK (change_detection IN ('always', 'append_only_key'));

ALTER TABLE rvbbit.mirror_tables
    DROP CONSTRAINT IF EXISTS mirror_tables_change_detection_contract_check;
ALTER TABLE rvbbit.mirror_tables
    ADD CONSTRAINT mirror_tables_change_detection_contract_check
    CHECK (
        change_detection = 'always'
        OR (
            change_detection = 'append_only_key'
            AND load_mode = 'snapshot'
            AND coalesce(cardinality(primary_key), 0) > 0
        )
    );

CREATE TABLE IF NOT EXISTS rvbbit.mirror_source_state (
    job_name text NOT NULL,
    source_table text NOT NULL,
    source_signature jsonb NOT NULL,
    destination_oid oid NOT NULL,
    destination_relfilenode oid,
    target_last_write_at timestamptz,
    checked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    unchanged_runs bigint NOT NULL DEFAULT 0,
    PRIMARY KEY (job_name, source_table),
    FOREIGN KEY (job_name, source_table)
        REFERENCES rvbbit.mirror_tables(job_name, source_table)
        ON DELETE CASCADE,
    CONSTRAINT mirror_source_state_signature_check CHECK (
        jsonb_typeof(source_signature) = 'object'
        AND source_signature ? 'kind'
        AND source_signature ? 'version'
        AND source_signature ? 'digest'
        AND source_signature ? 'row_count'
    ),
    CONSTRAINT mirror_source_state_unchanged_check CHECK (unchanged_runs >= 0)
);

REVOKE ALL ON rvbbit.mirror_source_state FROM PUBLIC;

CREATE OR REPLACE FUNCTION rvbbit.invalidate_mirror_source_state()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog, rvbbit
AS $invalidate_mirror_source_state$
BEGIN
    IF ROW(NEW.load_mode, NEW.primary_key, NEW.included_columns, NEW.change_detection)
       IS DISTINCT FROM
       ROW(OLD.load_mode, OLD.primary_key, OLD.included_columns, OLD.change_detection) THEN
        DELETE FROM rvbbit.mirror_source_state s
         WHERE s.job_name = NEW.job_name
           AND s.source_table = NEW.source_table;
    END IF;
    RETURN NEW;
END
$invalidate_mirror_source_state$;

DROP TRIGGER IF EXISTS mirror_source_state_table_config_changed
    ON rvbbit.mirror_tables;
CREATE TRIGGER mirror_source_state_table_config_changed
AFTER UPDATE OF load_mode, primary_key, included_columns, change_detection
ON rvbbit.mirror_tables
FOR EACH ROW EXECUTE FUNCTION rvbbit.invalidate_mirror_source_state();

CREATE OR REPLACE FUNCTION rvbbit.invalidate_mirror_job_source_state()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog, rvbbit
AS $invalidate_mirror_job_source_state$
BEGIN
    IF ROW(NEW.connection_name, NEW.source_schema)
       IS DISTINCT FROM ROW(OLD.connection_name, OLD.source_schema) THEN
        DELETE FROM rvbbit.mirror_source_state s
         WHERE s.job_name = NEW.job_name;
    END IF;
    RETURN NEW;
END
$invalidate_mirror_job_source_state$;

DROP TRIGGER IF EXISTS mirror_source_state_job_config_changed
    ON rvbbit.mirror_jobs;
CREATE TRIGGER mirror_source_state_job_config_changed
AFTER UPDATE OF connection_name, source_schema
ON rvbbit.mirror_jobs
FOR EACH ROW EXECUTE FUNCTION rvbbit.invalidate_mirror_job_source_state();

CREATE OR REPLACE FUNCTION rvbbit.set_mirror_table_change_detection(
    requested_job text,
    requested_source_table text,
    requested_change_detection text DEFAULT 'always'
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog, rvbbit
AS $set_mirror_table_change_detection$
DECLARE
    normalized_job text := lower(btrim(requested_job));
    normalized_detection text := lower(btrim(coalesce(requested_change_detection, 'always')));
    changed integer;
BEGIN
    PERFORM rvbbit.require_capability_catalog_admin();
    IF normalized_detection NOT IN ('always', 'append_only_key') THEN
        RAISE EXCEPTION 'change detection must be always or append_only_key';
    END IF;
    UPDATE rvbbit.mirror_tables t
       SET change_detection = normalized_detection,
           updated_at = clock_timestamp()
     WHERE t.job_name = normalized_job
       AND t.source_table = requested_source_table;
    GET DIAGNOSTICS changed = ROW_COUNT;
    IF changed = 0 THEN
        RAISE EXCEPTION 'mirror table %.% does not exist', normalized_job, requested_source_table;
    END IF;
    RETURN normalized_detection;
END
$set_mirror_table_change_detection$;

CREATE OR REPLACE FUNCTION rvbbit.record_mirror_source_signature(
    requested_job text,
    requested_source_table text,
    requested_signature jsonb
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog, rvbbit
AS $record_mirror_source_signature$
DECLARE
    destination regclass;
    destination_file oid;
    destination_write timestamptz;
BEGIN
    IF jsonb_typeof(requested_signature) <> 'object'
       OR NOT (requested_signature ?& ARRAY['kind','version','digest','row_count']) THEN
        RAISE EXCEPTION 'mirror source signature is incomplete';
    END IF;

    SELECT to_regclass(format('%I.%I', j.destination_schema, t.destination_table))
      INTO destination
      FROM rvbbit.mirror_jobs j
      JOIN rvbbit.mirror_tables t USING (job_name)
     WHERE j.job_name = lower(btrim(requested_job))
       AND t.source_table = requested_source_table;
    IF destination IS NULL THEN
        RAISE EXCEPTION 'mirror destination for %.% does not exist', requested_job, requested_source_table;
    END IF;

    SELECT pg_relation_filenode(destination), registry.last_write_at
      INTO destination_file, destination_write
      FROM rvbbit.tables registry
     WHERE registry.table_oid = destination;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'mirror destination % is not registered with RVBBIT', destination;
    END IF;

    INSERT INTO rvbbit.mirror_source_state (
        job_name, source_table, source_signature, destination_oid,
        destination_relfilenode, target_last_write_at, checked_at,
        published_at, unchanged_runs
    ) VALUES (
        lower(btrim(requested_job)), requested_source_table, requested_signature,
        destination::oid, destination_file, destination_write,
        clock_timestamp(), clock_timestamp(), 0
    )
    ON CONFLICT (job_name, source_table) DO UPDATE SET
        source_signature = EXCLUDED.source_signature,
        destination_oid = EXCLUDED.destination_oid,
        destination_relfilenode = EXCLUDED.destination_relfilenode,
        target_last_write_at = EXCLUDED.target_last_write_at,
        checked_at = EXCLUDED.checked_at,
        published_at = EXCLUDED.published_at,
        unchanged_runs = 0;
END
$record_mirror_source_signature$;

CREATE OR REPLACE FUNCTION rvbbit.skip_unchanged_mirror_snapshot(
    requested_job text,
    requested_source_table text,
    requested_signature jsonb
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog, rvbbit
AS $skip_unchanged_mirror_snapshot$
DECLARE
    destination regclass;
    destination_file oid;
    destination_write timestamptz;
    changed integer;
BEGIN
    SELECT to_regclass(format('%I.%I', j.destination_schema, t.destination_table))
      INTO destination
      FROM rvbbit.mirror_jobs j
      JOIN rvbbit.mirror_tables t USING (job_name)
     WHERE j.job_name = lower(btrim(requested_job))
       AND t.source_table = requested_source_table
       AND t.enabled
       AND t.load_mode = 'snapshot'
       AND t.change_detection = 'append_only_key';
    IF destination IS NULL THEN
        RETURN false;
    END IF;

    SELECT pg_relation_filenode(destination), registry.last_write_at
      INTO destination_file, destination_write
      FROM rvbbit.tables registry
     WHERE registry.table_oid = destination;
    IF NOT FOUND THEN
        RETURN false;
    END IF;

    UPDATE rvbbit.mirror_source_state s
       SET checked_at = clock_timestamp(),
           unchanged_runs = s.unchanged_runs + 1
     WHERE s.job_name = lower(btrim(requested_job))
       AND s.source_table = requested_source_table
       AND s.source_signature = requested_signature
       AND s.destination_oid = destination::oid
       AND s.destination_relfilenode IS NOT DISTINCT FROM destination_file
       AND s.target_last_write_at IS NOT DISTINCT FROM destination_write;
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed > 0;
END
$skip_unchanged_mirror_snapshot$;

CREATE OR REPLACE VIEW rvbbit.mirror_change_detection_status AS
SELECT
    c.connection_name,
    j.job_name,
    j.source_schema,
    t.source_table,
    j.destination_schema,
    t.destination_table,
    t.load_mode,
    t.change_detection,
    t.primary_key,
    state.source_signature ->> 'kind' AS signature_kind,
    nullif(state.source_signature ->> 'row_count', '')::bigint AS source_row_count,
    state.checked_at,
    state.published_at,
    coalesce(state.unchanged_runs, 0) AS unchanged_runs,
    state.destination_oid,
    state.destination_relfilenode,
    state.target_last_write_at
FROM rvbbit.mirror_connections c
JOIN rvbbit.mirror_jobs j USING (connection_name)
JOIN rvbbit.mirror_tables t USING (job_name)
LEFT JOIN rvbbit.mirror_source_state state
  ON state.job_name = t.job_name
 AND state.source_table = t.source_table;

REVOKE ALL ON rvbbit.mirror_change_detection_status FROM PUBLIC;
REVOKE ALL ON FUNCTION rvbbit.set_mirror_table_change_detection(text, text, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION rvbbit.record_mirror_source_signature(text, text, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION rvbbit.skip_unchanged_mirror_snapshot(text, text, jsonb) FROM PUBLIC;

COMMENT ON COLUMN rvbbit.mirror_tables.change_detection IS
    'always preserves ordinary snapshot semantics; append_only_key skips the full load when count and primary-key bounds are unchanged under an explicit no-update/no-delete source contract.';
COMMENT ON TABLE rvbbit.mirror_source_state IS
    'Last source-side snapshot signature and local target identity used to skip unchanged append-only mirror loads.';
COMMENT ON VIEW rvbbit.mirror_change_detection_status IS
    'Credential-free diagnostics for source-side mirror no-change gates.';
