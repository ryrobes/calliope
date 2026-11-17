"""Agent-facing contracts for the Business Context visibility layer."""
from __future__ import annotations

import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import calliope  # noqa: E402
import server  # noqa: E402


def test_business_context_tools_publish_typed_discoverable_contracts():
    mcp = server._build_mcp()
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}

    search = tools["search_business_context"]
    assert "interpreted business systems" in search.description.lower()
    assert set(search.parameters["properties"]) == {"query", "limit", "statuses"}
    assert search.parameters["properties"]["limit"]["type"] == "integer"
    assert search.parameters["properties"]["statuses"]["anyOf"][0]["type"] == "array"

    packet = tools["business_context_for_question"]
    assert set(packet.parameters["required"]) == {"question"}
    assert set(packet.parameters["properties"]) == {"question"}
    assert "automatically available" in packet.description.lower()

    detail = tools["get_business_value_system"]
    assert set(detail.parameters["required"]) == {"handle"}
    assert set(detail.parameters["properties"]) == {"handle", "include_sql"}
    assert detail.parameters["properties"]["include_sql"]["type"] == "boolean"
    assert "formulas" in detail.description.lower()

    change = tools["business_metric_change"]
    assert set(change.parameters["required"]) == {
        "name", "dimension", "prior_start", "prior_end",
        "current_start", "current_end",
    }
    assert change.parameters["properties"]["limit"]["type"] == "integer"
    assert "validated contributor" in change.description.lower()

    status = tools["business_context_status"]
    assert set(status.parameters["properties"]) == {"domain"}
    assert "fingerprint" in status.description.lower()

    refresh = tools["refresh_business_context"]
    assert set(refresh.parameters["required"]) == {"domain"}
    assert refresh.parameters["properties"]["force"]["type"] == "boolean"
    assert "fingerprint-gated" in refresh.description.lower()

    changes = tools["business_context_changes"]
    assert set(changes.parameters["properties"]) == {"domain", "since", "limit"}
    assert changes.parameters["properties"]["limit"]["type"] == "integer"
    assert "source sql is not disclosed" in changes.description.lower()


def test_calliope_prompt_routes_business_reasoning_to_context_tools():
    instructions = calliope._instructions([], None)

    assert "BUSINESS CONTEXT" in instructions
    assert "use search_business_context before raw-table exploration" in instructions
    assert "get_business_value_system" in instructions
    assert "business_metric_change" in instructions
    assert "business_context_status" in instructions
    assert "business_context_changes" in instructions
    assert "refresh_business_context" in instructions
    assert "marked usable may guide exploration" in instructions
    assert "must not be presented as a promoted canonical metric or cube" in instructions


