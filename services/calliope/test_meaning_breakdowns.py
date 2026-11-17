"""Proposal shape, publication history and the database boundary (no model calls)."""
import json
from pathlib import Path

import pytest
import meaning_jobs as mj
from test_workspace_sql import db  # noqa: F401

MIGRATION = Path(__file__).parents[2] / 'crates/pg_rvbbit/sql/migrations/0462_meaning_breakdowns.sql'
BREAKDOWN = {'name':'Sales mix','purpose':'Find which regions contribute to revenue.',
             'measures':['SUM(amount)'],'dimensions':['region'],'grain':'One row per region','caveats':'Orders only.'}
LEGACY = [
    {'name':'Revenue','formula':'SUM(amount)','grain':'Across rows, groupable by region','why':'Sales volume.'},
    {'name':'Average height','formula':'AVG(height)','grain':'gender','why':'Compare measurements.'},
    {'name':'Volume by day','formula':'COUNT(*) grouped by date','grain':'day','why':'See changes in activity.'},
]


def scalar(c, sql, args=None):
    return next(iter(c.execute(sql,args).fetchone().values()))


@pytest.fixture
def meaning(db):
    with db() as c:
        c.execute('''CREATE TABLE rvbbit.meaning_domains (
          domain_key text PRIMARY KEY,name text,about text,members text[],core text[] DEFAULT '{}',roles jsonb DEFAULT '{}',
          areas jsonb DEFAULT '[]',questions jsonb DEFAULT '[]',candidate_metrics jsonb DEFAULT '[]',gaps jsonb DEFAULT '[]',
          origin text DEFAULT 'map',status text DEFAULT 'done',usable boolean DEFAULT true,output jsonb,
          dossier_at timestamptz DEFAULT now(),updated_at timestamptz DEFAULT now());
          CREATE TABLE rvbbit.meaning_domain_versions(revision bigserial PRIMARY KEY,domain_key text,snapshot jsonb);
          CREATE TABLE rvbbit.meaning_subject_aliases(old_key text);
          CREATE TABLE rvbbit.meaning_relations(relation text,business_name text,rel_name text,n_rows bigint,kind text);
          CREATE FUNCTION rvbbit._jsonb_arr(j jsonb) RETURNS jsonb LANGUAGE sql IMMUTABLE AS
            $$ SELECT CASE WHEN jsonb_typeof(j)='array' THEN j ELSE '[]'::jsonb END $$;
          CREATE FUNCTION rvbbit.meaning_display_name(t text) RETURNS text LANGUAGE sql AS $$ SELECT t $$;
          CREATE FUNCTION rvbbit.meaning_tree_nouns(t text,n int) RETURNS text[] LANGUAGE sql AS $$ SELECT '{}'::text[] $$;
          CREATE FUNCTION rvbbit.meaning_noun(t text) RETURNS jsonb LANGUAGE sql AS $$ SELECT '{}'::jsonb $$;
          CREATE FUNCTION rvbbit.meaning_concepts(t text,n int) RETURNS jsonb LANGUAGE sql AS $$ SELECT '[]'::jsonb $$;
          CREATE FUNCTION rvbbit.meaning_subject_derived(t text,n int) RETURNS jsonb LANGUAGE sql AS $$ SELECT '[]'::jsonb $$;
          CREATE FUNCTION rvbbit.meaning_subject_lenses(t text) RETURNS jsonb LANGUAGE sql AS $$ SELECT '[]'::jsonb $$;
          CREATE FUNCTION rvbbit.meaning_candidate_metrics(t text) RETURNS jsonb LANGUAGE sql AS
            $$ SELECT candidate_metrics FROM rvbbit.meaning_domains WHERE domain_key=t $$;
          CREATE VIEW rvbbit.meaning_published_domains AS
            SELECT (jsonb_populate_record(NULL::rvbbit.meaning_domains,v.snapshot)).*
            FROM (SELECT DISTINCT ON(domain_key) snapshot,domain_key FROM rvbbit.meaning_domain_versions ORDER BY domain_key,revision DESC) v;''')
        c.execute("INSERT INTO rvbbit.meaning_domains(domain_key,name,about,members,candidate_metrics,output) VALUES('subject:test','Test','Published prose',ARRAY['qa.orders'],%s,%s)",
                  (json.dumps(LEGACY),json.dumps({'candidate_metrics':LEGACY})))
        c.execute("INSERT INTO rvbbit.meaning_domain_versions(domain_key,snapshot) SELECT domain_key,to_jsonb(d) FROM rvbbit.meaning_domains d")
        c.execute("UPDATE rvbbit.meaning_domains SET about='Unpublished draft',status='error'")
        c.execute(MIGRATION.read_text())
    return db


