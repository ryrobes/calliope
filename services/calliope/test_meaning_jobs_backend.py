"""The narrative worker narrates wherever Clover does, even with no OpenRouter key on the box."""
import sys
import types

import meaning_jobs as mj
import meaning_jobs


class FakeClient:
    calls = []

    def __init__(self, backend_name, model):
        self.backend_name = backend_name
        self.model = model
        self.strict_model_version = True

    allowed_on_clover = {"calliope-luna", "clover", "calliope"}

    @classmethod
    def from_postgres(cls, dsn, *, backend_name, model, request_user, timeout_seconds, transport_attempts):
        cls.calls.append((backend_name, model, request_user))
        if backend_name == "openrouter":
            raise RuntimeError("backend 'openrouter' has no usable token in its registered secret")
        if backend_name == "clover_llm" and model not in cls.allowed_on_clover:
            raise RuntimeError(f"model {model!r} is not registered for backend 'clover_llm'")
        if backend_name == "missing":
            raise RuntimeError("registered backend 'missing' was not found")
        if backend_name == "broken":
            raise RuntimeError("connection refused")
        return cls(backend_name, model)


def _install_fake(monkeypatch):
    module = types.ModuleType("business_topology.worker")
    module.HutchChatClient = FakeClient
    package = types.ModuleType("business_topology")
    package.worker = module
    monkeypatch.setitem(sys.modules, "business_topology", package)
    monkeypatch.setitem(sys.modules, "business_topology.worker", module)
    monkeypatch.setattr(mj, "_registered_model", lambda dsn, backend: "clover")
    monkeypatch.delenv("WAREHOUSE_MEANING_NARRATIVE_BACKEND", raising=False)
    monkeypatch.delenv("WAREHOUSE_MEANING_NARRATIVE_MODEL", raising=False)
    FakeClient.calls.clear()
    mj._ANNOUNCED_BACKENDS.clear()


def test_no_openrouter_token_falls_back_to_the_appliance_clover_backend(monkeypatch):
    _install_fake(monkeypatch)
    logged = []
    monkeypatch.setattr(mj, "_log", logged.append)
    client = mj.build_client("postgresql://box")
    assert (client.backend_name, client.model) == ("clover_llm", "calliope-luna")   # Luna Pro, not the weak registered model
    assert client.strict_model_version is False
    assert FakeClient.calls == [("openrouter", mj.DEFAULT_MODEL, "meaning-narrative-worker"), ("clover_llm", "calliope-luna", "meaning-narrative-worker")]
    assert logged and "narrating through 'clover_llm' as 'calliope-luna'" in logged[0]
    mj.build_client("postgresql://box")
    assert len(logged) == 1                                   # announced once per process


def test_when_the_strong_route_is_not_allowed_the_registered_model_is_the_last_resort(monkeypatch):
    _install_fake(monkeypatch)
    monkeypatch.setattr(FakeClient, "allowed_on_clover", {"clover", "calliope"})
    client = mj.build_client("postgresql://box")
    assert (client.backend_name, client.model) == ("clover_llm", "clover")
    assert [c[1] for c in FakeClient.calls] == [mj.DEFAULT_MODEL, "calliope-luna", "clover"]


def test_the_fallback_model_can_be_overridden(monkeypatch):
    _install_fake(monkeypatch)
    monkeypatch.setattr(FakeClient, "allowed_on_clover", {"clover", "calliope", "calliope-luna", "calliope-luna-900k"})
    monkeypatch.setenv("WAREHOUSE_MEANING_NARRATIVE_FALLBACK_MODEL", "calliope-luna-900k")
    assert mj.build_client("postgresql://box").model == "calliope-luna-900k"


def test_a_configured_backend_that_works_is_kept(monkeypatch):
    _install_fake(monkeypatch)
    monkeypatch.setenv("WAREHOUSE_MEANING_NARRATIVE_BACKEND", "house")
    monkeypatch.setenv("WAREHOUSE_MEANING_NARRATIVE_MODEL", "house-model")
    client = mj.build_client("postgresql://box")
    assert (client.backend_name, client.model) == ("house", "house-model")


def test_other_failures_still_raise(monkeypatch):
    _install_fake(monkeypatch)
    monkeypatch.setenv("WAREHOUSE_MEANING_NARRATIVE_BACKEND", "broken")
    import pytest
    with pytest.raises(RuntimeError, match="connection refused"):
        mj.build_client("postgresql://box")


