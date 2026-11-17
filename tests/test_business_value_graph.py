from __future__ import annotations

import json

import pytest

from business_topology.contracts import ContractError
from business_topology.value_graph import (
    FAMILY_CONSOLIDATION_SCHEMA_VERSION,
    VALUE_GRAPH_SCHEMA_VERSION,
    _baseline_count_graph,
    _fallback_graph,
    _sanitize_identifier_dimensions,
    discover_family_consolidations,
    discover_value_domains,
    discover_value_graph,
    group_relations_by_schema,
    normalize_value_domain_plan,
    normalize_family_consolidations,
    normalize_value_graph,
    validate_value_graph,
)


def packet():
    return {
        "schema_version": "rvbbit.business-value.domain-packet.v1",
        "domain_key": "commerce",
        "relations": [
            {
                "relation": "commerce.sales",
                "columns": [],
                "estimated_rows": 100,
            },
            {
                "relation": "commerce.products",
                "columns": [],
                "estimated_rows": 10,
            },
        ],
        "fact_candidates": [
            {
                "relation": "commerce.sales",
                "score": 18,
                "measure_columns": ["amount_sold", "quantity_sold"],
                "time_columns": ["time_id"],
                "shared_key_columns": ["product_id", "channel_id"],
            }
        ],
    }


def test_value_graph_fallback_is_executable_sql_not_a_metric_dsl():
    graph = _fallback_graph(packet())
    validate_value_graph(graph)

    assert graph["schema_version"] == VALUE_GRAPH_SCHEMA_VERSION
    assert graph["primary_metric_key"] == "amount_sold"
    metric = graph["metrics"][0]
    assert metric["metric_kind"] == "outcome"
    assert metric["unit"] == "currency"
    assert metric["dimensions"] == []
    assert metric["formula"] == "sum(Amount Sold)"
    assert metric["event_sql"] == (
        'SELECT "time_id"::timestamptz AS occurred_at, '
        '"amount_sold"::numeric AS value, '
        "'{}'::jsonb AS dimensions "
        'FROM "commerce"."sales" '
        'WHERE "time_id" IS NOT NULL AND "amount_sold" IS NOT NULL'
    )


def test_value_graph_contract_rejects_inventory_sprawl_and_unsafe_sql():
    graph = _fallback_graph(packet())
    graph["metrics"] = graph["metrics"] * 5
    with pytest.raises(ContractError, match="at most four"):
        validate_value_graph(graph)

    graph = _fallback_graph(packet())
    graph["metrics"][0]["event_sql"] = "DELETE FROM commerce.sales"
    with pytest.raises(ContractError, match="SELECT or WITH"):
        validate_value_graph(graph)


def test_value_graph_contract_rejects_opaque_identifier_dimensions():
    graph = _fallback_graph(packet())
    metric = graph["metrics"][0]
    metric["dimensions"] = [{"key": "product_id", "name": "Product ID"}]
    metric["primary_dimension_key"] = "product_id"
    with pytest.raises(ContractError, match="opaque identifier"):
        validate_value_graph(graph)

    graph = _fallback_graph(packet())
    graph["metrics"][0]["unit"] = "ratio"
    with pytest.raises(ContractError, match="invalid unit"):
        validate_value_graph(graph)


def test_identifier_dimensions_become_fanout_safe_business_labels():
    domain = packet()
    domain["join_opportunities"] = [
        {
            "shared_key": "product_id",
            "participants": [
                {"relation": "commerce.sales", "estimated_rows": 100, "business_label_columns": []},
                {
                    "relation": "commerce.products",
                    "estimated_rows": 10,
                    "business_label_columns": ["product_name", "product_category"],
                },
            ],
        }
    ]
    domain["relations"][1]["columns"] = [
        {"name": "product_name", "distinct_estimate": 10},
        {"name": "product_category", "distinct_estimate": 3},
    ]
    domain["relations"][0]["columns"] = [{"name": "product_id"}]
    graph = _fallback_graph(domain)
    metric = graph["metrics"][0]
    metric["dimensions"] = [{"key": "product_id", "name": "Product ID"}]
    metric["primary_dimension_key"] = "product_id"
    metric["event_sql"] = (
        "SELECT time_id AS occurred_at, amount_sold AS value, "
        "jsonb_build_object('product_id',product_id) AS dimensions FROM commerce.sales"
    )

    removed, enriched = _sanitize_identifier_dimensions(domain, graph)

    assert removed == ["amount_sold:product_id"]
    assert enriched == [
        "amount_sold:product_id->product_category",
        "amount_sold:product_id->product_name",
    ]
    assert metric["dimensions"] == [
        {"key": "product_category", "name": "Product Category"},
        {"key": "product_name", "name": "Product Name"},
    ]
    assert "GROUP BY regexp_replace(\"product_id\"::text" in metric["event_sql"]
    assert "regexp_replace(source_events.dimensions::jsonb->>'product_id'" in metric["event_sql"]