def test_backfill_preserves_original_history_and_publication_independence(meaning):
    with meaning() as c:
        versions=c.execute('SELECT snapshot FROM rvbbit.meaning_domain_versions ORDER BY revision').fetchall()
        assert len(versions)==2
        assert versions[0]['snapshot']['candidate_metrics']==LEGACY
        pub=c.execute('SELECT * FROM rvbbit.meaning_published_domains').fetchone()
        assert pub['about']=='Published prose' and pub['status']=='done'
        assert scalar(c,'SELECT about FROM rvbbit.meaning_domains')=='Unpublished draft'
        assert len(pub['candidate_metrics'])==1 and len(pub['candidate_breakdowns'])==2
        assert pub['candidate_metrics'][0]['grain']=='One value for the selected scope and period'
        assert pub['candidate_breakdowns'][0]['purpose']=='Compare measurements.'
        assert pub['candidate_breakdowns'][0]['dimensions']==['gender']
        assert pub['output']['candidate_metrics']==LEGACY
        assert scalar(c,"SELECT rvbbit.meaning_tree('subject:test')->'breakdowns'")==pub['candidate_breakdowns']
        assert scalar(c,"SELECT rvbbit.meaning_areas()->0->>'breakdowns'")=='2'
        c.execute(MIGRATION.read_text())
        assert scalar(c,'SELECT count(*) FROM rvbbit.meaning_domain_versions')==2


def test_writers_store_explicit_breakdowns_and_cannot_store_grouped_metrics(meaning):
    with meaning() as c:
        output={'candidate_metrics':[LEGACY[2]],'candidate_breakdowns':[BREAKDOWN]}
        c.execute('UPDATE rvbbit.meaning_domains SET output=%s,candidate_metrics=%s', (json.dumps(output),json.dumps(output['candidate_metrics'])))
        row=c.execute('SELECT candidate_metrics,candidate_breakdowns FROM rvbbit.meaning_domains').fetchone()
        assert row['candidate_metrics']==[]
        assert [b['name'] for b in row['candidate_breakdowns']]==['Sales mix','Volume by day']
        assert row['candidate_breakdowns'][0]==BREAKDOWN
        # An older worker omitting the new field cannot erase existing breakdowns.
        c.execute('UPDATE rvbbit.meaning_domains SET output=%s', (json.dumps({'candidate_metrics':[]}),))
        assert scalar(c,'SELECT candidate_breakdowns FROM rvbbit.meaning_domains')==row['candidate_breakdowns']
        # An explicit empty array can clear it in a later reviewed dossier.
        c.execute('UPDATE rvbbit.meaning_domains SET output=%s', (json.dumps({'candidate_metrics':[],'candidate_breakdowns':[]}),))
        assert scalar(c,'SELECT candidate_breakdowns FROM rvbbit.meaning_domains')==[]
        assert len(scalar(c,'SELECT candidate_breakdowns FROM rvbbit.meaning_published_domains'))==2
        # INSERT ... ON CONFLICT follows the real domain writer path.
        c.execute("INSERT INTO rvbbit.meaning_domains(domain_key,output,candidate_metrics) VALUES ('subject:test',%s,%s) ON CONFLICT(domain_key) DO UPDATE SET output=EXCLUDED.output,candidate_metrics=EXCLUDED.candidate_metrics",
                  (json.dumps(output),json.dumps(output['candidate_metrics'])))
        assert scalar(c,'SELECT candidate_metrics FROM rvbbit.meaning_domains')==[]
        assert len(scalar(c,'SELECT candidate_breakdowns FROM rvbbit.meaning_domains'))==2


def test_normalizers_require_a_narrative_and_bound_breakdown_fields():
    output={'candidate_breakdowns':[BREAKDOWN,{'name':'Just a grouping'},dict(BREAKDOWN,dimensions='region'),dict(BREAKDOWN,purpose='')]}
    for normalized in (mj.normalize_domain(output),mj.normalize_dossier(output,set())):
        assert normalized['candidate_breakdowns']==[BREAKDOWN]
        assert normalized['candidate_metrics']==[]
    bounded=mj.normalize_breakdowns({'candidate_breakdowns':[dict(BREAKDOWN,measures=['x'*1000]*20)]*20})
    assert len(bounded)==10 and bounded[0]['measures']==['x'*400]
    assert 'candidate_breakdowns' in mj._DOSSIER_CONTRACT and 'candidate_breakdowns' in mj._DOMAIN_CONTRACT
    assert 'ONE scalar value' in mj._DOSSIER_SYSTEM


def test_source_row_grain_alone_does_not_turn_a_scalar_ratio_into_a_breakdown(meaning):
    with meaning() as c:
        metric={'name':'Cost per lead','formula':'SUM(spend) / SUM(leads)','grain':'Lead','why':'Tracks acquisition efficiency.'}
        result=scalar(c,"SELECT rvbbit.meaning_classify_ideas(%s,'[]')",(json.dumps([metric]),))
        assert result=={'candidate_metrics':[metric],'candidate_breakdowns':[]}