def test_map_client_uses_the_same_fallback(monkeypatch):
    _install_fake(monkeypatch)
    monkeypatch.setenv("WAREHOUSE_MEANING_MAP_MODEL", "google/gemini-3.8-pro")
    mj._MAP_CLIENT = None
    default = FakeClient("clover_llm", "clover")
    client = mj.map_client("postgresql://box", default)
    assert (client.backend_name, client.model, FakeClient.calls[-1][2]) == ("clover_llm", "calliope-luna", "meaning-map")



def test_a_tick_stops_after_the_map_because_it_re_keys_the_subjects(monkeypatch):
    """The dossiers queued behind a map in the same snapshot name subjects the
    map may have replaced; running them raised on a missing row."""
    ran = []
    monkeypatch.setattr(mj, "run_map", lambda cf, client, fp: ran.append(("map", fp)) or {"map_id": 2})
    monkeypatch.setattr(mj, "run_dossier", lambda cf, client, key: ran.append(("dossier", key)) or {"domain_key": key})
    monkeypatch.setattr(mj, "run_recognition", lambda cf, client, schema: ran.append(("recognition", schema)) or {"schema": schema})
    queue = [
        {"kind": "recognition", "key": "co", "schema_name": "co", "members": None, "member_fingerprint": None, "reason": "new"},
        {"kind": "map", "key": "company", "schema_name": None, "members": None, "member_fingerprint": "fp-1", "reason": "new"},
        {"kind": "dossier", "key": "subject:old_key", "schema_name": None, "members": [], "member_fingerprint": "x", "reason": "new"},
    ]

    class Result:
        def __init__(self, row): self.row = row
        def fetchone(self): return self.row
        def fetchall(self): return self.row

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=None):
            if "meaning_narrative_spend" in sql: return Result({"s": 0})
            if "meaning_narrative_queue(%s)" in sql: return Result(queue)
            if "count(*) AS n FROM rvbbit.meaning_relations" in sql: return Result({"n": 0})
            if "meaning_narrative_queue(50)" in sql: return Result({"n": 1})
            return Result({"r": None})

    results = mj.tick(lambda: Conn(), client=object(), budget_usd=5.0, max_items=3)
    assert ran == [("recognition", "co"), ("map", "fp-1")]          # the stale dossier never ran
    assert [r.get("map_id") for r in results if "map_id" in r] == [2]

def test_the_enrich_route_asks_clover_for_sol_even_where_openrouter_is_usable(monkeypatch):
    # The pilot has an OpenRouter key and no model restriction on that backend,
    # so asking it for a Clover-only model connects and then fails every call.
    seen = {}

    def fake_connect(dsn, backend, model, request_user, timeout_seconds):
        seen["backend"], seen["model"] = backend, model
        return types.SimpleNamespace(backend_name=backend, model=model, strict_model_version=False)

    monkeypatch.setattr(meaning_jobs, "_connect_client", fake_connect)
    monkeypatch.delenv("WAREHOUSE_MEANING_ENRICH_BACKEND", raising=False)
    monkeypatch.delenv("WAREHOUSE_MEANING_NARRATIVE_BACKEND", raising=False)
    meaning_jobs._ENRICH_CLIENT = None
    client = meaning_jobs.enrich_client("postgresql://x", types.SimpleNamespace(model="other"))
    assert seen == {"backend": "clover_llm", "model": "calliope-sol"}
    assert client.model == "calliope-sol"


def test_an_operator_can_still_name_the_enrich_backend(monkeypatch):
    seen = {}

    def fake_connect(dsn, backend, model, request_user, timeout_seconds):
        seen["backend"], seen["model"] = backend, model
        return types.SimpleNamespace(backend_name=backend, model=model, strict_model_version=False)

    monkeypatch.setattr(meaning_jobs, "_connect_client", fake_connect)
    monkeypatch.setenv("WAREHOUSE_MEANING_ENRICH_BACKEND", "openrouter")
    monkeypatch.setenv("WAREHOUSE_MEANING_ENRICH_MODEL", "some/strong-model")
    meaning_jobs._ENRICH_CLIENT = None
    meaning_jobs.enrich_client("postgresql://x", types.SimpleNamespace(model="other"))
    assert seen == {"backend": "openrouter", "model": "some/strong-model"}
