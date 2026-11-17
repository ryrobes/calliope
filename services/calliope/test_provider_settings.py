from __future__ import annotations

import pytest
import json
import yaml

import provider_settings


def test_default_agent_candidate_edits_only_the_model_block():
    current = {
        "config_yaml": (
            "model:\n  default: calliope\n  provider: custom\n"
            "  base_url: ${env:RVBBIT_CLOVER_OPENAI_BASE_URL}\n  api_key: ${env:RVBBIT_CLOVER_KEY}\n"
            "  context_length: 262144\n  max_tokens: 8192\n"
            "platforms:\n  api_server:\n    enabled: true\n    extra:\n      keep_me: yes\n"
        ),
    }
    payload = yaml.safe_load(provider_settings._default_agent_candidate(current, "openrouter", "openai/gpt-5.6-sol"))
    assert payload["model"] == {
        "default": "openai/gpt-5.6-sol", "provider": "openrouter",
        "context_length": 262144, "max_tokens": 8192,          # budgets kept, Hutch bindings dropped
    }
    assert payload["platforms"]["api_server"]["extra"]["keep_me"] is True
    back = yaml.safe_load(provider_settings._default_agent_candidate(
        {"config_yaml": yaml.safe_dump(payload)}, "custom", "calliope"))
    assert back["model"]["provider"] == "custom" and back["model"]["default"] == "calliope"
    assert back["model"]["api_key"] == "${env:RVBBIT_CLOVER_KEY}"   # env reference, never a value


@pytest.mark.parametrize("agent", ["dashboard_studio", "dashboard studio", "../x", ""])
def test_agent_key_guard(monkeypatch, agent):
    with pytest.raises(ValueError, match="invalid agent key"):
        provider_settings.set_agent_model(lambda: None, agent, "clover", "calliope", actor="admin")


def _configured_conn(models=("openai/gpt-5.6-sol",), windows=None, outputs=None):
    windows = windows or {}
    outputs = outputs or {}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, params=()):
            self.sql, self.params = sql, params
            return self

        def fetchone(self):
            if "calliope_provider_state" in self.sql:
                return {"auth_state": "configured"}
            if "provider_models" in self.sql:
                return ({"context_window": windows.get(self.params[1]), "output_token_limit": outputs.get(self.params[1])}
                        if self.params[1] in models else None)
            return None

    return Connection


def test_default_agent_goes_through_reconcile_and_the_revision_controller(monkeypatch):
    seen = {}
    monkeypatch.setattr(provider_settings.hermes_config, "current", lambda _f: {
        "config_yaml": "model:\n  default: calliope\n  provider: custom\n",
        "secret_bindings": {"RVBBIT_CLOVER_KEY": "credential://clover"},
    })
    monkeypatch.setattr(provider_settings.hermes_profiles, "_reconcile_default_config",
                        lambda candidate: (candidate + "# reconciled\n", {"valid": True}))

    def queue_apply(_f, candidate, *, reason, actor, secret_bindings):
        seen.update(candidate=candidate, reason=reason, actor=actor, bindings=secret_bindings)
        return {"queued": True, "revision_id": "rev-1"}

    monkeypatch.setattr(provider_settings.hermes_config, "queue_apply", queue_apply)
    result = provider_settings.set_agent_model(_configured_conn(), "default", "openrouter", "openai/gpt-5.6-sol", actor="ryan")
    assert result == {"ok": True, "agent": "default", "provider": "openrouter", "model": "openai/gpt-5.6-sol",
                      "context_length": None, "max_tokens": None, "revision_id": "rev-1", "applied": "revision-controller"}
    assert seen["candidate"].endswith("# reconciled\n")           # hosted invariants reconciled before validation
    assert yaml.safe_load(seen["candidate"])["model"] == {"default": "openai/gpt-5.6-sol", "provider": "openrouter"}
    assert seen["bindings"] == {"RVBBIT_CLOVER_KEY": "credential://clover"}
    assert seen["reason"] == "point default at openrouter/openai/gpt-5.6-sol"


def test_context_budget_follows_the_models_window(monkeypatch):
    """A 200k-window model must not inherit the 262k appliance budget (Hermes'
    compaction threshold is a ratio of it and would sit past the provider's
    hard limit); a 1M-window model is capped at the appliance ceiling; an
    unknown window leaves the agent's budget alone."""
    monkeypatch.setattr(provider_settings.hermes_config, "current", lambda _f: {
        "config_yaml": "model:\n  default: calliope\n  provider: custom\n  context_length: 262144\n",
        "secret_bindings": {},
    })
    monkeypatch.setattr(provider_settings.hermes_profiles, "_reconcile_default_config", lambda c: (c, {"valid": True}))
    seen = {}
    monkeypatch.setattr(provider_settings.hermes_config, "queue_apply",
                        lambda _f, candidate, **k: seen.update(candidate=candidate, reason=k.get("reason")) or {"queued": True, "revision_id": "r"})
    windows = {"small/200k": 200_000, "google/gemini-flash": 1_048_576, "tiny/8k": 8_192}
    outputs = {"small/200k": 16_384, "google/gemini-flash": 65_536}
    conn = _configured_conn(tuple(windows), windows, outputs)
    provider_settings.set_agent_model(conn, "default", "openrouter", "small/200k", actor="ryan")
    assert yaml.safe_load(seen["candidate"])["model"]["context_length"] == 200_000
    assert yaml.safe_load(seen["candidate"])["model"]["max_tokens"] == 16_384        # model's own output limit
    assert "(context 200000, output 16384)" in seen["reason"]
    provider_settings.set_agent_model(conn, "default", "openrouter", "google/gemini-flash", actor="ryan")
    assert yaml.safe_load(seen["candidate"])["model"]["context_length"] == 262_144
    assert yaml.safe_load(seen["candidate"])["model"]["max_tokens"] == 32_768         # appliance output ceiling
    provider_settings.set_agent_model(conn, "default", "openrouter", "tiny/8k", actor="ryan")
    assert yaml.safe_load(seen["candidate"])["model"]["context_length"] == 262_144   # below floor: unknown → untouched
    # specialists get the same number through the supervisor
    posted = {}
    monkeypatch.setattr(provider_settings.hermes_profiles, "_control",
                        lambda m, p, payload=None, **k: posted.update(payload) or {"ok": True, "changed": True})
    provider_settings.set_agent_model(conn, "signal-watch", "openrouter", "small/200k", actor="ryan")
    assert posted["context_length"] == 200_000 and posted["max_tokens"] == 16_384


