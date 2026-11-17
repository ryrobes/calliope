from __future__ import annotations

import os
import sys
import threading
from types import SimpleNamespace
import uuid
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bench"))
sys.path.insert(0, str(REPO_ROOT / "services" / "calliope"))

import business_topology_jobs as jobs  # noqa: E402


class _FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...] | None]] = []
        self.commits = 0

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[Any, ...] | None = None):
        self.executed.append((statement, parameters))
        return self

    def commit(self) -> None:
        self.commits += 1


class _FakeLease:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def assert_owned(self) -> None:
        assert self.started and not self.stopped


def test_model_routes_keep_judgment_on_the_workflow_model(monkeypatch):
    monkeypatch.delenv("WAREHOUSE_HERMES_URL", raising=False)
    monkeypatch.delenv("WAREHOUSE_HERMES_API_KEY", raising=False)
    monkeypatch.setenv(
        "WAREHOUSE_BUSINESS_TOPOLOGY_JUDGMENT_BACKEND", "frontier_backend"
    )
    monkeypatch.setenv(
        "WAREHOUSE_BUSINESS_TOPOLOGY_JUDGMENT_MODEL", "frontier-model"
    )

    assert jobs._model_routes({}) == {
        "assembly": {"backend": "clover_llm", "model": "calliope"},
        "judgment": {"backend": "frontier_backend", "model": "frontier-model"},
    }
    assert jobs._model_routes({"backend": "clover_llm", "model": "clover"}) == {
        "assembly": {"backend": "clover_llm", "model": "calliope"},
        "judgment": {"backend": "frontier_backend", "model": "frontier-model"},
    }


def test_model_routes_use_configured_hermes_for_judgment_only(monkeypatch):
    monkeypatch.delenv(
        "WAREHOUSE_BUSINESS_TOPOLOGY_JUDGMENT_BACKEND", raising=False
    )
    monkeypatch.delenv(
        "WAREHOUSE_BUSINESS_TOPOLOGY_JUDGMENT_MODEL", raising=False
    )
    monkeypatch.setenv("WAREHOUSE_HERMES_URL", "http://hermes:8642")
    monkeypatch.setenv("WAREHOUSE_HERMES_API_KEY", "test-key")

    assert jobs._model_routes({"backend": "clover_llm", "model": "clover"}) == {
        "assembly": {"backend": "clover_llm", "model": "calliope"},
        "judgment": {"backend": "hermes", "model": "configured"},
    }


@pytest.mark.parametrize("error,attempts,delay", [
    (RuntimeError("429 lanes_saturated"), 1, 300),
    (RuntimeError("503 service unavailable"), 3, 1200),
    (ConnectionError("connection reset"), 5, 3600),
    (RuntimeError("429 lanes_saturated"), 6, None),
    (ValueError("invalid proposal"), 1, None),
    (jobs.WorkflowLeaseLost("upstream lease lost"), 1, None),
])
def test_worker_defers_transient_failures_with_bounded_attempts(monkeypatch, error, attempts, delay):
    stop = threading.Event()
    monkeypatch.setattr(jobs, "_STOP", stop)
    monkeypatch.setattr(jobs, "_DEFER_MAX_ATTEMPTS", 6)
    monkeypatch.setattr(jobs, "_register_worker", lambda *_args: None)
    monkeypatch.setattr(jobs, "_claim", lambda *_args: {"run_id": "run-1", "attempts": attempts})
    deferred, failed = [], []

    def run(*_args, **_kwargs):
        stop.set()
        raise error

    monkeypatch.setattr(jobs, "_run_workflow", run)
    monkeypatch.setattr(jobs, "_defer", lambda *args: deferred.append(args))
    monkeypatch.setattr(jobs, "_fail", lambda *args: failed.append(args))
    jobs._worker_loop("unused")
    if delay is not None:
        assert len(deferred) == 1 and deferred[0][-1] == delay
        assert not failed
    else:
        assert not deferred and len(failed) == 1
        assert failed[0][-1]["attempts"] == attempts


