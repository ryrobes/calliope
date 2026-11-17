"""A brief-style tool call whose streamed arguments never end is stopped, and the
stop ends the turn instead of being retried."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(os.environ.get('HERMES_SOURCE_ROOT', '/opt/hermes'))
sys.path.insert(0, str(ROOT))
from gateway.calliope_runaway_tool_arguments import DEFAULT_MAX_CHARS, RunawayToolArguments, observe


def chat_chunk(text='', name='', call_id='', index=0):
    function = SimpleNamespace(name=name or None, arguments=text or None)
    call = SimpleNamespace(index=index, id=call_id or None, function=function)
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(tool_calls=[call]))])


def stream(diag, name, total, step=500, call_id='call-1'):
    observe(diag, chat_chunk(name=name, call_id=call_id, text='{"task":"'))
    sent = 9
    while sent < total:
        observe(diag, chat_chunk(text='x' * step))
        sent += step


class GuardTests(unittest.TestCase):
    def test_a_runaway_delegation_brief_is_stopped_past_the_ceiling(self):
        diag = {}
        stream(diag, 'mcp__Roundtable__delegate_calliope_specialist', DEFAULT_MAX_CHARS - 1000)
        with self.assertRaises(RunawayToolArguments) as stopped:
            stream(diag, 'mcp__Roundtable__delegate_calliope_specialist', 2000, call_id='')
        self.assertIn('delegate_calliope_specialist', str(stopped.exception))
        self.assertIn('was not run', str(stopped.exception))

    def test_content_tools_carry_large_arguments(self):
        for name in ('mcp__Warehouse__publish_dashboard', 'write_file', 'patch', 'execute_code'):
            stream({}, name, DEFAULT_MAX_CHARS * 4)

    def test_a_new_call_at_the_same_index_starts_counting_again(self):
        diag = {}
        stream(diag, 'run_sql', DEFAULT_MAX_CHARS - 1000, call_id='a')
        stream(diag, 'run_sql', DEFAULT_MAX_CHARS - 1000, call_id='b')

    def test_anthropic_tool_use_deltas_are_counted(self):
        diag = {}
        observe(diag, {'type': 'content_block_start', 'index': 1,
                       'content_block': {'type': 'tool_use', 'id': 'tu1', 'name': 'ask_calliope'}})
        with self.assertRaises(RunawayToolArguments):
            for _ in range(DEFAULT_MAX_CHARS // 1000 + 2):
                observe(diag, {'type': 'content_block_delta', 'index': 1,
                               'delta': {'type': 'input_json_delta', 'partial_json': 'y' * 1000}})

    def test_the_ceiling_is_configurable_and_zero_turns_it_off(self):
        with patch.dict(os.environ, {'CALLIOPE_BRIEF_TOOL_ARGS_MAX_CHARS': '0'}):
            stream({}, 'delegate_task', DEFAULT_MAX_CHARS * 3)
        with patch.dict(os.environ, {'CALLIOPE_BRIEF_TOOL_ARGS_MAX_CHARS': '2000'}):
            with self.assertRaises(RunawayToolArguments):
                stream({}, 'delegate_task', 3000)


class InstalledTests(unittest.TestCase):
    def test_the_stream_chunk_counter_raises_outside_the_best_effort_block(self):
        from agent.chat_completion_helpers import _StreamingCall
        call = _StreamingCall.__new__(_StreamingCall)
        call.agent = SimpleNamespace(_touch_activity=lambda *_: None)
        call.last_chunk_time = {'t': 0.0}
        diag = {}
        call._count_chunk(diag, chat_chunk(name='delegate_calliope_specialist', call_id='c', text='{"task":"'))
        with patch.dict(os.environ, {'CALLIOPE_BRIEF_TOOL_ARGS_MAX_CHARS': '100'}):
            with self.assertRaises(RunawayToolArguments):
                call._count_chunk(diag, chat_chunk(text='z' * 200))

    def test_the_classifier_ends_the_turn_instead_of_retrying(self):
        from agent.error_classifier import classify_api_error
        verdict = classify_api_error(RunawayToolArguments('Stopped a runaway tool call'),
                                     provider='openrouter', model='openai/gpt-6.1-sol')
        self.assertEqual(verdict.reason.value, 'format_error')
        self.assertFalse(verdict.retryable)
        self.assertFalse(verdict.should_fallback)


if __name__ == '__main__':
    unittest.main()
