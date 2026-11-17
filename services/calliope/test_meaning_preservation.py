"""Integration regressions; use only an isolated Postgres with migration 0458.
MEANING_TEST_DSN=... pytest test_meaning_preservation.py
All fixture writes and review stubs roll back. No external model calls.
"""
import json
import os
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).parent))
import meaning_jobs as mj

@pytest.fixture
def db():
    dsn = os.environ.get('MEANING_TEST_DSN')
    if not dsn:
        pytest.skip('requires isolated MEANING_TEST_DSN')
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        try:
            c.execute("INSERT INTO rvbbit.meaning_relations(relation,schema_name,rel_name,status,domain_key) VALUES ('qa.old','qa','old','judged','subject:stable'),('qa.new','qa','new','judged',NULL)")
            c.execute("INSERT INTO rvbbit.meaning_domains(domain_key,schema_name,name,about,members,core,roles,origin,status,usable,dossier_at) VALUES ('subject:stable','qa','Stable','Published description',ARRAY['qa.old'],ARRAY['qa.old'],'{\"qa.old\":\"core\"}','map','done',true,now()-interval '8 hours')")
            c.execute("UPDATE rvbbit.meaning_domains SET narrative_fingerprint=rvbbit.meaning_subject_fingerprint(domain_key)")
            c.execute("SELECT rvbbit.meaning_publish_subject('subject:stable')")
            yield c
        finally:
            c.rollback()

def scalar(c, sql, args=None):
    return next(iter(c.execute(sql,args).fetchone().values()))

def test_map_is_additive_and_published_dossier_survives(db):
    before=scalar(db,'SELECT rvbbit.meaning_areas()')
    output={'subjects':[{'key':'replacement','name':'Replacement','members':[{'table':'qa.old'}]}, {'key':'stable','name':'Renamed','members':[{'table':'qa.new'}]}]}
    scalar(db,'SELECT rvbbit.meaning_apply_map(0,%s::jsonb)',(json.dumps(output),))
    assert scalar(db,"SELECT domain_key FROM rvbbit.meaning_relations WHERE relation='qa.old'")=='subject:stable'
    assert scalar(db,"SELECT name FROM rvbbit.meaning_domains WHERE domain_key='subject:stable'")=='Stable'
    assert scalar(db,'SELECT rvbbit.meaning_areas()')==before
    db.execute("UPDATE rvbbit.meaning_domains SET status='error',about='failed draft' WHERE domain_key='subject:stable'")
    assert scalar(db,'SELECT rvbbit.meaning_areas()')==before
    assert not scalar(db,"SELECT EXISTS(SELECT 1 FROM rvbbit.meaning_domains WHERE domain_key='subject:replacement')")

def test_only_structural_changes_queue_a_dossier_and_existing_sources_do_not_remap(db):
    db.execute("INSERT INTO rvbbit.meaning_recognitions(schema_name,status,fingerprint) VALUES('qa','done','changed')")
    db.execute("INSERT INTO rvbbit.meaning_map_sources(schema_name) VALUES('qa')")
    assert scalar(db,"SELECT count(*) FROM rvbbit.meaning_narrative_queue_base(100) WHERE kind IN ('map','dossier')")==0
    db.execute("UPDATE rvbbit.meaning_relations SET n_rows=123456,updated_at=now() WHERE relation='qa.old'")
    assert scalar(db,"SELECT count(*) FROM rvbbit.meaning_narrative_queue_base(100) WHERE kind IN ('map','dossier')")==0
    db.execute("INSERT INTO rvbbit.meaning_columns(relation,col_name,data_type) VALUES('qa.old','new_column','text')")
    rows=db.execute("SELECT kind,key FROM rvbbit.meaning_narrative_queue_base(100)").fetchall()
    assert rows==[{'kind':'dossier','key':'subject:stable'}]
    db.execute("INSERT INTO rvbbit.meaning_recognitions(schema_name,status,fingerprint) VALUES('new_source','done','fresh')")
    assert scalar(db,"SELECT count(*) FROM rvbbit.meaning_narrative_queue_base(100) WHERE kind='map'")==1

def test_merge_transfers_links_and_preserves_revision_history(db):
    db.execute("INSERT INTO rvbbit.meaning_domains(domain_key,schema_name,name,origin) VALUES('subject:target','qa','Target','map')")
    db.execute("INSERT INTO rvbbit.meaning_enrichments(subject_key,relation,name,label_name,status) VALUES('subject:stable','qa.old','label','Label','published')")
    scalar(db,"SELECT rvbbit.meaning_merge_subjects('subject:stable','subject:target','Confirmed overlap')")
    assert scalar(db,"SELECT domain_key FROM rvbbit.meaning_relations WHERE relation='qa.old'")=='subject:target'
    assert scalar(db,"SELECT subject_key FROM rvbbit.meaning_enrichments WHERE relation='qa.old'")=='subject:target'
    assert scalar(db,"SELECT count(*) FROM rvbbit.meaning_domain_versions WHERE domain_key='subject:stable'")==1
    assert scalar(db,"SELECT subject_key FROM rvbbit.meaning_subject_aliases WHERE old_key='subject:stable'")=='subject:target'

class Borrowed:
    def __init__(self,c): self.c=c
    def __enter__(self): return self.c
    def __exit__(self,*args): return False

class Client:
    model='test'
    def __init__(self,replies): self.replies=iter(replies);self.calls=0
    def complete_json(self,**kwargs):
        self.calls+=1
        x=next(self.replies)
        if isinstance(x,Exception): raise x
        return x,{'usage':{'cost':0.1,'prompt_tokens':10,'completion_tokens':10}}