def test_worker_records_failure_when_deferral_cannot_be_persisted(monkeypatch):
    stop = threading.Event()
    monkeypatch.setattr(jobs, "_STOP", stop)
    monkeypatch.setattr(jobs, "_register_worker", lambda *_args: None)
    monkeypatch.setattr(jobs, "_claim", lambda *_args: {"run_id": "run-1", "attempts": 1})
    failed = []

    def run(*_args, **_kwargs):
        stop.set()
        raise RuntimeError("429 lanes_saturated")

    def defer(*_args):
        raise RuntimeError("SQL deferral unavailable")

    monkeypatch.setattr(jobs, "_run_workflow", run)
    monkeypatch.setattr(jobs, "_defer", defer)
    monkeypatch.setattr(jobs, "_fail", lambda *args: failed.append(args))
    jobs._worker_loop("unused")
    assert len(failed) == 1 and "429 lanes_saturated" in failed[0][-2]
    assert failed[0][-1]["transient"] is True


def test_hermes_response_content_reads_agent_chat_envelopes():
    assert jobs._hermes_response_content({
        "message": {"content": [{"type": "text", "text": '{"ok":true}'}]},
        "model": "ignored-as-content",
    }) == '{"ok":true}'
    assert jobs._model_routes(
        {
            "backend": "clover_llm",
            "model": "clover",
            "assembly_backend": "private_bulk",
            "assembly_model": "private-model",
            "judgment_backend": "frontier",
            "judgment_model": "frontier-model",
        }
    ) == {
        "assembly": {"backend": "private_bulk", "model": "private-model"},
        "judgment": {"backend": "frontier", "model": "frontier-model"},
    }


def test_hermes_client_receipts_resolve_the_live_api_model(monkeypatch):
    monkeypatch.setenv("WAREHOUSE_HERMES_URL", "http://hermes:8642")
    monkeypatch.setenv("WAREHOUSE_HERMES_API_KEY", "test-key")
    monkeypatch.setenv("WAREHOUSE_HERMES_CONTROL_URL", "http://hermes:8643")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "config_yaml": (
                    "model:\n"
                    "  default: stealth/ox-alpha\n"
                    "  provider: openrouter\n"
                    "platforms:\n"
                    "  api_server:\n"
                    "    extra:\n"
                    "      model_name: stealth/ox-alpha\n"
                )
            }

    seen = {}

    def get(url, *, headers, timeout):
        seen.update(url=url, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(get=get, Timeout=lambda *args, **kwargs: (args, kwargs)),
    )
    client = jobs.HermesChatClient.from_environment(request_user="worker")
    assert client.model == "stealth/ox-alpha"
    assert client.provider == "openrouter"
    assert seen["url"] == "http://hermes:8643/v1/config"
    assert seen["headers"] == {"Authorization": "Bearer test-key"}


def test_hutch_client_accepts_managed_allowlist_and_prefix_routes(monkeypatch):
    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _statement, _parameters=None):
            return None

        def fetchone(self):
            return (
                "https://clover.example/v1/chat/completions",
                "openai_chat",
                "TEST_CLOVER_TOKEN",
                {
                    "model": "clover",
                    "model_aliases": ["gemma4"],
                    "model_allowlist": ["clover", "calliope"],
                    "model_prefix_allowlist": ["*", "calliope*"],
                },
            )

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def cursor(self):
            return _Cursor()

    monkeypatch.setattr(jobs.psycopg, "connect", lambda _dsn: _Connection())
    monkeypatch.setenv("TEST_CLOVER_TOKEN", "secret")

    assert jobs.HutchChatClient.from_postgres("unused", model="calliope").model == (
        "calliope"
    )
    assert jobs.HutchChatClient.from_postgres(
        "unused", model="calliope-high"
    ).model == "calliope-high"
    assert jobs.HutchChatClient.from_postgres("unused", model="gemma4").model == (
        "gemma4"
    )
    with pytest.raises(RuntimeError, match="not registered"):
        jobs.HutchChatClient.from_postgres("unused", model="openai/gpt-5.6")


