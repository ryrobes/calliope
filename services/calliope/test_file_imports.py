import json
import sqlite3
from pathlib import Path

import pytest

import file_imports as files
import server
from test_workspace_sql import db, OWNER, SESSION  # noqa: F401


@pytest.fixture
def importer(db, tmp_path, monkeypatch):
    monkeypatch.setenv('WAREHOUSE_FILE_IMPORT_ROOTS', str(tmp_path))
    with db() as conn:
        conn.execute('CREATE TABLE rvbbit.import_registered(target text PRIMARY KEY)')
        conn.execute("""CREATE FUNCTION rvbbit.enable_table(target regclass, force_it boolean) RETURNS boolean LANGUAGE plpgsql AS $$
        BEGIN INSERT INTO rvbbit.import_registered VALUES(target::text) ON CONFLICT DO NOTHING; RETURN true; END $$""")
    def load(content, suffix='.csv', **kw):
        path = tmp_path / ('source' + suffix)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        return files.import_file(db, SESSION, str(path), authorized_owner=OWNER, **kw)
    return load


def test_copy_preserves_source_and_retries_without_duplication(importer, db):
    content = 'Id,Amount,Note\r\n001,12.340,"Résumé, and\nsecond line"\r\n002,,"O\'Brien"\r\n'
    result = importer(content, schema='demo_raw', table='orders')
    assert result['run']['status'] == 'complete', result
    assert result['approval_required'] is False
    with db() as c:
        assert c.execute('SELECT * FROM demo_raw.orders ORDER BY "Id"').fetchall() == [
            {'Id': '001', 'Amount': '12.340', 'Note': 'Résumé, and\nsecond line'},
            {'Id': '002', 'Amount': '', 'Note': "O'Brien"}]
        assert c.execute('SELECT * FROM rvbbit.import_registered').fetchall() == [{'target': 'demo_raw.orders'}]
    repeat = importer(content, schema='demo_raw', table='orders')
    assert repeat['run']['result']['reused'] is True
    changed = importer(content + '003,20,new\n', schema='demo_raw', table='orders')
    assert changed['run']['status'] == 'failed'
    with db() as c:
        assert c.execute('SELECT count(*) n FROM demo_raw.orders').fetchone()['n'] == 2


def test_malformed_rows_rollback_entire_table_and_keep_failure_receipt(importer, db):
    result = importer('id,name\n1,First\n2,Extra,Oops\n', table='partial')
    assert result['run']['status'] == 'failed'
    assert 'Row 2' in result['run']['error']
    with db() as c:
        assert c.execute("SELECT to_regclass('calliope.partial') AS t").fetchone()['t'] is None
        assert c.execute('SELECT count(*) n FROM rvbbit.import_registered').fetchone()['n'] == 0
    assert server._activity_error('import_local_file', result, None)


def test_registration_failure_also_rolls_back(importer, db):
    with db() as c:
        c.execute("CREATE OR REPLACE FUNCTION rvbbit.enable_table(target regclass,force_it boolean) RETURNS boolean LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'registration failed'; END $$")
    result = importer('id\n1\n', table='failed_register')
    assert result['run']['status'] == 'failed'
    with db() as c:
        assert c.execute("SELECT to_regclass('calliope.failed_register') AS t").fetchone()['t'] is None


def test_existing_table_is_not_overwritten(importer, db):
    result = importer('id\n1\n', schema='sales', table='orders')
    assert result['run']['status'] == 'failed'
    assert 'already exists' in result['run']['error']
    with db() as c:
        assert c.execute('SELECT count(*) n FROM sales.orders').fetchone()['n'] == 2


def test_jsonl_unions_keys_preserves_null_and_decimal(importer, db):
    result = importer('{"id":"001","a":1.234567890123456789,"meta":{"cost":12.34}}\n{"id":"002","b":false}\n', suffix='.jsonl', table='json_rows')
    assert result['run']['status'] == 'complete', result
    with db() as c:
        r = c.execute('SELECT * FROM calliope.json_rows ORDER BY id').fetchall()
        assert r[0]['a'] == '1.234567890123456789'
        assert json.loads(r[0]['meta']) == {'cost': 12.34}
        assert r[1]['a'] is None and r[1]['b'] == 'false'


def test_sqlite_selection_and_import(importer, tmp_path, db):
    path = tmp_path / 'accounting.sqlite'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE ledger(id TEXT, cents INTEGER, note BLOB)')
        c.execute('CREATE TABLE other(x TEXT)')
        c.execute('INSERT INTO ledger VALUES(?,?,?)', ('0001', 12345, b'\x00\xff'))
    data = path.read_bytes()
    info = importer(data, suffix='.sqlite')
    assert info['source_tables'] == ['ledger', 'other']
    result = importer(data, suffix='.sqlite', source_table='ledger', table='ledger')
    assert result['run']['status'] == 'complete', result
    with db() as c:
        assert c.execute('SELECT * FROM calliope.ledger').fetchone() == {'id': '0001', 'cents': '12345', 'note': '\\x00ff'}


