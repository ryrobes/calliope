"""Live Stage delivery seams in the pinned Hermes; fail closed on upstream drift."""
import os
from pathlib import Path

root = Path(os.environ.get("HERMES_SOURCE_ROOT", "/opt/hermes"))


def patch(file, old, new):
    path = root / file
    content = path.read_text()
    if content.count(old) != 1:
        raise SystemExit("Stage delivery seam changed: " + file + ": " + old[:100])
    path.write_text(content.replace(old, new))


executor = "agent/tool_executor.py"
patch(executor, "                    self.results[index] = outcome\n", """                    self.results[index] = outcome
                    if not outcome.blocked and not outcome.is_error and not self.gate.abandoned.is_set():
                        # Independent Stage receipt; transcript append/flush still
                        # happens in original call order in _append_batch_results.
                        from gateway.calliope_stage_events import emit_ready
                        emit_ready(agent, outcome.ref, outcome.result)
""")

runs = "gateway/platforms/api_server_runs.py"
patch(runs, "    self._run_idempotency_store = store_factory()\n", """    self._run_idempotency_store = store_factory()
    from gateway.calliope_stage_events import StageEvents
    self._calliope_stage_events = StageEvents(getattr(self._run_idempotency_store, "_db_path", None))
""")
patch(runs, "            self._run_idempotency_store.close()\n", """            self._run_idempotency_store.close()
        if getattr(self, "_calliope_stage_events", None) is not None:
            self._calliope_stage_events.close()
""")
patch(runs, '        if event_type == "tool.started":\n', '''        if event_type == "tool.result_ready":
            from stage_delivery import receipt_fields
            from gateway.calliope_stage_events import publish_receipt
            fields = receipt_fields(tool_name, args, kwargs.get("result"))
            if not fields:
                return
            payload = _run_event(run_id, "tool.result_ready", tool=tool_name,
                                call_id=str(kwargs.get("tool_call_id") or ""), **fields)
            payload = publish_receipt(self, run_id, payload)
            _push(payload)
            return
        if event_type == "tool.started":
''')
patch(runs, '''        if run_id not in self._run_streams:
            return
        name = str(function_name or "")''', '''        # Keep receipts after an SSE disconnect too. Authenticated status polling
        # replays them while the run is still executing.
        name = str(function_name or "")''')
patch(runs, '''        payload.update(receipt_fields(name, args, result))
        try:''', '''        payload.update(receipt_fields(name, args, result))
        from gateway.calliope_stage_events import publish_receipt
        payload = publish_receipt(self, run_id, payload)
        try:''')
patch(runs, '''    _, status, _, _, err = _load_owned_run(
        self, request, _api_server=_api_server, permission="status", active_fallback=True)
    return err or web.json_response(status)''', '''    run_id, status, _, _, err = _load_owned_run(
        self, request, _api_server=_api_server, permission="status", active_fallback=True)
    if err is not None:
        return err
    if "stage_after" in request.query:
        try:
            after = max(0, int(request.query["stage_after"]))
        except (TypeError, ValueError):
            after = 0
        status = {**status, **self._calliope_stage_events.read(run_id, after)}
    return web.json_response(status)''')
patch(runs, '''    await response.prepare(request)
    try:
        while True:''', '''    await response.prepare(request)
    try:
        # Reattach without losing already-consumed receipts. Duplicate queue
        # entries are harmless: Calliope reconciles by exact tool/output identity.
        cursor = 0
        while True:
            page = self._calliope_stage_events.read(run_id, cursor)
            for event in page["stage_events"]:
                await response.write(_api_server._sse_frame(event))
            cursor = page["stage_cursor"]
            if not page["stage_has_more"]:
                break
        while True:''')

legacy = "gateway/platforms/api_server.py"
patch(legacy, '''            elif event_type in {"tool.started", "tool.failed"}:
                events.enqueue(event_type,''', '''            elif event_type == "tool.result_ready":
                from stage_delivery import receipt_fields
                fields = receipt_fields(tool_name, args, kwargs.get("result"))
                if fields:
                    events.enqueue(event_type, {"message_id": message_id, "tool_name": tool_name,
                                               "call_id": str(kwargs.get("tool_call_id") or ""), **fields})
            elif event_type in {"tool.started", "tool.failed"}:
                events.enqueue(event_type,''')