def test_identifier_sanitizer_removes_model_columns_absent_from_referenced_source():
    domain = packet()
    domain["relations"][0]["columns"] = [{"name": "product_id"}]
    graph = _fallback_graph(domain)
    metric = graph["metrics"][0]
    metric["dimensions"] = [
        {"key": "product_id", "name": "Product ID"},
        {"key": "country_id", "name": "Country ID"},
    ]
    metric["event_sql"] = (
        "SELECT time_id AS occurred_at, amount_sold AS value, "
        "jsonb_build_object('product_id',product_id,'country_id',country_id) AS dimensions "
        "FROM commerce.sales"
    )

    _sanitize_identifier_dimensions(domain, graph)

    assert "country_id" not in metric["event_sql"]


def test_value_graph_normalization_pins_domain_and_source_scope():
    graph = normalize_value_graph(
        packet(),
        {
            "name": "  Commerce   value system ",
            "summary": "  How sales become value. ",
            "primary_metric_key": None,
            "metrics": [],
            "edges": [],
            "open_questions": [],
        },
    )
    assert graph["domain_key"] == "commerce"
    assert graph["name"] == "Commerce value system"
    assert graph["source_relations"] == ["commerce.products", "commerce.sales"]


def test_value_graph_relation_grouping_is_domain_first():
    assert group_relations_by_schema(
        ["sh.sales", "crm.contacts", "sh.products", "crm.activities"]
    ) == {
        "crm": ["crm.activities", "crm.contacts"],
        "sh": ["sh.products", "sh.sales"],
    }


def test_value_domain_plan_supports_overlapping_semantic_membership_and_exact_joins():
    planning_packet = {
        "relations": [
            {"relation": "crime_feeds.boston_incidents", "columns": [{"name": "offense_code"}]},
            {"relation": "crime_feeds.boston_offense_codes", "columns": [{"name": "code"}]},
            {"relation": "crime_feeds.chicago_incidents", "columns": []},
        ]
    }
    plan = normalize_value_domain_plan(
        planning_packet,
        {
            "domains": [{
                "domain_key": "public_safety",
                "name": "Public Safety",
                "summary": "Incident reporting and response.",
                "concepts": [
                    {"concept_key": "incident", "name": "Incident", "kind": "event", "description": "A reported event."},
                    {"concept_key": "offense", "name": "Offense", "kind": "classification", "description": "How an incident is classified."},
                ],
                "processes": [{
                    "process_key": "incident_reporting", "name": "Incident reporting",
                    "description": "Report and classify incidents.",
                    "concept_keys": ["incident", "offense"],
                }],
                "systems": [
                    {
                        "system_key": "reported_incidents",
                        "name": "Reported Incidents",
                        "summary": "Incident volume and classification.",
                        "relationship_mode": "mixed",
                        "memberships": [
                            {"relation": "crime_feeds.boston_incidents", "role": "event_stream", "concept_keys": ["incident", "offense"], "rationale": "Boston event stream.", "confidence": 0.98},
                            {"relation": "crime_feeds.boston_offense_codes", "role": "reference", "concept_keys": ["offense"], "rationale": "Boston offense lookup.", "confidence": 0.99},
                            {"relation": "crime_feeds.chicago_incidents", "role": "event_stream", "concept_keys": ["incident", "offense"], "rationale": "Parallel Chicago stream.", "confidence": 0.96},
                        ],
                        "relationships": [{
                            "relationship": "joins_to",
                            "left_relation": "crime_feeds.boston_incidents",
                            "right_relation": "crime_feeds.boston_offense_codes",
                            "left_columns": ["offense_code"],
                            "right_columns": ["code"],
                            "rationale": "Documented offense code lookup.",
                            "confidence": 0.99,
                        }],
                        "analytical_questions": ["How do incident volumes differ by offense?"],
                        "gaps": ["No population denominator."],
                    },
                    {
                        "system_key": "offense_taxonomy",
                        "name": "Offense Taxonomy",
                        "summary": "How reported incidents are classified.",
                        "relationship_mode": "joined",
                        "memberships": [
                            {"relation": "crime_feeds.boston_incidents", "role": "event_stream", "concept_keys": ["incident", "offense"], "rationale": "Carries offense codes.", "confidence": 0.95},
                            {"relation": "crime_feeds.boston_offense_codes", "role": "reference", "concept_keys": ["offense"], "rationale": "Defines the codes.", "confidence": 0.99},
                        ],
                        "relationships": [{
                            "relationship": "joins_to",
                            "left_relation": "crime_feeds.boston_incidents",
                            "right_relation": "crime_feeds.boston_offense_codes",
                            "left_columns": ["offense_code"],
                            "right_columns": ["code"],
                            "rationale": "Documented lookup.",
                            "confidence": 0.99,
                        }],
                        "analytical_questions": [],
                        "gaps": [],
                    },
                ],
            }],
            "unassigned_relations": [],
        },
    )

    assert plan["domains"][0]["domain_key"] == "public_safety"
    assert [system["system_key"] for system in plan["systems"]] == [
        "offense_taxonomy", "reported_incidents"
    ]
    assert sum(
        membership["relation"] == "crime_feeds.boston_incidents"
        for system in plan["systems"]
        for membership in system["memberships"]
    ) == 2
    assert plan["systems"][0]["relationships"][0]["right_columns"] == ["code"]