def test_value_system_family_migration_keeps_grains_inside_one_reconciled_system():
    root = HERE.parent.parent
    migration = (
        root / "crates" / "pg_rvbbit" / "sql" / "migrations"
        / "0341_business_value_system_families.sql"
    ).read_text()
    registry = (root / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text()

    assert "CREATE TABLE IF NOT EXISTS rvbbit.business_value_system_families" in migration
    assert "domain_key text NOT NULL UNIQUE" in migration
    assert "semantic_fingerprint" in migration
    assert "Display names, metric aliases, and cube time buckets do not create a new system" in migration
    assert "pending_graph_id" in migration and "review_state='needs_decision'" in migration
    assert "usable=true" in migration
    assert "Semantically identical repeat excavation" in migration
    assert '"0341_business_value_system_families"' in registry


def test_semantic_domain_migration_detaches_domain_identity_from_schema():
    root = HERE.parent.parent
    migration = (
        root / "crates" / "pg_rvbbit" / "sql" / "migrations"
        / "0343_business_topology_semantic_domains.sql"
    ).read_text()
    registry = (root / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text()

    assert "business_context_domain_sources" in migration
    assert "proposal.source_relations" in migration
    assert "_sync_metadata" in migration
    assert "v_schemas,v_relations" in migration
    assert '"0343_business_topology_semantic_domains"' in registry


def test_semantic_steward_reconciles_physical_scope_without_run_chaining():
    root = HERE.parent.parent
    migration = (
        root / "crates" / "pg_rvbbit" / "sql" / "migrations"
        / "0344_business_context_semantic_steward_reconciliation.sql"
    ).read_text()
    registry = (root / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text()

    workflow_trigger = migration.split(
        "CREATE OR REPLACE FUNCTION rvbbit._business_context_mirror_finished()", 1
    )[0]
    mirror_trigger = migration.split(
        "CREATE OR REPLACE FUNCTION rvbbit._business_context_mirror_finished()", 1
    )[1].split("-- Repair physical source scopes", 1)[0]

    assert "v_pending_domains" in workflow_trigger
    assert "unnest(proposal.source_relations)" in workflow_trigger
    assert "modeled_fingerprint=coalesce" in workflow_trigger
    assert "business_context_steward_tick()" not in workflow_trigger
    assert "business_context_mark_domain_dirty" in mirror_trigger
    assert "v_semantic_domain" not in mirror_trigger
    assert "data_dirty_since=coalesce" in mirror_trigger
    assert "domain.modeled_fingerprint IS NULL" in migration
    assert '"0344_business_context_semantic_steward_reconciliation"' in registry


def test_semantic_state_backfill_clears_only_resolvable_nonphysical_domains():
    root = HERE.parent.parent
    migration = (
        root / "crates" / "pg_rvbbit" / "sql" / "migrations"
        / "0345_business_context_semantic_state_backfill.sql"
    ).read_text()
    registry = (root / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text()

    assert "domain.pending_workflow_run_id IS NULL" in migration
    assert "domain.status IN ('pending_catalog','dirty','error')" in migration
    assert "NOT EXISTS" in migration and "pg_namespace" in migration
    assert "business_context_domain_snapshot" in migration
    assert "->>'relation_count')::integer>0" in migration
    assert "modeled_fingerprint=repairable.fingerprint" in migration
    assert '"0345_business_context_semantic_state_backfill"' in registry


def _value_system_detail():
    return {
        "handle": "value_system:73daee00-b7a3-4939-8ce7-ea8499946cc8",
        "graph_id": "73daee00-b7a3-4939-8ce7-ea8499946cc8",
        "domain_key": "sh",
        "name": "Sales Value System",
        "summary": "Revenue and its validated contributors.",
        "status": "promoted",
        "source_relations": ["sh.sales", "sh.products"],
        "primary_metric_key": "sales_amount",
        "metrics": [{
            "metric_key": "sales_amount",
            "metric_name": "Total Sales Amount",
            "description": "Revenue recognized from completed sales.",
            "metric_kind": "outcome",
            "unit": "currency",
            "formula": "amount_sold",
            "grain": "one sales transaction",
            "time_semantics": "transaction date",
            "dimensions": [{"key": "category", "name": "Category"}],
            "primary_dimension_key": "category",
            "confidence": 0.95,
            "is_primary": True,
            "governed_metric_name": "sh_sales_amount",
            "governed_metric_version": 1,
            "source_event_sql_retained": True,
            "source_event_sql_sha256": "a" * 64,
        }],
        "relationships": [{
            "parent_metric_key": "sales_amount",
            "child_metric_key": "sales_quantity",
            "relationship": "multiplies",
            "confidence": 0.85,
            "rationale": "Volume drives revenue.",
        }],
        "open_questions": ["Confirm the preferred currency."],
        "reconciliation": {
            "worker_version": "business-value-graph-v1",
            "all_dimension_reconciliations_passed": True,
            "metrics": [{
                "metric_key": "sales_amount",
                "event_rows": 10,
                "valued_rows": 10,
                "total_value": 250,
                "bucket": "year",
                "prior_period": {"period_start": "2025-01-01", "value": 100},
                "current_period": {"period_start": "2026-01-01", "value": 150},
                "total_change": 50,
                "changes": {"category": [{
                    "dimension_value": "Hardware",
                    "prior_value": 60,
                    "current_value": 105,
                    "change": 45,
                    "share_of_change": 0.9,
                }]},
                "reconciliations": {"category": {
                    "passed": True,
                    "prior_difference": 0,
                    "current_difference": 0,
                }},
                "cube_recommendation": {
                    "metric": "sales_amount",
                    "time_dimension": "occurred_at",
                    "time_grain": "year",
                    "dimensions": ["category"],
                    "source": "validated_metric_event_projection",
                },
            }],
        },
        "promotion": {
            "promotion_id": "315f1d46-7cb5-4032-a13a-6dfef8ece98a",
            "cube_name": "sh_value",
            "cube_version": 1,
            "materialization_grain": "month",
            "metric_versions": {"sales_amount": 1},
            "promoted_at": "2026-08-13T12:00:00Z",
            "last_refreshed_at": "2026-08-13T12:30:00Z",
        },
    }


def test_business_value_system_projects_native_stage_surface_with_value_river_proof():
    projected = calliope._project_tool_result(
        "get_business_value_system",
        _value_system_detail(),
        {"handle": "value_system:73daee00-b7a3-4939-8ce7-ea8499946cc8"},
        "value-system-call",
    )

    assert len(projected) == 1
    surface = projected[0]
    assert surface["kind"] == "value_system"
    assert surface["lineage_key"] == "value-system:73daee00-b7a3-4939-8ce7-ea8499946cc8"
    assert surface["presentation"]["view"] == "value_river"
    assert surface["payload"]["schema_version"] == "calliope.value-system-surface.v1"
    metric = surface["payload"]["metrics"][0]
    assert metric["evaluation"]["changes"]["category"][0]["dimension_value"] == "Hardware"
    assert metric["evaluation"]["reconciliations"]["category"]["passed"] is True
    assert "source_event_sql" not in metric


def test_business_value_system_projects_from_namespaced_hermes_transcript():
    detail = _value_system_detail()
    projected = calliope.project_messages([
        {
            "role": "assistant",
            "tool_calls": [{
                "id": "call-value-system",
                "type": "function",
                "function": {
                    "name": "mcp__warehouse__get_business_value_system",
                    "arguments": json.dumps({"handle": detail["handle"]}),
                },
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "call-value-system",
            "content": json.dumps(detail),
        },
    ])

    assert len(projected) == 1
    assert projected[0]["kind"] == "value_system"
    assert projected[0]["tool_name"] == "mcp__warehouse__get_business_value_system"
    assert projected[0]["payload"]["graph_id"] == detail["graph_id"]


def test_decision_request_projects_native_stage_surface():
    decision_id = "6dbc2e74-daab-4633-b29c-0509263df880"
    projected = calliope._project_tool_result(
        "calliope_decision_request",
        {
            "decision": {
                "id": decision_id,
                "status": "open",
                "revision": 1,
                "decision_kind": "choice",
                "question": "Which sales definition should become canonical?",
                "summary": "One material conflict needs an owner decision.",
                "recommended_option": "keep_and_reconcile",
                "options": [
                    {
                        "key": "keep_and_reconcile",
                        "label": "Keep and reconcile",
                        "consequence": "Preserve the current cube and reconcile the candidate.",
                    }
                ],
                "evidence": [
                    {
                        "kind": "data",
                        "label": "sh.sales",
                        "evidence_role": "direct",
                        "admission_method": "exact_object_overlap",
                    }
                ],
            }
        },
        {},
        "decision-call",
    )

    assert len(projected) == 1
    assert projected[0]["kind"] == "decision"
    assert projected[0]["lineage_key"] == f"decision:{decision_id}"
    assert projected[0]["presentation"]["view"] == "decision_request"
    assert projected[0]["payload"]["evidence"][0]["label"] == "sh.sales"


def test_value_system_stage_renderer_is_native_interactive_and_readable():
    javascript = (HERE / "calliope" / "calliope.js").read_text()
    stylesheet = (HERE / "calliope" / "calliope.css").read_text()

    assert "value_system: renderValueSystem" in javascript
    assert "data-value-system-metric" in javascript
    assert "data-value-system-dimension" in javascript
    assert "data-value-system-contributor" in javascript
    assert "data-value-system-ask" in javascript
    assert 'unit !== "count"' in javascript
    assert ".surface.kind-value_system{container:value-system-card/inline-size;grid-column:span 6" in stylesheet
    assert ".value-system-intro p" in stylesheet


def _question_packet(mode="matched"):
    graph_id = "73daee00-b7a3-4939-8ce7-ea8499946cc8"
    return {
        "version": 1,
        "mode": mode,
        "query_hash": "abc",
        "matches": [],
        "value_systems": [{
            "graph_id": graph_id,
            "domain_key": "sh",
            "name": "Sales Value System",
            "status": "promoted",
            "sources": ["sh.sales"],
            "cube": {"name": "sh_value", "version": 1, "grain": "month"},
            "freshness": {"status": "clean", "structurally_stale": False},
            "pending_change": None,
            "metrics": [{
                "metric_key": "sales_amount",
                "name": "Sales Amount",
                "governed_metric_name": "sh_sales_amount",
                "dimensions": [{"key": "channel_id", "name": "Channel"}],
            }],
            "relationships": [],
        }],
        "contract": {"canonical_status": "promoted", "source_sql_included": False},
    }


def test_arbitrary_chat_instruction_receives_bounded_promoted_context():
    packet = _question_packet("ambient")
    instructions = calliope._instructions([], None, business_context=packet)

    assert "CALLIOPE_BUSINESS_CONTEXT_PACKET_BEGIN" in instructions
    assert '"mode":"ambient"' in instructions
    assert "navigation affordance" in instructions
    assert "Never announce that a hidden packet was injected" in instructions
    assert "sh_sales_amount" in instructions
    assert "source_sql" not in packet["value_systems"][0]["metrics"][0]


def test_question_terms_recover_business_words_without_intent_gate():
    terms = calliope._business_context_query_terms(
        "What drove sales last quarter, and could you show me why?"
    )

    assert "sales" in terms
    assert "quarter" in terms
    assert "what" not in terms
    assert "show" not in terms


def test_ordinary_turn_context_uses_local_matching_without_remote_embedding():
    statements = []

    class Result:
        def __init__(self, one=None, many=None):
            self.one = one
            self.many = many or []

        def fetchone(self):
            return self.one

        def fetchall(self):
            return self.many

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, _params=None):
            statements.append(statement)
            if "to_regprocedure" in statement:
                return Result({"search_ok": True, "graph_ok": True})
            return Result()

    packet = calliope._business_context_question_packet(
        Connection,
        "Tell me something unrelated to the promoted business systems",
        semantic=False,
    )

    assert packet == {}
    assert any("WITH tokens AS" in statement for statement in statements)
    assert not any(
        "FROM rvbbit.business_context_search(" in statement
        for statement in statements
    )
    assert not any("business_context_embed_missing" in statement for statement in statements)
    assert not any("business_context_crawl" in statement for statement in statements)


def test_turn_usage_requires_tool_or_query_evidence_and_resolves_concepts():
    messages = [{
        "role": "assistant",
        "tool_calls": [{
            "id": "metric-1",
            "function": {
                "name": "metric",
                "arguments": '{"name":"sh_sales_amount"}',
            },
        }],
    }, {
        "role": "tool",
        "tool_call_id": "metric-1",
        "content": '{"name":"sh_sales_amount","value":123}',
    }]
    surfaces = [{
        "id": "surface-1",
        "kind": "query",
        "title": "Sales by channel",
        "source": {"sql": "select channel_id, sum(amount_sold) from sh.sales group by 1"},
        "payload": {},
    }]

    usage = calliope._business_context_turn_usage(
        _question_packet(), messages, surfaces, [],
        "What drove sales by channel?", "complete",
    )

    assert usage["used"] is True
    assert usage["systems"][0]["name"] == "Sales Value System"
    assert usage["metrics"][0]["metric_key"] == "sales_amount"
    assert usage["objects"][0]["object_key"] == "channel_id"
    assert usage["sources"] == ["sh.sales"]
    assert usage["queries"][0]["surface_id"] == "surface-1"
    assert usage["evidence_score"] == 0.9


def test_terse_correction_attaches_to_previous_receipted_business_context():
    graph_id = _question_packet()["value_systems"][0]["graph_id"]
    previous = {
        "used_graph_ids": [graph_id],
        "used_metrics": [{
            "graph_id": graph_id,
            "metric_key": "sales_amount",
            "name": "Sales Amount",
            "governed_metric_name": "sh_sales_amount",
        }],
        "used_objects": [],
        "used_source_relations": ["sh.sales"],
    }

    usage = calliope._business_context_turn_usage(
        _question_packet("ambient"), [], [], [], "No, that's wrong.", "complete", previous
    )

    assert usage["feedback_signal"] == "possible_contradiction"
    assert usage["systems"][0]["graph_id"] == graph_id
    assert usage["metrics"][0]["metric_key"] == "sales_amount"
    assert any(
        item["evidence_kind"] == "user_feedback"
        for item in usage["_evidence_items"]
    )


def test_business_context_status_validation_is_bounded():
    result = server.tool_search_business_context(
        "revenue", statuses=["promoted", "made_up"]
    )

    assert result["error"]["code"] == "INVALID_STATUS"
    assert result["error"]["message"] == "made_up"