def test_specialist_agent_is_written_by_the_supervisor(monkeypatch):
    seen = {}

    def control(method, path, payload=None, *, timeout):
        seen.update(method=method, path=path, payload=payload, timeout=timeout)
        return {"ok": True, "changed": True, "config_hash": "abc"}

    monkeypatch.setattr(provider_settings.hermes_profiles, "_control", control)
    result = provider_settings.set_agent_model(_configured_conn(("calliope",)), "dashboard-studio", "clover", "calliope", actor="ryan")
    assert result["applied"] == "profile-config" and result["changed"] is True
    # "clover" is the UI's name for Hermes' custom provider bound to the Hutch endpoint
    assert seen == {"method": "POST", "path": "/v1/profile/model",
                    "payload": {"profile": "dashboard-studio", "provider": "custom", "model": "calliope"}, "timeout": 240}


def test_specialist_rejection_carries_the_validation_detail(monkeypatch):
    monkeypatch.setattr(provider_settings.hermes_profiles, "_control", lambda *a, **k: {
        "ok": False, "phase": "validation",
        "validation": {"valid": False, "errors": [{"path": "model.provider", "message": "provider must be one of the hosted Calliope providers"}]},
    })
    with pytest.raises(provider_settings.ProviderControlError) as failure:
        provider_settings.set_agent_model(_configured_conn(("calliope",)), "signal-watch", "clover", "calliope", actor="ryan")
    assert failure.value.status == 422
    assert "model.provider: provider must be one of the hosted Calliope providers" in str(failure.value)


def test_agents_merge_live_profiles_with_what_they_actually_called(monkeypatch):
    monkeypatch.setattr(provider_settings.hermes_profiles, "profiles", lambda: {"profiles": [
        {"key": "default", "name": "Calliope", "role_label": "Warehouse muse", "config_hash": "h1",
         "model": {"provider": "custom", "model": "calliope", "context_length": 262144, "max_tokens": 8192, "operator_managed": False, "errors": []}},
        {"key": "dashboard-studio", "name": "Iris", "role_label": "Dashboard Studio", "avatar_day_file": "iris.png",
         "model": {"provider": "openrouter", "model": "openai/gpt-5.6-sol", "operator_managed": True, "errors": []}},
    ]})
    monkeypatch.setattr(provider_settings, "_agent_calls", lambda _f: {
        "default": {"calls_7d": 12, "tokens_7d": 900, "last": {"provider": "clover", "model": "calliope", "at": "2026-08-17T00:00:00Z"}},
    })
    monkeypatch.setattr(provider_settings, "_default_agent_pending", lambda _f: {
        "revision_id": "rev-9", "state": "validated", "provider": "openrouter", "model": "openai/gpt-5.6-sol",
    })
    rows = provider_settings.agents(lambda: None)
    assert rows[0]["pending"]["state"] == "validated" and rows[1]["pending"] is None
    assert [row["key"] for row in rows] == ["default", "dashboard-studio"]
    assert rows[0]["provider"] == "clover" and rows[0]["model"] == "calliope"     # custom -> clover for the UI
    assert rows[0]["usage"]["last"]["provider"] == "clover"
    assert rows[1]["provider"] == "openrouter" and rows[1]["operator_managed"] is True and rows[1]["usage"] is None
    assert rows[1]["avatar_day_file"] == "iris.png"


def test_disconnect_never_removes_last_provider(monkeypatch):
    monkeypatch.setattr(provider_settings, "agents", lambda *_args: [])
    monkeypatch.setattr(provider_settings, "_configured_other_than", lambda *_args: 0)
    with pytest.raises(ValueError, match="at least one"):
        provider_settings.remove_provider(
            lambda: None, "openrouter", actor="admin@example.test"
        )


def test_oauth_cancel_uses_private_supervisor_without_tokens(monkeypatch):
    captured = {}

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

    def control(method, path, payload=None, *, timeout):
        captured.update(method=method, path=path, payload=payload, timeout=timeout)
        return {"ok": True, "status": "cancelled"}

    monkeypatch.setattr(provider_settings, "_control", control)
    result = provider_settings.cancel_oauth(
        lambda: Connection(), "openai-codex", "session-123", actor="admin@example.test"
    )
    assert result["status"] == "cancelled"
    assert captured == {
        "method": "DELETE",
        "path": "/v1/providers/oauth/session?provider=openai-codex&session_id=session-123",
        "payload": None,
        "timeout": 20,
        "sql": (
            "UPDATE rvbbit.calliope_provider_state SET auth_state='missing',status='unknown',"
            "last_error=NULL,updated_at=clock_timestamp(),updated_by=%s "
            "WHERE provider=%s AND auth_state='pending'"
        ),
        "params": ("admin@example.test", "openai-codex"),
    }


