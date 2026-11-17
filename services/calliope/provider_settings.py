"""Hosted provider credentials, model inventory, and per-agent model choice.

API-key values are encrypted through rvbbit.credentials before being resolved
for a private supervisor call. OAuth tokens never transit Calliope: Hermes'
native device flow and credential pool own them. Public/admin responses contain
only redacted state, model metadata, usage, and balance observations.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

import yaml

import hermes_config
import hermes_profiles


_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1] if len(_HERE.parents) > 1 else _HERE


def _migration_path(name: str) -> Path:
    shipped = _HERE / "sql" / name
    return shipped if shipped.exists() else _REPO / "crates/pg_rvbbit/sql/migrations" / name


# Applied in order at startup (idempotent; the extension chain applies the same
# files through rvbbit.migrate()). 0324 relaxes 0321's credential CHECK so the
# sync can mirror a key that lives only in Hermes' pool.
_MIGRATIONS = (
    _migration_path("0321_calliope_provider_control.sql"),
    _migration_path("0324_calliope_provider_state_hermes_holder.sql"),
)
_MIGRATION = _MIGRATIONS[0]
_CONTROL_URL = os.environ.get(
    "WAREHOUSE_HERMES_CONTROL_URL", "http://hermes:8643"
).rstrip("/")
_CONTROL_KEY = os.environ.get("HERMES_CONTROL_KEY") or os.environ.get(
    "WAREHOUSE_HERMES_API_KEY", ""
)
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_AGENT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# Hermes spells the appliance's Hutch endpoint `custom` (an OpenAI-compatible
# base_url + key bound through env references); the UI calls it "clover".
_HERMES_PROVIDER_IDS = {"clover": "custom"}
_CALLIOPE_PROVIDER_IDS = {hermes: ours for ours, hermes in _HERMES_PROVIDER_IDS.items()}
_CLOVER_MODEL_BINDINGS = {
    "base_url": "${env:RVBBIT_CLOVER_OPENAI_BASE_URL}",
    "api_key": "${env:RVBBIT_CLOVER_KEY}",
}
# Must mirror docker/calliope/reconcile-hermes-config.py. Hermes has no one
# global auxiliary provider; Calliope's appliance-level choice is represented
# as a marked first fallback on each built-in background task. MoA slots keep
# their preset-owned routing.
_CALLIOPE_AUXILIARY_TASKS = (
    "vision", "web_extract", "compression", "skills_hub", "approval", "mcp",
    "title_generation", "memory_query_rewrite", "tts_audio_tags",
    "triage_specifier", "kanban_decomposer", "profile_describer", "goal_judge",
    "curator", "monitor", "background_review",
)
_CALLIOPE_AUXILIARY_MARKER = "calliope_managed"
# Mirrors the hosted policy (docker/calliope/reconcile-hermes-config.py): the
# working budget Hermes' compaction ratios hang off is min(appliance ceiling,
# the model's advertised window). A model with a smaller window than the
# ceiling must not inherit the ceiling — its threshold would never be reached
# before the provider rejects the request.
_MODEL_CONTEXT_CEILING = int(os.environ.get("CALLIOPE_MODEL_CONTEXT_LENGTH") or 262_144)
_MODEL_CONTEXT_FLOOR = 32_768
# Per-response output cap: min(appliance ceiling, model's advertised output
# limit). A whole-dashboard write is 10-14k tokens; 8192 cut every one.
_MODEL_MAX_TOKENS_CEILING = int(os.environ.get("CALLIOPE_MODEL_MAX_TOKENS") or 32_768)
_MODEL_MAX_TOKENS_FLOOR = 4_096
_API_KEY_PROVIDERS = frozenset({"clover", "openrouter", "openai-api", "xai", "gemini"})
# The Claude Pro / Max subscription is NousResearch's DirectSDK plugin; its
# only login is the official Claude Code CLI, which the supervisor drives
# through the same code-entry surface as an OAuth grant (auth_kind stays
# 'oauth' for the UI and the state table; metadata.auth_kind says 'cli_login').
_CLI_LOGIN_PROVIDER = "claude-subscription-directsdk-experimental"
_CLI_LOGIN_PROVIDERS = frozenset({_CLI_LOGIN_PROVIDER})
_OAUTH_PROVIDERS = frozenset({"nous", "openai-codex", "xai-oauth", _CLI_LOGIN_PROVIDER})
# PKCE authorization-code logins: the human pastes the code Anthropic shows
# after signing in with the Claude Pro / Max subscription (see the supervisor).
_CODE_ENTRY_PROVIDERS = frozenset({_CLI_LOGIN_PROVIDER})
_SUPPORTED_PROVIDERS = frozenset({
    "clover", *_API_KEY_PROVIDERS, *_OAUTH_PROVIDERS,
})
_PROVIDER_ORDER = (
    "clover", "openai-codex", "nous", _CLI_LOGIN_PROVIDER, "xai-oauth", "openrouter", "openai-api", "gemini", "xai",
)
_PROVIDER_NAMES = {
    "clover": "Clover / Hutch",
    "openai-codex": "ChatGPT / Codex subscription",
    _CLI_LOGIN_PROVIDER: "Claude Pro / Max subscription (Claude Code)",
    "nous": "Nous Portal subscription",
    "xai-oauth": "xAI SuperGrok / Premium+",
    "openrouter": "OpenRouter",
    "openai-api": "OpenAI API",
    "gemini": "Google Gemini API",
    "xai": "xAI API",
}


class ProviderControlError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _control(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 30,
) -> dict[str, Any]:
    if not _CONTROL_KEY:
        raise ProviderControlError("Hermes provider control is not configured")
    raw = None if payload is None else _json(payload).encode("utf-8")
    request = urllib.request.Request(
        _CONTROL_URL + path,
        data=raw,
        method=method,
        headers={
            "Authorization": f"Bearer {_CONTROL_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            failure = json.loads(exc.read().decode("utf-8"))
        except Exception:
            failure = {}
        message = failure.get("error") if isinstance(failure, dict) else None
        raise ProviderControlError(
            str(message or f"Hermes provider control returned HTTP {exc.code}")[:800],
            400 if exc.code in {400, 404, 409, 422} else 502,
        ) from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        raise ProviderControlError("Hermes provider control is temporarily unavailable") from exc
    if not isinstance(result, dict):
        raise ProviderControlError("Hermes provider control returned an invalid response")
    return result


def ensure(conn_factory: Callable[..., Any]) -> None:
    with conn_factory() as conn:
        for path in _MIGRATIONS:
            conn.execute(path.read_text(encoding="utf-8"))


def _provider_metadata(item: dict[str, Any]) -> dict[str, Any]:
    oauth = item.get("oauth") if isinstance(item.get("oauth"), dict) else {}
    configured = bool(item.get("configured"))
    metadata: dict[str, Any] = {
        "source": "hermes",
        "flow": oauth.get("flow"),
        "has_refresh_token": bool(oauth.get("has_refresh_token")),
        "warning": str(item.get("warning") or "")[:400] or None,
    }
    if item.get("instance_shared"):
        metadata["instance_shared"] = True
    if str(item.get("auth_kind") or "api_key") == "oauth":
        # How the grant is obtained: Hermes' native device-code login, or the
        # Claude Code CLI login ('cli_login') the supervisor drives for the
        # DirectSDK plugin. The state table's auth_kind column stays 'oauth'.
        metadata["auth_kind"] = str(oauth.get("auth_kind") or "oauth")[:32]
        if oauth.get("available") is False:
            # The login binary is missing from the image; the card can say so.
            metadata["available"] = False
    if configured and str(item.get("auth_kind") or "api_key") == "api_key":
        # Where the secret lives. The upsert upgrades this to 'calliope' when
        # the existing row references a Calliope-owned credential.
        metadata["credential_holder"] = (
            "environment" if item.get("environment_managed") else "hermes"
        )
    bench = item.get("bench") if isinstance(item.get("bench"), dict) else None
    if bench and bench.get("all_benched"):
        # Every credential for this provider is benched after a 402/429; the
        # card explains it and offers a retry (clear_provider_bench).
        first = (bench.get("entries") or [{}])[0]
        metadata["bench"] = {
            "all_benched": True,
            "error_code": first.get("error_code"),
            "message": str(first.get("message") or "")[:240],
            "benched_until": first.get("benched_until"),
        }
    return metadata


def _capability_tags(caps: dict[str, Any]) -> list[str]:
    """rvbbit.provider_models.capabilities is an array of tags everywhere else
    (OpenRouter supported_parameters, register_self_hosted_model). Hermes
    reports per-model flags such as {"reasoning": true, "fast": false};
    keep the true flags as tags and any list-valued entries as their items."""
    tags: set[str] = set()
    for key, value in (caps or {}).items():
        name = str(key or "").strip()
        if not name:
            continue
        if value is True:
            tags.add(name)
        elif isinstance(value, list):
            for entry in value:
                text = str(entry or "").strip()
                if text and len(text) <= 64:
                    tags.add(text if name in {"supported_parameters", "capabilities", "tags"} else f"{name}:{text}")
    return sorted(tags)


def _sync_inventory(conn_factory: Callable[..., Any], inventory: dict[str, Any]) -> None:
    providers = inventory.get("providers")
    if inventory.get("ok") is False or not isinstance(providers, list) or not providers:
        raise ProviderControlError("Provider inventory was empty or invalid; the saved catalog was kept")
    known = []
    seen: set[str] = set()
    for item in providers:
        if not isinstance(item, dict):
            raise ProviderControlError("Provider inventory contained an invalid provider")
        provider = str(item.get("id") or "").strip().lower()
        if provider not in _SUPPORTED_PROVIDERS:
            continue
        if provider in seen or not isinstance(item.get("models"), list):
            raise ProviderControlError("Provider inventory contained duplicate providers or an invalid model list")
        if not isinstance(item.get("configured"), bool):
            raise ProviderControlError("Provider inventory was missing authentication state")
        # A failed fetch must not erase an already working provider's catalog.
        if item.get("configured") and (item.get("error") or not item["models"]):
            raise ProviderControlError("A configured provider returned no usable catalog; the saved catalog was kept")
        seen.add(provider)
        known.append(item)
    if not known:
        raise ProviderControlError("Provider inventory contained no supported providers")
    with conn_factory() as conn:
        with conn.transaction():
            conn.execute("SET LOCAL lock_timeout = '2s'")
            conn.execute("SET LOCAL statement_timeout = '15s'")
            for item in sorted(known, key=lambda row: row["id"]):
                provider = str(item["id"]).strip().lower()
                configured = bool(item.get("configured"))
                oauth = item.get("oauth") if isinstance(item.get("oauth"), dict) else {}
                error = str(oauth.get("error") or "")[:2000] or None
                auth_state = "configured" if configured else ("error" if error else "missing")
                status = "ready" if configured else ("error" if error else "unknown")
                auth_kind = str(item.get("auth_kind") or "api_key")
                conn.execute(
                    "INSERT INTO rvbbit.calliope_provider_state "
                    "(provider,display_name,auth_kind,auth_state,environment_managed,"
                    "credential_label,account_hint,status,last_error,metadata,tested_at,"
                    "models_synced_at,updated_at,updated_by) VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,clock_timestamp(),"
                    "clock_timestamp(),clock_timestamp(),'calliope-provider-sync') "
                    "ON CONFLICT (provider) DO UPDATE SET display_name=EXCLUDED.display_name,"
                    "auth_kind=EXCLUDED.auth_kind,auth_state=EXCLUDED.auth_state,"
                    "environment_managed=EXCLUDED.environment_managed,"
                    "account_hint=EXCLUDED.account_hint,status=EXCLUDED.status,"
                    "last_error=EXCLUDED.last_error,"
                    # A Calliope-installed key keeps its holder even though the
                    # sync rewrites the rest of the metadata document.
                    "metadata=CASE WHEN rvbbit.calliope_provider_state.credential_ref IS NOT NULL "
                    " AND EXCLUDED.auth_state='configured' AND EXCLUDED.auth_kind='api_key' "
                    " THEN EXCLUDED.metadata || '{\"credential_holder\":\"calliope\"}'::jsonb "
                    " ELSE EXCLUDED.metadata END,"
                    "tested_at=EXCLUDED.tested_at,models_synced_at=EXCLUDED.models_synced_at,"
                    "updated_at=clock_timestamp(),updated_by='calliope-provider-sync'",
                    (
                        provider,
                        str(item.get("name") or _PROVIDER_NAMES[provider])[:120],
                        auth_kind,
                        auth_state,
                        bool(item.get("environment_managed")),
                        "host environment" if item.get("environment_managed") and configured else None,
                        str(oauth.get("source_label") or "")[:240] or None,
                        status,
                        error,
                        _json(_provider_metadata(item)),
                    ),
                )
                credit = item.get("credit") if isinstance(item.get("credit"), dict) else None
                if credit:
                    numeric = lambda name: credit.get(name) if isinstance(credit.get(name), (int, float)) and not isinstance(credit.get(name), bool) else None
                    conn.execute(
                        "INSERT INTO rvbbit.calliope_provider_credit_snapshots "
                        "(provider,balance_kind,currency,credit_limit,credit_used,credit_remaining,raw) "
                        "SELECT %s,'credit','USD',%s,%s,%s,%s::jsonb WHERE NOT EXISTS ("
                        " SELECT 1 FROM rvbbit.calliope_provider_credit_snapshots prior "
                        " WHERE prior.provider=%s AND prior.observed_at > clock_timestamp()-interval '5 minutes' "
                        " AND prior.credit_limit IS NOT DISTINCT FROM %s "
                        " AND prior.credit_used IS NOT DISTINCT FROM %s "
                        " AND prior.credit_remaining IS NOT DISTINCT FROM %s)",
                        (
                            provider, numeric("limit"), numeric("usage"), numeric("remaining"),
                            _json({"source": "provider_api", "is_free_tier": bool(credit.get("is_free_tier"))}),
                            provider, numeric("limit"), numeric("usage"), numeric("remaining"),
                        ),
                    )
                models = []
                model_rows = []
                capabilities = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
                for raw_model in item.get("models") or []:
                    model = str(raw_model or "").strip()
                    if not isinstance(raw_model, str) or not model or len(model) > 256 or re.search(r"[\r\n\x00]", model):
                        raise ProviderControlError("Provider inventory contained an invalid model name")
                    models.append(model)
                    caps = capabilities.get(model) if isinstance(capabilities.get(model), dict) else {}
                    model_rows.append({
                        "model": model, "family": model.split("/", 1)[0],
                        "capabilities": _capability_tags(caps),
                        "context_window": caps.get("context_window"),
                        "output_token_limit": caps.get("max_output_tokens"),
                        "raw": {"source": "hermes", "configured": configured, "capabilities": caps},
                    })
                models = sorted(set(models))
                if model_rows:
                    # One set-based upsert per provider; keep other catalog writers' ownership.
                    model_rows = list({row["model"]: row for row in model_rows}.values())
                    conn.execute(
                        "INSERT INTO rvbbit.provider_models AS pm "
                        "(provider,model,display_name,family,capabilities,context_window,"
                        "output_token_limit,available,source,raw,fetched_at,updated_at) "
                        "SELECT %s,m.model,m.model,m.family,m.capabilities,m.context_window,"
                        "m.output_token_limit,%s,'hermes',m.raw,clock_timestamp(),clock_timestamp() "
                        "FROM jsonb_to_recordset(%s::jsonb) AS m(model text,family text,"
                        "capabilities jsonb,context_window bigint,output_token_limit bigint,raw jsonb) "
                        "ORDER BY m.model "
                        "ON CONFLICT (provider,model) DO UPDATE SET "
                        "display_name=CASE WHEN pm.source='hermes' THEN EXCLUDED.display_name ELSE pm.display_name END,"
                        "family=CASE WHEN pm.source='hermes' THEN EXCLUDED.family ELSE pm.family END,"
                        "capabilities=CASE WHEN pm.source='hermes' THEN EXCLUDED.capabilities ELSE pm.capabilities END,"
                        "context_window=CASE WHEN pm.source='hermes' THEN EXCLUDED.context_window "
                        " ELSE coalesce(pm.context_window,EXCLUDED.context_window) END,"
                        "output_token_limit=CASE WHEN pm.source='hermes' THEN EXCLUDED.output_token_limit "
                        " ELSE coalesce(pm.output_token_limit,EXCLUDED.output_token_limit) END,"
                        "available=CASE WHEN pm.source='hermes' THEN EXCLUDED.available ELSE pm.available END,"
                        "raw=CASE WHEN pm.source='hermes' THEN EXCLUDED.raw "
                        " ELSE coalesce(pm.raw,'{}'::jsonb) || jsonb_build_object('hermes',EXCLUDED.raw) END,"
                        "fetched_at=clock_timestamp(),updated_at=clock_timestamp()",
                        (provider, configured, _json(model_rows)),
                    )
                conn.execute(
                    "DELETE FROM rvbbit.provider_models WHERE provider=%s AND source='hermes' "
                    "AND NOT (model=ANY(%s::text[]))",
                    (provider, models),
                )
                conn.execute(
                    "INSERT INTO rvbbit.provider_catalog "
                    "(provider,auth_state,status,last_refresh,models_count,error,raw,updated_at) "
                    "VALUES (%s,%s,%s,clock_timestamp(),%s,%s,%s::jsonb,clock_timestamp()) "
                    "ON CONFLICT (provider) DO UPDATE SET auth_state=EXCLUDED.auth_state,"
                    "status=EXCLUDED.status,last_refresh=EXCLUDED.last_refresh,"
                    "models_count=EXCLUDED.models_count,error=EXCLUDED.error,raw=EXCLUDED.raw,"
                    "updated_at=clock_timestamp()",
                    (
                        provider, "configured" if configured else "missing",
                        "ok" if configured else "skipped", len(models), error,
                        _json({
                            "source": "hermes",
                            "auth_kind": auth_kind,
                            "login_kind": str(oauth.get("auth_kind") or auth_kind)[:32],
                        }),
                    ),
                )
            # An omitted provider is not a disconnect. Only an explicit
            # configured=false observation may replace its saved auth state.


def inventory_initialized(conn_factory: Callable[..., Any]) -> bool:
    with conn_factory() as conn:
        return bool(conn.execute(
            "SELECT EXISTS(SELECT 1 FROM rvbbit.calliope_provider_state "
            "WHERE models_synced_at IS NOT NULL) AS initialized"
        ).fetchone()["initialized"])


def refresh(
    conn_factory: Callable[..., Any], *, models: bool = False, only_if_empty: bool = False
) -> dict[str, Any]:
    # Session advisory lock serializes refreshes across workers without locking
    # catalog tables while Hermes/provider HTTP calls are in flight.
    with conn_factory() as guard:
        acquired = guard.execute(
            "SELECT pg_try_advisory_lock(741209, 1) AS acquired"
        ).fetchone()["acquired"]
        if not acquired:
            raise ProviderControlError("A provider refresh is already running. The saved catalog is still available.", 409)
        try:
            if only_if_empty and inventory_initialized(conn_factory):
                return {"ok": True, "skipped": True}
            inventory = _control(
                "GET",
                "/v1/providers?" + urllib.parse.urlencode({"refresh": "true" if models else "false"}),
                timeout=120 if models else 40,
            )
            _sync_inventory(conn_factory, inventory)
            return inventory
        finally:
            guard.execute("SELECT pg_advisory_unlock(741209, 1)")


def summary(conn_factory: Callable[..., Any]) -> dict[str, Any]:
    """Read saved inventory and current settings without refreshing providers."""
    import system_ai
    with conn_factory() as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            providers = conn.execute(
                "SELECT s.*,coalesce(m.model_count,0)::int AS model_count,c.credit_remaining,"
                "c.credit_used,c.credit_limit,c.currency,c.balance_kind,c.observed_at AS credit_observed_at "
                "FROM rvbbit.calliope_provider_state s LEFT JOIN LATERAL ("
                " SELECT count(*) AS model_count FROM rvbbit.provider_models pm "
                " WHERE pm.provider=s.provider AND pm.available) m ON true "
                "LEFT JOIN rvbbit.calliope_provider_credit_latest c USING (provider)"
            ).fetchall()
            models = conn.execute(
                "SELECT pm.provider,pm.model,pm.display_name,pm.family,pm.capabilities,"
                "pm.context_window,pm.output_token_limit,pm.updated_at "
                "FROM rvbbit.provider_models pm JOIN rvbbit.calliope_provider_state s "
                "ON s.provider=pm.provider AND s.auth_state='configured' "
                "WHERE pm.available ORDER BY pm.provider,coalesce(pm.display_name,pm.model),pm.model"
            ).fetchall()
            usage = conn.execute(
                "SELECT provider,model,sum(calls)::bigint AS calls,"
                "sum(tokens_in)::bigint AS tokens_in,sum(tokens_out)::bigint AS tokens_out,"
                "sum(reported_calls)::bigint AS reported_calls,"
                "sum(estimated_calls)::bigint AS estimated_calls,"
                "sum(uncosted_calls)::bigint AS uncosted_calls,"
                "sum(cost_usd)::numeric(24,9) AS cost_usd,max(last_call_at) AS last_call_at "
                "FROM rvbbit.calliope_provider_usage_daily "
                "WHERE usage_day >= current_date-29 GROUP BY provider,model "
                "ORDER BY max(last_call_at) DESC NULLS LAST"
            ).fetchall()
    provider_docs = [dict(row) for row in providers]
    provider_docs.sort(key=lambda row: _PROVIDER_ORDER.index(row["provider"]) if row["provider"] in _PROVIDER_ORDER else 99)
    for row in provider_docs:
        # Our names say what the credential IS (subscription grant vs API key);
        # Hermes' inventory names are bare vendor names.
        row["display_name"] = _PROVIDER_NAMES.get(row["provider"]) or row.get("display_name") or row["provider"]
        row["credential_ref"] = None  # never expose the internal secret reference in the UI
    return {
        "instance_openrouter_configured": system_ai.status()['configured'],
        "inventory_initialized": any(row.get("models_synced_at") is not None for row in provider_docs),
        "providers": provider_docs,
        "agents": agents(conn_factory),
        "fallback": current_fallback(conn_factory),
        "auxiliary": current_auxiliary(conn_factory),
        "loadouts": loadouts(conn_factory),
        "models": [dict(row) for row in models],
        "usage_30d": [dict(row) for row in usage],
        "configured_count": sum(1 for row in provider_docs if row.get("auth_state") == "configured"),
    }


def set_api_key(
    conn_factory: Callable[..., Any], provider: str, api_key: str, *, actor: str
) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()
    if provider not in _API_KEY_PROVIDERS:
        raise ValueError("provider does not accept an API key")
    token = str(api_key or "").strip()
    if not token or len(token.encode("utf-8")) > 64 * 1024:
        raise ValueError("API key is empty or too large")
    import system_ai
    if provider == "openrouter" and system_ai.key_path():
        # Onboarding and the legacy provider form use the same validated instance
        # key path as System, never a second independent Hermes credential.
        result = system_ai.save_key(token, conn_factory=conn_factory)
        if not result['hermes']['ok']:
            raise ProviderControlError(result['message'])
        return {**result, 'ok': True}
    with conn_factory() as conn:
        # put_credential rotates a deterministic reference. Keep that rotation
        # and its state update in one explicit transaction even when the app's
        # connection factory uses autocommit. A rejected replacement therefore
        # restores the prior credential instead of deleting or orphaning it.
        with conn.transaction():
            row = conn.execute(
                "SELECT rvbbit.put_credential('api_key','calliope-provider',%s,%s,%s,%s::jsonb) AS ref",
                (
                    provider, token, f"{_PROVIDER_NAMES[provider]} credential for Hermes",
                    _json({"provider": provider, "consumer": "calliope-hermes"}),
                ),
            ).fetchone()
            credential_ref = str(row["ref"])
            resolved = conn.execute(
                "SELECT rvbbit.resolve_credential(%s,'calliope-hermes','materialize provider credential') AS value",
                (credential_ref,),
            ).fetchone()["value"]
            result = _control(
                "POST", "/v1/providers/key", {"provider": provider, "api_key": resolved}, timeout=30
            )
            conn.execute(
                "INSERT INTO rvbbit.calliope_provider_state "
                "(provider,display_name,auth_kind,auth_state,credential_ref,credential_label,"
                "status,metadata,tested_at,created_by,updated_by) VALUES "
                "(%s,%s,'api_key','configured',%s,%s,'ready','{\"source\":\"canonical_credential\"}'::jsonb,"
                "clock_timestamp(),%s,%s) ON CONFLICT (provider) DO UPDATE SET "
                "auth_kind='api_key',auth_state='configured',credential_ref=EXCLUDED.credential_ref,"
                "credential_label=EXCLUDED.credential_label,status='ready',last_error=NULL,"
                "metadata=EXCLUDED.metadata,tested_at=clock_timestamp(),updated_at=clock_timestamp(),"
                "updated_by=EXCLUDED.updated_by",
                (provider, _PROVIDER_NAMES[provider], credential_ref, result.get("credential_label"), actor, actor),
            )
    refresh(conn_factory, models=True)
    return {"ok": True, "provider": provider, "validation": result.get("validation")}


def _configured_other_than(conn_factory: Callable[..., Any], provider: str) -> int:
    with conn_factory() as conn:
        return int(conn.execute(
            "SELECT count(*) AS n FROM rvbbit.calliope_provider_state "
            "WHERE auth_state='configured' AND provider<>%s", (provider,),
        ).fetchone()["n"])


def remove_provider(conn_factory: Callable[..., Any], provider: str, *, actor: str) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()
    if provider not in _API_KEY_PROVIDERS | _OAUTH_PROVIDERS:
        raise ValueError("unknown managed provider")
    import system_ai
    if provider == "openrouter" and system_ai.key_path():
        raise ValueError("OpenRouter uses the shared instance key and cannot be disconnected separately.")
    if provider == "clover":
        with conn_factory() as conn:
            state = conn.execute(
                "SELECT environment_managed FROM rvbbit.calliope_provider_state WHERE provider=%s",
                (provider,),
            ).fetchone()
        if state and state.get("environment_managed"):
            raise ValueError(f"{_PROVIDER_NAMES[provider]} is managed by the host environment")
    dependents = [
        str(agent.get("name") or agent.get("key"))
        for agent in agents(conn_factory) if agent.get("provider") == provider
    ]
    if dependents:
        raise ValueError(f"point these agents at another provider before disconnecting {provider}: {', '.join(dependents)}")
    if _configured_other_than(conn_factory, provider) < 1:
        raise ValueError("at least one model provider must remain configured")
    path = "/v1/providers/oauth" if provider in _OAUTH_PROVIDERS else "/v1/providers/key"
    _control("DELETE", path + "?" + urllib.parse.urlencode({"provider": provider}), timeout=30)
    with conn_factory() as conn:
        row = conn.execute(
            "SELECT credential_ref FROM rvbbit.calliope_provider_state WHERE provider=%s",
            (provider,),
        ).fetchone()
        if row and row.get("credential_ref"):
            conn.execute("SELECT rvbbit.delete_credential(%s)", (row["credential_ref"],))
        conn.execute(
            "UPDATE rvbbit.calliope_provider_state SET auth_state='missing',credential_ref=NULL,"
            "credential_label=NULL,account_hint=NULL,status='unknown',last_error=NULL,"
            "updated_at=clock_timestamp(),updated_by=%s WHERE provider=%s",
            (actor, provider),
        )
        conn.execute(
            "UPDATE rvbbit.provider_models SET available=false,updated_at=clock_timestamp() "
            "WHERE provider=%s AND source='hermes'", (provider,),
        )
    return {"ok": True, "provider": provider}


def start_oauth(conn_factory: Callable[..., Any], provider: str, *, actor: str) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()
    if provider not in _OAUTH_PROVIDERS:
        raise ValueError("provider does not support subscription OAuth")
    result = _control("POST", "/v1/providers/oauth/start", {"provider": provider}, timeout=40)
    flow = "authorization_code" if provider in _CODE_ENTRY_PROVIDERS else "device_code"
    with conn_factory() as conn:
        conn.execute(
            "INSERT INTO rvbbit.calliope_provider_state "
            "(provider,display_name,auth_kind,auth_state,status,metadata,created_by,updated_by) "
            "VALUES (%s,%s,'oauth','pending','unknown',%s::jsonb,%s,%s) "
            "ON CONFLICT (provider) DO UPDATE SET auth_kind='oauth',auth_state='pending',"
            "status='unknown',last_error=NULL,metadata=EXCLUDED.metadata,"
            "updated_at=clock_timestamp(),updated_by=EXCLUDED.updated_by",
            (
                provider, _PROVIDER_NAMES[provider],
                _json({"flow": flow, "auth_kind": "cli_login" if provider in _CLI_LOGIN_PROVIDERS else "oauth"}),
                actor, actor,
            ),
        )
    return result


def submit_oauth_code(
    conn_factory: Callable[..., Any], provider: str, session_id: str, code: str, *, actor: str
) -> dict[str, Any]:
    """Forward the pasted authorization code to the waiting Hermes login.
    The code is single-use and never stored by Calliope."""
    provider = str(provider or "").strip().lower()
    if provider not in _CODE_ENTRY_PROVIDERS:
        raise ValueError("provider does not take a pasted authorization code")
    code = str(code or "").strip()
    if not code or len(code) > 512:
        raise ValueError("paste the code Anthropic showed you")
    result = _control(
        "POST", "/v1/providers/oauth/code",
        {"provider": provider, "session_id": str(session_id or ""), "code": code},
        timeout=30,
    )
    with conn_factory() as conn:
        conn.execute(
            "UPDATE rvbbit.calliope_provider_state SET updated_at=clock_timestamp(),updated_by=%s "
            "WHERE provider=%s",
            (actor, provider),
        )
    return result


def set_agent_models(
    conn_factory: Callable[..., Any], changes: Any, *, actor: str, auxiliary: Any = None,
) -> dict[str, Any]:
    """Apply agent models and the optional Background Tasks route together.

    Specialist model edits and the auxiliary route share one supervisor write
    pass and one verified restart. Calliope's default model and auxiliary copy
    share one durable config revision. Everything validates before either path
    is touched, so the UI can offer one honest Apply control for the batch.
    """
    if not isinstance(changes, list):
        raise ValueError("changes must be a list")
    auxiliary_present = auxiliary is not None
    if not changes and not auxiliary_present:
        raise ValueError("changes must be non-empty unless auxiliary is supplied")
    if len(changes) > 256:
        raise ValueError("too many changes in one batch (max 256)")
    expected = {str(change.get("agent") or ""): change["expected_hash"]
                for change in changes if isinstance(change, dict) and change.get("expected_hash")}
    if expected:
        live = {item["key"]: item.get("config_hash")
                for item in hermes_profiles.profiles().get("profiles", [])}
        stale = [key for key, digest in expected.items() if live.get(key) != digest]
        if stale:
            raise ProviderControlError("Agent settings changed since they were loaded: " + ", ".join(stale) + ". Read current settings and review the selection.", 409)
    resolved: list[dict[str, Any]] = []
    seen: set[str] = set()
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("each change must be an object")
        agent = str(change.get("agent") or "").strip().lower()
        provider = str(change.get("provider") or "").strip().lower()
        model = str(change.get("model") or "").strip()
        if not _AGENT_KEY_RE.fullmatch(agent):
            raise ValueError(f"invalid agent key: {agent!r}")
        if agent in seen:
            raise ValueError(f"duplicate agent in batch: {agent}")
        seen.add(agent)
        context_length, max_tokens = _validate_choice(conn_factory, provider, model)
        effort = _validate_reasoning_effort(change.get("reasoning_effort"))
        resolved.append({
            "agent": agent, "provider": provider, "model": model,
            "hermes_provider": _HERMES_PROVIDER_IDS.get(provider, provider),
            "context_length": context_length, "max_tokens": max_tokens,
            "reasoning_effort": effort,
        })
    auxiliary_choice = None
    if auxiliary_present:
        if not isinstance(auxiliary, dict):
            raise ValueError("auxiliary must be an object")
        auxiliary_provider = str(auxiliary.get("provider") or "").strip().lower()
        auxiliary_model = str(auxiliary.get("model") or "").strip()
        if bool(auxiliary_provider) != bool(auxiliary_model):
            raise ValueError("background-task provider and model must be set together")
        if auxiliary_provider:
            _validate_choice(conn_factory, auxiliary_provider, auxiliary_model)
        auxiliary_choice = {
            "provider": auxiliary_provider,
            "model": auxiliary_model,
            "hermes_provider": _HERMES_PROVIDER_IDS.get(auxiliary_provider, auxiliary_provider),
        }
    default_change = next((item for item in resolved if item["agent"] == "default"), None)
    specialist_changes = [item for item in resolved if item["agent"] != "default"]
    outcome: dict[str, Any] = {"ok": True, "results": [], "restarts": 0}
    if specialist_changes or auxiliary_choice is not None:
        specialist_payload: dict[str, Any] = {
            "changes": [
                {"profile": item["agent"], "provider": item["hermes_provider"],
                 "model": item["model"],
                 **({"expected_hash": expected[item["agent"]]} if item["agent"] in expected else {}),
                 **({"context_length": item["context_length"]} if item["context_length"] else {}),
                 **({"max_tokens": item["max_tokens"]} if item["max_tokens"] else {}),
                 **({"reasoning_effort": item["reasoning_effort"]} if item["reasoning_effort"] is not None else {})}
                for item in specialist_changes
            ],
        }
        if auxiliary_choice is not None:
            specialist_payload["auxiliary"] = {
                "provider": auxiliary_choice["hermes_provider"] or None,
                "model": auxiliary_choice["model"] or None,
            }
        result = hermes_profiles._control(
            "POST", "/v1/profile/models",
            specialist_payload,
            timeout=300,   # one verified restart covers the whole batch
        )
        if not result.get("ok"):
            status = 422 if result.get("phase") == "validation" else 502
            raise ProviderControlError(
                _rejection_message(result, "Hermes rejected the model changes"), status,
            )
        outcome["results"].extend(result.get("results") or [])
        if result.get("restarted"):
            outcome["restarts"] += 1
    if default_change or auxiliary_choice is not None:
        current = hermes_config.current(conn_factory)
        if default_change:
            candidate = _default_agent_candidate(
                current, default_change["hermes_provider"], default_change["model"],
                default_change["context_length"], default_change["max_tokens"],
                default_change["reasoning_effort"],
            )
            payload = yaml.safe_load(candidate) or {}
        else:
            payload = yaml.safe_load(current.get("config_yaml") or "") or {}
        if not isinstance(payload, dict):
            raise ValueError("active Hermes config is not a YAML object")
        if auxiliary_choice is not None:
            _apply_auxiliary_lane(
                payload, auxiliary_choice["hermes_provider"], auxiliary_choice["model"],
            )
        candidate = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        candidate, reconciliation = hermes_profiles._reconcile_default_config(candidate)
        if not reconciliation.get("valid"):
            raise ProviderControlError(
                _rejection_message({"validation": reconciliation}, "Hermes rejected the model settings"), 422,
            )
        reason_parts = []
        if default_change:
            reason_parts.append(
                f"point default at {default_change['provider']}/{default_change['model']}"
            )
        if auxiliary_choice is not None:
            reason_parts.append(
                "clear the auxiliary background-task lane"
                if not auxiliary_choice["provider"]
                else f"set the auxiliary background-task lane to {auxiliary_choice['provider']}/{auxiliary_choice['model']}"
            )
        queued = hermes_config.queue_apply(
            conn_factory, candidate, reason="; ".join(reason_parts), actor=actor,
            secret_bindings=current.get("secret_bindings") or {},
        )
        if not queued.get("queued"):
            raise ProviderControlError(_rejection_message(queued, "Hermes rejected the model settings"), 422)
        outcome["default"] = {
            "ok": True,
            "agent": "default",
            "revision_id": queued.get("revision_id"),
            "applied": "revision-controller",
            **({"provider": default_change["provider"], "model": default_change["model"]}
               if default_change else {}),
        }
        outcome["restarts"] += 1   # the revision controller performs its own verified apply
    if auxiliary_choice is not None:
        outcome["auxiliary"] = (
            None if not auxiliary_choice["provider"]
            else {"provider": auxiliary_choice["provider"], "model": auxiliary_choice["model"]}
        )
    outcome["applied"] = len(specialist_changes) + (1 if default_change else 0)
    return outcome


def set_fallback_model(
    conn_factory: Callable[..., Any], provider: Any, model: Any, *, actor: str,
) -> dict[str, Any]:
    """Set (or clear, with empty provider+model) the appliance-wide fallback
    model: the model Hermes falls back to when an agent's primary provider
    cannot produce a client at init or exhausts at runtime (run 11's
    "No LLM provider configured" dead-end). Applied to every specialist
    profile through the supervisor (one verified restart) and to the default
    agent through the revision controller."""
    provider = str(provider or "").strip().lower()
    model = str(model or "").strip()
    clearing = not provider and not model
    if not clearing:
        if provider == "clover":
            raise ValueError("choose a cloud provider for the fallback; Clover is the appliance itself")
        _validate_choice(conn_factory, provider, model)
    hermes_provider = _HERMES_PROVIDER_IDS.get(provider, provider)
    # Specialists: one supervisor pass, one verified restart.
    result = hermes_profiles._control(
        "POST", "/v1/profile/fallback",
        {"provider": hermes_provider or None, "model": model or None},
        timeout=300,
    )
    if not result.get("ok"):
        status = 422 if result.get("phase") == "validation" else 502
        raise ProviderControlError(_rejection_message(result, "Hermes rejected the fallback model"), status)
    # Default agent: same change through the durable revision controller.
    current = hermes_config.current(conn_factory)
    payload = yaml.safe_load(current.get("config_yaml") or "") or {}
    if not isinstance(payload, dict):
        raise ValueError("active Hermes config is not a YAML object")
    if clearing:
        payload.pop("fallback_model", None)
    else:
        payload["fallback_model"] = {"provider": hermes_provider, "model": model}
    candidate = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    candidate, reconciliation = hermes_profiles._reconcile_default_config(candidate)
    if not reconciliation.get("valid"):
        raise ProviderControlError(
            _rejection_message({"validation": reconciliation}, "Hermes rejected the fallback model"), 422,
        )
    queued = hermes_config.queue_apply(
        conn_factory, candidate,
        reason=("clear the appliance fallback model" if clearing
                else f"set the appliance fallback model to {provider}/{model}"),
        actor=actor, secret_bindings=current.get("secret_bindings") or {},
    )
    if not queued.get("queued"):
        raise ProviderControlError(_rejection_message(queued, "Hermes rejected the fallback model"), 422)
    return {
        "ok": True, "fallback": (None if clearing else {"provider": provider, "model": model}),
        "specialists": result.get("results") or [], "revision_id": queued.get("revision_id"),
        "restarts": (1 if result.get("restarted") else 0) + 1,
    }


def _apply_auxiliary_lane(payload: dict[str, Any], provider: str, model: str) -> None:
    """Apply Calliope's managed auxiliary fallback to one Hermes payload."""
    aux = (
        json.loads(json.dumps(payload.get("auxiliary") or {}))
        if isinstance(payload.get("auxiliary"), dict)
        else {}
    )
    managed_entry = None
    if provider and model:
        managed_entry = {
            "provider": provider,
            "model": model,
            _CALLIOPE_AUXILIARY_MARKER: True,
        }
        if provider == "custom":
            managed_entry.update(_CLOVER_MODEL_BINDINGS)
    for task in _CALLIOPE_AUXILIARY_TASKS:
        current = aux.get(task)
        task_config = dict(current) if isinstance(current, dict) else {}
        raw_chain = task_config.get("fallback_chain")
        chain = [
            entry for entry in (raw_chain if isinstance(raw_chain, list) else [])
            if not (isinstance(entry, dict) and entry.get(_CALLIOPE_AUXILIARY_MARKER) is True)
        ]
        if managed_entry is not None:
            chain.insert(0, dict(managed_entry))
        if chain:
            task_config["fallback_chain"] = chain
        else:
            task_config.pop("fallback_chain", None)
        if task_config:
            aux[task] = task_config
        else:
            aux.pop(task, None)
    aux.pop("openrouter_model", None)
    aux.pop("free_only", None)
    # Keep an empty section when the operator chooses automatic routing so
    # the appliance's first-install fallback is not re-seeded on restart.
    payload["auxiliary"] = aux


