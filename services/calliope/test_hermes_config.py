import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

import hermes_config


def _policy_module():
    path = Path(__file__).parents[2] / "docker/calliope/reconcile-hermes-config.py"
    spec = importlib.util.spec_from_file_location("test_calliope_hermes_policy", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sql_timeouts_survive_config_reconciliation_on_both_lanes(monkeypatch):
    import calliope
    policy = _policy_module()
    path = Path(__file__).parents[2] / 'docker/calliope/hermes-config.yaml'
    payload = yaml.safe_load(path.read_text())
    assert policy.validate_hosted_payload(payload)['valid']
    for lane in ('Warehouse', 'Specialist'):
        payload['mcp_servers'][lane]['tool_timeouts'] = {'generate_image': 660}
    assert policy.reconcile_payload(payload)
    monkeypatch.setenv('WAREHOUSE_ADMIN_SQL_TIMEOUT_MS', '600000')
    for lane in ('Warehouse', 'Specialist'):
        config = payload['mcp_servers'][lane]
        assert config['timeout'] == 90
        for tool in ('run_workspace_sql', 'administer_local_sql'):
            assert config['tool_timeouts'][tool] > calliope._local_admin_sql_timeout_ms() / 1000 + 30
    import runpy
    profiles = runpy.run_path(str(path.with_name('reconcile-hermes-specialists.py')))
    standalone = profiles['_profile_config']('dashboard-studio')['mcp_servers']['Specialist']
    assert standalone['tool_timeouts'] == payload['mcp_servers']['Specialist']['tool_timeouts']


def test_failed_sql_receipts_are_failed_activity_not_transport_success():
    import server
    failed = {'run': {'id': 'run-one', 'status': 'failed', 'error': 'Statement 2 failed; batch rolled back'}}
    for tool in ('run_workspace_sql', 'administer_local_sql'):
        error = server._activity_error(tool, failed, None)
        assert error['code'] == 'SQL_EXECUTION_FAILED' and error['run_id'] == 'run-one'
        assert server._activity_error(tool, {'run': {'status': 'complete'}}, None) is None
        assert server._activity_error(tool, {'run': {'status': 'awaiting_approval'}}, None) is None
    explicit = {'code': 'TIMEOUT'}
    assert server._activity_error('run_workspace_sql', failed, explicit) is explicit


def test_secret_bindings_support_yaml_and_native_hermes_env_without_values():
    config = 'platforms:\n  telegram:\n    token: "${env:CALLIOPE_TELEGRAM_TOKEN}"\n'
    assert hermes_config._clean_bindings(
        {"CALLIOPE_TELEGRAM_TOKEN": "hermes/telegram/BOT_TOKEN"}, config
    ) == {"CALLIOPE_TELEGRAM_TOKEN": "hermes/telegram/BOT_TOKEN"}
    assert hermes_config._clean_bindings(
        {"TELEGRAM_BOT_TOKEN": "hermes/telegram/BOT_TOKEN"},
        "platforms:\n  telegram:\n    enabled: true\n",
    ) == {"TELEGRAM_BOT_TOKEN": "hermes/telegram/BOT_TOKEN"}
    with pytest.raises(ValueError, match="control environment variable"):
        hermes_config._clean_bindings({"PATH": "hermes/runtime/PATH"}, config)
    with pytest.raises(ValueError, match="invalid canonical"):
        hermes_config._clean_bindings(
            {"CALLIOPE_TELEGRAM_TOKEN": "not a reference with spaces"}, config
        )


def test_redacted_diff_has_paths_but_no_values():
    before = "platforms:\n  telegram:\n    enabled: false\n"
    after = (
        "platforms:\n  telegram:\n    enabled: true\n"
        '    token: "${env:CALLIOPE_TELEGRAM_TOKEN}"\n'
    )
    diff = hermes_config._redacted_diff(before, after)
    assert {item["path"] for item in diff} == {
        "platforms.telegram.enabled",
        "platforms.telegram.token",
    }
    assert "CALLIOPE_TELEGRAM_TOKEN" not in str(diff)


def test_validate_delegates_to_private_supervisor(monkeypatch):
    captured = {}

    def control(method, path, payload, *, timeout):
        captured.update(method=method, path=path, payload=payload, timeout=timeout)
        return {"valid": True, "config_hash": hashlib.sha256(payload["config_yaml"].encode()).hexdigest()}

    monkeypatch.setattr(hermes_config, "_control", control)
    result = hermes_config.validate("model: {}\n")
    assert result["valid"] is True
    assert captured == {
        "method": "POST",
        "path": "/v1/validate",
        "payload": {"config_yaml": "model: {}\n"},
        "timeout": 20,
    }
    assert result["secret_bindings"] == {}


def test_pinned_source_matches_shipping_docs_bundle():
    source = hermes_config._source()
    assert source["revision"] == "d337b736aa1e8ebecfab043842d13e4a2d2f48a3"
    assert source["image_digest"] == (
        "sha256:6bece0644e29a347e5ae17db43c36938c86f171c6f5e0cef18aa2075d331f3a3"
    )


def test_hosted_policy_keeps_customer_mcps_out_of_hermes():
    policy = _policy_module()
    config_path = Path(__file__).parents[2] / "docker/calliope/hermes-config.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert policy.validate_hosted_payload(payload)["valid"] is True
    payload["mcp_servers"]["Linear"] = {"url": "https://example.test/mcp"}
    result = policy.validate_hosted_payload(payload)
    assert result["valid"] is False
    assert result["additional_mcp_servers"] == ["Linear"]
    assert "Warehouse registry" in result["errors"][-1]["message"]


def test_hosted_policy_rejects_plaintext_authorization_headers():
    policy = _policy_module()
    config_path = Path(__file__).parents[2] / "docker/calliope/hermes-config.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["mcp_servers"]["Warehouse"]["headers"]["Authorization"] = "Bearer leaked"
    result = policy.validate_hosted_payload(payload)
    assert result["valid"] is False
    assert any("plaintext authorization" in issue["message"] for issue in result["errors"])


def test_hosted_policy_reconciles_legacy_durable_config_before_mutation():
    policy = _policy_module()
    config_path = Path(__file__).parents[2] / "docker/calliope/hermes-config.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    del payload["platform_toolsets"]

    assert policy.validate_hosted_payload(payload)["valid"] is False
    assert policy.reconcile_payload(payload) is True
    assert policy.validate_hosted_payload(payload)["valid"] is True
    lane = payload["platform_toolsets"]["api_server"]
    # Composed lane, not the preset: the preset bundles the credential-gated
    # Firecrawl web tools and BFL video suite — dead ends on the appliance.
    # Governed web search lives in the SQL dialect via capability_search.
    assert "hermes-api-server" not in lane and "web" not in lane and "search" not in lane
    assert lane == [
        "terminal",
        "file",
        "vision",
        "image_gen",
        "skills",
        "browser",
        "todo",
        "memory",
        "session_search",
        "code_execution",
        "cronjob",
        "homeassistant",
        "Warehouse",
        "Roundtable",
    ]
    assert policy.reconcile_payload(payload) is False


def test_hosted_policy_refreshes_generated_profile_invariant_after_import(
    tmp_path, monkeypatch
):
    """The supervisor imports policy before first-boot specialist generation.

    A profile added to the durable cache afterward must become both the
    validator's expected allowlist and the reconciler's desired allowlist
    without restarting the supervisor.
    """
    policy = _policy_module()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    payload = _shipping_payload()
    assert policy.reconcile_payload(payload) is True  # first-boot background fallback
    assert policy.reconcile_payload(payload) is False
    assert policy.validate_hosted_payload(payload)["valid"] is True

    cache = tmp_path / ".calliope-seeds" / "generated-specialists.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps({"specialists": [{"specialist_key": "new-live-profile"}]}),
        encoding="utf-8",
    )

    result = policy.validate_hosted_payload(payload)
    assert result["valid"] is False
    issue = next(
        item
        for item in result["errors"]
        if item["path"] == "gateway.multiplex_profile_allowlist"
    )
    assert "new-live-profile" in issue["expected"]

    assert policy.reconcile_payload(payload) is True
    assert payload["gateway"]["multiplex_profile_allowlist"][-1] == "new-live-profile"
    assert policy.validate_hosted_payload(payload)["valid"] is True