def test_provider_control_migration_keeps_secrets_out_of_state_tables():
    sql = provider_settings._MIGRATION.read_text(encoding="utf-8")
    assert "calliope_provider_state" in sql
    assert "calliope_provider_usage_daily" in sql
    assert "REVOKE ALL ON rvbbit.calliope_provider_state FROM PUBLIC" in sql
    assert "credential_ref text" in sql
    assert "access_token text" not in sql
    assert "refresh_token text" not in sql
    assert "THEN 'clover'" in sql


def test_rejected_api_key_rolls_back_canonical_rotation(monkeypatch):
    events = []

    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Transaction:
        def __enter__(self):
            events.append("transaction-enter")

        def __exit__(self, exc_type, _exc, _traceback):
            events.append(("transaction-exit", exc_type))

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def transaction(self):
            return Transaction()

        def execute(self, sql, _params=()):
            events.append(sql)
            if "put_credential" in sql:
                return Result({"ref": "api_key/calliope-provider/openrouter"})
            if "resolve_credential" in sql:
                return Result({"value": "rejected-key"})
            raise AssertionError(f"unexpected SQL after rejected key: {sql}")

    def reject(*_args, **_kwargs):
        raise provider_settings.ProviderControlError("provider rejected the API key")

    monkeypatch.setattr(provider_settings, "_control", reject)
    with pytest.raises(provider_settings.ProviderControlError, match="rejected"):
        provider_settings.set_api_key(
            lambda: Connection(), "openrouter", "rejected-key", actor="admin@example.test"
        )

    assert not any("delete_credential" in item for item in events if isinstance(item, str))
    assert events[-1] == ("transaction-exit", provider_settings.ProviderControlError)


def test_clover_can_use_settings_key_when_not_environment_managed():
    assert "clover" in provider_settings._API_KEY_PROVIDERS


def test_hermes_capability_flags_become_tag_arrays():
    """provider_models.capabilities is an array everywhere else
    (register_self_hosted_model rejects non-arrays); Hermes reports flags."""
    assert provider_settings._capability_tags({"reasoning": True, "fast": False}) == ["reasoning"]
    assert provider_settings._capability_tags(
        {"supported_parameters": ["tools", "temperature"], "input_modalities": ["image"], "vision": True}
    ) == ["input_modalities:image", "temperature", "tools", "vision"]
    assert provider_settings._capability_tags({}) == []


def test_sync_records_where_a_configured_api_key_lives(monkeypatch):
    hermes_held = provider_settings._provider_metadata(
        {"id": "openrouter", "configured": True, "auth_kind": "api_key", "oauth": {}}
    )
    assert hermes_held["credential_holder"] == "hermes"
    env_held = provider_settings._provider_metadata(
        {"id": "clover", "configured": True, "auth_kind": "api_key", "environment_managed": True}
    )
    assert env_held["credential_holder"] == "environment"
    assert "credential_holder" not in provider_settings._provider_metadata(
        {"id": "openrouter", "configured": False, "auth_kind": "api_key"}
    )
    assert "credential_holder" not in provider_settings._provider_metadata(
        {"id": "nous", "configured": True, "auth_kind": "oauth"}
    )


def test_startup_applies_the_relaxed_credential_check_after_0321():
    names = [path.name for path in provider_settings._MIGRATIONS]
    assert names == [
        "0321_calliope_provider_control.sql",
        "0324_calliope_provider_state_hermes_holder.sql",
    ]
    for path in provider_settings._MIGRATIONS:
        assert path.exists(), path
    relaxed = provider_settings._MIGRATIONS[1].read_text(encoding="utf-8")
    assert "DROP CONSTRAINT IF EXISTS calliope_provider_state_credential_check" in relaxed
    assert "metadata->>'credential_holder' = 'hermes'" in relaxed


def test_inventory_sync_never_takes_over_rows_other_writers_own(monkeypatch):
    """OpenRouter catalog rows ('openrouter_api'), managed Clover routes
    ('capability'), and self-hosted registrations keep their source, richer
    capabilities, and availability; Hermes only fills gaps and annotates raw."""
    statements = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def transaction(self):
            return self

        def execute(self, sql, params=()):
            statements.append((sql, params))
            return self

        def fetchone(self):
            return None

    inventory = {"providers": [{
        "id": "openrouter", "name": "OpenRouter", "auth_kind": "api_key", "configured": True,
        "models": ["anthropic/claude-fable-5"],
        "capabilities": {"anthropic/claude-fable-5": {"reasoning": True, "context_window": 1000000}},
    }]}
    provider_settings._sync_inventory(lambda: Connection(), inventory)
    upserts = [sql for sql, _ in statements if "INSERT INTO rvbbit.provider_models" in sql]
    assert len(upserts) == 1
    sql = upserts[0]
    import re
    assert not re.search(r"[,\s]source\s*=", sql.split("ON CONFLICT", 1)[1])   # never rewrites ownership
    assert "CASE WHEN pm.source='hermes' THEN EXCLUDED.capabilities ELSE pm.capabilities END" in sql
    assert "CASE WHEN pm.source='hermes' THEN EXCLUDED.available ELSE pm.available END" in sql
    assert "jsonb_build_object('hermes',EXCLUDED.raw)" in sql
    params = next(p for s, p in statements if "INSERT INTO rvbbit.provider_models" in s)
    model = json.loads(params[2])[0]
    assert model["capabilities"] == ["reasoning"]  # tags, not the flag dict
    assert model["context_window"] == 1000000
    # The state upsert keeps a Calliope-held credential's holder through the sync.
    state_sql = next(s for s, _ in statements if "INSERT INTO rvbbit.calliope_provider_state" in s)
    assert "credential_ref IS NOT NULL" in state_sql and '"credential_holder":"calliope"' in state_sql
    # And the hermes-only delete stays scoped to rows the sync inserted.
    delete_sql = next(s for s, _ in statements if "DELETE FROM rvbbit.provider_models" in s)
    assert "source='hermes'" in delete_sql


