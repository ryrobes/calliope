"""Thread briefs and Floor cards state what is true now, without identifiers."""
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import calliope

ROOT = Path(__file__).resolve().parents[2]
WHEN = datetime(2026, 10, 16, 4, 18, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, rows): self.rows = rows
    def fetchone(self): return self.rows[0] if self.rows else None
    def fetchall(self): return self.rows


class _Conn:
    """Answers the brief's four reads: opening ask, totals, latest turns, actions."""
    def __init__(self, actions):
        self.actions, self.sql = actions, []
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def execute(self, sql, params=None):
        self.sql.append(sql)
        if 'LIMIT 1' in sql and 'user_message' in sql:
            return _Rows([{'ordinal': 1, 'user_message': 'Refresh the statistics on the Seattle incidents table'}])
        if 'count(*)' in sql:
            return _Rows([{'n': 2, 'newest': 2}])
        if 'calliope_action_runs' in sql:
            return _Rows(self.actions)
        return _Rows([{'ordinal': 2, 'user_message': 'Approve it', 'assistant_message':
                       'ANALYZE is frozen for approval under receipt 013ee465-264a-40ab-92c4-df7a17468682.',
                       'error': None, 'status': 'complete'}])


def _action(**kw):
    return {'title': 'Run local RVBBIT ANALYZE SQL', 'status': 'planned', 'approved_at': None,
            'completed_at': None, 'error': None, 'review_state': '', **kw}


def test_action_outcomes_read_as_current_state_without_identifiers():
    outcome = calliope._session_action_outcome
    assert outcome(_action()) == 'Run local RVBBIT ANALYZE SQL: waiting for approval; not run yet'
    assert outcome(_action(review_state='retired')).endswith('approval expired; it was not run')
    assert outcome(_action(status='complete', approved_at=WHEN, completed_at=WHEN)) == \
        'Run local RVBBIT ANALYZE SQL: completed after approval (Oct 16 04:18 UTC)'
    assert outcome(_action(status='failed', error='permission\n denied')).endswith('failed: permission denied')


def test_a_finished_approval_changes_the_brief_input_and_its_hash():
    job = {'session_id': '9d1d38a3-8d0e-434c-99d8-0e32d459c34e', 'synopsis': 'ANALYZE awaits approval.'}
    pending = calliope._session_synopsis_input(lambda: _Conn([_action()]), job)
    done = calliope._session_synopsis_input(
        lambda: _Conn([_action(status='complete', approved_at=WHEN, completed_at=WHEN)]), job)
    assert 'Actions and approvals (current state; overrides older turns):' in done[0]
    assert 'completed after approval' in done[0] and 'waiting for approval' in pending[0]
    assert pending[2] != done[2], 'the outcome must change the hash so the brief is rewritten'
    assert '013ee465' not in done[0].split('Actions and approvals')[1]


def test_brief_instruction_prefers_final_state_and_business_words(monkeypatch):
    seen = {}

    class Conn(_Conn):
        def execute(self, sql, params=None):
            if 'to_regprocedure' in sql:
                return _Rows([{'clover3': True}])
            if 'clover_llm_apply' in sql:
                seen['instruction'] = params[1]
            return _Rows([{'synopsis': 'Statistics on the Seattle incidents table were refreshed after approval.'}])
    calliope._generate_session_synopsis(lambda: Conn([]), 'source', 'owner@example.com')
    text = seen['instruction']
    assert 'current truth' in text and 'Never include identifiers' in text and 'rather than ANALYZE' in text


def test_finishing_an_action_enqueues_its_thread_brief(monkeypatch):
    enqueued = []
    monkeypatch.setattr(calliope, '_enqueue_session_synopsis', lambda factory, sid: enqueued.append(sid))

    class Conn(_Conn):
        def execute(self, sql, params=None):
            self.sql.append(sql)
            return _Rows([{'session_id': 'aa2fc0c8-26e0-4d1b-99f9-8729f138f206'}])
    calliope._finish_action_run(lambda: Conn([]), 'run-1', 'complete', result={'ok': True})
    assert enqueued == ['aa2fc0c8-26e0-4d1b-99f9-8729f138f206']


def test_floor_prompt_migration_drops_receipt_numbers_and_state_titles():
    sql = (ROOT / 'crates/pg_rvbbit/sql/migrations/0478_floor_presentation_truth.sql').read_text()
    prompt = re.search(r"SELECT '(.*)'::text", sql, re.S).group(1)
    assert 'Prefer receipt numbers' not in prompt and 'Never include receipt numbers, UUIDs' in prompt
    assert 'not its momentary state' in prompt and 'no longer true' in prompt
    assert '"0478_floor_presentation_truth"' in (ROOT / 'crates/pg_rvbbit/src/migrations.rs').read_text()
    lock = json.loads((ROOT / 'release/schema-lock.json').read_text())
    assert '0478_floor_presentation_truth' in lock['migrations']
    release = json.loads((ROOT / 'release/calliope-release.json').read_text())
    assert json.dumps(release).count('0478_floor_presentation_truth') >= 1


def test_system_notes_are_not_called_team_work():
    source = (Path(__file__).parent / 'calliope/calliope.js').read_text()
    start = source.index('  const SYSTEM_NOTES = {')
    end = source.index('\n  }\n', source.index('function systemNote(turn)')) + 4
    script = source[start:end] + """
const note = (sender, kind='agent_message') => systemNote({turn_kind: kind, user_message: `[AGENT MESSAGE — note from ${sender}]\\nbody\\n[/AGENT MESSAGE]`});
console.log(JSON.stringify({
  brief: note('Daily Brief')?.running, connection: note('Connection')?.summary,
  stage: note('Stage edit')?.status, argus: note('Argus'), chat: note('Daily Brief', 'chat'),
}));"""
    out = json.loads(subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True).stdout)
    assert out == {'brief': "is writing today's synopsis", 'connection': 'Connection update',
                   'stage': 'Applying your Stage edit', 'argus': None, 'chat': None}


def test_brief_cleaner_keeps_underscores_inside_names():
    clean = calliope._clean_session_synopsis
    assert clean('Counted **crime_feeds.nypd_complaints**: _1,719,140_ rows.') == \
        'Counted crime_feeds.nypd_complaints: 1,719,140 rows.'
    assert clean('# Brief: `rvbbit.calliope_turns` grew') == 'Brief: rvbbit.calliope_turns grew'


def test_daily_brief_note_reads_plainly_and_skips_unchanged_editions():
    source = (Path(__file__).parent / 'calliope.py').read_text()
    start = source.index('"Edition digest:\\n"') - 4000
    block = source[start:source.index('async def resolve_daily_brief')]
    assert '"continue_work": "recent work"' in block and '"needs_now": "needs you"' in block
    assert 'f"- [{section}] {label}" + (f" — {stands}"' in block
    assert "AND (status IN ('queued','delivering') OR body=%s))" in block
