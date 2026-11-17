from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import pytest
import types


def _supervisor_module(monkeypatch, tmp_path):
    root = Path(__file__).parents[2]
    supervisor_path = root / "docker/calliope/hermes-gateway-supervisor.py"
    policy_path = root / "docker/calliope/reconcile-hermes-config.py"
    original = importlib.util.spec_from_file_location
    spec = original("test_calliope_provider_supervisor", supervisor_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("API_SERVER_KEY", "test-only")

    def redirected(name, location, *args, **kwargs):
        if name == "calliope_hermes_config_policy":
            location = policy_path
        return original(name, location, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "spec_from_file_location", redirected)
    spec.loader.exec_module(module)
    return module


def test_browser_keeper_clears_stale_cross_container_profile_lock(monkeypatch, tmp_path):
    module = _supervisor_module(monkeypatch, tmp_path)
    profile_dir = tmp_path / "chromium"
    profile_dir.mkdir()
    (profile_dir / "SingletonLock").symlink_to("old-container-5066")
    (profile_dir / "SingletonCookie").symlink_to("123456")
    (profile_dir / "SingletonSocket").symlink_to("/tmp/old-chromium/SingletonSocket")
    (profile_dir / "DevToolsActivePort").write_text("9222\n/devtools/browser/test\n")
    monkeypatch.setattr(module.socket, "gethostname", lambda: "new-container")

    assert module._clear_stale_chromium_profile_locks(profile_dir) is True
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "DevToolsActivePort"):
        assert not os.path.lexists(profile_dir / name)


def test_browser_discovery_uses_image_binary_without_a_browser_data_volume(monkeypatch, tmp_path):
    module = _supervisor_module(monkeypatch, tmp_path)
    chrome = tmp_path / "image" / "chromium"
    chrome.parent.mkdir()
    chrome.write_text("#!/bin/sh\n")
    chrome.chmod(0o755)
    monkeypatch.setenv("CALLIOPE_CHROMIUM_PATH", str(chrome))
    assert module._find_full_chromium() == str(chrome)


def test_browser_keeper_preserves_live_local_profile_lock(monkeypatch, tmp_path):
    module = _supervisor_module(monkeypatch, tmp_path)
    profile_dir = tmp_path / "chromium"
    profile_dir.mkdir()
    lock_path = profile_dir / "SingletonLock"
    lock_path.symlink_to("current-container-42")
    monkeypatch.setattr(module, "_chromium_profile_lock_is_live", lambda _path: True)

    assert module._clear_stale_chromium_profile_locks(profile_dir) is False
    assert os.path.lexists(lock_path)


def test_oauth_output_parser_handles_standard_and_codex_device_prompts(monkeypatch, tmp_path):
    module = _supervisor_module(monkeypatch, tmp_path)
    standard = module._oauth_line_fields(
        "  2. If prompted, enter code: ABCD-EFGH\n"
    )
    assert standard["user_code"] == "ABCD-EFGH"
    assert "message" in standard

    heading = module._oauth_line_fields("  2. Enter this code:\n")
    code = module._oauth_line_fields("\x1b[94mTZYT-QHPNK\x1b[0m\n", expect_code=True)
    url = module._oauth_line_fields("\x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n")
    assert heading["expect_code"] is True
    assert code["user_code"] == "TZYT-QHPNK"
    assert url["verification_url"] == "https://auth.openai.com/codex/device"


def test_model_options_are_bounded_and_secret_free(monkeypatch, tmp_path):
    module = _supervisor_module(monkeypatch, tmp_path)
    result = module._bounded_model_options({
        "provider": "custom",
        "model": "calliope",
        "providers": [{
            "slug": "custom",
            "name": "Clover",
            "is_current": True,
            "authenticated": True,
            "models": ["calliope", "bad\nmodel"],
            "api_key": "must-not-escape",
            "capabilities": {"calliope": {"reasoning": True}},
        }],
    })
    assert result["providers"] == [{
        "id": "clover",
        "name": "Clover / Hutch",
        "auth_kind": "environment",
        "configured": True,
        "models": ["calliope"],
        "total_models": 1,
        "warning": None,
        "capabilities": {"calliope": {"reasoning": True}},
    }]
    assert "must-not-escape" not in str(result)


def test_clover_inventory_distinguishes_host_and_settings_keys(monkeypatch, tmp_path):
    module = _supervisor_module(monkeypatch, tmp_path)
    monkeypatch.setattr(
        module,
        "_gateway_request",
        lambda *_args, **_kwargs: {"provider": "", "model": "", "providers": []},
    )
    monkeypatch.setattr(
        module,
        "_safe_oauth_status",
        lambda _provider: {"configured": False, "flow": "device_code"},
    )
    supervisor = module.GatewaySupervisor()
    tokens = []

    def catalog(token):
        tokens.append(token)
        return {"models": ["calliope", "calliope-sol"], "total_models": 2, "capabilities": {}}

    monkeypatch.setattr(module, "_hutch_model_catalog", catalog)

    monkeypatch.delenv("RVBBIT_CLOVER_KEY", raising=False)
    supervisor.provider_env["RVBBIT_CLOVER_KEY"] = "settings-key"
    manual = next(
        item for item in supervisor.provider_inventory()["providers"]
        if item["id"] == "clover"
    )
    assert manual["configured"] is True
    assert manual["auth_kind"] == "api_key"
    assert manual["environment_managed"] is False

    monkeypatch.setenv("RVBBIT_CLOVER_KEY", "deployment-key")
    hosted = next(
        item for item in supervisor.provider_inventory()["providers"]
        if item["id"] == "clover"
    )
    assert hosted["configured"] is True
    assert hosted["auth_kind"] == "environment"
    assert hosted["environment_managed"] is True
    assert hosted["models"] == ["calliope", "calliope-sol"]
    assert tokens == ["settings-key", "deployment-key"]