def test_disconnect_refuses_while_an_agent_still_uses_the_provider(monkeypatch):
    monkeypatch.setattr(provider_settings, "agents", lambda *_args: [
        {"key": "default", "name": "Calliope", "provider": "clover"},
        {"key": "signal-watch", "name": "Sentinel", "provider": "openrouter"},
    ])
    monkeypatch.setattr(provider_settings, "_configured_other_than", lambda *_args: 3)
    with pytest.raises(ValueError, match="point these agents at another provider before disconnecting openrouter: Sentinel"):
        provider_settings.remove_provider(lambda: None, "openrouter", actor="admin@example.test")


def test_rejection_says_why():
    """A bare 'Hermes rejected …' hid the actual invariant that failed; the
    operator has to see the path and the expected/active pair."""
    import provider_settings as module

    queued = {"queued": False, "validation": {"valid": False, "errors": [
        {"path": "model.context_length", "message": "hosted Calliope invariant would change", "expected": "262144", "actual": "131072"},
        {"path": "mcp_servers.Warehouse.tools", "message": "hosted Calliope invariant would change", "expected": "{...}", "actual": "{...}"},
        {"path": "a", "message": "b"}, {"path": "c", "message": "d"},
    ]}}
    message = module._rejection_message(queued, "Hermes rejected the model choice")
    assert message.startswith("Hermes rejected the model choice — model.context_length: hosted Calliope invariant would change (expected 262144, active revision has 131072)")
    assert "(+1 more)" in message
    assert module._rejection_message({"queued": False}, "Hermes rejected the model choice") == "Hermes rejected the model choice"
    assert module._rejection_message({"ok": False, "error": "gateway exited"}, "fallback") == "gateway exited"


def test_reasoning_effort_rides_the_model_choice_everywhere(monkeypatch):
    """agent.reasoning_effort per agent: validated against Hermes' stable
    scale, written into the default candidate through the revision controller,
    posted to the supervisor for specialists, and '' clears back to auto."""
    seen = {}
    monkeypatch.setattr(provider_settings.hermes_config, "current", lambda _f: {
        "config_yaml": "model:\n  default: calliope\n  provider: custom\nagent:\n  reasoning_effort: high\n",
        "secret_bindings": {},
    })
    monkeypatch.setattr(provider_settings.hermes_profiles, "_reconcile_default_config", lambda c: (c, {"valid": True}))
    monkeypatch.setattr(provider_settings.hermes_config, "queue_apply",
                        lambda _f, candidate, **k: seen.update(candidate=candidate, reason=k.get("reason")) or {"queued": True, "revision_id": "r"})
    conn = _configured_conn(("openai/gpt-5.6-sol",))
    result = provider_settings.set_agent_model(conn, "default", "openrouter", "openai/gpt-5.6-sol",
                                               actor="ryan", reasoning_effort="xhigh")
    assert result["reasoning_effort"] == "xhigh"
    assert yaml.safe_load(seen["candidate"])["agent"] == {"reasoning_effort": "xhigh"}
    assert "effort xhigh" in seen["reason"]
    # '' clears the override and drops an otherwise-empty agent block.
    provider_settings.set_agent_model(conn, "default", "openrouter", "openai/gpt-5.6-sol",
                                      actor="ryan", reasoning_effort="")
    assert "agent" not in yaml.safe_load(seen["candidate"])
    # None leaves the current setting alone.
    provider_settings.set_agent_model(conn, "default", "openrouter", "openai/gpt-5.6-sol", actor="ryan")
    assert yaml.safe_load(seen["candidate"])["agent"] == {"reasoning_effort": "high"}
    # specialists: the supervisor payload carries it; None omits it.
    posted = []
    monkeypatch.setattr(provider_settings.hermes_profiles, "_control",
                        lambda m, p, payload=None, **k: posted.append(payload) or {"ok": True, "changed": True})
    provider_settings.set_agent_model(conn, "signal-watch", "openrouter", "openai/gpt-5.6-sol",
                                      actor="ryan", reasoning_effort="low")
    assert posted[-1]["reasoning_effort"] == "low"
    provider_settings.set_agent_model(conn, "signal-watch", "openrouter", "openai/gpt-5.6-sol", actor="ryan")
    assert "reasoning_effort" not in posted[-1]
    with pytest.raises(ValueError):
        provider_settings.set_agent_model(conn, "signal-watch", "openrouter", "openai/gpt-5.6-sol",
                                          actor="ryan", reasoning_effort="galactic")


