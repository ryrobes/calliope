#!/usr/bin/env python3
"""Verify Computer teaching against PostgreSQL; every fixture write rolls back.

Run with the appliance's Python dependencies/module path and WAREHOUSE_DSN.
Does not use a desktop, submit memories, or send a continuation to a real task.
"""
from contextlib import contextmanager
import json
import os
import uuid

import psycopg
from psycopg.rows import dict_row

import desktop_access as access
import desktop_memory as memory


def main():
    actor = 'computer-feedback-acceptance@example.test'
    sid, lid = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg.connect(os.environ['WAREHOUSE_DSN'], row_factory=dict_row)

    @contextmanager
    def factory():
        yield conn

    try:
        conn.execute('INSERT INTO rvbbit.calliope_sessions(id,owner_email,title,hermes_session_id) VALUES(%s::uuid,%s,%s,%s)',
                     (sid, actor, 'Disposable Computer teaching acceptance', 'acceptance-' + sid))
        run = access.open_run(factory, sid, actor, 'Demonstrate and correct synthetic report selection')
        rid = run['id']
        conn.execute("UPDATE rvbbit.calliope_desktop_runs SET status='active' WHERE id=%s::uuid", (rid,))
        first = access.append_event(factory, rid, actor, 'control.changed', {'mode': 'human', 'generation': 101}, 'test-human')
        saved = memory.save_feedback(factory, sid, actor, rid, 'Choose the export date.', lid, human_generation=101)
        assert saved['demonstration']['id'] == first['id']
        assert saved['demonstration']['complete_takeover'] is False
        assert memory.save_feedback(factory, sid, actor, rid, 'Choose the export date.', lid, human_generation=101) == saved
        try:
            memory.save_feedback(factory, sid, actor, rid, 'Changed text', lid, human_generation=101)
        except access.DesktopError as exc:
            assert exc.code == 'IDEMPOTENCY_CONFLICT'
        else:
            raise AssertionError('Retry changed the explanation')
        # A failed evidence write must also roll back the lesson insertion.
        original = access.append_event_on_connection
        failed_id = str(uuid.uuid4())
        def fail(*args):
            raise RuntimeError('simulated evidence failure')
        access.append_event_on_connection = fail
        try:
            try:
                memory.save_feedback(factory, sid, actor, rid, 'Must roll back.', failed_id)
            except RuntimeError:
                pass
            else:
                raise AssertionError('Evidence failure was swallowed')
        finally:
            access.append_event_on_connection = original
        assert not conn.execute('SELECT 1 FROM rvbbit.calliope_desktop_lessons WHERE id=%s::uuid', (failed_id,)).fetchone()
        end = access.append_event(factory, rid, actor, 'control.changed', {'mode': 'agent', 'generation': 103}, 'test-agent')
        historical = memory.save_feedback(factory, sid, actor, rid, 'Correction: choose the latest completed export.',
                                          demonstration_id=first['id'])
        assert historical['demonstration']['end_event_id'] == end['id']
        assert historical['demonstration']['complete_takeover'] is True
        try:
            memory.save_feedback(factory, sid, actor, rid, 'Stale handoff', human_generation=101)
        except access.DesktopError as exc:
            assert exc.code == 'CONTROL_CHANGED'
        else:
            raise AssertionError('Stale takeover accepted')
        try:
            memory.save_feedback(factory, sid, actor, rid, 'Wrong span', demonstration_id=str(uuid.uuid4()))
        except access.DesktopError as exc:
            assert exc.code == 'DEMONSTRATION_NOT_FOUND'
        else:
            raise AssertionError('Unrelated span accepted')
        try:
            memory.save_feedback(factory, sid, 'someone-else@example.test', rid, 'Unauthorized')
        except Exception as exc:
            assert getattr(exc, 'status', None) in {403, 404}
        else:
            raise AssertionError('Wrong identity accepted')
        access.ready_message(factory, rid, 103)
        message = conn.execute('SELECT body FROM rvbbit.calliope_agent_messages WHERE to_session_id=%s::uuid', (sid,)).fetchone()
        assert 'Correction: choose the latest completed export.' in message['body']
        assert first['id'] in message['body']
        row = conn.execute(memory._LESSON_SOURCE + 'WHERE l.id=%s::uuid', (historical['lesson_id'],)).fetchone()
        content = memory.retention_body(row)['items'][0]
        assert json.loads(content['metadata']['demonstration']) == historical['demonstration']
        print('PASS: atomic lesson/evidence, stable retries, historical span, stale/foreign rejection, immediate continuation, retention provenance; rollback follows')
    finally:
        conn.rollback()
        conn.close()


if __name__ == '__main__':
    main()
