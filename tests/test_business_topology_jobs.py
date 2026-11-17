from __future__ import annotations

import copy
import json

from services.calliope.business_topology_jobs import (
    _compact_consolidation_graph,
    _domain_plan_cache_projection,
    _failed_cached_value_graph_receipt,
    _family_candidate,
    _graph_matches_packet,
    _load_family_consolidation_packets,
    _load_compatible_domain_plan_cache,
    _load_prior_semantic_evidence,
    _packet_requires_semantic_reconsideration,
    _value_graph_cache_projection,
)


def _semantic_packet() -> dict:
    return {
        "schema_version": "rvbbit.business-value.packet.v2",
        "domain_key": "reported_incidents",
        "relations": [
            {
                "relation": "crime_feeds.chicago_crimes",
                "estimated_rows": 42,
                "catalog_summary": "first crawl",
                "columns": [
                    {
                        "name": "incident_id",
                        "data_type": "text",
                        "type_family": "text",
                        "semantic_role": "identifier",
                        "measure_hint": False,
                        "time_hint": False,
                        "key_hint": True,
                        "null_fraction": 0.1,
                    }
                ],
            }
        ],
        "foreign_key_relationships": [],
        "semantic_model": {
            "business_domain": {"key": "public_safety", "name": "Public Safety"},
            "value_system_key": "reported_incidents",
            "value_system_name": "Reported Incidents",
            "value_system_summary": "How agencies record incidents.",
            "relationship_mode": "parallel",
            "concepts": [],
            "processes": [],
            "source_memberships": [],
            "semantic_relationships": [],
            "analytical_questions": [],
            "gaps": [],
        },
    }


def test_value_graph_cache_projection_ignores_enrichment_and_live_statistics():
    first = _semantic_packet()
    second = copy.deepcopy(first)
    second["topology_summaries"] = [{"name": "new context crawl"}]
    second["relations"][0]["estimated_rows"] = 9000
    second["relations"][0]["catalog_summary"] = "new prose"
    second["relations"][0]["columns"][0]["null_fraction"] = 0.9

    assert _value_graph_cache_projection(first) == _value_graph_cache_projection(second)


def test_domain_plan_cache_projection_ignores_receipt_identity_and_order():
    first = _semantic_packet()
    first["prior_semantic_evidence"] = [
        {
            "graph_id": "graph-old-a",
            "value_system_key": "support_cases",
            "source_relations": ["messy.case_dump"],
            "requires_review": False,
            "dimension_censuses": [
                {
                    "metric_key": "case_count",
                    "dimension_key": "severity",
                    "observed_at": "2026-08-27T12:00:00Z",
                    "coverage_complete": True,
                    "members": [{"value": "rare", "row_count": 2}],
                }
            ],
            "family_relationships": [
                {
                    "relationship_id": 41,
                    "workflow_run_id": "run-old",
                    "source_graph_id": "graph-old-a",
                    "target_graph_id": "graph-old-b",
                    "source_family_id": "family-old-a",
                    "target_family_id": "family-old-b",
                    "proposed_at": "2026-08-27T12:00:00Z",
                    "proposed_by": "postgres",
                    "candidate_fingerprint": "stable-candidate",
                    "relationship_kind": "distinct_family",
                    "review_state": "proposed",
                    "confidence": 0.88,
                    "rationale": "Different grains.",
                }
            ],
        },
        {
            "graph_id": "graph-old-c",
            "value_system_key": "account_activity",
            "source_relations": ["messy.usage_dump"],
            "requires_review": False,
            "dimension_censuses": [],
            "family_relationships": [],
        },
    ]
    second = copy.deepcopy(first)
    second["prior_semantic_evidence"].reverse()
    support = second["prior_semantic_evidence"][1]
    support["graph_id"] = "graph-new-a"
    support["dimension_censuses"][0]["observed_at"] = "2026-08-28T12:00:00Z"
    relationship = support["family_relationships"][0]
    relationship.update(
        {
            "relationship_id": 99,
            "workflow_run_id": "run-new",
            "source_graph_id": "graph-new-a",
            "target_graph_id": "graph-new-b",
            "source_family_id": "family-new-a",
            "target_family_id": "family-new-b",
            "proposed_at": "2026-08-28T12:00:00Z",
        }
    )

    assert _domain_plan_cache_projection(first) == _domain_plan_cache_projection(second)

    relationship["relationship_kind"] = "same_family"
    assert _domain_plan_cache_projection(first) != _domain_plan_cache_projection(second)