def set_auxiliary_model(
    conn_factory: Callable[..., Any], provider: Any, model: Any, *, actor: str,
) -> dict[str, Any]:
    """Set (or clear) the appliance-wide auxiliary fallback provider/model.

    Background tasks still use each agent's own model first. Hermes then tries
    this selected route before its automatic discovery chain. The managed
    fallback is written into every built-in auxiliary task while preserving
    hand-authored fallback entries and task-specific options."""
    result = set_agent_models(
        conn_factory, [], actor=actor,
        auxiliary={"provider": provider, "model": model},
    )
    # Preserve the standalone endpoint's response shape for older clients even
    # though it now delegates to the shared batch machinery.
    result["specialists"] = result.get("results") or []
    result["revision_id"] = (result.get("default") or {}).get("revision_id")
    return result


def current_auxiliary(conn_factory: Callable[..., Any]) -> dict[str, Any] | None:
    """The default agent's configured auxiliary lane overrides (the
    appliance-wide setting's source of truth for display). Legacy
    ``openrouter_model`` settings remain visible until the operator saves or
    clears the new provider/model control."""
    try:
        current = hermes_config.current(conn_factory)
        payload = yaml.safe_load(current.get("config_yaml") or "") or {}
        aux = payload.get("auxiliary")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(aux, dict):
        return None
    for task in _CALLIOPE_AUXILIARY_TASKS:
        task_config = aux.get(task)
        chain = task_config.get("fallback_chain") if isinstance(task_config, dict) else None
        for entry in chain if isinstance(chain, list) else []:
            if not isinstance(entry, dict) or entry.get(_CALLIOPE_AUXILIARY_MARKER) is not True:
                continue
            hermes_provider = str(entry.get("provider") or "")
            model = str(entry.get("model") or "")
            if hermes_provider and model:
                provider = _CALLIOPE_PROVIDER_IDS.get(hermes_provider, hermes_provider)
                return {"provider": provider, "model": model}
    legacy_model = str(aux.get("openrouter_model") or "")
    if legacy_model:
        return {
            "provider": "openrouter",
            "model": legacy_model,
            "legacy": True,
            "free_only": bool(aux.get("free_only")),
        }
    return None


