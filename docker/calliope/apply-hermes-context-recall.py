"""Apply against the pinned Hermes plus existing Calliope overlays; fail on seam drift."""
import os
from pathlib import Path

root = Path(os.environ.get("HERMES_SOURCE_ROOT", "/opt/hermes"))


def patch(file, old, new, count=1):
    path = root / file
    content = path.read_text()
    if content.count(old) != count:
        raise SystemExit("Context recall seam changed: " + file + ": " + old[:90])
    path.write_text(content.replace(old, new))


patch("toolsets.py", '"read_file", "read_tool_result", "write_file"',
      '"read_file", "read_tool_result", "session_context", "write_file"', 2)
patch("model_tools.py", '_AGENT_LOOP_TOOLS = {"todo_list", "memory", "session_search", "delegate_task"}',
      '_AGENT_LOOP_TOOLS = {"todo_list", "memory", "session_search", "session_context", "delegate_task"}')
patch("agent/inline_tool_executors.py", 'def _session_search(agent, args: dict, ctx: InlineToolContext) -> Any:',
      '''def _session_context(agent, args: dict, ctx: InlineToolContext) -> Any:
    from tools.calliope_session_context import session_context
    return session_context(args, db=agent._get_session_db_for_recall(), session_id=agent.session_id)


def _session_search(agent, args: dict, ctx: InlineToolContext) -> Any:''')
patch("agent/inline_tool_executors.py", '    "session_search": _session_search,',
      '    "session_search": _session_search,\n    "session_context": _session_context,')
patch("agent/inline_tool_executors.py", 'frozenset({"todo_list", "session_search", "memory"})',
      'frozenset({"todo_list", "session_search", "session_context", "memory"})')
patch("agent/tool_dispatch_helpers.py", '    "session_search",', '    "session_search",\n    "session_context",')
patch("agent/session_persistence.py", '    sync_flushed_message_markers(batch_msgs, batch_rows)',
      '''    sync_flushed_message_markers(batch_msgs, batch_rows)
    # Index committed records asynchronously. Persistence and model progress never wait on inference.
    from tools.calliope_middle_memory import queue_index
    queue_index(agent._session_db, agent.session_id)''')
patch("agent/tool_executor.py", '    persisted_result = function_result\n',
      '''    from tools.calliope_middle_memory import archive_result
    archive_result(getattr(agent, "_session_db", None), agent.session_id, tool_call_id, function_name, function_result)
    persisted_result = function_result
''')
patch("agent/agent_runtime_helpers.py", '"todo_list", "session_search", "memory",',
      '"todo_list", "session_search", "session_context", "memory",')
patch("agent/context_compressor.py", '    return content.startswith("[") and " chars)" in content and len(content) < 400',
      '    return content.startswith("[Retained tool evidence]") or (content.startswith("[") and " chars)" in content and len(content) < 400)')
patch("agent/context_compressor.py", '        result[idx] = {**msg, "content": _summarize_tool_result(tool_name, tool_args, content)}',
      '''        from tools.calliope_session_context import retain_tool_evidence
        digest = retain_tool_evidence(tool_name, tool_args, content, msg.get("tool_call_id"))
        if digest == content or len(digest) >= len(content):
            return False
        result[idx] = {**msg, "content": digest}''')
patch("agent/context_compressor.py", '            result[i] = _rewritten(msg, _lean_recovery_stub(msg.get("tool_name") or "", len(content), session_id))',
      '''            from tools.calliope_session_context import retain_tool_evidence
            digest = retain_tool_evidence(msg.get("tool_name") or "tool", "", content, msg.get("tool_call_id"))
            if digest == content or len(digest) >= len(content):
                continue
            result[i] = _rewritten(msg, digest)''')
patch("agent/context_compressor.py", '        if getattr(self, "tail_mode", "lean") != "lean":\n            return summary',
      '''        from tools.calliope_session_context import recovery_footer
        summary = recovery_footer(summary)
        from tools.calliope_compaction_scope import annotate_scope
        summary = annotate_scope(summary, getattr(self, "_calliope_summary_scope", None))
        if getattr(self, "tail_mode", "lean") != "lean":
            return summary''')