def test_value_domain_plan_rejects_table_inventory_omissions():
    planning_packet = {
        "relations": [
            {"relation": "sales.orders"},
            {"relation": "sales.customers"},
        ]
    }
    with pytest.raises(ContractError, match="does not account for every relation"):
        normalize_value_domain_plan(
            planning_packet,
            {"domains": [{
                "domain_key": "commerce",
                "name": "Commerce",
                "summary": "Selling products.",
                "concepts": [{"concept_key": "order", "name": "Order", "kind": "event", "description": "A purchase."}],
                "processes": [],
                "systems": [{
                    "system_key": "sales",
                    "name": "Sales",
                    "summary": "Order activity.",
                    "relationship_mode": "standalone",
                    "memberships": [{"relation": "sales.orders", "role": "transaction", "concept_keys": ["order"], "rationale": "Order facts.", "confidence": 0.9}],
                    "relationships": [],
                    "analytical_questions": [],
                    "gaps": [],
                }],
            }], "unassigned_relations": []},
        )


def test_value_domain_plan_preserves_cross_system_identifier_as_weak_candidate():
    packet = {
        "relations": [
            {"relation": "legacy.contacts", "columns": [{"name": "account_ref"}]},
            {"relation": "legacy.invoices", "columns": [{"name": "account_ref"}]},
        ],
        "relationship_evidence": [{
            "kind": "candidate_shared_identifier",
            "shared_key": "account_ref",
            "participants": [
                {"relation": "legacy.contacts"},
                {"relation": "legacy.invoices"},
            ],
            "purpose": "compare coverage before claiming an exact join",
        }],
    }
    plan = normalize_value_domain_plan(packet, {
        "domains": [{
            "domain_key": "customer_operations",
            "name": "Customer Operations",
            "summary": "Customer-facing activity.",
            "concepts": [{
                "concept_key": "account", "name": "Account", "kind": "object",
                "description": "A commercial account.",
            }],
            "processes": [],
            "systems": [
                {
                    "system_key": "contact_directory", "name": "Contact Directory",
                    "summary": "Known contacts.", "relationship_mode": "standalone",
                    "memberships": [{
                        "relation": "legacy.contacts", "role": "snapshot",
                        "concept_keys": ["account"], "rationale": "Contact export.",
                        "confidence": 0.8,
                    }],
                    "relationships": [{
                        "relationship": "comparable_to",
                        "left_relation": "legacy.contacts",
                        "right_relation": "legacy.invoices",
                        "subject_concept_key": "account",
                        "object_concept_key": None,
                        "left_columns": ["account_ref"],
                        "right_columns": ["account_ref"],
                        "rationale": "Repeated account_ref needs validation.",
                        "confidence": 0.6,
                    }],
                    "analytical_questions": [], "gaps": [],
                },
                {
                    "system_key": "invoice_cycle", "name": "Invoice Cycle",
                    "summary": "Billing activity.", "relationship_mode": "standalone",
                    "memberships": [{
                        "relation": "legacy.invoices", "role": "transaction",
                        "concept_keys": ["account"], "rationale": "Invoice export.",
                        "confidence": 0.8,
                    }],
                    "relationships": [], "analytical_questions": [], "gaps": [],
                },
            ],
        }],
        "unassigned_relations": [],
    })

    assert plan["cross_system_candidates"] == [{
        "candidate_key": plan["cross_system_candidates"][0]["candidate_key"],
        "kind": "candidate_shared_identifier",
        "shared_key": "account_ref",
        "relations": ["legacy.contacts", "legacy.invoices"],
        "system_keys": ["contact_directory", "invoice_cycle"],
        "domain_keys": ["customer_operations"],
        "exact_join": False,
        "status": "needs_validation",
        "rationale": "compare coverage before claiming an exact join",
    }]
    assert plan["systems"][0]["relationships"] == []