def test_optional_overlap_degrades_only_statement_timeouts(monkeypatch):
    corpus = {
        "populations": [
            {
                "population_id": f"source:field-{index}",
                "packet": {
                    "source": {"kind": "postgres_relation"},
                    "population": {"kind": "field"},
                },
            }
            for index in range(3)
        ]
    }
    semantic_map = {
        jobs.pair_key("source:field-0", "source:field-1"): {
            "embedding_similarity": 0.9
        }
    }
    captured: dict[str, Any] = {}

    def timeout(*_args: object, **kwargs: object):
        captured.update(kwargs)
        raise jobs.psycopg.errors.QueryCanceled(
            "canceling statement due to statement timeout"
        )

    monkeypatch.setenv(
        "WAREHOUSE_BUSINESS_TOPOLOGY_GLOBAL_OVERLAP_POPULATIONS", "2"
    )
    monkeypatch.setattr(jobs, "_connect", lambda *_args, **_kwargs: _FakeConnection())
    monkeypatch.setattr(jobs, "extract_postgres_overlap_shadow", timeout)

    evidence, audit = jobs._optional_overlap_evidence(
        "postgresql://unused", corpus, semantic_map, sample_rows=64
    )

    assert evidence == []
    assert audit["status"] == "degraded_statement_timeout"
    assert audit["global_scan_skip_reason"] == "population_budget"
    assert captured["include_global_scan"] is False
    assert captured["probe_batch_size"] == 100

    def bad_contract(*_args: object, **_kwargs: object):
        raise ValueError("bad contract")

    monkeypatch.setattr(jobs, "extract_postgres_overlap_shadow", bad_contract)
    with pytest.raises(ValueError, match="bad contract"):
        jobs._optional_overlap_evidence(
            "postgresql://unused", corpus, semantic_map, sample_rows=64
        )


def test_workflow_selection_omits_optional_bridge_frontier(monkeypatch):
    calls: list[dict[str, Any]] = []

    def select(_plan: dict[str, Any], **kwargs: Any) -> list[str]:
        calls.append(kwargs)
        return ["selected"]

    monkeypatch.setattr(jobs, "select_work", select)
    plan = {
        "work_items": [
            {
                "work_id": "unit-a",
                "work_kind": "neighborhood_synthesis",
                "scope_id": "excavation:a",
            },
            {
                "work_id": "unit-b",
                "work_kind": "neighborhood_synthesis",
                "scope_id": "excavation:b",
            },
            {
                "work_id": "bridge-a-b",
                "work_kind": "bridge_synthesis",
                "scope_id": "link:a-b",
            },
        ]
    }

    assert jobs._select_work_for_workflow(plan, include_bridges=False) == ["selected"]
    assert calls.pop() == {"unit_ids": ["excavation:a", "excavation:b"]}

    assert jobs._select_work_for_workflow(plan, include_bridges=True) == ["selected"]
    assert calls.pop() == {"all_work": True}