def _shipping_payload():
    config_path = Path(__file__).parents[2] / "docker/calliope/hermes-config.yaml"
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def test_hosted_policy_lets_the_operator_choose_provider_and_model(tmp_path, monkeypatch):
    """No aliases: the model block IS the agent's choice. A native provider
    carries no bindings; the Hutch route (`custom`) must keep its env refs;
    budgets are the operator's as long as they are positive integers."""
    policy = _policy_module()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    payload = _shipping_payload()
    assert policy.validate_hosted_payload(payload)["valid"] is True

    policy.apply_model_choice(payload, "openrouter", "openai/gpt-5.6-sol")
    assert payload["model"] == {"default": "openai/gpt-5.6-sol", "provider": "openrouter",
                                "context_length": 262144, "max_tokens": 32768}
    assert payload["platforms"]["api_server"]["extra"]["model_name"] == "openai/gpt-5.6-sol"
    assert policy.validate_hosted_payload(payload)["valid"] is True
    assert policy.reconcile_payload(payload) is True           # seeds only the background fallback
    assert policy.reconcile_payload(payload) is False          # the main-model choice is not an invariant

    payload["model"]["context_length"] = 200_000               # operator budget survives
    assert policy.reconcile_payload(payload) is False
    assert policy.validate_hosted_payload(payload)["valid"] is True

    policy.apply_model_choice(payload, "openrouter", "small/200k", 200_000, 16_384)
    assert payload["model"]["context_length"] == 200_000 and payload["model"]["max_tokens"] == 16_384
    assert policy.working_max_tokens(65_536) == 32_768 and policy.working_max_tokens(16_384) == 16_384
    assert policy.working_max_tokens(1_000) is None and policy.working_max_tokens(None) is None
    assert policy.working_context_length(1_048_576) == 262_144
    assert policy.working_context_length(200_000) == 200_000
    assert policy.working_context_length(8_192) is None and policy.working_context_length(None) is None
    with pytest.raises(ValueError, match="at least"):
        policy.apply_model_choice(payload, "openrouter", "x", 1000)

    policy.apply_model_choice(payload, "custom", "calliope")
    assert payload["model"]["api_key"] == "${env:RVBBIT_CLOVER_KEY}"
    assert payload["model"]["base_url"] == "${env:RVBBIT_CLOVER_OPENAI_BASE_URL}"
    assert payload["model"]["context_length"] == 200_000
    assert payload["platforms"]["api_server"]["extra"]["model_name"] == "calliope"
    assert policy.validate_hosted_payload(payload)["valid"] is True


