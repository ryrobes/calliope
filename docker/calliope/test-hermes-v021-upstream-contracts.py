"""Contracts for v0.21 features that replaced or complement Calliope patches."""

import asyncio
import ast
import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace

import yaml
from mcp import types as mcp_types

from agent import auxiliary_client, codex_runtime, tool_executor
from cron import executions
from gateway.platforms import api_server_runs
from gateway.platforms.api_server import APIServerAdapter
from hermes_cli import config as hermes_config
from tools import mcp_tool, mcp_tool_handlers


# v0.21 absorbed Calliope's prior /v1/runs session-history patch. Keep a
# build-time assertion so a future upstream rebase cannot silently regress it.
run_source = inspect.getsource(api_server_runs._handle_runs)
assert "_conversation_history_for_session" in run_source
assert "not conversation_history and selected_session_id" in run_source
run_event_source = inspect.getsource(api_server_runs._make_run_event_callback)
assert 'event["arguments"] = args' in run_event_source
assert 'name in ("todo", "todo_list")' in run_event_source
execute_source = inspect.getsource(api_server_runs._execute_run)
run_tree = ast.parse(execute_source)
completion_callback = next(
    node for node in ast.walk(run_tree)
    if isinstance(node, ast.FunctionDef) and node.name == "_run_tool_complete"
)
assert [argument.arg for argument in completion_callback.args.args] == [
    "tool_call_id",
    "function_name",
    "function_args",
    "function_result",
]
assert "payload.update(receipt_fields(name, args, result))" in execute_source
tool_executor_source = re.sub(r"\s+", " ", inspect.getsource(tool_executor))
assert (
    '_safe_callback(agent.tool_complete_callback, "Tool complete", '
    "ref.call_id, ref.name, display_args, result)"
) in tool_executor_source
codex_runtime_source = re.sub(r"\s+", " ", inspect.getsource(codex_runtime))
assert (
    "args=(_stable_call_id(item, name), name, args, result)"
    in codex_runtime_source
)

# Exercise the actual native callback: file/terminal receipts cross the wire,
# but HTML bodies, shell output, and arguments stay out of progress events.
receipts = []
callback_scope = dict(vars(api_server_runs))
callback_scope.update(run_id="scope-test", self=SimpleNamespace(_run_streams={"scope-test": True}),
    loop=SimpleNamespace(call_soon_threadsafe=lambda callback, payload: callback(payload)),
    run=SimpleNamespace(put_event=receipts.append))
exec(compile(ast.Module(body=[completion_callback], type_ignores=[]), "<native-callback>", "exec"), callback_scope)
callback = callback_scope["_run_tool_complete"]
callback("write-1", "write_file", {"path": "/opt/data/app.html", "content": "PRIVATE HTML"},
         json.dumps({"files_modified": ["/opt/data/app.html"]}))
callback("check-1", "terminal", {"command": "calliope-web-check /opt/data/app.html"},
         json.dumps({"exit_code": 1, "output": "PRIVATE SOURCE ERROR"}))
callback("write-2", "write_file", {"path": "/opt/data/app.html"}, json.dumps({"error": "denied"}))
assert receipts[0]["work_receipt"]["milestone"] == "file_written"
assert receipts[1]["work_receipt"]["milestone"] == "validation_failed"
assert "work_receipt" not in receipts[2]
assert all("arguments" not in receipt and "result" not in receipt for receipt in receipts)
assert "PRIVATE" not in json.dumps(receipts)

# The cron ledger path is now profile-local upstream; Calliope only adds its
# execution/session/evidence receipt columns around that implementation.
connect_source = inspect.getsource(executions._connect)
assert "get_hermes_home().resolve()" in connect_source

# v0.21's authenticated browser-extension controller is present, but Calliope
# deliberately leaves it disabled until a controller is enrolled explicitly.
adapter = object.__new__(APIServerAdapter)
routes = {(method, path) for method, path, _handler in adapter._http_route_table()}
assert ("POST", "/v1/auxiliary/tts-rewrite") in routes
auxiliary_tts_source = inspect.getsource(APIServerAdapter._handle_auxiliary_tts_rewrite)
assert 'task = "tts_audio_tags"' in auxiliary_tts_source
assert 'entry.get("calliope_managed") is True' in auxiliary_tts_source
assert "async_call_llm(**call_kwargs)" in auxiliary_tts_source
assert '"timeout": timeout_seconds' in auxiliary_tts_source
assert "_create_agent" not in auxiliary_tts_source
assert ("POST", "/v1/browser-control/register") in routes
assert ("GET", "/v1/browser-control/ws") in routes
assert ("POST", "/v1/artifacts/upload") in routes
assert ("GET", "/v1/artifacts/download/{artifact_id}") in routes
default_config = yaml.safe_load(
    Path("/opt/hermes/cli-config.yaml.example").read_text(encoding="utf-8")
)
assert default_config["browser"]["extension_control"]["enabled"] is False


# The selected Background Tasks model is a fallback for normal Hermes work,
# but Calliope voice must use it as the primary one-shot route. Exercise the
# handler boundary with the provider call stubbed: exact messages go through,
# while no agent/session/tool inputs are introduced.
class _AuxiliaryTTSRequest:
    headers = {"Authorization": "Bearer test-key"}

    async def json(self):
        return {
            "messages": [
                {"role": "system", "content": "existing Calliope voice prompt"},
                {"role": "user", "content": "existing Calliope voice input"},
            ],
            "timeout_seconds": 42,
        }


