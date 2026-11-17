from __future__ import annotations

import copy
import uuid

import pytest
from psycopg import sql
from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb


def test_semantic_ledger_localizes_revisions_and_plans_only_exact_join_watches(rvbbit):
    suffix = uuid.uuid4().hex[:10]
    schema_name = f"ledger_{suffix}"
    domain_key = f"reported_incidents_{suffix}"
    family_id = None
    run_ids: list[uuid.UUID] = []
    try:
        rvbbit.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        rvbbit.execute(
            sql.SQL(
                "CREATE TABLE {}.incidents "
                "(id bigint,offense_code text,occurred_at timestamptz)"
            ).format(sql.Identifier(schema_name))
        )
        rvbbit.execute(
            sql.SQL("CREATE TABLE {}.offense_codes (code text,label text)").format(
                sql.Identifier(schema_name)
            )
        )
        rvbbit.execute(
            sql.SQL(
                "INSERT INTO {}.incidents(id,offense_code,occurred_at) VALUES "
                "(1,'A',clock_timestamp()),(2,'B',clock_timestamp()),"
                "(3,'X',clock_timestamp())"
            ).format(sql.Identifier(schema_name))
        )
        rvbbit.execute(
            sql.SQL(
                "INSERT INTO {}.offense_codes(code,label) VALUES "
                "('A','Alpha'),('B','Bravo')"
            ).format(sql.Identifier(schema_name))
        )
        family_id = rvbbit.execute(
            "INSERT INTO rvbbit.business_value_system_families(domain_key) "
            "VALUES (%s) RETURNING family_id",
            (domain_key,),
        ).fetchone()[0]

        graph = {
            "schema_version": "rvbbit.business-value.graph.v1",
            "domain_key": domain_key,
            "name": "Reported incidents",
            "summary": "Reported incident volume.",
            "source_relations": [
                f"{schema_name}.incidents",
                f"{schema_name}.offense_codes",
            ],
            "business_domain": {
                "domain_key": "public_safety",
                "name": "Public Safety",
                "summary": "Incident reporting and response.",
            },
            "concepts": [
                {
                    "concept_key": "incident",
                    "name": "Incident",
                    "kind": "event",
                    "description": "A reported event.",
                }
            ],
            "processes": [
                {
                    "process_key": "incident_reporting",
                    "name": "Incident reporting",
                    "description": "Record and classify events.",
                    "concept_keys": ["incident"],
                }
            ],
            "source_memberships": [
                {
                    "relation": f"{schema_name}.incidents",
                    "role": "event_stream",
                    "concept_keys": ["incident"],
                    "rationale": "Incident grain.",
                    "confidence": 0.98,
                },
                {
                    "relation": f"{schema_name}.offense_codes",
                    "role": "reference",
                    "concept_keys": ["incident"],
                    "rationale": "Offense labels.",
                    "confidence": 0.99,
                },
            ],
            "semantic_relationships": [
                {
                    "relationship": "joins_to",
                    "left_relation": f"{schema_name}.incidents",
                    "right_relation": f"{schema_name}.offense_codes",
                    "left_columns": ["offense_code"],
                    "right_columns": ["code"],
                    "rationale": "Exact offense lookup.",
                    "confidence": 0.99,
                },
                {
                    "relationship": "comparable_to",
                    "left_relation": f"{schema_name}.incidents",
                    "right_relation": f"{schema_name}.offense_codes",
                    "left_columns": [],
                    "right_columns": [],
                    "rationale": "A semantic comparison is not a row join.",
                    "confidence": 0.6,
                },
            ],
            "analytical_questions": ["How many incidents were reported?"],
            "gaps": [],
            "primary_metric_key": "incident_count",
            "metrics": [
                {
                    "metric_key": "incident_count",
                    "name": "Incident count",
                    "description": "Reported incidents.",
                    "metric_kind": "outcome",
                    "unit": "count",
                    "formula": "count(*)",
                    "grain": "one incident row",
                    "time_semantics": "reported occurrence time",
                    "event_sql": (
                        "SELECT occurred_at,1::numeric AS value,"
                        "jsonb_build_object('offense_code',offense_code) AS dimensions "
                        f"FROM {schema_name}.incidents"
                    ),
                    "dimensions": [
                        {"key": "offense_code", "name": "Offense code"}
                    ],
                    "confidence": 0.9,
                }
            ],
            "edges": [],
        }
        receipt = {
            "schema_version": "rvbbit.business-value.receipt.v1",
            "evaluation": {
                "metrics": [
                    {
                        "metric_key": "incident_count",
                        "event_rows": 0,
                        "reconciliations": {},
                    }
                ],
                "validated_metrics": 1,
                "all_dimension_reconciliations_passed": True,
            },
        }

        def create_revision(revision: int, value: dict, predecessor=None):
            run_id = rvbbit.execute(
                "INSERT INTO rvbbit.business_topology_workflow_runs"
                "(run_name,status,phase,parameters,progress,result,finished_at) "
                "VALUES (%s,'completed','completed','{}','{}','{}',clock_timestamp()) "
                "RETURNING run_id",
                (f"semantic ledger revision {revision}",),
            ).fetchone()[0]
            run_ids.append(run_id)
            return rvbbit.execute(
                "INSERT INTO rvbbit.business_value_graph_proposals("
                "workflow_run_id,domain_key,name,summary,status,source_relations,"
                "primary_metric_key,graph,receipt,family_id,family_revision,"
                "semantic_fingerprint,predecessor_graph_id,usable) "
                "VALUES (%s,%s,%s,%s,'proposed',%s,%s,%s,%s,%s,%s,%s,%s,true) "
                "RETURNING graph_id",
                (
                    run_id,
                    domain_key,
                    value["name"],
                    value["summary"],
                    value["source_relations"],
                    value["primary_metric_key"],
                    Jsonb(value),
                    Jsonb(receipt),
                    family_id,
                    revision,
                    f"revision-{revision}",
                    predecessor,
                ),
            ).fetchone()[0]

        first_graph_id = create_revision(1, graph)
        second = copy.deepcopy(graph)
        second["gaps"] = ["No population denominator."]
        second_graph_id = create_revision(2, second, first_graph_id)
        third_graph_id = create_revision(3, second, second_graph_id)
        rvbbit.execute(
            "UPDATE rvbbit.business_value_system_families "
            "SET active_graph_id=%s,revision=3,observation_count=3 WHERE family_id=%s",
            (third_graph_id, family_id),
        )

        dossier = rvbbit.execute(
            "SELECT assertion_count,watch_count,watch_states,assertion_diff,"
            "automated_watch_coverage "
            "FROM rvbbit.business_semantic_dossiers WHERE graph_id=%s",
            (second_graph_id,),
        ).fetchone()
        assert dossier[0] == 10
        assert dossier[1] == 4
        assert dossier[2] == {"passing": 3, "unknown": 1}
        assert dossier[3]["change_kind"] == "changed"
        assert [item["kind"] for item in dossier[3]["added"]] == ["gap"]
        assert dossier[3]["changed"] == []
        assert dossier[3]["removed"] == []
        assert dossier[4] == 1.0

        repeat_diff = rvbbit.execute(
            "SELECT assertion_diff FROM rvbbit.business_semantic_dossiers WHERE graph_id=%s",
            (third_graph_id,),
        ).fetchone()[0]
        assert repeat_diff["change_kind"] == "no_change"
        assert repeat_diff["material_change"] is False
        assert repeat_diff["added"] == []
        assert repeat_diff["changed"] == []
        assert repeat_diff["removed"] == []

        watch_counts = dict(
            rvbbit.execute(
                "SELECT watch_kind,count(*)::integer "
                "FROM rvbbit.business_semantic_watch_specs WHERE graph_id=%s "
                "GROUP BY watch_kind",
                (third_graph_id,),
            ).fetchall()
        )
        assert watch_counts == {
            "join_coverage": 1,
            "metric_reconciliation": 1,
            "source_presence": 2,
        }
        metric_assertion = rvbbit.execute(
            "SELECT object_value,evidence FROM rvbbit.business_semantic_assertions "
            "WHERE graph_id=%s AND assertion_kind='metric'",
            (third_graph_id,),
        ).fetchone()
        assert "event_sql" not in metric_assertion[0]
        assert metric_assertion[0]["event_sql_fingerprint"]
        assert metric_assertion[1]["validated"] is True

        execution = rvbbit.execute(
            "SELECT rvbbit.business_semantic_run_dossier_watches(%s,'pytest',50)",
            (third_graph_id,),
        ).fetchone()[0]
        assert execution["observed"] == 4
        assert execution["passing"] == 3
        assert execution["failing"] == 1
        assert execution["unknown"] == 0

        observations = rvbbit.execute(
            "SELECT watch_kind,state,measurements "
            "FROM rvbbit.business_semantic_watch_observations "
            "WHERE graph_id=%s ORDER BY observation_id",
            (third_graph_id,),
        ).fetchall()
        assert len(observations) == 4
        assert all("event_sql" not in measurement for _, _, measurement in observations)
        join_observation = next(row for row in observations if row[0] == "join_coverage")
        assert join_observation[1] == "failing"
        assert join_observation[2]["left_rows"] == 3
        assert join_observation[2]["matched_left_rows"] == 2
        assert join_observation[2]["maximum_fanout"] == 1
        metric_observation = next(
            row for row in observations if row[0] == "metric_reconciliation"
        )
        assert metric_observation[1] == "passing"
        assert metric_observation[2]["event_rows"] == 3
        assert metric_observation[2]["total_value"] == 3
        assert metric_observation[2]["dimensions_reconciled"] is True
        assert metric_observation[2]["reconciliations"] == [
            {
                "difference": 0,
                "passed": True,
                "group_count": 3,
                "grouped_total": 3,
                "dimension_key": "offense_code",
            }
        ]

        census = rvbbit.execute(
            "SELECT coverage_mode,coverage_complete,member_list_complete,"
            "rows_observed,member_count,members,taxonomy_changed "
            "FROM rvbbit.business_semantic_dimension_census_current "
            "WHERE graph_id=%s AND metric_key='incident_count' "
            "AND dimension_key='offense_code'",
            (third_graph_id,),
        ).fetchone()
        assert census[0:5] == ("exact", True, True, 3, 3)
        assert {member["value"] for member in census[5]} == {"A", "B", "X"}
        assert all(member["row_count"] == 1 for member in census[5])
        assert census[6] is False

        join_state = rvbbit.execute(
            "SELECT state.effective_confidence,state.validation_state,state.review_state,"
            "watch.watch_id "
            "FROM rvbbit.business_semantic_watch_specs watch "
            "JOIN rvbbit.business_semantic_assertion_states state USING (assertion_id) "
            "WHERE watch.graph_id=%s AND watch.watch_kind='join_coverage'",
            (third_graph_id,),
        ).fetchone()
        assert join_state[0] == 0.84
        assert join_state[1] == "contradicted"
        assert join_state[2] == "needs_revision"

        rvbbit.execute(
            sql.SQL(
                "INSERT INTO {}.offense_codes(code,label) VALUES ('X','Unknown')"
            ).format(sql.Identifier(schema_name))
        )
        rerun = rvbbit.execute(
            "SELECT rvbbit.business_semantic_run_watch(%s,'pytest-rerun')",
            (join_state[3],),
        ).fetchone()[0]
        assert rerun["state"] == "passing"
        evolved = rvbbit.execute(
            "SELECT effective_confidence,validation_state,review_state,observation_count "
            "FROM rvbbit.business_semantic_assertion_states "
            "WHERE assertion_id=(SELECT assertion_id "
            "FROM rvbbit.business_semantic_watch_specs WHERE watch_id=%s)",
            (join_state[3],),
        ).fetchone()
        assert evolved == (0.85, "supported", "needs_revision", 2)

        dossier_after = rvbbit.execute(
            "SELECT dossier FROM rvbbit.business_semantic_dossiers WHERE graph_id=%s",
            (third_graph_id,),
        ).fetchone()[0]
        assert dossier_after["needs_revision_count"] == 1
        assert dossier_after["needs_decision_count"] == 0
        join_watch = next(
            watch for watch in dossier_after["watches"]
            if watch["kind"] == "join_coverage"
        )
        assert join_watch["observation_count"] == 2
        assert join_watch["last_observed_at"] is not None

        metric_watch_id = rvbbit.execute(
            "SELECT watch_id FROM rvbbit.business_semantic_watch_specs "
            "WHERE graph_id=%s AND watch_kind='metric_reconciliation'",
            (third_graph_id,),
        ).fetchone()[0]
        rvbbit.execute(
            sql.SQL(
                "INSERT INTO {}.incidents(id,offense_code,occurred_at) "
                "VALUES (4,'D',clock_timestamp())"
            ).format(sql.Identifier(schema_name))
        )
        drift_run = rvbbit.execute(
            "SELECT rvbbit.business_semantic_run_watch(%s,'pytest-drift')",
            (metric_watch_id,),
        ).fetchone()[0]
        assert drift_run["state"] == "passing"
        assert drift_run["dimension_census"]["changed_dimensions"] == 1
        drift_census = rvbbit.execute(
            "SELECT coverage_complete,member_list_complete,rows_observed,"
            "member_count,added_values,removed_values,taxonomy_changed "
            "FROM rvbbit.business_semantic_dimension_census_current "
            "WHERE graph_id=%s AND metric_key='incident_count' "
            "AND dimension_key='offense_code'",
            (third_graph_id,),
        ).fetchone()
        assert drift_census == (True, True, 4, 4, ["D"], [], True)
        metric_state = rvbbit.execute(
            "SELECT validation_state,review_state "
            "FROM rvbbit.business_semantic_assertion_states "
            "WHERE assertion_id=(SELECT assertion_id "
            "FROM rvbbit.business_semantic_watch_specs WHERE watch_id=%s)",
            (metric_watch_id,),
        ).fetchone()
        assert metric_state == ("warning", "needs_revision")

        source_watch_id = rvbbit.execute(
            "UPDATE rvbbit.business_semantic_watch_specs "
            "SET next_due_at=clock_timestamp()-interval '1 second' "
            "WHERE watch_id=(SELECT watch_id "
            "FROM rvbbit.business_semantic_watch_specs "
            "WHERE graph_id=%s AND watch_kind='source_presence' LIMIT 1) "
            "RETURNING watch_id",
            (third_graph_id,),
        ).fetchone()[0]
        tick = rvbbit.execute(
            "SELECT rvbbit.business_semantic_watch_tick(1)"
        ).fetchone()[0]
        assert tick["status"] == "completed"
        assert tick["observed"] == 1
        scheduled = rvbbit.execute(
            "SELECT trigger_kind FROM rvbbit.business_semantic_watch_observations "
            "WHERE watch_id=%s ORDER BY observation_id DESC LIMIT 1",
            (source_watch_id,),
        ).fetchone()[0]
        assert scheduled == "scheduled"

        relationship_id = rvbbit.execute(
            "INSERT INTO rvbbit.business_semantic_family_relationship_proposals("
            "workflow_run_id,source_graph_id,target_graph_id,relationship_kind,"
            "confidence,proposed_business_domain_key,proposed_business_domain_name,"
            "proposed_family_key,proposed_family_name,rationale,evidence,model_receipt,"
            "candidate_fingerprint,adjudication_version,proposed_by) "
            "VALUES (%s,%s,%s,'alternative_definition',0.88,'public_safety',"
            "'Public Safety',%s,'Reported incidents','Different revisions use an "
            "explicitly reviewed inclusion rule.','{\"shared_concepts\":[\"incident\"]}',"
            "'{\"model_version\":\"pytest\"}',%s,'pytest-v1','pytest') "
            "RETURNING relationship_id",
            (
                run_ids[-1],first_graph_id,third_graph_id,domain_key,
                f"pytest-family-{suffix}",
            ),
        ).fetchone()[0]
        relationship = rvbbit.execute(
            "SELECT source_family_id,target_family_id,relationship_kind,review_state "
            "FROM rvbbit.business_semantic_family_relationship_current "
            "WHERE relationship_id=%s",
            (relationship_id,),
        ).fetchone()
        assert relationship == (
            family_id,family_id,"alternative_definition","proposed"
        )
        decision = rvbbit.execute(
            "SELECT rvbbit.business_semantic_decide_family_relationship(%s,'accepted','pytest')",
            (relationship_id,),
        ).fetchone()[0]
        assert decision["decision"] == "accepted"
        assert rvbbit.execute(
            "SELECT review_state FROM rvbbit.business_semantic_family_relationship_current "
            "WHERE relationship_id=%s",
            (relationship_id,),
        ).fetchone()[0] == "accepted"

        with pytest.raises(RaiseException, match="append-only"):
            rvbbit.execute(
                "UPDATE rvbbit.business_semantic_watch_observations "
                "SET measurements='{}' WHERE watch_id=%s",
                (source_watch_id,),
            )
        with pytest.raises(RaiseException, match="append-only"):
            rvbbit.execute(
                "UPDATE rvbbit.business_semantic_dimension_census_snapshots "
                "SET members='[]' WHERE watch_id=%s",
                (metric_watch_id,),
            )
        with pytest.raises(RaiseException, match="append-only"):
            rvbbit.execute(
                "UPDATE rvbbit.business_semantic_family_relationship_proposals "
                "SET rationale='rewritten' WHERE relationship_id=%s",
                (relationship_id,),
            )
    finally:
        for run_id in run_ids:
            rvbbit.execute(
                "DELETE FROM rvbbit.business_topology_workflow_runs WHERE run_id=%s",
                (run_id,),
            )
        if family_id is not None:
            rvbbit.execute(
                "DELETE FROM rvbbit.business_value_system_families WHERE family_id=%s",
                (family_id,),
            )
        rvbbit.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                sql.Identifier(schema_name)
            )
        )
