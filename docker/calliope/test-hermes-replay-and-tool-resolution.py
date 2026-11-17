"""Live-trace regressions for replay boundaries and exact deferred MCP dispatch.

Runs in the pinned image without network, credentials, or model requests.
"""
import copy
from dataclasses import dataclass
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent.agent_runtime_helpers import _dedupe_tool_call_ids, repair_tool_call
from agent.conversation_loop import _run_phase
from agent.turn_context import build_api_messages
from agent.turn_tool_validation import validate_tool_calls

SQL = "mcp__Warehouse__run_sql"
WORKSPACE = "mcp__Warehouse__run_workspace_sql"
RECALL = "mcp__Warehouse__recall_calliope_sql"
SKETCH = "mcp__Warehouse__read_calliope_sketch"


def assistant_call(call_id, name=SQL):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": '{"sql":"SELECT 1"}'}}


def compacted_history():
    messages = []
    for i in range(3):
        messages.extend([{"role": "user", "content": f"old question {i}"},
                         {"role": "assistant", "content": "done"}])
    messages.append({"role": "user", "content": "current task"})
    for i in range(15):
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [assistant_call(f"old-{i}")]},
            {"role": "tool", "name": SQL, "tool_call_id": f"old-{i}", "content": "old result"},
        ])
    messages.extend([
        {"role": "assistant", "content": "", "tool_calls": [assistant_call("a"), assistant_call("b")]},
        {"role": "tool", "name": SQL, "tool_call_id": "a", "content": "successful rows A"},
        {"role": "tool", "name": SQL, "tool_call_id": "b", "content": "successful rows B"},
    ])
    return messages


def wire_agent():
    return SimpleNamespace(
        _current_turn_timestamp=1000, ephemeral_system_prompt=None,
        _copy_reasoning_content_for_api=lambda *_: None,
        _should_sanitize_tool_calls=lambda: False,
    )


def wire(messages, anchor):
    api, _ = build_api_messages(
        wire_agent(), messages, current_turn_user_idx=anchor, ext_prefetch_cache=None,
        plugin_user_context=None, moa_config=None, active_system_prompt=None,
    )
    return _dedupe_tool_call_ids(api)


@dataclass
class HistoryVerdict:
    messages: list
    action: str = "continue"


class ReplayBoundaryTests(unittest.TestCase):
    def assert_real_results(self, messages):
        results = [m["content"] for m in messages if m.get("tool_call_id") in {"a", "b"}]
        self.assertEqual(results, ["successful rows A", "successful rows B"])

    def test_phase_reanchors_both_trackers_before_history_grows_past_old_index(self):
        rebuilt = compacted_history()
        state = SimpleNamespace(messages=[], user_message="current task", current_turn_user_idx=38)
        agent = SimpleNamespace(_persist_user_message_idx=38)
        def compression_phase(agent, messages):
            return HistoryVerdict(copy.deepcopy(rebuilt))
        _run_phase(compression_phase, agent, state)
        self.assertEqual(state.current_turn_user_idx, 6)
        self.assertEqual(agent._persist_user_message_idx, 6)
        self.assert_real_results(wire(state.messages, state.current_turn_user_idx))

    def test_in_place_rebuild_also_moves_anchor(self):
        state = SimpleNamespace(messages=[{"role": "user", "content": "current task"}],
                                user_message="current task", current_turn_user_idx=0)
        agent = SimpleNamespace(_persist_user_message_idx=0)
        def rebuilding_phase(agent, messages):
            messages[:] = compacted_history()
            return HistoryVerdict(messages)
        _run_phase(rebuilding_phase, agent, state)
        self.assertEqual(state.current_turn_user_idx, 6)
        self.assertEqual(agent._persist_user_message_idx, 6)

    def test_invalid_send_anchor_cannot_split_a_live_tool_block(self):
        messages = compacted_history()
        original = copy.deepcopy(messages)
        for anchor in [37, 38, 39, -1, 500, None]:
            with self.subTest(anchor=anchor):
                self.assert_real_results(wire(messages, anchor))
        self.assertEqual(messages, original, "wire cleanup must not mutate durable history")

    def test_matching_current_turn_wins_over_later_user_notice(self):
        messages = compacted_history() + [{"role": "user", "content": "later steering"}]
        state = SimpleNamespace(messages=messages, user_message="current task", current_turn_user_idx=38)
        agent = SimpleNamespace()
        def history_phase(agent, messages):
            return HistoryVerdict(messages)
        _run_phase(history_phase, agent, state)
        self.assertEqual(state.current_turn_user_idx, 6)

    def test_no_surviving_user_disables_replay_prefix(self):
        messages = compacted_history()[7:]
        state = SimpleNamespace(messages=messages, user_message="current task", current_turn_user_idx=15)
        agent = SimpleNamespace()
        def history_phase(agent, messages):
            return HistoryVerdict(messages)
        _run_phase(history_phase, agent, state)
        self.assertEqual(state.current_turn_user_idx, -1)
        self.assertEqual(agent._persist_user_message_idx, -1)
        self.assert_real_results(wire(state.messages, state.current_turn_user_idx))

    def test_intact_resume_prefix_still_recovers_a_genuinely_unanswered_call(self):
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [assistant_call("interrupted", "terminal")]},
            {"role": "user", "content": "current task"},
        ]
        result = wire(messages, 1)
        notice = next(m for m in result if m.get("tool_call_id") == "interrupted")
        self.assertIn("Orphan recovery", notice["content"])