def test_value_domain_plan_hoists_unsupported_weak_cross_system_suggestion():
    packet = {
        "relations": [
            {"relation": "legacy.contacts", "columns": [{"name": "account_ref"}]},
            {"relation": "legacy.incidents", "columns": [{"name": "case_ref"}]},
        ],
        "relationship_evidence": [],
    }
    plan = normalize_value_domain_plan(packet, {
        "domains": [{
            "domain_key": "customer_operations",
            "name": "Customer Operations",
            "summary": "Customer-facing activity.",
            "concepts": [{
                "concept_key": "account", "name": "Account", "kind": "object",
                "description": "A commercial account.",
            }],
            "processes": [],
            "systems": [
                {
                    "system_key": "contact_directory", "name": "Contact Directory",
                    "summary": "Known contacts.", "relationship_mode": "standalone",
                    "memberships": [{
                        "relation": "legacy.contacts", "role": "snapshot",
                        "concept_keys": ["account"], "rationale": "Contact export.",
                        "confidence": 0.8,
                    }],
                    "relationships": [{
                        "relationship": "comparable_to",
                        "left_relation": "legacy.contacts",
                        "right_relation": "legacy.incidents",
                        "subject_concept_key": "account",
                        "object_concept_key": None,
                        "left_columns": [], "right_columns": [],
                        "rationale": "Possibly related customer activity.",
                        "confidence": 0.55,
                    }],
                    "analytical_questions": [], "gaps": [],
                },
                {
                    "system_key": "incident_log", "name": "Incident Log",
                    "summary": "Support incidents.", "relationship_mode": "standalone",
                    "memberships": [{
                        "relation": "legacy.incidents", "role": "event_stream",
                        "concept_keys": ["account"], "rationale": "Incident export.",
                        "confidence": 0.8,
                    }],
                    "relationships": [], "analytical_questions": [], "gaps": [],
                },
            ],
        }],
        "unassigned_relations": [],
    })

    assert plan["systems"][0]["relationships"] == []
    assert plan["cross_system_candidates"] == [{
        "candidate_key": plan["cross_system_candidates"][0]["candidate_key"],
        "kind": "model_suggested_relationship",
        "relationship": "comparable_to",
        "relations": ["legacy.contacts", "legacy.incidents"],
        "system_keys": ["contact_directory", "incident_log"],
        "domain_keys": ["customer_operations"],
        "exact_join": False,
        "status": "needs_evidence",
        "rationale": "Possibly related customer activity.",
        "confidence": 0.55,
    }]