def test_domain_plan_cache_projection_deduplicates_family_decision_receipts():
    first = _semantic_packet()
    decision = {
        "candidate_fingerprint": "first-candidate",
        "relationship_kind": "distinct_family",
        "review_state": "proposed",
        "source_value_system_key": "usage_activity",
        "target_value_system_key": "incident_notes",
        "source_business_domain_key": "product_delivery",
        "target_business_domain_key": "product_delivery",
        "confidence": 0.72,
        "rationale": "Different event grains.",
    }
    first["prior_semantic_evidence"] = [{
        "value_system_key": "usage_activity",
        "source_relations": ["messy.usage_dump"],
        "requires_review": False,
        "dimension_censuses": [],
        "family_relationships": [decision],
    }]
    second = copy.deepcopy(first)
    second["prior_semantic_evidence"][0]["family_relationships"].append({
        **decision,
        "candidate_fingerprint": "new-candidate",
        "confidence": 0.91,
        "rationale": "New wording for the same durable decision.",
    })

    assert _domain_plan_cache_projection(first) == _domain_plan_cache_projection(second)

    second["prior_semantic_evidence"][0]["family_relationships"][1][
        "review_state"
    ] = "accepted"
    assert _domain_plan_cache_projection(first) != _domain_plan_cache_projection(second)