def test_set_agent_models_batches_specialists_and_queues_the_default_once(monkeypatch):
    """A loadout-style apply: every specialist change goes to the supervisor's
    batch endpoint (one verified restart), the default rides the revision
    controller, and everything validates BEFORE anything is applied."""
    control_calls = []

    def control(method, path, payload, timeout=0):
        control_calls.append((method, path, payload, timeout))
        return {"ok": True, "restarted": True,
                "results": [{"profile": item["profile"], "changed": True} for item in payload["changes"]]}

    monkeypatch.setattr(provider_settings.hermes_profiles, "_control", control)
    monkeypatch.setattr(provider_settings.hermes_config, "current", lambda _f: {
        "config_yaml": "model:\n  default: calliope\n  provider: custom\n", "secret_bindings": {},
    })
    monkeypatch.setattr(provider_settings.hermes_profiles, "_reconcile_default_config", lambda c: (c, {"valid": True}))
    queued = {}
    monkeypatch.setattr(
        provider_settings.hermes_config,
        "queue_apply",
        lambda _f, candidate, **k: queued.update(candidate=candidate, reason=k.get("reason"))
        or {"queued": True, "revision_id": "rev-9"},
    )

    conn = _configured_conn((
        "google/gemini-3.7-flash", "openai/gpt-5.6-sol", "openai/gpt-5.6-terra", "a/b", "c/d",
    ))
    result = provider_settings.set_agent_models(conn, [
        {"agent": "dashboard-studio", "provider": "openrouter", "model": "google/gemini-3.7-flash"},
        {"agent": "analytical-skeptic", "provider": "openrouter", "model": "openai/gpt-5.6-sol"},
        {"agent": "default", "provider": "openrouter", "model": "openai/gpt-5.6-terra"},
    ], actor="ryan", auxiliary={
        "provider": "openrouter", "model": "openai/gpt-5.6-sol",
    })
    assert result["ok"] is True and result["applied"] == 3
    assert result["restarts"] == 2      # one supervisor restart + the controller's own verified apply
    assert result["default"]["applied"] == "revision-controller"
    assert len(control_calls) == 1      # ONE batch call for both specialists
    method, path, payload, timeout = control_calls[0]
    assert (method, path) == ("POST", "/v1/profile/models")
    assert [item["profile"] for item in payload["changes"]] == ["dashboard-studio", "analytical-skeptic"]
    assert payload["auxiliary"] == {"provider": "openrouter", "model": "openai/gpt-5.6-sol"}
    assert yaml.safe_load(queued["candidate"])["auxiliary"]["compression"]["fallback_chain"][0] == {
        "provider": "openrouter", "model": "openai/gpt-5.6-sol", "calliope_managed": True,
    }
    assert timeout >= 240

    # An invalid row fails the WHOLE batch before anything is applied.
    control_calls.clear()
    with pytest.raises(ValueError):        # duplicate agent
        provider_settings.set_agent_models(conn, [
            {"agent": "dashboard-studio", "provider": "openrouter", "model": "a/b"},
            {"agent": "dashboard-studio", "provider": "openrouter", "model": "c/d"},
        ], actor="ryan")
    assert not control_calls
    with pytest.raises(ValueError):        # unknown model fails the WHOLE batch up front
        provider_settings.set_agent_models(conn, [
            {"agent": "dashboard-studio", "provider": "openrouter", "model": "a/b"},
            {"agent": "analytical-skeptic", "provider": "openrouter", "model": "not/in-catalog"},
        ], actor="ryan")
    assert not control_calls


def test_fallback_model_applies_to_specialists_and_the_default_controller(monkeypatch):
    control_calls = []
    monkeypatch.setattr(provider_settings.hermes_profiles, "_control",
                        lambda m, p, body, timeout=0: control_calls.append((m, p, body)) or {"ok": True, "restarted": True, "results": []})
    monkeypatch.setattr(provider_settings.hermes_config, "current", lambda _f: {
        "config_yaml": "model:\n  default: calliope\n  provider: custom\n", "secret_bindings": {},
    })
    monkeypatch.setattr(provider_settings.hermes_profiles, "_reconcile_default_config", lambda c: (c, {"valid": True}))
    queued = {}
    monkeypatch.setattr(provider_settings.hermes_config, "queue_apply",
                        lambda _f, candidate, **k: queued.update(candidate=candidate, reason=k.get("reason")) or {"queued": True, "revision_id": "rev-2"})

    result = provider_settings.set_fallback_model(_configured_conn(), "openrouter", "openai/gpt-5.6-sol", actor="ryan")
    assert result["ok"] is True and result["restarts"] == 2
    assert control_calls[0][1] == "/v1/profile/fallback"
    assert control_calls[0][2] == {"provider": "openrouter", "model": "openai/gpt-5.6-sol"}
    assert yaml.safe_load(queued["candidate"])["fallback_model"] == {"provider": "openrouter", "model": "openai/gpt-5.6-sol"}
    assert "fallback" in queued["reason"]

    # clearing removes the key everywhere and validates nothing
    control_calls.clear()
    monkeypatch.setattr(provider_settings.hermes_config, "current", lambda _f: {
        "config_yaml": "model:\n  default: calliope\n  provider: custom\nfallback_model:\n  provider: openrouter\n  model: m\n",
        "secret_bindings": {},
    })
    cleared = provider_settings.set_fallback_model(_configured_conn(), "", "", actor="ryan")
    assert cleared["fallback"] is None
    assert control_calls[0][2] == {"provider": None, "model": None}
    assert "fallback_model" not in yaml.safe_load(queued["candidate"])

    with pytest.raises(ValueError):   # the appliance cannot be its own fallback
        provider_settings.set_fallback_model(_configured_conn(), "clover", "calliope", actor="ryan")
    # current_fallback maps the hermes provider id back to ours
    assert provider_settings.current_fallback(_configured_conn()) == {"provider": "openrouter", "model": "m"}