def test_hosted_policy_repairs_stale_api_server_model_alias():
    policy = _policy_module()
    payload = _shipping_payload()
    payload["model"].update(default="stealth/ox-alpha", provider="openrouter")
    payload["model"].pop("base_url", None)
    payload["model"].pop("api_key", None)
    assert payload["platforms"]["api_server"]["extra"]["model_name"] == "calliope"
    result = policy.validate_hosted_payload(payload)
    assert result["valid"] is False
    assert any(
        issue["path"] == "platforms.api_server.extra.model_name"
        for issue in result["errors"]
    )
    assert policy.reconcile_payload(payload) is True
    assert payload["platforms"]["api_server"]["extra"]["model_name"] == "stealth/ox-alpha"
    assert policy.validate_hosted_payload(payload)["valid"] is True


@pytest.mark.parametrize(
    "mutation,problem",
    [
        (lambda m: m.update(provider="vertex"), "hosted Calliope providers"),
        (lambda m: m.update(default=""), "non-empty"),
        (lambda m: m.update(default="gpt 5"), "whitespace"),
        (lambda m: m.update(api_key="sk-live-leaked"), "plaintext secret"),
        (lambda m: m.update(base_url="https://example.test/v1"), "hosted Calliope invariant would change"),
        (lambda m: m.update(context_length=0), "positive integer"),
        (lambda m: (m.update(provider="openrouter"), m.pop("api_key")), "credential pool"),  # base_url left behind
    ],
)
def test_hosted_policy_rejects_unsafe_model_blocks(mutation, problem):
    policy = _policy_module()
    payload = _shipping_payload()
    mutation(payload["model"])
    result = policy.validate_hosted_payload(payload)
    assert result["valid"] is False
    assert any(problem in issue["message"] for issue in result["errors"]), result["errors"]