def test_compatible_domain_plan_cache_recovers_prior_normalized_plan(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("WAREHOUSE_BUSINESS_TOPOLOGY_DIR", str(tmp_path))
    packet = _semantic_packet()
    packet["prior_semantic_evidence"] = [{
        "graph_id": "old-graph",
        "value_system_key": "reported_incidents",
        "source_relations": ["crime_feeds.chicago_crimes"],
        "requires_review": False,
        "dimension_censuses": [],
        "family_relationships": [],
    }]
    run_root = tmp_path / "completed-run"
    run_root.mkdir()
    (run_root / "value-domain-plan.private.json").write_text(json.dumps({
        "planning_packet": packet,
        "plan": {"domains": [{"domain_key": "public_safety"}]},
        "receipt": {"model_version": "frontier-v1"},
    }))
    current = copy.deepcopy(packet)
    current["prior_semantic_evidence"][0]["graph_id"] = "new-graph"

    recovered = _load_compatible_domain_plan_cache(current)

    assert recovered is not None
    assert recovered["compatible_run_plan"] == "completed-run"
    assert recovered["plan"] == {"domains": [{"domain_key": "public_safety"}]}


def test_current_graph_must_match_sources_and_the_approved_semantic_plan():
    packet = _semantic_packet()
    graph = {
        **copy.deepcopy(packet["semantic_model"]),
        "domain_key": "reported_incidents",
        "name": "Reported Incidents",
        "summary": "How agencies record incidents.",
        "source_relations": ["crime_feeds.chicago_crimes"],
    }

    assert _graph_matches_packet(graph, packet)
    graph["source_relations"] = ["crime_feeds.detroit_crimes"]
    assert not _graph_matches_packet(graph, packet)


def test_exact_dimension_evidence_invalidates_cache_and_review_bypasses_reuse():
    first = _semantic_packet()
    second = copy.deepcopy(first)
    second["prior_semantic_evidence"] = [{
        "value_system_key": "reported_incidents",
        "requires_review": True,
        "dimension_censuses": [{
            "dimension_key": "class",
            "coverage_complete": True,
            "member_list_complete": True,
            "members": [{"value": "Class C", "row_count": 30}],
        }],
    }]

    assert _value_graph_cache_projection(first) != _value_graph_cache_projection(second)
    assert _packet_requires_semantic_reconsideration(second)


def test_prior_semantic_evidence_withholds_sampled_members():
    exact = {
        "metric_key": "filed_reports",
        "dimension_key": "class",
        "coverage_mode": "exact",
        "coverage_complete": True,
        "member_list_complete": True,
        "rows_observed": 5035,
        "member_count": 3,
        "members": [{"value": "Class C", "row_count": 30}],
        "taxonomy_changed": False,
    }
    sampled = {
        "metric_key": "people",
        "dimension_key": "name",
        "coverage_mode": "bounded_sample",
        "coverage_complete": False,
        "member_list_complete": False,
        "rows_observed": 250000,
        "member_count": 128,
        "members": [{"value": "Private Person", "row_count": 1}],
        "taxonomy_changed": True,
        "added_values": ["Private Person"],
    }

    class Result:
        def __init__(self, *, one=None, rows=None):
            self.one = one
            self.rows = rows or []

        def fetchone(self):
            return self.one

        def fetchall(self):
            return self.rows

    class Connection:
        def __init__(self):
            self.statements = []

        def execute(self, statement, _parameters=None):
            self.statements.append(statement)
            if "to_regclass" in statement:
                return Result(one=(True,))
            return Result(rows=[(
                "graph-1", "sightings", "Filed Sightings",
                ["public.bigfoot_sightings_locations"],
                {"domain_key": "field_sighting_reports", "name": "Field Sighting Reports"},
                0, 0, [exact, sampled], [{
                    "relationship_kind": "parallel_population",
                    "review_state": "accepted",
                    "target_value_system_key": "regional_sightings",
                }],
            )])

    connection = Connection()
    evidence = _load_prior_semantic_evidence(
        connection, ["public.bigfoot_sightings_locations"]
    )

    assert evidence[0]["dimension_censuses"][0]["members"][0]["value"] == "Class C"
    assert evidence[0]["dimension_censuses"][1]["members"] == []
    assert evidence[0]["dimension_censuses"][1]["member_values_withheld"] is True
    assert evidence[0]["family_relationships"][0]["review_state"] == "accepted"
    assert evidence[0]["requires_review"] is True
    grouped_columns = connection.statements[1].split("GROUP BY", 1)[1].split("ORDER BY", 1)[0]
    assert "current.updated_at" in grouped_columns


def test_cached_metric_timeout_is_explicit_and_does_not_claim_validation():
    receipt = _failed_cached_value_graph_receipt(
        {"generation_receipts": [{"model": "hermes"}]},
        TimeoutError("statement timeout"),
        kind="value_graph_database",
    )

    assert receipt["generation_receipts"] == []
    assert receipt["evaluation"]["metrics"] == []
    assert receipt["evaluation"]["validated_metrics"] == 0
    assert receipt["cache"]["revalidation"] == "failed"
    assert receipt["cache"]["original_generation_receipts"] == 1


def test_family_candidate_blocks_semantically_related_disjoint_city_populations():
    def compact(graph_id: str, system_key: str, city: str) -> dict:
        return _compact_consolidation_graph({
            "graph_id": graph_id,
            "family_id": None,
            "domain_key": system_key,
            "name": system_key.replace("_", " ").title(),
            "summary": "Incident reporting and offense classification.",
            "source_relations": [f"crime_feeds.{city}_incidents"],
            "graph": {
                "business_domain": {"domain_key": "public_safety", "name": "Public Safety"},
                "value_system_key": system_key,
                "concepts": [{"concept_key": "incident", "name": "Incident", "kind": "event"}],
                "processes": [{"process_key": "incident_reporting", "name": "Incident reporting", "concept_keys": ["incident"]}],
                "source_memberships": [{"relation": f"crime_feeds.{city}_incidents", "role": "event_stream", "concept_keys": ["incident"]}],
                "metrics": [{"metric_key": "incident_count", "name": "Incident count", "metric_kind": "outcome", "unit": "count", "grain": "one incident", "dimensions": [{"key": "offense"}]}],
            },
        })

    boston = compact("graph-boston", "reported_incidents", "boston")
    chicago = compact("graph-chicago", "offense_mix", "chicago")
    candidate = _family_candidate(boston, chicago)

    assert candidate is not None
    assert candidate["evidence"]["same_business_domain"] is True
    assert candidate["evidence"]["shared_concepts"] == ["incident"]
    assert candidate["evidence"]["source_overlap"] == 0
    assert candidate["evidence"]["candidate_score"] >= 0.5

    prose_revision = copy.deepcopy(chicago)
    prose_revision["value_system"]["name"] = "A newly worded display name"
    prose_revision["value_system"]["summary"] = "Regenerated narrative and metric prose."
    prose_revision["metrics"][0]["grain"] = "a differently worded grain"
    assert (
        _family_candidate(boston, prose_revision)["candidate_fingerprint"]
        == candidate["candidate_fingerprint"]
    )

    structural_revision = copy.deepcopy(chicago)
    structural_revision["concepts"].append({
        "key": "case", "name": "Case", "kind": "object",
    })
    assert (
        _family_candidate(boston, structural_revision)["candidate_fingerprint"]
        != candidate["candidate_fingerprint"]
    )

    same_sources = {**chicago, "source_relations": list(boston["source_relations"])}
    assert _family_candidate(boston, same_sources) is None

    unrelated = {**chicago, "business_domain": {"key": "workforce", "name": "Workforce"}, "concepts": [{"key": "employee", "name": "Employee", "kind": "actor"}], "processes": []}
    assert _family_candidate(boston, unrelated) is None


def test_family_candidate_generalizes_across_normal_business_systems():
    def compact(
        graph_id: str,
        system_key: str,
        domain_key: str,
        relation: str,
        concepts: list[str],
        process: str,
    ) -> dict:
        return _compact_consolidation_graph({
            "graph_id": graph_id,
            "family_id": None,
            "domain_key": system_key,
            "name": system_key.replace("_", " ").title(),
            "summary": "Synthetic acceptance dossier.",
            "source_relations": [relation],
            "graph": {
                "business_domain": {
                    "domain_key": domain_key,
                    "name": domain_key.replace("_", " ").title(),
                },
                "value_system_key": system_key,
                "concepts": [
                    {"concept_key": key, "name": key.title(), "kind": "object"}
                    for key in concepts
                ],
                "processes": [{
                    "process_key": process,
                    "name": process.replace("_", " ").title(),
                    "concept_keys": concepts,
                }],
                "source_memberships": [{
                    "relation": relation,
                    "role": "transaction",
                    "concept_keys": concepts,
                }],
                "metrics": [{
                    "metric_key": f"{system_key}_row_count",
                    "name": f"{system_key.title()} Row Count",
                    "metric_kind": "outcome",
                    "unit": "count",
                    "grain": "one operational record",
                    "dimensions": [{"key": "status"}],
                }],
            },
        })

    fulfillment = compact(
        "fulfillment-graph", "order_fulfillment", "customer_operations",
        "erp.shipments", ["order", "shipment"], "fulfill_order",
    )
    payments = compact(
        "payments-graph", "order_payments", "customer_operations",
        "billing.payment_events", ["order", "payment"], "collect_payment",
    )
    inventory = compact(
        "inventory-graph", "inventory_position", "supply_planning",
        "warehouse.daily_stock", ["inventory"], "replenish_inventory",
    )

    related = _family_candidate(fulfillment, payments)
    assert related is not None
    assert related["evidence"]["same_business_domain"] is True
    assert related["evidence"]["shared_concepts"] == ["order"]
    assert related["evidence"]["source_overlap"] == 0
    assert _family_candidate(fulfillment, inventory) is None


def test_family_packets_prefer_staged_revisions_and_handle_existing_dict_rows():
    def row(graph_id: str, system_key: str, relation: str, staged: bool) -> dict:
        return {
            "graph_id": graph_id,
            "family_id": None,
            "domain_key": system_key,
            "name": system_key.replace("_", " ").title(),
            "summary": "Public safety incidents and offense classification.",
            "source_relations": [relation],
            "staged": staged,
            "graph": {
                "business_domain": {"domain_key": "public_safety", "name": "Public Safety"},
                "value_system_key": system_key,
                "concepts": [{"concept_key": "incident", "name": "Incident", "kind": "event"}],
                "processes": [{"process_key": "reporting", "name": "Incident reporting", "concept_keys": ["incident"]}],
                "source_memberships": [{"relation": relation, "role": "event_stream", "concept_keys": ["incident"]}],
                "metrics": [{"metric_key": "incident_count", "name": "Incident count"}],
            },
        }

    rows = [
        row("staged-reports", "reported_incidents", "crime_feeds.boston_incidents", True),
        row("staged-offenses", "offense_mix", "crime_feeds.chicago_incidents", True),
        row("current-reports", "reported_incidents", "crime_feeds.boston_incidents", False),
        row("current-offenses", "offense_mix", "crime_feeds.detroit_incidents", False),
        row("current-stops", "traffic_stop_enforcement", "public.traffic_violations", False),
    ]

    class Result:
        def __init__(self, values):
            self.values = values

        def fetchall(self):
            return self.values

    class Connection:
        def execute(self, sql, _params):
            if "FROM rvbbit.business_value_graph_proposals proposal" in sql:
                return Result(rows)
            # Exercise the real dict-row shape used by candidate_conn.  The
            # fingerprint need not match; this proves reruns do not KeyError.
            return Result([{"candidate_fingerprint": "already-recorded"}])

    packets = _load_family_consolidation_packets(
        Connection(), ["staged-reports", "staged-offenses"]
    )
    targets = {
        candidate["target"]["graph_id"]
        for packet in packets
        for candidate in packet["candidates"]
    }

    assert "current-reports" not in targets
    assert "current-offenses" not in targets
    assert "staged-offenses" in targets or "staged-reports" in targets
