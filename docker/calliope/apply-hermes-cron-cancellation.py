"""Connect an exact-occurrence Stop request to Hermes' existing cron watchdog."""
import os
from pathlib import Path

root = Path(os.environ.get('HERMES_SOURCE_ROOT', '/opt/hermes'))


def replace(name, old, new):
    path = root / name
    source = path.read_text()
    if source.count(old) != 1:
        raise SystemExit(f'Pinned Hermes cancellation seam changed: {name}')
    path.write_text(source.replace(old, new))


replace('cron/executions.py',
        '    add_column_if_missing(conn, "executions", "hermes_session_id", "hermes_session_id TEXT")\n',
        '    add_column_if_missing(conn, "executions", "hermes_session_id", "hermes_session_id TEXT")\n'
        '    add_column_if_missing(conn, "executions", "calliope_stop_requested", "calliope_stop_requested INTEGER NOT NULL DEFAULT 0")\n')

replace('cron/scheduler.py',
        '    early, prompt = _prepare_job_prompt(job, job_id, job_name, extra_prompt, cancel_event)\n',
        '    from cron.calliope_cancellation import bind_execution_cancel\n'
        '    cancel_event = bind_execution_cancel(execution_id or job.get("execution_id"), cancel_event)\n'
        '    early, prompt = _prepare_job_prompt(job, job_id, job_name, extra_prompt, cancel_event)\n')

replace('cron/scheduler.py',
        '        if agent is not None and hasattr(agent, "interrupt"):\n'
        '            agent.interrupt("Cron fire claim ownership was lost")\n'
        '        raise RuntimeError(f"Cron job \'{job_name}\' lost its durable fire claim ownership")\n',
        '        reason = getattr(cancel_event, "reason", "Cron fire claim ownership was lost")\n'
        '        if agent is not None and hasattr(agent, "interrupt"):\n'
        '            agent.interrupt(reason)\n'
        '        raise RuntimeError(f"Cron job \'{job_name}\': {reason}")\n')

replace('gateway/platforms/api_server.py',
        '            ("POST", "/v1/commands/prepare", self._handle_command_prepare),\n',
        '            ("POST", "/v1/commands/prepare", self._handle_command_prepare),\n'
        '            ("POST", "/v1/cron/executions/stop", self._handle_calliope_cron_stop),\n')

replace('gateway/platforms/api_server.py',
        '    async def _handle_cron_fire(self, request: "web.Request") -> "web.Response":\n',
        '''    async def _handle_calliope_cron_stop(self, request: "web.Request") -> "web.Response":
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err
        body, err = await self._read_json_body(request)
        if err:
            return err
        from cron.calliope_cancellation import request_cancel
        try:
            with self._profile_scope(_api_request_profile.get()):
                result = await asyncio.to_thread(request_cancel, body.get("session_id"), body.get("job_id"))
            return web.json_response(result)
        except LookupError as exc:
            return web.json_response({"error": str(exc)}, status=404)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

    async def _handle_cron_fire(self, request: "web.Request") -> "web.Response":
''')
print('Applied exact-occurrence Computer cancellation to Hermes cron')
