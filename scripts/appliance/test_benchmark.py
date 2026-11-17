import contextlib
import importlib.util
import io
import json
import sqlite3
from pathlib import Path
import sys
import tarfile
import types
import zipfile

import pytest

HERE=Path(__file__).resolve().parent

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

bench=load('benchmark',HERE/'benchmark.py')
capture=load('capture',HERE/'benchmark-capture.py')

def test_middle_memory_export_is_scoped_and_excludes_vectors_and_text(tmp_path):
    path=tmp_path/'session-context-v1.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE chunks(id,session_id,message_id,role,tool_name,tool_call_id,chunk_offset,text,vector,model_version)')
        for sid in ('mine','other'):
            db.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?)',(sid,sid,1,'tool','run_sql','q',0,'private body','[1,0]','encoder:v1'))
    result=capture.context_index_snapshot(path,{'mine'})
    assert len(result)==1 and result[0]['session_id']=='mine'
    assert result[0]['embedded']==1 and result[0]['model_version']=='encoder:v1'
    assert 'private body' not in json.dumps(result) and 'vector' not in result[0]


def test_archive_checksums_and_nested_checksum_files(tmp_path):
    bundle=tmp_path/'run';bundle.mkdir();(bundle/'report.md').write_text('exact evidence')
    (bundle/'files').mkdir();(bundle/'files/checksums.json').write_text('{}')
    archive=bench.seal_archive(bundle);bench.verify(archive)
    with zipfile.ZipFile(archive) as z:assert z.read('report.md')==b'exact evidence'
    # A payload modified while preserving ZIP validity must still fail SHA checks.
    bad=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive) as src,zipfile.ZipFile(bad,'w') as dst:
        for n in src.namelist():dst.writestr(n,b'changed' if n=='report.md' else src.read(n))
    with pytest.raises(ValueError,match='Checksum'):bench.verify(bad)

@pytest.mark.parametrize('name',['../outside','/absolute'])
def test_archive_rejects_traversal(tmp_path,name):
    path=tmp_path/'bad.tgz'
    with tarfile.open(path,'w:gz') as t:
        info=tarfile.TarInfo(name);info.size=1;t.addfile(info,io.BytesIO(b'x'))
    with pytest.raises(ValueError,match='Unsafe'):bench.safe_extract(path,tmp_path/'out')


def test_symlink_member_is_rejected(tmp_path):
    path=tmp_path/'bad.tgz'
    with tarfile.open(path,'w:gz') as t:
        info=tarfile.TarInfo('link');info.type=tarfile.SYMTYPE;info.linkname='/etc/passwd';t.addfile(info)
    with pytest.raises(ValueError):bench.safe_extract(path,tmp_path/'out')


def test_cost_attribution_preserves_unknown_and_prior_revaluation():
    def row(id,when,**kw):return dict(activity_id=id,occurred_at=when,run_id=None,session_id=None,cost_kind='reported',cost_usd=1,**kw)
    old=row('old','2026-09-25T00:00:00Z');old['cost_usd']=None;old['cost_kind']='unknown'
    direct=row('direct','2026-09-26T12:01:00Z');direct['run_id']='r'
    bg=row('background','2026-09-26T12:02:00Z')
    unknown=row('unknown','2026-09-26T12:02:00Z');unknown['cost_usd']=None;unknown['cost_kind']='unknown'
    settled={**old,'cost_usd':2,'cost_kind':'reported'}
    rows,totals=bench.cost_summary([old],[direct,bg,unknown,settled],{'r'},set(),'2026-09-26T12:00:00Z')
    assert [r['benchmark_attribution'] for r in rows]==['linked','window_only','window_only','prior_revaluation']
    assert totals=={'linked':{'reported':1},'window_only':{'reported':1}}
    assert rows[2]['cost_usd'] is None


def test_configuration_redaction_preserves_model_and_usage():
    value={'model':'calliope','api_key':'never-export','nested':{'password':'bad'},'max_tokens':1024}
    result=capture.scrub(value)
    assert result=={'model':'calliope','api_key':'[redacted]','nested':{'password':'[redacted]'},'max_tokens':1024}
    assert capture.scrub({'key':'provider_secret','value':'bad'})['value']=='[redacted]'
    assert capture.scrub('https://user:secret@example.com/path?token=secret')=='https://example.com/path'
    assert not capture.file_allowed(Path('.ssh/key'))
    assert not capture.file_allowed(Path('.env.local'))
    assert capture.file_allowed(Path('deliverable/index.html'))
    assert capture.since('2026-09-26 12:00:01+00:00','2026-09-26T12:00:00Z')


