"""SQL execution data survives receipt projection and renders as ordinary results."""
import uuid

import calliope
from test_workspace_sql import db, OWNER, SESSION  # noqa: F401
from test_zen_chat_layout import zen_page  # noqa: F401


def project(run):
    return calliope._project_tool_result('run_workspace_sql', {'run': run}, {}, 'sql-test')[0]


def test_result_projection_preserves_batch_rows_aliases_nulls_and_full_sql():
    sql = 'SELECT 1; -- ' + 'x' * 3000
    rows = [{'Display name': f'row {i}', 'Optional?': None, 'Document': {'a b': [None, {'value': i}]}} for i in range(45)]
    run = {'id': str(uuid.uuid4()), 'status': 'complete',
           'action_snapshot': {'executor': 'workspace_sql', 'receipt_only': True},
           'input_redacted': {'sql': sql, 'sql_sha256': 'a' * 64},
           'result': {'committed': True, 'statements': [
               {'statement': i + 1, 'columns': ['Display name', 'Optional?', 'Document'], 'rows': rows}
               for i in range(50)]}}
    frozen = project(run)['payload']['run']
    assert frozen['input_redacted']['sql'] == sql
    assert len(frozen['result']['statements']) == 50
    assert frozen['result']['statements'][-1]['rows'] == rows
    assert frozen['result']['statements'][-1]['row_count'] == 45
    assert not frozen['result']['statements'][-1]['truncated']


def test_result_bounds_are_explicit_and_array_null_positions_survive():
    value = {'columns': [{'name': 'First'}, {'name': 'Second'}],
             'rows': [[None, 'x' * 3000] for _ in range(205)]}
    frozen = calliope._sql_result_snapshot(value)
    assert frozen['columns'] == ['First', 'Second']
    assert len(frozen['rows']) == 200
    assert frozen['rows'][0][0] is None
    assert frozen['truncated'] and frozen['cells_truncated']
    assert len(frozen['rows'][0][1]) <= 2401
    legacy = calliope._sql_result_snapshot({'columns': ['n'], 'rows': [[1]], 'row_count': 20})
    assert legacy['truncated'] and legacy['row_count'] == 1
    nonfinite = calliope._sql_result_snapshot({'columns': ['n'], 'rows': [[float('nan')]]})
    assert nonfinite['rows'] == [[None]]


def test_workspace_stage_rehydrates_old_snapshot_without_reexecuting(db):
    result = calliope.run_workspace_sql_for_session(db, SESSION,
        'CREATE TABLE calliope.result AS SELECT i AS "Order number", NULL::text AS "Notes?" FROM generate_series(1,45) i; SELECT * FROM calliope.result')
    run = result['run']
    assert run['status'] == 'complete', run.get('error')
    surface = project(run)
    # Simulate the previous generic nested-evidence depth limit.
    surface['payload'] = calliope._bounded_evidence_json(surface['payload'])
    surface['id'] = str(uuid.uuid4())
    restored = calliope._refresh_local_sql_surfaces(db, SESSION, OWNER, [surface])[0]
    rows = restored['payload']['run']['result']['statements'][-1]['rows']
    assert len(rows) == 45
    assert rows[-1] == {'Order number': 45, 'Notes?': None}
    assert not restored['payload']['approval_required']
    # The same CREATE could not succeed twice; inspect the original receipt only.
    with db() as conn:
        assert conn.execute('SELECT count(*) n FROM rvbbit.calliope_action_runs').fetchone()['n'] == 1
    assert calliope._refresh_local_sql_surfaces(db, SESSION, 'someone-else@example.com', [surface])[0] == surface


def test_sql_receipts_show_tables_tabs_and_batch_outcomes_in_real_browser(zen_page):
    page = zen_page
    run = {'id': str(uuid.uuid4()), 'status': 'complete',
           'action_snapshot': {'id': 'admin.local_sql', 'title': 'Run local RVBBIT SELECT SQL', 'executor': 'local_sql', 'receipt_only': True},
           'input_redacted': {'sql': 'SELECT 42 AS "Answer", NULL AS "Notes?"', 'sql_sha256': 'a' * 64, 'statement_type': 'select'},
           'result': {'columns': [{'name': 'Answer'}, {'name': 'Notes?'}], 'rows': [{'Answer': 42, 'Notes?': None}], 'row_count': 1, 'elapsed_ms': 12}}
    surface = project(run)
    surface.update(id='direct', turn_id='one', created_at='2026-09-14T23:00:00Z')
    surface['presentation']['phase'] = 'exploration'
    page.evaluate('''surface => {
      qa.setupEvents();qa.state.surfaces=[surface];qa.state.zenExpandedSurfaces.add('direct');
      qa.renderChat(true);qa.renderStage();
    }''', surface)
    receipt = page.locator('[data-sql-execution]')
    assert receipt.locator('tbody').inner_text() == '42\t—'
    assert 'SQL EXECUTION' in page.locator('.evidence-ledger').inner_text().upper()
    assert 'SELECT 42' in page.locator('.evidence-ledger-title').first.inner_text()
    assert not receipt.locator('.sql-execution-receipt code').is_visible()
    receipt.locator('[data-view="sql"]').click()
    assert 'SELECT 42' in receipt.locator('.sql-code').inner_text()
    receipt.locator('[data-view="table"]').click()
    assert receipt.locator('tbody').is_visible()
    assert page.locator('[data-approve-local-sql]').count() == 0
    assert page.locator('.surface.selected').count() == 0
    receipt.locator('.sql-execution-receipt summary').click()
    assert receipt.locator('[data-open-action-run]').get_attribute('data-open-action-run') == run['id']
    assert 'line-through' not in receipt.locator('strong').first.evaluate('(el)=>getComputedStyle(el).textDecorationLine')
    page.screenshot(path='/tmp/calliope-sql-execution-receipt.png')

    run['action_snapshot']['executor'] = 'workspace_sql'
    run['result'] = {'committed': True, 'elapsed_ms': 22, 'statements': [
        {'statement': 1, 'status': 'CREATE TABLE', 'columns': [], 'rows': []},
        {'statement': 2, 'status': 'INSERT 0 1', 'rows_affected': 1, 'columns': [], 'rows': []},
        {'statement': 3, 'status': 'SELECT 0', 'columns': ['name'], 'rows': []},
        {'statement': 4, 'status': 'SELECT 1', 'columns': ['name'], 'rows': [['<script>bad()</script>']]},
    ]}
    surface['payload'] = project(run)['payload']
    page.evaluate('surface=>{qa.state.surfaces=[surface];qa.renderStage()}', surface)
    assert receipt.locator('.sql-execution-statement').count() == 4
    assert '1 rows affected' in receipt.inner_text()
    assert 'Query returned no rows.' in receipt.inner_text()
    assert '<script>bad()</script>' in receipt.inner_text()
    assert receipt.locator('script').count() == 0
    page.set_viewport_size({'width': 700, 'height': 900})
    page.screenshot(path='/tmp/calliope-workspace-sql-receipt.png')
