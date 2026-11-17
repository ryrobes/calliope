"""A human handback continues an occurrence, rather than rerunning a schedule."""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import uuid

import pytest

import calliope
import desktop_access as access


def uid():
    return str(uuid.uuid4())


class Ledger:
    def __init__(self):
        self.desktop = {'id': uid(), 'session_id': uid(), 'status': 'active',
                        'owner_email': 'owner@example.com', 'goal': 'Export report'}
        self.assignment = {'id': uid(), 'work_order_id': uid(), 'status': 'blocked',
                           'instruction_snapshot': 'Export the current report once.',
                           'context_snapshot': {'report_date': '2026-09-24'},
                           'result_summary': 'Login expired', 'result_details': {'login': 'expired'},
                           'steps': ['Reached login'], 'completed_at': datetime.now(timezone.utc)}
        self.messages, self.events, self.updates = {}, [], []
        self.feedback = []

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def transaction(self): return nullcontext()
    def fetchone(self): return self.result
    def fetchall(self): return self.result
    def execute(self, query, params=()):
        self.result = None
        if query.startswith('SELECT r.*,s.owner_email'):
            self.result = self.desktop
        elif query.startswith('SELECT 1 FROM rvbbit.calliope_agent_messages'):
            self.result = self.messages.get(params[0])
        elif query.startswith('SELECT a.* FROM rvbbit.calliope_work_order_runs'):
            self.result = self.assignment
        elif query.startswith('SELECT l.feedback'):
            self.result = self.feedback
        elif query.startswith('INSERT INTO rvbbit.calliope_workbench_events'):
            self.events.append(json.loads(params[4]))
        elif query.startswith('UPDATE rvbbit.calliope_work_order_runs'):
            self.assignment['status'] = 'running'
            self.assignment['context_snapshot'].update(json.loads(params[0]))
        elif query.startswith('INSERT INTO rvbbit.calliope_agent_messages'):
            self.messages[params[0]] = {'body': params[4], 'payload': json.loads(params[5])}
        elif query.startswith('UPDATE rvbbit.calliope_work_'):
            self.updates.append((query, params))
        else:
            raise AssertionError(query)
        return self


def test_handback_preserves_blocked_receipt_and_resumes_same_occurrence_once():
    ledger = Ledger()
    original = deepcopy(ledger.assignment)
    access.ready_message(lambda: ledger, ledger.desktop['id'], 7)
    assert ledger.assignment['status'] == 'running'
    assert len(ledger.events) == 1
    assert ledger.events[0]['previous_result']['result_summary'] == 'Login expired'
    message = next(iter(ledger.messages.values()))
    assert message['payload']['work_order_run_id'] == original['id']
    assert original['instruction_snapshot'] in message['body']
    assert '"report_date": "2026-09-24"' in message['body']
    assert 'do not replay interrupted code' in message['body']
    assert 'finish_calliope_work_order_run' in message['body']
    # The continuation can block again. An archive retry of the old handback
    # must not reactivate it; a fresh human handback has another generation.
    ledger.assignment['status'] = 'blocked'
    access.ready_message(lambda: ledger, ledger.desktop['id'], 7)
    assert ledger.assignment['status'] == 'blocked'
    assert len(ledger.messages) == 1 and len(ledger.events) == 1
    access.ready_message(lambda: ledger, ledger.desktop['id'], 9)
    assert ledger.assignment['status'] == 'running'
    assert len(ledger.messages) == 2


def test_handback_waits_for_scheduler_blocked_receipt_before_waking_notebook():
    ledger = Ledger()
    ledger.assignment['status'] = 'running'
    access.ready_message(lambda: ledger, ledger.desktop['id'], 7)
    assert not ledger.messages
    ledger.assignment['status'] = 'blocked'
    access.ready_message(lambda: ledger, ledger.desktop['id'], 7)
    assert len(ledger.messages) == 1


def test_handback_delivers_explanation_and_correction_before_memory_extraction():
    ledger = Ledger()
    ledger.feedback = [{'feedback': 'Correction: choose export date.', 'demonstration': {'id': 'new'}},
                       {'feedback': 'I showed the date picker.', 'demonstration': {'id': 'old'}}]
    access.ready_message(lambda: ledger, ledger.desktop['id'], 7)
    body = next(iter(ledger.messages.values()))['body']
    assert body.index('I showed the date picker.') < body.index('Correction: choose export date.')
    assert '"id": "new"' in body