def test_value_domain_discovery_retries_the_same_strong_lane():
    calls = []
    progress = []

    class Client:
        def complete_json(self, **_kwargs):
            calls.append(_kwargs)
            if len(calls) == 1:
                raise TimeoutError("slow frontier response")
            return json.dumps({
                "domains": [{
                    "domain_key": "commerce", "name": "Commerce", "summary": "Selling products.",
                    "concepts": [{"concept_key": "order", "name": "Order", "kind": "event", "description": "A purchase."}],
                    "processes": [],
                    "systems": [{
                        "system_key": "sales", "name": "Sales", "summary": "Order activity.",
                        "relationship_mode": "standalone",
                        "memberships": [{"relation": "sales.orders", "role": "transaction", "concept_keys": ["order"], "rationale": "Order facts.", "confidence": 0.9}],
                        "relationships": [], "analytical_questions": [], "gaps": [],
                    }],
                }], "unassigned_relations": [],
            }), {"model_version": "frontier-v1"}

    plan, receipt = discover_value_domains(
        {"relations": [{"relation": "sales.orders"}]},
        Client(),
        progress_callback=progress.append,
    )

    assert len(calls) == 2
    assert plan["domains"][0]["domain_key"] == "commerce"
    assert plan["systems"][0]["system_key"] == "sales"
    assert receipt["generation_receipts"][0]["model_version"] == "frontier-v1"
    assert progress[:3] == [
        {"event": "planning_started", "attempt": 1, "attempt_limit": 2},
        {
            "event": "planning_failed",
            "attempt": 1,
            "attempt_limit": 2,
            "error_class": "TimeoutError",
            "error": "TimeoutError: slow frontier response",
        },
        {"event": "planning_started", "attempt": 2, "attempt_limit": 2},
    ]


def test_family_consolidation_requires_one_typed_decision_per_candidate():
    packet = {
        "source": {"graph_id": "source-graph"},
        "candidates": [{
            "candidate_id": "candidate-1",
            "candidate_fingerprint": "fingerprint-1",
            "target": {"graph_id": "target-graph"},
            "evidence": {
                "same_business_domain": True,
                "shared_concepts": ["incident"],
            },
        }],
    }
    result = normalize_family_consolidations(packet, {"decisions": [{
        "candidate_id": "candidate-1",
        "relationship_kind": "parallel_population",
        "confidence": 0.94,
        "proposed_business_domain_key": "public_safety",
        "proposed_business_domain_name": "Public Safety",
        "proposed_family_key": "reported_incidents",
        "proposed_family_name": "Reported Incidents",
        "rationale": "Both systems observe incident reporting for separate cities.",
        "evidence_refs": ["same_business_domain", "shared_concepts"],
        "caveats": ["City records are comparable, not directly joinable."],
    }]})

    assert result["schema_version"] == FAMILY_CONSOLIDATION_SCHEMA_VERSION
    assert result["decisions"][0]["source_graph_id"] == "source-graph"
    assert result["decisions"][0]["target_graph_id"] == "target-graph"
    assert result["decisions"][0]["relationship_kind"] == "parallel_population"

    with pytest.raises(ContractError, match="omitted candidates"):
        normalize_family_consolidations(packet, {"decisions": []})


def test_family_consolidation_uses_one_compact_strong_model_call():
    packet = {
        "source": {"graph_id": "source-graph", "value_system": {"name": "Reported Incidents"}},
        "candidates": [{
            "candidate_id": "candidate-1",
            "candidate_fingerprint": "fingerprint-1",
            "target": {"graph_id": "target-graph", "value_system": {"name": "Offense Mix"}},
            "evidence": {"same_business_domain": True},
        }],
    }
    calls = []

    class Client:
        def complete_json(self, **kwargs):
            calls.append(kwargs)
            return json.dumps({"decisions": [{
                "candidate_id": "candidate-1",
                "relationship_kind": "same_family",
                "confidence": 0.91,
                "proposed_business_domain_key": "public_safety",
                "proposed_business_domain_name": "Public Safety",
                "proposed_family_key": "reported_incidents",
                "proposed_family_name": "Reported Incidents",
                "rationale": "Offense mix is a categorical view of the same incident stream.",
                "evidence_refs": ["same_business_domain"],
                "caveats": [],
            }]}), {"model_version": "hermes/frontier"}

    result, receipt = discover_family_consolidations(packet, Client())

    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 4_500
    assert "compact semantic dossiers" in calls[0]["user"]
    assert result["decisions"][0]["relationship_kind"] == "same_family"
    assert receipt["generation_receipts"][0]["model_version"] == "hermes/frontier"