def test_auxiliary_lane_applies_to_specialists_and_the_default_controller(monkeypatch):
    """The background-task (auxiliary) lane is independent of fallback_model:
    every task uses its agent model first and then the provider/model selected
    from Calliope's full configured catalog."""
    control_calls = []
    monkeypatch.setattr(provider_settings.hermes_profiles, "_control",
                        lambda m, p, body, timeout=0: control_calls.append((m, p, body)) or {"ok": True, "restarted": True, "results": []})
    monkeypatch.setattr(provider_settings.hermes_config, "current", lambda _f: {
        "config_yaml": "model:\n  default: calliope\n  provider: custom\nauxiliary:\n  vision:\n    provider: auto\n    fallback_chain:\n    - provider: nous\n      model: Hermes-4-405B\n",
        "secret_bindings": {},
    })
    monkeypatch.setattr(provider_settings.hermes_profiles, "_reconcile_default_config", lambda c: (c, {"valid": True}))
    queued = {}
    monkeypatch.setattr(provider_settings.hermes_config, "queue_apply",
                        lambda _f, candidate, **k: queued.update(candidate=candidate, reason=k.get("reason")) or {"queued": True, "revision_id": "rev-3"})

    result = provider_settings.set_auxiliary_model(
        _configured_conn(), "openrouter", "openai/gpt-5.6-sol", actor="ryan",
    )
    assert result["ok"] is True and result["restarts"] == 2
    assert result["auxiliary"] == {"provider": "openrouter", "model": "openai/gpt-5.6-sol"}
    assert control_calls[0][1] == "/v1/profile/models"
    assert control_calls[0][2] == {
        "changes": [],
        "auxiliary": {"provider": "openrouter", "model": "openai/gpt-5.6-sol"},
    }
    aux = yaml.safe_load(queued["candidate"])["auxiliary"]
    assert aux["compression"]["fallback_chain"][0] == {
        "provider": "openrouter", "model": "openai/gpt-5.6-sol", "calliope_managed": True,
    }
    assert aux["vision"]["provider"] == "auto"
    assert aux["vision"]["fallback_chain"][1] == {"provider": "nous", "model": "Hermes-4-405B"}
    assert "auxiliary" in queued["reason"]

    # The provider/model pair must come from the configured full catalog.
    with pytest.raises(ValueError):
        provider_settings.set_auxiliary_model(
            _configured_conn(), "anthropic", "not/in-catalog", actor="ryan",
        )

    # Clearing removes only Calliope's managed entries; manual task policy survives.
    control_calls.clear()
    monkeypatch.setattr(provider_settings.hermes_config, "current", lambda _f: {
        "config_yaml": "model:\n  default: calliope\n  provider: custom\n"
                       "auxiliary:\n  vision:\n    provider: auto\n    fallback_chain:\n"
                       "    - provider: openrouter\n      model: m\n      calliope_managed: true\n"
                       "    - provider: nous\n      model: Hermes-4-405B\n",
        "secret_bindings": {},
    })
    cleared = provider_settings.set_auxiliary_model(_configured_conn(), "", "", actor="ryan")
    assert cleared["auxiliary"] is None
    assert control_calls[0][2] == {
        "changes": [], "auxiliary": {"provider": None, "model": None},
    }
    aux = yaml.safe_load(queued["candidate"])["auxiliary"]
    assert aux["vision"] == {
        "provider": "auto", "fallback_chain": [{"provider": "nous", "model": "Hermes-4-405B"}],
    }

    # current_auxiliary maps Hermes' provider id back to Calliope's label.
    monkeypatch.setattr(provider_settings.hermes_config, "current", lambda _f: {
        "config_yaml": "auxiliary:\n  compression:\n    fallback_chain:\n"
                       "    - provider: custom\n      model: calliope\n      calliope_managed: true\n",
        "secret_bindings": {},
    })
    assert provider_settings.current_auxiliary(_configured_conn()) == {"provider": "clover", "model": "calliope"}
    # Legacy OpenRouter settings stay legible until the operator saves/clears.
    monkeypatch.setattr(provider_settings.hermes_config, "current", lambda _f: {
        "config_yaml": "auxiliary:\n  openrouter_model: m\n  free_only: true\n", "secret_bindings": {},
    })
    assert provider_settings.current_auxiliary(_configured_conn()) == {
        "provider": "openrouter", "model": "m", "legacy": True, "free_only": True,
    }
    monkeypatch.setattr(provider_settings.hermes_config, "current", lambda _f: {
        "config_yaml": "auxiliary:\n  vision:\n    provider: openrouter\n", "secret_bindings": {},
    })
    assert provider_settings.current_auxiliary(_configured_conn()) is None


def test_auxiliary_lane_can_target_clover_without_exposing_credentials():
    payload = {"model": {"default": "x", "provider": "openrouter"}}
    provider_settings._apply_auxiliary_lane(payload, "custom", "calliope")
    entry = payload["auxiliary"]["title_generation"]["fallback_chain"][0]
    assert entry == {
        "provider": "custom", "model": "calliope", "calliope_managed": True,
        "base_url": "${env:RVBBIT_CLOVER_OPENAI_BASE_URL}",
        "api_key": "${env:RVBBIT_CLOVER_KEY}",
    }


def test_explicit_automatic_background_routing_survives_reconciliation():
    import runpy
    from pathlib import Path
    policy = runpy.run_path(str(Path(__file__).parents[2] / "docker/calliope/reconcile-hermes-config.py"))
    payload = {}
    policy["apply_managed_auxiliary_defaults"](payload)
    provider_settings._apply_auxiliary_lane(payload, "", "")
    assert payload["auxiliary"] == {}
    policy["reconcile_payload"](payload)
    assert payload["auxiliary"] == {}


