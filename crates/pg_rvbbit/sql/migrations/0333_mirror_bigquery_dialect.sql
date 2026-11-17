-- 0333: BigQuery joins the mirror dialect allowlist.
--
-- The dlt mirror sidecar (data/dlt-mirror) now ships sqlalchemy-bigquery; a
-- BigQuery connection is a service account rather than host/port/user, so its
-- DSN is `bigquery://<project>?credentials_base64=<service-account json>` and
-- the mirror job's source_schema is `<project>.<dataset>`. Nothing else in
-- the control plane changes; the CHECK constraint just admits the dialect.

ALTER TABLE rvbbit.mirror_connections
    DROP CONSTRAINT IF EXISTS mirror_connections_dialect_check;
ALTER TABLE rvbbit.mirror_connections
    ADD CONSTRAINT mirror_connections_dialect_check
    CHECK (dialect IN ('postgresql','mysql','mariadb','mssql','oracle','db2','bigquery'));