def test_family_consolidation_retry_exposes_the_actual_failure_then_recovers():
    packet = {
        "source": {"graph_id": "fulfillment-graph"},
        "candidates": [{
            "candidate_id": "candidate-1",
            "candidate_fingerprint": "fingerprint-1",
            "target": {"graph_id": "payments-graph"},
            "evidence": {"same_business_domain": True, "shared_concepts": ["order"]},
        }],
    }
    events = []

    class Client:
        calls = 0

        def complete_json(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("Hermes upstream read timed out")
            return json.dumps({"decisions": [{
                "candidate_id": "candidate-1",
                "relationship_kind": "extension",
                "confidence": 0.88,
                "proposed_business_domain_key": "customer_operations",
                "proposed_business_domain_name": "Customer Operations",
                "proposed_family_key": "order_lifecycle",
                "proposed_family_name": "Order Lifecycle",
                "rationale": "Payments and fulfillment are linked stages of an order lifecycle.",
                "evidence_refs": ["same_business_domain", "shared_concepts"],
                "caveats": ["Operational grains differ."],
            }]}), {"model_version": "hermes/frontier"}

    result, _receipt = discover_family_consolidations(
        packet, Client(), attempts=2, progress_callback=events.append
    )

    failure = next(event for event in events if event["event"] == "consolidation_failed")
    assert failure["error_class"] == "TimeoutError"
    assert failure["error"] == "TimeoutError: Hermes upstream read timed out"
    assert result["decisions"][0]["relationship_kind"] == "extension"


def test_value_graph_reports_live_inference_and_validation_progress(monkeypatch):
    events = []

    class Client:
        def complete_json(self, **_kwargs):
            return json.dumps(
                {
                    "name": "Commerce value system",
                    "summary": "No governed metric is implied by this fixture.",
                    "primary_metric_key": None,
                    "metrics": [],
                    "edges": [],
                    "open_questions": [],
                }
            ), {"latency_ms": 42.5, "model_version": "fixture/model-v1"}

    monkeypatch.setattr(
        "business_topology.value_graph.evaluate_value_graph",
        lambda *_args, **_kwargs: {"metrics": []},
    )

    _graph, receipt = discover_value_graph(
        object(), packet(), Client(), progress_callback=events.append
    )

    assert [event["event"] for event in events] == [
        "inference_started",
        "inference_completed",
        "validation_completed",
    ]
    assert events[0] == {
        "event": "inference_started",
        "attempt": 1,
        "attempt_limit": 2,
    }
    assert events[1]["latency_ms"] == 42.5
    assert events[1]["model_version"] == "fixture/model-v1"
    assert len(receipt["generation_receipts"]) == 1


class _ProbeConn:
    """Fake connection: answers the fallback probe per (relation, time, measure)."""

    def __init__(self, live: set[tuple[str, str, str]], *, boom: set[tuple[str, str, str]] = frozenset()):
        self.live = live
        self.boom = boom
        self.rollbacks = 0
        self.probes: list[tuple[str, str, str]] = []

    def execute(self, sql, params=None):
        if sql.startswith("SET LOCAL"):
            return self
        import re as _re

        m = _re.search(r'FROM "([^"]+)"\."([^"]+)" WHERE "([^"]+)" IS NOT NULL AND "([^"]+)" IS NOT NULL', sql)
        assert m, sql
        key = (f"{m.group(1)}.{m.group(2)}", m.group(3), m.group(4))
        self.probes.append(key)
        if key in self.boom:
            raise RuntimeError("relation does not exist")
        self._row = (1,) if key in self.live else None
        return self

    def fetchone(self):
        return self._row

    def rollback(self):
        self.rollbacks += 1


def _co_packet():
    domain = packet()
    domain["domain_key"] = "co"
    domain["fact_candidates"] = [
        {
            "relation": "co.products",
            "score": 12,
            "measure_columns": ["unit_price"],
            "time_columns": ["image_last_updated"],
            "shared_key_columns": ["product_id"],
        },
        {
            "relation": "co.orders",
            "score": 9,
            "measure_columns": ["order_total"],
            "time_columns": ["created_tms", "order_tms"],
            "shared_key_columns": ["customer_id"],
        },
    ]
    return domain


def test_fallback_without_connection_keeps_first_candidate_columns():
    graph = _fallback_graph(_co_packet())
    assert graph["primary_metric_key"] == "unit_price"
    assert '"image_last_updated"::timestamptz' in graph["metrics"][0]["event_sql"]


def test_fallback_probes_for_a_stream_that_actually_yields_events():
    # co.products.image_last_updated is entirely NULL (probe finds no row);
    # co.orders.created_tms does not exist (probe raises); order_tms works.
    conn = _ProbeConn(
        live={("co.orders", "order_tms", "order_total")},
        boom={("co.orders", "created_tms", "order_total")},
    )
    graph = _fallback_graph(_co_packet(), conn)
    validate_value_graph(graph)
    assert graph["primary_metric_key"] == "order_total"
    metric = graph["metrics"][0]
    assert '"order_tms"::timestamptz AS occurred_at' in metric["event_sql"]
    assert 'FROM "co"."orders"' in metric["event_sql"]
    assert conn.probes == [
        ("co.products", "image_last_updated", "unit_price"),
        ("co.orders", "created_tms", "order_total"),
        ("co.orders", "order_tms", "order_total"),
    ]
    assert conn.rollbacks == 1  # the raising probe was rolled back, then skipped


def test_fallback_keeps_a_baseline_count_when_no_richer_stream_executes():
    conn = _ProbeConn(live=set())
    graph = _fallback_graph(_co_packet(), conn)
    validate_value_graph(graph)
    assert graph["primary_metric_key"] == "sales_row_count"
    assert graph["metrics"][0]["unit"] == "count"
    assert "statement_timestamp()" in graph["metrics"][0]["event_sql"]
    assert "baseline row-count" in graph["summary"]


def test_baseline_count_uses_event_time_and_low_cardinality_dimensions():
    domain = packet()
    domain["relations"][0]["columns"] = [
        {
            "name": "time_id",
            "semantic_role": "time",
            "distinct_estimate": 90,
        },
        {
            "name": "channel",
            "semantic_role": "business_label",
            "distinct_estimate": 4,
        },
        {
            "name": "order_id",
            "semantic_role": "key",
            "distinct_estimate": 100,
        },
    ]

    graph = _baseline_count_graph(domain)

    validate_value_graph(graph)
    metric = graph["metrics"][0]
    assert metric["metric_key"] == "sales_row_count"
    assert metric["dimensions"] == [{"key": "channel", "name": "Channel"}]
    assert '"time_id"::timestamptz AS occurred_at' in metric["event_sql"]
    assert "'channel',\"channel\"::text" in metric["event_sql"]


def test_metric_free_model_response_gets_a_baseline_count(monkeypatch):
    class Client:
        def complete_json(self, **_kwargs):
            return json.dumps({
                "name": "Commerce",
                "summary": "A current business population.",
                "primary_metric_key": None,
                "metrics": [],
                "edges": [],
                "open_questions": [],
            }), {"model_version": "fixture"}

    captured = {}

    def evaluate(_conn, graph):
        captured.update(graph)
        return {"metrics": [{}], "validated_metrics": 1}

    monkeypatch.setattr("business_topology.value_graph.evaluate_value_graph", evaluate)
    graph, receipt = discover_value_graph(object(), packet(), Client())

    assert graph["primary_metric_key"] == "sales_row_count"
    assert captured["metrics"][0]["unit"] == "count"
    assert receipt["baseline_metric_added"] is True


class _SummaryConn:
    """Fake connection for evaluate_metric: returns one aggregate summary row."""

    def __init__(self, row):
        self.row = row

    def execute(self, sql, params=None):
        return self

    def fetchone(self):
        return self.row


def test_evaluate_metric_reports_empty_streams_before_dimension_shape():
    from business_topology.value_graph import evaluate_metric

    metric = {"metric_key": "unit_price", "event_sql": "SELECT 1", "dimensions": []}
    # Zero rows: bool_and() would be NULL; the message must say "no events",
    # not blame the dimension shape.
    with pytest.raises(ContractError, match="returned no value-bearing events"):
        evaluate_metric(_SummaryConn((0, 0, None, None, 0, None)), metric)
    # Rows present but a non-object dimensions column: now it is a shape error.
    with pytest.raises(ContractError, match="object-shaped dimensions"):
        evaluate_metric(_SummaryConn((5, 5, None, None, 10, False)), metric)
    discover_family_consolidations,
