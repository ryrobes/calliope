# Local source-file ingestion

Callie can import files already in her workspace without setting up a database
connection, installing Fletch, or routing the source rows through the model.

`import_local_file(session_id, path, schema, table)` reads a server-visible file,
creates a new raw table, streams it over PostgreSQL `COPY FROM STDIN`, and calls
`rvbbit.enable_table` in the same transaction. Source paths refer to the shared
Hermes filesystem, not PostgreSQL's container filesystem or the Desktop.

- CSV/TSV: original headers and text fields, including leading zeros and blanks.
- JSON/JSONL/GeoJSON: objects become rows, keys become columns, nested values stay
  JSON text, absent/null keys become SQL NULL. Use `record_path` for a nested array.
- SQLite: select `source_table`; inspection lists tables if selection is needed.
- XLSX: select `sheet`; `header_row` is one-based and defaults to 1. A title or
  merged header returns a row preview so Callie can choose the actual header.
  Values are the saved workbook caches; the importer does not calculate formulas.

Omit `table` to inspect columns and a small preview. Raw columns intentionally
remain text; apply dates, numbers, and business rules in reconciled SQL views.
Other formats can be converted to CSV/JSONL with terminal and passed back to the
same tool. A file/format error is not evidence of a missing Warehouse connection.

Imports require an existing Admins Team session and the user's import request.
They create new data tables/schemas immediately with an execution receipt. They
never overwrite or append to an existing table. Retrying the same source hash,
selection and target reuses its registered import; different input requires a
new table name. The receipt and table comment retain provenance and row counts.
Unrelated tables, mirrors and internal system schemas remain untouched.

`WAREHOUSE_FILE_IMPORT_ROOTS` and `WAREHOUSE_BRAIN_IMPORT_ROOTS` are explicit
OS-path-separated directories. The hosted stack shares `/opt/data` read-only
with Warehouse and defaults both settings to:

```
/opt/data/workspace:/opt/data/outputs:/app/data/brain-imports
```

Only these data directories are exposed for imports, not Hermes configuration,
credentials or the filesystem root. Existing installs with an explicit old Brain
root need their setting updated. Standalone Warehouse deployments should set
roots that are actually mounted in their server container.

## Documents and email

Use `brain_crawl_folder` for a text-document directory, or `brain_ingest_file`
for a text/Markdown/EML file or JSON/JSONL document manifest. Manifest records use
`title` (or `subject`), `body`, stable `uri` (or `message_id`), and optionally
`author`/`from_address` and `occurred_at`/`sent_at`. Normalize PDF/DOCX/MBOX locally
when necessary. Import one representation per source record, not every duplicate
export. The single-document `brain_ingest` remains available as a fallback.

Preserve source access roles and verify retrieval for the intended user. Omitting
roles uses existing source defaults, which can be default-deny for a new source.
Partial failures retain successful document IDs and identify the failed URIs;
retries with the same source and URIs are idempotent.