def current_fallback(conn_factory: Callable[..., Any]) -> dict[str, Any] | None:
    """The default agent's configured fallback (the appliance-wide setting's
    source of truth for display)."""
    try:
        current = hermes_config.current(conn_factory)
        payload = yaml.safe_load(current.get("config_yaml") or "") or {}
        fallback = payload.get("fallback_model")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(fallback, dict) or not fallback.get("model"):
        return None
    hermes_provider = str(fallback.get("provider") or "")
    ours = next((key for key, value in _HERMES_PROVIDER_IDS.items() if value == hermes_provider), hermes_provider)
    return {"provider": ours, "model": str(fallback.get("model") or "")}


# ── model loadouts ──────────────────────────────────────────────────────────

_LOADOUT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")


def loadouts(conn_factory: Callable[..., Any]) -> list[dict[str, Any]]:
    try:
        with conn_factory() as conn:
            rows = conn.execute(
                "SELECT id,name,owner_email,config,created_at,updated_at "
                "FROM rvbbit.calliope_model_loadouts ORDER BY name"
            ).fetchall()
    except Exception:  # noqa: BLE001 — table lands with the calliope boot DDL
        return []
    result = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item["id"])
        for key in ("created_at", "updated_at"):
            if hasattr(item.get(key), "isoformat"):
                item[key] = item[key].isoformat()
        result.append(item)
    return result