def test_hutch_inventory_discovers_chat_definitions_and_only_safe_metadata(monkeypatch, tmp_path):
    import io
    import json

    module = _supervisor_module(monkeypatch, tmp_path)
    monkeypatch.setenv("RVBBIT_CLOVER_OPENAI_BASE_URL", "https://hutch.example/v1/")
    rows = [
        {"id": "calliope-sol", "meta": {"capability": "chat.completions",
         "context_window": 262144, "max_output_tokens": 32768, "api_key": "must-not-escape"}},
        {"id": "calliope"},
        {"id": "calliope-sol"},
        {"id": "calliope-opus", "meta": {"capability": "responses"}},
        {"id": "calliope-voice", "meta": {"capability": "audio.speech"}},
        {"id": "calliope-embed", "meta": {"capability": "embeddings"}},
        {"id": "embed"}, {"id": "unrelated"}, {"id": "calliope\nbad"}, {"id": 123},
    ]

    def fetch(request, timeout):
        assert request.full_url == "https://hutch.example/v1/models"
        assert request.get_header("Authorization") == "Bearer test-only-key"
        assert timeout == 10
        return io.BytesIO(json.dumps({"data": rows}).encode())

    monkeypatch.setattr(module.urllib.request, "urlopen", fetch)
    result = module._hutch_model_catalog("test-only-key")
    assert result["models"] == ["calliope", "calliope-opus", "calliope-sol"]
    assert result["total_models"] == 3
    assert result["capabilities"]["calliope-sol"] == {
        "chat": True, "context_window": 262144, "max_output_tokens": 32768,
    }
    assert "must-not-escape" not in str(result) and "test-only-key" not in str(result)


@pytest.mark.parametrize("payload", [{}, {"data": []}, {"data": [{"id": "embed"}]},
                                      {"data": "not-a-list"}, ["calliope"]])
def test_hutch_inventory_rejects_empty_or_invalid_response(monkeypatch, tmp_path, payload):
    import io
    import json

    module = _supervisor_module(monkeypatch, tmp_path)
    monkeypatch.setattr(module.urllib.request, "urlopen",
                        lambda *_a, **_k: io.BytesIO(json.dumps(payload).encode()))
    with pytest.raises(ValueError):
        module._hutch_model_catalog("test-only-key")


def test_hutch_discovery_failure_cannot_publish_single_model_fallback(monkeypatch, tmp_path):
    module = _supervisor_module(monkeypatch, tmp_path)
    monkeypatch.setenv("RVBBIT_CLOVER_KEY", "test-only-key")
    monkeypatch.setattr(module, "_gateway_request", lambda *_a, **_k: {
        "providers": [{"slug": "custom", "is_current": True,
                       "authenticated": True, "models": ["calliope"]}]})
    monkeypatch.setattr(module, "_safe_oauth_status", lambda _p: {"configured": False})

    def failed(_token):
        raise RuntimeError("provider body contains must-not-escape")

    monkeypatch.setattr(module, "_hutch_model_catalog", failed)
    result = module.GatewaySupervisor().provider_inventory(refresh=True)
    item = next(p for p in result["providers"] if p["id"] == "clover")
    assert item["models"] == [] and item["error"]
    assert "must-not-escape" not in str(result)


def test_hutch_inventory_does_not_invent_default_alias(monkeypatch, tmp_path):
    import io

    module = _supervisor_module(monkeypatch, tmp_path)
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_a, **_k:
                        io.BytesIO(b'{"data": [{"id": "calliope-sol"}]}'))
    assert module._hutch_model_catalog("test-only-key")["models"] == ["calliope-sol"]


def test_gateway_child_uses_durable_model_instead_of_environment_alias(monkeypatch, tmp_path):
    module = _supervisor_module(monkeypatch, tmp_path)
    monkeypatch.setenv("API_SERVER_MODEL_NAME", "calliope")
    supervisor = module.GatewaySupervisor()
    supervisor.runtime_env["RUNTIME_ONLY"] = "present"
    supervisor.provider_env["OPENROUTER_API_KEY"] = "secret"

    child_env = supervisor._gateway_child_environment()

    assert "API_SERVER_MODEL_NAME" not in child_env
    assert child_env["RUNTIME_ONLY"] == "present"
    assert child_env["OPENROUTER_API_KEY"] == "secret"


def test_model_rollout_readiness_tolerates_only_non_core_degradation(monkeypatch, tmp_path):
    """A disk warning must not turn every model edit into a 90-second rollback,
    while a degraded model/config/runtime check must still fail closed."""
    module = _supervisor_module(monkeypatch, tmp_path)
    detailed = {
        "status": "degraded",
        "readiness": {"checks": {
            "state_db": {"status": "ok"},
            "config": {"status": "ok"},
            "model": {"status": "ok"},
            "gateway": {"status": "ok"},
            "disk": {"status": "degraded", "used_percent": 91.2},
        }},
    }
    assert module._gateway_readiness_error(detailed) is None
    detailed["readiness"]["checks"]["model"]["status"] = "degraded"
    assert module._gateway_readiness_error(detailed) == "gateway core readiness is degraded: model=degraded"
    assert "core checks unavailable" in module._gateway_readiness_error({"status": "degraded"})


def test_provider_environment_restores_only_calliope_pool_keys(monkeypatch, tmp_path):
    module = _supervisor_module(monkeypatch, tmp_path)
    supervisor = module.GatewaySupervisor()
    monkeypatch.delenv("RVBBIT_CLOVER_KEY", raising=False)
    monkeypatch.setattr(
        supervisor,
        "_calliope_pool_token",
        lambda provider: "durable-clover-key" if provider == "clover" else "",
    )
    supervisor._restore_provider_environment()
    assert supervisor.provider_env == {"RVBBIT_CLOVER_KEY": "durable-clover-key"}


def test_native_logged_out_status_is_missing_not_error(monkeypatch, tmp_path):
    module = _supervisor_module(monkeypatch, tmp_path)
    native_package = types.ModuleType("hermes_cli")
    native_auth = types.ModuleType("hermes_cli.auth")
    native_auth.get_codex_auth_status = lambda: {"logged_in": False}
    native_auth.get_nous_auth_status_local = lambda: {"logged_in": False}
    native_auth.get_xai_oauth_auth_status = lambda: {
            "logged_in": False,
            "error": "No xAI OAuth credentials stored. Select xAI in hermes model.",
        }
    native_package.auth = native_auth
    monkeypatch.setitem(sys.modules, "hermes_cli", native_package)
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", native_auth)
    status = module._safe_oauth_status("xai-oauth")
    assert status["configured"] is False
    assert status["error"] is None