def test_every_attempt_is_charged_and_repair_obeys_budget(db):
    client=Client(['bad json','{}'])
    wrapped=mj.AccountedClient(client,lambda:Borrowed(db),0.3,'dossier','subject:stable','changed')
    with pytest.raises(mj.BudgetExceeded):
        mj._complete(wrapped,system='s',user='u',max_tokens=100)
    assert client.calls==1
    assert float(scalar(db,'SELECT rvbbit.meaning_model_spend()'))==0.1
    assert scalar(db,"SELECT count(*) FROM rvbbit.meaning_model_calls WHERE event='charged'")==1

def test_transport_failure_keeps_reservation(db):
    wrapped=mj.AccountedClient(Client([TimeoutError('unknown outcome')]),lambda:Borrowed(db),1,'map','company','new_source')
    with pytest.raises(TimeoutError): wrapped.complete_json(system='s',user='u',max_tokens=10)
    assert float(scalar(db,'SELECT rvbbit.meaning_model_spend()'))==0.25
    assert scalar(db,"SELECT count(*) FROM rvbbit.meaning_model_calls WHERE event='failed'")==1

def test_rejected_review_rolls_back_draft_and_keeps_publication(db):
    # Packet/review stubs avoid reading real sources or calling Jev.
    db.execute("CREATE OR REPLACE FUNCTION rvbbit.meaning_subject_packet(p_key text, p_max_tables int DEFAULT 90) RETURNS jsonb LANGUAGE sql AS $$ SELECT '{}'::jsonb $$")
    db.execute("CREATE OR REPLACE FUNCTION rvbbit.meaning_review_domain(p_domain_key text) RETURNS jsonb LANGUAGE sql AS $$ SELECT '{\"usable\":false}'::jsonb $$")
    before=scalar(db,'SELECT rvbbit.meaning_areas()')
    out=mj.run_dossier(lambda:Borrowed(db),Client(['{"about":"New draft","core":["qa.old"]}']),'subject:stable')
    assert out['status']=='error'
    assert scalar(db,'SELECT rvbbit.meaning_areas()')==before
    assert scalar(db,"SELECT about FROM rvbbit.meaning_domains WHERE domain_key='subject:stable'")=='Published description'

def test_successful_review_publishes_new_revision(db):
    db.execute("CREATE OR REPLACE FUNCTION rvbbit.meaning_subject_packet(p_key text,p_max_tables int DEFAULT 90) RETURNS jsonb LANGUAGE sql AS $$ SELECT '{}'::jsonb $$")
    db.execute("CREATE OR REPLACE FUNCTION rvbbit.meaning_review_domain(p_domain_key text) RETURNS jsonb LANGUAGE plpgsql AS $$ BEGIN UPDATE rvbbit.meaning_domains SET usable=true WHERE domain_key=p_domain_key; RETURN '{\"usable\":true}'::jsonb; END $$")
    out=mj.run_dossier(lambda:Borrowed(db),Client(['{"about":"Reviewed description","core":["qa.old"]}']),'subject:stable')
    assert out['status']=='done'
    assert scalar(db,"SELECT count(*) FROM rvbbit.meaning_domain_versions WHERE domain_key='subject:stable'")==2
    assert scalar(db,'SELECT rvbbit.meaning_areas()')[0]['about']=='Reviewed description'
    assert scalar(db,"SELECT count(*) FROM rvbbit.meaning_narrative_queue_base(100) WHERE kind='dossier'")==0

def test_stale_model_reply_cannot_replace_publication(db):
    db.execute("CREATE OR REPLACE FUNCTION rvbbit.meaning_subject_packet(p_key text,p_max_tables int DEFAULT 90) RETURNS jsonb LANGUAGE sql AS $$ SELECT '{}'::jsonb $$")
    class ChangingClient(Client):
        def complete_json(self,**kwargs):
            db.execute("INSERT INTO rvbbit.meaning_columns(relation,col_name,data_type) VALUES('qa.old','arrived_during_call','text')")
            return super().complete_json(**kwargs)
    out=mj.run_dossier(lambda:Borrowed(db),ChangingClient(['{"about":"Outdated draft","core":["qa.old"]}']),'subject:stable')
    assert out['status']=='superseded'
    assert scalar(db,'SELECT rvbbit.meaning_areas()')[0]['about']=='Published description'

def test_orphan_repair_follows_relation_and_is_idempotent(db):
    db.execute("INSERT INTO rvbbit.meaning_enrichments(subject_key,relation,name,label_name,status) VALUES('subject:lost','qa.old','label','Label','published')")
    migration=Path(__file__).parents[2]/'crates/pg_rvbbit/sql/migrations/0458_meaning_accumulates.sql'
    db.execute(migration.read_text(),prepare=False)
    db.execute(migration.read_text(),prepare=False)
    assert scalar(db,"SELECT subject_key FROM rvbbit.meaning_enrichments WHERE relation='qa.old'")=='subject:stable'
    assert scalar(db,'SELECT count(*) FROM rvbbit.meaning_association_repairs')==1


def test_floor_query_failure_retains_last_good_areas():
    # Exercise the actual nested route helper without starting all app workers.
    import ast
    from typing import Any
    source=ast.parse((Path(__file__).parent/'calliope.py').read_text())
    node=next(n for n in ast.walk(source) if isinstance(n,ast.FunctionDef) and n.name=='_floor_areas')
    class Down:
        def __enter__(self): raise RuntimeError('temporary database failure')
        def __exit__(self,*args): pass
    cached=[{'key':'subject:stable','name':'Stable'}]
    namespace={'Any':Any,'json':json,'os':os,'conn_factory':Down,'_FLOOR_AREAS_CACHE':{'at':0,'areas':cached}}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'<floor helper>','exec'),namespace)
    assert namespace['_floor_areas']()==cached
    assert namespace['_FLOOR_AREAS_CACHE']['areas']==cached
