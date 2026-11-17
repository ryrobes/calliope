"""Provider matrix and route boundaries; run in the pinned Hermes image too."""
import asyncio
import ast
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

root = Path(os.environ.get("HERMES_SOURCE_ROOT", "/opt/hermes"))
module = root / "gateway/calliope_data_completion.py"
if not module.exists():
    module = Path(__file__).with_name("hermes-data-completion.py")
spec = importlib.util.spec_from_file_location("data_completion", module)
data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(data)


def body(**changes):
    return {"provider": "openai-codex", "model": "selected-model",
            "messages": [{"role": "user", "content": "Return JSON"}],
            "max_tokens": 4000, "timeout_seconds": 90, **changes}


class CompletionTests(unittest.TestCase):
    def setup_provider(self, provider="openai-codex", response=None):
        client = NS(base_url="https://provider.test/v1", close=Mock())
        runtime = Mock(return_value={"provider": provider, "api_key": "private",
                                    "base_url": client.base_url, "api_mode": "chat_completions"})
        response = response or NS(id="reply-1", model="wire-model", usage=NS(
            prompt_tokens=100, completion_tokens=20, prompt_tokens_details=NS(cached_tokens=40)),
            choices=[NS(message=NS(content='{"sql":"SELECT 1"}', tool_calls=None), finish_reason="stop")])
        aux = NS(scoped_runtime_main=lambda value: nullcontext(),
                 resolve_provider_client=Mock(return_value=(client, "wire-model")),
                 _build_call_kwargs=Mock(return_value={"model": "wire-model", "max_tokens": 4000}),
                 _provider_requires_stream=Mock(return_value=False),
                 _create_with_progress=Mock(return_value=response))
        modules = {"agent": NS(auxiliary_client=aux),
                   "hermes_cli.runtime_provider": NS(resolve_runtime_provider=runtime)}
        return client, runtime, aux, modules

    def test_provider_matrix_uses_same_auth_and_transport_without_aux_fallback(self):
        for provider in ("custom", "openrouter", "openai-codex", "nous", "xai-oauth",
                         "claude-subscription-directsdk-experimental", "openai-api", "gemini", "xai"):
            with self.subTest(provider=provider):
                client, runtime, aux, modules = self.setup_provider(provider)
                with patch.dict(sys.modules, modules):
                    value = data.completion(body(provider=provider))
                runtime.assert_called_once_with(requested=provider, target_model="selected-model")
                args = aux.resolve_provider_client.call_args
                self.assertEqual(args.args, (provider,))
                self.assertEqual(args.kwargs["explicit_api_key"], "private")
                self.assertEqual(args.kwargs["model"], "selected-model")
                self.assertEqual(value["provider"], provider)
                self.assertEqual(value["model"], "selected-model")
                self.assertEqual(value["receipt"]["model_version"], "wire-model")
                self.assertEqual(value["receipt"]["usage"]["prompt_tokens_details"]["cached_tokens"], 40)
                self.assertNotIn("private", json.dumps(value))
                self.assertNotIn("tools", aux._build_call_kwargs.call_args.kwargs)
                client.close.assert_called_once()

    def test_missing_client_and_transport_errors_do_not_try_another_route(self):
        for missing in (True, False):
            client, runtime, aux, modules = self.setup_provider()
            if missing:
                aux.resolve_provider_client.return_value = (None, None)
            else:
                aux._create_with_progress.side_effect = RuntimeError("provider unavailable")
            with patch.dict(sys.modules, modules), self.assertRaises(RuntimeError):
                data.completion(body())
            self.assertEqual(aux.resolve_provider_client.call_count, 1)
            self.assertEqual(aux._create_with_progress.call_count, 0 if missing else 1)

    def test_custom_without_endpoint_cannot_enter_auto_discovery(self):
        client, runtime, aux, modules = self.setup_provider("custom")
        runtime.return_value = {"provider": "custom"}
        with patch.dict(sys.modules, modules), self.assertRaises(RuntimeError):
            data.completion(body(provider="custom"))
        aux.resolve_provider_client.assert_not_called()

    def test_empty_answer_keeps_usage_and_never_uses_reasoning(self):
        response = {"choices": [{"message": {"content": "", "reasoning": "private thoughts"}}],
                    "usage": {"prompt_tokens": 17, "completion_tokens": 3}}
        client, runtime, aux, modules = self.setup_provider(response=response)
        with patch.dict(sys.modules, modules):
            result = data.completion(body())
        self.assertEqual(result["receipt"]["usage"]["completion_tokens"], 3)
        self.assertEqual(result["error"]["code"], "data_completion_empty")
        self.assertNotIn("private thoughts", json.dumps(result))

    def test_reject_tools_even_with_text(self):
        response = {"choices": [{"message": {"content": "answer", "tool_calls": [{"name": "bash"}]}}]}
        client, runtime, aux, modules = self.setup_provider(response=response)
        with patch.dict(sys.modules, modules):
            self.assertIn("error", data.completion(body()))

    def test_limits_and_no_credentials_or_agent_options(self):
        cases = [{"tools": []}, {"api_key": "secret"}, {"base_url": "https://elsewhere"},
                 {"session_id": "s"}, {"provider": "auto"}, {"model": ""},
                 {"max_tokens": True}, {"max_tokens": 16385}, {"timeout_seconds": float("nan")},
                 {"timeout_seconds": float("inf")}, {"timeout_seconds": True},
                 {"messages": [{"role": "tool", "content": "x"}]},
                 {"messages": [{"role": [], "content": "x"}]},
                 {"messages": [{"role": "user", "content": "x", "tool_calls": []}]},
                 {"messages": [{"role": "user", "content": "x" * 256001}]}]
        for changes in cases:
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                data.validate(body(**changes))
        self.assertEqual(data.validate(body())["max_tokens"], 4000)

    def test_auth_before_body_or_dispatch(self):
        sentinel = data.web.json_response({"error": "unauthorized"}, status=401)
        adapter = NS(_check_auth=Mock(return_value=sentinel), _read_json_body=Mock())
        self.assertIs(asyncio.run(data.handle(adapter, object())), sentinel)
        adapter._read_json_body.assert_not_called()

    def test_errors_are_sanitized(self):
        async def read(request):
            return body(), None
        adapter = NS(_check_auth=lambda request: None, _read_json_body=read)
        with patch.object(data, "completion", side_effect=RuntimeError("secret https://user:password@host")):
            response = asyncio.run(data.handle(adapter, object()))
        self.assertEqual(response.status, 502)
        self.assertNotIn("secret", response.text)
        self.assertNotIn("password", response.text)

    def test_pinned_hermes_contract(self):
        if not (root / "agent/auxiliary_client.py").exists():
            self.skipTest("Live Hermes contract runs during image build")
        tree = ast.parse((root / "agent/auxiliary_client.py").read_text())
        names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertTrue({"resolve_provider_client", "_build_call_kwargs", "_create_with_progress",
                         "_provider_requires_stream"} <= names)
        api = (root / "gateway/platforms/api_server.py").read_text()
        self.assertEqual(api.count('("POST", "/v1/data/completion", self._handle_data_completion)'), 1)
        self.assertIn("from gateway.calliope_data_completion import handle", api)


if __name__ == "__main__":
    unittest.main()