def test_api_key_validation_probes_the_provider_not_the_gateway(monkeypatch, tmp_path):
    """The gateway API server (:8642) has no /api/providers/validate — that
    route lives on the Hermes web dashboard, which the appliance does not run.
    Calling it surfaced as 'Hermes gateway returned HTTP 404' on every pasted
    OpenRouter key. The supervisor now asks the provider directly."""
    import io
    import urllib.error
    import urllib.request

    module = _supervisor_module(monkeypatch, tmp_path)

    def never(*_args, **_kwargs):
        raise AssertionError("validation must not go through the Hermes gateway")

    monkeypatch.setattr(module, "_gateway_request", never)

    seen = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def fake_urlopen(request, timeout=0):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        code = seen.get("code", 200)
        if code >= 400:
            raise urllib.error.HTTPError(request.full_url, code, "x", {}, io.BytesIO(b"{}"))
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    ok = module._probe_provider_key("openrouter", "sk-or-test")
    assert ok == {"ok": True, "reachable": True, "message": ""}
    assert seen["url"] == "https://openrouter.ai/api/v1/key"
    assert seen["auth"] == "Bearer sk-or-test"

    seen["code"] = 401
    rejected = module._probe_provider_key("openrouter", "sk-or-bad")
    assert rejected["ok"] is False and rejected["reachable"] is True
    assert "rejected" in rejected["message"]

    seen["code"] = 429   # valid key, rate limited right now
    assert module._probe_provider_key("xai", "xai-key")["ok"] is True

    seen["code"] = 503
    assert module._probe_provider_key("openai-api", "k")["message"] == "Provider returned HTTP 503 for this key."

    # Gemini: the OpenAI-compatible listing takes the key as a bearer token, and
    # Google answers a bad key with 400 INVALID_ARGUMENT rather than 401.
    seen["code"] = 200
    assert module._probe_provider_key("gemini", "AIza-test")["ok"] is True
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/models"
    assert seen["auth"] == "Bearer AIza-test"
    seen["code"] = 400
    bad = module._probe_provider_key("gemini", "AIza-bad")
    assert bad["ok"] is False and bad["reachable"] is True and "rejected" in bad["message"]
    seen["code"] = 400   # for every other provider a 400 is still just an HTTP status
    assert module._probe_provider_key("openai-api", "k")["message"] == "Provider returned HTTP 400 for this key."
    assert "gemini" in module._MANAGED_PROVIDER_IDS
    assert module._PROVIDER_KEY_ENV["gemini"] == "GEMINI_API_KEY"
    assert module._PROVIDER_ORDER.index("gemini") == module._PROVIDER_ORDER.index("openai-api") + 1

    def offline(request, timeout=0):
        raise urllib.error.URLError("dns down")

    monkeypatch.setattr(urllib.request, "urlopen", offline)
    unreachable = module._probe_provider_key("openrouter", "sk")
    assert unreachable == {"ok": False, "reachable": False,
                           "message": "Could not reach the provider to verify the key."}

    # Providers without a probe are not blocked (same contract as Hermes).
    assert module._probe_provider_key("nous", "x") == {"ok": True, "reachable": False, "message": ""}