def test_loadouts_snapshot_and_apply_only_the_diff(monkeypatch):
    saved = {}

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def execute(self, sql, params=()):
            self.sql, self.params = sql, params
            return self
        def fetchone(self):
            if "INSERT INTO rvbbit.calliope_model_loadouts" in self.sql:
                saved.update(name=self.params[0], config=json.loads(self.params[2]))
                return {"id": "11111111-1111-4111-8111-111111111111"}
            if "FROM rvbbit.calliope_model_loadouts WHERE id" in self.sql:
                return {"id": self.params[0], "name": saved["name"], "config": saved["config"]}
            if "DELETE FROM rvbbit.calliope_model_loadouts" in self.sql:
                return {"name": saved["name"]}
            return None
        def fetchall(self): return []

    agents_now = [
        {"key": "default", "provider": "openrouter", "model": "openai/gpt-5.6-sol"},
        {"key": "dashboard-studio", "provider": "openrouter", "model": "google/gemini-3.7-flash"},
        {"key": "broken", "error": "unreadable"},
    ]
    monkeypatch.setattr(provider_settings, "agents", lambda _f: agents_now)
    monkeypatch.setattr(provider_settings, "current_fallback", lambda _f: None)
    result = provider_settings.save_loadout(lambda: Conn(), "flash reviewers", actor="ryan")
    assert result["ok"] is True and result["agents"] == 2
    # The snapshot names each agent's reasoning effort too ("" = auto), so a
    # loadout restores the whole thinking posture, not just the models.
    assert saved["config"]["agents"] == [
        {"agent": "default", "provider": "openrouter", "model": "openai/gpt-5.6-sol", "reasoning_effort": ""},
        {"agent": "dashboard-studio", "provider": "openrouter", "model": "google/gemini-3.7-flash", "reasoning_effort": ""},
    ]
    with pytest.raises(ValueError):
        provider_settings.save_loadout(lambda: Conn(), "bad/name!", actor="ryan")

    # applying: only the drifted agent is sent through the batch path
    agents_now[1] = {"key": "dashboard-studio", "provider": "xai-oauth", "model": "grok-4.6"}
    batches = []
    monkeypatch.setattr(provider_settings, "set_agent_models",
                        lambda _f, changes, actor: batches.append(changes) or {"ok": True, "applied": len(changes), "restarts": 1, "results": []})
    applied = provider_settings.apply_loadout(lambda: Conn(), "11111111-1111-4111-8111-111111111111", actor="ryan")
    assert applied["applied"] == 1 and applied["name"] == "flash reviewers"
    assert batches[0] == [{"agent": "dashboard-studio", "provider": "openrouter", "model": "google/gemini-3.7-flash", "reasoning_effort": ""}]

    # nothing drifted → nothing applied, no restart
    agents_now[1] = {"key": "dashboard-studio", "provider": "openrouter", "model": "google/gemini-3.7-flash"}
    batches.clear()
    same = provider_settings.apply_loadout(lambda: Conn(), "11111111-1111-4111-8111-111111111111", actor="ryan")
    assert same["applied"] == 0 and not batches


def test_bulk_models_supports_full_fleet_and_preserves_unspecified_thinking(monkeypatch):
    calls = []
    monkeypatch.setattr(provider_settings.hermes_profiles, '_control', lambda *a, **k: calls.append(a[2]) or {'ok':True,'restarted':True,'results':[]})
    changes = [{'agent':f'expert-{i}','provider':'openrouter','model':'model-a'} for i in range(24)]
    result = provider_settings.set_agent_models(_configured_conn(('model-a',)), changes, actor='admin')
    assert result['ok'] and result['restarts'] == 1
    assert len(calls[0]['changes']) == 24
    assert all('reasoning_effort' not in change for change in calls[0]['changes'])
    assert 'auxiliary' not in calls[0]


def test_bulk_stale_selection_fails_before_any_apply(monkeypatch):
    monkeypatch.setattr(provider_settings.hermes_profiles, 'profiles', lambda: {'profiles':[{'key':'expert','config_hash':'new'}]})
    monkeypatch.setattr(provider_settings.hermes_profiles, '_control', lambda *_a, **_k: pytest.fail('must not apply a stale selection'))
    with pytest.raises(provider_settings.ProviderControlError) as failure:
        provider_settings.set_agent_models(_configured_conn(('model-a',)), [{'agent':'expert','provider':'openrouter','model':'model-a','expected_hash':'old'}],actor='admin')
    assert failure.value.status == 409


