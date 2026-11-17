"""Build-time tests exercise the real SQLite ledger and upstream watchdog."""
import asyncio
from contextlib import contextmanager
import inspect
import json
from pathlib import Path
import tempfile
import threading

from aiohttp import web
from cron import executions, scheduler
from cron.calliope_cancellation import bind_execution_cancel, request_cancel
from gateway.platforms.api_server import APIServerAdapter
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


@contextmanager
def profile(path):
    path.mkdir(parents=True, exist_ok=True)
    token = set_hermes_home_override(str(path))
    try:
        yield
    finally:
        reset_hermes_home_override(token)


def create(session):
    row = executions.create_execution('same-job', source='computer-build-check')
    executions.mark_execution_running(row['id'])
    executions.bind_execution_session(row['id'], session)
    return row['id']


with tempfile.TemporaryDirectory(prefix='computer-cron-cancel-') as tmp:
    first, second = Path(tmp) / 'one', Path(tmp) / 'two'
    with profile(first):
        old, newer = create('cron_same-job_old'), create('cron_same-job_new')
        signal = bind_execution_cancel(old, None)
        assert not signal.is_set()
        result = request_cancel('cron_same-job_old', 'same-job')
        assert result['execution_id'] == old and result['stop_requested'] and not result['terminal']
        assert signal.is_set()
        assert not bind_execution_cancel(newer, None).is_set()
        assert request_cancel('cron_same-job_old', 'same-job')['execution_id'] == old
        try:
            request_cancel('cron_same-job_old', 'wrong-job')
        except LookupError:
            pass
        else:
            raise AssertionError('Mismatched job must not cancel')

    # Watchdog calls retain the originating profile even on another thread or
    # while another profile is selected in the gateway.
    with profile(second):
        other = create('cron_same-job_other')
        assert not bind_execution_cancel(other, None).is_set()
        observed = []
        thread = threading.Thread(target=lambda: observed.append(signal.is_set()))
        thread.start()
        thread.join(2)
        assert observed == [True]
        try:
            request_cancel('cron_same-job_old', 'same-job')
        except LookupError:
            pass
        else:
            raise AssertionError('Cancellation crossed profiles')

    class Agent:
        def __init__(self):
            self.interrupted, self.ended = threading.Event(), threading.Event()
        def run_conversation(self, prompt, task_id):
            try:
                assert self.interrupted.wait(12), 'Watchdog did not interrupt the agent'
                return {'final_response': 'interrupted'}
            finally:
                self.ended.set()
        def interrupt(self, reason):
            assert reason == 'Stopped by the person using Computer'
            self.interrupted.set()
        def get_activity_summary(self):
            return {'seconds_since_activity': 0}

    with profile(first):
        agent = Agent()
        try:
            scheduler._run_agent_with_watchdog(agent, 'probe', {'id': 'same-job'},
                'same-job', 'Computer stop test', 'test-task', signal)
        except RuntimeError as exc:
            assert 'Stopped by the person' in str(exc)
        else:
            raise AssertionError('Cancelled execution returned a normal result')
        assert agent.ended.wait(2)
        executions.finish_execution(old, success=False, error=signal.reason)
        assert request_cancel('cron_same-job_old', 'same-job')['terminal'] is True
        parent = threading.Event()
        parent.set()
        assert bind_execution_cancel(newer, parent).is_set()
        assert bind_execution_cancel(None, parent) is parent

    class Adapter:
        def __init__(self, authorized): self.authorized = authorized
        def _check_auth(self, request):
            return None if self.authorized else web.json_response({'error':'unauthorized'}, status=401)
        async def _read_json_body(self, request): return request, None
        def _profile_scope(self, name): return profile(first)

    handler = APIServerAdapter._handle_calliope_cron_stop
    body = {'session_id': 'cron_same-job_new', 'job_id': 'same-job'}
    assert asyncio.run(handler(Adapter(False), body)).status == 401
    with profile(first):
        assert not bind_execution_cancel(newer, None).is_set()
    response = asyncio.run(handler(Adapter(True), body))
    assert response.status == 200 and json.loads(response.body)['execution_id'] == newer
    assert asyncio.run(handler(Adapter(True), {'session_id': '../wrong'})).status == 400
    assert asyncio.run(handler(Adapter(True), {'session_id':'missing','job_id':'same-job'})).status == 404

assert 'bind_execution_cancel(' in inspect.getsource(scheduler.run_job)
routes = {(method, path) for method, path, _ in object.__new__(APIServerAdapter)._http_route_table()}
assert ('POST', '/v1/cron/executions/stop') in routes
print('Computer scheduled Stop targets one profile-local occurrence and interrupts its agent')
