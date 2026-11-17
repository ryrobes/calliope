"""Run the real crawler SQL against a small PostgreSQL graph fixture, no inference."""
import json
import os
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / 'crates/pg_rvbbit/sql/migrations'


@pytest.fixture
def db():
    dsn = os.environ.get('BUSINESS_CONTEXT_TEST_DSN')
    if not dsn:
        pytest.skip('requires an empty disposable PostgreSQL database')
    with psycopg.connect(dsn, autocommit=True) as c:
        assert c.execute("SELECT to_regnamespace('rvbbit')").fetchone()[0] is None
        c.execute("""
            CREATE SCHEMA rvbbit;
            CREATE TABLE rvbbit.catalog_docs(graph_id text,kind text,rel_name text,doc text,embedding real[],embedded_at timestamptz);
            CREATE TABLE rvbbit.business_context_state(singleton bool,crawled_at timestamptz,node_count int,embedded_count int,last_error text);
            INSERT INTO rvbbit.business_context_state(singleton) VALUES(true);
            CREATE TABLE rvbbit.business_value_graph_current(graph_id uuid);
            CREATE TABLE rvbbit.business_semantic_topology(graph_id uuid,graph jsonb,business_domain_key text,business_domain_name text,value_system_key text,value_system_name text,status text,analytical_questions jsonb,gaps jsonb,source_relations text[],concepts jsonb,processes jsonb,semantic_relationships jsonb,source_memberships jsonb,materialization_relation text,summary text);
            CREATE TABLE rvbbit.test_edges(lkind text,llabel text,predicate text,rkind text,rlabel text,evidence jsonb);
            CREATE FUNCTION rvbbit._business_context_crawl_metric_v1(boolean) RETURNS jsonb LANGUAGE sql AS $$SELECT '{}'::jsonb$$;
            CREATE FUNCTION rvbbit._business_context_index_node(text,text,text,jsonb,text,boolean) RETURNS jsonb LANGUAGE sql AS $$SELECT '{}'::jsonb$$;
            CREATE FUNCTION rvbbit.kg_assert_node(text,text,jsonb,double precision,text,double precision,text) RETURNS bigint LANGUAGE plpgsql AS $$BEGIN IF nullif(btrim($2),'') IS NULL THEN RAISE EXCEPTION 'node_label must be non-empty'; END IF; RETURN 1; END$$;
            CREATE FUNCTION rvbbit.kg_assert_edge(text,text,text,text,text,double precision,jsonb,jsonb,text,double precision,text) RETURNS bigint LANGUAGE plpgsql AS $$BEGIN PERFORM rvbbit.kg_assert_node($1,$2,'{}',1.0,'',0.0,$11); PERFORM rvbbit.kg_assert_node($4,$5,'{}',1.0,'',0.0,$11); IF nullif(btrim($3),'') IS NULL THEN RAISE EXCEPTION 'predicate must be non-empty'; END IF; INSERT INTO rvbbit.test_edges VALUES($1,$2,$3,$4,$5,$8); RETURN 1; END$$;
            INSERT INTO rvbbit.business_value_graph_current VALUES('11111111-1111-1111-1111-111111111111');
            INSERT INTO rvbbit.business_semantic_topology VALUES('11111111-1111-1111-1111-111111111111','{"business_domain":{}}','ads','Advertising','delivery','Delivery','promoted','[]','[]',ARRAY['ads.source'],'[]','[]','[]','[]',NULL,'Delivery context');
        """)
        try:
            yield c
        finally:
            c.execute('DROP SCHEMA rvbbit CASCADE')


def seed(c, relationships):
    c.execute('UPDATE rvbbit.business_semantic_topology SET semantic_relationships=%s::jsonb', (json.dumps(relationships),))


def install(c):
    c.execute((MIGRATIONS / '0413_business_context_relationship_endpoints.sql').read_text())


def test_original_fails_but_migration_preserves_one_source_concept_relationship(db):
    relationship = dict(relationship='informs',left_relation='ads.source',right_relation=None,
                        subject_concept_key='update',object_concept_key='setting',rationale='Source annotation')
    seed(db,[relationship])
    old=(MIGRATIONS/'0347_business_semantic_topology.sql').read_text()
    db.execute(old[old.index('CREATE OR REPLACE FUNCTION rvbbit.business_context_crawl('):old.index('CREATE OR REPLACE FUNCTION rvbbit.business_context_search(')])
    with pytest.raises(psycopg.errors.RaiseException,match='node_label must be non-empty'):
        db.execute('SELECT rvbbit.business_context_crawl(false)')
    install(db)
    result=db.execute('SELECT rvbbit.business_context_crawl(false)').fetchone()[0]
    assert result['semantic_relationships_skipped']==0
    assert db.execute("SELECT lkind,llabel,rkind,rlabel,evidence FROM rvbbit.test_edges WHERE predicate='informs'").fetchone()==('business_concept','ads:update','business_concept','ads:setting',relationship)
    assert db.execute('SELECT semantic_relationships FROM rvbbit.business_semantic_topology').fetchone()[0]==[relationship]


def test_table_pair_and_concept_fallbacks_and_invalid_evidence(db):
    relationships=[
        dict(relationship='tables',left_relation=' ads.left ',right_relation=' ads.right ',subject_concept_key='a',object_concept_key='b'),
        dict(relationship='concepts',subject_concept_key=' a ',object_concept_key=' b '),
        dict(relationship='right_only',right_relation='ads.right',subject_concept_key='a',object_concept_key='b'),
        dict(relationship='blank_table',left_relation='ads.left',right_relation=' ',subject_concept_key='a',object_concept_key='b'),
        dict(relationship='incomplete',left_relation='ads.left'),
        dict(relationship='incomplete_concept',subject_concept_key='a',object_concept_key=' '),
        dict(relationship=' ',left_relation='ads.left',right_relation='ads.right'),
    ]
    seed(db,relationships);install(db);install(db)  # migration replay is safe
    result=db.execute('SELECT rvbbit.business_context_crawl(false)').fetchone()[0]
    assert result['semantic_relationships_skipped']==3
    rows=db.execute("SELECT predicate,lkind,llabel,rkind,rlabel FROM rvbbit.test_edges WHERE predicate<>'contains_value_system' ORDER BY predicate").fetchall()
    assert len(rows)==4
    for pred,lk,ll,rk,rl in rows:
        if pred=='tables':
            assert (lk,rk)==('business_source_relation','business_source_relation')
            assert ll.endswith(':ads.left') and rl.endswith(':ads.right')
        else:
            assert (lk,ll,rk,rl)==('business_concept','ads:a','business_concept','ads:b')
    assert db.execute('SELECT semantic_relationships FROM rvbbit.business_semantic_topology').fetchone()[0]==relationships