def save_loadout(conn_factory: Callable[..., Any], name: str, *, actor: str) -> dict[str, Any]:
    """Snapshot every agent's CURRENT provider/model under a name."""
    name = str(name or "").strip()
    if not _LOADOUT_NAME_RE.fullmatch(name):
        raise ValueError("loadout names are 1-80 letters, digits, spaces, dots, dashes")
    snapshot = [
        {"agent": item["key"], "provider": item.get("provider"), "model": item.get("model"),
         "reasoning_effort": item.get("reasoning_effort") or ""}
        for item in agents(conn_factory)
        if not item.get("error") and item.get("provider") and item.get("model")
    ]
    if not snapshot:
        raise ValueError("no agent models to save")
    fallback = current_fallback(conn_factory)
    document = {"agents": snapshot, **({"fallback": fallback} if fallback else {})}
    with conn_factory() as conn:
        row = conn.execute(
            "INSERT INTO rvbbit.calliope_model_loadouts (name,owner_email,config) "
            "VALUES (%s,%s,%s::jsonb) "
            "ON CONFLICT (name) DO UPDATE SET config=EXCLUDED.config,"
            "owner_email=EXCLUDED.owner_email,updated_at=clock_timestamp() RETURNING id",
            (name, actor, json.dumps(document)),
        ).fetchone()
    return {"ok": True, "id": str(row["id"]), "name": name, "agents": len(snapshot)}


