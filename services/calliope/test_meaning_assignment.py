"""New sources must enter the company map before tail-table classification."""
import subprocess

import psycopg
from psycopg.rows import dict_row
import pytest

import meaning_jobs


@pytest.fixture(scope='module')
def database(tmp_path_factory):
    root = tmp_path_factory.mktemp('meaning-assignment')
    data, sock = root/'db', root/'socket'
    sock.mkdir()
    subprocess.run(['initdb','-D',str(data),'-A','trust','--no-locale','-E','UTF8'],check=True,capture_output=True)
    subprocess.run(['pg_ctl','-D',str(data),'-l',str(root/'pg.log'),'-o',f"-k {sock} -c listen_addresses=''",'-w','start'],check=True,capture_output=True)
    try:
        with psycopg.connect(host=str(sock),dbname='postgres',row_factory=dict_row,autocommit=True) as conn:
            conn.execute('''CREATE SCHEMA rvbbit;
                CREATE TABLE rvbbit.business_sources(schema_name text PRIMARY KEY,role text);
                CREATE TABLE rvbbit.meaning_relations(relation text,schema_name text,status text,
                    domain_key text,placed_by text,plumbing numeric,kind text);
                CREATE TABLE rvbbit.meaning_map_sources(schema_name text PRIMARY KEY);
                CREATE TABLE rvbbit.assignment_calls(n int);
                CREATE FUNCTION rvbbit.meaning_assign_unplaced(int) RETURNS jsonb LANGUAGE plpgsql AS $$
                BEGIN INSERT INTO rvbbit.assignment_calls VALUES(1); RETURN '{"assigned":1}'; END $$;
                INSERT INTO rvbbit.business_sources VALUES('public','business'),('hvac_demo','business');
                INSERT INTO rvbbit.meaning_map_sources VALUES('public');
                INSERT INTO rvbbit.meaning_relations VALUES('hvac_demo.orders','hvac_demo','judged',NULL,NULL,0,'transactional_fact');
            ''')
        yield dict(host=str(sock),dbname='postgres',row_factory=dict_row)
    finally:
        subprocess.run(['pg_ctl','-D',str(data),'-m','immediate','-w','stop'],check=True,capture_output=True)


@pytest.fixture
def db(database):
    with psycopg.connect(**database) as conn, conn.transaction(force_rollback=True):
        yield conn


def calls(conn):
    return conn.execute('SELECT count(*) AS n FROM rvbbit.assignment_calls').fetchone()['n']


def test_old_map_cannot_classify_a_new_source_before_its_map(db):
    assert meaning_jobs.assign_mapped_sources(db) == {'status':'awaiting_source_map','sources':['hvac_demo']}
    assert calls(db) == 0
    # Applying the expanded map records incorporated sources in this registry.
    db.execute("INSERT INTO rvbbit.meaning_map_sources VALUES('hvac_demo')")
    assert meaning_jobs.assign_mapped_sources(db) == {'assigned':1}
    assert calls(db) == 1


@pytest.mark.parametrize('exclusion',[
    "plumbing=0.9", "kind='system_plumbing'", "placed_by='override'",
    "status='derived'", "domain_key='subject:existing'",
])
def test_ineligible_or_already_placed_relations_do_not_block_tail_assignment(db,exclusion):
    db.execute('UPDATE rvbbit.meaning_relations SET '+exclusion)
    assert meaning_jobs.assign_mapped_sources(db) == {'assigned':1}
    assert calls(db) == 1


def test_derived_business_sources_also_wait_for_their_map(db):
    db.execute("UPDATE rvbbit.business_sources SET role='derived' WHERE schema_name='hvac_demo'")
    assert meaning_jobs.assign_mapped_sources(db)['status'] == 'awaiting_source_map'
    assert calls(db) == 0