def test_reconcile_seeds_the_pinned_tool_working_set():
    """tools.tool_search.pinned: seeded when missing or empty, an operator's
    own list survives, and the legacy bool shape keeps its enabled intent."""
    policy = _policy_module()
    payload = _shipping_payload()
    payload.pop("tools", None)
    assert policy.reconcile_payload(payload) is True
    pinned = payload["tools"]["tool_search"]["pinned"]
    assert pinned == list(policy.PINNED_TOOLS_DEFAULT)
    assert "mcp__Warehouse__capability_search" in pinned
    assert "mcp__Warehouse__calliope_observation_context" in pinned
    assert "mcp__Warehouse__calliope_decision_request" in pinned
    assert "mcp__Roundtable__delegate_calliope_specialist" in pinned
    # idempotent
    assert all("pinned" != k or v for k, v in payload["tools"]["tool_search"].items())
    before = dict(payload["tools"]["tool_search"])
    policy.reconcile_payload(payload)
    assert payload["tools"]["tool_search"] == before
    # operator's own list survives
    payload["tools"]["tool_search"]["pinned"] = ["mcp__Warehouse__run_sql"]
    policy.reconcile_payload(payload)
    assert payload["tools"]["tool_search"]["pinned"] == ["mcp__Warehouse__run_sql"]
    # legacy bool keeps intent
    payload["tools"]["tool_search"] = False
    policy.reconcile_payload(payload)
    assert payload["tools"]["tool_search"]["enabled"] is False
    assert payload["tools"]["tool_search"]["pinned"] == list(policy.PINNED_TOOLS_DEFAULT)


@pytest.mark.parametrize("previous_index", range(4))
def test_reconcile_moves_the_previous_pinned_default_forward(previous_index):
    policy = _policy_module()
    payload = _shipping_payload()
    payload["tools"]["tool_search"]["pinned"] = list(
        policy.PINNED_TOOLS_PREVIOUS_DEFAULTS[previous_index]
    )

    assert policy.reconcile_payload(payload) is True
    assert payload["tools"]["tool_search"]["pinned"] == list(
        policy.PINNED_TOOLS_DEFAULT
    )


def test_auxiliary_settings_apply_and_carry_preserve_per_task_overrides():
    """The background-task control installs one marked fallback across native
    auxiliary tasks while preserving task options and hand-authored fallback
    entries. It remains independent of the conversation fallback model."""
    policy = _policy_module()

    manual = {"provider": "nous", "model": "Hermes-4-405B"}
    payload = {"auxiliary": {"vision": {"provider": "auto", "timeout": 77, "fallback_chain": [manual]}}}
    assert policy.apply_auxiliary_settings(payload, "openrouter", "qwen/qwen3-30b-a3b:free") is True
    vision = payload["auxiliary"]["vision"]
    assert vision["provider"] == "auto" and vision["timeout"] == 77
    assert vision["fallback_chain"] == [
        {"provider": "openrouter", "model": "qwen/qwen3-30b-a3b:free", "calliope_managed": True},
        manual,
    ]
    for task in policy.CALLIOPE_AUXILIARY_TASKS:
        assert payload["auxiliary"][task]["fallback_chain"][0]["calliope_managed"] is True
    # idempotent second apply reports no change
    assert policy.apply_auxiliary_settings(payload, "openrouter", "qwen/qwen3-30b-a3b:free") is False
    # Switching providers replaces our entry and binds Clover through env refs.
    assert policy.apply_auxiliary_settings(payload, "custom", "calliope") is True
    managed = payload["auxiliary"]["compression"]["fallback_chain"][0]
    assert managed == {
        "provider": "custom", "model": "calliope", "calliope_managed": True,
        "base_url": "${env:RVBBIT_CLOVER_OPENAI_BASE_URL}",
        "api_key": "${env:RVBBIT_CLOVER_KEY}",
    }
    # Clearing removes only Calliope's marked entries.
    assert policy.apply_auxiliary_settings(payload, "", "") is True
    assert payload["auxiliary"] == {
        "vision": {"provider": "auto", "timeout": 77, "fallback_chain": [manual]},
    }
    # Clearing also retires the old OpenRouter-only keys.
    bare = {"auxiliary": {"openrouter_model": "m", "free_only": True}}
    policy.apply_auxiliary_settings(bare, None, None)
    assert bare["auxiliary"] == {}
    with pytest.raises(ValueError):
        policy.apply_auxiliary_settings({}, "openrouter", "model with spaces")
    with pytest.raises(ValueError):
        policy.apply_auxiliary_settings({}, "bogus", "model")

    desired = {"auxiliary": {"stale": True}}
    carried = {"compression": {"fallback_chain": [{"provider": "openrouter", "model": "m", "calliope_managed": True}]}}
    policy.carry_auxiliary_settings({"auxiliary": carried}, desired)
    assert desired["auxiliary"] == carried
    policy.carry_auxiliary_settings({}, desired)
    assert desired["auxiliary"] == carried
    policy.carry_auxiliary_settings({"auxiliary": {}}, desired)
    assert desired["auxiliary"] == {}