def test_claude_cli_login_rides_the_oauth_code_entry_surface(monkeypatch):
    """The Claude Pro / Max subscription is the DirectSDK plugin, signed in
    through the Claude Code CLI. It uses the same start / code / poll / remove
    surface as a Hermes OAuth grant: the state row keeps auth_kind='oauth'
    (the CHECK constraint and the provider card both know that word) while
    metadata.auth_kind says how the grant is obtained. The custom Anthropic
    PKCE path is gone."""
    provider = "claude-subscription-directsdk-experimental"
    assert provider in provider_settings._OAUTH_PROVIDERS
    assert provider in provider_settings._CODE_ENTRY_PROVIDERS
    assert provider not in provider_settings._API_KEY_PROVIDERS
    assert "anthropic" not in provider_settings._SUPPORTED_PROVIDERS
    assert "anthropic" not in provider_settings._PROVIDER_NAMES
    assert provider_settings._PROVIDER_NAMES[provider] == "Claude Pro / Max subscription (Claude Code)"
    assert provider_settings._PROVIDER_ORDER.index(provider) == provider_settings._PROVIDER_ORDER.index("nous") + 1

    executed = []
    control_calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, sql, params=()):
            executed.append((sql, params))
            return self

        def fetchone(self):
            return {"credential_ref": None, "environment_managed": False}

    def control(method, path, payload=None, *, timeout):
        control_calls.append((method, path, payload))
        return {"ok": True, "provider": provider, "session_id": "s1", "flow": "authorization_code",
                "awaiting_code": True, "status": "pending"}

    monkeypatch.setattr(provider_settings, "_control", control)

    started = provider_settings.start_oauth(lambda: Connection(), provider, actor="admin@example.test")
    assert started["awaiting_code"] is True
    assert control_calls[-1] == ("POST", "/v1/providers/oauth/start", {"provider": provider})
    sql, params = executed[-1]
    assert "auth_kind='oauth'" in sql and params[0] == provider
    assert json.loads(params[2]) == {"flow": "authorization_code", "auth_kind": "cli_login"}

    with pytest.raises(ValueError, match="paste the code Anthropic showed you"):
        provider_settings.submit_oauth_code(lambda: Connection(), provider, "s1", "  ", actor="admin@example.test")
    provider_settings.submit_oauth_code(lambda: Connection(), provider, "s1", "abc#123", actor="admin@example.test")
    assert control_calls[-1] == ("POST", "/v1/providers/oauth/code",
                                 {"provider": provider, "session_id": "s1", "code": "abc#123"})
    with pytest.raises(ValueError, match="does not take"):
        provider_settings.submit_oauth_code(lambda: Connection(), "openai-codex", "s1", "abc", actor="admin@example.test")
    with pytest.raises(ValueError):
        provider_settings.start_oauth(lambda: Connection(), "anthropic", actor="admin@example.test")

    # a native device-code grant still writes plain 'oauth' metadata
    provider_settings.start_oauth(lambda: Connection(), "nous", actor="admin@example.test")
    assert json.loads(executed[-1][1][2]) == {"flow": "device_code", "auth_kind": "oauth"}

    # disconnect goes through the supervisor's oauth route, which logs the CLI out
    monkeypatch.setattr(provider_settings, "agents", lambda _factory: [])
    monkeypatch.setattr(provider_settings, "_configured_other_than", lambda _factory, _provider: 1)
    provider_settings.remove_provider(lambda: Connection(), provider, actor="admin@example.test")
    assert control_calls[-1] == ("DELETE", "/v1/providers/oauth?provider=" + provider, None)
    assert not any("delete_credential" in sql for sql, _params in executed)

    # the inventory sync carries the login kind in metadata, column stays 'oauth'
    item = {
        "id": provider, "auth_kind": "oauth", "configured": True, "models": ["sonnet"],
        "oauth": {"configured": True, "auth_kind": "cli_login", "available": True,
                  "flow": "authorization_code", "source": "claude-code-cli",
                  "source_label": "Claude Max", "expires_at": None, "has_refresh_token": None, "error": None},
    }
    metadata = provider_settings._provider_metadata(item)
    assert metadata["auth_kind"] == "cli_login" and metadata["flow"] == "authorization_code"
    assert metadata["has_refresh_token"] is False and "available" not in metadata
    assert "credential_holder" not in metadata
    item["oauth"]["available"] = False
    assert provider_settings._provider_metadata(item)["available"] is False
    native = provider_settings._provider_metadata({
        "id": "nous", "auth_kind": "oauth", "configured": True, "models": ["m"],
        "oauth": {"flow": "device_code", "has_refresh_token": True},
    })
    assert native["auth_kind"] == "oauth" and native["has_refresh_token"] is True
    assert "auth_kind" not in provider_settings._provider_metadata({
        "id": "openrouter", "auth_kind": "api_key", "configured": True, "models": ["m"],
    })


def test_gemini_is_an_ordinary_api_key_provider():
    """Google's Gemini API (an AI Studio key) rides the same managed-key surface
    as OpenAI and xAI: pasted once, held in the Hermes credential pool, offered
    to every agent's model picker. Vertex stays out until it has its own
    service-account credential kind."""
    assert "gemini" in provider_settings._API_KEY_PROVIDERS
    assert "gemini" in provider_settings._SUPPORTED_PROVIDERS
    assert "gemini" not in provider_settings._OAUTH_PROVIDERS
    assert provider_settings._PROVIDER_NAMES["gemini"] == "Google Gemini API"
    assert provider_settings._PROVIDER_ORDER.index("gemini") == provider_settings._PROVIDER_ORDER.index("openai-api") + 1
    assert "vertex" not in provider_settings._SUPPORTED_PROVIDERS


def test_onboarding_openrouter_uses_the_instance_key_path(monkeypatch,tmp_path):
    import system_ai
    monkeypatch.setenv('CALLIOPE_OPENROUTER_KEY_FILE',str(tmp_path/'instance.key'))
    saved=[]
    monkeypatch.setattr(system_ai,'save_key',lambda token,**kw: saved.append((token,kw)) or {'configured':True,'hermes':{'ok':True}})
    factory=lambda: None
    result=provider_settings.set_api_key(factory,'openrouter','same-key',actor='admin')
    assert result['configured'] and saved==[('same-key',{'conn_factory':factory})]
    with pytest.raises(ValueError,match='cannot be disconnected'):
        provider_settings.remove_provider(factory,'openrouter',actor='admin')
    monkeypatch.setattr(system_ai,'save_key',lambda *a,**kw: {'hermes':{'ok':False},'message':'Saved, but Hermes synchronization is pending.'})
    with pytest.raises(provider_settings.ProviderControlError,match='synchronization is pending'):
        provider_settings.set_api_key(factory,'openrouter','same-key',actor='admin')
