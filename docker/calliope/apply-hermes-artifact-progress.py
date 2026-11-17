"""Install build observation at accepted stream chunks and real tool boundaries."""
import os
from pathlib import Path

root = Path(os.environ.get('HERMES_SOURCE_ROOT', '/opt/hermes'))


def patch(file, old, new):
    path = root / file
    source = path.read_text()
    if source.count(old) != 1:
        raise SystemExit('Artifact progress seam changed: ' + file + ': ' + old[:80])
    path.write_text(source.replace(old, new))


patch('agent/chat_completion_helpers.py', '            stream_chunk_metadata(diag, chunk)\n',
      '            stream_chunk_metadata(diag, chunk)\n'
      '            from gateway.calliope_artifact_progress import observe_chunk\n'
      '            observe_chunk(self.agent, chunk)\n')
patch('agent/codex_runtime.py', '        has_progress = _codex_event_has_content(event)\n',
      '        if _writer_is_current():\n'
      '            from gateway.calliope_artifact_progress import observe_chunk\n'
      '            observe_chunk(agent, event)\n'
      '        has_progress = _codex_event_has_content(event)\n')
patch('agent/tool_executor.py', '    agent._current_tool = function_name\n',
      '    from gateway.calliope_artifact_progress import observe_tool\n'
      '    observe_tool(agent, ref, started=True)\n'
      '    agent._current_tool = function_name\n')
patch('agent/tool_executor.py', '    if not blocked and agent.tool_complete_callback:\n',
      '    if not blocked:\n'
      '        from gateway.calliope_artifact_progress import observe_tool\n'
      '        observe_tool(agent, ref, result=result)\n'
      '    if not blocked and agent.tool_complete_callback:\n')
patch('gateway/platforms/api_server_runs.py', '        if event_type == "tool.result_ready":\n',
      '        if event_type == "artifact.progress":\n'
      '            from artifact_progress import public_progress\n'
      '            progress = public_progress(kwargs.get("progress"))\n'
      '            if progress:\n'
      '                store = getattr(self, "_calliope_stage_events", None)\n'
      '                if store is not None:\n'
      '                    store.record_work(run_id, progress)\n'
      '                _push(_run_event(run_id, "artifact.progress", progress=progress))\n'
      '            return\n'
      '        if event_type == "tool.result_ready":\n')
patch('gateway/platforms/api_server.py', '            elif event_type == "tool.result_ready":\n',
      '            elif event_type == "artifact.progress":\n'
      '                from artifact_progress import public_progress\n'
      '                progress = public_progress(kwargs.get("progress"))\n'
      '                if progress:\n'
      '                    events.enqueue("artifact.progress", {"progress": progress})\n'
      '            elif event_type == "tool.result_ready":\n')

patch('gateway/platforms/api_server_runs.py', '            for event in page["stage_events"]:\n',
      '            if cursor == 0:\n'
      '                for progress in page.get("artifact_progress", []):\n'
      '                    await response.write(_api_server._sse_frame(_run_event(run_id, "artifact.progress", progress=progress)))\n'
      '            for event in page["stage_events"]:\n')