def validate_calls(calls, scoped, *, bridge=True):
    names = {SQL, SKETCH, "terminal"} | ({"tool_call"} if bridge else set())
    agent = SimpleNamespace(
        valid_tool_names=names, _invalid_tool_retries=0, _invalid_json_retries=0, log_prefix="",
        _uniquify_tool_call_ids=lambda _: None, _buffer_vprint=Mock(), _vprint=Mock(),
    )
    agent._repair_tool_call = lambda name: repair_tool_call(agent, name)
    agent._build_assistant_message = lambda message, _: {
        "role": "assistant", "content": "", "tool_calls": [
            {"id": c.id, "type": "function", "function": vars(c.function).copy()} for c in message.tool_calls
        ],
    }
    history = []
    with patch("agent.tool_executor._tool_search_scoped_names", return_value=frozenset(scoped)):
        verdict = validate_tool_calls(
            agent, SimpleNamespace(tool_calls=calls), "tool_calls", messages=history,
            conversation_history=[], api_call_count=1, effective_task_id="verification",
        )
    return verdict, history


def call(name, args, call_id="target"):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=args))


class ToolResolutionTests(unittest.TestCase):
    def test_exact_deferred_names_keep_operation_arguments_and_id(self):
        for name in [WORKSPACE, RECALL]:
            for arguments in [{"sql": "SELECT 1", "limit": 25}, '{"query":"sky_points"}']:
                with self.subTest(name=name, arguments=arguments):
                    tc = call(name, copy.deepcopy(arguments))
                    verdict, _ = validate_calls([tc], {name})
                    self.assertEqual(verdict.action, "ok")
                    self.assertEqual(tc.id, "target")
                    self.assertEqual(tc.function.name, "tool_call")
                    expected = json.loads(arguments) if isinstance(arguments, str) else arguments
                    self.assertEqual(json.loads(tc.function.arguments), {
                        "calls": [{"name": name, "arguments": expected}]})

    def test_out_of_scope_exact_name_is_not_repaired_to_another_operation(self):
        for name in [WORKSPACE, RECALL, "mcp__OtherTenant__run_sql"]:
            with self.subTest(name=name):
                tc = call(name, '{"sql":"SELECT 1"}')
                verdict, history = validate_calls([tc], set())
                self.assertEqual(verdict.action, "continue")
                self.assertEqual(tc.function.name, name)
                self.assertEqual(history[-1]["role"], "tool")
                self.assertIn(name, history[-1]["content"])

    def test_hidden_bridge_does_not_make_capabilities_available(self):
        tc = call(WORKSPACE, '{}')
        verdict, _ = validate_calls([tc], {WORKSPACE}, bridge=False)
        self.assertEqual(verdict.action, "continue")
        self.assertEqual(tc.function.name, WORKSPACE)

    def test_invalid_arguments_are_not_silently_wrapped_or_executed(self):
        for args in ['{broken', '[]', ['not', 'an', 'object']]:
            with self.subTest(args=args):
                tc = call(WORKSPACE, args)
                verdict, _ = validate_calls([tc], {WORKSPACE})
                self.assertEqual(verdict.action, "continue")
                self.assertEqual(tc.function.name, WORKSPACE)

    def test_mixed_visible_and_deferred_calls_both_survive(self):
        direct = call(SQL, '{"sql":"SELECT 1"}', 'direct')
        deferred = call(RECALL, '{"receipt_id":3792}', 'deferred')
        verdict, _ = validate_calls([direct, deferred], {RECALL})
        self.assertEqual(verdict.action, "ok")
        self.assertEqual(direct.function.name, SQL)
        self.assertEqual(deferred.function.name, "tool_call")

    def test_unknown_remote_names_never_fuzzy_match(self):
        agent = SimpleNamespace(valid_tool_names={SQL, SKETCH, "connectors__delete_file", "terminal"})
        for name in [WORKSPACE, RECALL, "mcp__Warehouse__run_sq", "mcp__Warehouse__run_sql_tool", "connectors__delete_files"]:
            with self.subTest(name=name):
                self.assertIsNone(repair_tool_call(agent, name))
        self.assertEqual(repair_tool_call(agent, "terminal_tool"), "terminal")
        self.assertEqual(repair_tool_call(agent, "terminl"), "terminal")


if __name__ == "__main__":
    unittest.main(verbosity=2)