def test_install_provider_key_blocks_only_on_a_provider_rejection(monkeypatch, tmp_path):
    module = _supervisor_module(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_gateway_request", lambda *a, **k: (_ for _ in ()).throw(AssertionError("gateway")))
    supervisor = module.GatewaySupervisor()
    monkeypatch.setattr(
        module, "_probe_provider_key",
        lambda provider, token, **_k: {"ok": False, "reachable": True, "message": "That API key was rejected."},
    )
    import pytest
    with pytest.raises(ValueError, match="rejected"):
        supervisor.install_provider_key("openrouter", "sk-or-bad")


def _stub_native_validator(monkeypatch):
    native_package = types.ModuleType("hermes_cli")
    native_config = types.ModuleType("hermes_cli.config")
    native_config.validate_config_structure = lambda payload: []
    native_package.config = native_config
    monkeypatch.setitem(sys.modules, "hermes_cli", native_package)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", native_config)


def _seed_profile(module, tmp_path, key="dashboard-studio"):
    import yaml

    (tmp_path / "config.yaml").write_text("model:\n  default: calliope\n  provider: custom\n", encoding="utf-8")
    root = tmp_path / "profiles" / key
    root.mkdir(parents=True)
    (root / "config.yaml").write_text(yaml.safe_dump({
        "model": {"default": "calliope", "provider": "custom",
                  **module.POLICY.CLOVER_MODEL_BINDINGS, "context_length": 262144, "max_tokens": 32768},
        "platforms": {"api_server": {"enabled": False}},
        "platform_toolsets": {"api_server": ["hermes-api-server", "Specialist"]},
        "mcp_servers": {"Specialist": {"url": "http://calliope:8765/mcp?lane=specialist",
                                                "forward_session_identity": True}},
    }, sort_keys=False), encoding="utf-8")
    return root


def test_set_profile_model_writes_marker_and_restarts_verified(monkeypatch, tmp_path):
    """Models & agents for a specialist: rebase, shared-policy validation,
    atomic write + operator marker, verified restart; rollback of the file
    when the gateway does not come back healthy."""
    import yaml

    module = _supervisor_module(monkeypatch, tmp_path)
    _stub_native_validator(monkeypatch)
    root = _seed_profile(module, tmp_path)
    supervisor = module.GatewaySupervisor()
    calls = []
    monkeypatch.setattr(supervisor, "stop_child", lambda *a, **k: calls.append("stop"))
    monkeypatch.setattr(supervisor, "start_child", lambda *a, **k: calls.append("start"))
    monkeypatch.setattr(supervisor, "wait_healthy", lambda *a, **k: {"gateway": {"status": "ok"}})

    result = supervisor.set_profile_model("dashboard-studio", "openrouter", "openai/gpt-5.6-sol")
    assert result["ok"] is True and result["changed"] is True
    assert result["model"]["provider"] == "openrouter" and result["model"]["model"] == "openai/gpt-5.6-sol"
    assert result["model"]["operator_managed"] is True
    payload = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert payload["model"] == {"default": "openai/gpt-5.6-sol", "provider": "openrouter",
                                "context_length": 262144, "max_tokens": 32768}   # Hutch bindings gone
    assert (root / ".calliope-managed" / module.MODEL_OPERATOR_MARKER).exists()
    assert calls == ["stop", "start"]
    listed = next(item for item in supervisor.profiles()["profiles"] if item["key"] == "dashboard-studio")
    assert listed["model"]["provider"] == "openrouter" and listed["model"]["errors"] == []

    # Same choice again: no write, no restart.
    calls.clear()
    again = supervisor.set_profile_model("dashboard-studio", "openrouter", "openai/gpt-5.6-sol")
    assert again["ok"] is True and again["changed"] is False and calls == []

    # Unhealthy after the change: the previous file comes back.
    def unhealthy(*a, **k):
        if calls.count("start") == 1:
            raise RuntimeError("gateway exited during startup with 1")
        return {"gateway": {"status": "ok"}}

    monkeypatch.setattr(supervisor, "wait_healthy", unhealthy)
    failed = supervisor.set_profile_model("dashboard-studio", "custom", "calliope")
    assert failed["ok"] is False and failed["phase"] == "verification" and failed["rolled_back"] is True
    assert failed["rollback"]["healthy"] is True
    payload = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert payload["model"]["provider"] == "openrouter"                         # restored
    assert calls == ["stop", "start", "stop", "start"]


def test_set_profile_model_carries_reasoning_effort(monkeypatch, tmp_path):
    """The effort level rides the same validated write + verified restart:
    set into agent.reasoning_effort, reported by the summary, '' clears it."""
    import yaml

    module = _supervisor_module(monkeypatch, tmp_path)
    _stub_native_validator(monkeypatch)
    root = _seed_profile(module, tmp_path)
    supervisor = module.GatewaySupervisor()
    monkeypatch.setattr(supervisor, "stop_child", lambda *a, **k: None)
    monkeypatch.setattr(supervisor, "start_child", lambda *a, **k: None)
    monkeypatch.setattr(supervisor, "wait_healthy", lambda *a, **k: {"gateway": {"status": "ok"}})

    result = supervisor.set_profile_model("dashboard-studio", "openrouter", "openai/gpt-5.6-sol",
                                          None, None, "xhigh")
    assert result["ok"] is True and result["model"]["reasoning_effort"] == "xhigh"
    payload = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert payload["agent"]["reasoning_effort"] == "xhigh"

    # Model-only change leaves the effort in place (None = untouched).
    result = supervisor.set_profile_model("dashboard-studio", "openrouter", "openai/gpt-5.6-terra")
    payload = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert payload["agent"]["reasoning_effort"] == "xhigh"
    assert result["model"]["reasoning_effort"] == "xhigh"

    # '' clears back to auto and drops the empty agent block.
    result = supervisor.set_profile_model("dashboard-studio", "openrouter", "openai/gpt-5.6-terra",
                                          None, None, "")
    payload = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert "agent" not in payload and result["model"]["reasoning_effort"] is None

    bad = supervisor.set_profile_model("dashboard-studio", "openrouter", "openai/gpt-5.6-terra",
                                       None, None, "galactic")
    assert bad["ok"] is False and bad["phase"] == "validation"
    assert "reasoning_effort" in bad["validation"]["errors"][0]["message"]

    # The batch endpoint takes the same field per change.
    batch = supervisor.set_profile_models([
        {"profile": "dashboard-studio", "provider": "openrouter", "model": "openai/gpt-5.6-sol",
         "reasoning_effort": "low"},
    ])
    assert batch["ok"] is True
    payload = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    assert payload["agent"]["reasoning_effort"] == "low"
    assert batch["results"][0]["model"]["reasoning_effort"] == "low"


def test_set_profile_model_rejects_bad_choices_and_the_default_profile(monkeypatch, tmp_path):
    module = _supervisor_module(monkeypatch, tmp_path)
    _stub_native_validator(monkeypatch)
    _seed_profile(module, tmp_path)
    supervisor = module.GatewaySupervisor()
    monkeypatch.setattr(supervisor, "stop_child", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no restart")))

    default = supervisor.set_profile_model("default", "openrouter", "x")
    assert default["ok"] is False and default["phase"] == "durable_controller_required"

    bad = supervisor.set_profile_model("dashboard-studio", "vertex", "gemini-3.1-pro-preview")
    assert bad["ok"] is False and bad["phase"] == "validation"
    assert "unsupported model provider" in bad["validation"]["errors"][0]["message"]

    empty = supervisor.set_profile_model("dashboard-studio", "openrouter", "")
    assert empty["ok"] is False and empty["phase"] == "validation"


def test_specialist_config_validation_holds_the_model_block_to_policy(monkeypatch, tmp_path):
    module = _supervisor_module(monkeypatch, tmp_path)
    _stub_native_validator(monkeypatch)
    root = _seed_profile(module, tmp_path)
    raw = (root / "config.yaml").read_text(encoding="utf-8")
    assert module._specialist_config_validation("dashboard-studio", raw.encode())["valid"] is True
    leaked = raw.replace("${env:RVBBIT_CLOVER_KEY}", "sk-live-leaked")
    result = module._specialist_config_validation("dashboard-studio", leaked.encode())
    assert result["valid"] is False
    assert any(issue["path"] == "model.api_key" for issue in result["errors"])


CLAUDE_CLI_PROVIDER = "claude-subscription-directsdk-experimental"

# A stand-in for the official Claude Code CLI with the lines measured inside
# the Hermes image: `auth status` prints JSON and exits 1 while logged out,
# `auth login` (stdin a pipe) prints the authorize URL, blocks on the code
# prompt (no trailing newline) and writes the CLI's own credential file,
# `auth logout` removes it. The credential file holds a token-looking value
# so a leak through any Calliope payload would be caught.
_FAKE_CLAUDE_CLI = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
config = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"])
credentials = config / ".credentials.json"
(config / "calls.log").parent.mkdir(parents=True, exist_ok=True)
with open(config / "calls.log", "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")
args = sys.argv[1:]
if args == ["auth", "status"]:
    if credentials.exists():
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
                          "subscriptionType": "max", "email": "someone@example.com",
                          "organization": "Example Org"}))
        sys.exit(0)
    print(json.dumps({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty",
                      "analyticsDisabled": False, "projectsDirectory": "/opt/data/.claude/projects"}))
    sys.exit(1)
if args == ["auth", "login"]:
    print("Opening browser to sign in...", flush=True)
    print("If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true"
          "&client_id=abc&response_type=code&redirect_uri=https%3A%2F%2Fconsole.anthropic.com"
          "%2Foauth%2Fcode%2Fcallback&scope=user%3Aprofile&state=xyz", flush=True)
    sys.stdout.write("Paste code here if prompted > ")
    sys.stdout.flush()
    code = sys.stdin.readline().strip()
    if not code:
        print("Login failed: no code")
        sys.exit(1)
    credentials.write_text(json.dumps({"claudeAiOauth": {"accessToken": "sk-ant-oat01-secret-" + code,
                                                          "refreshToken": "sk-ant-ort01-secret"}}))
    credentials.chmod(0o600)
    print("Login successful")
    sys.exit(0)
