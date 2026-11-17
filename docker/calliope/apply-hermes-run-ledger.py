#!/usr/bin/env python3
"""Install guarded telemetry seams in the pinned Hermes v0.21.4 source."""
import os
from pathlib import Path

root = Path(os.environ.get('HERMES_SOURCE_ROOT', '/opt/hermes'))

def replace(path, old, new):
    path = root / path
    text = path.read_text()
    if text.count(old) != 1:
        raise SystemExit(f'Pinned Hermes ledger seam changed: {path}')
    path.write_text(text.replace(old, new))

replace('hermes_cli/lifecycle.py',
        'def _observe(hook_name: str, **kwargs: Any) -> None:\n',
        'def _observe(hook_name: str, **kwargs: Any) -> None:\n'
        '    from gateway.calliope_ledger import observe\n'
        '    observe(hook_name, **kwargs)\n')
replace('hermes_cli/lifecycle.py',
        '    from hermes_cli import plugins\n\n    return plugins.has_hook(hook_name)',
        '    from gateway.calliope_ledger import handles_hook\n'
        '    if handles_hook(hook_name):\n        return True\n'
        '    from hermes_cli import plugins\n\n    return plugins.has_hook(hook_name)')
replace('agent/turn_facade.py',
        '    def run_conversation(\n',
        '    from gateway.calliope_ledger import agent_span as _calliope_agent_span\n'
        '    @_calliope_agent_span\n    def run_conversation(\n')
replace('gateway/platforms/api_server_runs.py',
        '    self._run_statuses[run_id] = current\n',
        '    self._run_statuses[run_id] = current\n'
        '    if status != previous_status or status in TERMINAL_STATUSES:\n'
        '        from gateway.calliope_ledger import observe\n'
        '        observe("run.status", **current)\n')
replace('agent/api_request_hooks.py',
        '        return self._sanitize_hook_payload({"method": "POST", "body": body})',
        '        from gateway.calliope_ledger import measured_payload\n'
        '        return measured_payload(self._sanitize_hook_payload({"method": "POST", "body": body}), body)')
replace('agent/api_request_hooks.py',
        '        return self._sanitize_hook_payload(\n',
        '        from gateway.calliope_ledger import measured_payload\n'
        '        payload = self._sanitize_hook_payload(\n')
replace('agent/api_request_hooks.py',
        '\n    def _invoke_api_request_error_hook(\n',
        '        return measured_payload(payload, response)\n\n'
        '    def _invoke_api_request_error_hook(\n')
# Ordinary default-route completions need the same billing identity as explicit
# overrides. Preserve the upstream sanitizer and confirmed-route enforcement.
old = '''        if requested_runtime or route or confirmed_runtime_lock or (route_source and route_source != "global"):
            runtime = self._turn_runtime_metadata(
                agent, route=route, requested_runtime=requested_runtime,
                route_source=route_source, confirmed_runtime_lock=confirmed_runtime_lock)
            if isinstance(result, dict):
                result["runtime"] = runtime
            usage["runtime"] = runtime
'''
replace('gateway/platforms/api_server.py', old, '\n'.join(line[4:] for line in old.splitlines()[1:])+'\n')

# This retry loop is inside one model hook pair. Give each stream attempt its
# own diagnostic receipt, correlated to the existing logical attempt.
replace('agent/chat_completion_helpers.py',
        '                try:\n                    self.result["response"] = _with_stream_emitters(\n',
        '                from gateway.calliope_ledger import stream_attempt_started, stream_attempt_finished\n'
        '                self.clients.diag = {}\n'
        '                _receipt = stream_attempt_started(self.agent, _stream_attempt + 1, _max_stream_retries + self._compat_retries + 1)\n'
        '                try:\n                    self.result["response"] = _with_stream_emitters(\n')
replace('agent/chat_completion_helpers.py',
        '                    return  # success\n                except Exception as e:\n                    self._close_managed_stream()',
        '                    stream_attempt_finished(_receipt, self.clients.diag, response=self.result["response"])\n'
        '                    return  # success\n                except Exception as e:\n'
        '                    stream_attempt_finished(_receipt, self.clients.diag, error=e)\n'
        '                    self._close_managed_stream()')
replace('agent/chat_completion_helpers.py',
        '            diag["bytes"] = int(diag.get("bytes", 0)) + _estimate_chunk_bytes(chunk)',
        '            diag["bytes"] = int(diag.get("bytes", 0)) + _estimate_chunk_bytes(chunk)\n'
        '            from gateway.calliope_ledger import stream_chunk_metadata\n'
        '            stream_chunk_metadata(diag, chunk)')