def test_profile_refresh_preserves_operator_pause(monkeypatch,tmp_path):
    reconcile=load('specialists',HERE.parents[1]/'docker/calliope/reconcile-hermes-specialists.py')
    jobs=types.ModuleType('cron.jobs');calls=[]
    jobs.list_jobs=lambda **kw:[{'id':'job','name':'nightly','enabled':False,'state':'paused'}]
    jobs.use_cron_store=lambda _:contextlib.nullcontext()
    jobs.create_job=lambda **kw:calls.append(('create',kw))
    jobs.update_job=lambda id,values:calls.append(('update',values))
    constants=types.ModuleType('hermes_constants')
    constants.set_hermes_home_override=lambda _:None;constants.reset_hermes_home_override=lambda _:None
    monkeypatch.setitem(sys.modules,'cron.jobs',jobs);monkeypatch.setitem(sys.modules,'hermes_constants',constants)
    monkeypatch.setattr(reconcile,'_profile_model_pin',lambda _:('custom','calliope'))
    assert reconcile._reconcile_cron(tmp_path,{'cron':{'name':'nightly','prompt':'Do scheduled work','schedule':'0 3 * * *','skill':'example'}})=='updated'
    assert 'enabled' not in calls[0][1] and 'state' not in calls[0][1]


def test_skill_capture_retains_unchanged_content_excludes_credentials_and_symlinks(tmp_path):
    data=tmp_path/'data';out=tmp_path/'export'
    skill=data/'skills'/'learned';skill.mkdir(parents=True)
    (skill/'SKILL.md').write_text('learned method')
    (skill/'.env').write_text('secret')
    profile=data/'profiles'/'iris'/'skills'/'review';profile.mkdir(parents=True)
    (profile/'SKILL.md').write_text('profile method')
    (skill/'link').symlink_to(profile/'SKILL.md')
    captured=capture.capture_skills(out,data)
    assert set(captured)=={'skills/learned/SKILL.md','profiles/iris/skills/review/SKILL.md'}
    for name,row in captured.items():
        assert (out/row['exported_as']).read_bytes()==(data/name).read_bytes()
    assert capture.capture_skills(out,data)==captured
    (skill/'SKILL.md').write_text('changed method')
    assert capture.capture_skills(out,data)['skills/learned/SKILL.md']['sha256']!=captured['skills/learned/SKILL.md']['sha256']


def test_legacy_anonymous_tool_events_cannot_collapse_or_claim_closure():
    ledger=load('ledger',HERE/'export-run-ledger.py')
    events=[{'event':'tool.started','event_id':'a','epoch_seconds':1},
            {'event':'tool.started','event_id':'b','epoch_seconds':2},
            {'event':'tool.completed','event_id':'c','epoch_seconds':3}]
    summary=ledger.summarize(events)
    assert summary['open_tool_calls']==2 and summary['unidentified_tool_events']==3
    assert summary['duplicate_tool_terminals']==0


def test_generated_view_export_captures_dependencies_without_rows():
    import os
    import psycopg
    from psycopg.rows import dict_row
    dsn=os.environ.get('CALLIOPE_COST_TEST_DSN')
    if not dsn:pytest.skip('Disposable database required')
    with psycopg.connect(dsn,row_factory=dict_row) as db:
        try:
            db.execute('CREATE SCHEMA IF NOT EXISTS calliope')
            db.execute('CREATE TABLE calliope.harness_source(id integer)')
            db.execute('CREATE VIEW calliope.harness_view AS SELECT id FROM calliope.harness_source')
            db.execute('CREATE MATERIALIZED VIEW calliope.harness_materialized AS SELECT id FROM calliope.harness_view WITH NO DATA')
            rows={r['name']:r for r in capture.capture_view_definitions(db)}
            assert 'harness_source' not in rows
            assert 'harness_source' in rows['harness_view']['definition']
            assert rows['harness_view']['dependencies']==[{'schema':'calliope','name':'harness_source','kind':'r'}]
            assert rows['harness_materialized']['dependencies']==[{'schema':'calliope','name':'harness_view','kind':'v'}]
        finally:db.rollback()
