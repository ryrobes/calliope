"""Import agent-staged source files over the existing Warehouse connection.

Raw columns are text deliberately: identifiers, decimals and source dates survive
unchanged. Typed/reconciled views are a subsequent SQL step. COPY streams from
this service, never asks PostgreSQL to open an agent-container path.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from psycopg import sql

DEFAULT_ROOTS = '/opt/data/workspace:/opt/data/outputs:/app/data/brain-imports'
MAX_BYTES = 128 * 1024 * 1024
MAX_ROWS = 1_000_000
MARKER = 'Calliope file import: '


def read_source(path, env='WAREHOUSE_FILE_IMPORT_ROOTS'):
    roots = [Path(p).expanduser().resolve() for p in os.environ.get(env, DEFAULT_ROOTS).split(os.pathsep) if p.strip()]
    source = Path(str(path)).expanduser().resolve()
    if not any(root != Path('/') and source.is_relative_to(root) for root in roots):
        raise ValueError(f'File must be inside {env}: ' + ', '.join(map(str, roots)))
    if not source.is_file():
        raise ValueError(f'Not a readable staged file: {source}. Use the Hermes workspace, not the desktop filesystem.')
    with source.open('rb') as f:
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('File exceeds 128 MiB; split it into source partitions before import.')
    return source, data


def text_value(value):
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return '\\x' + value.hex()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list, bool)):
        return _json_value(value)
    return str(value)


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k, ensure_ascii=False) + ':' + _json_value(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ','.join(_json_value(v) for v in value) + ']'
    return json.dumps(value, ensure_ascii=False)


def _headers(values):
    names = [str(v) if v is not None else '' for v in values]
    if not names or len(names) > 1000 or any(not v or '\x00' in v or len(v.encode()) > 63 for v in names):
        raise ValueError('Provide 1–1000 nonempty column names, each at most 63 UTF-8 bytes. Normalize headers in a separate staged copy.')
    if len(names) != len(set(names)):
        raise ValueError('Duplicate column names; normalize headers in a separate staged copy.')
    return names


def parse_file(source, data, source_table=None, sheet=None, record_path=None, header_row=1):
    """Return columns, an iterator of rows, and format/selection metadata."""
    ext = source.suffix.lower()
    meta = {'format': ext.lstrip('.')}
    if not isinstance(header_row, int) or isinstance(header_row, bool) or not 1 <= header_row <= 10000:
        raise ValueError('header_row must be a one-based row number between 1 and 10000')
    if ext in {'.csv', '.tsv'}:
        csv.field_size_limit(MAX_BYTES)
        rows = csv.reader(io.StringIO(data.decode('utf-8-sig'), newline=''), delimiter='\t' if ext == '.tsv' else ',')
        for _ in range(header_row - 1):
            next(rows, None)
        meta['header_row'] = header_row
        return _headers(next(rows, [])), rows, meta
    if ext in {'.json', '.jsonl', '.ndjson', '.geojson'}:
        text = data.decode('utf-8-sig')
        records = [json.loads(line, parse_float=Decimal) for line in text.splitlines() if line.strip()] if ext in {'.jsonl', '.ndjson'} else json.loads(text, parse_float=Decimal)
        for key in str(record_path or '').split('.') if record_path else []:
            records = records[key]
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list) or not records or any(not isinstance(r, dict) for r in records):
            raise ValueError('Expected JSON objects or an array of objects; use record_path for a nested array, or normalize to JSONL.')
        columns = _headers(dict.fromkeys(k for r in records for k in r))
        meta['record_path'] = record_path
        return columns, ([text_value(r.get(k)) for k in columns] for r in records), meta
    if ext in {'.sqlite', '.sqlite3', '.db'}:
        # Read a snapshot, not a mutable source DB or its WAL. No SQL from the file is executed.
        with tempfile.TemporaryDirectory(prefix='calliope-import-') as td:
            snapshot = Path(td) / 'source.sqlite'
            snapshot.write_bytes(data)
            with sqlite3.connect(f'{snapshot.as_uri()}?mode=ro&immutable=1', uri=True) as conn:
                conn.execute('PRAGMA trusted_schema=OFF')
                available = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                if source_table is None and len(available) == 1:
                    source_table = available[0]
                if source_table not in available:
                    return [], iter(()), {**meta, 'selection_required': 'source_table', 'source_tables': available}
                quoted = '"' + source_table.replace('"', '""') + '"'
                cur = conn.execute('SELECT * FROM ' + quoted)
                columns = _headers(d[0] for d in cur.description)
                records = cur.fetchmany(MAX_ROWS + 1)
        meta['source_table'] = source_table
        return columns, ([text_value(v) for v in r] for r in records), meta
    if ext == '.xlsx':
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            if sheet is None and len(wb.sheetnames) == 1:
                sheet = wb.sheetnames[0]
            if sheet not in wb.sheetnames:
                return [], iter(()), {**meta, 'selection_required': 'sheet', 'sheets': wb.sheetnames}
            rows = wb[sheet].iter_rows(values_only=True)
            for _ in range(header_row - 1):
                next(rows, None)
            header = next(rows, [])
            try:
                columns = _headers(header)
            except ValueError as exc:
                preview = [list(map(text_value, header))]
                for row in rows:
                    if len(preview) >= 8:
                        break
                    preview.append(list(map(text_value, row)))
                return [], iter(()), {**meta, 'sheet': sheet, 'selection_required': 'header_row',
                    'preview_start_row': header_row, 'preview': preview, 'message': str(exc)}
            records = []
            for row in rows:
                if any(v is not None for v in row):
                    records.append([text_value(v) for v in row])
                if len(records) > MAX_ROWS:
                    break
        finally:
            wb.close()
        meta.update(sheet=sheet, header_row=header_row, formula_values='cached workbook values; recalculate the source workbook if caches are missing')
        return columns, iter(records), meta
    raise ValueError('Supported files: CSV, TSV, JSON, JSONL, GeoJSON, SQLite and XLSX. Normalize other tabular formats to CSV/JSONL in the workspace, then call import_local_file. Documents use brain_ingest_file or brain_crawl_folder.')


def _target(schema, table):
    for value in (schema, table):
        if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', str(value or '')):
            raise ValueError('Use lowercase PostgreSQL identifiers for destination schema and table.')
    if schema.startswith('pg_') or schema in {'rvbbit', 'information_schema', 'calliope_runtime'}:
        raise ValueError('Choose a data schema, not an internal system schema.')
    return f'{schema}.{table}'


def import_file(conn_factory, session_id, path, *, schema='calliope', table=None,
                source_table=None, sheet=None, record_path=None, header_row=1, authorized_owner=None):
    import calliope
    owner = calliope._execution_subject_for_session(conn_factory, session_id, authorized_owner)
    if not calliope.calliope_dreams.is_company_admin(conn_factory, owner):
        raise PermissionError('Local file ingestion requires an Admins Team session')
    source, data = read_source(path)
    columns, rows, meta = parse_file(source, data, source_table, sheet, record_path, header_row)
    provenance = {'source_path': str(source), 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), **meta}
    if meta.get('selection_required'):
        return {**provenance, 'imported': False, 'next': 'Call import_local_file again with the requested source_table, sheet or header_row selection.'}
    if table is None:
        preview = []
        for i, row in enumerate(rows):
            if i == 5:
                break
            preview.append(list(row))
        return {**provenance, 'imported': False, 'columns': columns, 'preview': preview,
                'next': 'Pass schema and table to import; raw columns remain text for lossless reconciliation.'}
    target = _target(schema, table)
    signature = {**provenance, 'columns': columns, 'schema': schema, 'table': table}
    action = {'id': 'data.import_local_file', 'version': 1, 'title': f'Import {source.name} into {target}',
              'summary': 'Load a staged source file and register its raw table.', 'executor': 'file_import',
              'risk': 'new_data_table', 'receipt_only': True, 'fields': []}
    run_id = str(uuid.uuid4())
    with conn_factory() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_action_runs (id,owner_email,session_id,action_id,action_version,action_snapshot,status,input_values,input_redacted,plan,steps,started_at) VALUES(%s,%s,%s,%s,1,%s::jsonb,'running',%s::jsonb,%s::jsonb,%s::jsonb,'[]',now())",
                     (run_id, owner, session_id, action['id'], json.dumps(action), json.dumps(signature), json.dumps(signature), json.dumps({'approval_required': False})))
    try:
        with conn_factory() as conn, conn.transaction():
            conn.execute("SELECT set_config('statement_timeout','120000',true)")
            conn.execute("SELECT set_config('lock_timeout','10000',true)")
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('file-import:' + target,))
            existing = conn.execute("SELECT c.oid, c.relkind, obj_description(c.oid,'pg_class') AS comment FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s", (schema, table)).fetchone()
            reused = False
            if existing:
                comment = existing.get('comment') or ''
                saved = json.loads(comment[len(MARKER):]) if comment.startswith(MARKER) else {}
                if existing['relkind'] != 'r' or saved.get('signature') != signature:
                    raise ValueError(f'{target} already exists with different or unknown provenance. Choose a new table; import never overwrites existing data.')
                count = conn.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(schema, table))).fetchone()['n']
                if count != saved.get('rows'):
                    raise ValueError('Previously imported table row count changed; choose a new target to re-import the original file.')
                reused = True
            else:
                conn.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema)))
                conn.execute(sql.SQL('CREATE TABLE {} ({})').format(sql.Identifier(schema, table), sql.SQL(', ').join(sql.SQL('{} text').format(sql.Identifier(c)) for c in columns)))
                count = 0
                with conn.cursor().copy(sql.SQL('COPY {} ({}) FROM STDIN').format(sql.Identifier(schema, table), sql.SQL(', ').join(map(sql.Identifier, columns)))) as copy:
                    for row in rows:
                        if len(row) != len(columns):
                            raise ValueError(f'Row {count+1} has {len(row)} values for {len(columns)} columns')
                        count += 1
                        if count > MAX_ROWS:
                            raise ValueError('File exceeds 1 million rows; split into source partitions.')
                        copy.write_row(row)
                saved = {'signature': signature, 'rows': count, 'imported_at': datetime.now(timezone.utc).isoformat(), 'session_id': session_id}
                conn.execute(sql.SQL('COMMENT ON TABLE {} IS {}').format(sql.Identifier(schema, table), sql.Literal(MARKER + json.dumps(saved))))
            # Registration and load share one commit, including retry recovery.
            conn.execute('SELECT rvbbit.enable_table(%s::regclass, false)', (target,))
            result = {'imported': True, 'reused': reused, 'row_count': count, 'columns': columns,
                      'warehouse_objects': [target], 'registered': True, 'provenance': provenance,
                      'raw_types': 'text; apply business types in reconciled SQL views'}
            # Receipt commits with the table so a lost response is safe to retry.
            conn.execute("UPDATE rvbbit.calliope_action_runs SET status='complete',result=%s::jsonb,verification=%s::jsonb,completed_at=now() WHERE id=%s",
                         (json.dumps(result), json.dumps({'committed': True, 'registered': True, 'row_count': count}), run_id))
    except Exception as exc:
        calliope._finish_action_run(conn_factory, run_id, 'failed', error=calliope._action_error_text(str(exc)))
    return {'run': calliope._action_run_for_owner(conn_factory, owner, run_id), 'approval_required': False,
            'execution_mode': 'file_import'}


def document_records(source, data):
    """Explicit document inputs; never treat CSV exports as prose documents."""
    if source.suffix.lower() in {'.jsonl', '.ndjson', '.json'}:
        text = data.decode('utf-8-sig')
        records = [json.loads(line) for line in text.splitlines() if line.strip()] if source.suffix.lower() != '.json' else json.loads(text)
        if isinstance(records, dict):
            records = [records]
        for i, record in enumerate(records):
            if not isinstance(record, dict) or not isinstance(record.get('body'), str):
                raise ValueError('Normalize document/email JSONL to records with title (or subject), body, and a stable uri (or message_id).')
            yield {'title': str(record.get('title') or record.get('subject') or f'{source.stem} {i+1}'),
                   'body': record['body'], 'uri': str(record.get('uri') or record.get('message_id') or f'{source.as_uri()}#record-{i+1}'),
                   'author': record.get('author') or record.get('from_address'), 'occurred_at': record.get('occurred_at') or record.get('sent_at')}
    elif source.suffix.lower() == '.eml':
        from email import policy
        from email.parser import BytesParser
        msg = BytesParser(policy=policy.default).parsebytes(data)
        part = msg.get_body(preferencelist=('plain', 'html'))
        yield {'title': str(msg.get('Subject') or source.stem), 'body': part.get_content() if part else '',
               'uri': str(msg.get('Message-ID') or source.as_uri()), 'author': str(msg.get('From') or '')}
    elif source.suffix.lower() in {'.txt', '.md', '.markdown', '.rst', '.org', '.log'}:
        yield {'title': source.stem, 'body': data.decode('utf-8-sig'), 'uri': str(source)}
    else:
        raise ValueError('Use UTF-8 text/Markdown, EML, or document JSONL. Extract PDF/DOCX text in the workspace first; do not import duplicate representations.')