def test_value_modeling_progress_counts_the_hidden_inference_phase(
    monkeypatch, tmp_path
):
    updates: list[dict[str, Any]] = []

    class ValueConnection:
        read_only = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, statement, _parameters=None):
            self.statement = statement
            return self

        def fetchone(self):
            if "to_regprocedure" in self.statement:
                return (True,)
            if "business_value_stage_graph" in self.statement:
                return ("value-graph-1",)
            raise AssertionError(self.statement)

        def fetchall(self):
            assert "business_topology_proposal_bundles" in self.statement
            return []

        def commit(self):
            return None

    class Lease:
        def assert_owned(self):
            return None

    def discover(_conn, _packet, _client, *, progress_callback):
        progress_callback(
            {"event": "inference_started", "attempt": 1, "attempt_limit": 2}
        )
        progress_callback(
            {
                "event": "inference_completed",
                "attempt": 1,
                "attempt_limit": 2,
                "latency_ms": 125.0,
                "model_version": "fixture/model-v1",
            }
        )
        progress_callback(
            {"event": "validation_completed", "attempt": 1, "attempt_limit": 2}
        )
        return {
            "schema_version": "rvbbit.business-value.graph.v1",
            "metrics": [{"metric_key": "revenue"}],
        }, {
            "generation_receipts": [{"model_version": "fixture/model-v1"}]
        }

    monkeypatch.setattr(jobs, "_connect", lambda *_args, **_kwargs: ValueConnection())
    monkeypatch.setattr(jobs, "_private_root", lambda: tmp_path)
    monkeypatch.setattr(
        jobs,
        "_update",
        lambda _dsn, _worker, _run, _phase, progress=None, **_kwargs: updates.append(
            dict(progress or {})
        ),
    )
    monkeypatch.setattr(
        jobs,
        "load_value_domain_planning_packet",
        lambda *_args, **_kwargs: {"relations": [
            {"relation": "sales.orders"},
            {"relation": "sales.customers"},
        ]},
    )
    monkeypatch.setattr(
        jobs,
        "discover_value_domains",
        lambda _packet, _client, *, progress_callback: (
            progress_callback({
                "event": "planning_started", "attempt": 1, "attempt_limit": 2,
            })
            or progress_callback({
                "event": "planning_completed", "attempt": 1, "attempt_limit": 2,
                "latency_ms": 50.0, "model_version": "fixture/model-v1",
            })
            or ({
                "schema_version": "rvbbit.business-value.domain-plan.v3",
                "domains": [{
                    "domain_key": "commerce",
                    "name": "Commerce",
                    "summary": "Selling products.",
                    "concepts": [],
                    "processes": [],
                    "systems": [],
                }],
                "systems": [{
                    "domain_key": "commerce",
                    "domain_name": "Commerce",
                    "domain_summary": "Selling products.",
                    "system_key": "sales_activity",
                    "name": "Sales activity",
                    "summary": "Orders and customers.",
                    "relationship_mode": "joined",
                    "relations": ["sales.orders", "sales.customers"],
                    "memberships": [],
                    "relationships": [],
                    "concepts": [],
                    "processes": [],
                }],
                "unassigned_relations": [],
            }, {
                "generation_receipts": [{"model_version": "fixture/model-v1"}],
            })
        ),
    )
    monkeypatch.setattr(
        jobs,
        "load_value_system_packet",
        lambda *_args, **_kwargs: {"domain_key": "sales"},
    )
    monkeypatch.setattr(jobs, "discover_value_graph", discover)

    result = jobs._discover_and_stage_value_graphs(
        "postgresql://unused",
        "worker-1",
        str(uuid.uuid4()),
        ["sales.orders", "sales.customers"],
        object(),
        Lease(),
        lease_seconds=300,
        proposed_by="analyst@example.com",
        run_root=tmp_path,
        prior_llm_attempts=5,
    )

    assert result["llm_attempts"] == 2
    assert result["staged"] == 1
    started = next(
        update
        for update in updates
        if update.get("current_value_domain_state") == "inference_started"
    )
    assert started["current_value_domain"] == "sales_activity"
    assert started["current_value_domain_index"] == 1
    assert started["value_modeling_llm_attempts"] == 2
    assert started["llm_attempts"] == 7
    completed = next(
        update
        for update in updates
        if update.get("current_value_domain_state") == "inference_completed"
    )
    assert completed["current_value_domain_latency_ms"] == 125.0
    assert completed["current_value_domain_model_version"] == "fixture/model-v1"
    assert updates[-1]["current_value_domain_state"] == "finished"
    assert updates[-1]["family_consolidation_state"] == "finished"
    assert updates[-1]["family_consolidation_candidates"] == 0


