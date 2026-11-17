"""Outcome provenance and real PostgreSQL learning lifecycle checks."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

import desktop_learning as learning


def example(jid='job'):
    code = "rows = page.locator('tbody tr').count()\nassert rows == expected_rows\nprint({'visible_rows': rows})"
    output = "{'visible_rows': 5}"
    sources = [{'id': jid + '/code.py', 'kind': 'code', 'job_id': jid, 'status': 'completed', 'text': code},
               {'id': jid + '/stdout.txt', 'kind': 'output', 'job_id': jid, 'status': 'completed', 'text': output}]
    body = {'id': None, 'key': 'invoice-filter-check', 'title': 'Check filtered invoices', 'application': 'Invoice review',
            'route': 'browser', 'when_to_use': 'Check invoice rows after filtering', 'inputs': ['expected_rows'],
            'steps': ['Attach to visible Chrome and read the filtered table rows'],
            'checks': ['Assert the observed row count equals expected_rows'], 'pitfalls': ['Wait for filtering to settle'],
            'example': code, 'evidence': [{'source': s['id'], 'quote': s['text']} for s in sources]}
    return {'methods': [body], 'evaluations': []}, {'sources': sources, 'candidates': []}


@pytest.mark.parametrize('change', ['output_missing', 'made_up_quote', 'foreign_method', 'literal_token', 'reuse_without_code'])
def test_reviewer_cannot_promote_unsupported_or_stale_evidence(change):
    raw, packet = example()
    m = raw['methods'][0]
    if change == 'output_missing': m['evidence'].pop()
    if change == 'made_up_quote': m['evidence'][1]['quote'] = 'I observed something else'
    if change == 'foreign_method': m['id'] = str(uuid4())
    if change == 'literal_token': m['steps'] = ['Click token s00000001:1']
    if change == 'reuse_without_code':
        mid = str(uuid4()); packet['candidates'] = [{'id': mid, 'version': 1}]
        raw = {'methods': [], 'evaluations': [{'id': mid, 'version': 1, 'used': True,
            'outcome': 'uncertain', 'reason': 'Maybe', 'evidence': [m['evidence'][1]]}]}
    with pytest.raises(ValueError): learning.validate(raw, packet)


def test_credentials_are_not_copied_to_derived_memory():
    text = 'password="swordfish"\n{"access_token": "privatevalue"}\nAuthorization: Bearer abc.def.secret\nhttps://x/?token=privatevalue&x=1'
    value = learning.clean(text)
    assert 'swordfish' not in value and 'privatevalue' not in value and 'abc.def.secret' not in value
    assert 'x=1' in value


@pytest.fixture
def db(tmp_path, monkeypatch):
    dsn = os.environ.get('COMPUTER_LEARNING_TEST_DSN')
    if not dsn:
        pytest.skip('requires a disposable PostgreSQL database')
    factory = lambda: psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    with factory() as c:
        assert c.execute("SELECT to_regnamespace('rvbbit') ns").fetchone()['ns'] is None
        c.execute("""CREATE SCHEMA rvbbit;
            CREATE TABLE rvbbit.calliope_desktops(id uuid PRIMARY KEY);
            CREATE TABLE rvbbit.calliope_desktop_runs(id uuid PRIMARY KEY,desktop_id uuid,session_id uuid,requester text,
                goal text,status text,outcome text,created_at timestamptz DEFAULT now(),ended_at timestamptz DEFAULT now());
            CREATE TABLE rvbbit.calliope_turns(id uuid PRIMARY KEY,session_id uuid,author_email text,turn_kind text DEFAULT 'chat',
                user_message text,created_at timestamptz DEFAULT now());
            CREATE TABLE rvbbit.calliope_workbench_events(id uuid PRIMARY KEY,desktop_run_id uuid,sequence bigint,event_type text,
                actor_email text,idempotency_key text,payload jsonb,created_at timestamptz DEFAULT now(),
                UNIQUE(desktop_run_id,idempotency_key),UNIQUE(desktop_run_id,sequence));""")
    try:
        learning.ensure(factory)
        learning.ensure(factory)
        desktop, session = uuid4(), uuid4()
        with factory() as c: c.execute('INSERT INTO rvbbit.calliope_desktops VALUES(%s)', (desktop,))
        def run(actor='one@example.com', status='finished'):
            rid, jid = uuid4(), uuid4()
            with factory() as c:
                row = c.execute("INSERT INTO rvbbit.calliope_desktop_runs VALUES(%s,%s,%s,%s,'Check filtered invoices',%s,'Claimed done',now(),now()) RETURNING *",
                                (rid, desktop, session, actor, status)).fetchone()
            learning.access.append_event(factory, rid, actor, 'evidence.archived', {}, 'evidence.archived')
            path = tmp_path / 'computer-evidence' / str(rid) / 'jobs' / str(jid)
            path.mkdir(parents=True)
            raw, packet = example(str(jid))
            (path/'archived.json').write_text(json.dumps({'id': str(jid), 'status': 'completed', 'language': 'python', 'exit_code': 0,
                                                         'started_at': 1, 'ended_at': 2, 'intent': 'Check filtered invoices'}))
            for source in packet['sources']: (path/source['id'].split('/')[1]).write_text(source['text'])
            (path/'stderr.txt').write_text('')
            return row, raw
        monkeypatch.setenv('CALLIOPE_COMPUTER_LEARNING', 'on')
        monkeypatch.setattr(learning.httpx.Client, 'post', lambda *a, **kw: (_ for _ in ()).throw(TimeoutError()))
        yield SimpleNamespace(factory=factory, run=run, config=SimpleNamespace(file_root=tmp_path), desktop=desktop, session=session)
    finally:
        with factory() as c: c.execute('DROP SCHEMA rvbbit CASCADE')


def learn(db):
    run, raw = db.run()
    learning.enqueue(db.factory)
    assert learning.drain_one(db.factory, db.config, lambda c, p: (raw, {'model': 'test-reviewer'}))
    with db.factory() as c:
        row = c.execute('SELECT * FROM rvbbit.calliope_computer_methods').fetchone()
        assert row is not None
    return run, row, raw


def evaluate(db, run, method, outcome='supported', used=True):
    data, _ = learning.packet(db.factory, db.config, run['id'])
    refs = [{'source': s['id'], 'quote': s['text']} for s in data['sources'] if s['kind'] in {'code', 'output'}]
    result = {'methods': [], 'evaluations': [{'id': str(method['id']), 'version': method['version'],
        'used': used, 'outcome': outcome, 'reason': 'Rows were observed in the browser', 'evidence': refs}]}
    learning.enqueue(db.factory)
    assert learning.drain_one(db.factory, db.config, lambda c, p: (result, {'model': 'test-reviewer'}))


def test_end_to_end_archive_review_recall_and_async_memory(db):
    run, method, _ = learn(db)
    assert method['successes'] == 1 and method['status'] == 'tentative'
    result = learning.recall(db.factory, run, run['requester'])
    assert result['items'][0]['id'] == str(method['id'])
    assert result['items'][0]['steps'] and result['items'][0]['checks']
    assert learning.recall(db.factory, run, 'other@example.com')['items'] == []
    assert learning.recall(db.factory, {**run, 'desktop_id': uuid4()}, run['requester'])['items'] == []
    assert learning.recall(db.factory, {**run, 'goal': 'Play a song'}, run['requester'])['items'] == []
    calls = []
    def transport(verb, bank, path, body=None):
        calls.append((verb, body))
        return {'success': True, 'async': True, 'operation_id': 'op'} if verb == 'POST' else {'status': 'completed'}
    assert learning.retain_one(db.factory, transport)
    with db.factory() as c:
        v = c.execute('SELECT * FROM rvbbit.calliope_computer_method_versions').fetchone()
        assert v['retention_status'] == 'pending'
        c.execute('UPDATE rvbbit.calliope_computer_method_versions SET next_attempt_at=now()')
    assert calls[0][1]['items'][0]['tags'] == ['computer-method']
    assert learning.retain_one(db.factory, transport)
    with db.factory() as c:
        assert c.execute('SELECT retention_status FROM rvbbit.calliope_computer_method_versions').fetchone()['retention_status'] == 'retained'


def test_three_distinct_runs_establish_then_failed_reuse_withdraws_method(db):
    _, method, _ = learn(db)
    for _ in range(2):
        run, _ = db.run()
        evaluate(db, run, method)
    with db.factory() as c:
        m = c.execute('SELECT * FROM rvbbit.calliope_computer_methods').fetchone()
        assert m['successes'] == 3 and m['status'] == 'established'
    run, _ = db.run()
    evaluate(db, run, method, 'failed')
    assert learning.recall(db.factory, run, run['requester'])['items'] == []
    with db.factory() as c:
        m = c.execute('SELECT * FROM rvbbit.calliope_computer_methods').fetchone()
        assert m['failures'] == 1 and m['status'] == 'needs_review'


def test_reanalysis_does_not_multiply_successes_and_revision_starts_new_counts(db):
    run, method, raw = learn(db)
    learning.access.append_event(db.factory, run['id'], run['requester'], 'feedback.received', {'feedback': 'These five rows are correct'}, 'feedback-1')
    evaluate(db, run, method)
    with db.factory() as c:
        assert c.execute('SELECT successes FROM rvbbit.calliope_computer_methods').fetchone()['successes'] == 1
    raw['methods'][0]['id'] = str(method['id'])
    raw['methods'][0]['pitfalls'].append('Wait for the spinner to disappear')
    learning.access.append_event(db.factory, run['id'], run['requester'], 'feedback.received', {'feedback': 'Wait for the spinner next time'}, 'feedback-2')
    learning.enqueue(db.factory)
    learning.drain_one(db.factory, db.config, lambda c, p: (raw, {}))
    with db.factory() as c:
        m = c.execute('SELECT * FROM rvbbit.calliope_computer_methods').fetchone()
        assert m['version'] == 2 and m['successes'] == 1
        assert c.execute('SELECT count(*) n FROM rvbbit.calliope_computer_method_versions').fetchone()['n'] == 2


def test_claims_are_exclusive_and_expired_results_cannot_commit(db):
    run, raw = db.run()
    learning.enqueue(db.factory)
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(lambda _: learning.claim(db.factory), range(2)))
    assert sum(x is not None for x in claims) == 1
    old = next(x for x in claims if x)
    data, _ = learning.packet(db.factory, db.config, run['id'])
    with db.factory() as c: c.execute("UPDATE rvbbit.calliope_computer_reviews SET next_attempt_at=now()")
    new = learning.claim(db.factory)
    assert new['lease'] != old['lease']
    assert not learning.persist(db.factory, old, run, learning.validate(raw, data), {}, data)
    assert learning.persist(db.factory, new, run, learning.validate(raw, data), {}, data)
    assert not learning.persist(db.factory, new, run, learning.validate(raw, data), {}, data)


def test_model_outage_retries_without_blocking_or_inventing_learning(db):
    db.run(); learning.enqueue(db.factory)
    def outage(*args): raise TimeoutError('private provider body')
    for _ in range(3):
        assert learning.drain_one(db.factory, db.config, outage)
        with db.factory() as c: c.execute('UPDATE rvbbit.calliope_computer_reviews SET next_attempt_at=now()')
    with db.factory() as c:
        review = c.execute('SELECT * FROM rvbbit.calliope_computer_reviews').fetchone()
        assert review['status'] == 'failed' and review['result']['error'] == 'TimeoutError'
        assert review['result']['stage'] == 'review'
        assert 'private provider body' not in json.dumps(review['result'])
        assert c.execute('SELECT count(*) n FROM rvbbit.calliope_computer_methods').fetchone()['n'] == 0


def test_chat_correction_requeues_only_matching_author_and_run_window(db):
    run, _, _ = learn(db)
    def message(actor):
        with db.factory() as c:
            c.execute('INSERT INTO rvbbit.calliope_turns(id,session_id,author_email,user_message) VALUES(%s,%s,%s,%s)',
                      (uuid4(), db.session, actor, 'Wrong month; use the export date instead'))
    message('other@example.com'); learning.enqueue(db.factory)
    assert learning.claim(db.factory) is None
    message(run['requester']); learning.enqueue(db.factory)
    assert learning.claim(db.factory)['run_id'] == run['id']
    data, _ = learning.packet(db.factory, db.config, run['id'])
    assert len([s for s in data['sources'] if s['kind'] == 'human']) == 1
    db.run()
    message(run['requester'])
    old, _ = learning.packet(db.factory, db.config, run['id'])
    assert len([s for s in old['sources'] if s['kind'] == 'human']) == 1


def test_forgetting_is_scoped_durable_and_preserves_history(db, monkeypatch):
    run, method, raw = learn(db)
    monkeypatch.setattr(learning.access, 'run_for_session', lambda *a: run)
    with pytest.raises(learning.access.DesktopError):
        learning.retire(db.factory, db.session, 'other@example.com', run['id'], method['id'])
    assert learning.retire(db.factory, db.session, run['requester'], run['id'], method['id'])['status'] == 'retired'
    assert learning.recall(db.factory, run, run['requester'])['items'] == []
    db.run()
    # Get the new run's own evidence, not the first run's sources.
    with db.factory() as c:
        latest = c.execute('SELECT * FROM rvbbit.calliope_desktop_runs ORDER BY created_at DESC LIMIT 1').fetchone()
    data, _ = learning.packet(db.factory, db.config, latest['id'])
    raw['methods'][0]['evidence'] = [{'source': s['id'], 'quote': s['text']} for s in data['sources'] if s['kind'] in {'code','output'}]
    learning.enqueue(db.factory)
    learning.drain_one(db.factory, db.config, lambda c, p: (raw, {}))
    with db.factory() as c:
        assert c.execute('SELECT status FROM rvbbit.calliope_computer_methods').fetchone()['status'] == 'retired'
        assert c.execute('SELECT count(*) n FROM rvbbit.calliope_computer_method_versions').fetchone()['n'] == 1


def test_large_evidence_is_explicitly_omitted_not_silently_shortened(db):
    run, _ = db.run()
    path = next((Path(db.config.file_root)/'computer-evidence'/str(run['id'])).glob('jobs/*/stdout.txt'))
    path.write_text('x' * 200000)
    data, _ = learning.packet(db.factory, db.config, run['id'])
    assert data['omissions'][0]['reason'] == 'over review budget'
    assert not any(s['kind'] == 'output' for s in data['sources'])


def test_failure_archive_keeps_response_private_and_redacted(tmp_path):
    work = {'run_id': str(uuid4()), 'lease': str(uuid4()), 'attempts': 1}
    diagnostic = learning.review_diagnostic(SimpleNamespace(file_root=tmp_path), work, 'validation',
        learning.ReviewFailure('Unattributed evidence'), {'password': 'swordfish', 'methods': []}, {'model': 'test'})
    assert diagnostic['validation_message'] == 'Unattributed evidence'
    assert 'response' not in diagnostic
    path = tmp_path / 'computer-evidence' / work['run_id'] / diagnostic['diagnostic_file']
    assert path.stat().st_mode & 0o777 == 0o600
    assert 'swordfish' not in path.read_text()
    assert json.loads(path.read_text())['reviewer']['model'] == 'test'


def test_validation_failure_retry_receives_reason_without_promoting_bad_evidence(db):
    run, raw = db.run(); learning.enqueue(db.factory)
    raw['methods'][0]['evidence'][1]['quote'] = 'made up observation'
    learning.drain_one(db.factory, db.config, lambda c, p: (raw, {'model': 'test'}))
    with db.factory() as c:
        row = c.execute('SELECT * FROM rvbbit.calliope_computer_reviews').fetchone()
        assert row['result']['validation_message'] == 'Unattributed evidence'
        assert row['result']['stage'] == 'validation'
        assert c.execute('SELECT count(*) n FROM rvbbit.calliope_computer_methods').fetchone()['n'] == 0
        c.execute('UPDATE rvbbit.calliope_computer_reviews SET next_attempt_at=now()')
    def corrected(config, packet):
        assert packet['previous_review_failure'] == 'Unattributed evidence'
        raw['methods'][0]['evidence'] = [{'source': s['id'], 'quote': s['text']} for s in packet['sources'] if s['kind'] in {'code','output'}]
        return raw, {'model': 'test'}
    learning.drain_one(db.factory, db.config, corrected)
    with db.factory() as c:
        assert c.execute('SELECT status FROM rvbbit.calliope_computer_reviews').fetchone()['status'] == 'complete'
        assert c.execute('SELECT successes FROM rvbbit.calliope_computer_methods').fetchone()['successes'] == 1


def test_short_reviewer_references_resolve_to_durable_evidence_ids():
    raw, packet = example('long-original-job-id')
    for index, source in enumerate(packet['sources'], 1):
        source['ref'] = f'e{index}'
        raw['methods'][0]['evidence'][index - 1]['source'] = source['ref']
    result = learning.validate(raw, packet)
    assert [e['source'] for e in result['methods'][0]['evidence']] == [s['id'] for s in packet['sources']]
    raw['methods'][0]['evidence'][0]['source'] = 'e999'
    with pytest.raises(learning.ReviewFailure, match='Unknown evidence source at reference 1'):
        learning.validate(raw, packet)