def test_background_fallback_is_seeded_once_without_replacing_main_model():
    policy = _policy_module()
    payload = {"model": {"provider": "custom", "default": "calliope-sonnet"}}
    assert policy.apply_managed_auxiliary_defaults(payload)
    assert payload["model"] == {"provider": "custom", "default": "calliope-sonnet"}
    for task in policy.CALLIOPE_AUXILIARY_TASKS:
        lane = payload["auxiliary"][task]
        assert "provider" not in lane and "model" not in lane
        assert lane["fallback_chain"] == [{
            "provider": "custom", "model": "calliope-luna", "calliope_managed": True,
            **policy.CLOVER_MODEL_BINDINGS,
        }]
    assert not policy.apply_managed_auxiliary_defaults(payload)
    policy.apply_auxiliary_settings(payload, None, None)
    policy.reconcile_payload(payload)
    assert payload["auxiliary"] == {}  # Explicit automatic survives full boot reconciliation.


@pytest.mark.parametrize("aux", [{}, {"compression": {"provider": "openrouter", "model": "operator/model"}}, {"openrouter_model": "legacy/model"}])
def test_background_defaults_preserve_existing_operator_settings(aux):
    policy = _policy_module()
    payload = {"auxiliary": aux}
    assert not policy.apply_managed_auxiliary_defaults(payload)
    assert payload["auxiliary"] == aux


def test_hosted_policy_retires_model_route_aliases():
    policy = _policy_module()
    payload = _shipping_payload()
    payload["platforms"]["api_server"]["extra"]["model_routes"] = {
        "calliope": {"model": "grok-4.6", "provider": "xai-oauth"},
    }
    result = policy.validate_hosted_payload(payload)
    assert result["valid"] is False
    assert any("aliases were retired" in issue["message"] for issue in result["errors"])
    assert policy.reconcile_payload(payload) is True
    assert "model_routes" not in payload["platforms"]["api_server"]["extra"]
    assert policy.validate_hosted_payload(payload)["valid"] is True