def apply_loadout(conn_factory: Callable[..., Any], loadout_id: str, *, actor: str) -> dict[str, Any]:
    """Apply a saved loadout: only the agents whose model differs are changed,
    through the same batched path (specialists in one restart)."""
    with conn_factory() as conn:
        row = conn.execute(
            "SELECT id,name,config FROM rvbbit.calliope_model_loadouts WHERE id=%s::uuid",
            (str(loadout_id),),
        ).fetchone()
    if not row:
        raise ValueError("loadout not found")
    document = row["config"] if isinstance(row["config"], dict) else json.loads(row["config"] or "{}")
    saved = {str(item.get("agent")): item for item in document.get("agents") or [] if item.get("agent")}
    current = {item["key"]: item for item in agents(conn_factory) if not item.get("error")}
    changes = []
    skipped = []
    for agent_key, item in saved.items():
        live = current.get(agent_key)
        if live is None:
            skipped.append({"agent": agent_key, "reason": "agent no longer exists"})
            continue
        saved_effort = item.get("reasoning_effort")
        live_effort = str(live.get("reasoning_effort") or "")
        effort_differs = saved_effort is not None and str(saved_effort) != live_effort
        if (live.get("provider"), live.get("model")) == (item.get("provider"), item.get("model")) and not effort_differs:
            continue
        changes.append({
            "agent": agent_key, "provider": item.get("provider"), "model": item.get("model"),
            **({"reasoning_effort": str(saved_effort)} if saved_effort is not None else {}),
        })
    if not changes:
        return {"ok": True, "name": row["name"], "applied": 0, "skipped": skipped,
                "message": "every agent already runs this loadout"}
    result = set_agent_models(conn_factory, changes, actor=actor)
    result["name"] = row["name"]
    result["skipped"] = skipped
    return result


