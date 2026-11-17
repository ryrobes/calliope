"""Canonical scope, retained receipts and reuse requirements."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import semantic_contracts as sc
import server
from test_workspace_sql import db  # noqa: F401

MIGRATION = Path(__file__).parents[2] / 'crates/pg_rvbbit/sql/migrations/0470_canonical_metric_scopes.sql'
CONTRACT = dict(population='All reported sightings; no region filter', unit='sightings',
                time_basis='sighted_at', window='Trailing 30 days [now minus 30 days, now), UTC',
                why='Recent reported activity', time_zone='UTC')


def test_canonical_definition_is_usable_without_an_external_selection():
    c = sc.metric_contract(CONTRACT, {'days': 30})
    assert c['defaults'] == {'days': 30} and c['snapshot'] == 'daily'
    for key in sc.METRIC_FIELDS:
        with pytest.raises(ValueError): sc.metric_contract({k:v for k,v in CONTRACT.items() if k != key})
    with pytest.raises(ValueError): sc.metric_contract(dict(CONTRACT, window='selected date range'))
    with pytest.raises(ValueError): sc.metric_contract(dict(CONTRACT, time_zone='invented-zone'))
    assert sc.metric_contract(dict(CONTRACT, time_basis='Current source state', window='All open jobs at observation time'))


def test_legacy_review_does_not_rewrite_defaults_or_discard_proposals():
    metric = {'params': {'days':30}, 'grain':'One scalar', 'labels':{'origin':'meaning','window':'last {days} days'}}
    before=json.dumps(metric,sort_keys=True)
    assert any('legacy' in s for s in sc.metric_review(metric))
    assert json.dumps(metric,sort_keys=True)==before
    assert sc.normalize_idea({'name':'Revenue','formula':'SUM(amount)'}) == {'name':'Revenue','formula':'SUM(amount)'}


def test_cube_requires_a_specific_reuse_comparison_before_any_write():
    class C:
        def execute(self,sql,args):
            assert sql.startswith('SELECT'); return self
        def fetchall(self):
            return [{'name':'sales_lines','grain':'invoice_id,line_no','description':'All invoice lines',
                     'sources':['sales.lines'],'sql':'SELECT * FROM sales.lines'}]
    assert sc.cube_reuse_check(C(),'sales_by_region','SELECT region FROM sales.lines','region','Sales grouped by region')['error']['code']=='CUBE_REUSE_REVIEW'
    assert sc.cube_reuse_check(C(),'copy','SELECT * FROM sales.lines','invoice_id,line_no','Duplicate')['error']['code']=='CUBE_ALREADY_EXISTS'
    result=sc.cube_reuse_check(C(),'invoices','SELECT invoice_id FROM sales.lines','invoice_id','Invoice facts',
                             {'reviewed':['sales_lines'],'difference':'grain','reason':'Invoice facts have one row per invoice; line facts repeat them.'})
    assert result['reviewed'][0]['name']=='sales_lines'


def test_incomplete_metric_is_rejected_before_opening_a_connection(monkeypatch):
    monkeypatch.setattr(server,'_conn',lambda:pytest.fail('No connection for an incomplete definition'))
    result=server.tool_create_metric('bad','SELECT count(*) FROM sales.lines')
    assert result['error']['code']=='METRIC_DEFINITION_REQUIRED'


@pytest.fixture
def canonical(db):
    with db() as c:
        c.execute('''CREATE TABLE rvbbit.metric_defs(name text,version int,params jsonb);
          CREATE TABLE rvbbit.metric_observations(observation_id bigint PRIMARY KEY,metric_name text,metric_version int,
            params jsonb,value jsonb,observed_at timestamptz,data_as_of timestamptz);
          CREATE TABLE rvbbit.meaning_domains(domain_key text,candidate_metrics jsonb,origin text,status text);
          CREATE FUNCTION rvbbit.meaning_metric_slug(text) RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT lower($1) $$;''')
        c.execute(MIGRATION.read_text())
    return db


def test_history_matches_effective_defaults_and_current_definition_without_changing_receipts(canonical):
    with canonical() as c:
        c.execute("INSERT INTO rvbbit.metric_defs VALUES('sightings',1,'{\"days\":30}'),('sightings',2,'{\"days\":30}')")
        for ident,version,params,value in [(1,1,{},99),(2,2,{},10),(3,2,{'days':30},11),(4,2,{'days':7},800),(5,2,{'days':None},801),(6,2,{'region':'west'},802)]:
            c.execute("INSERT INTO rvbbit.metric_observations VALUES(%s,'sightings',%s,%s,%s,now()-(%s*interval '1 day'),'2020-01-01')",(ident,version,json.dumps(params),json.dumps(value),7-ident))
        rows=c.execute('SELECT observation_id,value FROM rvbbit.metric_default_observations ORDER BY observed_at').fetchall()
        assert [(r['observation_id'],r['value']) for r in rows]==[(2,10),(3,11)]
        assert c.execute('SELECT count(*) n FROM rvbbit.metric_observations').fetchone()['n']==6
        c.execute("INSERT INTO rvbbit.metric_defs VALUES('sightings',3,'{\"days\":7}')")
        assert c.execute('SELECT count(*) n FROM rvbbit.metric_default_observations').fetchone()['n']==0
        assert c.execute('SELECT count(*) n FROM rvbbit.metric_observations').fetchone()['n']==6


def test_discovery_tick_does_not_invent_30_days_or_execute_a_formula(canonical):
    with canonical() as c:
        c.execute("INSERT INTO rvbbit.meaning_domains VALUES('subject:x',%s,'map','done')",(json.dumps([{'name':'Sightings','formula':'dangerous_function()','grain':'selected period'}]),))
        for apply in (False,True):
            row=c.execute('SELECT * FROM rvbbit.meaning_register_implied_metrics(NULL,%s)',(apply,)).fetchone()
            assert row['action']=='needs_canonical_definition' and row['value'] is None
        assert c.execute('SELECT count(*) n FROM rvbbit.metric_defs').fetchone()['n']==0
        c.execute(MIGRATION.read_text())  # reapplying preserves all existing state


@pytest.mark.parametrize('row,ok', [({'value': 0},True),({'n':None},True),({'value':[1,2]},False),({'value':{'n':1}},False),({'x':1,'y':2},False)])
def test_default_proof_requires_one_scalar_headline(row,ok):
    class C:
        def execute(self,sql,args=None):return self
        def fetchone(self):return {'sql':'SELECT 0 AS value'}
        def fetchall(self):return [{'row':row}]
    if ok: assert sc.prove_metric(C(),'SELECT 0',{})==row
    else:
        with pytest.raises(ValueError):sc.prove_metric(C(),'SELECT 0',{})


def test_unresolved_default_is_rejected_before_executing_metric_sql():
    class C:
        def execute(self,sql,args=None):
            assert sql.startswith('SELECT rvbbit.preview_metric_sql');return self
        def fetchone(self):return {'sql':"SELECT sum(amount) FROM sales WHERE region={region}"}
    with pytest.raises(ValueError,match='region'):sc.prove_metric(C(),'template',{})