def test_hosted_policy_seeds_and_normalises_the_model_block():
    policy = _policy_module()
    payload = _shipping_payload()
    # A hand-edited block the appliance cannot serve falls back to the Hutch route…
    payload["model"] = {"default": "", "provider": "anthropic", "context_length": 131072}
    assert policy.reconcile_payload(payload) is True
    assert payload["model"] == {"default": "calliope", "provider": "custom",
                                **policy.CLOVER_MODEL_BINDINGS,
                                "context_length": 262144, "max_tokens": 32768}   # 131072 / 8192 moved forward
    # …a native provider carrying stale Hutch bindings loses them.
    payload["model"].update(provider="xai-oauth", default="grok-4.6")
    assert policy.reconcile_payload(payload) is True
    assert "api_key" not in payload["model"] and "base_url" not in payload["model"]
    assert policy.validate_hosted_payload(payload)["valid"] is True
    # carry_model_choice: an operator's choice survives a regenerated block.
    fresh = {"default": "calliope", "provider": "custom", **policy.CLOVER_MODEL_BINDINGS, "context_length": 262144, "max_tokens": 32768}
    carried = policy.carry_model_choice({"default": "grok-4.6", "provider": "xai-oauth", "context_length": 100_000}, dict(fresh))
    assert carried == {"default": "grok-4.6", "provider": "xai-oauth", "context_length": 100_000, "max_tokens": 32768}


def test_controller_adopts_a_live_config_that_changed_underneath_it(monkeypatch):
    """The boot reconciler rewrites config.yaml when appliance invariants move;
    until the store re-baselines, every UI candidate built from the stale
    active revision fails validation on invariants nobody touched (the
    'calliope-alt' rejection, 2026-08-17). Adopting the live secret-free config
    as the active revision fixes that without a restart."""
    import hermes_config as module

    executed = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            executed.append((" ".join(sql.split()), params))
            return self

        def fetchone(self):
            sql = executed[-1][0]
            if "SELECT id FROM rvbbit.hermes_config_revisions WHERE config_hash" in sql:
                return None
            return None

    monkeypatch.setattr(module, "_source", lambda: {"revision": "abc", "image_digest": "sha256:x"})
    live = {"config_yaml": "model:\n  context_length: 262144\n", "config_hash": "new-hash", "validation": {"valid": True}}
    active = {"id": "11111111-1111-1111-1111-111111111111", "config_hash": "old-hash", "secret_bindings": {"RVBBIT_CLOVER_KEY": "credential://clover"}}
    revision_id = module._adopt_live_config(lambda: Conn(), live, active)
    assert revision_id
    sql_text = "\n".join(sql for sql, _ in executed)
    assert "INSERT INTO rvbbit.hermes_config_revisions" in sql_text
    inserted = next(params for sql, params in executed if "INSERT INTO rvbbit.hermes_config_revisions" in sql)
    assert inserted[1] == live["config_yaml"] and inserted[2] == "new-hash"
    assert "adopted live gateway config" in inserted[3]
    assert json.loads(inserted[6]) == {"RVBBIT_CLOVER_KEY": "credential://clover"}   # bindings carried forward, never values
    assert "SET state='superseded'" in sql_text                            # the stale revision is closed out
    # hermes_config_revisions_one_active_idx: the stale row must be retired
    # before the adopted one is inserted as active (a hosted install looped on a
    # UniqueViolation when the order was the other way round).
    superseded_at = next(i for i, (sql, _) in enumerate(executed) if "SET state='superseded'" in sql)
    inserted_at = next(i for i, (sql, _) in enumerate(executed) if "INSERT INTO rvbbit.hermes_config_revisions" in sql)
    assert superseded_at < inserted_at
    assert "'activated','calliope-controller'" in sql_text
    assert "active_revision_id=%s,last_good_revision_id=%s,active_config_hash=%s" in sql_text
    # the reconcile loop reaches for it only when nothing is pending and the hashes differ
    from pathlib import Path
    src = Path(module.__file__).read_text(encoding="utf-8")
    loop = src.split("def reconcile_once", 1)[1]
    assert "_adopt_live_config(conn_factory, current, active)" in loop
    assert 'current.get("config_hash") != active.get("config_hash")' in loop


def test_web_coordinator_reconciles_native_delegation_out_of_legacy_config():
    policy = _policy_module()
    payload = _shipping_payload()
    payload["platform_toolsets"]["api_server"].insert(10, "delegation")
    payload["platform_toolsets"]["google_chat"] = ["delegation", "Warehouse"]
    assert policy.validate_hosted_payload(payload)["valid"] is False
    assert policy.reconcile_payload(payload) is True
    lane = payload["platform_toolsets"]["api_server"]
    assert "delegation" not in lane
    assert "Roundtable" in lane
    assert payload["platform_toolsets"]["google_chat"] == ["delegation", "Warehouse"]
    assert policy.validate_hosted_payload(payload)["valid"] is True
    assert policy.reconcile_payload(payload) is False
    assert "mcp__Warehouse__list_calliope_specialists" in payload["tools"]["tool_search"]["pinned"]