def delete_loadout(conn_factory: Callable[..., Any], loadout_id: str, *, actor: str) -> dict[str, Any]:
    with conn_factory() as conn:
        row = conn.execute(
            "DELETE FROM rvbbit.calliope_model_loadouts WHERE id=%s::uuid RETURNING name",
            (str(loadout_id),),
        ).fetchone()
    if not row:
        raise ValueError("loadout not found")
    return {"ok": True, "deleted": row["name"]}


def clear_provider_bench(
    conn_factory: Callable[..., Any], provider: str, *, actor: str
) -> dict[str, Any]:
    """Reset a provider's benched (exhausted) pool credentials so the next call
    retries immediately — for after the operator fixed the cause externally
    (added credits, raised a limit). The credential itself never changes."""
    provider = str(provider or "").strip().lower()
    if provider not in _HERMES_PROVIDER_IDS and provider not in _PROVIDER_NAMES:
        raise ValueError(f"unknown provider: {provider!r}")
    result = _control(
        "POST", "/v1/providers/bench/clear", {"provider": provider}, timeout=30,
    )
    with conn_factory() as conn:
        conn.execute(
            "UPDATE rvbbit.calliope_provider_state SET last_error=NULL,"
            "updated_at=clock_timestamp(),updated_by=%s WHERE provider=%s",
            (actor, provider),
        )
    refresh(conn_factory, models=False)
    return result