if args == ["auth", "logout"]:
    if credentials.exists():
        credentials.unlink()
    print("Logged out")
    sys.exit(0)
print("unexpected: " + " ".join(args))
sys.exit(2)
'''


def _install_fake_claude_cli(monkeypatch, tmp_path):
    binary = tmp_path / "fake-bin" / "claude"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(_FAKE_CLAUDE_CLI, encoding="utf-8")
    binary.chmod(0o755)
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    monkeypatch.setenv("CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND", str(binary))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("CLAUDE_SUBSCRIPTION_DIRECTSDK_CONFIG_DIR", raising=False)
    return binary, config_dir


def _assert_secret_free(payload, credentials_path=None):
    text = str(payload)
    assert "sk-ant" not in text
    assert "@" not in text
    assert "secret" not in text
    assert "Example Org" not in text
    if credentials_path is not None:
        assert str(credentials_path) not in text


def test_claude_cli_login_takes_a_pasted_code(monkeypatch, tmp_path):
    """Claude Pro / Max is the DirectSDK plugin, whose only login is the
    official Claude Code CLI. With stdin as a pipe the CLI prints the
    authorize URL and blocks on its code prompt; the supervisor drives it
    through the same code-entry surface as a Hermes OAuth grant: stdin kept
    open, awaiting_code once the URL is out, the code written and closed,
    approval read back from `claude auth status` — never from the CLI's
    credential file."""
    import json as _json
    import time as _t

    module = _supervisor_module(monkeypatch, tmp_path)
    binary, config_dir = _install_fake_claude_cli(monkeypatch, tmp_path)
    credentials = config_dir / ".credentials.json"
    supervisor = module.GatewaySupervisor()

    before = module._safe_oauth_status(CLAUDE_CLI_PROVIDER)
    assert before["configured"] is False and before["error"] is None
    assert before["auth_kind"] == "cli_login" and before["available"] is True
    assert before["flow"] == "authorization_code" and before["has_refresh_token"] is None

    real_popen = module.subprocess.Popen
    popen_calls = []

    def spy_popen(cmd, **kwargs):
        popen_calls.append((cmd, kwargs.get("stdin"), kwargs.get("env") or {}))
        return real_popen(cmd, **kwargs)

    monkeypatch.setattr(module.subprocess, "Popen", spy_popen)

    started = supervisor.start_provider_oauth(CLAUDE_CLI_PROVIDER)
    cmd, stdin, env = popen_calls[0]
    assert cmd == [str(binary), "auth", "login"] and "--console" not in cmd
    assert stdin is module.subprocess.PIPE
    assert env["CLAUDE_CONFIG_DIR"] == str(config_dir)
    assert started["flow"] == "authorization_code" and started["status"] == "pending"
    assert started["verification_url"].startswith("https://claude.com/cai/oauth/authorize?code=true")
    assert started["awaiting_code"] is True and started["user_code"] is None
    assert started["message"] == "Sign in with your Claude subscription, then paste the code Anthropic shows you."
    _assert_secret_free(started, credentials)

    with pytest.raises(ValueError, match="malformed"):
        supervisor.submit_provider_oauth_code(CLAUDE_CLI_PROVIDER, started["session_id"], "not a code!")
    handed = supervisor.submit_provider_oauth_code(CLAUDE_CLI_PROVIDER, started["session_id"], "abc123#xyz")
    assert handed["status"] == "exchanging" and handed["awaiting_code"] is False
    # once the CLI exits 0 and `auth status` says logged in, the job is approved
    deadline = _t.monotonic() + 15
    while _t.monotonic() < deadline:
        state = supervisor.poll_provider_oauth(CLAUDE_CLI_PROVIDER, started["session_id"])
        if state["status"] != "exchanging":
            break
        _t.sleep(0.05)
    assert state["status"] == "approved" and state["message"] == "Provider connected."
    _assert_secret_free(state, credentials)
    # the code reached the CLI's stdin exactly once, and the CLI's own store took it
    assert _json.loads(credentials.read_text())["claudeAiOauth"]["accessToken"].endswith("abc123#xyz")
    calls = (config_dir / "calls.log").read_text().splitlines()
    assert calls.count("auth login") == 1

    after = module._safe_oauth_status(CLAUDE_CLI_PROVIDER)
    assert after["configured"] is True and after["error"] is None
    assert after["source"] == "claude-code-cli" and after["source_label"] == "Claude Max"
    assert after["expires_at"] is None and after["has_refresh_token"] is None
    assert after["auth_kind"] == "cli_login" and after["flow"] == "authorization_code"
    _assert_secret_free(after, credentials)

    # disconnect = `claude auth logout`; Calliope deletes nothing itself
    removed = supervisor.disconnect_provider_oauth(CLAUDE_CLI_PROVIDER)
    assert removed == {"ok": True, "provider": CLAUDE_CLI_PROVIDER, "configured": False,
                       "cleared": True, "error": None}
    assert not credentials.exists() and config_dir.exists()
    assert module._safe_oauth_status(CLAUDE_CLI_PROVIDER)["configured"] is False

    # device-code providers still never get stdin and take no pasted code
    import io

    class FakeProcess:
        stdin = None
        stdout = io.StringIO("Enter this code: ABCD-EFGH\n  https://auth.example/device\n")

        def poll(self):
            return 0

        def wait(self):
            return 0

        def terminate(self):
            pass

    monkeypatch.setattr(module.subprocess, "Popen", lambda cmd, **kw: popen_calls.append((cmd, kw.get("stdin"), {})) or FakeProcess())
    monkeypatch.setattr(module, "_safe_oauth_status", lambda provider: {"configured": True, "flow": "device_code"})
    other = supervisor.start_provider_oauth("openai-codex")
    cmd, stdin, _env = popen_calls[-1]
    assert cmd[:5] == ["/opt/hermes/.venv/bin/hermes", "auth", "add", "openai-codex", "--type"]
    assert stdin is module.subprocess.DEVNULL and other["flow"] == "device_code"
    with pytest.raises(ValueError, match="does not take"):
        supervisor.submit_provider_oauth_code("openai-codex", other["session_id"], "abc")
    # the retired custom Anthropic PKCE path is gone
    with pytest.raises(ValueError):
        supervisor.start_provider_oauth("anthropic")


def test_claude_cli_status_is_secret_free_and_says_when_the_cli_is_missing(monkeypatch, tmp_path):
    """The status reader is `claude auth status`: only the plan name leaves it
    (no email, org, or token). Without the binary the provider reports
    available=False with the install hint, start fails cleanly, and
    disconnect has nothing to clear. The inventory folds the plugin's
    provider in as an OAuth-kind card ordered right after Nous."""
    import json as _json
    import shutil as _shutil

    module = _supervisor_module(monkeypatch, tmp_path)
    binary, config_dir = _install_fake_claude_cli(monkeypatch, tmp_path)
    credentials = config_dir / ".credentials.json"
    credentials.write_text(_json.dumps({"claudeAiOauth": {"accessToken": "sk-ant-oat01-secret"}}))
    logged_in = module._safe_oauth_status(CLAUDE_CLI_PROVIDER)
    assert logged_in["configured"] is True and logged_in["source_label"] == "Claude Max"
    _assert_secret_free(logged_in, credentials)
    assert module._claude_plan_label("pro") == "Claude Pro"
    assert module._claude_plan_label("") == "Claude subscription"

    # the binary is missing: env override points nowhere and PATH has no `claude`
    monkeypatch.setenv("CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND", str(tmp_path / "nowhere" / "claude"))
    monkeypatch.setattr(_shutil, "which", lambda *_a, **_k: None)
    missing = module._safe_oauth_status(CLAUDE_CLI_PROVIDER)
    assert missing["configured"] is False and missing["available"] is False
    assert "not installed" in missing["error"] and "CLAUDE_SUBSCRIPTION_DIRECTSDK_COMMAND" in missing["error"]
    supervisor = module.GatewaySupervisor()
    with pytest.raises(ValueError, match="not installed"):
        supervisor.start_provider_oauth(CLAUDE_CLI_PROVIDER)
    assert supervisor.oauth_jobs == {}
    gone = supervisor.disconnect_provider_oauth(CLAUDE_CLI_PROVIDER)
    assert gone["ok"] is True and gone["cleared"] is False and "not installed" in gone["error"]

    # inventory: the plugin's provider profile is an OAuth-kind card whose
    # oauth block says how the grant is obtained
    monkeypatch.setattr(module, "_gateway_request", lambda *_a, **_k: {
        "provider": CLAUDE_CLI_PROVIDER, "model": "sonnet",
        "providers": [
            {"slug": "nous", "name": "Nous Portal", "authenticated": False, "models": []},
            {"slug": CLAUDE_CLI_PROVIDER, "name": "Claude Subscription (DirectSDK)",
             "authenticated": False, "models": ["sonnet", "haiku", "opus", "fable"],
             "api_key": "must-not-escape"},
            {"slug": "anthropic", "name": "Anthropic", "authenticated": True,
             "models": ["claude-sonnet-5"]},
        ],
    })
    monkeypatch.delenv("RVBBIT_CLOVER_KEY", raising=False)
    inventory = supervisor.provider_inventory()
    ids = [item["id"] for item in inventory["providers"]]
    assert "anthropic" not in ids and "must-not-escape" not in str(inventory)
    assert ids.index(CLAUDE_CLI_PROVIDER) == ids.index("nous") + 1
    card = next(item for item in inventory["providers"] if item["id"] == CLAUDE_CLI_PROVIDER)
    assert card["auth_kind"] == "oauth" and card["configured"] is False
    assert card["models"] == ["sonnet", "haiku", "opus", "fable"]
    assert card["oauth"]["auth_kind"] == "cli_login" and card["oauth"]["available"] is False
    assert card["oauth"]["flow"] == "authorization_code" and card["oauth"]["has_refresh_token"] is None
    _assert_secret_free(inventory, credentials)


def test_set_profile_models_batch_applies_everything_with_one_restart(monkeypatch, tmp_path):
    """A team-wide model change is validated and written per profile, then the
    gateway restarts ONCE — not once per agent. Any validation failure restores
    the already-written files and nothing restarts."""
    import yaml

    module = _supervisor_module(monkeypatch, tmp_path)
    _stub_native_validator(monkeypatch)
    studio = _seed_profile(module, tmp_path, "dashboard-studio")
    skeptic = _seed_profile(module, tmp_path, "analytical-skeptic")
    supervisor = module.GatewaySupervisor()
    calls = []
    monkeypatch.setattr(supervisor, "stop_child", lambda *a, **k: calls.append("stop"))
    monkeypatch.setattr(supervisor, "start_child", lambda *a, **k: calls.append("start"))
    monkeypatch.setattr(supervisor, "wait_healthy", lambda *a, **k: {"gateway": {"status": "ok"}})

    result = supervisor.set_profile_models([
        {"profile": "dashboard-studio", "provider": "openrouter", "model": "google/gemini-3.7-flash"},
        {"profile": "analytical-skeptic", "provider": "openrouter", "model": "openai/gpt-5.6-sol"},
    ], auxiliary={"provider": "openrouter", "model": "openai/gpt-5.6-sol"})
    assert result["ok"] is True and result["changed"] is True and result["restarted"] is True
    assert calls == ["stop", "start"]                       # ONE restart for both
    assert [item["profile"] for item in result["results"]] == ["dashboard-studio", "analytical-skeptic"]
    assert all(item["changed"] for item in result["results"])
    for root, model in ((studio, "google/gemini-3.7-flash"), (skeptic, "openai/gpt-5.6-sol")):
        payload = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
        assert payload["model"]["default"] == model and payload["model"]["provider"] == "openrouter"
        assert payload["auxiliary"]["compression"]["fallback_chain"][0] == {
            "provider": "openrouter", "model": "openai/gpt-5.6-sol", "calliope_managed": True,
        }
        assert (root / ".calliope-managed" / module.MODEL_OPERATOR_MARKER).exists()

    # Idempotent batch: nothing to write, nothing restarts.
    calls.clear()
    again = supervisor.set_profile_models([
        {"profile": "dashboard-studio", "provider": "openrouter", "model": "google/gemini-3.7-flash"},
    ])
    assert again["ok"] is True and again["changed"] is False and again["restarted"] is False and calls == []

    # A bad row mid-batch restores the earlier write and never restarts.
    calls.clear()
    before = (studio / "config.yaml").read_bytes()
    failed = supervisor.set_profile_models([
        {"profile": "dashboard-studio", "provider": "openrouter", "model": "x/y"},
        {"profile": "analytical-skeptic", "provider": "vertex", "model": "gemini-3.1-pro-preview"},
    ])
    assert failed["ok"] is False and failed["phase"] == "validation" and failed["profile"] == "analytical-skeptic"
    assert (studio / "config.yaml").read_bytes() == before   # first write rolled back
    assert calls == []

    # The default profile stays on the revision controller; duplicates are rejected.
    default = supervisor.set_profile_models([{"profile": "default", "provider": "openrouter", "model": "x"}])
    assert default["ok"] is False and default["phase"] == "durable_controller_required"
    with pytest.raises(ValueError):
        supervisor.set_profile_models([
            {"profile": "dashboard-studio", "provider": "openrouter", "model": "a/b"},
            {"profile": "dashboard-studio", "provider": "openrouter", "model": "c/d"},
        ])
    with pytest.raises(ValueError):
        supervisor.set_profile_models([])


def test_set_profile_fallback_writes_every_profile_with_one_restart(monkeypatch, tmp_path):
    import yaml

    module = _supervisor_module(monkeypatch, tmp_path)
    _stub_native_validator(monkeypatch)
    studio = _seed_profile(module, tmp_path, "dashboard-studio")
    skeptic = _seed_profile(module, tmp_path, "analytical-skeptic")
    supervisor = module.GatewaySupervisor()
    calls = []
    monkeypatch.setattr(supervisor, "stop_child", lambda *a, **k: calls.append("stop"))
    monkeypatch.setattr(supervisor, "start_child", lambda *a, **k: calls.append("start"))
    monkeypatch.setattr(supervisor, "wait_healthy", lambda *a, **k: {"gateway": {"status": "ok"}})

    result = supervisor.set_profile_fallback("openrouter", "openai/gpt-5.6-sol")
    assert result["ok"] is True and result["restarted"] is True and calls == ["stop", "start"]
    for root in (studio, skeptic):
        payload = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
        assert payload["fallback_model"] == {"provider": "openrouter", "model": "openai/gpt-5.6-sol"}
        assert (root / ".calliope-managed" / module.MODEL_OPERATOR_MARKER).exists()

    calls.clear()
    again = supervisor.set_profile_fallback("openrouter", "openai/gpt-5.6-sol")
    assert again["changed"] is False and calls == []                # idempotent, no restart

    cleared = supervisor.set_profile_fallback(None, None)
    assert cleared["ok"] is True and cleared["restarted"] is True
    payload = yaml.safe_load((studio / "config.yaml").read_text(encoding="utf-8"))
    assert "fallback_model" not in payload

    bad = supervisor.set_profile_fallback("custom", "calliope")
    assert bad["ok"] is False and bad["phase"] == "validation"


def test_set_profile_auxiliary_writes_every_profile_with_one_restart(monkeypatch, tmp_path):
    """The auxiliary (background-task) lane rides the same one-restart batch
    shape as the fallback: every specialist profile gets the operator's
    selected provider/model fallback, per-task options survive, and clearing
    removes only Calliope's marked entries."""
    import yaml

    module = _supervisor_module(monkeypatch, tmp_path)
    _stub_native_validator(monkeypatch)
    studio = _seed_profile(module, tmp_path, "dashboard-studio")
    skeptic = _seed_profile(module, tmp_path, "analytical-skeptic")
    payload = yaml.safe_load((studio / "config.yaml").read_text(encoding="utf-8"))
    payload["auxiliary"] = {
        "vision": {
            "provider": "auto",
            "fallback_chain": [{"provider": "nous", "model": "Hermes-4-405B"}],
        },
    }
    (studio / "config.yaml").write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    supervisor = module.GatewaySupervisor()
    calls = []
    monkeypatch.setattr(supervisor, "stop_child", lambda *a, **k: calls.append("stop"))
    monkeypatch.setattr(supervisor, "start_child", lambda *a, **k: calls.append("start"))
    monkeypatch.setattr(supervisor, "wait_healthy", lambda *a, **k: {"gateway": {"status": "ok"}})

    result = supervisor.set_profile_auxiliary("openrouter", "qwen/qwen3-30b-a3b:free")
    assert result["ok"] is True and result["restarted"] is True and calls == ["stop", "start"]
    for root in (studio, skeptic):
        aux = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))["auxiliary"]
        managed = aux["compression"]["fallback_chain"][0]
        assert managed == {
            "provider": "openrouter", "model": "qwen/qwen3-30b-a3b:free", "calliope_managed": True,
        }
        assert (root / ".calliope-managed" / module.MODEL_OPERATOR_MARKER).exists()
    aux = yaml.safe_load((studio / "config.yaml").read_text(encoding="utf-8"))["auxiliary"]
    assert aux["vision"]["provider"] == "auto"
    assert aux["vision"]["fallback_chain"][1] == {"provider": "nous", "model": "Hermes-4-405B"}

    calls.clear()
    again = supervisor.set_profile_auxiliary("openrouter", "qwen/qwen3-30b-a3b:free")
    assert again["changed"] is False and calls == []                # idempotent, no restart

    cleared = supervisor.set_profile_auxiliary(None, None)
    assert cleared["ok"] is True and cleared["restarted"] is True
    payload = yaml.safe_load((studio / "config.yaml").read_text(encoding="utf-8"))
    assert payload["auxiliary"] == {
        "vision": {
            "provider": "auto",
            "fallback_chain": [{"provider": "nous", "model": "Hermes-4-405B"}],
        },
    }
    payload = yaml.safe_load((skeptic / "config.yaml").read_text(encoding="utf-8"))
    assert payload["auxiliary"] == {}

    bad = supervisor.set_profile_auxiliary("openrouter", "model with spaces")
    assert bad["ok"] is False and bad["phase"] == "validation"


def test_full_fleet_models_and_stale_profile_preflight(monkeypatch, tmp_path):
    import yaml
    module = _supervisor_module(monkeypatch, tmp_path)
    _stub_native_validator(monkeypatch)
    profiles = {f'expert-{i}': _seed_profile(module,tmp_path,f'expert-{i}') for i in range(24)}
    for path in profiles.values():
        config = yaml.safe_load((path/'config.yaml').read_text())
        config.setdefault('agent',{})['reasoning_effort'] = 'high'
        config['fallback_model'] = {'provider':'openrouter','model':'fallback-keep'}
        (path/'config.yaml').write_text(yaml.safe_dump(config))
    supervisor = module.GatewaySupervisor()
    restarts = []
    monkeypatch.setattr(supervisor,'stop_child',lambda *a,**k:restarts.append('stop'))
    monkeypatch.setattr(supervisor,'start_child',lambda *a,**k:restarts.append('start'))
    monkeypatch.setattr(supervisor,'wait_healthy',lambda *a,**k:{'gateway':{'status':'ok'}})
    changes = [{'profile':key,'provider':'openrouter','model':'new-model','expected_hash':module._sha((path/'config.yaml').read_bytes())} for key,path in profiles.items()]
    before = {key:(path/'config.yaml').read_bytes() for key,path in profiles.items()}
    changes[-1]['expected_hash'] = 'stale'
    assert supervisor.set_profile_models(changes)['ok'] is False
    assert not restarts
    assert all((profiles[key]/'config.yaml').read_bytes()==value for key,value in before.items())
    changes[-1]['expected_hash'] = module._sha(before[changes[-1]['profile']])
    result = supervisor.set_profile_models(changes)
    assert result['ok'] and len(result['results']) == 24
    assert restarts == ['stop','start']
    for path in profiles.values():
        config = yaml.safe_load((path/'config.yaml').read_text())
        assert config['model']['default'] == 'new-model'
        assert config['agent']['reasoning_effort'] == 'high'
        assert config['fallback_model']['model'] == 'fallback-keep'


def test_every_post_route_branch_answers_the_request():
    """A `/v1/...` branch in do_POST that computes a result but never calls _reply closes the
    socket without a response ("Remote end closed connection without response" on the
    Calliope side). The fallback route shipped that way on 2026-09-23; pin the shape."""
    import re
    source = (Path(__file__).resolve().parents[2] / "docker/calliope/hermes-gateway-supervisor.py").read_text(encoding="utf-8")
    start = source.index("    def do_POST(self)")
    end = source.index("\n    def ", start + 10)
    body = source[start:end]
    branches = re.split(r"\n\s+elif self\.path (?:==|in) ", body)[1:]
    offenders = []
    for branch in branches:
        head = branch.split("\n", 1)[0]
        if "_reply(" not in branch and "return" not in branch:
            offenders.append(head[:80])
    assert not offenders, f"do_POST branches without a reply: {offenders}"


def test_shared_instance_key_boot_rotation_restart_and_retry(monkeypatch, tmp_path):
    module = _supervisor_module(monkeypatch, tmp_path)
    path = tmp_path / 'instance.key';path.write_text('first-key')
    monkeypatch.setenv('CALLIOPE_OPENROUTER_KEY_FILE', str(path))
    monkeypatch.setenv('OPENROUTER_API_KEY', 'stale-container-key')
    entries = []
    class Pool:
        def entries(self): return list(entries)
        def add_entry(self, entry): entries.append(entry);return entry
        def resolve_target(self, target):
            return next(((i,e,None) for i,e in enumerate(entries) if e.id==target),(None,None,None))
        def remove_index(self, index): return entries.pop(index)
    native = types.ModuleType('agent.credential_pool')
    native.AUTH_TYPE_API_KEY = 'api_key'
    native.PooledCredential = lambda **values: types.SimpleNamespace(**values)
    native.load_pool = lambda provider: Pool()
    monkeypatch.setitem(sys.modules, 'agent.credential_pool', native)
    supervisor = module.GatewaySupervisor();probes=[];events=[]
    monkeypatch.setattr(supervisor, '_provider_base_url', lambda provider: 'https://provider.test')
    monkeypatch.setattr(module, '_probe_provider_key', lambda provider,token: probes.append(token) or {'ok':True,'reachable':True})
    monkeypatch.setattr(supervisor, 'stop_child', lambda: events.append('stop'))
    def start(): events.append('start');supervisor._restore_provider_environment()
    monkeypatch.setattr(supervisor, 'start_child', start)
    monkeypatch.setattr(supervisor, 'wait_healthy', lambda **kw: events.append('healthy'))
    assert supervisor.sync_instance_key(restart=False)['changed']
    assert not events and len(entries)==1 and probes==['first-key']
    start();events.clear()
    assert supervisor.provider_env['OPENROUTER_API_KEY']=='first-key'
    assert not supervisor.sync_instance_key()['restarted'] and not events
    path.write_text('second-key')
    assert supervisor.sync_instance_key()['restarted']
    assert events==['stop','start','healthy'] and probes==['first-key','second-key']
    assert len(entries)==1 and entries[0].access_token=='second-key'
    assert supervisor._gateway_child_environment()['OPENROUTER_API_KEY']=='second-key'
    events.clear();supervisor.sync_instance_key();assert not events
    with pytest.raises(ValueError,match='shared instance key'):supervisor.install_provider_key('openrouter','different-key')
    with pytest.raises(ValueError,match='cannot be disconnected'):supervisor.remove_provider_key('openrouter')
    path.write_text('third-key')
    monkeypatch.setattr(supervisor,'wait_healthy',lambda **kw: (_ for _ in ()).throw(RuntimeError('private diagnostic')))
    with pytest.raises(RuntimeError,match='restart health check') as failure:supervisor.sync_instance_key()
    assert 'third-key' not in str(failure.value) and 'private diagnostic' not in str(failure.value)
    assert supervisor.instance_key_restart_pending
    monkeypatch.setattr(supervisor,'wait_healthy',lambda **kw: None)
    assert supervisor.sync_instance_key()['restarted'] and not supervisor.instance_key_restart_pending
    assert len(entries)==1 and probes==['first-key','second-key','third-key']
    path.write_text('rejected-key')
    monkeypatch.setattr(module,'_probe_provider_key',lambda *a,**kw: {'ok':False,'reachable':False})
    with pytest.raises(ValueError,match='could not validate'):supervisor.sync_instance_key()
    assert len(entries)==1 and entries[0].access_token=='third-key'


def test_claude_cli_env_names_the_login_user_when_the_container_does_not(monkeypatch, tmp_path):
    """Claude Code's login check reads USER/LOGNAME; without them a logged-in
    CLI reports loggedIn: false. The container leaves them unset, so the
    supervisor fills them from the running uid and keeps any given value."""
    import pwd

    module = _supervisor_module(monkeypatch, tmp_path)
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("LOGNAME", raising=False)
    env = module._claude_cli_env()
    name = pwd.getpwuid(os.getuid()).pw_name
    assert env["USER"] == name and env["LOGNAME"] == name
    assert env["DISABLE_TELEMETRY"] == "1"
    monkeypatch.setenv("USER", "someone-else")
    assert module._claude_cli_env()["USER"] == "someone-else"