@pytest.mark.parametrize("source", ["process", "native_env", "flat_oauth", "versioned_oauth"])
def test_image_provider_autodetection_uses_hermes_credential_stores(tmp_path, monkeypatch, source):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    expected = "openrouter"
    if source == "process":
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-key")
    elif source == "native_env":
        (tmp_path / ".env").write_text('OPENROUTER_API_KEY="test-only-key"\n')
    else:
        expected = "xai"
        auth = {"xai-oauth": {"access_token": "test-only-token"}}
        if source == "versioned_oauth":
            auth = {"version": 1, "providers": auth}
        (tmp_path / "auth.json").write_text(json.dumps(auth))
    policy = _policy_module()
    payload = _shipping_payload()
    payload.pop("image_gen", None)
    assert policy.reconcile_payload(payload) is True
    assert payload["image_gen"]["provider"] == expected
    assert "test-only" not in json.dumps(payload)
    assert policy.reconcile_payload(payload) is False
    payload["image_gen"] = {"provider": "openai", "model": "operator-choice"}
    policy.reconcile_payload(payload)
    assert payload["image_gen"] == {"provider": "openai", "model": "operator-choice"}


def test_image_provider_ignores_empty_persistent_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / ".env").write_text('OPENROUTER_API_KEY=""\n')
    policy = _policy_module()
    payload = _shipping_payload()
    payload.pop("image_gen", None)
    policy.reconcile_payload(payload)
    assert not payload.get("image_gen")


def test_hosted_policy_renames_legacy_lane_keys_and_pins():
    """Boxes provisioned before 2026-09-23 carry WarehouseSpecialist/WarehouseRoundtable keys and
    pins; the policy renames them in place (an operator's pin list keeps its choices) and drops
    the old key when the new one is already present."""
    policy = _policy_module()
    payload = {
        "mcp_servers": {"Warehouse": {"url": "x"}, "WarehouseSpecialist": {"url": "s"}, "WarehouseRoundtable": {"url": "r"}, "Roundtable": {"url": "already"}},
        "tools": {"tool_search": {"pinned": ["mcp__Warehouse__run_sql", "mcp__WarehouseRoundtable__delegate_calliope_specialist", "mcp__WarehouseSpecialist__compare", "mcp__Roundtable__delegate_calliope_specialist"]}},
        "platform_toolsets": {"api_server": ["terminal", "Warehouse", "WarehouseSpecialist", "WarehouseRoundtable"]},
    }
    assert policy.normalize_legacy_lane_names(payload) is True
    assert set(payload["mcp_servers"]) == {"Warehouse", "Specialist", "Roundtable"}
    assert payload["mcp_servers"]["Roundtable"] == {"url": "already"}
    assert payload["tools"]["tool_search"]["pinned"] == ["mcp__Warehouse__run_sql", "mcp__Roundtable__delegate_calliope_specialist", "mcp__Specialist__compare"]
    assert payload["platform_toolsets"]["api_server"] == ["terminal", "Warehouse", "Specialist", "Roundtable"]
    assert policy.normalize_legacy_lane_names(payload) is False


def test_managed_media_defaults_preserve_explicit_selections(monkeypatch):
    monkeypatch.setenv('RVBBIT_CLOVER_KEY','fixture')
    policy=_policy_module()
    payload={}
    assert policy.apply_managed_media_defaults(payload)
    assert payload['image_gen']['openai']['model']=='calliope-image'
    assert payload['stt']['openai']['key_env']=='RVBBIT_CLOVER_KEY'
    payload['image_gen']={'provider':'off'}
    payload['stt']={'provider':'local'}
    assert not policy.apply_managed_media_defaults(payload)
    assert payload['image_gen']=={'provider':'off'}
    assert payload['stt']=={'provider':'local'}