def poll_oauth(
    conn_factory: Callable[..., Any], provider: str, session_id: str, *, actor: str
) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()
    if provider not in _OAUTH_PROVIDERS:
        raise ValueError("provider does not support subscription OAuth")
    result = _control(
        "GET", "/v1/providers/oauth/poll?" + urllib.parse.urlencode({
            "provider": provider, "session_id": str(session_id or ""),
        }), timeout=20,
    )
    status = str(result.get("status") or "pending")
    if status == "approved":
        refresh(conn_factory, models=True)
    elif status in {"error", "expired", "denied"}:
        with conn_factory() as conn:
            conn.execute(
                "UPDATE rvbbit.calliope_provider_state SET auth_state=%s,status='error',"
                "last_error=%s,updated_at=clock_timestamp(),updated_by=%s WHERE provider=%s",
                ("expired" if status == "expired" else "error", result.get("message"), actor, provider),
            )
    return result


def cancel_oauth(
    conn_factory: Callable[..., Any], provider: str, session_id: str, *, actor: str
) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()
    session = str(session_id or "").strip()
    if provider not in _OAUTH_PROVIDERS or not session or len(session) > 240:
        raise ValueError("invalid subscription OAuth session")
    result = _control(
        "DELETE",
        "/v1/providers/oauth/session?" + urllib.parse.urlencode({
            "provider": provider, "session_id": session,
        }),
        timeout=20,
    )
    with conn_factory() as conn:
        conn.execute(
            "UPDATE rvbbit.calliope_provider_state SET auth_state='missing',status='unknown',"
            "last_error=NULL,updated_at=clock_timestamp(),updated_by=%s "
            "WHERE provider=%s AND auth_state='pending'",
            (actor, provider),
        )
    return result


def _agent_calls(conn_factory: Callable[..., Any]) -> dict[str, dict[str, Any]]:
    """What each agent actually called lately, from the Hermes session mirror
    (rvbbit.hermes_api_calls, keyed by store = default | <profile>). Empty
    when the mirror is not installed."""
    try:
        with conn_factory() as conn:
            rows = conn.execute(
                "SELECT store, provider, model, count(*)::bigint AS calls, max(ts) AS last_call_at, "
                "sum(input_tokens)::bigint AS tokens_in, sum(output_tokens)::bigint AS tokens_out "
                "FROM rvbbit.hermes_api_calls WHERE ts >= now() - interval '7 days' "
                "GROUP BY store, provider, model ORDER BY store, max(ts) DESC"
            ).fetchall()
    except Exception:  # noqa: BLE001 — the mirror is optional
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        row = dict(row)
        store = str(row.get("store") or "default")
        entry = out.setdefault(store, {"calls_7d": 0, "tokens_7d": 0, "last": None})
        entry["calls_7d"] += int(row.get("calls") or 0)
        entry["tokens_7d"] += int(row.get("tokens_in") or 0) + int(row.get("tokens_out") or 0)
        if entry["last"] is None:   # rows arrive newest-first per store
            hermes_provider = str(row.get("provider") or "")
            entry["last"] = {
                "provider": _CALLIOPE_PROVIDER_IDS.get(hermes_provider, hermes_provider),
                "model": row.get("model"),
                "at": row.get("last_call_at"),
            }
    return out


