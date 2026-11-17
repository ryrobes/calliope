"""Durable cancellation of one claimed Calliope schedule occurrence."""
from contextvars import copy_context
import logging

from cron import executions

log = logging.getLogger(__name__)


def request_cancel(session_id, job_id):
    if not isinstance(session_id, str) or not 1 <= len(session_id) <= 240:
        raise ValueError('A bounded execution session_id is required')
    if not isinstance(job_id, str) or not 1 <= len(job_id) <= 160:
        raise ValueError('A bounded job_id is required')
    with executions._transaction() as conn:
        rows = conn.execute(
            'SELECT * FROM executions WHERE hermes_session_id=? AND job_id=?',
            (session_id, job_id),
        ).fetchall()
        if not rows:
            raise LookupError('Execution not found in this profile')
        if len(rows) != 1:
            raise ValueError('Execution session is ambiguous; no occurrence was stopped')
        row = dict(rows[0])
        active = row['status'] in ('claimed', 'running')
        if active:
            conn.execute('UPDATE executions SET calliope_stop_requested=1 WHERE id=?', (row['id'],))
        return {'execution_id': row['id'], 'status': row['status'], 'stop_requested': active,
                'terminal': not active}


class ExecutionCancel:
    def __init__(self, execution_id, parent):
        self.execution_id, self.parent = execution_id, parent
        # The watchdog may run outside the scheduler's thread. Keep the original
        # profile scope, never whichever profile happens to poll this object.
        self.context = copy_context()
        self.reason = 'Cron fire claim ownership was lost'

    def is_set(self):
        if self.parent is not None and self.parent.is_set():
            self.reason = getattr(self.parent, 'reason', 'Cron fire claim ownership was lost')
            return True
        try:
            row = self.context.copy().run(executions.get_execution, self.execution_id)
        except Exception as exc:
            log.warning('Execution cancellation check deferred: %s', type(exc).__name__)
            return False
        if row and row.get('calliope_stop_requested'):
            self.reason = 'Stopped by the person using Computer'
            return True
        return False


def bind_execution_cancel(execution_id, parent):
    return ExecutionCancel(str(execution_id), parent) if execution_id else parent