@pytest.mark.parametrize('status', ['complete', 'failed'])
def test_handback_cannot_resurrect_finished_assignment(status):
    ledger = Ledger()
    ledger.assignment['status'] = status
    access.ready_message(lambda: ledger, ledger.desktop['id'], 7)
    assert not ledger.messages and not ledger.events
    ledger.assignment['status'] = 'blocked'
    ledger.desktop['status'] = 'finished'
    access.ready_message(lambda: ledger, ledger.desktop['id'], 7)
    assert not ledger.messages and not ledger.events


def test_old_scheduler_cannot_finish_occurrence_after_notebook_handback():
    ledger = Ledger()
    ledger.assignment['context_snapshot'] = {'computer_continuation': {'generation': 7}}
    class Connection(Ledger):
        def execute(self, query, params=()):
            assert query.startswith('SELECT r.*,w.title')
            self.result = ledger.assignment
            return self
    with pytest.raises(PermissionError, match='earlier scheduler'):
        calliope.finish_work_order_run(Connection, ledger.assignment['id'], 'complete', 'Old result',
                                      scheduler_session_id='cron_old')


def test_scheduler_reconciliation_cannot_fail_a_handed_back_occurrence():
    ledger = Ledger()
    ledger.assignment.update(status='running', context_snapshot={'computer_continuation': {'generation': 7}})
    class Connection(Ledger):
        def execute(self, query, params=()):
            # This is the locked read at commit time. A handback may have
            # happened after the reconciler selected the unfinished run.
            assert query.startswith('SELECT r.*,w.title')
            self.result = ledger.assignment
            return self
    result = calliope.finish_work_order_run(Connection, ledger.assignment['id'], 'failed',
        'Old scheduler completed without a receipt', scheduler_reconciliation=True)
    assert result['scheduler_ownership_transferred'] is True
    assert result['status'] == 'running'


@pytest.mark.parametrize('job_present', [True, False])
@pytest.mark.parametrize('status,handed_back', [('running', True), ('complete', True), ('failed', False), ('blocked', False)])
def test_schedule_refresh_preserves_notebook_owned_result(job_present, status, handed_back):
    import ast
    import asyncio
    from pathlib import Path
    tree = ast.parse(Path(calliope.__file__).read_text())
    func = next(node for node in ast.walk(tree)
                if isinstance(node, ast.AsyncFunctionDef) and node.name == 'refresh_work_order_schedule_status')
    wid = uid()
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def transaction(self): return nullcontext()
        def execute(self, query, params=()):
            if query.startswith('SELECT id,trigger_kind,status,hermes_job_id'):
                self.result = [{'id':wid,'trigger_kind':'once','status':'active','hermes_job_id':'old-job'}]
            elif query.startswith('SELECT context_snapshot'):
                self.result = {'status':status, 'context_snapshot':{'computer_continuation':{'generation':7}} if handed_back else {}}
            else:
                raise AssertionError('Scheduler must not overwrite notebook-owned state: ' + query)
            return self
        def fetchall(self): return self.result
        def fetchone(self): return self.result
    async def hermes(*args):
        return {'jobs': [{'id':'old-job','state':'completed','latest_execution':{'status':'completed'}}] if job_present else []}
    namespace = {**vars(calliope), 'conn_factory':Connection, 'config':None, '_hermes_json':hermes}
    exec(compile(ast.Module(body=[func], type_ignores=[]), str(calliope.__file__), 'exec'), namespace)
    asyncio.run(namespace['refresh_work_order_schedule_status']('owner@example.com', wid))


@pytest.mark.parametrize('status', ['blocked', 'running'])
def test_computer_occurrence_blocks_new_schedule_even_after_old_lease(status):
    wid, sid = uid(), uid()
    class Connection(Ledger):
        def execute(self, query, params=()):
            if query.startswith('SELECT * FROM rvbbit.calliope_work_orders'):
                self.result = {'source_session_id': sid, 'definition_version': 1,
                               'status': 'active', 'hermes_job_id': 'job_original'}
            elif query.startswith('SELECT id,session_id,started_at,status'):
                assert "d.status IN ('active','queued')" in query
                self.result = {'id': uid(), 'status': status, 'computer_pending': True,
                               'started_at': datetime.now(timezone.utc) - timedelta(days=2)}
            else:
                raise AssertionError('A paused task must not start a replacement: ' + query)
            return self
    with pytest.raises(RuntimeError, match='overlap policy skipped'):
        calliope.begin_work_order_run(Connection, wid, sid, 1, 'job_original', scheduler_session_id='cron_new')