def _default_agent_pending(conn_factory: Callable[..., Any]) -> dict[str, Any] | None:
    """A queued/applying revision for the default agent, if any — the
    controller applies it within its next tick (validated -> applying ->
    active, with a verified gateway restart), so the UI can say so."""
    try:
        with conn_factory() as conn:
            # Same selection the controller loop uses: only the revision the
            # controller state points at counts (an orphaned 'validated' row
            # from an abandoned edit must not read as queued forever).
            row = conn.execute(
                "SELECT r.id, r.state, r.reason, r.config_yaml, r.created_at "
                "FROM rvbbit.hermes_config_controller_state s "
                "JOIN rvbbit.hermes_config_revisions r ON r.id=s.desired_revision_id "
                "WHERE s.singleton AND r.state IN ('validated','applying')"
            ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    row = dict(row)
    try:
        payload = yaml.safe_load(row.get("config_yaml") or "") or {}
        block = payload.get("model") if isinstance(payload, dict) and isinstance(payload.get("model"), dict) else {}
    except Exception:  # noqa: BLE001
        block = {}
    hermes_provider = str(block.get("provider") or "")
    return {
        "revision_id": str(row.get("id") or ""),
        "state": row.get("state"),
        "reason": row.get("reason"),
        "provider": _CALLIOPE_PROVIDER_IDS.get(hermes_provider, hermes_provider) or None,
        "model": block.get("default"),
        "created_at": row.get("created_at"),
    }


def agents(conn_factory: Callable[..., Any]) -> list[dict[str, Any]]:
    """Every Hermes agent (Calliope + specialists) with its provider/model.

    Read from the gateway's live profile configs through the supervisor —
    the same files the agents run from — plus what they last called."""
    try:
        listing = hermes_profiles.profiles()
    except Exception as exc:  # noqa: BLE001 — surface as an empty list, not a 502 for the whole page
        return [{"key": "default", "name": "Calliope", "error": str(exc)[:300]}]
    calls = _agent_calls(conn_factory)
    pending = _default_agent_pending(conn_factory)
    result: list[dict[str, Any]] = []
    for item in listing.get("profiles") or []:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        key = str(item["key"])
        block = item.get("model") if isinstance(item.get("model"), dict) else {}
        hermes_provider = str(block.get("provider") or "")
        result.append({
            "key": key,
            "name": item.get("name") or ("Calliope" if key == "default" else key),
            "role_label": item.get("role_label"),
            "description": item.get("description"),
            "avatar_file": item.get("avatar_file"),
            "avatar_day_file": item.get("avatar_day_file"),
            "avatar_night_file": item.get("avatar_night_file"),
            "signature_font": item.get("signature_font"),
            "provider": _CALLIOPE_PROVIDER_IDS.get(hermes_provider, hermes_provider) or None,
            "model": block.get("model"),
            "context_length": block.get("context_length"),
            "max_tokens": block.get("max_tokens"),
            "reasoning_effort": block.get("reasoning_effort"),
            "operator_managed": bool(block.get("operator_managed")),
            "config_user_managed": bool(item.get("config_user_managed")),
            "config_hash": item.get("config_hash"),
            "model_errors": block.get("errors") or [],
            "usage": calls.get(key),
            "pending": pending if key == "default" else None,
        })
    return result


# Hermes' stable reasoning-effort scale (docs "Reasoning Effort" for the
# pinned revision). '' = auto: Hermes' default (medium) or the provider's own.
# Hermes clamps a requested level downward on routes that support fewer
# levels and silently skips reasoning controls for models without them, so
# any level is safe to store for any model.
REASONING_EFFORT_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


def _validate_reasoning_effort(value: Any) -> str | None:
    """None = leave the agent's current setting alone; '' = clear to auto."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "auto"):
        return ""
    if text not in REASONING_EFFORT_LEVELS:
        raise ValueError("reasoning_effort must be one of: " + ", ".join(REASONING_EFFORT_LEVELS))
    return text


def _validate_choice(conn_factory: Callable[..., Any], provider: str, model: str) -> tuple[int | None, int | None]:
    """Validate provider/model against the catalog; return (context budget,
    output cap) for that model (None = leave the agent's current value alone)."""
    if provider not in _SUPPORTED_PROVIDERS or not _PROVIDER_RE.fullmatch(provider):
        raise ValueError("unsupported model provider")
    if not model or len(model) > 256 or re.search(r"[\s\x00-\x1f]", model):
        raise ValueError("model is empty or invalid")
    with conn_factory() as conn:
        state = conn.execute(
            "SELECT auth_state FROM rvbbit.calliope_provider_state WHERE provider=%s", (provider,),
        ).fetchone()
        known = conn.execute(
            "SELECT context_window, output_token_limit FROM rvbbit.provider_models "
            "WHERE provider=%s AND model=%s AND available",
            (provider, model),
        ).fetchone()
    if not state or state["auth_state"] != "configured":
        raise ValueError(f"configure {provider} before pointing an agent at it")
    if not known and not (provider == "clover" and model.startswith("calliope")):
        raise ValueError("select a model from the refreshed provider catalog")
    return (
        _working_context_length((known or {}).get("context_window")),
        _working_max_tokens((known or {}).get("output_token_limit")),
    )


def _working_context_length(model_window: Any) -> int | None:
    try:
        window = int(model_window)
    except (TypeError, ValueError):
        return None
    if window < _MODEL_CONTEXT_FLOOR:
        return None
    return min(_MODEL_CONTEXT_CEILING, window)


def _working_max_tokens(model_output_limit: Any) -> int | None:
    try:
        limit = int(model_output_limit)
    except (TypeError, ValueError):
        return None
    if limit < _MODEL_MAX_TOKENS_FLOOR:
        return None
    return min(_MODEL_MAX_TOKENS_CEILING, limit)


def _default_agent_candidate(
    current: dict[str, Any], hermes_provider: str, model: str, context_length: int | None = None,
    max_tokens: int | None = None, reasoning_effort: str | None = None,
) -> str:
    payload = yaml.safe_load(current.get("config_yaml") or "") or {}
    if not isinstance(payload, dict):
        raise ValueError("active Hermes config is not a YAML object")
    block = payload.get("model")
    if not isinstance(block, dict):
        block = {}
        payload["model"] = block
    block["default"] = model
    block["provider"] = hermes_provider
    if context_length:
        block["context_length"] = int(context_length)
    if max_tokens:
        block["max_tokens"] = int(max_tokens)
    if hermes_provider == "custom":
        block.update(_CLOVER_MODEL_BINDINGS)
    else:
        for key in _CLOVER_MODEL_BINDINGS:
            block.pop(key, None)
    if reasoning_effort is not None:
        agent_block = payload.get("agent")
        if not isinstance(agent_block, dict):
            agent_block = {}
            payload["agent"] = agent_block
        if reasoning_effort:
            agent_block["reasoning_effort"] = reasoning_effort
        else:
            agent_block.pop("reasoning_effort", None)
            if not agent_block:
                payload.pop("agent", None)
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def _rejection_message(result: dict[str, Any], fallback: str) -> str:
    """Say why the control plane refused, not just that it did."""
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    errors = validation.get("errors") if isinstance(validation.get("errors"), list) else []
    parts = []
    for item in errors[:3]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        message = str(item.get("message") or "").strip()
        detail = f"{path}: {message}" if path else message
        if item.get("expected") is not None and item.get("actual") is not None:
            detail += f" (expected {item['expected']}, active revision has {item['actual']})"
        parts.append(detail)
    if not parts:
        return str(result.get("error") or fallback)
    more = f" (+{len(errors) - 3} more)" if len(errors) > 3 else ""
    return fallback + " — " + "; ".join(parts) + more


def set_agent_model(
    conn_factory: Callable[..., Any], agent: str, provider: str, model: str, *, actor: str,
    reasoning_effort: Any = None,
) -> dict[str, Any]:
    """Point one agent (default = Calliope, else a specialist profile) at
    provider/model. Calliope goes through the durable revision controller
    (validated, versioned, verified restart); a specialist is written into its
    profile config by the supervisor with the same validation and a verified
    restart."""
    agent = str(agent or "").strip().lower()
    provider = str(provider or "").strip().lower()
    model = str(model or "").strip()
    if not _AGENT_KEY_RE.fullmatch(agent):
        raise ValueError("invalid agent key")
    context_length, max_tokens = _validate_choice(conn_factory, provider, model)
    effort = _validate_reasoning_effort(reasoning_effort)
    hermes_provider = _HERMES_PROVIDER_IDS.get(provider, provider)
    budgets = [f"context {context_length}" if context_length else "", f"output {max_tokens}" if max_tokens else ""]
    budgets = [item for item in budgets if item]
    if effort is not None:
        budgets.append(f"effort {effort or 'auto'}")
    reason = f"point {agent} at {provider}/{model}" + (f" ({', '.join(budgets)})" if budgets else "")
    if agent == "default":
        current = hermes_config.current(conn_factory)
        candidate = _default_agent_candidate(current, hermes_provider, model, context_length, max_tokens, effort)
        candidate, reconciliation = hermes_profiles._reconcile_default_config(candidate)
        if not reconciliation.get("valid"):
            raise ProviderControlError(
                _rejection_message({"validation": reconciliation}, "Hermes rejected the model choice"), 422,
            )
        queued = hermes_config.queue_apply(
            conn_factory, candidate, reason=reason, actor=actor,
            secret_bindings=current.get("secret_bindings") or {},
        )
        if not queued.get("queued"):
            raise ProviderControlError(_rejection_message(queued, "Hermes rejected the model choice"), 422)
        return {
            "ok": True, "agent": agent, "provider": provider, "model": model,
            "context_length": context_length, "max_tokens": max_tokens,
            **({"reasoning_effort": effort} if effort is not None else {}),
            "revision_id": queued.get("revision_id"), "applied": "revision-controller",
        }
    result = hermes_profiles._control(
        "POST", "/v1/profile/model",
        {"profile": agent, "provider": hermes_provider, "model": model,
         **({"context_length": context_length} if context_length else {}),
         **({"max_tokens": max_tokens} if max_tokens else {}),
         **({"reasoning_effort": effort} if effort is not None else {})},
        timeout=240,   # verified restart: stop, start, health probe (90 s), possible rollback
    )
    if not result.get("ok"):
        status = 422 if result.get("phase") == "validation" else 409 if result.get("phase") == "compare_and_swap" else 502
        raise ProviderControlError(_rejection_message(result, f"Hermes rejected the model choice for {agent}"), status)
    return {
        "ok": True, "agent": agent, "provider": provider, "model": model,
        "context_length": context_length, "max_tokens": max_tokens,
        "applied": "profile-config", "changed": bool(result.get("changed")),
        "config_hash": result.get("config_hash"),
    }