async def _fake_auxiliary_call(**kwargs):
    auxiliary_calls.append(kwargs)
    kwargs["route_info"].update({"provider": "openrouter", "model": "utility/model"})
    return SimpleNamespace(
        model="utility/model",
        choices=[SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content="[warmly] Existing rewritten script."),
        )],
    )


original_auxiliary_call = auxiliary_client.async_call_llm
original_load_config = hermes_config.load_config_readonly
auxiliary_calls = []
try:
    auxiliary_client.async_call_llm = _fake_auxiliary_call
    hermes_config.load_config_readonly = lambda: {
        "auxiliary": {
            "tts_audio_tags": {
                "provider": "auto",
                "model": "",
                "fallback_chain": [{
                    "provider": "openrouter",
                    "model": "utility/model",
                    "calliope_managed": True,
                }],
            }
        }
    }
    adapter._api_key = "test-key"
    auxiliary_response = asyncio.run(
        adapter._handle_auxiliary_tts_rewrite(_AuxiliaryTTSRequest())
    )
finally:
    auxiliary_client.async_call_llm = original_auxiliary_call
    hermes_config.load_config_readonly = original_load_config

auxiliary_payload = json.loads(auxiliary_response.text)
assert auxiliary_response.status == 200
assert auxiliary_payload["route_source"] == "background_tasks"
assert auxiliary_payload["provider"] == "openrouter"
assert auxiliary_payload["model"] == "utility/model"
assert auxiliary_calls[0]["task"] == "tts_audio_tags"
assert auxiliary_calls[0]["provider"] == "openrouter"
assert auxiliary_calls[0]["model"] == "utility/model"
assert auxiliary_calls[0]["timeout"] == 42
assert auxiliary_calls[0]["messages"] == [
    {"role": "system", "content": "existing Calliope voice prompt"},
    {"role": "user", "content": "existing Calliope voice input"},
]
assert "tools" not in auxiliary_calls[0]
assert "main_runtime" not in auxiliary_calls[0]


# MCP 2.x changed ClientRequest into a union alias. Calliope's provenance
# forwarding must send the concrete request model. v0.21.4 split the MCP client
# (tools/mcp_tool_handlers.py owns tools/call) and absorbed Calliope's stdio-watch
# fix: the race inspects the watcher FUNCTION and constructs the coroutine exactly
# once, only when it actually races, instead of building one merely to inspect it.
class _MetadataSession:
    def __init__(self):
        self.request = None
        self.validated = False

    async def send_request(self, request, result_type):
        assert result_type is mcp_types.CallToolResult
        self.request = request
        return mcp_types.CallToolResult(content=[])

    async def _validate_tool_result(self, name, result):
        assert name == "probe"
        assert mcp_tool.mcp_field(result, "is_error", "isError", False) is False
        self.validated = True


metadata_session = _MetadataSession()
asyncio.run(mcp_tool_handlers._call_tool_with_metadata(
    metadata_session,
    "probe",
    {"value": 1},
    {"rvbbit.ai/hermes-caller": {"source": "hermes", "platform": "api_server"}},
))
assert isinstance(metadata_session.request, mcp_types.CallToolRequest)
assert metadata_session.request.params.meta["rvbbit.ai/hermes-caller"]["source"] == "hermes"
assert metadata_session.validated is True

assert "mcp_types.ClientRequest(" not in inspect.getsource(
    mcp_tool_handlers._call_tool_with_metadata
)
race_source = inspect.getsource(mcp_tool_handlers._call_tool_racing_stdio_death)
assert race_source.count("_watch_children()") == 1
assert "inspect.iscoroutinefunction(_watch_children)" in race_source
assert "watch_task = asyncio.ensure_future(_watch_children())" in race_source
assert "_call_tool_with_metadata(server.session, tool_name, args, request_metadata)" in race_source
# The provenance capture must stay on the caller thread inside _make_tool_handler;
# a patch applied with a stale --include silently becomes a no-op otherwise.
assert "_forwarded_session_metadata(" in inspect.getsource(mcp_tool_handlers._make_tool_handler)

# Two turns can share a conversation. Their exact run provenance must survive
# tool dispatch onto another thread without leaking into an unrelated task.
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from gateway.calliope_run_context import bind_run_id, current_run_id
from gateway.session_context import set_session_vars, clear_session_vars

assert "bind_run_id(run.run_id)" in inspect.getsource(api_server_runs._run_agent_sync)
tokens = set_session_vars(platform="api_server", session_id="same_notebook")
try:
    contexts = []
    for run_id in ("run_old", "run_new"):
        with bind_run_id(run_id):
            contexts.append(copy_context())
    def capture(context):
        return context.run(mcp_tool_handlers._forwarded_session_metadata,
                           {"forward_session_identity": True})
    with ThreadPoolExecutor(max_workers=2) as executor:
        metadata = list(executor.map(capture, contexts))
    assert [item["rvbbit.ai/hermes-caller"]["run_id"] for item in metadata] == ["run_old", "run_new"]
    assert current_run_id() == ""
    assert "run_id" not in mcp_tool_handlers._forwarded_session_metadata(
        {"forward_session_identity": True})["rvbbit.ai/hermes-caller"]
finally:
    clear_session_vars(tokens)

print("Hermes v0.21 upstream absorption and browser-control contracts: ok")
