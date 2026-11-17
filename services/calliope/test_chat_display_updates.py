"""Durable display notes are separate from drafts and internal tool events."""
from contextlib import nullcontext
from types import SimpleNamespace

import calliope


def test_display_notes_keep_order_and_paragraphs_without_replay_duplicates():
    rows = [
        {'turn_id': 'one', 'text': 'First paragraph.\n\nSecond paragraph.'},
        {'turn_id': 'one', 'text': 'First paragraph. Second paragraph.'},
        {'turn_id': 'one', 'text': ''},
        {'turn_id': 'one', 'text': 'Checking the details now.'},
        {'turn_id': 'two', 'text': 'A separate turn.'},
    ]
    calls = []
    def execute(sql, parameters):
        calls.append((sql, parameters))
        return SimpleNamespace(fetchall=lambda: rows)
    conn = SimpleNamespace(transaction=nullcontext, execute=execute)
    assert calliope._turn_display_updates(conn, ['one', 'two']) == {
        'one': ['First paragraph.\n\nSecond paragraph.', 'Checking the details now.'],
        'two': ['A separate turn.'],
    }
    assert calls[0][1] == (['one', 'two'],)
    assert "event_type='calliope.progress'" in calls[0][0]
    assert 'ORDER BY turn_id,sequence' in calls[0][0]
    assert calliope._turn_display_updates(conn, []) == {}
    assert len(calls) == 1


def test_long_committed_speech_survives_but_draft_snapshots_remain_bounded():
    text = 'A paragraph.\n\n' * 1000
    progress = calliope._compact_turn_stream_event(calliope._sse('calliope.progress', {'text': text}))
    draft = calliope._compact_turn_stream_event(calliope._sse('calliope.draft', {'text': text}))
    assert progress[1]['text'] == text
    assert len(draft[1]['text']) == 4000
