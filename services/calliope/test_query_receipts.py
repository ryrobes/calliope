"""Execution receipts survive Hermes' model-context compression."""
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import calliope
from test_generated_image_delivery import db, seed
from test_zen_chat_layout import zen_page

SQL = 'WITH qualifying AS (SELECT ' + "'evidence', " * 60 + "42 AS last_column) SELECT * FROM qualifying -- exact_receipt_end"
CLIPPED = SQL[:200] + '...[truncated]'
RESULT = {'columns': ['n'], 'rows': [[1]], 'row_count': 1}


def projected(sql, call_id='call-one', name=None):
    if name:
        return calliope._project_progressive_sql_result('run_sql_multi', {'results': {name: RESULT}}, {'queries': {name: sql}}, call_id)
    return calliope._project_progressive_sql_result('run_sql', RESULT, {'sql': sql}, call_id)


@pytest.mark.parametrize('complete_first', [True, False])
def test_query_replay_preserves_one_complete_receipt(db, complete_first):
    target = seed(db)
    first = calliope._insert_surfaces(db, **target, projected=projected(SQL if complete_first else CLIPPED))[0]
    second = calliope._insert_surfaces(db, **target, projected=projected(CLIPPED if complete_first else SQL))
    assert (not second) if complete_first else second[0]['id'] == first['id']
    assert calliope._insert_surfaces(db, **target, projected=projected(CLIPPED)) == []
    with db() as conn:
        rows = conn.execute('SELECT * FROM rvbbit.calliope_surfaces').fetchall()
    assert len(rows) == 1
    assert rows[0]['id'] == UUID(first['id'])
    assert rows[0]['source']['sql'] == SQL
    assert rows[0]['source']['sql_truncated'] is False
    assert rows[0]['parent_surface_id'] is None


def test_batch_queries_and_separate_executions_stay_distinct(db):
    target = seed(db)
    for name in ['first','second']:
        calliope._insert_surfaces(db, **target, projected=projected(SQL,name=name))
        assert calliope._insert_surfaces(db, **target, projected=projected(CLIPPED,name=name)) == []
    calliope._insert_surfaces(db, **target, projected=projected(SQL,call_id='another-execution',name='first'))
    with db() as conn:
        assert conn.execute('SELECT count(*) AS n FROM rvbbit.calliope_surfaces').fetchone()['n'] == 3


def test_legacy_repair_keeps_links_and_never_invents_missing_sql(db):
    target=seed(db)
    full=calliope._insert_surfaces(db,**target,projected=projected(SQL))[0]
    clipped_id=str(uuid4())
    with db() as conn:
        conn.execute("INSERT INTO rvbbit.calliope_surfaces SELECT %s::uuid,session_id,turn_id,ordinal+1,kind,title,tool_name,tool_call_id,'old-clipped-lineage',id,artifact_slug,artifact_version,payload,%s::jsonb,presentation,design_profile_version_id,created_at FROM rvbbit.calliope_surfaces WHERE id=%s::uuid",(clipped_id,json.dumps(projected(CLIPPED)[0]['source']),full['id']))
    unknown=calliope._insert_surfaces(db,**target,projected=projected(CLIPPED,call_id='no-full-evidence'))[0]
    migration=(Path(__file__).parents[2]/'crates/pg_rvbbit/sql/migrations/0435_query_receipt_replay.sql').read_text()
    with db() as conn:
        conn.execute(migration);conn.execute(migration)
        rows={str(r['id']):r for r in conn.execute('SELECT * FROM rvbbit.calliope_surfaces').fetchall()}
    assert len(rows)==3
    assert rows[clipped_id]['source']['sql']==SQL
    assert rows[clipped_id]['presentation']['duplicate_query_receipt_of']==full['id']
    assert calliope._is_suppressed_surface(rows[clipped_id])
    assert not calliope._is_suppressed_surface(rows[full['id']])
    assert rows[unknown['id']]['source']['sql']==CLIPPED
    assert not calliope._is_suppressed_surface(rows[unknown['id']])


def test_complete_result_sql_can_repair_compressed_arguments():
    item=calliope._query_surface({**RESULT,'sql':SQL},{'sql':CLIPPED},'call')
    assert item['source']['sql']==SQL
    assert item['source']['args']['sql']==SQL


def test_sql_tab_renders_the_entire_receipt(zen_page):
    page=zen_page
    page.evaluate('''surface => {
      surface.turn_id='one';surface.created_at=new Date().toISOString();
      document.querySelector('#stage').innerHTML=qa.surfaceCard(surface);
    }''',{'id':'query','kind':'query','title':'SQL receipt','source':{'sql':SQL},'payload':RESULT})
    assert 'exact_receipt_end' in page.locator('[data-query-view="sql"] code').text_content()
    assert '[truncated]' not in page.locator('[data-query-view="sql"] code').text_content()
