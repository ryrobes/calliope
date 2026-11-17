"""Bootstrap contract and restricted-role tests. Use an expendable test Postgres."""
import importlib.util
import os
from pathlib import Path
import tempfile

import pytest

spec = importlib.util.spec_from_file_location('research', Path(__file__).with_name('research.py'))
research = importlib.util.module_from_spec(spec)
spec.loader.exec_module(research)


def test_preparation_preserves_credentials_and_has_no_sqlite():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        research.prepare(path)
        first = (path/'research.env').read_text()
        research.prepare(path)
        assert first == (path/'research.env').read_text()
        assert (path/'research.env').stat().st_mode & 0o777 == 0o600
        assert (path/'research-bootstrap.sql').stat().st_mode & 0o777 == 0o600
        sql = (path/'research-bootstrap.sql').read_text()
        assert 'NOSUPERUSER' in sql and 'rvbbit.force_heap_scan' in sql
        assert 'JSONB' in sql and 'SQLite' not in sql


@pytest.mark.skipif(not os.environ.get('CLOVER_RESEARCH_ADMIN_TEST_DSN'), reason='expendable Postgres required')
def test_restricted_writer_and_complete_evidence_storage():
    import psycopg
    with psycopg.connect(os.environ['CLOVER_RESEARCH_ADMIN_TEST_DSN'], autocommit=True) as admin:
        admin.execute('CREATE TABLE IF NOT EXISTS public.research_test_private(secret text)')
        with psycopg.connect(os.environ['CLOVER_RESEARCH_TEST_DSN']) as writer:
            assert writer.execute("SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user").fetchone() == (False,False,False)
            assert writer.execute("SELECT has_table_privilege('public.research_test_private','select')").fetchone() == (False,)
            assert writer.execute("SELECT count(*) FROM clover_research.research_runs").fetchone()[0] > 0
            row = writer.execute('''SELECT s.evidence_text, jsonb_array_length(c.snapshot_ids_json)
                FROM clover_research.research_run_sources s JOIN clover_research.research_claims c USING(run_id)
                WHERE s.evidence_text IS NOT NULL LIMIT 1''').fetchone()
            assert row and row[0] and row[1] > 0