patch("agent/context_compressor.py", '            "Produce only the structured summary; do not add a greeting, preamble, or prefix. "',
      '''            "Produce only the structured summary; do not add a greeting, preamble, or prefix. "
            "Keep exact query findings, row counts, mappings, failed attempts and tool_call_id recovery IDs "
            "when relevant to unfinished work. Retained tool evidence contains facts, not merely call history. "
            "Use session_context retrieval for omitted details instead of planning to rerun completed work. "''')
patch("agent/context_compressor.py", '        # Focus guidance goes last so it takes precedence.\n',
      '''        from tools.calliope_compaction_scope import scope_prompt
        prompt += scope_prompt(getattr(self, "_calliope_summary_scope", None))
        # Focus guidance goes last so it takes precedence.
''')
patch("agent/context_compressor.py", '        turns_to_summarize = scan.turns_to_summarize\n',
      '''        turns_to_summarize = scan.turns_to_summarize
        from tools.calliope_compaction_scope import capture_scope
        self._calliope_summary_scope = capture_scope(messages, compress_start, compress_end)
''')
patch("agent/context_compressor.py", 'Update "Active State" to reflect current state.',
      'Update "Active State" to reflect state at the end of the compacted segment; newer retained messages take precedence.')
patch("agent/context_compressor.py", '[Current working state — include:',
      '[Working state observed at the end of this segment, not the whole session. Mark missing evidence as unknown. Include:')
patch("agent/context_compressor.py", '        self._previous_summary = self._summary_has_user_turn = self._last_summary_error = None',
      '''        self._calliope_summary_scope = None
        self._previous_summary = self._summary_has_user_turn = self._last_summary_error = None''')
patch("agent/context_compressor.py", '                tool_actions.append(_summarize_tool_result(tool_name, tool_args, text or ""))',
      '''                from tools.calliope_session_context import retain_tool_evidence
                tool_actions.append(retain_tool_evidence(tool_name, tool_args, msg.get("content") or "",
                                                         msg.get("tool_call_id")))''')
patch("agent/context_compressor.py", 'enumerate((assistant_actions + tool_actions)[:12], start=1)',
      'enumerate((tool_actions[-8:] + assistant_actions[-4:]), start=1)')
patch("agent/context_compressor.py", '''f"session_search(query='<keywords>', session_id='{session_id}') — "''',
      '''"session_context(query='<keywords>') and read its message_id or tool_call_id — "''')
patch("agent/tool_executor.py", '    timeout_s = None if function_name in _SEQUENTIAL_DEADLINE_EXEMPT_TOOLS else _resolve_sequential_tool_timeout()',
      '''    from agent.calliope_tool_deadlines import tool_deadline
    timeout_s = None if function_name in _SEQUENTIAL_DEADLINE_EXEMPT_TOOLS else tool_deadline(
        _resolve_sequential_tool_timeout(), [(function_name, function_args)])''')
patch("agent/tool_executor.py", '    timeout_s = _resolve_concurrent_tool_timeout()\n    batch = _ConcurrentBatch',
      '''    from agent.calliope_tool_deadlines import tool_deadline
    timeout_s = tool_deadline(_resolve_concurrent_tool_timeout(), [(pc.name, pc.args) for pc in parsed_calls])
    batch = _ConcurrentBatch''')
patch("agent/tool_executor.py", '            _interrupt_worker_tids(agent, worker_tid)\n',
      '            _interrupt_worker_tids(agent, worker_tid, reason="tool execution deadline exceeded")\n')
patch("agent/tool_executor.py", '                _interrupt_worker_tids(agent, worker_tids)\n',
      '                _interrupt_worker_tids(agent, worker_tids, reason="tool batch deadline exceeded")\n')
patch("tools/mcp_tool_handlers.py", '        return tool_error("MCP call interrupted: user sent a new message")',
      '''        from tools.interrupt import get_interrupt_reason
        reason = get_interrupt_reason() or "execution interrupted"
        return tool_error("MCP call interrupted: " + reason,
                          error_type="tool_timeout" if "deadline" in reason else "tool_interrupted")''')