def test_full_workflow_orchestration_persists_private_resume_points(monkeypatch, tmp_path):
    run_id = str(uuid.uuid4())
    bundle_id = str(uuid.uuid4())
    connection = _FakeConnection()
    phase_updates: list[tuple[str, dict[str, Any]]] = []
    execution_routes: dict[str, Any] = {}
    constructed_clients: list[tuple[str, str, object]] = []
    creation_calls = {
        "extract": 0,
        "embed": 0,
        "neighborhoods": 0,
        "semantic": 0,
        "overlap": 0,
        "plan": 0,
    }

    corpus = {
        "schema_version": "rvbbit.business-topology.eval-corpus.v1",
        "corpus_id": f"business-topology-{run_id}",
        "populations": [
            {"population_id": "source:context"},
            {"population_id": "source:id"},
            {"population_id": "source:name"},
        ],
    }
    neighborhoods = {
        "neighborhoods": [{"neighborhood_id": "area-1"}],
        "summary": {"singleton_sources": 1, "bridges": 0},
    }
    semantic = [
        {
            "left_population_id": "source:id",
            "right_population_id": "source:name",
            "local_evidence": {"embedding_similarity": 0.72},
        }
    ]
    overlap = [
        {
            "left_population_id": "source:id",
            "right_population_id": "source:name",
            "local_evidence": {"shared_fingerprint_count": 3},
        }
    ]
    plan = {
        "schema_version": "rvbbit.business-topology.excavation-plan.v1",
        "summary": {"excavation_units": 1, "boundary_links": 0},
        "work_items": [
            {"work_id": "motif-1", "work_kind": "source_motifs"},
            {
                "work_id": "synthesis-1",
                "work_kind": "neighborhood_synthesis",
                "scope_id": "area-1",
            },
        ],
    }

    def extract(*_args: object, **_kwargs: object):
        creation_calls["extract"] += 1
        return corpus, {"rolled_back": True}

    def embed(*_args: object, **_kwargs: object):
        creation_calls["embed"] += 1
        return [{"population_id": "source:context", "vector": [1.0, 0.0]}], {
            "failures": []
        }

    def find_neighborhoods(*_args: object, **_kwargs: object):
        creation_calls["neighborhoods"] += 1
        return neighborhoods

    def build_semantic(*_args: object, **_kwargs: object):
        creation_calls["semantic"] += 1
        return semantic

    def extract_overlap(*_args: object, **_kwargs: object):
        creation_calls["overlap"] += 1
        return overlap, {"rolled_back": True}

    def build_plan(*_args: object, **_kwargs: object):
        creation_calls["plan"] += 1
        return plan

    def execute(
        _plan: dict[str, Any],
        selected: list[str],
        *,
        progress_callback: Any,
        **_kwargs: object,
    ) -> dict[str, Any]:
        execution_routes.update(
            {
                "default": _kwargs["client"],
                "by_work_kind": _kwargs["clients_by_work_kind"],
            }
        )
        progress_callback(
            {
                "event": "started",
                "total_work_items": len(selected),
                "completed_work_items": 0,
            }
        )
        progress_callback(
            {
                "event": "completed",
                "work_id": selected[-1],
                "work_kind": "neighborhood_synthesis",
                "completed_work_items": len(selected),
                "hutch_llm_attempts": 2,
            }
        )
        progress_callback(
            {
                "event": "finished",
                "completed_work_items": len(selected),
                "hutch_llm_attempts": 2,
            }
        )
        return {"hutch_llm_attempts": 2, "local_correspondence_calls": 0}

    monkeypatch.setattr(jobs, "_private_root", lambda: tmp_path)
    monkeypatch.setattr(jobs, "_connect", lambda *_args, **_kwargs: connection)
    monkeypatch.setattr(jobs, "_LeaseKeeper", _FakeLease)
    monkeypatch.setattr(
        jobs,
        "_update",
        lambda _dsn, _worker, _run, phase, progress=None, **_kwargs: phase_updates.append(
            (phase, dict(progress or {}))
        ),
    )
    monkeypatch.setattr(jobs, "extract_postgres_shadow_corpus", extract)
    monkeypatch.setattr(jobs, "_embedding_rows", embed)
    monkeypatch.setattr(jobs, "propose_source_neighborhoods", find_neighborhoods)
    monkeypatch.setattr(jobs, "build_embedding_evidence", build_semantic)
    monkeypatch.setattr(jobs, "_optional_overlap_evidence", extract_overlap)
    monkeypatch.setattr(jobs, "build_excavation_plan", build_plan)
    monkeypatch.setattr(jobs, "select_work", lambda *_args, **_kwargs: ["motif-1", "synthesis-1"])
    monkeypatch.setattr(
        jobs,
        "execution_preview",
        lambda *_args, **_kwargs: {"generative_calls_before_repairs": 2},
    )
    monkeypatch.setattr(jobs, "execute_plan", execute)
    monkeypatch.setattr(
        jobs,
        "stage_execution_bundles",
        lambda *_args, **_kwargs: {"staged": 1, "bundle_ids": [bundle_id]},
    )
    monkeypatch.setattr(
        jobs,
        "_discover_and_stage_value_graphs",
        lambda *_args, **_kwargs: {
            "staged": 1,
            "metrics": 2,
            "graph_ids": ["value-graph-1"],
            "errors": [],
            "llm_attempts": 1,
        },
    )

    def make_client(_cls: object, _dsn: str, **kwargs: object) -> object:
        client = object()
        constructed_clients.append(
            (str(kwargs["backend_name"]), str(kwargs["model"]), client)
        )
        return client

    monkeypatch.setattr(
        jobs.HutchChatClient, "from_postgres", classmethod(make_client)
    )
    job = {
        "run_id": run_id,
        "requested_by": "archaeologist@example.com",
        "parameters": {
            "schema_version": "rvbbit.business-topology.workflow-parameters.v1",
            "corpus_id": f"business-topology-{run_id}",
            "relations": ["public.sample_business_data"],
            "sample_rows": 64,
            "max_work_items": 10,
            "max_llm_calls": 4,
            "backend": "clover_llm",
            "model": "calliope",
            "policy": {},
        },
    }

    first = jobs._run_workflow("postgresql://unused", "worker-1", job, lease_seconds=300)
    assert first["bundles_staged"] == 1
    assert first["bundle_ids"] == [bundle_id]
    assert first["value_graphs_staged"] == 1
    assert first["value_metrics_staged"] == 2
    assert first["primary_llm_attempts"] == 2
    assert first["value_modeling_llm_attempts"] == 1
    assert first["llm_attempts"] == 3
    assert first["materialized_topology"] is False
    assert first["submitted_proposals"] is True
    assert first["completed_work_items"] == 2
    assert [(backend, model) for backend, model, _client in constructed_clients] == [
        ("clover_llm", "calliope-luna"),
        ("clover_llm", "calliope"),
    ]
    assert execution_routes["default"] is constructed_clients[0][2]
    assert execution_routes["by_work_kind"] == {
        "source_motifs": constructed_clients[1][2]
    }
    assert "executing" in [phase for phase, _progress in phase_updates]
    assert [phase for phase, _progress in phase_updates][-1] == "staging"
    assert creation_calls == {
        "extract": 1,
        "embed": 2,
        "neighborhoods": 1,
        "semantic": 1,
        "overlap": 1,
        "plan": 1,
    }

    run_root = tmp_path / run_id
    expected_artifacts = {
        "workflow.json",
        "corpus.private.json",
        "context-vectors.private.json",
        "neighborhoods.private.json",
        "field-vectors.private.json",
        "semantic-evidence.private.json",
        "overlap-evidence.private.json",
        "overlap-audit.private.json",
        "excavation-plan.private.json",
    }
    assert expected_artifacts <= {path.name for path in run_root.iterdir()}
    assert all(
        oct((run_root / name).stat().st_mode & 0o777) == "0o600"
        for name in expected_artifacts
    )
    assert oct(run_root.stat().st_mode & 0o777) == "0o700"

    # A reclaimed run reuses every expensive private checkpoint. The execution
    # layer is independently resumable and decides which work receipts remain.
    second = jobs._run_workflow("postgresql://unused", "worker-2", job, lease_seconds=300)
    assert second["bundles_staged"] == 1
    assert creation_calls == {
        "extract": 1,
        "embed": 2,
        "neighborhoods": 1,
        "semantic": 1,
        "overlap": 1,
        "plan": 1,
    }
    assert connection.commits == 2
    assert any("workflow_run_id" in statement for statement, _params in connection.executed)

    # The test never relies on permissive process umasks for private artifacts.
    assert os.access(run_root / "workflow.json", os.R_OK)
