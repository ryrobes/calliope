"""Durable Business Topology excavation worker for the Warehouse appliance.

PostgreSQL is the control plane: DataRabbit inserts one bounded workflow row,
this standing worker leases it, and every visible phase/progress update is
written back to SQL.  Private intermediate artifacts live in Warehouse's
durable data volume and are resumable after a process/container restart.

The worker stages validated proposal bundles only.  It never promotes or
materializes governed Business Topology.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import psycopg
import yaml
from psycopg.rows import dict_row

# Local source checkouts keep the reusable evaluator under bench/.  The image
# installs that same package directly at /app/business_topology.
try:
    from business_topology.bundles import stage_execution_bundles
except ModuleNotFoundError:
    _repo_bench = Path(__file__).resolve().parents[2] / "bench"
    if _repo_bench.is_dir():
        sys.path.insert(0, str(_repo_bench))
    from business_topology.bundles import stage_execution_bundles

from business_topology.candidates import pair_key
from business_topology.domains import propose_source_neighborhoods
from business_topology.embedding import (
    build_embedding_evidence,
    embed_postgres_shadow,
    make_embedding_inputs,
)
from business_topology.excavation import build_excavation_plan
from business_topology.extract import (
    extract_postgres_overlap_shadow,
    extract_postgres_shadow_corpus,
)
from business_topology.value_graph import (
    FAMILY_CONSOLIDATION_SCHEMA_VERSION,
    VALUE_DOMAIN_PLAN_SCHEMA_VERSION,
    VALUE_GRAPH_SCHEMA_VERSION,
    discover_family_consolidations,
    discover_value_domains,
    discover_value_graph,
    evaluate_value_graph,
    load_value_domain_planning_packet,
    load_value_system_packet,
)
from business_topology.worker import (
    WORKER_VERSION as EXCAVATION_WORKER_VERSION,
)
from business_topology.worker import (
    HutchChatClient,
    execute_plan,
    execution_preview,
    select_work,
)

WORKFLOW_WORKER_VERSION = "business-topology-appliance-worker-v8"
_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.Lock()
_STOP = threading.Event()
_MIGRATION_MISSING_LOGGED = False


class WorkflowLeaseLost(RuntimeError):
    """The run was cancelled or is no longer owned by this worker."""


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _model_routes(params: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Keep structural assembly distinct from business judgment.

    ``calliope`` is the inexpensive assembly contract on the hosted roster;
    judgment defaults to the stronger ``calliope-luna``. Installations may
    repoint either route by env without teaching workflows a
    deployment-specific model name. (The old ``clover`` fallback was not a
    hosted model name and failed every non-Hermes run with
    "model 'clover' is not registered".)
    """

    hermes_configured = bool(
        os.environ.get("WAREHOUSE_HERMES_URL", "").strip()
        and os.environ.get("WAREHOUSE_HERMES_API_KEY", "").strip()
    )
    judgment_backend = str(
        params.get("judgment_backend")
        or os.environ.get(
            "WAREHOUSE_BUSINESS_TOPOLOGY_JUDGMENT_BACKEND", ""
        ).strip()
        or ("hermes" if hermes_configured else "clover_llm")
    )
    judgment = {
        "backend": judgment_backend,
        "model": str(
            params.get("judgment_model")
            or os.environ.get(
                "WAREHOUSE_BUSINESS_TOPOLOGY_JUDGMENT_MODEL", ""
            ).strip()
            or ("configured" if judgment_backend == "hermes" else "calliope-luna")
        ),
    }
    assembly = {
        "backend": str(
            params.get("assembly_backend")
            or os.environ.get(
                "WAREHOUSE_BUSINESS_TOPOLOGY_ASSEMBLY_BACKEND", ""
            ).strip()
            or "clover_llm"
        ),
        "model": str(
            params.get("assembly_model")
            or os.environ.get("WAREHOUSE_BUSINESS_TOPOLOGY_ASSEMBLY_MODEL", "").strip()
            or "calliope"
        ),
    }
    return {"assembly": assembly, "judgment": judgment}


_DIRECT_JUDGMENT_BACKENDS = {
    # Hermes provider name -> registered rvbbit.backends row (transport openai_chat)
    "openrouter": "openrouter",
}


def _direct_judgment_client(
    dsn: str,
    *,
    provider: str,
    model: str,
    request_user: str,
    timeout_seconds: float,
) -> "HutchChatClient | None":
    """Resolve Hermes' configured judgment model to a tool-less direct call.

    Opt out with ``WAREHOUSE_BUSINESS_TOPOLOGY_JUDGMENT_TRANSPORT=hermes``.
    Returns None when the provider has no registered direct backend, the
    model is unresolved, or the backend has no usable token here.
    """

    transport = os.environ.get("WAREHOUSE_BUSINESS_TOPOLOGY_JUDGMENT_TRANSPORT", "").strip().lower()
    if transport == "hermes":
        return None
    backend_name = _DIRECT_JUDGMENT_BACKENDS.get(str(provider or "").strip().lower())
    resolved_model = str(model or "").strip()
    if not backend_name or not resolved_model or resolved_model == "configured":
        return None
    try:
        client = HutchChatClient.from_postgres(
            dsn,
            backend_name=backend_name,
            model=resolved_model,
            request_user=request_user,
            timeout_seconds=timeout_seconds,
            transport_attempts=3,
        )
    except Exception as exc:  # noqa: BLE001 - fall back to the Hermes session
        print(
            f"business topology judgment: direct {backend_name!r} route unavailable "
            f"({type(exc).__name__}: {exc}); using the Hermes session",
            file=sys.stderr,
        )
        return None
    print(
        f"business topology judgment: direct {backend_name!r} route, model={resolved_model} "
        "(no tools, no memory)",
        file=sys.stderr,
    )
    return client


