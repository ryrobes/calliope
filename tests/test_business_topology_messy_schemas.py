from __future__ import annotations

from pathlib import Path

from business_topology.value_graph import load_value_domain_planning_packet


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/business_topology_messy_schemas.sql"
SCHEMAS = ("topology_messy_business", "topology_legacy_mess")


def _drop_fixture(rvbbit) -> None:
    for schema in SCHEMAS:
        rvbbit.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_messy_schema_fixture_preserves_full_scope_and_relationship_evidence(rvbbit):
    """Exercise metadata distillation on joined OLTP and constraint-free exports."""

    _drop_fixture(rvbbit)
    try:
        rvbbit.execute(FIXTURE.read_text(encoding="utf-8"))
        relation_rows = rvbbit.execute(
            """
            SELECT n.nspname,c.relname
              FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname=ANY(%s) AND c.relkind='r'
             ORDER BY n.nspname,c.relname
            """,
            (list(SCHEMAS),),
        ).fetchall()
        relations = [f"{schema}.{relation}" for schema, relation in relation_rows]

        assert len(relations) == 17
        assert sum(value.startswith("topology_messy_business.") for value in relations) == 12
        assert sum(value.startswith("topology_legacy_mess.") for value in relations) == 5

        packet = load_value_domain_planning_packet(rvbbit, relations)
        assert {item["relation"] for item in packet["relations"]} == set(relations)
        assert packet["privacy"]["raw_values"] is False
        assert packet["privacy"]["value_hashes"] is False

        declared = [
            item for item in packet["relationship_evidence"]
            if item["kind"] == "foreign_key"
        ]
        assert len(declared) == 12
        assert any(
            item["source_relation"] == "topology_messy_business.order_lines"
            and item["target_relation"] == "topology_messy_business.orders"
            and item["source_columns"] == ["order_id"]
            for item in declared
        )

        candidates = [
            item for item in packet["relationship_evidence"]
            if item["kind"] == "candidate_shared_identifier"
        ]
        account_candidate = next(
            item for item in candidates if item["shared_key"] == "account_ref"
        )
        assert {
            participant["relation"] for participant in account_candidate["participants"]
        } == {
            "topology_legacy_mess.contact_export",
            "topology_legacy_mess.incident_notes",
            "topology_legacy_mess.invoice_dump",
            "topology_legacy_mess.product_usage_log",
        }
        assert "candidate" in account_candidate["purpose"]

        cards = {item["relation"]: item for item in packet["relations"]}
        account_columns = {
            item["name"]: item
            for item in cards["topology_legacy_mess.invoice_dump"]["columns"]
        }
        assert account_columns["account_ref"]["semantic_role"] == "key"
        assert account_columns["amount_text"]["type_family"] == "text"

        ordered = load_value_domain_planning_packet(
            rvbbit,
            ["topology_legacy_mess.contact_export"],
            topology_summaries=[
                {
                    "name": "Zulu finding",
                    "sources": ["topology_legacy_mess.contact_export"],
                    "nodes": [],
                },
                {
                    "name": "Alpha finding",
                    "sources": ["topology_legacy_mess.contact_export"],
                    "nodes": [],
                },
            ],
        )
        assert [
            finding["name"]
            for finding in ordered["relations"][0]["topology_findings"]
        ] == ["Alpha finding", "Zulu finding"]
    finally:
        _drop_fixture(rvbbit)


def test_messy_schema_queue_accounts_for_every_relation(rvbbit):
    """Targeted excavation must not cherry-pick a few attractive fact tables."""

    _drop_fixture(rvbbit)
    run_ids = []
    try:
        rvbbit.execute(FIXTURE.read_text(encoding="utf-8"))
        expected_counts = {
            "topology_messy_business": 12,
            "topology_legacy_mess": 5,
        }
        for schema, expected_count in expected_counts.items():
            run_id = rvbbit.execute(
                "SELECT rvbbit.business_topology_queue_schema(%s)", (schema,)
            ).fetchone()[0]
            run_ids.append(run_id)
            parameters = rvbbit.execute(
                """
                SELECT parameters
                  FROM rvbbit.business_topology_workflow_runs
                 WHERE run_id=%s
                """,
                (run_id,),
            ).fetchone()[0]
            assert parameters["scope_schema"] == schema
            assert len(parameters["relations"]) == expected_count
            assert all(value.startswith(f"{schema}.") for value in parameters["relations"])
    finally:
        if run_ids:
            rvbbit.execute(
                "DELETE FROM rvbbit.business_topology_workflow_runs WHERE run_id=ANY(%s)",
                (run_ids,),
            )
        _drop_fixture(rvbbit)
