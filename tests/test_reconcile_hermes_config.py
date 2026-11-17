from __future__ import annotations

import importlib.util
import pytest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docker" / "calliope" / "reconcile-hermes-config.py"


def _module():
    spec = importlib.util.spec_from_file_location("reconcile_hermes_config", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reconcile_updates_managed_budgets_and_preserves_other_config(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(
        "_config_version: 34\n"
        "model:\n"
        "  default: calliope\n"
        "  provider: custom\n"
        "memory:\n"
        "  memory_enabled: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CALLIOPE_MODEL_CONTEXT_LENGTH", "28672")
    monkeypatch.setenv("CALLIOPE_MODEL_MAX_TOKENS", "8192")
    module = _module()

    assert module.reconcile(path) is True
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert payload["model"] == {
        "default": "calliope",
        "provider": "custom",
        "base_url": "${env:RVBBIT_CLOVER_OPENAI_BASE_URL}",
        "api_key": "${env:RVBBIT_CLOVER_KEY}",
        "context_length": 28_672,
        "max_tokens": 8_192,
    }
    assert payload["memory"]["memory_enabled"] is True
    toolsets = payload["platform_toolsets"]["api_server"]
    assert {"terminal", "file", "Warehouse", "Roundtable"} <= set(toolsets)
    assert "Specialist" not in toolsets
    assert "hermes-api-server" not in toolsets  # Current Hermes uses expanded native toolsets.
    assert module.reconcile(path) is False


def test_reconcile_scopes_default_agent_to_parent_warehouse_lane(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "model: {}\n"
        "platform_toolsets:\n"
        "  api_server:\n"
        "    - hermes-api-server\n"
        "    - Warehouse\n"
        "    - Specialist\n",
        encoding="utf-8",
    )

    module = _module()
    assert module.reconcile(path) is True
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    toolsets = payload["platform_toolsets"]["api_server"]
    assert {"terminal", "file", "Warehouse", "Roundtable"} <= set(toolsets)
    assert "Specialist" not in toolsets
    assert "hermes-api-server" not in toolsets  # Current Hermes uses expanded native toolsets.
    assert module.reconcile(path) is False


def test_reconcile_uses_safe_defaults_for_invalid_environment(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("model: {}\n", encoding="utf-8")
    monkeypatch.setenv("CALLIOPE_MODEL_CONTEXT_LENGTH", "not-a-number")
    monkeypatch.setenv("CALLIOPE_MODEL_MAX_TOKENS", "-1")

    assert _module().reconcile(path) is True
    model = yaml.safe_load(path.read_text(encoding="utf-8"))["model"]
    assert model["context_length"] == 262_144   # 2026-08-17: 131k left ~6k over the ~86k base
    assert model["max_tokens"] == 32_768   # 2026-08-18: 8192 cut every whole-dashboard write


def test_reconcile_replaces_upstream_context_defaults_but_keeps_operator_values(tmp_path):
    """Hermes seeds compaction at 85% and 50 KB tool outputs; the appliance wants
    conversational defaults, but an operator's own numbers must survive."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "_config_version: 35\n"
        "model:\n  default: calliope\n  provider: custom\n"
        "compression:\n  enabled: true\n  threshold: 0.85\n  target_ratio: 0.2\n  protect_last_n: 20\n"
        "tool_output:\n  max_bytes: 50000\n  max_lines: 2000\n",
        encoding="utf-8",
    )
    module = _module()
    assert module.reconcile(path) is True
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert payload["compression"]["threshold"] == 0.7
    assert payload["compression"]["target_ratio"] == 0.25
    assert payload["compression"]["protect_last_n"] == 12
    assert payload["compression"]["proactive_prune_tokens"] == 120000
    assert payload["compression"]["proactive_prune_min_result_chars"] == 6000
    assert payload["compression"]["proactive_prune_min_reclaim_tokens"] == 8192
    assert payload["compression"]["idle_compact_after_seconds"] == 3600
    assert payload["tool_output"] == {"max_bytes": 50000, "max_lines": 2000}
    assert payload["session_reset"] == {"mode": "idle", "idle_minutes": 240}
    # the background skill curator (a full-history fork after every build turn) is off
    assert payload["skills"]["creation_nudge_interval"] == 0
    assert module.reconcile(path) is False

    # An operator who tuned these keeps them.
    payload["compression"]["threshold"] = 0.6
    payload["compression"]["proactive_prune_tokens"] = 45000
    payload["compression"]["idle_compact_after_seconds"] = 900
    payload["tool_output"]["max_bytes"] = 30000
    payload["session_reset"]["idle_minutes"] = 60
    payload["skills"]["creation_nudge_interval"] = 50          # an operator who wants the curator keeps it
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    module.reconcile(path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert payload["skills"]["creation_nudge_interval"] == 50
    assert payload["compression"]["threshold"] == 0.6
    assert payload["compression"]["proactive_prune_tokens"] == 45000
    assert payload["compression"]["idle_compact_after_seconds"] == 900
    assert payload["tool_output"]["max_bytes"] == 30000
    assert payload["session_reset"]["idle_minutes"] == 60

    # A box that received the earlier appliance defaults is moved forward:
    # the first pass (0.45 / 24 KB), the second (0.5 / 40 KB, prune off) and
    # the third (0.7 with a 60k prune line that sat below the ~86k base).
    for stale_threshold, stale_prune, stale_bytes in ((0.45, 0, 24000), (0.5, 0, 40000), (0.7, 60000, 50000)):
        payload["compression"]["threshold"] = stale_threshold
        payload["compression"]["proactive_prune_tokens"] = stale_prune
        payload["compression"]["idle_compact_after_seconds"] = 0
        payload["tool_output"]["max_bytes"] = stale_bytes
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        assert module.reconcile(path) is True
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert payload["compression"]["threshold"] == 0.7
        assert payload["compression"]["proactive_prune_tokens"] == 120000
        assert payload["compression"]["idle_compact_after_seconds"] == 3600
        assert payload["tool_output"]["max_bytes"] == 50000
    # …and the 30-message protected tail (which stopped a 27-message session
    # from compacting at all once turns carried history) moves to 12, while an
    # operator's own tail length is theirs.
    payload["compression"]["protect_last_n"] = 30
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    assert module.reconcile(path) is True
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["compression"]["protect_last_n"] == 12
    payload["compression"]["protect_last_n"] = 8
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    module.reconcile(path)
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["compression"]["protect_last_n"] == 8


def test_fallback_model_apply_carry_and_validation():
    policy = _module()
    payload = {"model": {"default": "grok-4.6", "provider": "xai-oauth"}}
    assert policy.apply_fallback_model(payload, "openrouter", "openai/gpt-5.6-sol") is True
    assert payload["fallback_model"] == {"provider": "openrouter", "model": "openai/gpt-5.6-sol"}
    assert policy.apply_fallback_model(payload, "openrouter", "openai/gpt-5.6-sol") is False  # idempotent
    with pytest.raises(ValueError):
        policy.apply_fallback_model(payload, "custom", "calliope")   # the appliance cannot be its own fallback
    with pytest.raises(ValueError):
        policy.apply_fallback_model(payload, "vertex", "gemini-3.1-pro-preview")  # unsupported provider id
    # Gemini (the AI Studio API key) is a hosted provider like xai/openai-api.
    assert "gemini" in policy.HOSTED_MODEL_PROVIDERS
    assert policy.apply_fallback_model(payload, "gemini", "gemini-3.1-pro-preview") is True
    assert payload["fallback_model"] == {"provider": "gemini", "model": "gemini-3.1-pro-preview"}
    assert policy.apply_fallback_model(payload, None, None) is True  # clear
    assert "fallback_model" not in payload

    # the specialists reconciler carries a valid operator fallback and drops junk
    desired = {"model": {}}
    policy.carry_fallback_model({"fallback_model": {"provider": "openrouter", "model": "m"}}, desired)
    assert desired["fallback_model"] == {"provider": "openrouter", "model": "m"}
    policy.carry_fallback_model({"fallback_model": {"provider": "bogus", "model": "m"}}, desired)
    assert "fallback_model" not in desired
    # reconcile_payload keeps the key (top-level unknowns survive invariants)
    payload = {"model": {"default": "calliope", "provider": "custom"},
               "fallback_model": {"provider": "openrouter", "model": "m"}}
    policy.reconcile_payload(payload)
    assert payload["fallback_model"] == {"provider": "openrouter", "model": "m"}


def test_empty_root_profile_uses_openrouter_release_default(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('model: {}\n')
    _module().reconcile(path)
    model = yaml.safe_load(path.read_text())['model']
    assert model['default'] == 'openai/gpt-6.1-sol'
    assert model['provider'] == 'openrouter'
    assert 'base_url' not in model
    assert 'api_key' not in model