def _hermes_response_content(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(
            _hermes_response_content(item) for item in value
        ).strip()
    if not isinstance(value, Mapping):
        return str(value or "").strip()
    message = value.get("message")
    if isinstance(message, Mapping):
        content = _hermes_response_content(message.get("content"))
        if content:
            return content
    if "text" in value and len(value) <= 4:
        content = _hermes_response_content(value.get("text"))
        if content:
            return content
    for key in ("content", "text", "response", "result"):
        content = _hermes_response_content(value.get(key))
        if content:
            return content
    return ""


@dataclass
class HermesChatClient:
    """Strong-model judgment through the appliance's configured Hermes lane."""

    endpoint_url: str
    token: str
    request_user: str = "business-topology-worker"
    timeout_seconds: float = 900.0
    backend_name: str = "hermes"
    model: str = "configured"
    provider: str = ""

    @classmethod
    def from_environment(
        cls,
        *,
        request_user: str,
        timeout_seconds: float = 900.0,
    ) -> "HermesChatClient":
        import httpx

        endpoint = os.environ.get("WAREHOUSE_HERMES_URL", "").strip().rstrip("/")
        token = os.environ.get("WAREHOUSE_HERMES_API_KEY", "").strip()
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("WAREHOUSE_HERMES_URL is not configured for topology judgment")
        if not token:
            raise RuntimeError("WAREHOUSE_HERMES_API_KEY is not configured for topology judgment")
        resolved_model = "configured"
        resolved_provider = ""
        control_url = os.environ.get(
            "WAREHOUSE_HERMES_CONTROL_URL", "http://hermes:8643"
        ).strip().rstrip("/")
        control_key = os.environ.get("HERMES_CONTROL_KEY", "").strip() or token
        try:
            current = httpx.get(
                f"{control_url}/v1/config",
                headers={"Authorization": f"Bearer {control_key}"},
                timeout=httpx.Timeout(10.0, connect=4.0),
            )
            current.raise_for_status()
            document = current.json()
            payload = yaml.safe_load(str(document.get("config_yaml") or "")) or {}
            model_block = payload.get("model") if isinstance(payload, Mapping) else None
            platforms = payload.get("platforms") if isinstance(payload, Mapping) else None
            api_server = platforms.get("api_server") if isinstance(platforms, Mapping) else None
            extra = api_server.get("extra") if isinstance(api_server, Mapping) else None
            if isinstance(model_block, Mapping):
                resolved_provider = str(model_block.get("provider") or "").strip()
                resolved_model = str(model_block.get("default") or "").strip() or resolved_model
            if isinstance(extra, Mapping):
                # This exact request override wins if a hand-edited config ever
                # drifts from the model block again.
                resolved_model = str(extra.get("model_name") or "").strip() or resolved_model
        except Exception:
            # Receipt enrichment is not on the inference data path. A
            # transient control outage must not block a healthy Hermes lane.
            pass
        return cls(
            endpoint_url=endpoint,
            token=token,
            request_user=request_user,
            timeout_seconds=timeout_seconds,
            model=resolved_model,
            provider=resolved_provider,
        )

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        import httpx

        session_id = f"business_topology_{int(time.time())}_{os.getpid()}_{hashlib.sha256(user.encode()).hexdigest()[:10]}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        started = time.perf_counter()
        result: dict[str, Any] = {}
        with httpx.Client(
            timeout=httpx.Timeout(self.timeout_seconds, connect=8.0)
        ) as client:
            created = client.post(
                f"{self.endpoint_url}/api/sessions",
                headers=headers,
                json={"id": session_id, "source": "api_server"},
            )
            if created.status_code >= 400:
                raise RuntimeError(
                    f"Hermes topology session creation failed ({created.status_code}): "
                    f"{created.text[:600].replace(self.token, '<redacted>')}"
                )
            try:
                response = client.post(
                    f"{self.endpoint_url}/api/sessions/{quote(session_id, safe='')}/chat",
                    headers=headers,
                    json={
                        "message": [{"type": "text", "text": user}],
                        "instructions": (
                            f"{system}\n\nReturn only the requested JSON. Keep the "
                            f"response within approximately {max(max_tokens, 16)} output tokens."
                        ),
                    },
                )
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"Hermes topology judgment failed ({response.status_code}): "
                        f"{response.text[:800].replace(self.token, '<redacted>')}"
                    )
                raw = response.json()
                result = raw if isinstance(raw, dict) else {"result": raw}
            finally:
                try:
                    client.delete(
                        f"{self.endpoint_url}/api/sessions/{quote(session_id, safe='')}",
                        headers=headers,
                    )
                except Exception:
                    pass
        content = _hermes_response_content(result)
        if not content:
            raise RuntimeError("Hermes returned empty topology judgment content")
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
        resolved_model = str(result.get("model") or self.model)
        return content, {
            "schema_version": "rvbbit.business-topology.hermes-receipt.v1",
            "backend": self.backend_name,
            "service_provider": self.provider or None,
            "service_model": resolved_model,
            "model_version": resolved_model,
            "provider_request_id": result.get("id"),
            "endpoint_host": urlparse(self.endpoint_url).netloc,
            "latency_ms": elapsed_ms,
            "usage": {
                key: usage[key]
                for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")
                if key in usage
            },
            "prompt_sha256": _sha256({"system": system, "user": user}),
            "response_sha256": hashlib.sha256(content.encode()).hexdigest(),
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _private_root() -> Path:
    path = Path(
        os.environ.get(
            "WAREHOUSE_BUSINESS_TOPOLOGY_DIR",
            "/app/data/business-topology",
        )
    )
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _semantic_cache_projection(value: Any) -> Any:
    """Remove volatile row statistics while preserving semantic source shape."""

    if isinstance(value, Mapping):
        return {
            str(key): _semantic_cache_projection(item)
            for key, item in value.items()
            if key not in {"estimated_rows", "distinct_estimate", "null_fraction"}
        }
    if isinstance(value, list):
        return [_semantic_cache_projection(item) for item in value]
    return value


def _semantic_cache_path(kind: str, contract: str, packet: Mapping[str, Any]) -> Path:
    key = _sha256({
        "kind": kind,
        "contract": contract,
        "packet": _semantic_cache_projection(packet),
    })
    path = _private_root() / "semantic-cache" / kind / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    return path


_PRIOR_EVIDENCE_RECEIPT_KEYS = {
    "graph_id",
    "observed_at",
    "proposed_at",
    "proposed_by",
    "relationship_id",
    "source_family_id",
    "source_graph_id",
    "target_family_id",
    "target_graph_id",
    "workflow_run_id",
}

_FAMILY_DECISION_CACHE_KEYS = {
    "adjudication_version",
    "proposed_business_domain_key",
    "relationship_kind",
    "review_state",
    "source_business_domain_key",
    "source_value_system_key",
    "target_business_domain_key",
    "target_value_system_key",
}


def _domain_plan_cache_projection(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Keep prior meaning in the plan key without keying on receipt identity.

    Re-processing creates fresh graph, family, workflow, and relationship row IDs
    even when the approved semantics are unchanged.  Prior evidence is also read
    newest-first, so its order changes after every successful proposal.  Neither
    should spend another model call.  Review state, complete taxonomy contents,
    relationship decisions, confidence, rationale, and supporting evidence remain
    in the key so a real semantic change still invalidates the cached plan.
    """

    projected = _semantic_cache_projection(packet)
    raw_evidence = projected.get("prior_semantic_evidence", [])
    stable_evidence: list[dict[str, Any]] = []
    for raw in raw_evidence if isinstance(raw_evidence, list) else []:
        if not isinstance(raw, Mapping):
            continue
        item = {
            str(key): value
            for key, value in raw.items()
            if key not in _PRIOR_EVIDENCE_RECEIPT_KEYS
        }
        censuses = item.get("dimension_censuses")
        if isinstance(censuses, list):
            item["dimension_censuses"] = sorted(
                (
                    {
                        str(key): value
                        for key, value in census.items()
                        if key not in _PRIOR_EVIDENCE_RECEIPT_KEYS
                    }
                    for census in censuses
                    if isinstance(census, Mapping)
                ),
                key=lambda census: (
                    str(census.get("metric_key") or ""),
                    str(census.get("dimension_key") or ""),
                ),
            )
        relationships = item.get("family_relationships")
        if isinstance(relationships, list):
            decisions = {
                _canonical_json({
                    str(key): value
                    for key, value in relationship.items()
                    if key in _FAMILY_DECISION_CACHE_KEYS
                }): {
                    str(key): value
                    for key, value in relationship.items()
                    if key in _FAMILY_DECISION_CACHE_KEYS
                }
                for relationship in relationships
                if isinstance(relationship, Mapping)
            }
            item["family_relationships"] = [
                decisions[key] for key in sorted(decisions)
            ]
        stable_evidence.append(item)
    projected["prior_semantic_evidence"] = sorted(
        stable_evidence,
        key=lambda item: (
            str(item.get("value_system_key") or ""),
            tuple(sorted(str(value) for value in item.get("source_relations", []))),
        ),
    )
    return projected


def _load_compatible_domain_plan_cache(
    packet: Mapping[str, Any],
    *,
    candidate_limit: int = 128,
) -> dict[str, Any] | None:
    """Recover a semantically identical plan after cache-key format changes."""

    expected = _sha256(_domain_plan_cache_projection(packet))
    candidates: list[Path] = []
    for path in _private_root().glob("*/value-domain-plan.private.json"):
        try:
            if path.is_file():
                candidates.append(path)
        except OSError:
            continue
    candidates.sort(
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    for path in candidates[:max(1, candidate_limit)]:
        try:
            envelope = _read_json(path)
            if not isinstance(envelope, Mapping):
                continue
            prior_packet = envelope.get("planning_packet")
            if (
                not isinstance(prior_packet, Mapping)
                or not isinstance(envelope.get("plan"), Mapping)
                or not isinstance(envelope.get("receipt"), Mapping)
            ):
                continue
            if _sha256(_domain_plan_cache_projection(prior_packet)) != expected:
                continue
            return {
                "planning_packet_fingerprint": expected,
                "plan": dict(envelope["plan"]),
                "receipt": dict(envelope["receipt"]),
                "compatible_run_plan": str(path.parent.name),
            }
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return None


def _value_graph_cache_projection(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Key metric synthesis by stable structure plus the approved semantic plan.

    Catalog prose, topology summaries, planner statistics, and derived candidate
    scores may change after a context crawl even when the tables and semantic
    system are unchanged. They are useful prompt evidence, but must not force a
    duplicate inference after the strong model has already fixed the meaning.
    """

    relations: list[dict[str, Any]] = []
    for relation in packet.get("relations", []):
        if not isinstance(relation, Mapping):
            continue
        columns = [
            {
                "name": str(column.get("name") or ""),
                "data_type": str(column.get("data_type") or ""),
                "type_family": str(column.get("type_family") or ""),
                "semantic_role": str(column.get("semantic_role") or ""),
                "measure_hint": bool(column.get("measure_hint")),
                "time_hint": bool(column.get("time_hint")),
                "key_hint": bool(column.get("key_hint")),
            }
            for column in relation.get("columns", [])
            if isinstance(column, Mapping)
        ]
        relations.append(
            {
                "relation": str(relation.get("relation") or ""),
                "columns": sorted(columns, key=lambda item: item["name"]),
            }
        )
    return {
        "schema_version": str(packet.get("schema_version") or ""),
        "domain_key": str(packet.get("domain_key") or ""),
        "relations": sorted(relations, key=lambda item: item["relation"]),
        "foreign_key_relationships": packet.get("foreign_key_relationships", []),
        "semantic_model": packet.get("semantic_model", {}),
        "prior_semantic_evidence": packet.get("prior_semantic_evidence", []),
    }


def _graph_semantic_model(graph: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "business_domain": graph.get("business_domain", {}),
        "value_system_key": str(
            graph.get("value_system_key") or graph.get("domain_key") or ""
        ),
        "value_system_name": str(graph.get("name") or ""),
        "value_system_summary": str(graph.get("summary") or ""),
        "relationship_mode": str(graph.get("relationship_mode") or ""),
        "concepts": graph.get("concepts", []),
        "processes": graph.get("processes", []),
        "source_memberships": graph.get("source_memberships", []),
        "semantic_relationships": graph.get("semantic_relationships", []),
        "analytical_questions": graph.get("analytical_questions", []),
        "gaps": graph.get("gaps", []),
    }


def _graph_matches_packet(graph: Mapping[str, Any], packet: Mapping[str, Any]) -> bool:
    expected_relations = sorted(
        str(relation.get("relation") or "")
        for relation in packet.get("relations", [])
        if isinstance(relation, Mapping)
    )
    return (
        sorted(str(value) for value in graph.get("source_relations", []))
        == expected_relations
        and _canonical_json(_graph_semantic_model(graph))
        == _canonical_json(packet.get("semantic_model", {}))
    )


def _packet_requires_semantic_reconsideration(packet: Mapping[str, Any]) -> bool:
    return any(
        bool(item.get("requires_review"))
        for item in packet.get("prior_semantic_evidence", [])
        if isinstance(item, Mapping)
    )


def _load_current_value_graph(
    conn: Any,
    packet: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Seed the durable file cache from a matching validated SQL proposal."""

    if _packet_requires_semantic_reconsideration(packet):
        return None

    try:
        rows = conn.execute(
            """
            SELECT graph,receipt
              FROM rvbbit.business_value_graph_current
             WHERE domain_key=%s
             ORDER BY usable DESC,updated_at DESC,graph_id DESC
             LIMIT 8
            """,
            (str(packet.get("domain_key") or ""),),
        ).fetchall()
    except Exception:  # pragma: no cover - older appliances lack the current view
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            rollback()
        return None
    for raw_graph, raw_receipt in rows:
        if not isinstance(raw_graph, Mapping) or not _graph_matches_packet(
            raw_graph, packet
        ):
            continue
        return dict(raw_graph), (
            dict(raw_receipt) if isinstance(raw_receipt, Mapping) else {}
        )
    return None


def _load_prior_semantic_evidence(
    conn: Any,
    relations: Sequence[str],
) -> list[dict[str, Any]]:
    """Return compact, coverage-aware evidence from current Value Systems.

    Exact complete dictionaries may inform re-processing. Bounded samples keep
    their counts and coverage labels but deliberately withhold member values so
    a model cannot mistake a top sample for an exhaustive taxonomy.
    """

    if not relations:
        return []
    try:
        available = conn.execute(
            "SELECT to_regclass('rvbbit.business_semantic_dimension_census_current') IS NOT NULL "
            "AND to_regclass('rvbbit.business_semantic_dossiers') IS NOT NULL"
        ).fetchone()[0]
        if not available:
            return []
        rows = conn.execute(
            """
            SELECT current.graph_id::text,current.domain_key,current.name,
                   current.source_relations,current.graph->'business_domain',
                   dossier.needs_revision_count,dossier.needs_decision_count,
                   coalesce(jsonb_agg(census.census ORDER BY census.metric_key,census.dimension_key)
                       FILTER (WHERE census.census_id IS NOT NULL),'[]'::jsonb),
                   coalesce((
                       SELECT jsonb_agg(relationship.relationship
                                        ORDER BY relationship.confidence DESC,
                                                 relationship.relationship_id)
                         FROM rvbbit.business_semantic_family_relationship_current relationship
                        WHERE relationship.source_family_id=current.family_id
                           OR relationship.target_family_id=current.family_id
                   ),'[]'::jsonb)
              FROM rvbbit.business_value_graph_current current
              JOIN rvbbit.business_semantic_dossiers dossier USING (graph_id)
              LEFT JOIN rvbbit.business_semantic_dimension_census_current census USING (graph_id)
             WHERE current.source_relations && %s::text[]
             GROUP BY current.graph_id,current.domain_key,current.name,
                      current.source_relations,current.graph->'business_domain',
                      dossier.needs_revision_count,dossier.needs_decision_count,
                      current.family_id,current.updated_at
             ORDER BY current.updated_at DESC,current.graph_id
             LIMIT 24
            """,
            (list(relations),),
        ).fetchall()
    except Exception:
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            rollback()
        return []

    evidence: list[dict[str, Any]] = []
    for row in rows:
        raw_censuses = row[7] if isinstance(row[7], list) else []
        censuses: list[dict[str, Any]] = []
        taxonomy_changed = False
        for raw in raw_censuses:
            if not isinstance(raw, Mapping):
                continue
            complete = bool(
                raw.get("coverage_complete") and raw.get("member_list_complete")
            )
            changed = bool(raw.get("taxonomy_changed"))
            taxonomy_changed = taxonomy_changed or changed
            censuses.append(
                {
                    "metric_key": str(raw.get("metric_key") or "")[:80],
                    "dimension_key": str(raw.get("dimension_key") or "")[:80],
                    "coverage_mode": str(raw.get("coverage_mode") or "bounded_sample"),
                    "coverage_complete": bool(raw.get("coverage_complete")),
                    "member_list_complete": bool(raw.get("member_list_complete")),
                    "rows_observed": int(raw.get("rows_observed") or 0),
                    "member_count": int(raw.get("member_count") or 0),
                    "members": [
                        {
                            "value": str(member.get("value") or "")[:256],
                            "row_count": int(member.get("row_count") or 0),
                            "first_seen": member.get("first_seen"),
                            "last_seen": member.get("last_seen"),
                        }
                        for member in (raw.get("members") or [])
                        if complete and isinstance(member, Mapping)
                    ][:128],
                    "member_values_withheld": not complete,
                    "taxonomy_changed": changed,
                    "added_values": [str(value)[:256] for value in raw.get("added_values", [])][:64],
                    "removed_values": [str(value)[:256] for value in raw.get("removed_values", [])][:64],
                    "observed_at": raw.get("observed_at"),
                }
            )
        needs_revision = int(row[5] or 0)
        needs_decision = int(row[6] or 0)
        family_relationships = [
            dict(value) for value in (row[8] if isinstance(row[8], list) else [])
            if isinstance(value, Mapping)
        ]
        pending_family_review = any(
            str(value.get("review_state") or "proposed") == "proposed"
            and str(value.get("relationship_kind") or "")
                not in {"distinct_family", "abstain"}
            for value in family_relationships
        )
        evidence.append(
            {
                "graph_id": str(row[0]),
                "value_system_key": str(row[1]),
                "value_system_name": str(row[2]),
                "source_relations": [str(value) for value in (row[3] or [])],
                "business_domain": dict(row[4]) if isinstance(row[4], Mapping) else {},
                "needs_revision_count": needs_revision,
                "needs_decision_count": needs_decision,
                "requires_review": bool(
                    taxonomy_changed or needs_revision > 0 or needs_decision > 0
                    or pending_family_review
                ),
                "dimension_censuses": censuses,
                "family_relationships": family_relationships,
            }
        )
    return evidence


def _load_knowledge_loop_feedback(
    conn: Any,
    relations: Sequence[str],
) -> dict[str, Any]:
    """Return the human/usage feedback the Knowledge Loop has accumulated
    against value systems touching these relations.

    Corrections outrank data-shape inference: a deny-listed claim must not
    be re-asserted unless new evidence explicitly rebuts it, and demotion
    reasons are context the next excavation is required to respect.
    """

    if not relations:
        return {}
    try:
        available = conn.execute(
            "SELECT to_regclass('rvbbit.knowledge_loop_denylist') IS NOT NULL"
        ).fetchone()[0]
        if not available:
            return {}
        denied = conn.execute(
            """
            SELECT deny.subject_key,deny.predicate,deny.reason,
                   deny.created_at,proposal.domain_key,proposal.name
              FROM rvbbit.knowledge_loop_denylist deny
              LEFT JOIN rvbbit.business_value_graph_proposals proposal
                ON proposal.graph_id=deny.source_graph_id
             WHERE deny.rebutted_at IS NULL
               AND deny.family_id IN (
                   SELECT family_id FROM rvbbit.business_value_graph_proposals
                    WHERE source_relations && %s::text[] AND family_id IS NOT NULL)
             ORDER BY deny.created_at DESC
             LIMIT 40
            """,
            (list(relations),),
        ).fetchall()
        demotions = conn.execute(
            """
            SELECT event.graph_id::text,event.reason,event.created_at,
                   proposal.domain_key,proposal.name
              FROM rvbbit.knowledge_loop_events event
              JOIN rvbbit.business_value_graph_proposals proposal
                ON proposal.graph_id=event.graph_id
             WHERE event.kind='demoted'
               AND proposal.source_relations && %s::text[]
             ORDER BY event.created_at DESC
             LIMIT 12
            """,
            (list(relations),),
        ).fetchall()
        corrections = conn.execute(
            """
            SELECT proposal.graph_id::text,proposal.domain_key,proposal.name,
                   count(*)::integer AS signals,
                   max(receipt.created_at) AS last_signal_at
              FROM rvbbit.business_context_turn_receipts receipt
              JOIN rvbbit.business_value_graph_proposals proposal
                ON proposal.graph_id=ANY(receipt.used_graph_ids)
             WHERE receipt.feedback_signal IN ('possible_correction','possible_contradiction')
               AND proposal.source_relations && %s::text[]
             GROUP BY proposal.graph_id,proposal.domain_key,proposal.name
             ORDER BY max(receipt.created_at) DESC
             LIMIT 12
            """,
            (list(relations),),
        ).fetchall()
    except Exception:
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            rollback()
        return {}

    if not denied and not demotions and not corrections:
        return {}
    return {
        "denied_claims": [
            {
                "subject_key": str(row[0] or "")[:160],
                "predicate": str(row[1] or "")[:120],
                "reason": str(row[2] or "")[:300],
                "domain_key": str(row[4] or "")[:80],
                "value_system": str(row[5] or "")[:240],
            }
            for row in denied
        ],
        "demotions": [
            {
                "graph_id": str(row[0] or ""),
                "reason": str(row[1] or "")[:300],
                "domain_key": str(row[3] or "")[:80],
                "value_system": str(row[4] or "")[:240],
            }
            for row in demotions
        ],
        "correction_signals": [
            {
                "graph_id": str(row[0] or ""),
                "domain_key": str(row[1] or "")[:80],
                "value_system": str(row[2] or "")[:240],
                "signals": int(row[3] or 0),
            }
            for row in corrections
        ],
    }


_CONSOLIDATION_TOKEN_RE = re.compile(r"[a-z][a-z0-9]+")
_CONSOLIDATION_STOPWORDS = {
    "and", "for", "from", "how", "the", "this", "that", "with", "system",
    "value", "data", "current", "reported", "reporting", "public",
}


def _consolidation_tokens(*values: Any) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        tokens.update(
            token
            for token in _CONSOLIDATION_TOKEN_RE.findall(str(value or "").lower())
            if token not in _CONSOLIDATION_STOPWORDS and len(token) > 2
        )
    return tokens


def _set_overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return round(len(left & right) / len(left | right), 4)


def _compact_consolidation_graph(row: Mapping[str, Any]) -> dict[str, Any]:
    graph = row.get("graph")
    graph = dict(graph) if isinstance(graph, Mapping) else {}
    business_domain = graph.get("business_domain")
    business_domain = dict(business_domain) if isinstance(business_domain, Mapping) else {}
    concepts = [
        {
            "key": str(item.get("concept_key") or "")[:80],
            "name": str(item.get("name") or "")[:180],
            "kind": str(item.get("kind") or "")[:40],
        }
        for item in graph.get("concepts", [])
        if isinstance(item, Mapping)
    ][:32]
    processes = [
        {
            "key": str(item.get("process_key") or "")[:80],
            "name": str(item.get("name") or "")[:180],
            "concept_keys": [str(value)[:80] for value in item.get("concept_keys", [])][:16],
        }
        for item in graph.get("processes", [])
        if isinstance(item, Mapping)
    ][:24]
    metrics = [
        {
            "key": str(item.get("metric_key") or "")[:80],
            "name": str(item.get("name") or "")[:180],
            "kind": str(item.get("metric_kind") or "")[:40],
            "unit": str(item.get("unit") or "")[:40],
            "grain": str(item.get("grain") or "")[:300],
            "dimensions": [
                str(dimension.get("key") or "")[:80]
                for dimension in item.get("dimensions", [])
                if isinstance(dimension, Mapping)
            ][:24],
        }
        for item in graph.get("metrics", [])
        if isinstance(item, Mapping)
    ][:12]
    memberships = [
        {
            "relation": str(item.get("relation") or "")[:260],
            "role": str(item.get("role") or "")[:40],
            "concept_keys": [str(value)[:80] for value in item.get("concept_keys", [])][:16],
        }
        for item in graph.get("source_memberships", [])
        if isinstance(item, Mapping)
    ][:32]
    relationships = [
        {
            "relationship": str(item.get("relationship") or "")[:60],
            "left_relation": str(item.get("left_relation") or "")[:260] or None,
            "right_relation": str(item.get("right_relation") or "")[:260] or None,
            "subject_concept_key": str(item.get("subject_concept_key") or "")[:80] or None,
            "object_concept_key": str(item.get("object_concept_key") or "")[:80] or None,
        }
        for item in graph.get("semantic_relationships", [])
        if isinstance(item, Mapping)
    ][:32]
    return {
        "graph_id": str(row.get("graph_id") or ""),
        "family_id": str(row.get("family_id") or "") or None,
        "business_domain": {
            "key": str(
                business_domain.get("domain_key")
                or business_domain.get("key")
                or row.get("domain_key")
                or ""
            )[:80],
            "name": str(business_domain.get("name") or row.get("name") or "")[:180],
        },
        "value_system": {
            "key": str(graph.get("value_system_key") or row.get("domain_key") or "")[:80],
            "name": str(row.get("name") or graph.get("name") or "")[:180],
            "summary": str(row.get("summary") or graph.get("summary") or "")[:1600],
            "relationship_mode": str(graph.get("relationship_mode") or "")[:40],
        },
        "source_relations": sorted(str(value)[:260] for value in row.get("source_relations", [])),
        "concepts": concepts,
        "processes": processes,
        "memberships": memberships,
        "relationships": relationships,
        "metrics": metrics,
        "analytical_questions": [str(value)[:500] for value in graph.get("analytical_questions", [])][:12],
        "gaps": [str(value)[:500] for value in graph.get("gaps", [])][:12],
    }


def _consolidation_semantic_projection(graph: Mapping[str, Any]) -> dict[str, Any]:
    """Return stable family identity evidence for durable candidate keys.

    Generated prose, metric SQL/grain, confidence, and graph revision IDs can
    vary on every reconsideration without changing which two semantic families
    are being compared.  Fingerprinting those volatile fields turns a rerun
    into duplicate relationship proposals.  Structural membership and typed
    semantic edges still reopen adjudication when the modeled family actually
    changes.
    """
    business_domain = graph.get("business_domain")
    business_domain = business_domain if isinstance(business_domain, Mapping) else {}
    value_system = graph.get("value_system")
    value_system = value_system if isinstance(value_system, Mapping) else {}

    def keyed(values: Any, key: str) -> list[str]:
        return sorted({
            str(item.get(key) or "")
            for item in values or []
            if isinstance(item, Mapping) and item.get(key)
        })

    memberships = sorted(
        (
            str(item.get("relation") or ""),
            str(item.get("role") or ""),
            tuple(sorted(str(value) for value in item.get("concept_keys", []))),
        )
        for item in graph.get("memberships", [])
        if isinstance(item, Mapping) and item.get("relation")
    )
    relationships = sorted(
        (
            str(item.get("relationship") or ""),
            str(item.get("left_relation") or ""),
            str(item.get("right_relation") or ""),
            str(item.get("subject_concept_key") or ""),
            str(item.get("object_concept_key") or ""),
        )
        for item in graph.get("relationships", [])
        if isinstance(item, Mapping) and item.get("relationship")
    )
    return {
        "business_domain_key": str(business_domain.get("key") or ""),
        "value_system_key": str(value_system.get("key") or ""),
        "relationship_mode": str(value_system.get("relationship_mode") or ""),
        "source_relations": sorted(str(value) for value in graph.get("source_relations", [])),
        "concept_keys": keyed(graph.get("concepts"), "key"),
        "process_keys": keyed(graph.get("processes"), "key"),
        "memberships": memberships,
        "relationships": relationships,
    }


def _family_candidate(
    source: Mapping[str, Any],
    target: Mapping[str, Any],
) -> dict[str, Any] | None:
    source_relations = set(str(value) for value in source.get("source_relations", []))
    target_relations = set(str(value) for value in target.get("source_relations", []))
    if source_relations == target_relations:
        return None
    source_concepts = {
        str(item.get("key") or "") for item in source.get("concepts", [])
        if isinstance(item, Mapping) and item.get("key")
    }
    target_concepts = {
        str(item.get("key") or "") for item in target.get("concepts", [])
        if isinstance(item, Mapping) and item.get("key")
    }
    source_processes = {
        str(item.get("key") or "") for item in source.get("processes", [])
        if isinstance(item, Mapping) and item.get("key")
    }
    target_processes = {
        str(item.get("key") or "") for item in target.get("processes", [])
        if isinstance(item, Mapping) and item.get("key")
    }
    source_metrics = {
        str(item.get("key") or "") for item in source.get("metrics", [])
        if isinstance(item, Mapping) and item.get("key")
    }
    target_metrics = {
        str(item.get("key") or "") for item in target.get("metrics", [])
        if isinstance(item, Mapping) and item.get("key")
    }
    source_words = _consolidation_tokens(
        source.get("business_domain", {}).get("name"),
        source.get("value_system", {}).get("key"),
        source.get("value_system", {}).get("name"),
        source.get("value_system", {}).get("summary"),
        *(item.get("name") for item in source.get("concepts", []) if isinstance(item, Mapping)),
        *(item.get("name") for item in source.get("processes", []) if isinstance(item, Mapping)),
    )
    target_words = _consolidation_tokens(
        target.get("business_domain", {}).get("name"),
        target.get("value_system", {}).get("key"),
        target.get("value_system", {}).get("name"),
        target.get("value_system", {}).get("summary"),
        *(item.get("name") for item in target.get("concepts", []) if isinstance(item, Mapping)),
        *(item.get("name") for item in target.get("processes", []) if isinstance(item, Mapping)),
    )
    same_domain = bool(
        source.get("business_domain", {}).get("key")
        and source.get("business_domain", {}).get("key")
        == target.get("business_domain", {}).get("key")
    )
    source_overlap = _set_overlap(source_relations, target_relations)
    concept_overlap = _set_overlap(source_concepts, target_concepts)
    process_overlap = _set_overlap(source_processes, target_processes)
    metric_overlap = _set_overlap(source_metrics, target_metrics)
    semantic_overlap = _set_overlap(source_words, target_words)
    score = round(min(
        1.0,
        (0.34 if same_domain else 0.0)
        + 0.18 * source_overlap
        + 0.22 * concept_overlap
        + 0.10 * process_overlap
        + 0.06 * metric_overlap
        + 0.14 * semantic_overlap,
    ), 4)
    if score < 0.28 and source_overlap == 0:
        return None
    source_signature = _sha256(_consolidation_semantic_projection(source))
    target_signature = _sha256(_consolidation_semantic_projection(target))
    pair_signatures = sorted([source_signature, target_signature])
    fingerprint = _sha256({
        "contract": FAMILY_CONSOLIDATION_SCHEMA_VERSION,
        "semantic_pair": pair_signatures,
    })

    def derived_relations(values: set[str]) -> list[str]:
        return sorted(
            value for value in values
            if any(token in value.lower() for token in ("_dlt", ".dlt", "staging", "cubes."))
        )

    evidence = {
        "candidate_score": score,
        "same_business_domain": same_domain,
        "source_overlap": source_overlap,
        "shared_sources": sorted(source_relations & target_relations),
        "concept_overlap": concept_overlap,
        "shared_concepts": sorted(source_concepts & target_concepts),
        "process_overlap": process_overlap,
        "shared_processes": sorted(source_processes & target_processes),
        "metric_overlap": metric_overlap,
        "shared_metrics": sorted(source_metrics & target_metrics),
        "semantic_token_overlap": semantic_overlap,
        "shared_semantic_tokens": sorted(source_words & target_words)[:32],
        "source_derived_relations": derived_relations(source_relations),
        "target_derived_relations": derived_relations(target_relations),
    }
    return {
        "candidate_id": fingerprint[:24],
        "candidate_fingerprint": fingerprint,
        "target": dict(target),
        "evidence": evidence,
    }


def _load_family_consolidation_packets(
    conn: Any,
    graph_ids: Sequence[str],
    *,
    max_candidates_per_graph: int = 6,
    max_candidates_total: int = 32,
) -> list[dict[str, Any]]:
    if not graph_ids:
        return []
    rows = conn.execute(
        """
        SELECT proposal.graph_id::text,proposal.family_id::text,proposal.domain_key,
               proposal.name,proposal.summary,proposal.source_relations,proposal.graph,
               (proposal.graph_id=ANY(%s::uuid[])) AS staged
          FROM rvbbit.business_value_graph_proposals proposal
         WHERE proposal.graph_id=ANY(%s::uuid[])
            OR proposal.graph_id IN (
                SELECT graph_id FROM rvbbit.business_value_graph_current
            )
         ORDER BY staged DESC,proposal.updated_at DESC,proposal.graph_id
        """,
        (list(graph_ids), list(graph_ids)),
    ).fetchall()
    compact = [_compact_consolidation_graph(row) for row in rows]
    staged_ids = set(str(value) for value in graph_ids)
    sources = [item for item in compact if item["graph_id"] in staged_ids]
    # A staged graph is the candidate revision of its Value System for this
    # run.  Do not also offer that system's prior current head as a separate
    # target: doing so asks Hermes the same business question twice and can
    # persist contradictory duplicate judgments.  Staged revisions still
    # compare with one another and with every other current family.
    staged_system_keys = {
        str(item.get("value_system", {}).get("key") or "")
        for item in sources
        if item.get("value_system", {}).get("key")
    }
    targets = [
        item for item in compact
        if item["graph_id"] in staged_ids
        or str(item.get("value_system", {}).get("key") or "") not in staged_system_keys
    ]
    seen: set[str] = set()
    packets: list[dict[str, Any]] = []
    candidate_total = 0
    for source in sources:
        ranked: list[dict[str, Any]] = []
        for target in targets:
            if source["graph_id"] == target["graph_id"]:
                continue
            candidate = _family_candidate(source, target)
            if candidate is None or candidate["candidate_fingerprint"] in seen:
                continue
            ranked.append(candidate)
        ranked.sort(
            key=lambda item: (
                -float(item["evidence"]["candidate_score"]),
                str(item["target"].get("graph_id") or ""),
            )
        )
        selected = ranked[:max_candidates_per_graph]
        if candidate_total + len(selected) > max_candidates_total:
            selected = selected[:max(0, max_candidates_total - candidate_total)]
        if not selected:
            continue
        fingerprints = [str(item["candidate_fingerprint"]) for item in selected]
        existing = {
            str(
                row.get("candidate_fingerprint")
                if isinstance(row, Mapping)
                else row[0]
            )
            for row in conn.execute(
                """
                SELECT candidate_fingerprint
                  FROM rvbbit.business_semantic_family_relationship_proposals
                 WHERE candidate_fingerprint=ANY(%s::text[])
                """,
                (fingerprints,),
            ).fetchall()
        }
        selected = [
            item for item in selected
            if item["candidate_fingerprint"] not in existing
        ]
        if not selected:
            continue
        seen.update(str(item["candidate_fingerprint"]) for item in selected)
        candidate_total += len(selected)
        packets.append({
            "schema_version": FAMILY_CONSOLIDATION_SCHEMA_VERSION,
            "source": source,
            "candidates": selected,
            "policy": {
                "precision_first": True,
                "schema_is_not_domain": True,
                "table_is_not_family": True,
                "cube_is_materialization": True,
                "derived_sources_do_not_create_identity": True,
                "destructive_merge": False,
            },
        })
        if candidate_total >= max_candidates_total:
            break
    return packets


def _record_family_consolidations(
    conn: Any,
    run_id: str,
    decisions: Sequence[Mapping[str, Any]],
    receipt: Mapping[str, Any],
    proposed_by: str,
) -> int:
    inserted = 0
    for decision in decisions:
        evidence = {
            **dict(decision.get("candidate_evidence") or {}),
            "evidence_refs": list(decision.get("evidence_refs") or []),
            "caveats": list(decision.get("caveats") or []),
        }
        row = conn.execute(
            """
            INSERT INTO rvbbit.business_semantic_family_relationship_proposals(
                workflow_run_id,source_graph_id,target_graph_id,relationship_kind,
                confidence,proposed_business_domain_key,proposed_business_domain_name,
                proposed_family_key,proposed_family_name,rationale,evidence,
                model_receipt,candidate_fingerprint,adjudication_version,proposed_by
            ) VALUES (
                %s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,
                %s::jsonb,%s::jsonb,%s,%s,%s
            )
            ON CONFLICT (candidate_fingerprint) DO NOTHING
            RETURNING relationship_id
            """,
            (
                run_id,decision.get("source_graph_id"),decision.get("target_graph_id"),
                decision.get("relationship_kind"),decision.get("confidence"),
                decision.get("proposed_business_domain_key"),
                decision.get("proposed_business_domain_name"),
                decision.get("proposed_family_key"),decision.get("proposed_family_name"),
                decision.get("rationale"),json.dumps(evidence),json.dumps(dict(receipt)),
                decision.get("candidate_fingerprint"),FAMILY_CONSOLIDATION_SCHEMA_VERSION,
                proposed_by,
            ),
        ).fetchone()
        inserted += int(row is not None)
    return inserted


def _cached_value_graph_receipt(
    prior_receipt: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    *,
    kind: str,
) -> dict[str, Any]:
    """Keep validation provenance while making the lack of inference explicit."""

    return {
        "schema_version": "rvbbit.business-value.receipt.v1",
        "worker_version": "business-value-graph-v2",
        "generation_receipts": [],
        "evaluation": dict(evaluation),
        "stripped_identifier_dimensions": list(
            prior_receipt.get("stripped_identifier_dimensions") or []
        ),
        "enriched_identifier_dimensions": list(
            prior_receipt.get("enriched_identifier_dimensions") or []
        ),
        "baseline_metric_added": bool(prior_receipt.get("baseline_metric_added")),
        "fallback": bool(prior_receipt.get("fallback")),
        "cache": {
            "hit": True,
            "kind": kind,
            "original_generation_receipts": len(
                prior_receipt.get("generation_receipts") or []
            ),
        },
    }


def _failed_cached_value_graph_receipt(
    prior_receipt: Mapping[str, Any],
    error: Exception,
    *,
    kind: str,
) -> dict[str, Any]:
    """Retain known semantics without claiming a fresh metric validation."""

    receipt = _cached_value_graph_receipt(
        prior_receipt,
        {
            "schema_version": "rvbbit.business-value.evaluation.v1",
            "worker_version": "business-value-graph-v2",
            "metrics": [],
            "validated_metrics": 0,
            "all_dimension_reconciliations_passed": False,
            "revalidation_error": f"{type(error).__name__}: {error}"[:1000],
        },
        kind=kind,
    )
    receipt["cache"]["revalidation"] = "failed"
    return receipt


def _write_private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    os.chmod(path, 0o600)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _load_or_create(path: Path, create: Any) -> Any:
    if path.is_file():
        return _read_json(path)
    value = create()
    _write_private_json(path, value)
    return value


def _select_work_for_workflow(
    plan: Mapping[str, Any],
    *,
    include_bridges: bool,
) -> list[str]:
    """Select the workflow closure promised by its public parameters.

    Bridge probes and synthesis are optional because they add a second, often
    sizeable inference frontier.  Selecting ``all_work`` when bridges are
    disabled defeats both the work-item and LLM-call budgets before staging has
    a chance to honor ``include_bridges``.
    """

    if include_bridges:
        return select_work(plan, all_work=True)

    unit_ids = sorted(
        {
            str(work["scope_id"])
            for work in plan.get("work_items", [])
            if isinstance(work, Mapping)
            and work.get("work_kind") == "neighborhood_synthesis"
            and isinstance(work.get("scope_id"), str)
        }
    )
    if not unit_ids:
        raise ValueError("excavation plan contains no neighborhood synthesis work")
    return select_work(plan, unit_ids=unit_ids)


def _topology_summaries_for_value_graph(
    conn: Any,
    run_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT coalesce(receipt #>> '{result,canonical_name}',scope_id) AS name,
               context->'sources' AS sources,
               receipt #> '{result,nodes}' AS nodes
          FROM rvbbit.business_topology_proposal_bundles
         WHERE workflow_run_id=%s::uuid
           AND work_kind='neighborhood_synthesis'
         ORDER BY bundle_id
        """,
        (run_id,),
    ).fetchall()
    summaries: list[dict[str, Any]] = []
    for name, raw_sources, raw_nodes in rows:
        sources = raw_sources if isinstance(raw_sources, list) else []
        summaries.append(
            {
                "name": str(name or ""),
                "sources": [
                    ".".join(
                        str(value)
                        for value in (
                            source.get("source", {}).get("schema"),
                            source.get("source", {}).get("relation"),
                        )
                        if value
                    )
                    for source in sources
                    if isinstance(source, Mapping)
                ],
                "nodes": [
                    {
                        "name": node.get("name"),
                        "kind": node.get("node_kind"),
                        "description": node.get("description"),
                    }
                    for node in (raw_nodes if isinstance(raw_nodes, list) else [])
                    if isinstance(node, Mapping)
                ],
            }
        )
    return summaries


def _discover_and_stage_value_graphs(
    dsn: str,
    worker_id: str,
    run_id: str,
    relations: Sequence[str],
    client: HutchChatClient,
    lease: "_LeaseKeeper",
    *,
    lease_seconds: int,
    proposed_by: str,
    run_root: Path,
    prior_llm_attempts: int = 0,
    prior_semantic_cache_hits: int = 0,
) -> dict[str, Any]:
    with _connect(dsn, dict_rows=False) as conn:
        available = conn.execute(
            "SELECT to_regprocedure('rvbbit.business_value_stage_graph(uuid,jsonb,jsonb,text)') IS NOT NULL"
        ).fetchone()[0]
    if not available:
        return {"staged": 0, "metrics": 0, "graph_ids": [], "errors": []}

    value_modeling_llm_attempts = 0
    _update(
        dsn,
        worker_id,
        run_id,
        "value_modeling",
        {
            "current_step": "Reconciling source findings into business domains",
            "value_domain_count": 0,
            "value_domains_completed": 0,
            "current_value_domain": None,
            "current_value_domain_index": 0,
            "current_value_domain_state": "starting",
            "primary_llm_attempts": prior_llm_attempts,
            "value_modeling_llm_attempts": 0,
            "llm_attempts": prior_llm_attempts,
            "semantic_cache_hits": prior_semantic_cache_hits,
            "value_domain_planning_error_class": None,
            "value_domain_planning_error": None,
        },
        lease_seconds=lease_seconds,
    )
    with _connect(dsn) as read_conn:
        read_conn.read_only = True
        summaries = _topology_summaries_for_value_graph(read_conn, run_id)
        planning_packet = load_value_domain_planning_packet(
            read_conn,
            relations,
            topology_summaries=summaries,
        )
        prior_semantic_evidence = _load_prior_semantic_evidence(
            read_conn,relations
        )
        planning_packet["prior_semantic_evidence"] = prior_semantic_evidence
        operator_feedback = _load_knowledge_loop_feedback(read_conn, relations)
        if operator_feedback:
            planning_packet["operator_feedback"] = operator_feedback
        planning_packet["semantic_evidence_policy"] = {
            "exact_complete_members_are_a_dictionary": True,
            "bounded_sample_members_are_not_exhaustive": True,
            "preserve_family_identity_when_taxonomy_drifts": True,
            "operator_feedback_outranks_data_shape_inference": True,
            "denied_claims_require_new_evidence_to_reassert": True,
        }
        plan_path = run_root / "value-domain-plan.private.json"
        planning_state = "preparing"

        def report_planning_progress(event: Mapping[str, Any]) -> None:
            nonlocal value_modeling_llm_attempts, planning_state
            planning_state = str(event.get("event") or "planning")
            if planning_state == "planning_started":
                value_modeling_llm_attempts += 1
            progress: dict[str, Any] = {
                "current_step": "Reconciling source findings into business domains",
                "current_value_domain": None,
                "current_value_domain_state": planning_state,
                "value_modeling_llm_attempts": value_modeling_llm_attempts,
                "llm_attempts": prior_llm_attempts + value_modeling_llm_attempts,
            }
            if planning_state in {"planning_started", "planning_completed"}:
                progress["value_domain_planning_error_class"] = None
                progress["value_domain_planning_error"] = None
            if event.get("latency_ms") is not None:
                progress["value_domain_planning_latency_ms"] = event["latency_ms"]
            if event.get("model_version"):
                progress["value_domain_planning_model_version"] = str(
                    event["model_version"]
                )[:240]
            if event.get("error_class"):
                progress["value_domain_planning_error_class"] = str(
                    event["error_class"]
                )[:120]
            if event.get("error"):
                progress["value_domain_planning_error"] = str(event["error"])[:4000]
            _update(
                dsn,
                worker_id,
                run_id,
                "value_modeling",
                progress,
                lease_seconds=lease_seconds,
            )

        plan_cache_packet = _domain_plan_cache_projection(planning_packet)
        plan_cache_path = _semantic_cache_path(
            "domain-plans", VALUE_DOMAIN_PLAN_SCHEMA_VERSION, plan_cache_packet
        )
        compatible_plan_cache = (
            None
            if plan_cache_path.is_file()
            else _load_compatible_domain_plan_cache(planning_packet)
        )
        semantic_cache_hits = 0
        if plan_path.is_file():
            plan_envelope = _read_json(plan_path)
            domain_plan = dict(plan_envelope["plan"])
            plan_receipt = dict(plan_envelope["receipt"])
            value_modeling_llm_attempts += len(
                plan_receipt.get("generation_receipts") or []
            )
            planning_state = "planning_resumed"
        elif plan_cache_path.is_file() or compatible_plan_cache is not None:
            plan_envelope = (
                _read_json(plan_cache_path)
                if plan_cache_path.is_file()
                else compatible_plan_cache
            )
            if compatible_plan_cache is not None:
                _write_private_json(plan_cache_path, compatible_plan_cache)
            domain_plan = dict(plan_envelope["plan"])
            plan_receipt = {
                **dict(plan_envelope["receipt"]),
                "generation_receipts": [],
                "cache": {
                    "hit": True,
                    "kind": "domain_plan",
                    "compatible_run_plan": plan_envelope.get(
                        "compatible_run_plan"
                    ),
                    "original_generation_receipts": len(
                        plan_envelope.get("receipt", {}).get("generation_receipts") or []
                    ),
                },
            }
            semantic_cache_hits += 1
            planning_state = "planning_cache_hit"
            _write_private_json(
                plan_path,
                {
                    "planning_packet": planning_packet,
                    "plan": domain_plan,
                    "receipt": plan_receipt,
                },
            )
        else:
            domain_plan, plan_receipt = discover_value_domains(
                planning_packet,
                client,
                progress_callback=report_planning_progress,
            )
            _write_private_json(
                plan_path,
                {
                    "planning_packet": planning_packet,
                    "plan": domain_plan,
                    "receipt": plan_receipt,
                },
            )
            _write_private_json(
                plan_cache_path,
                {
                    "planning_packet_fingerprint": _sha256(
                        plan_cache_packet
                    ),
                    "plan": domain_plan,
                    "receipt": plan_receipt,
                },
            )
            planning_state = "planning_completed"
        business_domains = list(domain_plan.get("domains") or [])
        systems = list(domain_plan.get("systems") or [])
        cross_system_candidates = list(
            domain_plan.get("cross_system_candidates") or []
        )
        _update(
            dsn,
            worker_id,
            run_id,
            "value_modeling",
            {
                "current_step": "Explaining each coherent business domain",
                "business_domain_count": len(business_domains),
                "value_domain_count": len(systems),
                "value_domain_plan": [
                    {
                        "domain_key": system.get("domain_key"),
                        "domain_name": system.get("domain_name"),
                        "system_key": system.get("system_key"),
                        "name": system.get("name"),
                        "relationship_mode": system.get("relationship_mode"),
                        "relations": system.get("relations"),
                        "membership_count": len(system.get("memberships") or []),
                        "concept_count": len(system.get("concepts") or []),
                        "process_count": len(system.get("processes") or []),
                    }
                    for system in systems
                ],
                "unassigned_relations": list(domain_plan.get("unassigned_relations") or []),
                # The model's own verdict on whether a topology exists here,
                # and what it said it lacked — receipts for the recognition
                # step, never acted on inside this run.
                "value_domain_plan_verdict": dict(domain_plan.get("verdict") or {}),
                "value_domain_plan_needs": list(domain_plan.get("needs") or []),
                "cross_system_candidate_count": len(cross_system_candidates),
                "cross_system_candidates": cross_system_candidates,
                "semantic_cache_hits": (
                    prior_semantic_cache_hits + semantic_cache_hits
                ),
                "current_value_domain_state": planning_state,
                "value_modeling_llm_attempts": value_modeling_llm_attempts,
                "llm_attempts": prior_llm_attempts + value_modeling_llm_attempts,
            },
            lease_seconds=lease_seconds,
        )
        staged_payloads: list[tuple[dict[str, Any], dict[str, Any]]] = []
        errors: list[dict[str, str]] = []
        value_root = run_root / "value-graphs.private"
        value_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(value_root, 0o700)
        for index, system in enumerate(systems, start=1):
            lease.assert_owned()
            domain_key = str(system["system_key"])
            domain_relations = [str(value) for value in system.get("relations") or []]
            path = value_root / f"{_sha256(domain_key)[:20]}.json"
            domain_state = "preparing"
            domain_error: str | None = None
            domain_error_class: str | None = None
            _update(
                dsn,
                worker_id,
                run_id,
                "value_modeling",
                {
                    "current_value_domain": domain_key,
                    "current_value_domain_index": index,
                    "current_value_domain_state": domain_state,
                    "current_value_domain_attempt": 0,
                    "current_value_domain_error_class": None,
                    "current_value_domain_error": None,
                    "value_modeling_llm_attempts": value_modeling_llm_attempts,
                    "llm_attempts": prior_llm_attempts + value_modeling_llm_attempts,
                    "semantic_cache_hits": (
                        prior_semantic_cache_hits + semantic_cache_hits
                    ),
                },
                lease_seconds=lease_seconds,
            )
            try:
                if path.is_file():
                    envelope = _read_json(path)
                    graph = dict(envelope["graph"])
                    receipt = dict(envelope["receipt"])
                    value_modeling_llm_attempts += len(
                        receipt.get("generation_receipts") or []
                    )
                    domain_state = "resumed"
                else:
                    relation_set = set(domain_relations)
                    domain_summaries = [
                        summary
                        for summary in summaries
                        if any(
                            source in relation_set
                            for source in summary.get("sources", [])
                        )
                    ]
                    packet = load_value_system_packet(
                        read_conn,
                        domain_relations,
                        topology_summaries=domain_summaries,
                        system=system,
                    )
                    packet["prior_semantic_evidence"] = [
                        item
                        for item in prior_semantic_evidence
                        if set(domain_relations)
                        & set(item.get("source_relations") or [])
                    ]
                    packet["semantic_evidence_policy"] = dict(
                        planning_packet["semantic_evidence_policy"]
                    )
                    packet["domain_plan"] = {
                        "business_domain_key": system.get("domain_key"),
                        "business_domain_name": system.get("domain_name"),
                        "name": system.get("name"),
                        "summary": system.get("summary"),
                        "relationship_mode": system.get("relationship_mode"),
                    }

                    def report_value_progress(event: Mapping[str, Any]) -> None:
                        nonlocal value_modeling_llm_attempts, domain_state
                        domain_state = str(event.get("event") or "inference")
                        if domain_state == "inference_started":
                            value_modeling_llm_attempts += 1
                        progress = {
                            "current_value_domain": domain_key,
                            "current_value_domain_index": index,
                            "current_value_domain_state": domain_state,
                            "current_value_domain_attempt": int(
                                event.get("attempt") or 0
                            ),
                            "current_value_domain_attempt_limit": int(
                                event.get("attempt_limit") or 0
                            ),
                            "value_modeling_llm_attempts": value_modeling_llm_attempts,
                            "llm_attempts": (
                                prior_llm_attempts + value_modeling_llm_attempts
                            ),
                            "semantic_cache_hits": (
                                prior_semantic_cache_hits + semantic_cache_hits
                            ),
                            "current_value_domain_error_class": None,
                            "current_value_domain_error": None,
                        }
                        if event.get("latency_ms") is not None:
                            progress["current_value_domain_latency_ms"] = event[
                                "latency_ms"
                            ]
                        if event.get("model_version"):
                            progress["current_value_domain_model_version"] = str(
                                event["model_version"]
                            )[:240]
                        if event.get("error_class"):
                            progress["current_value_domain_error_class"] = str(
                                event["error_class"]
                            )[:120]
                        if event.get("error"):
                            progress["current_value_domain_error"] = str(
                                event["error"]
                            )[:1000]
                        _update(
                            dsn,
                            worker_id,
                            run_id,
                            "value_modeling",
                            progress,
                            lease_seconds=lease_seconds,
                        )

                    graph_cache_path = _semantic_cache_path(
                        "value-graphs",
                        VALUE_GRAPH_SCHEMA_VERSION,
                        _value_graph_cache_projection(packet),
                    )
                    cached_graph: dict[str, Any] | None = None
                    cached_receipt: dict[str, Any] = {}
                    cache_kind = ""
                    if graph_cache_path.is_file():
                        cached = _read_json(graph_cache_path)
                        candidate = dict(cached["graph"])
                        if _graph_matches_packet(candidate, packet):
                            cached_graph = candidate
                            cached_receipt = dict(cached.get("receipt") or {})
                            cache_kind = "value_graph"
                    if cached_graph is None:
                        current = _load_current_value_graph(read_conn, packet)
                        if current is not None:
                            cached_graph, cached_receipt = current
                            cache_kind = "value_graph_database"

                    if cached_graph is not None:
                        try:
                            graph = cached_graph
                            evaluation = evaluate_value_graph(read_conn, graph)
                            receipt = _cached_value_graph_receipt(
                                cached_receipt,
                                evaluation,
                                kind=cache_kind,
                            )
                            semantic_cache_hits += 1
                            domain_state = "inference_cache_hit"
                            report_value_progress({"event": domain_state})
                            _write_private_json(
                                graph_cache_path, {"graph": graph, "receipt": receipt}
                            )
                        except Exception as exc:
                            read_conn.rollback()
                            graph = cached_graph
                            receipt = _failed_cached_value_graph_receipt(
                                cached_receipt,
                                exc,
                                kind=cache_kind,
                            )
                            semantic_cache_hits += 1
                            domain_state = "inference_cache_revalidation_failed"
                            report_value_progress(
                                {
                                    "event": domain_state,
                                    "error_class": type(exc).__name__,
                                    "error": f"{type(exc).__name__}: {exc}"[:1000],
                                }
                            )
                            errors.append(
                                {
                                    "domain_key": domain_key,
                                    "error": (
                                        "Cached semantic graph retained; live metric "
                                        f"revalidation failed: {type(exc).__name__}: {exc}"
                                    )[:1000],
                                }
                            )
                            _write_private_json(
                                graph_cache_path, {"graph": graph, "receipt": receipt}
                            )
                    else:
                        graph, receipt = discover_value_graph(
                            read_conn,
                            packet,
                            client,
                            progress_callback=report_value_progress,
                        )
                        _write_private_json(
                            graph_cache_path, {"graph": graph, "receipt": receipt}
                        )
                    _write_private_json(path, {"graph": graph, "receipt": receipt})
                    if domain_state not in {
                        "inference_cache_hit",
                        "inference_cache_revalidation_failed",
                    }:
                        domain_state = "completed"
                staged_payloads.append((graph, receipt))
            except Exception as exc:  # noqa: BLE001 - isolate one bounded domain
                domain_state = "failed"
                domain_error_class = type(exc).__name__
                domain_error = f"{type(exc).__name__}: {exc}"[:1000]
                errors.append(
                    {
                        "domain_key": domain_key,
                        "error": domain_error,
                    }
                )
            _update(
                dsn,
                worker_id,
                run_id,
                "value_modeling",
                {
                    "value_domains_completed": index,
                    "value_graphs_ready": len(staged_payloads),
                    "value_graph_errors": errors,
                    "current_value_domain": domain_key,
                    "current_value_domain_index": index,
                    "current_value_domain_state": domain_state,
                    "current_value_domain_error_class": domain_error_class,
                    "current_value_domain_error": domain_error,
                    "value_modeling_llm_attempts": value_modeling_llm_attempts,
                    "llm_attempts": prior_llm_attempts + value_modeling_llm_attempts,
                    "semantic_cache_hits": (
                        prior_semantic_cache_hits + semantic_cache_hits
                    ),
                },
                lease_seconds=lease_seconds,
            )

    _update(
        dsn,
        worker_id,
        run_id,
        "value_modeling",
        {
            "current_value_domain": None,
            "current_value_domain_index": len(systems),
            "current_value_domain_state": "finished",
            "current_value_domain_error_class": None,
            "current_value_domain_error": None,
            "semantic_cache_hits": prior_semantic_cache_hits + semantic_cache_hits,
            "value_modeling_llm_attempts": value_modeling_llm_attempts,
            "llm_attempts": prior_llm_attempts + value_modeling_llm_attempts,
        },
        lease_seconds=lease_seconds,
    )

    graph_ids: list[str] = []
    metric_count = 0
    with _connect(dsn) as write_conn:
        for graph, receipt in staged_payloads:
            graph_id = write_conn.execute(
                "SELECT rvbbit.business_value_stage_graph(%s::uuid,%s::jsonb,%s::jsonb,%s)::text",
                (run_id, json.dumps(graph), json.dumps(receipt), proposed_by),
            ).fetchone()[0]
            graph_ids.append(str(graph_id))
            metric_count += len(graph.get("metrics") or [])
        write_conn.commit()

    consolidation_proposals = 0
    consolidation_candidates = 0
    consolidation_errors: list[dict[str, str]] = []
    consolidation_retry_packets: list[tuple[int, dict[str, Any]]] = []
    if graph_ids:
        try:
            with _connect(dsn, dict_rows=True) as candidate_conn:
                candidate_conn.read_only = True
                consolidation_packets = _load_family_consolidation_packets(
                    candidate_conn,graph_ids
                )
        except Exception as exc:  # noqa: BLE001 - older/mid-migration appliance
            consolidation_packets = []
            consolidation_errors.append({
                "source_graph_id": "",
                "error": f"candidate loading failed: {type(exc).__name__}: {exc}"[:1000],
            })

        consolidation_candidates = sum(
            len(packet.get("candidates") or []) for packet in consolidation_packets
        )
        _update(
            dsn,
            worker_id,
            run_id,
            "value_modeling",
            {
                "current_step": "Comparing Value System families",
                "family_consolidation_state": "starting",
                "family_consolidation_candidates": consolidation_candidates,
                "family_consolidation_completed": 0,
                "family_consolidation_proposals": 0,
                "family_consolidation_retry_packets": 0,
                "family_consolidation_retry_completed": 0,
                "family_consolidation_error_class": None,
                "family_consolidation_error": None,
                "value_modeling_llm_attempts": value_modeling_llm_attempts,
                "llm_attempts": prior_llm_attempts + value_modeling_llm_attempts,
            },
            lease_seconds=lease_seconds,
        )
        for index, packet in enumerate(consolidation_packets, start=1):
            lease.assert_owned()
            source_graph_id = str(packet.get("source", {}).get("graph_id") or "")
            consolidation_state = "preparing"
            failure: dict[str, str] | None = None
            failure_class: str | None = None

            def report_consolidation_progress(event: Mapping[str, Any]) -> None:
                nonlocal value_modeling_llm_attempts, consolidation_state
                consolidation_state = str(event.get("event") or "consolidating")
                if consolidation_state == "consolidation_started":
                    value_modeling_llm_attempts += 1
                progress: dict[str, Any] = {
                    "current_step": "Comparing Value System families",
                    "current_family_graph_id": source_graph_id,
                    "family_consolidation_state": consolidation_state,
                    "family_consolidation_index": index,
                    "family_consolidation_packet_count": len(consolidation_packets),
                    "family_consolidation_candidates": consolidation_candidates,
                    "family_consolidation_completed": index - 1,
                    "family_consolidation_proposals": consolidation_proposals,
                    "family_consolidation_error_class": None,
                    "family_consolidation_error": None,
                    "value_modeling_llm_attempts": value_modeling_llm_attempts,
                    "llm_attempts": prior_llm_attempts + value_modeling_llm_attempts,
                }
                if event.get("latency_ms") is not None:
                    progress["family_consolidation_latency_ms"] = event["latency_ms"]
                if event.get("model_version"):
                    progress["family_consolidation_model_version"] = str(
                        event["model_version"]
                    )[:240]
                if event.get("error_class"):
                    progress["family_consolidation_error_class"] = str(
                        event["error_class"]
                    )[:120]
                if event.get("error"):
                    progress["family_consolidation_error"] = str(
                        event["error"]
                    )[:1000]
                _update(
                    dsn,worker_id,run_id,"value_modeling",progress,
                    lease_seconds=lease_seconds,
                )

            try:
                result, consolidation_receipt = discover_family_consolidations(
                    packet,client,progress_callback=report_consolidation_progress
                )
                with _connect(dsn) as relationship_conn:
                    consolidation_proposals += _record_family_consolidations(
                        relationship_conn,run_id,result.get("decisions") or [],
                        consolidation_receipt,proposed_by,
                    )
                    relationship_conn.commit()
                consolidation_state = "completed"
            except Exception as exc:  # noqa: BLE001 - isolate one family packet
                consolidation_state = "failed"
                failure_class = type(exc).__name__
                failure = {
                    "source_graph_id": source_graph_id,
                    "error": f"{type(exc).__name__}: {exc}"[:1000],
                }
                consolidation_errors.append(failure)
                consolidation_retry_packets.append((index, dict(packet)))
            _update(
                dsn,
                worker_id,
                run_id,
                "value_modeling",
                {
                    "current_step": "Comparing Value System families",
                    "current_family_graph_id": source_graph_id,
                    "family_consolidation_state": consolidation_state,
                    "family_consolidation_candidates": consolidation_candidates,
                    "family_consolidation_completed": index,
                    "family_consolidation_packet_count": len(consolidation_packets),
                    "family_consolidation_proposals": consolidation_proposals,
                    "family_consolidation_errors": consolidation_errors,
                    "family_consolidation_error_class": (
                        failure_class
                    ),
                    "family_consolidation_error": (
                        failure["error"] if failure else None
                    ),
                    "value_modeling_llm_attempts": value_modeling_llm_attempts,
                    "llm_attempts": prior_llm_attempts + value_modeling_llm_attempts,
                },
                lease_seconds=lease_seconds,
            )

        retry_total = len(consolidation_retry_packets)
        if retry_total:
            _update(
                dsn,
                worker_id,
                run_id,
                "value_modeling",
                {
                    "current_step": "Retrying failed Value System family comparisons",
                    "family_consolidation_state": "retrying",
                    "family_consolidation_retry_packets": retry_total,
                    "family_consolidation_retry_completed": 0,
                    "family_consolidation_error_class": None,
                    "family_consolidation_error": None,
                },
                lease_seconds=lease_seconds,
            )
        for retry_index, (original_index, packet) in enumerate(
            consolidation_retry_packets, start=1
        ):
            lease.assert_owned()
            source_graph_id = str(packet.get("source", {}).get("graph_id") or "")
            retry_state = "preparing"

            def report_consolidation_retry(event: Mapping[str, Any]) -> None:
                nonlocal value_modeling_llm_attempts, retry_state
                event_state = str(event.get("event") or "consolidating")
                retry_state = f"retry_{event_state}"
                if event_state == "consolidation_started":
                    value_modeling_llm_attempts += 1
                progress: dict[str, Any] = {
                    "current_step": "Retrying failed Value System family comparisons",
                    "current_family_graph_id": source_graph_id,
                    "family_consolidation_state": retry_state,
                    "family_consolidation_index": original_index,
                    "family_consolidation_packet_count": len(consolidation_packets),
                    "family_consolidation_candidates": consolidation_candidates,
                    "family_consolidation_completed": len(consolidation_packets),
                    "family_consolidation_proposals": consolidation_proposals,
                    "family_consolidation_retry_index": retry_index,
                    "family_consolidation_retry_packets": retry_total,
                    "family_consolidation_retry_completed": retry_index - 1,
                    "family_consolidation_error_class": None,
                    "family_consolidation_error": None,
                    "value_modeling_llm_attempts": value_modeling_llm_attempts,
                    "llm_attempts": prior_llm_attempts + value_modeling_llm_attempts,
                }
                if event.get("latency_ms") is not None:
                    progress["family_consolidation_latency_ms"] = event["latency_ms"]
                if event.get("model_version"):
                    progress["family_consolidation_model_version"] = str(
                        event["model_version"]
                    )[:240]
                if event.get("error_class"):
                    progress["family_consolidation_error_class"] = str(
                        event["error_class"]
                    )[:120]
                if event.get("error"):
                    progress["family_consolidation_error"] = str(
                        event["error"]
                    )[:1000]
                _update(
                    dsn,worker_id,run_id,"value_modeling",progress,
                    lease_seconds=lease_seconds,
                )

            retry_error: dict[str, str] | None = None
            retry_error_class: str | None = None
            try:
                result, consolidation_receipt = discover_family_consolidations(
                    packet,
                    client,
                    attempts=1,
                    progress_callback=report_consolidation_retry,
                )
                with _connect(dsn) as relationship_conn:
                    consolidation_proposals += _record_family_consolidations(
                        relationship_conn,run_id,result.get("decisions") or [],
                        consolidation_receipt,proposed_by,
                    )
                    relationship_conn.commit()
                consolidation_errors = [
                    item for item in consolidation_errors
                    if item.get("source_graph_id") != source_graph_id
                ]
                retry_state = "retry_completed"
            except Exception as exc:  # noqa: BLE001 - one final targeted retry
                retry_state = "retry_failed"
                retry_error_class = type(exc).__name__
                retry_error = {
                    "source_graph_id": source_graph_id,
                    "error": f"{type(exc).__name__}: {exc}"[:1000],
                }
                consolidation_errors = [
                    item for item in consolidation_errors
                    if item.get("source_graph_id") != source_graph_id
                ] + [retry_error]
            _update(
                dsn,
                worker_id,
                run_id,
                "value_modeling",
                {
                    "current_step": "Retrying failed Value System family comparisons",
                    "current_family_graph_id": source_graph_id,
                    "family_consolidation_state": retry_state,
                    "family_consolidation_proposals": consolidation_proposals,
                    "family_consolidation_errors": consolidation_errors,
                    "family_consolidation_retry_index": retry_index,
                    "family_consolidation_retry_packets": retry_total,
                    "family_consolidation_retry_completed": retry_index,
                    "family_consolidation_error_class": (
                        retry_error_class
                    ),
                    "family_consolidation_error": (
                        retry_error["error"] if retry_error else None
                    ),
                    "value_modeling_llm_attempts": value_modeling_llm_attempts,
                    "llm_attempts": prior_llm_attempts + value_modeling_llm_attempts,
                },
                lease_seconds=lease_seconds,
            )
        _update(
            dsn,
            worker_id,
            run_id,
            "value_modeling",
            {
                "current_step": "Value System family comparison complete",
                "current_value_domain_state": "finished",
                "current_family_graph_id": None,
                "family_consolidation_state": "finished",
                "family_consolidation_candidates": consolidation_candidates,
                "family_consolidation_completed": len(consolidation_packets),
                "family_consolidation_packet_count": len(consolidation_packets),
                "family_consolidation_proposals": consolidation_proposals,
                "family_consolidation_errors": consolidation_errors,
                "family_consolidation_retry_packets": len(
                    consolidation_retry_packets
                ),
                "family_consolidation_retry_completed": len(
                    consolidation_retry_packets
                ),
                "family_consolidation_error_class": None,
                "family_consolidation_error": None,
                "value_modeling_llm_attempts": value_modeling_llm_attempts,
                "llm_attempts": prior_llm_attempts + value_modeling_llm_attempts,
            },
            lease_seconds=lease_seconds,
        )
    return {
        "staged": len(graph_ids),
        "metrics": metric_count,
        "graph_ids": graph_ids,
        "errors": errors,
        "llm_attempts": value_modeling_llm_attempts,
        "domain_plan": domain_plan,
        "domain_plan_receipt": plan_receipt,
        "semantic_cache_hits": semantic_cache_hits,
        "family_consolidation_candidates": consolidation_candidates,
        "family_consolidation_proposals": consolidation_proposals,
        "family_consolidation_errors": consolidation_errors,
        "family_consolidation_retry_packets": len(consolidation_retry_packets),
        "family_consolidation_retry_completed": len(consolidation_retry_packets),
    }


def _connect(dsn: str, *, autocommit: bool = False, dict_rows: bool = False):
    options: dict[str, Any] = {"autocommit": autocommit}
    if dict_rows:
        options["row_factory"] = dict_row
    return psycopg.connect(dsn, **options)


def _worker_id() -> str:
    configured = os.environ.get("WAREHOUSE_BUSINESS_TOPOLOGY_WORKER_ID", "").strip()
    if configured:
        return configured[:240]
    return f"calliope-server:{socket.gethostname()}:{os.getpid()}"


def _register_worker(
    dsn: str,
    worker_id: str,
    status: str,
    details: Mapping[str, Any] | None = None,
) -> None:
    with _connect(dsn, autocommit=True) as conn:
        conn.execute(
            "SELECT rvbbit.business_topology_register_workflow_worker(%s,%s,%s,%s::jsonb)",
            (
                worker_id,
                WORKFLOW_WORKER_VERSION,
                status,
                json.dumps(dict(details or {})),
            ),
        )


def _claim(dsn: str, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
    with _connect(dsn, autocommit=True, dict_rows=True) as conn:
        row = conn.execute(
            "SELECT * FROM rvbbit.business_topology_claim_workflow(%s,%s)",
            (worker_id, lease_seconds),
        ).fetchone()
    return dict(row) if row else None


def _update(
    dsn: str,
    worker_id: str,
    run_id: str,
    phase: str,
    progress: Mapping[str, Any] | None = None,
    *,
    lease_seconds: int,
) -> None:
    with _connect(dsn, autocommit=True, dict_rows=True) as conn:
        row = conn.execute(
            "SELECT rvbbit.business_topology_update_workflow(%s::uuid,%s,%s,%s::jsonb,%s)",
            (run_id, worker_id, phase, json.dumps(dict(progress or {})), lease_seconds),
        ).fetchone()
    if not row or row["business_topology_update_workflow"] is not True:
        raise WorkflowLeaseLost(f"workflow {run_id} was cancelled or its lease was lost")


def _usage_rollup(run_root: Path) -> dict[str, Any]:
    """Sum the per-call receipts a run left in its private directory.

    Every judgment/assembly call writes a receipt with the provider's usage
    block (prompt/completion tokens, cost), but until 2026-09-10 those only
    lived on disk — the run's database result carried call counts, so "what
    did last night cost" needed a script. This rolls them into one block the
    sweep summary can sum. Cached calls leave no receipt, so this is spend,
    not work."""

    totals = {
        "llm_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "receipt_files": 0,
        "models": {},
    }
    if not run_root.is_dir():
        return totals

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            usage = node.get("usage")
            if isinstance(usage, Mapping) and (
                "prompt_tokens" in usage or "total_tokens" in usage
            ):
                prompt = int(usage.get("prompt_tokens") or 0)
                completion = int(usage.get("completion_tokens") or 0)
                totals["llm_calls"] += 1
                totals["prompt_tokens"] += prompt
                totals["completion_tokens"] += completion
                totals["total_tokens"] += int(usage.get("total_tokens") or (prompt + completion))
                try:
                    totals["cost_usd"] += float(usage.get("cost") or 0.0)
                except (TypeError, ValueError):
                    pass
                model = str(node.get("service_model") or node.get("model_version") or "").strip()
                if model:
                    totals["models"][model] = totals["models"].get(model, 0) + 1
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    try:
        for path in run_root.rglob("*.json"):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except Exception:  # noqa: BLE001 — a partial or private file is not a receipt
                continue
            totals["receipt_files"] += 1
            walk(payload)
    except OSError:
        pass
    totals["cost_usd"] = round(totals["cost_usd"], 4)
    return totals


def backfill_usage(dsn: str, limit: int = 1000) -> int:
    """Attach usage rollups to finished runs that predate the rollup (one-off).

    Completed runs get result.usage, failed ones progress.usage — the same
    places _complete/_fail write it now. Returns the number of runs updated."""

    updated = 0
    with _connect(dsn, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT run_id::text, status FROM rvbbit.business_topology_workflow_runs "
            "WHERE status IN ('completed','failed','cancelled') "
            "AND coalesce(result->'usage', progress->'usage') IS NULL "
            "ORDER BY finished_at DESC NULLS LAST LIMIT %s",
            (int(limit),),
        ).fetchall()
        for row in rows:
            run_id = str(row[0] if not hasattr(row, "keys") else row["run_id"])
            status = str(row[1] if not hasattr(row, "keys") else row["status"])
            root = _private_root() / run_id
            if not root.is_dir():
                continue
            usage = _usage_rollup(root)
            column = "result" if status == "completed" else "progress"
            conn.execute(
                f"UPDATE rvbbit.business_topology_workflow_runs SET {column} = "
                f"coalesce({column},'{{}}'::jsonb) || jsonb_build_object('usage', %s::jsonb) "
                "WHERE run_id=%s::uuid",
                (json.dumps(usage), run_id),
            )
            updated += 1
    return updated


def _complete(
    dsn: str,
    worker_id: str,
    run_id: str,
    result: Mapping[str, Any],
) -> None:
    payload = dict(result)
    payload.setdefault("usage", _usage_rollup(_private_root() / run_id))
    with _connect(dsn, autocommit=True) as conn:
        conn.execute(
            "SELECT rvbbit.business_topology_complete_workflow(%s::uuid,%s,%s::jsonb)",
            (run_id, worker_id, json.dumps(payload)),
        )


def _fail(
    dsn: str,
    worker_id: str,
    run_id: str,
    error: str,
    progress: Mapping[str, Any] | None = None,
) -> None:
    payload = dict(progress or {})
    payload.setdefault("usage", _usage_rollup(_private_root() / run_id))
    with _connect(dsn, autocommit=True) as conn:
        conn.execute(
            "SELECT rvbbit.business_topology_fail_workflow(%s::uuid,%s,%s,%s::jsonb)",
            (run_id, worker_id, error[:6000], json.dumps(payload)),
        )


def _defer(
    dsn: str,
    worker_id: str,
    run_id: str,
    error: str,
    delay_seconds: int,
) -> Any:
    with _connect(dsn, autocommit=True) as conn:
        row = conn.execute(
            "SELECT rvbbit.business_topology_defer_workflow(%s::uuid,%s,%s,%s)",
            (run_id, worker_id, error[:6000], int(delay_seconds)),
        ).fetchone()
    return row[0] if row else None


_TRANSIENT_FAILURE_MARKERS = (
    # An empty completion is the provider dropping the answer, not a defect
    # in the packet (pilot 2026-09-10: 6 of 8 sweep failures were gemini via
    # OpenRouter returning nothing); defer and retry like a 429.
    "empty chat-completion content",
    "empty completion",
    "429",
    "lanes_saturated",
    "lane saturated",
    "too many requests",
    "rate limit",
    "connecterror",
    "connecttimeout",
    "readtimeout",
    "remoteprotocolerror",
    "server disconnected",
    "connection reset",
    "502",
    "503",
    "504",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "upstream",
)

_DEFER_MAX_ATTEMPTS = _env_int("WAREHOUSE_BUSINESS_TOPOLOGY_DEFER_MAX_ATTEMPTS", 6, 1, 50)


def _transient_failure(exc: BaseException) -> bool:
    """Upstream saturation or a dropped connection is not a workflow defect."""

    if isinstance(exc, WorkflowLeaseLost):
        return False
    haystack = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in haystack for marker in _TRANSIENT_FAILURE_MARKERS)


def _defer_delay_seconds(attempts: int) -> int:
    # 5 min, 10, 20, 40, ... capped at an hour; attempts is post-claim (≥1).
    return min(3600, 300 * (2 ** max(0, attempts - 1)))


class _LeaseKeeper:
    def __init__(self, dsn: str, worker_id: str, run_id: str, lease_seconds: int):
        self.dsn = dsn
        self.worker_id = worker_id
        self.run_id = run_id
        self.lease_seconds = lease_seconds
        self.stop_event = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name=f"topology-lease-{run_id[:8]}",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=5)

    def assert_owned(self) -> None:
        if self.lost.is_set():
            raise WorkflowLeaseLost(
                f"workflow {self.run_id} was cancelled or its lease was lost"
            )

    def _run(self) -> None:
        interval = min(max(self.lease_seconds // 3, 10), 30)
        while not self.stop_event.wait(interval):
            try:
                with _connect(self.dsn, autocommit=True, dict_rows=True) as conn:
                    row = conn.execute(
                        "SELECT rvbbit.business_topology_renew_workflow_lease(%s::uuid,%s,%s)",
                        (self.run_id, self.worker_id, self.lease_seconds),
                    ).fetchone()
                if not row or row["business_topology_renew_workflow_lease"] is not True:
                    self.lost.set()
                    return
                _register_worker(
                    self.dsn,
                    self.worker_id,
                    "busy",
                    {"run_id": self.run_id, "excavation_worker": EXCAVATION_WORKER_VERSION},
                )
            except Exception as exc:  # noqa: BLE001 - the main lease still has time
                print(
                    f"business topology lease heartbeat failed run={self.run_id}: {exc}",
                    file=sys.stderr,
                )


def _embedding_rows(
    dsn: str,
    corpus: Mapping[str, Any],
    *,
    kinds: Sequence[str],
    channels: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    inputs = make_embedding_inputs(corpus, kinds=kinds, channels=channels)
    with _connect(dsn) as conn:
        vectors, audit = embed_postgres_shadow(
            conn,
            inputs,
            specialist="embed",
            mode="document",
            max_batch_items=16,
            max_batch_chars=24_000,
            statement_timeout_ms=300_000,
        )
    failures = audit.get("failures") or []
    if failures:
        raise RuntimeError(
            f"embedding failed for {len(failures)} of {len(inputs)} topology packets"
        )
    return vectors, audit


def _merge_evidence(
    *groups: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, float]]:
    merged: dict[str, dict[str, float]] = {}
    for group in groups:
        for item in group:
            left = str(item.get("left_population_id") or "")
            right = str(item.get("right_population_id") or "")
            values = item.get("local_evidence")
            if not left or not right or left == right or not isinstance(values, Mapping):
                continue
            target = merged.setdefault(pair_key(left, right), {})
            for key, value in values.items():
                if isinstance(value, (int, float)):
                    target[str(key)] = max(float(value), target.get(str(key), float("-inf")))
    return merged


def _probe_pairs(
    evidence: Mapping[str, Mapping[str, float]],
    *,
    limit: int,
) -> list[tuple[str, str]]:
    def priority(key: str) -> tuple[float, float, float, str]:
        values = evidence[key]
        return (
            -float(values.get("query_join_count", 0.0)),
            -float(values.get("query_cooccurrence_count", 0.0)),
            -float(values.get("embedding_similarity", -1.0)),
            key,
        )

    result: list[tuple[str, str]] = []
    for key in sorted(evidence, key=priority)[:limit]:
        left, right = key.split("\x1f", 1)
        result.append((left, right))
    return result


def _postgres_field_population_count(corpus: Mapping[str, Any]) -> int:
    return sum(
        1
        for item in corpus.get("populations", [])
        if isinstance(item, Mapping)
        and isinstance(item.get("packet"), Mapping)
        and item["packet"].get("source", {}).get("kind") == "postgres_relation"
        and item["packet"].get("population", {}).get("kind") == "field"
    )


def _optional_overlap_evidence(
    dsn: str,
    corpus: Mapping[str, Any],
    semantic_map: Mapping[str, Any],
    *,
    sample_rows: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collect bounded overlap evidence without making it a workflow gate."""

    population_count = _postgres_field_population_count(corpus)
    global_population_limit = _env_int(
        "WAREHOUSE_BUSINESS_TOPOLOGY_GLOBAL_OVERLAP_POPULATIONS",
        512,
        2,
        50_000,
    )
    probe_limit = _env_int(
        "WAREHOUSE_BUSINESS_TOPOLOGY_OVERLAP_PROBE_PAIRS", 1000, 1, 10_000
    )
    probe_batch_size = _env_int(
        "WAREHOUSE_BUSINESS_TOPOLOGY_OVERLAP_PROBE_BATCH_SIZE",
        100,
        1,
        probe_limit,
    )
    global_timeout_ms = _env_int(
        "WAREHOUSE_BUSINESS_TOPOLOGY_GLOBAL_OVERLAP_TIMEOUT_MS",
        120_000,
        1_000,
        900_000,
    )
    targeted_timeout_ms = _env_int(
        "WAREHOUSE_BUSINESS_TOPOLOGY_TARGETED_OVERLAP_TIMEOUT_MS",
        30_000,
        1_000,
        300_000,
    )
    include_global_scan = population_count <= global_population_limit
    probe_pairs = _probe_pairs(semantic_map, limit=probe_limit)
    base_audit = {
        "field_population_count": population_count,
        "global_population_limit": global_population_limit,
        "global_scan_requested": include_global_scan,
        "global_scan_skip_reason": (
            None if include_global_scan else "population_budget"
        ),
        "targeted_probe_pairs_requested": len(probe_pairs),
    }
    if population_count < 2:
        return [], {
            **base_audit,
            "status": "skipped_insufficient_field_populations",
        }
    if not include_global_scan and not probe_pairs:
        return [], {**base_audit, "status": "skipped_no_bounded_candidates"}

    try:
        with _connect(dsn) as conn:
            evidence, audit = extract_postgres_overlap_shadow(
                conn,
                corpus,
                probe_pairs=probe_pairs,
                sample_rows=sample_rows,
                min_shared=1,
                max_fingerprint_fanout=50,
                max_pairs=50_000,
                max_probe_pairs=probe_limit,
                statement_timeout_ms=global_timeout_ms,
                include_global_scan=include_global_scan,
                probe_batch_size=probe_batch_size,
                targeted_statement_timeout_ms=targeted_timeout_ms,
            )
    except psycopg.Error as exc:
        if getattr(exc, "sqlstate", None) != "57014":
            raise
        return [], {
            **base_audit,
            "status": "degraded_statement_timeout",
            "error_class": type(exc).__name__,
            "error": str(exc)[:1000],
        }

    audit = {**base_audit, **dict(audit)}
    if not include_global_scan and audit.get("status") == "complete":
        audit["status"] = "complete_targeted_only"
    return evidence, audit


class _ExecutionProgress:
    def __init__(
        self,
        dsn: str,
        worker_id: str,
        run_id: str,
        lease: _LeaseKeeper,
        lease_seconds: int,
        total: int,
    ):
        self.dsn = dsn
        self.worker_id = worker_id
        self.run_id = run_id
        self.lease = lease
        self.lease_seconds = lease_seconds
        self.total = max(total, 1)
        self.last_update = 0.0

    def __call__(self, event: Mapping[str, Any]) -> None:
        self.lease.assert_owned()
        now = time.monotonic()
        work_kind = str(event.get("work_kind") or "")
        force = (
            event.get("event") in {"started", "finished", "failed"}
            or work_kind != "correspondence"
            or now - self.last_update >= 0.75
        )
        if not force:
            return
        completed = int(event.get("completed_work_items") or 0)
        _update(
            self.dsn,
            self.worker_id,
            self.run_id,
            "executing",
            {
                "work_item_count": self.total,
                "completed_work_items": completed,
                "work_progress": round(completed / self.total, 6),
                "current_work_id": event.get("work_id"),
                "current_work_kind": work_kind or None,
                "work_event": event.get("event"),
                "resumed_work_items": int(event.get("resumed_work_items") or 0),
                "local_correspondence_calls": int(
                    event.get("local_correspondence_calls") or 0
                ),
                "llm_attempts": int(event.get("hutch_llm_attempts") or 0),
                "semantic_cache_hits": int(event.get("semantic_cache_hits") or 0),
            },
            lease_seconds=self.lease_seconds,
        )
        self.last_update = now


def _run_workflow(
    dsn: str,
    worker_id: str,
    job: Mapping[str, Any],
    *,
    lease_seconds: int,
) -> dict[str, Any]:
    run_id = str(job["run_id"])
    params = dict(job.get("parameters") or {})
    relations = [str(value) for value in params.get("relations") or []]
    if not relations:
        raise RuntimeError("workflow has no relation scope")

    run_root = _private_root() / run_id
    run_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(run_root, 0o700)
    parameter_hash = _sha256(params)
    manifest_path = run_root / "workflow.json"
    if manifest_path.is_file():
        manifest = _read_json(manifest_path)
        if manifest.get("parameters_sha256") != parameter_hash:
            raise RuntimeError("private workflow directory belongs to different parameters")
    else:
        _write_private_json(
            manifest_path,
            {
                "schema_version": "rvbbit.business-topology.appliance-workflow.v1",
                "run_id": run_id,
                "parameters_sha256": parameter_hash,
                "parameters": params,
            },
        )

    lease = _LeaseKeeper(dsn, worker_id, run_id, lease_seconds)
    lease.start()
    try:
        sample_rows = int(params.get("sample_rows") or 2048)
        corpus_path = run_root / "corpus.private.json"
        _update(
            dsn,
            worker_id,
            run_id,
            "profiling",
            {"relation_count": len(relations), "current_step": "Profiling source populations"},
            lease_seconds=lease_seconds,
        )

        extraction_audit: dict[str, Any] = {}

        def create_corpus() -> dict[str, Any]:
            nonlocal extraction_audit
            with _connect(dsn) as conn:
                corpus, extraction_audit = extract_postgres_shadow_corpus(
                    conn,
                    relations,
                    corpus_id=str(params.get("corpus_id") or f"business-topology-{run_id}"),
                    sample_rows=sample_rows,
                    statement_timeout_ms=120_000,
                )
            return corpus

        corpus = _load_or_create(corpus_path, create_corpus)
        population_count = len(corpus.get("populations") or [])
        _update(
            dsn,
            worker_id,
            run_id,
            "profiling",
            {
                "population_count": population_count,
                "profiled_relations": len(relations),
                "extraction_audit": extraction_audit,
            },
            lease_seconds=lease_seconds,
        )
        lease.assert_owned()

        context_vectors_path = run_root / "context-vectors.private.json"
        _update(
            dsn,
            worker_id,
            run_id,
            "embedding_sources",
            {"current_step": "Finding source neighborhoods"},
            lease_seconds=lease_seconds,
        )
        context_audit: dict[str, Any] = {}

        def create_context_vectors() -> list[dict[str, Any]]:
            nonlocal context_audit
            vectors, context_audit = _embedding_rows(
                dsn,
                corpus,
                kinds=("record_context",),
                channels=("context",),
            )
            return vectors

        context_vectors = _load_or_create(context_vectors_path, create_context_vectors)
        neighborhoods_path = run_root / "neighborhoods.private.json"
        neighborhoods = _load_or_create(
            neighborhoods_path,
            lambda: propose_source_neighborhoods(corpus, context_vectors),
        )
        neighborhood_summary = dict(neighborhoods.get("summary") or {})
        _update(
            dsn,
            worker_id,
            run_id,
            "partitioning",
            {
                "neighborhood_count": len(neighborhoods.get("neighborhoods") or []),
                "singleton_source_count": int(
                    neighborhood_summary.get("singleton_sources") or 0
                ),
                "bridge_count": int(neighborhood_summary.get("bridges") or 0),
                "source_embedding_audit": context_audit,
            },
            lease_seconds=lease_seconds,
        )
        lease.assert_owned()

        field_vectors_path = run_root / "field-vectors.private.json"
        _update(
            dsn,
            worker_id,
            run_id,
            "embedding_populations",
            {"current_step": "Comparing semantic populations"},
            lease_seconds=lease_seconds,
        )
        field_audit: dict[str, Any] = {}

        def create_field_vectors() -> list[dict[str, Any]]:
            nonlocal field_audit
            vectors, field_audit = _embedding_rows(
                dsn,
                corpus,
                kinds=("field",),
                channels=("focus", "context"),
            )
            return vectors

        field_vectors = _load_or_create(field_vectors_path, create_field_vectors)
        semantic_path = run_root / "semantic-evidence.private.json"
        semantic_evidence = _load_or_create(
            semantic_path,
            lambda: build_embedding_evidence(corpus, field_vectors, top_k=24),
        )
        semantic_map = _merge_evidence(semantic_evidence)

        overlap_path = run_root / "overlap-evidence.private.json"
        overlap_audit_path = run_root / "overlap-audit.private.json"

        _update(
            dsn,
            worker_id,
            run_id,
            "evidence",
            {
                "semantic_candidate_count": len(semantic_evidence),
                "current_step": "Testing bounded local overlap evidence",
            },
            lease_seconds=lease_seconds,
        )
        if overlap_path.is_file():
            overlap_evidence = _read_json(overlap_path)
            overlap_audit = (
                _read_json(overlap_audit_path)
                if overlap_audit_path.is_file()
                else {"status": "legacy_checkpoint_without_audit"}
            )
        else:
            overlap_evidence, overlap_audit = _optional_overlap_evidence(
                dsn,
                corpus,
                semantic_map,
                sample_rows=sample_rows,
            )
            _write_private_json(overlap_audit_path, overlap_audit)
            _write_private_json(overlap_path, overlap_evidence)
        merged_evidence = _merge_evidence(semantic_evidence, overlap_evidence)
        _update(
            dsn,
            worker_id,
            run_id,
            "evidence",
            {
                "semantic_candidate_count": len(semantic_evidence),
                "overlap_candidate_count": len(overlap_evidence),
                "candidate_pair_count": len(merged_evidence),
                "population_embedding_audit": field_audit,
                "overlap_audit": overlap_audit,
                "overlap_evidence_degraded": str(
                    overlap_audit.get("status") or ""
                ).startswith(("partial", "degraded", "skipped")),
            },
            lease_seconds=lease_seconds,
        )
        lease.assert_owned()

        policy = dict(params.get("policy") or {})
        plan_path = run_root / "excavation-plan.private.json"
        _update(
            dsn,
            worker_id,
            run_id,
            "planning",
            {"current_step": "Compiling the bounded excavation DAG"},
            lease_seconds=lease_seconds,
        )
        plan = _load_or_create(
            plan_path,
            lambda: build_excavation_plan(
                corpus,
                neighborhoods,
                evidence_by_pair=merged_evidence,
                maximum_sources_per_unit=int(
                    policy.get("maximum_sources_per_unit") or 12
                ),
                maximum_populations_per_unit=int(
                    policy.get("maximum_populations_per_unit") or 48
                ),
                max_pairs_per_unit=int(policy.get("max_pairs_per_unit") or 64),
                max_pairs_per_link=int(policy.get("max_pairs_per_link") or 8),
                max_population_fanout=int(
                    policy.get("max_population_fanout") or 8
                ),
                minimum_pair_priority=float(
                    policy.get("minimum_pair_priority") or 0.32
                ),
                max_cross_neighborhood_links=int(
                    policy.get("max_cross_neighborhood_links") or 24
                ),
            ),
        )
        include_bridges = bool(params.get("include_bridges", False))
        selected = _select_work_for_workflow(
            plan,
            include_bridges=include_bridges,
        )
        preview = execution_preview(plan, selected)
        plan_summary = dict(plan.get("summary") or {})
        plan_sha256 = _sha256(plan)
        _update(
            dsn,
            worker_id,
            run_id,
            "planning",
            {
                "plan_sha256": plan_sha256,
                "excavation_unit_count": int(
                    plan_summary.get("excavation_units") or 0
                ),
                "boundary_link_count": int(plan_summary.get("boundary_links") or 0),
                "work_item_count": len(selected),
                "completed_work_items": 0,
                "generative_calls_planned": int(
                    preview.get("generative_calls_before_repairs") or 0
                ),
                "plan_summary": plan_summary,
            },
            lease_seconds=lease_seconds,
        )
        lease.assert_owned()

        max_work_items = int(params.get("max_work_items") or 500)
        max_llm_calls = int(params.get("max_llm_calls") or 128)
        inference_timeout_seconds = float(
            _env_int(
                "WAREHOUSE_BUSINESS_TOPOLOGY_INFERENCE_TIMEOUT_SECONDS",
                900,
                240,
                3600,
            )
        )
        routes = _model_routes(params)
        request_user = str(job.get("requested_by") or "business-topology-worker")
        if routes["judgment"]["backend"] == "hermes":
            judgment_client = HermesChatClient.from_environment(
                request_user=request_user,
                timeout_seconds=float(
                    _env_int(
                        "WAREHOUSE_HERMES_INFERENCE_TIMEOUT_SECONDS",
                        900,
                        240,
                        3600,
                    )
                ),
            )
            routes["judgment"]["model"] = judgment_client.model
            if judgment_client.provider:
                routes["judgment"]["provider"] = judgment_client.provider
            # Judgment is a completion, not an agent turn. A Hermes session
            # carries the api_server toolset and Hindsight memory, and a
            # strong model handed a 60k-token packet plus a terminal goes
            # investigating instead of answering (pilot, 2026-09-10: 17 of
            # 33 calls were tool turns; every session was retained into the
            # user-facing memory bank). When Hermes' configured provider is
            # registered as a direct openai_chat backend, call it there with
            # the same model. Falls back to the Hermes session when the
            # backend has no usable token on this box.
            direct = _direct_judgment_client(
                dsn,
                provider=judgment_client.provider,
                model=judgment_client.model,
                request_user=request_user,
                timeout_seconds=inference_timeout_seconds,
            )
            if direct is not None:
                judgment_client = direct
                routes["judgment"] = {
                    "backend": direct.backend_name,
                    "model": direct.model,
                    "provider": judgment_client.backend_name,
                    "transport": "direct",
                }
        else:
            # A pinned judge (run parameters or env) is the same tool-less,
            # memory-less direct call; record it as such for the morning check.
            judgment_client = HutchChatClient.from_postgres(
                dsn,
                backend_name=routes["judgment"]["backend"],
                model=routes["judgment"]["model"],
                request_user=request_user,
                timeout_seconds=inference_timeout_seconds,
                transport_attempts=3,
            )
            routes["judgment"]["transport"] = "direct"
        assembly_client = judgment_client
        if routes["assembly"] != routes["judgment"]:
            assembly_client = HutchChatClient.from_postgres(
                dsn,
                backend_name=routes["assembly"]["backend"],
                model=routes["assembly"]["model"],
                request_user=request_user,
                timeout_seconds=inference_timeout_seconds,
                transport_attempts=3,
            )
        import cost_accounting
        # The Hermes route is already accounted at its model-request boundary.
        # Direct clients have no agent ledger; record each live completion here.
        if not isinstance(judgment_client, HermesChatClient):
            judgment_client = cost_accounting.AccountedClient(
                judgment_client, lambda: _connect(dsn), "topology.judgment", str(run_id))
        if not isinstance(assembly_client, HermesChatClient):
            assembly_client = cost_accounting.AccountedClient(
                assembly_client, lambda: _connect(dsn), "topology.assembly", str(run_id))
        execution_dir = run_root / "execution.private"
        execution_summary = execute_plan(
            plan,
            selected,
            output_dir=execution_dir,
            client=judgment_client,
            clients_by_work_kind={"source_motifs": assembly_client},
            shared_cache_dir=_private_root() / "semantic-cache" / "excavation-work",
            max_work_items=max_work_items,
            max_llm_calls=max_llm_calls,
            repair_attempts=1,
            progress_callback=_ExecutionProgress(
                dsn,
                worker_id,
                run_id,
                lease,
                lease_seconds,
                len(selected),
            ),
        )
        lease.assert_owned()

        _update(
            dsn,
            worker_id,
            run_id,
            "staging",
            {"current_step": "Staging validated proposal bundles"},
            lease_seconds=lease_seconds,
        )
        with _connect(dsn) as conn:
            staging = stage_execution_bundles(
                conn,
                plan,
                execution_dir,
                include_bridges=include_bridges,
                proposed_by=str(job.get("requested_by") or "business-topology-worker"),
            )
        bundle_ids = [str(value) for value in staging.get("bundle_ids") or []]
        if bundle_ids:
            with _connect(dsn) as conn:
                conn.execute(
                    """
                    UPDATE rvbbit.business_topology_proposal_bundles
                       SET workflow_run_id=%s::uuid
                     WHERE bundle_id=ANY(%s::uuid[])
                    """,
                    (run_id, bundle_ids),
                )
                conn.commit()

        value_graphs = _discover_and_stage_value_graphs(
            dsn,
            worker_id,
            run_id,
            relations,
            judgment_client,
            lease,
            lease_seconds=lease_seconds,
            proposed_by=str(job.get("requested_by") or "business-topology-worker"),
            run_root=run_root,
            prior_llm_attempts=int(
                execution_summary.get("hutch_llm_attempts") or 0
            ),
            prior_semantic_cache_hits=int(
                execution_summary.get("semantic_cache_hits") or 0
            ),
        )
        lease.assert_owned()

        result = {
            "schema_version": "rvbbit.business-topology.workflow-result.v1",
            "plan_sha256": plan_sha256,
            "relation_count": len(relations),
            "population_count": population_count,
            "neighborhood_count": len(neighborhoods.get("neighborhoods") or []),
            "excavation_unit_count": int(plan_summary.get("excavation_units") or 0),
            "work_item_count": len(selected),
            "completed_work_items": len(selected),
            "llm_attempts": (
                int(execution_summary.get("hutch_llm_attempts") or 0)
                + int(value_graphs.get("llm_attempts") or 0)
            ),
            "primary_llm_attempts": int(
                execution_summary.get("hutch_llm_attempts") or 0
            ),
            "value_modeling_llm_attempts": int(
                value_graphs.get("llm_attempts") or 0
            ),
            "local_correspondence_calls": int(
                execution_summary.get("local_correspondence_calls") or 0
            ),
            "semantic_cache_hits": (
                int(execution_summary.get("semantic_cache_hits") or 0)
                + int(value_graphs.get("semantic_cache_hits") or 0)
            ),
            "bundles_staged": int(staging.get("staged") or 0),
            "bundle_ids": bundle_ids,
            "value_graphs_staged": int(value_graphs.get("staged") or 0),
            "value_metrics_staged": int(value_graphs.get("metrics") or 0),
            "value_graph_ids": list(value_graphs.get("graph_ids") or []),
            "value_graph_errors": list(value_graphs.get("errors") or []),
            "family_consolidation_candidates": int(
                value_graphs.get("family_consolidation_candidates") or 0
            ),
            "family_consolidation_proposals": int(
                value_graphs.get("family_consolidation_proposals") or 0
            ),
            "family_consolidation_errors": list(
                value_graphs.get("family_consolidation_errors") or []
            ),
            "family_consolidation_retry_packets": int(
                value_graphs.get("family_consolidation_retry_packets") or 0
            ),
            "family_consolidation_retry_completed": int(
                value_graphs.get("family_consolidation_retry_completed") or 0
            ),
            "business_domain_plan": list(
                (value_graphs.get("domain_plan") or {}).get("domains") or []
            ),
            "value_domain_plan": list(
                (value_graphs.get("domain_plan") or {}).get("systems") or []
            ),
            "unassigned_relations": list(
                (value_graphs.get("domain_plan") or {}).get("unassigned_relations") or []
            ),
            "cross_system_candidate_count": len(
                (value_graphs.get("domain_plan") or {}).get("cross_system_candidates") or []
            ),
            "cross_system_candidates": list(
                (value_graphs.get("domain_plan") or {}).get("cross_system_candidates") or []
            ),
            "model_routes": {
                "assembly": {
                    **routes["assembly"],
                    "work_kinds": ["source_motifs"],
                },
                "judgment": {
                    **routes["judgment"],
                    "work_kinds": [
                        "neighborhood_synthesis",
                        "bridge_synthesis",
                        "value_domain_planning",
                        "value_modeling",
                        "family_consolidation",
                    ],
                },
            },
            "materialized_topology": False,
            "submitted_proposals": True,
        }
        return result
    finally:
        lease.stop()


def _worker_loop(dsn: str) -> None:
    global _MIGRATION_MISSING_LOGGED
    worker_id = _worker_id()
    poll_seconds = _env_int(
        "WAREHOUSE_BUSINESS_TOPOLOGY_POLL_SECONDS", 2, 1, 60
    )
    lease_seconds = _env_int(
        "WAREHOUSE_BUSINESS_TOPOLOGY_LEASE_SECONDS", 300, 30, 3600
    )
    while not _STOP.is_set():
        try:
            _register_worker(
                dsn,
                worker_id,
                "ready",
                {"excavation_worker": EXCAVATION_WORKER_VERSION},
            )
            _MIGRATION_MISSING_LOGGED = False
            job = _claim(dsn, worker_id, lease_seconds)
            if job is None:
                _STOP.wait(poll_seconds)
                continue
            run_id = str(job["run_id"])
            print(
                f"business topology workflow claimed run={run_id} worker={worker_id}",
                file=sys.stderr,
            )
            _register_worker(
                dsn,
                worker_id,
                "busy",
                {"run_id": run_id, "excavation_worker": EXCAVATION_WORKER_VERSION},
            )
            try:
                result = _run_workflow(
                    dsn,
                    worker_id,
                    job,
                    lease_seconds=lease_seconds,
                )
                _complete(dsn, worker_id, run_id, result)
                print(
                    f"business topology workflow completed run={run_id} "
                    f"bundles={result.get('bundles_staged', 0)}",
                    file=sys.stderr,
                )
            except Exception as exc:  # noqa: BLE001 - durable failure boundary
                error = f"{type(exc).__name__}: {exc}"
                attempts = int(job.get("attempts") or 0)
                if _transient_failure(exc) and attempts < _DEFER_MAX_ATTEMPTS:
                    delay = _defer_delay_seconds(attempts)
                    try:
                        until = _defer(dsn, worker_id, run_id, error, delay)
                        print(
                            f"business topology workflow deferred run={run_id} "
                            f"attempt={attempts} until={until}: {error}",
                            file=sys.stderr,
                        )
                        continue
                    except Exception as defer_exc:  # noqa: BLE001
                        print(
                            f"business topology workflow could not defer "
                            f"run={run_id}: {defer_exc}",
                            file=sys.stderr,
                        )
                print(
                    f"business topology workflow failed run={run_id}: {error}\n"
                    f"{traceback.format_exc(limit=12)}",
                    file=sys.stderr,
                )
                try:
                    _fail(
                        dsn,
                        worker_id,
                        run_id,
                        error,
                        {
                            "error_class": type(exc).__name__,
                            "transient": _transient_failure(exc),
                            "attempts": attempts,
                        },
                    )
                except Exception as mark_exc:  # noqa: BLE001
                    print(
                        f"business topology workflow could not record failure "
                        f"run={run_id}: {mark_exc}",
                        file=sys.stderr,
                    )
        except psycopg.Error as exc:
            # Old databases simply do not have migration 0285 yet.  Stay quiet
            # after the first diagnostic and begin working automatically once
            # pg_rvbbit is upgraded/migrated.
            if not _MIGRATION_MISSING_LOGGED:
                print(
                    f"business topology workflow worker waiting for SQL contract: {exc}",
                    file=sys.stderr,
                )
                _MIGRATION_MISSING_LOGGED = True
            _STOP.wait(max(poll_seconds, 15))
        except Exception as exc:  # noqa: BLE001 - keep the standing worker alive
            print(
                f"business topology workflow worker loop error: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            _STOP.wait(max(poll_seconds, 5))


def start_worker(dsn: str) -> bool:
    """Start the singleton standing worker; return whether it is enabled."""

    global _THREAD
    if not _env_bool("WAREHOUSE_BUSINESS_TOPOLOGY_WORKER", True):
        return False
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return True
        _STOP.clear()
        _THREAD = threading.Thread(
            target=_worker_loop,
            args=(dsn,),
            name="business-topology-workflows",
            daemon=True,
        )
        _THREAD.start()
    return True


def stop_worker() -> None:
    _STOP.set()