def test_excel_multiple_sheets(importer, tmp_path, db):
    from openpyxl import Workbook
    w = Workbook(); w.active.title = 'Budget'; w.active.append(['Id', 'USD']); w.active.append(['001', 12.5]); w.create_sheet('Other')
    p = tmp_path / 'book.xlsx'; w.save(p)
    info = importer(p.read_bytes(), suffix='.xlsx')
    assert info['sheets'] == ['Budget', 'Other']
    result = importer(p.read_bytes(), suffix='.xlsx', sheet='Budget', table='budget')
    assert result['run']['status'] == 'complete', result
    with db() as c:
        assert c.execute('SELECT * FROM calliope.budget').fetchone() == {'Id': '001', 'USD': '12.5'}


def test_excel_title_rows_are_visible_and_selectable(importer, tmp_path, db):
    from openpyxl import Workbook
    w = Workbook(); w.active.append(['Planning estimates']); w.active.append([])
    w.active.append(['Id', 'USD']); w.active.append(['0001', 20])
    p = tmp_path / 'title.xlsx'; w.save(p)
    info = importer(p.read_bytes(), suffix='.xlsx', table='plan')
    assert info['selection_required'] == 'header_row'
    assert info['preview'][2] == ['Id', 'USD']
    result = importer(p.read_bytes(), suffix='.xlsx', table='plan', header_row=3)
    assert result['run']['status'] == 'complete', result
    assert result['run']['result']['row_count'] == 1


def test_preview_does_not_create_tables_or_action_receipts(importer, db):
    info = importer('Id,Amount\n001,12.50\n')
    assert info['preview'] == [['001', '12.50']]
    assert info['imported'] is False
    with db() as c:
        assert c.execute('SELECT count(*) n FROM rvbbit.calliope_action_runs').fetchone()['n'] == 0


def test_auth_checked_before_file_access(db, monkeypatch):
    import calliope
    monkeypatch.setattr(calliope.calliope_dreams, 'is_company_admin', lambda *_: False)
    with pytest.raises(PermissionError, match='Admins'):
        files.import_file(db, SESSION, '/missing', authorized_owner=OWNER)


def test_paths_and_identifier_boundaries(tmp_path, monkeypatch):
    root = tmp_path / 'imports'; root.mkdir()
    outside = tmp_path / 'secret.csv'; outside.write_text('a\nsecret\n')
    (root / 'escape.csv').symlink_to(outside)
    monkeypatch.setenv('WAREHOUSE_FILE_IMPORT_ROOTS', str(root))
    with pytest.raises(ValueError, match='inside'):
        files.read_source(root / 'escape.csv')
    for schema, table in [('rvbbit', 'x'), ('pg_catalog', 'x'), ('public', 'x;DROP TABLE y')]:
        with pytest.raises(ValueError): files._target(schema, table)


def test_document_jsonl_retains_message_identity_and_sender():
    records = list(files.document_records(Path('/demo/messages.jsonl'), b'{"message_id":"m-1","subject":"Offer","body":"Yes please","from_address":"sender@example.invalid","sent_at":"2026-10-07T12:00:00Z"}\n'))
    assert records == [{'title': 'Offer', 'body': 'Yes please', 'uri': 'm-1', 'author': 'sender@example.invalid', 'occurred_at': '2026-10-07T12:00:00Z'}]


def test_brain_file_reports_partial_failure_and_passes_roles(tmp_path, monkeypatch):
    path = tmp_path / 'mail.jsonl'
    path.write_text('\n'.join(json.dumps({'title': f'Mail {n}', 'body': 'Hello', 'uri': f'mail:{n}'}) for n in range(2)))
    monkeypatch.setenv('WAREHOUSE_BRAIN_IMPORT_ROOTS', str(tmp_path))
    monkeypatch.setattr(server, '_logged', lambda _name, _args, fn: fn())
    calls = []
    def ingest(source, **kw):
        calls.append((source, kw))
        return {'doc_id': 1} if len(calls) == 1 else {'error': {'message': 'retry me'}}
    monkeypatch.setattr(server, 'tool_brain_ingest', ingest)
    result = server._mcp_brain_ingest_file(str(path), 'Demo mail', ['staff'])
    assert result['ingested'] == 1 and result['failed'] == 1
    assert result['error']['code'] == 'BRAIN_FILE_PARTIAL_IMPORT'
    assert calls[0][1]['roles'] == ['staff']
    assert calls[1][1]['uri'] == 'mail:1'


def test_mcp_tools_and_recovery_guidance():
    tools = {t.name: t for t in server._build_mcp()._tool_manager.list_tools()}
    assert tools['import_local_file'].parameters['required'] == ['session_id', 'path']
    assert 'COPY FROM STDIN' in tools['import_local_file'].description
    assert 'brain_ingest_file' in tools
    assert 'import_local_file' in server._INSTRUCTIONS
